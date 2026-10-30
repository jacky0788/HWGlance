"""背景服務與示範資料：用假時鐘跑，不等真的時間、不碰硬體。"""
from __future__ import annotations

import json

from hwglance.sensors.demo import DemoSource
from hwglance.sensors.normalize import normalize
from hwglance.sensors.service import SensorService

ENV = {"admin": True, "pawnio": "2.2.0"}


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_demo_looks_like_this_machine():
    snap = normalize(DemoSource(seed=1).read())
    assert [t["label"] for t in snap["overview"]] == [                 # 跟 2026-10-31 真機 probe 的總覽一樣
        "CPU", "GPU 核心", "主機板", "晶片組",
        "KIOXIA-EXCERIA G2", "KINGSTON SA2000M8500G", "CT1000MX500",
        "TOSHIBA DT02ABA400", "ST20000DM001-3Y3103", "CT500MX500"]
    board = next(d for d in snap["devices"] if d["kind"] == "board")
    assert board["unplugged"] == 2 and len(board["fans"]) == 5          # 機殼風扇 3、水冷幫浦沒接


def test_demo_values_stay_plausible_over_ten_minutes():
    clock = FakeClock()
    demo = DemoSource(seed=7, clock=clock)
    seen: dict[str, list[float]] = {}
    for i in range(600):
        clock.now += 1.0
        demo.update(slow=i % 10 == 0)
        for d in normalize(demo.read())["devices"]:
            for t in d["temps"]:
                seen.setdefault(t["label"] + d["id"], []).append(t["value"])
    cpu = seen["核心 Tctl/Tdie/amdcpu/0"]
    assert 35 < min(cpu) and max(cpu) < 90 and max(cpu) - min(cpu) > 20     # 有忽冷忽熱
    assert all(20 < v < 110 for values in seen.values() for v in values)
    raw = demo.read()
    for hw in raw:
        for part in hw.walk():
            for s in part.sensors:
                if s.value is not None:
                    assert s.min <= s.value <= s.max


def test_service_ticks_into_snapshot_json_and_history():
    clock = FakeClock()
    service = SensorService(DemoSource(seed=1, clock=clock), env=ENV, interval=1.0)
    assert service.snapshot()["state"] == "starting" and service.snapshot()["devices"] == []
    assert service.open()
    for _ in range(3):
        clock.now += 1.0
        service.tick()
    snap = json.loads(service.snapshot_json())
    assert snap["seq"] == 3 and snap["state"] == "running"
    assert snap["source"]["kind"] == "demo" and snap["source"]["notes"][0].startswith("示範資料")
    assert snap["perf"]["readMs"] >= 0 and len(snap["overview"]) == 10
    window = service.history.window(60)
    assert len(window["t"]) == 3
    assert len(window["series"]["/amdcpu/0/temperature/2"]) == 3
    assert service.raw()[0]["id"] == "/motherboard"
    assert service.details("/amdcpu/0")["sections"][0]["rows"][1:] == []        # 規格還沒讀：只有型號
    service.load_specs()
    assert service.details("/amdcpu/0")["sections"][0]["rows"][1]["value"] == "AM4"
    gpu = service.details("/gpu-nvidia/0")
    assert "顯存被誰佔用" in [s["title"] for s in gpu["sections"]]          # 示範資料也有佔用顯存的程式


class BrokenSource:
    kind = "lhm"
    version = None

    def __init__(self, fail_open: bool) -> None:
        self.fail_open = fail_open

    def open(self) -> None:
        if self.fail_open:
            raise FileNotFoundError("找不到 LibreHardwareMonitorLib.dll")

    def update(self, slow: bool = True) -> None:
        raise RuntimeError("PawnIO 沒回應")

    def read(self):
        return []

    def close(self) -> None:
        pass


def test_open_failure_is_shown_not_raised():
    service = SensorService(BrokenSource(fail_open=True), env={"admin": False, "pawnio": None})
    assert service.open() is False
    snap = service.snapshot()
    assert snap["state"] == "error"
    notes = snap["source"]["notes"]
    assert any("系統管理員" in n for n in notes) and any("PawnIO" in n for n in notes)
    assert any("找不到 LibreHardwareMonitorLib.dll" in n for n in notes)


def test_read_failure_keeps_running_and_reports():
    service = SensorService(BrokenSource(fail_open=False), env=ENV)
    assert service.open()
    service.tick()
    snap = service.snapshot()
    assert snap["state"] == "error" and any("PawnIO 沒回應" in n for n in snap["source"]["notes"])
