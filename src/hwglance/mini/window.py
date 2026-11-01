"""迷你模式的視窗：Win32 分層視窗（WS_EX_LAYERED＋UpdateLayeredWindow），每個像素有自己的透明度——
背景可以半透明、字照樣清楚。WebView2 的視窗只能整個一起半透明（2026-11-01 實測，SPEC「第 9 段的前置實測」），
所以這裡不用網頁，用 Pillow 畫好整張圖貼上去（render.py）。

自己一條執行緒＋訊息迴圈；別的執行緒只用 PostMessage 叫它（顯示、隱藏、重畫、鎖定、結束）。
這條執行緒設成「每個螢幕各自的 DPI」，高 DPI 螢幕上字才清楚（本體其他部分不受影響）。
滑鼠：拖曳移動、雙擊展開主畫面、右鍵選單；鎖定時滑鼠穿透（WS_EX_TRANSPARENT）。
鎖定後滑鼠碰不到它，所以有全域快速鍵（HOTKEYS：Ctrl+Alt+L 鎖定／解除、Ctrl+Alt+M 開關迷你模式），
系統匣與設定頁也能解除（使用者 2026-11-02：鎖定後什麼都改不了）。
置頂：每次重畫都再拉一次；別的程式（例如遊戲）跑到前景時馬上再拉一次（SetWinEventHook，只收通知、不注入）。
獨佔全螢幕的遊戲沒辦法：畫面直接交給遊戲，任何視窗都蓋不上去（SPEC D29）。
位置記在 data/mini.json（螢幕的實際像素）；顯示與拖完時整個框拉回最近那個螢幕的工作區裡（不會一截跑到螢幕外）。
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
FRAME_MS = 500                      # 多久重畫一次（FPS 會變，比讀數的 1 秒快一點），每次也再確認一次置頂
RAISE_AGAIN_MS = 400                # 別的程式跑到前景後再拉一次（有些遊戲啟動後才把自己設成置頂）
MARGIN = 24                         # 預設位置離螢幕左上角多遠（96 DPI 的像素）
# 全域快速鍵：(id, 顯示的名稱, 虛擬鍵碼, 做什麼)；Ctrl+Alt 在一般遊戲裡很少用
HOTKEYS = ((1, "Ctrl+Alt+L", 0x4C, "鎖定／解除鎖定"), (2, "Ctrl+Alt+M", 0x4D, "開關迷你模式"))
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000

WS_POPUP = 0x80000000
WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOPMOST = 0x00080000, 0x00000020, 0x00000008
WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x00000080, 0x08000000
GWL_EXSTYLE = -20
WM_DESTROY, WM_CLOSE, WM_NULL, WM_TIMER = 0x0002, 0x0010, 0x0000, 0x0113
WM_NCHITTEST, WM_CONTEXTMENU, WM_NCLBUTTONDBLCLK = 0x0084, 0x007B, 0x00A3
WM_ENTERSIZEMOVE, WM_EXITSIZEMOVE, WM_DPICHANGED, WM_DISPLAYCHANGE, WM_HOTKEY = 0x0231, 0x0232, 0x02E0, 0x007E, 0x0312
WM_APP = 0x8000
EVENT_SYSTEM_FOREGROUND, WINEVENT_OUTOFCONTEXT, WINEVENT_SKIPOWNPROCESS = 0x0003, 0x0000, 0x0002
TIMER_FRAME, TIMER_RAISE = 1, 2
CMD_SHOW, CMD_HIDE, CMD_REFRESH, CMD_LOCK = WM_APP + 1, WM_APP + 2, WM_APP + 3, WM_APP + 4
HTCAPTION = 2
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
ULW_ALPHA, AC_SRC_OVER, AC_SRC_ALPHA = 0x2, 0x0, 0x1
MF_STRING, MF_CHECKED, MF_SEPARATOR = 0x0, 0x8, 0x800
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x0002, 0x0100, 0x0080
MONITOR_DEFAULTTONULL, MONITOR_DEFAULTTOPRIMARY, MONITOR_DEFAULTTONEAREST = 0, 1, 2
DPI_AWARENESS_PER_MONITOR_V2 = -4
MENU_MAIN, MENU_LOCK, MENU_CLOSE, MENU_SETTINGS = 1, 2, 3, 4

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
WINEVENTPROC = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG,
                                  wintypes.LONG, wintypes.DWORD, wintypes.DWORD)


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
        (u, "RegisterHotKey"): ([wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT], wintypes.BOOL),
        (u, "UnregisterHotKey"): ([wintypes.HWND, ctypes.c_int], wintypes.BOOL),
        (u, "SetWinEventHook"): ([wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, WINEVENTPROC, wintypes.DWORD,
                                  wintypes.DWORD, wintypes.DWORD], H),
        (u, "UnhookWinEvent"): ([H], wintypes.BOOL),
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


def fit(x: int, y: int, w: int, h: int, work: tuple[int, int, int, int]) -> tuple[int, int]:
    """整個框拉進工作區（左、上、右、下）；框比工作區還大就對齊左上。"""
    left, top, right, bottom = work
    return max(left, min(x, right - w)), max(top, min(y, bottom - h))


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
                 on_toggle: Callable[[], None] | None = None, on_settings: Callable[[], None] | None = None,
                 locked: bool = False) -> None:
        self._frame = frame
        self._state_file = state_file
        self._on_main, self._on_close, self._on_lock = on_main, on_close, on_lock
        self._on_toggle, self._on_settings = on_toggle, on_settings
        self._locked = locked
        self._visible = False
        self._hwnd = 0
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._place = True                  # 下一次貼圖時要不要指定位置（剛顯示、螢幕變了）
        self._dragging = False
        self._hook = None
        self.hotkeys: list[dict] = []       # [{keys, action, ok}]：設定頁顯示；ok=False 是被別的程式佔用了
        # 留著參考，不然被回收後 Windows 會呼叫到不存在的函式
        self._wndproc = WNDPROC(self._proc)
        self._eventproc = WINEVENTPROC(self._foreground_changed)

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
            if self._hwnd:
                self._register_hotkeys()
                # 別的程式跑到前景（例如遊戲）就再拉一次置頂；只收通知（OUTOFCONTEXT），不碰別的程式
                self._hook = self._u.SetWinEventHook(EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND, None,
                                                     self._eventproc, 0, 0,
                                                     WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS)
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

    def _register_hotkeys(self) -> None:
        self.hotkeys = []
        for key_id, name, vk, action in HOTKEYS:
            ok = bool(self._u.RegisterHotKey(self._hwnd, key_id, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, vk))
            self.hotkeys.append({"keys": name, "action": action, "ok": ok})
            if not ok:
                print(f"[{time.strftime('%X')}] 快速鍵 {name} 被別的程式佔用了", flush=True)

    def _raise(self) -> None:
        if self._visible:
            self._u.SetWindowPos(self._hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    def _foreground_changed(self, hook, event, hwnd, obj, child, thread, time_ms) -> None:
        try:
            self._raise()
            if self._visible:
                self._u.SetTimer(self._hwnd, TIMER_RAISE, RAISE_AGAIN_MS, None)
        except Exception:                                 # noqa: BLE001
            traceback.print_exc()

    def _proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_NCHITTEST:
                return HTCAPTION                          # 整個框都是標題列：按住就能拖
            if msg == WM_TIMER:
                if wparam == TIMER_RAISE:
                    self._u.KillTimer(hwnd, TIMER_RAISE)
                    self._raise()
                    return 0
                self._raise()
                self._paint()
                return 0
            if msg == WM_HOTKEY:
                action = {1: self._on_lock, 2: self._on_toggle}.get(wparam)
                if action is not None:
                    self._later(action)
                return 0
            if msg == CMD_SHOW:
                if not self._visible:
                    self._visible, self._place = True, True
                    self._paint()
                    self._u.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
                    self._u.SetTimer(hwnd, TIMER_FRAME, FRAME_MS, None)
                return 0
            if msg == CMD_HIDE:
                if self._visible:
                    self._visible = False
                    self._u.KillTimer(hwnd, TIMER_FRAME)
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
            if msg == WM_ENTERSIZEMOVE:
                self._dragging = True
                return 0
            if msg == WM_EXITSIZEMOVE:                    # 拖完：一截跑到螢幕外就拉回來，再記下位置
                self._dragging = False
                r = wintypes.RECT()
                self._u.GetWindowRect(hwnd, ctypes.byref(r))
                x, y = fit(r.left, r.top, r.right - r.left, r.bottom - r.top, self._work_area(r))
                if (x, y) != (r.left, r.top):
                    self._u.SetWindowPos(hwnd, None, x, y, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE | 0x0004)
                save_position(self._state_file, x, y)
                return 0
            if msg in (WM_DPICHANGED, WM_DISPLAYCHANGE):
                self._place = msg == WM_DISPLAYCHANGE      # 螢幕拔插：重新檢查位置還在不在螢幕上
                self._paint()
                return 0
            if msg == WM_CLOSE:
                self._u.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                self._u.KillTimer(hwnd, TIMER_FRAME)
                self._u.KillTimer(hwnd, TIMER_RAISE)
                for key_id, *_ in HOTKEYS:
                    self._u.UnregisterHotKey(hwnd, key_id)
                if self._hook:
                    self._u.UnhookWinEvent(self._hook)
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
        keys = {h["action"]: h["keys"] for h in self.hotkeys if h["ok"]}
        lock_key, mini_key = keys.get(HOTKEYS[0][3]), keys.get(HOTKEYS[1][3])
        menu = self._u.CreatePopupMenu()
        self._u.AppendMenuW(menu, MF_STRING, MENU_MAIN, "展開主畫面")
        self._u.AppendMenuW(menu, MF_STRING, MENU_SETTINGS, "迷你模式設定…")
        self._u.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        self._u.AppendMenuW(menu, MF_STRING | (MF_CHECKED if self._locked else 0), MENU_LOCK,
                            f"鎖定：滑鼠穿透（{lock_key} 解除）" if lock_key else "鎖定：滑鼠穿透（從系統匣解除）")
        self._u.AppendMenuW(menu, MF_STRING, MENU_CLOSE, f"關閉迷你模式（{mini_key}）" if mini_key else "關閉迷你模式")
        pt = wintypes.POINT()
        self._u.GetCursorPos(ctypes.byref(pt))
        self._u.SetForegroundWindow(hwnd)                 # 不先這樣，點選單外面時選單不會收起來
        cmd = self._u.TrackPopupMenuEx(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY, pt.x, pt.y, hwnd, None)
        self._u.PostMessageW(hwnd, WM_NULL, 0, 0)
        self._u.DestroyMenu(menu)
        action = {MENU_MAIN: self._on_main, MENU_LOCK: self._on_lock, MENU_CLOSE: self._on_close,
                  MENU_SETTINGS: self._on_settings or self._on_main}.get(cmd)
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
        if self._place:
            pos = self._position(dpi, image.width, image.height)
        elif self._dragging:
            pos = None                                    # 使用者正在拖：不要跟他搶
        else:                                             # 項目變多、框變寬：右邊超出螢幕就往左挪
            r = wintypes.RECT()
            self._u.GetWindowRect(self._hwnd, ctypes.byref(r))
            grown = wintypes.RECT(r.left, r.top, r.left + image.width, r.top + image.height)
            fitted = fit(r.left, r.top, image.width, image.height, self._work_area(grown))
            pos = fitted if fitted != (r.left, r.top) else None
        self._present(image, pos)
        self._place = False
        took = time.perf_counter() - started
        if took > 0.2:
            print(f"[{time.strftime('%X')}] 迷你模式畫一次花了 {took * 1000:.0f} ms", flush=True)

    def _work_area(self, rect: wintypes.RECT | None = None) -> tuple[int, int, int, int]:
        """離 rect 最近的螢幕的工作區（扣掉工作列）；沒給 rect 就是主螢幕。"""
        if rect is None:
            mon = self._u.MonitorFromPoint(wintypes.POINT(0, 0), MONITOR_DEFAULTTOPRIMARY)
        else:
            mon = self._u.MonitorFromRect(ctypes.byref(rect), MONITOR_DEFAULTTONEAREST)
        info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        self._u.GetMonitorInfoW(mon, ctypes.byref(info))
        w = info.rcWork
        return w.left, w.top, w.right, w.bottom

    def _position(self, dpi: int, w: int, h: int) -> tuple[int, int]:
        saved = load_position(self._state_file)
        if saved is not None:
            r = wintypes.RECT(saved[0], saved[1], saved[0] + w, saved[1] + h)
            if self._u.MonitorFromRect(ctypes.byref(r), MONITOR_DEFAULTTONULL):   # 還碰得到某個螢幕：拉進那個螢幕裡
                return fit(saved[0], saved[1], w, h, self._work_area(r))
        left, top, _, _ = self._work_area()                # 螢幕拔掉了：回主螢幕左上角
        margin = round(MARGIN * dpi / 96)
        return left + margin, top + margin

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
