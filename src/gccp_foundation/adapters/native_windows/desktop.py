from ctypes import wintypes as W
import ctypes as C
import os
from pathlib import Path
import subprocess
import time
import uuid


def _gui_thread_state_changed(before, after):
    """Caret blink alone does not mean focus/menu/capture changed."""
    return (dict(before, flags=before.get('flags',0) & ~1) !=
            dict(after, flags=after.get('flags',0) & ~1))


class WindowsDesktop:
    def __init__(self):
        if os.name != "nt":
            raise OSError("Windows desktop required")
        self.u = C.WinDLL("user32", use_last_error=True)
        self.k = C.WinDLL("kernel32", use_last_error=True)
        self.cb = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
        self.u.EnumWindows.argtypes = [self.cb, W.LPARAM]
        self.u.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
        self.u.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
        self.u.GetWindowThreadProcessId.restype = W.DWORD
        self.u.GetWindow.argtypes = [W.HWND, W.UINT]
        self.u.GetWindow.restype = W.HWND
        self.u.IsWindow.argtypes = [W.HWND]
        self.u.IsWindowVisible.argtypes = [W.HWND]
        self.u.IsWindowEnabled.argtypes = [W.HWND]
        self.u.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
        self.k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        self.k.OpenProcess.restype = W.HANDLE
        self.k.CloseHandle.argtypes = [W.HANDLE]
        self.k.GetProcessTimes.argtypes = [W.HANDLE] + [C.POINTER(W.FILETIME)] * 4
        self.k.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]

    def process(self, pid):
        handle = self.k.OpenProcess(0x1000, False, pid)
        if not handle:
            raise C.WinError(C.get_last_error())
        try:
            times = [W.FILETIME() for _ in range(4)]
            if not self.k.GetProcessTimes(handle, *[C.byref(x) for x in times]):
                raise C.WinError(C.get_last_error())
            buf = C.create_unicode_buffer(32768)
            size = W.DWORD(len(buf))
            if not self.k.QueryFullProcessImageNameW(handle, 0, buf, C.byref(size)):
                raise C.WinError(C.get_last_error())
            return dict(pid=pid, created_filetime=str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime),
                        executable=buf.value)
        finally:
            self.k.CloseHandle(handle)

    def windows(self):
        rows = []

        @self.cb
        def visit(hwnd, _):
            pid = W.DWORD()
            self.u.GetWindowThreadProcessId(hwnd, C.byref(pid))
            buf = C.create_unicode_buffer(8192)
            self.u.GetWindowTextW(hwnd, buf, len(buf))
            if self.u.IsWindowVisible(hwnd):
                rows.append(dict(hwnd=int(hwnd), pid=pid.value, title=buf.value,
                                 enabled=bool(self.u.IsWindowEnabled(hwnd)), owner=int(self.u.GetWindow(hwnd, 4) or 0)))
            return True

        if not self.u.EnumWindows(visit, 0):
            raise OSError("Visible window enumeration incomplete")
        return rows

    def gui_thread_context(self, foreground_hwnd):
        """Read native thread focus/menu/capture; does not identify a Qt editor.

        Never calls SetFocus, AttachThreadInput, activates windows or reads text.
        """
        class GuiThreadInfo(C.Structure):
            _fields_ = [('size', W.DWORD), ('flags', W.DWORD),
                        ('active', W.HWND), ('focus', W.HWND), ('capture', W.HWND),
                        ('menu_owner', W.HWND), ('move_size', W.HWND), ('caret', W.HWND),
                        ('caret_rect', W.RECT)]
        self.u.GetGUIThreadInfo.argtypes = [W.DWORD, C.POINTER(GuiThreadInfo)]
        self.u.GetGUIThreadInfo.restype = W.BOOL
        process = W.DWORD()
        thread = self.u.GetWindowThreadProcessId(foreground_hwnd, C.byref(process))
        info = GuiThreadInfo(); info.size = C.sizeof(info)
        if not thread or not self.u.GetGUIThreadInfo(thread, C.byref(info)):
            return dict(available=False, error='native_gui_thread_metadata_unavailable')
        focus_pid = W.DWORD()
        focus_thread = self.u.GetWindowThreadProcessId(info.focus, C.byref(focus_pid)) if info.focus else 0
        return dict(available=True, thread_id=int(thread), thread_pid=process.value,
            flags=int(info.flags), active_hwnd=int(info.active or 0),
            focus_hwnd=int(info.focus or 0), focus_pid=focus_pid.value,
            focus_thread_id=int(focus_thread), capture_hwnd=int(info.capture or 0),
            menu_owner_hwnd=int(info.menu_owner or 0), move_size_hwnd=int(info.move_size or 0),
            caret_hwnd=int(info.caret or 0))

    def window_context(self, pid, target_hwnd, related_pids=()):
        """Read geometry/z-order/foreground only. Never activates or dismisses UI.

        Includes the selected process family and higher overlapping windows;
        unrelated window titles are omitted. Handles are diagnostic evidence,
        not substitute Window objects for the computer-use client.
        """
        self.u.GetForegroundWindow.restype = W.HWND
        self.u.GetWindowRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
        self.u.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
        self.u.GetSystemMetrics.argtypes = [C.c_int]
        foreground_before = int(self.u.GetForegroundWindow() or 0)
        gui_before = self.gui_thread_context(foreground_before)
        rows = self.windows()
        targets = [r for r in rows if r['hwnd'] == target_hwnd and r['pid'] == pid]
        if len(targets) != 1:
            raise ValueError("Unique visible current target window required")
        family = {pid, *related_pids}
        for index, row in enumerate(rows):
            rectangle = W.RECT()
            if not self.u.GetWindowRect(row['hwnd'], C.byref(rectangle)):
                raise OSError("Window geometry changed during observation")
            row['rect'] = [rectangle.left, rectangle.top, rectangle.right, rectangle.bottom]
            row['z_order'] = index
        target = targets[0]
        def overlaps(a, b):
            return max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3])
        observed = []
        for row in rows:
            if (row['pid'] not in family and row['hwnd'] != foreground_before
                    and not (row['z_order'] < target['z_order'] and overlaps(row['rect'], target['rect']))):
                continue
            name = C.create_unicode_buffer(512)
            self.u.GetClassNameW(row['hwnd'], name, len(name))
            row['class_name'] = name.value
            if row['pid'] not in family:
                row['title'] = '[unrelated window title omitted]'
            observed.append(row)
        left, top, width, height = [self.u.GetSystemMetrics(i) for i in (76, 77, 78, 79)]
        foreground_after = int(self.u.GetForegroundWindow() or 0)
        gui_after = self.gui_thread_context(foreground_after)
        return dict(observation_id=uuid.uuid4().hex, captured_ms=int(time.time()*1000),
            source='win32_visible_top_level_window_metadata', enumeration_complete=True,
            read_only=True, input_performed=False, process=self.process(pid),
            target_hwnd=target_hwnd, related_pids=sorted(family - {pid}),
            foreground_hwnd=foreground_after,
            foreground_changed_during_capture=foreground_before != foreground_after,
            native_gui_thread=gui_after,
            native_gui_thread_changed_during_capture=_gui_thread_state_changed(gui_before, gui_after),
            desktop_rect=[left, top, left+width, top+height], windows=observed)

    def processes(self):
        class Entry(C.Structure):
            _fields_ = [("size", W.DWORD), ("usage", W.DWORD), ("pid", W.DWORD), ("heap", C.c_size_t),
                        ("module", W.DWORD), ("threads", W.DWORD), ("parent", W.DWORD),
                        ("priority", W.LONG), ("flags", W.DWORD), ("name", W.WCHAR * 260)]
        self.k.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
        self.k.CreateToolhelp32Snapshot.restype = W.HANDLE
        self.k.Process32FirstW.argtypes = [W.HANDLE, C.POINTER(Entry)]
        self.k.Process32NextW.argtypes = [W.HANDLE, C.POINTER(Entry)]
        handle = self.k.CreateToolhelp32Snapshot(2, 0)
        if handle == C.c_void_p(-1).value:
            raise C.WinError(C.get_last_error())
        rows = []
        try:
            entry = Entry()
            entry.size = C.sizeof(entry)
            more = self.k.Process32FirstW(handle, C.byref(entry))
            while more:
                rows.append(dict(pid=entry.pid, parent_pid=entry.parent, name=entry.name))
                more = self.k.Process32NextW(handle, C.byref(entry))
        finally:
            self.k.CloseHandle(handle)
        return rows

    def modules(self, pid):
        psapi = C.WinDLL("psapi", use_last_error=True)
        psapi.EnumProcessModulesEx.argtypes = [W.HANDLE, C.POINTER(W.HMODULE), W.DWORD, C.POINTER(W.DWORD), W.DWORD]
        psapi.GetModuleFileNameExW.argtypes = [W.HANDLE, W.HMODULE, W.LPWSTR, W.DWORD]
        handle = self.k.OpenProcess(0x410, False, pid)
        if not handle:
            raise C.WinError(C.get_last_error())
        try:
            modules = (W.HMODULE * 4096)()
            needed = W.DWORD()
            if not psapi.EnumProcessModulesEx(handle, modules, C.sizeof(modules), C.byref(needed), 3):
                raise C.WinError(C.get_last_error())
            if needed.value > C.sizeof(modules):
                raise OSError("Module inventory incomplete")
            result = []
            for module in modules[:needed.value // C.sizeof(W.HMODULE)]:
                buf = C.create_unicode_buffer(32768)
                if psapi.GetModuleFileNameExW(handle, module, buf, len(buf)):
                    result.append(buf.value)
            return result
        finally:
            self.k.CloseHandle(handle)

    def interactive_state(self):
        self.u.OpenInputDesktop.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        self.u.OpenInputDesktop.restype = W.HANDLE
        self.u.GetUserObjectInformationW.argtypes = [W.HANDLE, C.c_int, W.LPVOID, W.DWORD, C.POINTER(W.DWORD)]
        self.u.CloseDesktop.argtypes = [W.HANDLE]
        handle = self.u.OpenInputDesktop(0, False, 0x1)
        if not handle:
            return "unavailable"
        try:
            name = C.create_unicode_buffer(256)
            required = W.DWORD()
            if not self.u.GetUserObjectInformationW(handle, 2, name, C.sizeof(name), C.byref(required)):
                return "unknown"
            return "ready" if name.value.casefold() == "default" else "locked"
        finally:
            self.u.CloseDesktop(handle)

    def launch(self, executable, project):
        # Arguments remain data; no shell interpolation or DLL/protection changes.
        return subprocess.Popen([str(executable), str(project)], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=0x08000000).pid

    def close_window(self, hwnd):
        if not self.u.PostMessageW(hwnd, 0x10, 0, 0):
            raise C.WinError(C.get_last_error())

    def exists(self, hwnd):
        return bool(self.u.IsWindow(hwnd))


def await_condition(probe, predicate, deadline, cancellation):
    """Bounded fallback polling; reports observed conditions, not a vendor event."""
    while time.monotonic() < deadline:
        if cancellation.is_cancelled():
            raise TimeoutError("Operation cancelled before a verified condition")
        observation = probe()
        if predicate(observation):
            return observation
        time.sleep(min(.1, max(0, deadline - time.monotonic())))
    raise TimeoutError("Condition not verified before deadline; do not infer action failure")
