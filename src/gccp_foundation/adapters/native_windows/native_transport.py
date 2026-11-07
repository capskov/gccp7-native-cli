"""Fixed-entry transport reused from the live Qt-only bridge; no GCCP process launch."""
from pathlib import Path
import ctypes as C
from ctypes import wintypes as W
K = C.WinDLL('kernel32', use_last_error=True)
P = C.WinDLL('psapi', use_last_error=True)
U = C.WinDLL('user32', use_last_error=True)
K.OpenProcess.argtypes = [W.DWORD,W.BOOL,W.DWORD];K.OpenProcess.restype = W.HANDLE
K.CloseHandle.argtypes = [W.HANDLE]
K.GetProcessTimes.argtypes = [W.HANDLE]+[C.POINTER(W.FILETIME)]*4
K.GetModuleHandleW.argtypes = [W.LPCWSTR];K.GetModuleHandleW.restype = W.HMODULE
K.GetProcAddress.argtypes = [W.HMODULE,C.c_char_p];K.GetProcAddress.restype = C.c_void_p
K.GetModuleHandleExW.argtypes = [W.DWORD,C.c_void_p,C.POINTER(W.HMODULE)]
K.GetModuleFileNameW.argtypes = [W.HMODULE,W.LPWSTR,W.DWORD]
P.EnumProcessModulesEx.argtypes = [W.HANDLE,C.POINTER(W.HMODULE),W.DWORD,C.POINTER(W.DWORD),W.DWORD]
P.GetModuleFileNameExW.argtypes = [W.HANDLE,W.HMODULE,W.LPWSTR,W.DWORD]
K.VirtualAllocEx.argtypes = [W.HANDLE,C.c_void_p,C.c_size_t,W.DWORD,W.DWORD];K.VirtualAllocEx.restype = C.c_void_p
K.VirtualFreeEx.argtypes = [W.HANDLE,C.c_void_p,C.c_size_t,W.DWORD]
K.WriteProcessMemory.argtypes = [W.HANDLE,C.c_void_p,C.c_void_p,C.c_size_t,C.POINTER(C.c_size_t)]
K.CreateRemoteThread.argtypes = [W.HANDLE,C.c_void_p,C.c_size_t,C.c_void_p,C.c_void_p,W.DWORD,C.POINTER(W.DWORD)];K.CreateRemoteThread.restype = W.HANDLE
K.WaitForSingleObject.argtypes = [W.HANDLE,W.DWORD]
K.GetExitCodeThread.argtypes = [W.HANDLE,C.POINTER(W.DWORD)]
U.GetWindowTextW.argtypes = [W.HWND,W.LPWSTR,C.c_int]
U.GetWindowThreadProcessId.argtypes = [W.HWND,C.POINTER(W.DWORD)]

def modules(handle):
    array = (W.HMODULE*2048)();needed=W.DWORD()
    if not P.EnumProcessModulesEx(handle,array,C.sizeof(array),C.byref(needed),3) or needed.value>C.sizeof(array):
        raise C.WinError(C.get_last_error())
    result=[]
    for mod in array[:needed.value//C.sizeof(W.HMODULE)]:
        buf=C.create_unicode_buffer(32768)
        if not P.GetModuleFileNameExW(handle,mod,buf,len(buf)):raise C.WinError(C.get_last_error())
        result.append({'base':mod,'path':buf.value})
    return result

def call_once(handle,address,argument):
    tid=W.DWORD();thread=K.CreateRemoteThread(handle,None,0,address,argument,0,C.byref(tid))
    if not thread:raise C.WinError(C.get_last_error())
    try:
        if K.WaitForSingleObject(thread,10000)!=0:
            raise RuntimeError('Remote call outcome unknown; no repeat or forced thread termination')
        code=W.DWORD()
        if not K.GetExitCodeThread(thread,C.byref(code)):raise C.WinError(C.get_last_error())
        return {'thread_id':tid.value,'exit_code':code.value}
    finally:K.CloseHandle(thread)
