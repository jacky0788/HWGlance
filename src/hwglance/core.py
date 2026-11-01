"""監控本體：感測服務＋只綁 127.0.0.1 的 HTTP 伺服器＋系統匣＋主畫面的視窗子程序＋迷你模式。

主畫面是另一個程序（python -m hwglance --ui）：關掉視窗時 WebView2 跟著結束、記憶體全部還回去，
本體留在系統匣繼續讀溫度（設定「關掉視窗時留在系統匣」關掉、或系統匣開不起來時，就整個結束）。
迷你模式是本體自己的疊加層視窗（mini/）；主畫面與迷你模式同時只開一個（open_ui）。
結束的方法：系統匣選單「結束 HWGlance」、設定頁的「結束」、或 POST /api/app/quit。
"""
from __future__ import annotations

import ctypes
import os
import queue
import secrets
import socket
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from collections.abc import Callable
from pathlib import Path

from . import APP_NAME, instance, paths, system
from .api.app import create_app
from .events.service import EventService
from .logs import streams_to_log
from .sensors.blackbox import BlackBox, DemoBlackBox
from .sensors.service import SensorService
from .settings import Settings, SettingsStore
from .ui import TITLES

DEFAULT_PORT = 8796
CREATE_NO_WINDOW = 0x08000000
WM_CLOSE = 0x0010
TRAY_HINT = ("關掉視窗後，HWGlance 會在這裡繼續監控，圖示上的數字就是 CPU 溫度。右鍵圖示可以結束。",
             "HWGlance 還在系統匣")
NO_WEBVIEW_HINT = ("這台沒有 Microsoft Edge WebView2 Runtime，畫面改用瀏覽器開。裝好 WebView2 後重開 HWGlance 就是獨立視窗。",
                   "HWGlance 用瀏覽器開")


class Server:
    """背景執行緒裡的 uvicorn，叫得停（跟 ProjectHub 同一套）。"""

    def __init__(self, app, port: int) -> None:
        import uvicorn
        self.port = port
        self._server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                                     log_level="warning", access_log=False))
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._server.run, daemon=True, name="hwglance-http")
        self._thread.start()

    def wait_ready(self, timeout: float = 20.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if getattr(self._server, "started", False):
                return True
            if self._thread is not None and not self._thread.is_alive():
                return False
            time.sleep(0.05)
        return False

    def stop(self) -> None:
        self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)


def pick_port(preferred: int) -> int:
    """固定埠（視窗的 localStorage 才會記得住設定）；被別的程式占了就換一個空的。"""
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return int(s.getsockname()[1])
    raise OSError("找不到可以用的埠")


def apply_settings(service: SensorService, s: Settings) -> None:
    """設定存檔後馬上生效：下一輪就用新的更新頻率、門檻、單位、卡片顏色（顏色只放改過的）。"""
    service.interval = s.interval
    service.bands = s.merged_bands()
    service.prefs = {"unit": s.unit, "colors": dict(s.colors)}


def window_python() -> str:
    """開視窗用 pythonw（不會多一個黑色命令列視窗）。"""
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return str(pythonw if pythonw.is_file() else exe)


def find_window(title: str) -> int:
    """用標題找視窗（venv 的 pythonw 會再開一個真正的子程序，Popen 的 pid 對不到視窗）。"""
    if sys.platform != "win32":
        return 0
    return ctypes.windll.user32.FindWindowW(None, title) or 0


def bring_to_front(hwnd: int) -> None:
    user32 = ctypes.windll.user32
    SW_RESTORE = 9
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)


def post_close(hwnd: int) -> None:
    ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


