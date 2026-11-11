"""視窗子程序：pywebview（Windows 走 WebView2）包住 http://127.0.0.1:<埠>/。由本體（core.py）開，不單獨跑。

不打包瀏覽器：WebView2 是 Windows 內建的 Edge 核心。這個程序只有主畫面；迷你模式是本體自己的疊加層視窗
（mini/，不用 WebView2）。網頁上按「迷你模式」是叫本體的 API，本體顯示疊加層、再叫這個程序結束。
系統匣、run.bat 要叫出或結束時，本體把指令排進佇列，這裡用長輪詢 GET /api/ui/commands 拿（poll_commands）；
連續幾次連不上本體就自己關掉，視窗不會比本體活得久。視窗關掉，這個程序就結束（WebView2 一起結束）。
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

from . import APP_NAME, paths
from .logs import streams_to_log

TOKEN_HEADER = "x-hwglance-token"     # 跟 api/security.py 一樣（這裡不 import api，免得視窗程序載入 FastAPI）
POLL_TIMEOUT = 25.0                   # 長輪詢一次最多等幾秒
POLL_FAILURES = 3                     # 連續幾次連不上本體就結束
EXIT_GRACE = 5.0                      # 最後一個視窗關掉後，最多再等幾秒就強制結束程序

TITLES = {"main": APP_NAME}           # core.py 用標題找視窗
SETTINGS_HASH = "#settings/mini"      # 開主畫面後直接開設定頁的迷你模式那一段（迷你模式右鍵的「迷你模式設定…」）
MIN_SIZE = (760, 560)
SCREEN_SHARE = (0.58, 0.82)          # 主畫面佔螢幕的比例（寬、高），不寫死像素
APP_ID = "jacky0788.HWGlance"        # Windows 工具列的身分
BACKGROUND = {True: "#0f1115", False: "#f3f4f7"}   # 跟 tokens.css 的 --bg 一樣，網頁載入前不閃白


def taskbar_identity() -> None:
    """Windows 預設把所有 Python 程式歸成同一組、顯示 Python 圖示；給自己一個身分，工具列才分得出來。
    要在開任何視窗之前呼叫（做法跟 ProjectHub 的 desktop.py 一樣）。"""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except (AttributeError, OSError):
        pass


def system_is_dark() -> bool:
    """視窗底色（網頁載入前那一下）要跟系統的深淺色一樣。Mac 看「外觀」設定（深色時 AppleInterfaceStyle＝Dark）。"""
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["/usr/bin/defaults", "read", "-g", "AppleInterfaceStyle"],
                                 capture_output=True, text=True, timeout=3, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return out.stdout.strip() == "Dark"
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def window_size(webview) -> tuple[int, int]:
    try:
        screen = webview.screens[0]
        w, h = int(screen.width * SCREEN_SHARE[0]), int(screen.height * SCREEN_SHARE[1])
    except Exception:                                     # noqa: BLE001 - 拿不到螢幕資訊就用保守值
        w, h = 1100, 760
    return max(w, MIN_SIZE[0]), max(h, MIN_SIZE[1])


# ---- Mac 的視窗外觀：標題列透明、網頁延伸到標題列底下，紅黃綠按鈕疊在網頁的頂端列左邊（跟 Finder、備忘錄一樣） ----
# 網頁的頂端列跟標題列一樣高（網址帶 ?shell=mac&titlebar=<點>，前端 lib/macShell.ts 調版面）。WKWebView 會吃掉頂端列上的
# 滑鼠，所以換掉它的 mouseDown：按在頂端列、又不是按鈕的地方（網頁用 js_api 回報按鈕在哪）＝拖曳視窗，
# 連按兩下照「系統設定 › 桌面與 Dock」的「連按兩下視窗標題列」縮放或縮到 Dock。

MAC_TITLEBAR = 28                   # 量不到時的標題列高度（點）


MAC_LIGHTS = 78                     # 量不到時，紅黃綠按鈕佔的寬度（點，含左邊距）


def mac_url(url: str, titlebar: int, lights: int = MAC_LIGHTS) -> str:
    """http://127.0.0.1:埠/#hash → http://127.0.0.1:埠/?shell=mac&titlebar=32&lights=70#hash"""
    base, sep, frag = url.partition("#")
    return f"{base}?shell=mac&titlebar={titlebar}&lights={lights}{sep}{frag}"


def mac_lights_width() -> int:
    """紅黃綠按鈕右邊到視窗左邊的距離（點）。每版 macOS 不一樣（27 的按鈕比以前大），用一個不顯示的視窗量。
    要在主執行緒（UiApp.create 在 webview.start 之前呼叫）。"""
    try:
        import AppKit
        mask = (AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable
                | AppKit.NSWindowStyleMaskMiniaturizable | AppKit.NSWindowStyleMaskResizable)
        probe = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, 400, 300), mask, AppKit.NSBackingStoreBuffered, True)
        button = probe.standardWindowButton_(AppKit.NSWindowZoomButton)
        width = round(AppKit.NSMaxX(button.frame())) if button is not None else 0
        probe.close()
        return width or MAC_LIGHTS
    except Exception:                                     # noqa: BLE001
        return MAC_LIGHTS


