"""記憶體的組成與釋放（memory.py，SPEC D32）。

組成用假的計數器測；真的 Windows 只讀這台的組成（只讀）、只收回測試自己開的假程式的記憶體；
清待命快取**絕不真的呼叫**（測試哪天用系統管理員跑就會真的清掉），用假的函式測流程。API 用 DemoMemory。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from hwglance import memory
from hwglance.api.app import create_app
from hwglance.memory import COOLDOWN, DemoMemory, MemoryTool, compose, trim_targets
from hwglance.processes import ActionLog
from hwglance.sensors.demo import DemoSource
from hwglance.sensors.service import SensorService

GB = 2**30
VALUES = {"available": 28 * GB, "standby_normal": 19 * GB, "standby_reserve": 5 * GB, "standby_core": 0,
          "free": 4 * GB, "compressed": 3 * GB, "modified": GB // 2}


def segs(c: dict) -> dict[str, int]:
    return {s["key"]: s["bytes"] for s in c["segments"]}


def test_compose_adds_up_to_total():
    c = compose(64 * GB, VALUES, compression_on=True)
    s = segs(c)
    assert list(s) == ["used", "compressed", "modified", "standby", "free"]
    assert sum(s.values()) == 64 * GB and s["standby"] == 24 * GB and s["used"] == 64 * GB - 28 * GB - 3 * GB - GB // 2
    assert c["compression"] == {"enabled": True, "bytes": 3 * GB}
    off = segs(compose(64 * GB, VALUES, compression_on=False))      # 壓縮沒開：沒有壓縮區
    assert off["compressed"] == 0 and off["used"] == s["used"] + 3 * GB
    assert compose(8 * GB, {}, True)["available"] == 0                 # 什麼都讀不到也不會壞


def test_trim_skips_kernel_compression_and_foreground():
    table = {0: ("[System Process]", 0), 4: ("System", 0), 90: ("Memory Compression", 4), 100: ("explorer.exe", 4),
             200: ("game.exe", 100), 300: ("chrome.exe", 100), 310: ("Registry", 4)}
    assert trim_targets(table, {200}) == [100, 300]


class FakeLog:
    def __init__(self) -> None:
        self.lines: list[dict] = []

    def write(self, **fields) -> None:
        self.lines.append(fields)


def test_release_flow_with_fakes():
    now = [1000.0]
    reads = iter([compose(64 * GB, VALUES, True), compose(64 * GB, {**VALUES, "available": 40 * GB, "free": 16 * GB}, True)])
    trimmed: list[int] = []
    log = FakeLog()
    tool = MemoryTool(log=log, read=lambda: next(reads), table=lambda: {7: ("a.exe", 1), 8: ("b.exe", 1), 9: ("game.exe", 1)},
                      trim=lambda pid: trimmed.append(pid) or pid != 8, purge=lambda: "needs_admin",
                      foreground=lambda: 9, admin=lambda: False, clock=lambda: now[0], sleep=lambda s: None)
    r = tool.release()
    assert trimmed == [7, 8] and r["trimmed"] == 1 and r["tried"] == 2 and r["skippedForeground"]
    assert r["freed"] == 12 * GB and (r["freeBefore"], r["freeAfter"]) == (4 * GB, 16 * GB) and r["standby"] == "needs_admin"
    assert log.lines[0]["action"] == "release-memory" and log.lines[0]["standby"] == "needs_admin"
    with pytest.raises(RuntimeError, match="秒後再試"):
        tool.release()                                                   # 20 秒內不能再按
    now[0] += COOLDOWN + 1
    tool._read = lambda: compose(64 * GB, VALUES, True)
    assert tool.release()["freed"] == 0                                  # 沒變多就是 0，不會是負的


def test_purge_needs_admin_without_touching_anything(monkeypatch):
    called = []
    monkeypatch.setattr(memory.system, "is_admin", lambda: False)
    monkeypatch.setattr(memory, "_enable_privilege", lambda name: called.append(name) or True)
    assert memory.purge_standby() == "needs_admin" and called == []


# ---- 真的 Windows：只讀這台的組成、只收回自己開的假程式 ----

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows 才有")


@windows_only
def test_read_this_pc_composition():
    c = memory.read_composition(memory.Counters())
    s = segs(c)
    assert c["total"] > 2 * GB and sum(s.values()) == c["total"]
    assert 0 < c["available"] <= c["total"] and s["used"] > 0


class _Counters(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]


def working_set(pid: int) -> int:
    k = ctypes.WinDLL("kernel32")
    k.OpenProcess.restype = wt.HANDLE
    h = k.OpenProcess(0x1000 | 0x0010, False, pid)                       # QUERY_LIMITED | VM_READ
    try:
        c = _Counters()
        c.cb = ctypes.sizeof(c)
        assert k.K32GetProcessMemoryInfo(h, ctypes.byref(c), c.cb)
        return int(c.WorkingSetSize)
    finally:
        k.CloseHandle(h)


@windows_only
def test_empty_working_set_of_our_own_child():
    python = getattr(sys, "_base_executable", sys.executable)              # 真的 python（venv 的只是轉一手）
    code = "b = bytearray(200 * 2**20); b[::4096] = b'x' * len(b[::4096]); print('ready', flush=True); import time; time.sleep(30)"
    proc = subprocess.Popen([python, "-c", code], stdout=subprocess.PIPE, text=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        assert proc.stdout.readline().strip() == "ready"
        before = working_set(proc.pid)
        assert before > 150 * 2**20
        assert memory.empty_working_set(proc.pid)
        time.sleep(0.2)
        assert working_set(proc.pid) < before / 4                         # 收回了；程式本身照樣活著
        assert proc.poll() is None
    finally:
        proc.kill()


# ---- API（示範資料，DemoMemory）----

TOKEN = "t"


def api_client(with_memory: bool = True, tmp_log=None):
    service = SensorService(DemoSource(seed=1), env={"admin": True, "pawnio": "2.2.0"})
    assert service.open()
    service.tick()
    return TestClient(create_app(service, TOKEN, memory=DemoMemory() if with_memory else None),
                      base_url="http://127.0.0.1:8796")


def test_api_memory_with_demo():
    c = api_client()
    status = c.get("/api/memory").json()
    assert status["total"] == 64 * GB and status["canPurge"] is True and status["last"] is None
    assert sum(s["bytes"] for s in status["segments"]) == status["total"]
    assert c.post("/api/memory/release").status_code == 403                                     # 沒帶 token
    r = c.post("/api/memory/release", headers={"X-HWGlance-Token": TOKEN}).json()
    assert r["freed"] > 4 * GB and r["freeAfter"] - r["freeBefore"] > 15 * GB and r["standby"] == "purged"
    assert c.get("/api/memory").json()["last"]["freed"] == r["freed"]
    again = c.post("/api/memory/release", headers={"X-HWGlance-Token": TOKEN})
    assert again.status_code == 429 and "秒後再試" in again.json()["detail"]
    none = api_client(with_memory=False)
    assert none.get("/api/memory").status_code == 404
    assert none.post("/api/memory/release", headers={"X-HWGlance-Token": TOKEN}).status_code == 404


def test_release_is_logged(tmp_path):
    log = ActionLog(tmp_path / "actions.log")
    tool = MemoryTool(log=log, read=lambda: compose(8 * GB, VALUES, True), table=lambda: {}, trim=lambda p: True,
                      purge=lambda: "purged", foreground=lambda: None, admin=lambda: True, sleep=lambda s: None)
    tool.release()
    line = json.loads((tmp_path / "actions.log").read_text(encoding="utf-8").splitlines()[0])
    assert line["action"] == "release-memory" and line["standby"] == "purged"