class Window:
    """主畫面的視窗子程序。同時只會有一個。

    open()：已經開著就叫到前面（找不到視窗就請視窗程序自己叫出來）；沒開就開。
    視窗程序用長輪詢 GET /api/ui/commands 拿指令（ui.py 的 poll_commands）。
    子程序自己結束（使用者關掉視窗）時呼叫 on_exit；是我們叫它結束的（close：結束程式、切到迷你模式）就不叫。
    webview=False（這台沒有 WebView2 Runtime）：不開視窗程序，改用預設瀏覽器開同一個網址。
    """

    def __init__(self, port: int, on_exit: Callable[[], None] | None = None, *, token: str = "",
                 spawn: Callable[[str], subprocess.Popen] | None = None,
                 find: Callable[[str], int] = find_window,
                 front: Callable[[int], None] = bring_to_front,
                 close: Callable[[int], None] = post_close,
                 webview: bool = True, browser: Callable[[str], object] = webbrowser.open,
                 quit_timeout: float = 4.0, respawn_gap: float = 2.0) -> None:
        self.port = port
        self.token = token
        self.on_exit = on_exit
        self.webview = webview
        self._browser = browser
        self._spawn = spawn or self._spawn_process
        self._find, self._front, self._close = find, front, close
        self._quit_timeout = quit_timeout
        self._respawn_gap = respawn_gap          # 保險：上一個視窗程序（與它的 WebView2）結束後隔一下才開新的
        self._commands: queue.Queue[dict] = queue.Queue()
        self._proc = None
        self._last_exit = 0.0
        self._lock = threading.Lock()

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def open(self, mode: str = "main") -> None:
        """mode 只為了相容（以前還有迷你視窗）；一律是主畫面。"""
        mode = "main"
        if not self.webview:
            self._browser(f"http://127.0.0.1:{self.port}/")
            return
        with self._lock:
            if self.alive():
                hwnd = self._find(TITLES[mode])
                if hwnd:
                    self._front(hwnd)
                else:
                    self._commands.put({"op": "switch", "mode": mode})
                return
            wait = self._respawn_gap - (time.monotonic() - self._last_exit)
            if wait > 0:
                time.sleep(wait)
            while not self._commands.empty():         # 上一個視窗程序沒拿走的指令作廢
                self._commands.get_nowait()
            proc = self._spawn(mode)
            self._proc = proc
        threading.Thread(target=self._watch, args=(proc,), daemon=True, name="hwglance-window").start()

    def next_commands(self, timeout: float) -> list[dict]:
        """給 API 的長輪詢用：等到有指令或逾時（回傳空清單）。"""
        try:
            first = self._commands.get(timeout=timeout)
        except queue.Empty:
            return []
        out = [first]
        while not self._commands.empty():
            out.append(self._commands.get_nowait())
        return out

    def close(self) -> None:
        """結束程式時：請視窗程序自己關；不理就送 WM_CLOSE；再不理就砍掉。"""
        with self._lock:
            proc, self._proc = self._proc, None
            if proc is None or proc.poll() is not None:
                return
            self._commands.put({"op": "quit"})
            try:
                proc.wait(self._quit_timeout)
                return
            except subprocess.TimeoutExpired:
                pass
            for title in TITLES.values():
                hwnd = self._find(title)
                if hwnd:
                    self._close(hwnd)
            try:
                proc.wait(self._quit_timeout)
            except subprocess.TimeoutExpired:
                proc.kill()

    def _watch(self, proc) -> None:
        proc.wait()
        with self._lock:
            self._last_exit = time.monotonic()
            mine = self._proc is proc
            if mine:
                self._proc = None
        if mine and self.on_exit is not None:
            self.on_exit()

    def _spawn_process(self, mode: str) -> subprocess.Popen:
        exe = window_python()
        flags = CREATE_NO_WINDOW if sys.platform == "win32" and exe.endswith("python.exe") else 0
        env = {**os.environ, "HWGLANCE_TOKEN": self.token}     # 不放在命令列（工作管理員看得到）
        return subprocess.Popen([exe, "-m", "hwglance", "--ui", "--port", str(self.port), "--mode", mode],
                                creationflags=flags, env=env)


def start_tray(open_ui: Callable[[str], None], quit_app: Callable[[], None], mini=None):
    """系統匣開不起來（例如 pystray 沒裝好）就回傳 None，程式照樣能用，只是關掉視窗就結束。"""
    try:
        from .tray import Tray
        tray = Tray(open_ui, quit_app, mini)
        tray.start()
        return tray
    except Exception:                                     # noqa: BLE001
        traceback.print_exc()
        return None


class Views:
    """主畫面與迷你模式同時只開一個（跟以前的迷你視窗一樣）：開迷你模式就把主畫面關掉，開主畫面就把迷你模式收起來。
    迷你模式開不起來（不是 Windows、或建立視窗失敗）就開主畫面。"""

    def __init__(self, window: Window | None, mini=None) -> None:
        self.window, self.mini = window, mini

    def open(self, mode: str) -> None:
        if mode == "mini" and self.mini is not None and self.mini.ready:
            self.mini.show()
            if self.window is not None and self.window.alive():
                threading.Thread(target=self.window.close, daemon=True, name="hwglance-close-main").start()
            return
        if self.mini is not None:
            self.mini.hide()
        if self.window is not None:
            self.window.open("main")


