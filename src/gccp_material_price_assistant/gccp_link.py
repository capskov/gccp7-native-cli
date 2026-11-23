"""The live GCCP7 material selection and its native quotation message."""
import ctypes as ct
from ctypes import wintypes as win
import json
from decimal import Decimal, ROUND_HALF_UP
from queue import SimpleQueue

TRACE = r"""
const api = Process.getModuleByName('user32.dll').getExportByName('SendMessageTimeoutW');
Interceptor.attach(api, {
    onEnter(args) {
        if (args[1].toUInt32() !== 0x4a) return;
        try {
            const data = args[3], ptr = Process.pointerSize;
            if (data.readPointer().toUInt32() !== 0x14c0) return;
            const length = data.add(ptr).readU32();
            if (!length || length > 1000000) return;
            const body = JSON.parse(data.add(2 * ptr).readPointer().readUtf8String(length));
            if (body.Identifier === 'GetResource' && body.Action === 4 && body.SubAction === 1) {
                send({hwnd: args[2].toString(), body});
            }
        } catch (_) {}
    }
});
"""


class GccpLink:
    def __init__(self):
        self.events = SimpleQueue()
        self.session = None
        self.script = None
        self.pid = None

    def connect(self):
        import frida

        device = frida.get_local_device()
        matches = [p for p in device.enumerate_processes() if p.name.lower() == "gbe64.exe"]
        if len(matches) != 1:
            raise RuntimeError("请打开一个广联达概算窗口，再连接。")
        self.pid = matches[0].pid
        self.session = device.attach(self.pid)
        try:
            self.script = self.session.create_script(TRACE)
            self.script.on("message", self._receive)
            self.session.on("detached", lambda *_: self.events.put(("detached", None)))
            self.script.load()
        except Exception:
            self.close()
            raise

    def _receive(self, message, _data):
        if message.get("type") != "send":
            return
        event = message["payload"]
        body = event["body"]
        if body.get("ProcessID") == self.pid:
            self.events.put(("selection", event if len(body.get("Materials", [])) == 1 else None))

    def close(self):
        if self.session and not self.session.is_detached:
            self.session.detach()
        self.session = None
        self.script = None
        self.pid = None

    def send_quote(self, selection, quote, coefficient, *, precision=None):
        if not self.session or self.session.is_detached:
            raise RuntimeError("广联达已断开，请重新连接。")
        body = selection["body"]
        target = body["Materials"][0]
        hwnd = int(selection["hwnd"], 16)
        user = ct.WinDLL("user32", use_last_error=True)
        owner = user.GetWindowThreadProcessId
        owner.argtypes = (win.HWND, ct.POINTER(win.DWORD))
        owner.restype = win.DWORD
        pid = win.DWORD()
        if not hwnd or not owner(hwnd, ct.byref(pid)) or pid.value != self.pid:
            raise RuntimeError("广联达选中行已失效，请重新点选。")

        message = quotation_message(body, target, quote, coefficient, precision=precision)
        payload = json.dumps(message, ensure_ascii=False, allow_nan=False).encode("utf-8")
        buffer = ct.create_string_buffer(payload)

        class CopyData(ct.Structure):
            _fields_ = [("tag", ct.c_size_t), ("size", win.DWORD), ("data", ct.c_void_p)]

        data = CopyData(0x14C0, len(payload), ct.addressof(buffer))
        send = user.SendMessageTimeoutW
        send.argtypes = (
            win.HWND, win.UINT, win.WPARAM, ct.c_void_p,
            win.UINT, win.UINT, ct.POINTER(ct.c_size_t),
        )
        send.restype = ct.c_ssize_t
        result = ct.c_size_t()
        if not send(hwnd, 0x4A, 0, ct.byref(data), 2, 5000, ct.byref(result)):
            raise RuntimeError("广联达未确认收到报价，请先查看当前行及「应用修改」。")


def quotation_message(body, target, quote, coefficient, *, precision=None):
    if quote.get("source") in ("北京信息价（房建）", "北京信息价（京标价协）") and quote.get("source_values_json"):
        original = json.loads(quote["source_values_json"])
        year, month = original["quote_month"].split("-")
        association = quote["source"] == "北京信息价（京标价协）"
        period = f"{month}月京标价协" if association else f"{int(month)}月"
        quote = dict(quote, native_source=f"北京信息价({year}年{period})",
                     material_code=original.get("material_code", ""))

    fields = ("ex_tax_cents", "incl_tax_cents")
    quoted = {field: Decimal(str(quote[field])) / 100 for field in fields}
    if precision is None:
        precision = max(2, *(-value.normalize().as_tuple().exponent for value in quoted.values()))

    def price(field):
        return (quoted[field] * coefficient).quantize(
            Decimal(1).scaleb(-precision), rounding=ROUND_HALF_UP
        )

    ex_tax = price("ex_tax_cents")
    incl_tax = price("incl_tax_cents")
    date = quote["quote_month"]
    parts = date.split("-")
    if len(parts) >= 2 and all(part.isdigit() for part in parts[:2]):
        date = f"{parts[0]}年{int(parts[1])}月"
        if len(parts) == 3 and parts[2].isdigit():
            date += f"{int(parts[2])}日"
    source = quote.get("native_source") or quote["source"] or "材料价格库"
    if date and not quote.get("native_source"):
        if "市场价" in source:
            source = "市场价"
        source += f"({date})"
    return {
        "Action": 2,
        "SubAction": 1,
        "Identifier": "InputAllResRate",
        "FrameID": str(body["FrameID"]),
        "ProjectName": body["ProjectName"],
        "TaxMethod": body["TaxMethod"],
        "GczsMethod": 1,
        "Materials": [{
            "Code": target["Code"],
            "MaterialID": target["MaterialID"],
            "Name": quote["material"],
            "Unit": target["Unit"],
            "Spec": quote["specification"] or quote["model"],
            "Price": format(ex_tax if body["TaxMethod"] == 1 else incl_tax, "f"),
            "PricePreTaxRate": float(ex_tax),
            "PriceTaxRate": float(incl_tax),
            "PriceSource": source,
            "Brand": quote["brand"],
            "Company": quote["supplier"],
            "MaterialType": "",
            "JgbMethod": 1,
            "MaterialCode": quote.get("material_code", ""),
            "GLJPriceCode": quote.get("glj_price_code", ""),
        }],
    }
