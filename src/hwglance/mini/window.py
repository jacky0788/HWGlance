"""迷你模式的視窗：Win32 分層視窗（WS_EX_LAYERED＋UpdateLayeredWindow），每個像素有自己的透明度——
背景可以半透明、字照樣清楚。WebView2 的視窗只能整個一起半透明（2026-11-01 實測，SPEC「第 9 段的前置實測」），
所以這裡不用網頁，用 Pillow 畫好整張圖貼上去（render.py）。

自己一條執行緒＋訊息迴圈；別的執行緒只用 PostMessage 叫它（顯示、隱藏、重畫、鎖定、結束）。
這條執行緒設成「每個螢幕各自的 DPI」，高 DPI 螢幕上字才清楚（本體其他部分不受影響）。
滑鼠：拖曳移動、雙擊展開主畫面、右鍵選單；鎖定時滑鼠穿透（WS_EX_TRANSPARENT），只能從系統匣或設定頁解除。
位置記在 data/mini.json（螢幕的實際像素）；那個位置已經不在任何螢幕上（螢幕拔掉了）就回主螢幕左上角。
"""
from __future__ import annotations

import ctypes
import json
import sys
import threading
import time
import traceback
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path

TITLE = "HWGlance 迷你模式"
CLASS_NAME = "HWGlanceMini"
FRAME_MS = 500                      # 多久重畫一次（FPS 會變，比讀數的 1 秒快一點）
TOPMOST_EVERY = 4                   # 每幾次重畫再確認一次置頂（有些全螢幕程式會把置頂視窗壓下去）
MARGIN = 24                         # 預設位置離螢幕左上角多遠（96 DPI 的像素）

WS_POPUP = 0x80000000
WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOPMOST = 0x00080000, 0x00000020, 0x00000008
WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x00000080, 0x08000000
GWL_EXSTYLE = -20
WM_DESTROY, WM_CLOSE, WM_NULL, WM_TIMER = 0x0002, 0x0010, 0x0000, 0x0113
WM_NCHITTEST, WM_CONTEXTMENU, WM_NCLBUTTONDBLCLK = 0x0084, 0x007B, 0x00A3
WM_EXITSIZEMOVE, WM_DPICHANGED, WM_DISPLAYCHANGE = 0x0232, 0x02E0, 0x007E
WM_APP = 0x8000
CMD_SHOW, CMD_HIDE, CMD_REFRESH, CMD_LOCK = WM_APP + 1, WM_APP + 2, WM_APP + 3, WM_APP + 4
HTCAPTION = 2
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
ULW_ALPHA, AC_SRC_OVER, AC_SRC_ALPHA = 0x2, 0x0, 0x1
MF_STRING, MF_CHECKED, MF_SEPARATOR = 0x0, 0x8, 0x800
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x0002, 0x0100, 0x0080
MONITOR_DEFAULTTONULL, MONITOR_DEFAULTTOPRIMARY = 0, 1
DPI_AWARENESS_PER_MONITOR_V2 = -4
MENU_MAIN, MENU_LOCK, MENU_CLOSE = 1, 2, 3

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