def run(port: int = DEFAULT_PORT, demo: bool = False, serve_only: bool = False,
        mode: str = "main", hidden: bool = False, demo_gpu_reset: float | None = None,
        demo_profile: str = "this-pc") -> int:
    streams_to_log("hwglance")
    store = SettingsStore()
    watch = None
    recorder: BlackBox | None = None
    if demo:
        from .events.demo import DemoEventLog
        from .sensors.demo import PROFILES
        source, event_log = PROFILES[demo_profile](gpu_reset_at=demo_gpu_reset), DemoEventLog()
        box: BlackBox | DemoBlackBox = DemoBlackBox(event_log.downs)     # 示範：算出來的，不寫檔
    else:
        from .events.reader import WindowsEventLog
        from .events.watch import DriverWatch
        from .sensors.lhm import LhmSource
        lhm = LhmSource(paths.lhm_dir())
        source, event_log = lhm, WindowsEventLog()
        # 顯示卡驅動一報錯就先停讀顯示卡，不等到整張卡重設才碰上（sensors/guard.py）
        watch = DriverWatch(lambda what: lhm.guard.trip("gpu", f"驅動回報 {what}"))
        box = recorder = BlackBox(paths.data_dir() / "blackbox")
    service = SensorService(source)
    events = EventService(event_log, service.disk_models)
    apply_settings(service, store.get())
    store.subscribe(lambda s: apply_settings(service, s))
    if recorder is not None:
        service.subscribe(recorder.record)
        recorder.start()
    service.start()
    if watch is not None:
        watch.start()

    port = pick_port(port)
    token = secrets.token_urlsafe(24)
    quit_event = threading.Event()
    webview = serve_only or system.webview2_version() is not None
    if not webview:
        print(NO_WEBVIEW_HINT[0], flush=True)
    window = None if serve_only else Window(port, token=token, webview=webview)
    fps = mini = tray = None
    views = Views(window)

    def window_closed() -> None:
        """使用者關掉主畫面或迷你模式：留在系統匣（第一次提醒一下），或照設定整個結束。"""
        s = store.get()
        if tray is None or not s.close_to_tray:
            quit_event.set()
        elif not s.tray_hint_shown:
            tray.notify(*TRAY_HINT)
            store.update(tray_hint_shown=True)

    if not serve_only:
        from .fps import DemoFps, PresentMonFps
        from .mini import MiniMode
        fps = DemoFps() if demo else PresentMonFps(paths.presentmon_dir(), admin=system.is_admin())
        mini = MiniMode(service, store, fps, on_main=lambda: views.open("main"), on_close=window_closed,
                        on_change=lambda: tray.refresh() if tray is not None else None)
        mini.start()
        views.mini = mini
        tray = start_tray(views.open, quit_event.set, mini)
    if tray is not None and not webview:
        tray.notify(*NO_WEBVIEW_HINT)
    if tray is not None:
        service.subscribe(tray.update)

    if window is not None:
        window.on_exit = window_closed
    server = Server(create_app(service, token, open_ui=views.open if window else None,
                               ui_commands=window.next_commands if window else None,
                               settings=store, on_quit=quit_event.set, events=events, blackbox=box,
                               fps_status=fps.status if fps is not None else None), port)
    server.start()
    if not server.wait_ready():
        service.stop()
        if watch is not None:
            watch.stop()
        if recorder is not None:
            recorder.stop()
        if mini is not None:
            mini.stop()
        if tray is not None:
            tray.stop()
        print("伺服器啟動失敗，看 data/logs/hwglance.log", file=sys.stderr)
        return 1
    try:
        if window is None:
            print(f"{APP_NAME}：http://127.0.0.1:{port}/（Ctrl+C 結束）", flush=True)
        else:
            instance.write(port, token)
            if not hidden or tray is None:
                views.open(mode)
        while not quit_event.wait(0.5):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        instance.remove()
        if window is not None:
            window.close()
        if mini is not None:
            mini.stop()                               # 也停掉 PresentMon
        if tray is not None:
            tray.stop()
        server.stop()
        if watch is not None:
            watch.stop()
        service.stop()
        if recorder is not None:
            recorder.stop()                           # 讀數停了之後：剩下的寫完、存好
    return 0
