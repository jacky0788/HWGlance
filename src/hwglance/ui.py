"""視窗子程序：pywebview（Windows 走 WebView2）包住 http://127.0.0.1:<埠>/。由本體（core.py）開，不單獨跑。

不打包瀏覽器：WebView2 是 Windows 內建的 Edge 核心。同一個程序裡只會有主畫面或迷你視窗其中一個；
網頁上的「迷你模式」「展開」按鈕呼叫 window.pywebview.api.switch()，在這裡先開新的、再關舊的（不重開程序）。
系統匣、run.bat 要切換或結束時，本體把指令排進佇列，這裡用長輪詢 GET /api/ui/commands 拿（poll_commands）；
連續幾次連不上本體就自己關掉，視窗不會比本體活得久。最後一個視窗關掉，這個程序就結束（WebView2 一起結束）。
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

TITLES = {"main": APP_NAME, "mini": f"{APP_NAME} 迷你"}     # core.py 用標題找視窗
MIN_SIZE = (760, 560)
SCREEN_SHARE = (0.58, 0.82)          # 主畫面佔螢幕的比例（寬、高），不寫死像素
MINI_SIZE = (340, 112)               # 迷你視窗（邏輯像素），剛好放得下兩格＋一行摘要
MINI_MARGIN = (24, 72)               # 預設放在右下角，離螢幕邊緣（右、下）多遠；下面要讓出工作列
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


def read_state() -> dict:
    try:
        data = json.loads(paths.ui_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(state: dict) -> None:
    try:
        f = paths.ui_state_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


def mini_position(screens, saved: object, size: tuple[int, int] = MINI_SIZE) -> tuple[int, int]:
    """上次的位置（還在某個螢幕裡的話）；不然放主螢幕右下角。座標都是邏輯像素。"""
    w, h = size
    if isinstance(saved, (list, tuple)) and len(saved) == 2:
        x, y = int(saved[0]), int(saved[1])
        for s in screens:
            if s.x <= x and s.y <= y and x + w <= s.x + s.width and y + h <= s.y + s.height:
                return x, y
    main = screens[0]
    return main.x + main.width - w - MINI_MARGIN[0], main.y + main.height - h - MINI_MARGIN[1]


class Bridge:
    """網頁按鈕叫得到的函式：window.pywebview.api.switch('mini')、.close()。每個視窗各一個。

    動作交給另一條執行緒、這裡馬上回傳：這個呼叫來自「要被關掉的那個視窗」，如果等它關掉才回傳，
    pywebview 回傳結果的執行緒會永遠等下去，程序就結束不了（2026-10-30 實測）。"""

    def __init__(self, app: UiApp, mode: str) -> None:
        self._app = app
        self._mode = mode

    def switch(self, mode: str) -> bool:
        threading.Thread(target=self._app.switch, args=(mode,), daemon=True).start()
        return True

    def close(self) -> bool:
        threading.Thread(target=self._app.close, args=(self._mode,), daemon=True).start()
        return True


class UiApp:
    def __init__(self, port: int) -> None:
        self.port = port
        self.windows: dict[str, object] = {}
        self._lock = threading.Lock()
        self._state = read_state()
        self._dark = system_is_dark()

    def url(self, mode: str) -> str:
        return f"http://127.0.0.1:{self.port}/" + ("?view=mini" if mode == "mini" else "")

    def create(self, mode: str):
        import webview
        if mode == "mini":
            w, h = MINI_SIZE
            x, y = mini_position(webview.screens, self._state.get("mini"))
            win = webview.create_window(TITLES["mini"], self.url("mini"), js_api=Bridge(self, "mini"),
                                        width=w, height=h, x=x, y=y, min_size=MINI_SIZE, resizable=False,
                                        frameless=True, easy_drag=False, on_top=True,
                                        background_color=BACKGROUND[self._dark])
            win.events.moved += self._remember_mini
        else:
            width, height = window_size(webview)
            win = webview.create_window(TITLES["main"], self.url("main"), js_api=Bridge(self, "main"),
                                        width=width, height=height, min_size=MIN_SIZE,
                                        background_color=BACKGROUND[self._dark])
        win.events.closed += lambda: self._closed(mode)
        self.windows[mode] = win
        print(f"[{time.strftime('%X')}] 開了 {mode} 視窗", flush=True)
        return win

    def switch(self, mode: str) -> None:
        """先開新的、再關舊的（反過來的話最後一個視窗一關，整個程序就結束了）。"""
        if mode not in TITLES:
            return
        with self._lock:
            existing = self.windows.get(mode)
            others = [w for m, w in self.windows.items() if m != mode]
            print(f"[{time.strftime('%X')}] 切換到 {mode}：已開 {list(self.windows)}", flush=True)
            if existing is None:
                self.create(mode)
        if existing is not None:
            existing.restore()
            existing.show()
        for w in others:
            w.destroy()

    def close(self, mode: str) -> None:
        win = self.windows.get(mode)
        if win is not None:
            win.destroy()

    def quit(self) -> None:
        for win in list(self.windows.values()):
            win.destroy()

    def _remember_mini(self, x: int, y: int) -> None:
        self._state["mini"] = [x, y]

    def _closed(self, mode: str) -> None:
        print(f"[{time.strftime('%X')}] 關了 {mode} 視窗", flush=True)
        with self._lock:
            self.windows.pop(mode, None)
            last = not self.windows                  # 切換時新視窗先開，所以這裡不會是空的
        if mode == "mini":
            write_state(self._state)
        if last:
            threading.Thread(target=self._exit_soon, daemon=True).start()

    @staticmethod
    def _exit_soon(grace: float = EXIT_GRACE) -> None:
        """保險：最後一個視窗關了，程序照理會自己結束；被哪條執行緒卡住的話，過一下強制結束。"""
        time.sleep(grace)
        print(f"[{time.strftime('%X')}] 視窗都關了但程序沒結束，強制結束", flush=True)
        os._exit(0)


def poll_commands(app: UiApp, token: str, fetch=None) -> None:
    """拿本體的指令來做：{"op": "switch", "mode": …}、{"op": "quit"}。本體不在了（連續連不上）就結束。"""
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
    first = app.create(mode if mode in TITLES else "main")
    token = os.environ.get("HWGLANCE_TOKEN", "")
    if token:
        # 不用 webview.start(func=…)：那條執行緒不是 daemon，視窗都關了它還會讓程序活著
        def start_polling() -> None:
            first.events.shown.wait(15)              # 等視窗出來（訊息迴圈跑起來）才能在別的執行緒開新視窗
            poll_commands(app, token)

        threading.Thread(target=start_polling, daemon=True, name="hwglance-commands").start()
    icon = paths.icon_file()
    webview.start(private_mode=False, storage_path=str(paths.webview_dir()),
                  icon=str(icon) if icon.is_file() else None)
    print(f"[{time.strftime('%X')}] 視窗都關了，結束視窗程序", flush=True)
    return 0
