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
        win = webview.create_window(TITLES["main"], self.url(mode), width=width, height=height, min_size=MIN_SIZE,
                                    background_color=BACKGROUND[self._dark])
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
