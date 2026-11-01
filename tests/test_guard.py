"""讀取的保險（sensors/guard.py）：出錯先停讀、穩定 1 分鐘後關掉重開。
LHM 的部分用假的 Computer／硬體物件跑 2026-10-31 那種情況（讀顯示卡時存取違規），不碰真的硬體、不載入 LHM。"""
from __future__ import annotations

import json
from pathlib import Path

from hwglance.events.model import RawEvent
from hwglance.events.reader import build_query
from hwglance.events.watch import CHANNEL, DRIVER_EVENTS, describe
from hwglance.sensors.demo import DemoSource
from hwglance.sensors.guard import QUIET, RECOVERED_NOTE, RETRY_MAX, Guard
from hwglance.sensors.lhm import LhmSource, describe_error
from hwglance.sensors.service import SensorService

ENV = {"admin": True, "pawnio": "2.2.0"}


class Clock:
    def __init__(self, now: float = 1_793_420_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class AccessViolationException(Exception):
    """pythonnet 把 .NET 的例外變成同名的 Python 例外。"""


class FakeSensor:
    def __init__(self, ident: str, value: float) -> None:
        self.Identifier, self.Name, self.SensorType = ident, "Core", "Temperature"
        self.Value, self.Min, self.Max = value, value, value


class FakeHw:
    def __init__(self, ident: str, name: str, kind: str, value: float, fail: Exception | None = None) -> None:
        self.Identifier, self.Name, self.HardwareType = ident, name, kind
        self.Sensors, self.SubHardware = [FakeSensor(f"{ident}/temperature/0", value)], []
        self.fail = fail
        self.updates = 0

    def Update(self) -> None:                          # noqa: N802 - LHM 的方法名稱
        self.updates += 1
        if self.fail is not None:
            raise self.fail


def cpu() -> FakeHw:
    return FakeHw("/amdcpu/0", "AMD Ryzen 7 5800X", "Cpu", 55.0)


def gpu(fail: Exception | None = None) -> FakeHw:
    return FakeHw("/gpu-nvidia/0", "NVIDIA GeForce RTX 5070 Ti", "GpuNvidia", 61.0, fail)


class FakeComputer:
    """模仿 LHM 的 Computer：Is*Enabled 關掉時先把那一組拿掉再 Close（Close 可能出錯），打開時換一組新的硬體物件。"""

    def __init__(self, broken_gpu: Exception | None = None) -> None:
        self.groups = {"cpu": [cpu()], "gpu": [gpu(broken_gpu)]}
        self.log: list[tuple[str, bool]] = []
        self.close_errors: list[Exception] = []
        self.open_errors: list[Exception] = []

    @property
    def Hardware(self) -> list[FakeHw]:               # noqa: N802
        return [hw for group in self.groups.values() for hw in group]

    def _switch(self, kind: str, on: bool) -> None:
        self.log.append((kind, on))
        if on:
            if self.open_errors:
                raise self.open_errors.pop(0)
            self.groups[kind] = [cpu() if kind == "cpu" else gpu()]
        else:
            self.groups.pop(kind, None)
            if self.close_errors:
                raise self.close_errors.pop(0)

    IsCpuEnabled = property(lambda self: "cpu" in self.groups, lambda self, on: self._switch("cpu", on))
    IsGpuEnabled = property(lambda self: "gpu" in self.groups, lambda self, on: self._switch("gpu", on))


def lhm(computer: FakeComputer, clock: Clock) -> LhmSource:
    source = LhmSource(Path("vendor/lhm"), guard=Guard(clock=clock))
    source._computer = computer                         # 不呼叫 open()：不載入 .NET
    return source


def values(source: LhmSource) -> dict[str, float | None]:
    return {hw.id: hw.sensors[0].value for hw in source.read()}


# ---- Guard ----

def test_guard_waits_until_errors_stop_then_reopens():
    clock = Clock()
    guard = Guard(clock=clock)
    assert guard.trip("gpu", "驅動回報 FECS 致命錯誤") is True
    assert guard.paused("gpu") and not guard.paused("cpu") and guard.due() == []
    [note] = guard.notes()
    assert note.startswith("顯示卡 ") and "FECS 致命錯誤" in note and "先暫停讀取" in note and "穩定 1 分鐘後" in note
    clock.now += 30
    assert guard.trip("gpu", "驅動回報 TDR 重設") is False          # 還在出錯：往後延，原因不變
    clock.now += QUIET - 1
    assert guard.due() == []
    clock.now += 1
    assert guard.due() == ["gpu"]
    guard.reopened("gpu")
    assert not guard.paused("gpu")
    [note] = guard.notes()
    assert "重新連上了" in note and "FECS 致命錯誤" in note
    clock.now += RECOVERED_NOTE + 1
    assert guard.notes() == []


def test_guard_backs_off_when_reopening_fails():
    clock = Clock()
    guard = Guard(clock=clock)
    guard.trip("gpu", "讀取時存取違規")
    clock.now += QUIET
    waits = []
    for _ in range(5):
        assert guard.due() == ["gpu"]
        guard.reopened("gpu", "重新開啟時存取違規")
        start = clock.now
        while not guard.due():
            clock.now += 10
        waits.append(clock.now - start)
    assert waits == [2 * QUIET, 4 * QUIET, 8 * QUIET, RETRY_MAX, RETRY_MAX]
    assert guard.paused("gpu") and "重新連上失敗（重新開啟時存取違規）" in guard.notes()[0]


# ---- LHM：用假的 Computer 跑 10/31 的情況 ----

def test_access_violation_pauses_only_that_kind_and_reopens_after_a_minute():
    clock = Clock()
    computer = FakeComputer(broken_gpu=AccessViolationException("嘗試讀取或寫入受保護的記憶體。"))
    broken = computer.groups["gpu"][0]
    source = lhm(computer, clock)
    source.update()                                     # 10/31 這裡整個程式就結束了
    assert source.guard.paused("gpu") and source.errors == []
    assert "讀取時存取違規" in source.notes()[0]
    assert values(source) == {"/amdcpu/0": 55.0, "/gpu-nvidia/0": None}   # 顯示卡空著，不拿出錯前的舊數字
    for _ in range(5):
        clock.now += 10
        source.update()
    assert broken.updates == 1 and computer.groups["cpu"][0].updates == 6   # 暫停時完全不碰顯示卡
    clock.now += QUIET
    source.update()
    assert computer.log == [("gpu", False), ("gpu", True)]                  # 關掉重開：換一組新的硬體物件
    assert computer.groups["gpu"][0] is not broken and computer.groups["gpu"][0].updates == 1
    assert values(source) == {"/amdcpu/0": 55.0, "/gpu-nvidia/0": 61.0}
    assert "重新連上了" in source.notes()[0]


def test_driver_error_pauses_gpu_before_it_is_touched():
    clock = Clock()
    computer = FakeComputer()
    source = lhm(computer, clock)
    source.guard.trip("gpu", "驅動回報 FECS 致命錯誤")           # events/watch.py 看到驅動報錯
    source.update()
    assert computer.groups["gpu"][0].updates == 0 and computer.groups["cpu"][0].updates == 1


def test_kinds_that_were_never_opened_are_not_switched_on():
    clock = Clock()
    computer = FakeComputer()
    source = lhm(computer, clock)
    source.components = ("cpu",)                         # 例如只開 CPU 的時候，驅動報錯也不要順手打開顯示卡
    source.guard.trip("gpu", "驅動回報 TDR 重設")
    clock.now += QUIET
    source.update()
    assert computer.log == [] and not source.guard.paused("gpu")


def test_reopen_failures_are_retried():
    clock = Clock()
    computer = FakeComputer(broken_gpu=AccessViolationException())
    computer.close_errors = [AccessViolationException()]     # 收 NVML 時又出錯：LHM 已經拿掉那一組，要再關一次
    computer.open_errors = [AccessViolationException()]      # 第一次重新初始化也失敗
    source = lhm(computer, clock)
    source.update()
    clock.now += QUIET
    source.update()
    assert computer.log == [("gpu", False), ("gpu", False), ("gpu", True)]
    assert source.guard.paused("gpu") and "/gpu-nvidia/0" not in values(source)
    assert "重新連上失敗（重新開啟時存取違規），2 分鐘後再試" in source.notes()[0]
    clock.now += 2 * QUIET
    source.update()
    assert not source.guard.paused("gpu") and values(source)["/gpu-nvidia/0"] == 61.0


def test_describe_error():
    class Inner:
        def GetType(self):                              # noqa: N802
            return type("T", (), {"Name": "AccessViolationException"})

    class TargetInvocationException(Exception):
        InnerException = Inner()

    assert describe_error(AccessViolationException()) == "存取違規"
    assert describe_error(TargetInvocationException()) == "存取違規"     # 反射呼叫時外面包一層
    assert describe_error(KeyError("x")) == "KeyError"


# ---- 示範資料：--demo-gpu-reset ----

def test_demo_gpu_reset_shows_note_and_blank_gpu_then_recovers():
    mono, wall = Clock(1000.0), Clock()
    demo = DemoSource(seed=1, clock=mono, gpu_reset_at=0, guard=Guard(clock=wall))
    service = SensorService(demo, env=ENV)
    assert service.open()
    mono.now += 1
    service.tick()
    snap = json.loads(service.snapshot_json())
    card = next(d for d in snap["devices"] if d["kind"] == "gpu")
    assert all(t["value"] is None for t in card["temps"]) and card["temps"]
    assert any("顯示卡" in n and "先暫停讀取" in n for n in snap["source"]["notes"])
    wall.now += QUIET
    mono.now += QUIET
    service.tick()
    snap = json.loads(service.snapshot_json())
    card = next(d for d in snap["devices"] if d["kind"] == "gpu")
    assert all(t["value"] is not None for t in card["temps"])
    assert any("重新連上了" in n for n in snap["source"]["notes"])


# ---- 驅動事件（events/watch.py）----

def test_driver_events_are_described_like_the_event_page():
    fecs = RawEvent(log="System", provider="nvlddmkm", event_id=13, level=2, time=0.0,
                    args=(r"\Device\Video3", "Graphics FECS Exception: UCODE Fatal Error"))
    tdr = RawEvent(log="System", provider="nvlddmkm", event_id=153, level=3, time=0.0,
                   args=(r"\Device\Video3", "GpuRcReset TDR occurred on GPUID:a00"))
    unknown = RawEvent(log="System", provider="amdkmdag", event_id=4097, level=2, time=0.0)
    assert [describe(e) for e in (fecs, tdr)] == ["FECS 致命錯誤", "TDR 重設"]
    assert describe(unknown) == "顯示卡驅動錯誤"
    query = build_query(CHANNEL, DRIVER_EVENTS, None)
    assert "Provider[@Name='nvlddmkm'] and (Level=1 or Level=2 or Level=3)" in query and "EventID=4101" in query
    assert "timediff" not in query                      # 訂閱只收之後的新事件，不用時間條件