def _api():
    """user32／gdi32 的函式加上參數型別（64 位元的 handle、LPARAM 才不會被截斷）。"""
    u, g, k = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("gdi32"), ctypes.WinDLL("kernel32")
    H, P = wintypes.HANDLE, ctypes.c_void_p
    sigs = {
        (u, "DefWindowProcW"): ([wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], LRESULT),
        (u, "RegisterClassExW"): ([ctypes.POINTER(WNDCLASSEXW)], wintypes.ATOM),
        (u, "UnregisterClassW"): ([wintypes.LPCWSTR, wintypes.HINSTANCE], wintypes.BOOL),
        (u, "CreateWindowExW"): ([wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                  wintypes.HINSTANCE, P], wintypes.HWND),
        (u, "DestroyWindow"): ([wintypes.HWND], wintypes.BOOL),
        (u, "GetMessageW"): ([ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT], wintypes.BOOL),
        (u, "TranslateMessage"): ([ctypes.POINTER(wintypes.MSG)], wintypes.BOOL),
        (u, "DispatchMessageW"): ([ctypes.POINTER(wintypes.MSG)], LRESULT),
        (u, "PostMessageW"): ([wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], wintypes.BOOL),
        (u, "PostQuitMessage"): ([ctypes.c_int], None),
        (u, "ShowWindow"): ([wintypes.HWND, ctypes.c_int], wintypes.BOOL),
        (u, "SetWindowPos"): ([wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                               wintypes.UINT], wintypes.BOOL),
        (u, "GetWindowLongPtrW"): ([wintypes.HWND, ctypes.c_int], ctypes.c_ssize_t),
        (u, "SetWindowLongPtrW"): ([wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t], ctypes.c_ssize_t),
        (u, "GetWindowRect"): ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
        (u, "UpdateLayeredWindow"): ([wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                                      ctypes.POINTER(wintypes.SIZE), wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                                      wintypes.DWORD, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD], wintypes.BOOL),
        (u, "GetDC"): ([wintypes.HWND], wintypes.HDC),
        (u, "ReleaseDC"): ([wintypes.HWND, wintypes.HDC], ctypes.c_int),
        (u, "SetTimer"): ([wintypes.HWND, ctypes.c_size_t, wintypes.UINT, P], ctypes.c_size_t),
        (u, "KillTimer"): ([wintypes.HWND, ctypes.c_size_t], wintypes.BOOL),
        (u, "GetDpiForWindow"): ([wintypes.HWND], wintypes.UINT),
        (u, "SetThreadDpiAwarenessContext"): ([P], P),
        (u, "CreatePopupMenu"): ([], wintypes.HMENU),
        (u, "AppendMenuW"): ([wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR], wintypes.BOOL),
        (u, "TrackPopupMenuEx"): ([wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.HWND, P],
                                  wintypes.BOOL),
        (u, "DestroyMenu"): ([wintypes.HMENU], wintypes.BOOL),
        (u, "GetCursorPos"): ([ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
        (u, "SetForegroundWindow"): ([wintypes.HWND], wintypes.BOOL),
        (u, "MonitorFromRect"): ([ctypes.POINTER(wintypes.RECT), wintypes.DWORD], H),
        (u, "MonitorFromPoint"): ([wintypes.POINT, wintypes.DWORD], H),
        (u, "GetMonitorInfoW"): ([H, ctypes.POINTER(MONITORINFO)], wintypes.BOOL),
        (g, "CreateCompatibleDC"): ([wintypes.HDC], wintypes.HDC),
        (g, "CreateDIBSection"): ([wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT, ctypes.POINTER(P), H,
                                   wintypes.DWORD], wintypes.HBITMAP),
        (g, "SelectObject"): ([wintypes.HDC, H], H),
        (g, "DeleteObject"): ([H], wintypes.BOOL),
        (g, "DeleteDC"): ([wintypes.HDC], wintypes.BOOL),
        (k, "GetModuleHandleW"): ([wintypes.LPCWSTR], wintypes.HMODULE),
    }
    for (dll, name), (args, res) in sigs.items():
        fn = getattr(dll, name)
        fn.argtypes, fn.restype = args, res
    return u, g, k


def load_position(path: Path) -> tuple[int, int] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return int(data["x"]), int(data["y"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_position(path: Path, x: int, y: int) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"x": x, "y": y}), encoding="utf-8")
    except OSError:
        pass


class MiniWindow:
    """frame(倍率) 回傳要貼上的 RGBA 圖（Pillow）；倍率＝這個螢幕的 DPI ÷ 96。回呼都在別的執行緒跑，不卡住視窗。"""

    def __init__(self, frame: Callable[[float], object], *, state_file: Path,
                 on_main: Callable[[], None], on_close: Callable[[], None], on_lock: Callable[[], None],
                 locked: bool = False) -> None:
        self._frame = frame
        self._state_file = state_file
        self._on_main, self._on_close, self._on_lock = on_main, on_close, on_lock
        self._locked = locked
        self._visible = False
        self._hwnd = 0
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._ticks = 0
        self._place = True                  # 下一次貼圖時要不要指定位置（剛顯示、螢幕變了）
        self._wndproc = WNDPROC(self._proc)  # 留著參考，不然被回收後 Windows 會呼叫到不存在的函式

    @property
    def visible(self) -> bool:
        return self._visible

    @property
    def locked(self) -> bool:
        return self._locked

    def start(self) -> bool:
        if sys.platform != "win32":
            return False
        self._thread = threading.Thread(target=self._run, daemon=True, name="hwglance-mini")
        self._thread.start()
        return self._ready.wait(5) and bool(self._hwnd)

    def show(self) -> None:
        self._post(CMD_SHOW)

    def hide(self) -> None:
        self._post(CMD_HIDE)

    def refresh(self) -> None:
        self._post(CMD_REFRESH)

    def set_locked(self, locked: bool) -> None:
        self._post(CMD_LOCK, int(locked))

    def stop(self) -> None:
        self._post(WM_CLOSE)
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _post(self, msg: int, wparam: int = 0) -> None:
        if self._hwnd:
            self._u.PostMessageW(self._hwnd, msg, wparam, 0)

    # ---- 以下都在視窗自己的執行緒 ----

    def _run(self) -> None:
        try:
            self._u, self._g, k = _api()
            try:
                self._u.SetThreadDpiAwarenessContext(ctypes.c_void_p(DPI_AWARENESS_PER_MONITOR_V2))
            except (AttributeError, OSError):
                pass
            self._inst = k.GetModuleHandleW(None)
            wc = WNDCLASSEXW(cbSize=ctypes.sizeof(WNDCLASSEXW), lpfnWndProc=self._wndproc, hInstance=self._inst,
                             lpszClassName=CLASS_NAME)
            self._u.RegisterClassExW(ctypes.byref(wc))
            ex = WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
            self._hwnd = self._u.CreateWindowExW(ex | (WS_EX_TRANSPARENT if self._locked else 0), CLASS_NAME, TITLE,
                                                 WS_POPUP, 0, 0, 1, 1, None, None, self._inst, None) or 0
        except Exception:                                 # noqa: BLE001 - 迷你模式開不起來不影響主程式
            traceback.print_exc()
        finally:
            self._ready.set()
        if not self._hwnd:
            return
        msg = wintypes.MSG()
        while self._u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            self._u.TranslateMessage(ctypes.byref(msg))
            self._u.DispatchMessageW(ctypes.byref(msg))
        self._u.UnregisterClassW(CLASS_NAME, self._inst)

    def _proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_NCHITTEST:
                return HTCAPTION                          # 整個框都是標題列：按住就能拖
            if msg == WM_TIMER:
                self._ticks += 1
                if self._ticks % TOPMOST_EVERY == 0:
                    self._u.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
                self._paint()
                return 0
            if msg == CMD_SHOW:
                if not self._visible:
                    self._visible, self._place = True, True
                    self._paint()
                    self._u.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
                    self._u.SetTimer(hwnd, 1, FRAME_MS, None)
                return 0
            if msg == CMD_HIDE:
                if self._visible:
                    self._visible = False
                    self._u.KillTimer(hwnd, 1)
                    self._u.ShowWindow(hwnd, SW_HIDE)
                return 0
            if msg == CMD_REFRESH:
                self._paint()
                return 0
            if msg == CMD_LOCK:
                self._locked = bool(wparam)
                style = self._u.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
                style = style | WS_EX_TRANSPARENT if self._locked else style & ~WS_EX_TRANSPARENT
                self._u.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style)
                self._paint()
                return 0
            if msg == WM_CONTEXTMENU:
                self._menu(hwnd)
                return 0
            if msg == WM_NCLBUTTONDBLCLK:
                self._later(self._on_main)
                return 0
            if msg == WM_EXITSIZEMOVE:
                r = wintypes.RECT()
                self._u.GetWindowRect(hwnd, ctypes.byref(r))
                save_position(self._state_file, r.left, r.top)
                return 0
            if msg in (WM_DPICHANGED, WM_DISPLAYCHANGE):
                self._place = msg == WM_DISPLAYCHANGE      # 螢幕拔插：重新檢查位置還在不在螢幕上
                self._paint()
                return 0
            if msg == WM_CLOSE:
                self._u.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                self._u.KillTimer(hwnd, 1)
                self._u.PostQuitMessage(0)
                return 0
        except Exception:                                 # noqa: BLE001 - 例外不能從回呼丟回 Windows
            traceback.print_exc()
            return 0
        return self._u.DefWindowProcW(hwnd, msg, wparam, lparam)

    @staticmethod
    def _later(fn: Callable[[], None]) -> None:
        """開主畫面、存設定可能要等，交給別的執行緒，視窗照樣回應。"""
        threading.Thread(target=fn, daemon=True, name="hwglance-mini-action").start()

    def _menu(self, hwnd) -> None:
        menu = self._u.CreatePopupMenu()
        self._u.AppendMenuW(menu, MF_STRING, MENU_MAIN, "展開主畫面")
        self._u.AppendMenuW(menu, MF_STRING | (MF_CHECKED if self._locked else 0), MENU_LOCK,
                            "鎖定（滑鼠穿透，從系統匣解除）")
        self._u.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        self._u.AppendMenuW(menu, MF_STRING, MENU_CLOSE, "關閉迷你模式")
        pt = wintypes.POINT()
        self._u.GetCursorPos(ctypes.byref(pt))
        self._u.SetForegroundWindow(hwnd)                 # 不先這樣，點選單外面時選單不會收起來
        cmd = self._u.TrackPopupMenuEx(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY, pt.x, pt.y, hwnd, None)
        self._u.PostMessageW(hwnd, WM_NULL, 0, 0)
        self._u.DestroyMenu(menu)
        action = {MENU_MAIN: self._on_main, MENU_LOCK: self._on_lock, MENU_CLOSE: self._on_close}.get(cmd)
        if action is not None:
            self._later(action)

    def _paint(self) -> None:
        if not self._visible:
            return
        dpi = self._u.GetDpiForWindow(self._hwnd) or 96
        started = time.perf_counter()
        image = self._frame(dpi / 96)
        if image is None:
            return
        pos = self._position(dpi, image.width, image.height) if self._place else None
        self._present(image, pos)
        self._place = False
        took = time.perf_counter() - started
        if took > 0.2:
            print(f"[{time.strftime('%X')}] 迷你模式畫一次花了 {took * 1000:.0f} ms", flush=True)

    def _position(self, dpi: int, w: int, h: int) -> tuple[int, int]:
        saved = load_position(self._state_file)
        if saved is not None:
            r = wintypes.RECT(saved[0], saved[1], saved[0] + w, saved[1] + h)
            if self._u.MonitorFromRect(ctypes.byref(r), MONITOR_DEFAULTTONULL):
                return saved
        mon = self._u.MonitorFromPoint(wintypes.POINT(0, 0), MONITOR_DEFAULTTOPRIMARY)
        info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        self._u.GetMonitorInfoW(mon, ctypes.byref(info))
        margin = round(MARGIN * dpi / 96)
        return info.rcWork.left + margin, info.rcWork.top + margin

    def _present(self, image, pos: tuple[int, int] | None) -> None:
        """RGBA（不是預乘）→ 預乘的 BGRA → DIB → UpdateLayeredWindow。pos=None 就留在原地（使用者拖過的位置）。"""
        from PIL import Image
        w, h = image.size
        r, g, b, a = image.convert("RGBa").split()          # Pillow 的 RGBa＝預乘過的 RGBA
        data = Image.merge("RGBA", (b, g, r, a)).tobytes()  # 換成 Windows 要的 B、G、R、A 順序
        screen = self._u.GetDC(None)
        mem = self._g.CreateCompatibleDC(screen)
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth, bmi.bmiHeader.biHeight = w, -h          # 負的高度＝由上往下
        bmi.bmiHeader.biPlanes, bmi.bmiHeader.biBitCount = 1, 32
        bits = ctypes.c_void_p()
        bitmap = self._g.CreateDIBSection(screen, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        try:
            if not bitmap or not bits.value:
                return
            ctypes.memmove(bits, data, len(data))
            old = self._g.SelectObject(mem, bitmap)
            blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
            dst = wintypes.POINT(*pos) if pos is not None else None
            self._u.UpdateLayeredWindow(self._hwnd, screen, ctypes.byref(dst) if dst is not None else None,
                                        ctypes.byref(wintypes.SIZE(w, h)), mem, ctypes.byref(wintypes.POINT(0, 0)),
                                        0, ctypes.byref(blend), ULW_ALPHA)
            self._g.SelectObject(mem, old)
        finally:
            if bitmap:
                self._g.DeleteObject(bitmap)
            self._g.DeleteDC(mem)
            self._u.ReleaseDC(None, screen)