MAC_BUNDLE_ID = "com.jacky0788.hwglance"    # scripts/make-mac-app.sh 的 CFBundleIdentifier


def mac_identity() -> None:
    """從終端機開（不是 HWGlance.app）時，選單列與 Dock 至少寫 HWGlance、用 HWGlance 的圖示，不是「Python」。
    要在 import webview 之前（它一 import 就建好 NSApplication 與選單）。從 App 開的就照 App 的 Info.plist。"""
    try:
        import AppKit
        from Foundation import NSBundle
        info = NSBundle.mainBundle().infoDictionary()
        if info is None or info.get("CFBundleIdentifier") == MAC_BUNDLE_ID:
            return
        info["CFBundleName"] = APP_NAME
        icon = paths.APP_ROOT / "assets" / "app-mac.png"
        if icon.is_file():
            image = AppKit.NSImage.alloc().initWithContentsOfFile_(str(icon))
            AppKit.NSApplication.sharedApplication().setApplicationIconImage_(image)
    except Exception:                                     # noqa: BLE001 - 只是名稱與圖示
        pass


def mac_titlebar_height() -> int:
    try:
        import AppKit
        mask = (AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable
                | AppKit.NSWindowStyleMaskMiniaturizable | AppKit.NSWindowStyleMaskResizable)
        frame = AppKit.NSWindow.frameRectForContentRect_styleMask_(AppKit.NSMakeRect(0, 0, 100, 100), mask)
        return round(frame.size.height - 100) or MAC_TITLEBAR
    except Exception:                                     # noqa: BLE001
        return MAC_TITLEBAR


class MacShell:
    """給網頁呼叫的 js_api（window.pywebview.api.drag_region）：頂端列多高、哪些長方形是按鈕（CSS 像素＝點）。"""

    def __init__(self, titlebar: int) -> None:
        self._height = float(titlebar)
        self._holes: list[tuple[float, float, float, float]] = []

    def drag_region(self, height, holes) -> bool:
        self._height = max(0.0, min(200.0, float(height)))
        self._holes = [tuple(float(v) for v in h[:4]) for h in list(holes)[:200] if len(h) >= 4]
        return True

    def _drags(self, x: float, y: float) -> bool:
        """(x, y) 從視窗左上角量；在頂端列、又不在任何按鈕上。"""
        return y <= self._height and not any(hx <= x <= hx + hw and hy <= y <= hy + hh for hx, hy, hw, hh in self._holes)


_mac_shell: MacShell | None = None


def mac_install_drag(shell: MacShell) -> None:
    """換掉 pywebview 的 WKWebView 的 mouseDown（整個程序一次）。"""
    global _mac_shell
    first = _mac_shell is None
    _mac_shell = shell
    if not first:
        return
    import AppKit
    import objc
    from webview.platforms import cocoa
    host = cocoa.BrowserView.WebKitHost

    def mouseDown_(self, event):                          # noqa: N802 - Objective-C 的名稱
        window = self.window()
        shell_now = _mac_shell
        if window is not None and shell_now is not None:
            loc = event.locationInWindow()
            y = window.contentView().frame().size.height - loc.y
            if shell_now._drags(loc.x, y):
                if event.clickCount() == 2:
                    action = AppKit.NSUserDefaults.standardUserDefaults().stringForKey_("AppleActionOnDoubleClick")
                    if action == "Minimize":
                        window.performMiniaturize_(None)
                    elif action != "None":
                        window.performZoom_(None)
                else:
                    window.performWindowDragWithEvent_(event)
                return
        objc.super(host, self).mouseDown_(event)

    host.mouseDown_ = objc.selector(mouseDown_, selector=b"mouseDown:", signature=b"v@:@")


def mac_window(win) -> None:
    """（主執行緒）標題列透明、內容延伸到標題列底下、不顯示標題字（網頁頂端列有 logo 與名稱）。"""
    import AppKit
    native = win.native
    native.setStyleMask_(native.styleMask() | AppKit.NSWindowStyleMaskFullSizeContentView)
    native.setTitlebarAppearsTransparent_(True)
    native.setTitleVisibility_(AppKit.NSWindowTitleHidden)
    # pywebview 把標題列那一層塗成系統的視窗底色；透明標題列要讓網頁的頂端列透出來
    titlebar = native.contentView().superview().subviews().lastObject()
    if titlebar is not None and titlebar.respondsToSelector_(b"setBackgroundColor:"):
        titlebar.setBackgroundColor_(AppKit.NSColor.clearColor())


