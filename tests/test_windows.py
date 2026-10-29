"""視窗控制（core.Window）、視窗程序的指令輪詢（ui.poll_commands）、迷你視窗位置（ui.mini_position）。
用假的程序與假的找視窗函式，不開真的視窗。"""
from __future__ import annotations

import subprocess
import threading
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from hwglance.api.app import create_app
from hwglance.core import Window
from hwglance.sensors.demo import DemoSource
from hwglance.sensors.service import SensorService
from hwglance.settings import SettingsStore
from hwglance.ui import MINI_MARGIN, MINI_SIZE, POLL_FAILURES, TITLES, mini_position, poll_commands


class FakeProc:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self._done = threading.Event()

    def poll(self):
        return 0 if self._done.is_set() else None

    def wait(self, timeout=None):
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired("ui", timeout)
        return 0

    def kill(self) -> None:
        self._done.set()

    def exit(self) -> None:            # 使用者把視窗關掉
        self._done.set()


class FakeDesktop:
    """假的桌面：記得開過哪些程序、哪個視窗現在開著。"""

    def __init__(self) -> None:
        self.procs: list[FakeProc] = []
        self.fronted: list[str] = []
        self.closed: list[str] = []

    def spawn(self, mode: str) -> FakeProc:
        p = FakeProc(mode)
        self.procs.append(p)
        return p

    def current(self) -> FakeProc | None:
        return next((p for p in reversed(self.procs) if p.poll() is None), None)

    def find(self, title: str) -> int:
        p = self.current()
        return 1 if p is not None and TITLES[p.mode] == title else 0

    def front(self, hwnd: int) -> None:
        self.fronted.append(self.current().mode)

    def close(self, hwnd: int) -> None:     # 收到 WM_CLOSE 就結束
        p = self.current()
        self.closed.append(p.mode)
        p.exit()


def make(exits: list[str], **kw) -> tuple[Window, FakeDesktop]:
    desk = FakeDesktop()
    w = Window(8796, on_exit=lambda: exits.append("exit"), spawn=desk.spawn, find=desk.find,
               front=desk.front, close=desk.close, quit_timeout=0.1, **kw)
    return w, desk


def wait_until(cond, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def test_open_same_mode_brings_to_front_instead_of_second_window():
    exits: list[str] = []
    w, desk = make(exits)
    w.open("main")
    w.open("main")
    assert [p.mode for p in desk.procs] == ["main"] and desk.fronted == ["main"]


def test_open_other_mode_asks_the_window_process_to_switch():
    exits: list[str] = []
    w, desk = make(exits)
    w.open("main")
    w.open("mini")
    assert [p.mode for p in desk.procs] == ["main"]             # 沒有重開程序
    assert w.next_commands(0.1) == [{"op": "switch", "mode": "mini"}]
    assert w.next_commands(0.05) == []                           # 拿走就沒了


def test_user_closing_the_window_reports_exit_once_and_reopens_later():
    exits: list[str] = []
    w, desk = make(exits, respawn_gap=0.2)
    w.open("mini")
    desk.procs[0].exit()
    assert wait_until(lambda: exits == ["exit"])
    assert not w.alive()
    started = time.monotonic()
    w.open("main")                           # 剛關掉：等一下再開（WebView2 要時間收尾）
    assert desk.procs[-1].mode == "main" and time.monotonic() - started >= 0.1


def test_quit_asks_politely_then_closes_the_window():
    exits: list[str] = []
    w, desk = make(exits)
    w.open("main")
    w.close()                                # 假程序不會自己看指令 → 逾時後送 WM_CLOSE
    time.sleep(0.1)
    assert exits == [] and desk.closed == ["main"] and not w.alive()
    assert w.next_commands(0.05) == [{"op": "quit"}]


def test_stale_commands_are_dropped_when_a_new_window_starts():
    exits: list[str] = []
    w, desk = make(exits, respawn_gap=0)
    w.open("main")
    w.open("mini")                           # 排了一個切換，但視窗程序還沒拿就被使用者關掉
    desk.procs[0].exit()
    assert wait_until(lambda: not w.alive())
    w.open("main")
    assert w.next_commands(0.05) == []


def test_unknown_mode_falls_back_to_main():
    exits: list[str] = []
    w, desk = make(exits)
    w.open("huge")
    assert desk.procs[0].mode == "main"


class FakeUi:
    def __init__(self) -> None:
        self.port = 8796
        self.calls: list[str] = []

    def switch(self, mode: str) -> None:
        self.calls.append(mode)

    def quit(self) -> None:
        self.calls.append("quit")


def test_poll_commands_follows_switch_and_quit():
    ui = FakeUi()
    batches = iter([[{"op": "switch", "mode": "mini"}], [], [{"op": "switch", "mode": "main"}, {"op": "quit"}]])
    poll_commands(ui, "t", fetch=lambda: next(batches))
    assert ui.calls == ["mini", "main", "quit"]


def test_poll_commands_gives_up_when_the_core_is_gone(monkeypatch):
    monkeypatch.setattr("hwglance.ui.time.sleep", lambda s: None)
    ui = FakeUi()
    tries = []

    def broken():
        tries.append(1)
        raise ConnectionRefusedError

    poll_commands(ui, "t", fetch=broken)
    assert ui.calls == ["quit"] and len(tries) == POLL_FAILURES


def test_commands_endpoint_needs_token():
    service = SensorService(DemoSource(seed=1), env={"admin": True, "pawnio": None})
    queued = [{"op": "switch", "mode": "mini"}]
    app = create_app(service, "tok", ui_commands=lambda timeout: queued, settings=SettingsStore())
    c = TestClient(app, base_url="http://127.0.0.1:8796")
    assert c.get("/api/ui/commands", params={"timeout": 0}).status_code == 403
    r = c.get("/api/ui/commands", params={"timeout": 0}, headers={"X-HWGlance-Token": "tok"})
    assert r.json() == {"commands": queued}


def screen(x: int, y: int, width: int, height: int) -> SimpleNamespace:
    return SimpleNamespace(x=x, y=y, width=width, height=height)


def test_mini_position_defaults_to_bottom_right_and_reuses_saved_spot():
    screens = [screen(0, 0, 1707, 960), screen(1707, 0, 1920, 1080)]
    w, h = MINI_SIZE
    assert mini_position(screens, None) == (1707 - w - MINI_MARGIN[0], 960 - h - MINI_MARGIN[1])
    assert mini_position(screens, [2000, 100]) == (2000, 100)            # 在第二個螢幕裡
    assert mini_position(screens, [5000, 100]) == mini_position(screens, None)   # 螢幕拔掉了就回右下角