class UiApp:
    def __init__(self, port: int) -> None:
        self.port = port
        self.windows: dict[str, object] = {}
        self._lock = threading.Lock()
        self._dark = system_is_dark()

    def url(self, mode: str) -> str:
        return f"http://127.0.0.1:{self.port}/" + (SETTINGS_HASH if mode == "settings" else "")

    def create(self, mode: str = "main"):
        import webview
        width, height = window_size(webview)
        url, extra = self.url(mode), {}
        if sys.platform == "darwin":
            bar = mac_titlebar_height()
            url, extra["js_api"] = mac_url(url, bar, mac_lights_width()), MacShell(bar)
            mac_install_drag(extra["js_api"])
        win = webview.create_window(TITLES["main"], url, width=width, height=height, min_size=MIN_SIZE,
                                    background_color=BACKGROUND[self._dark], **extra)
        if sys.platform == "darwin":
            from PyObjCTools import AppHelper
            win.events.before_show += lambda: AppHelper.callAfter(mac_window, win)
        win.events.closed += lambda: self._closed("main")
        self.windows["main"] = win
        print(f"[{time.strftime('%X')}] 開了主畫面", flush=True)
        return win

    def switch(self, mode: str = "main") -> None:
        """本體叫出主畫面：已經開著就還原、拉到前面；mode="settings" 再打開設定頁（迷你模式在本體，不在這裡）。"""
        with self._lock:
            existing = self.windows.get("main")
            if existing is None:
                self.create(mode)
        if existing is not None:
            if mode == "settings":
                existing.evaluate_js(f"location.hash = '{SETTINGS_HASH}'")
            existing.restore()
            existing.show()

    def close(self, mode: str = "main") -> None:
        win = self.windows.get(mode)
        if win is not None:
            win.destroy()

    def quit(self) -> None:
        for win in list(self.windows.values()):
            win.destroy()

    def _closed(self, mode: str) -> None:
        print(f"[{time.strftime('%X')}] 關了 {mode} 視窗", flush=True)
        with self._lock:
            self.windows.pop(mode, None)
            last = not self.windows
        if last:
            threading.Thread(target=self._exit_soon, daemon=True).start()

    @staticmethod
    def _exit_soon(grace: float = EXIT_GRACE) -> None:
        """保險：最後一個視窗關了，程序照理會自己結束；被哪條執行緒卡住的話，過一下強制結束。"""
        time.sleep(grace)
        print(f"[{time.strftime('%X')}] 視窗都關了但程序沒結束，強制結束", flush=True)
        os._exit(0)


def poll_commands(app: UiApp, token: str, fetch=None) -> None:
    """拿本體的指令來做：{"op": "switch", "mode": "main"}（叫出主畫面）、{"op": "quit"}。本體不在了（連續連不上）就結束。"""
    url = f"http://127.0.0.1:{app.port}/api/ui/commands?timeout={POLL_TIMEOUT:g}"

    def default_fetch() -> list[dict]:
        req = urllib.request.Request(url, headers={TOKEN_HEADER: token})
        with urllib.request.urlopen(req, timeout=POLL_TIMEOUT + 10) as r:
            return json.loads(r.read()).get("commands", [])

    fetch = fetch or default_fetch
    failures = 0
    while True:
        try:
            commands = fetch()
            failures = 0
        except (OSError, ValueError):
            failures += 1
            if failures >= POLL_FAILURES:
                app.quit()
                return
            time.sleep(1.0)
            continue
        for c in commands:
            print(f"[{time.strftime('%X')}] 本體指令：{c}", flush=True)
            if c.get("op") == "switch":
                app.switch(str(c.get("mode", "main")))
            elif c.get("op") == "quit":
                app.quit()
                return


def run(port: int, mode: str = "main") -> int:
    streams_to_log("ui")
    taskbar_identity()
    if sys.platform == "darwin":
        mac_identity()
    try:
        import webview
    except ImportError:
        print("桌面視窗需要 pywebview；或改用瀏覽器：python -m hwglance --serve", file=sys.stderr)
        return 1
    app = UiApp(port)
    first = app.create(mode if mode in ("main", "settings") else "main")
    token = os.environ.get("HWGLANCE_TOKEN", "")
    if token:
        # 不用 webview.start(func=…)：那條執行緒不是 daemon，視窗都關了它還會讓程序活著
        def start_polling() -> None:
            first.events.shown.wait(15)              # 等視窗出來（訊息迴圈跑起來）才能在別的執行緒動視窗
            poll_commands(app, token)

        threading.Thread(target=start_polling, daemon=True, name="hwglance-commands").start()
    icon = paths.icon_file()
    webview.start(private_mode=False, storage_path=str(paths.webview_dir()),
                  icon=str(icon) if icon.is_file() else None)
    print(f"[{time.strftime('%X')}] 視窗都關了，結束視窗程序", flush=True)
    return 0
