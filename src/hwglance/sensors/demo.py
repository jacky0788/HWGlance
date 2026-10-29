"""示範資料：照這台電腦的硬體（Ryzen 7 5800X、RTX 5070 Ti、TUF GAMING X570-PLUS、6 顆硬碟）模擬讀數。

硬體與感測器的名稱、Identifier 都跟 LHM v0.9.6 一樣，所以後面的整理流程跟真的完全相同。
負載在閒置與高負載之間交替（像開遊戲、關遊戲），溫度用一階延遲慢慢追，像真的散熱一樣。
開發介面（python -m hwglance --serve --demo）與測試用。
"""
from __future__ import annotations

import math
import random
import time
from collections.abc import Callable

from .model import RawHardware, RawSensor

# (Identifier, 型號, 閒置溫度, 高負載時多幾度, 已用 %, 總容量 GB)
DRIVES = (
    ("/nvme/0", "KIOXIA-EXCERIA G2 SSD", 40.0, 8.0, 61.0, 1863.0),
    ("/nvme/1", "KINGSTON SA2000M8500G", 47.0, 9.0, 83.0, 465.0),
    ("/ssd/2", "CT1000MX500SSD1", 33.0, 3.0, 47.0, 931.0),
    ("/ssd/3", "CT500MX500SSD1", 32.0, 3.0, 72.0, 465.0),
    ("/hdd/4", "TOSHIBA DT02ABA400", 37.0, 2.0, 88.0, 3726.0),
    ("/hdd/5", "ST20000DM001-3Y3103", 39.0, 2.0, 64.0, 18626.0),
)
BOARD_FANS = (  # (名稱, 閒置轉速, 高負載時多幾轉)；0 表示接頭沒接
    ("CPU Fan", 720.0, 900.0),
    ("CPU Optional Fan", 0.0, 0.0),
    ("Chassis Fan #1", 850.0, 320.0),
    ("Chassis Fan #2", 830.0, 300.0),
    ("Chassis Fan #3", 0.0, 0.0),
    ("Chipset Fan", 2250.0, 150.0),
    ("AIO Pump", 0.0, 0.0),
)
GPU_VRAM_MB = 16303.0
RAM_GB = 63.9


class _Lag:
    """一階延遲：溫度不會瞬間跳到目標值，而是以時間常數 tau 秒慢慢追。"""

    def __init__(self, value: float, tau: float) -> None:
        self.value, self.tau = value, tau

    def step(self, target: float, dt: float) -> float:
        self.value += (target - self.value) * (1 - math.exp(-dt / self.tau))
        return self.value


class DemoSource:
    kind = "demo"
    version = "demo"

    def __init__(self, seed: int | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self._rng = random.Random(seed)
        self._clock = clock
        self._t0 = self._last = clock()
        self._cpu = _Lag(42.0, 6.0)
        self._gpu = _Lag(38.0, 14.0)
        self._board = _Lag(35.0, 90.0)
        self._chipset = _Lag(56.0, 60.0)
        self._drives = [_Lag(idle, 120.0) for _, _, idle, *_ in DRIVES]
        self._extremes: dict[str, tuple[float, float]] = {}
        self._v: dict[str, float] = {}
        self.update()

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def _busy(self, t: float, period: float, phase: float) -> float:
        """0～1 的負載，閒置與高負載交替，加一點雜訊。"""
        x = 0.5 + 0.5 * math.sin(2 * math.pi * (t / period + phase))
        busy = 1 / (1 + math.exp(-14 * (x - 0.55)))
        return min(1.0, max(0.0, 0.06 + 0.88 * busy + self._rng.uniform(-0.03, 0.03)))

    def _noise(self, amount: float) -> float:
        return self._rng.uniform(-amount, amount)

    def update(self, slow: bool = True) -> None:
        now = self._clock()
        dt = min(5.0, max(0.05, now - self._last))
        self._last = now
        t = now - self._t0
        v = self._v
        cpu_load = self._busy(t, 150.0, 0.0)
        gpu_load = self._busy(t, 150.0, 0.02)
        v["cpu_load"] = cpu_load
        v["gpu_load"] = gpu_load
        v["cpu"] = self._cpu.step(40.0 + 44.0 * cpu_load, dt) + self._noise(0.4)
        v["gpu"] = self._gpu.step(36.0 + 36.0 * gpu_load, dt) + self._noise(0.2)
        v["board"] = self._board.step(34.0 + 6.0 * cpu_load, dt)
        v["chipset"] = self._chipset.step(55.0 + 5.0 * cpu_load, dt)
        if slow:
            for i, (_, _, idle, extra, *_rest) in enumerate(DRIVES):
                v[f"drive{i}"] = self._drives[i].step(idle + extra * cpu_load, dt * 10)

    def _sensor(self, hw_id: str, kind: str, index: int, name: str, value: float | None) -> RawSensor:
        sid = f"{hw_id}/{kind.lower()}/{index}"
        if value is None:
            return RawSensor(sid, name, kind, None)
        lo, hi = self._extremes.get(sid, (value, value))
        lo, hi = min(lo, value), max(hi, value)
        self._extremes[sid] = (lo, hi)
        return RawSensor(sid, name, kind, value, lo, hi)

    def read(self) -> list[RawHardware]:
        return [self._cpu_hw(), self._gpu_hw(), self._board_hw(), *self._memory_hw(), *self._drives_hw()]

    def _cpu_hw(self) -> RawHardware:
        v, hid, s = self._v, "/amdcpu/0", self._sensor
        load = v["cpu_load"]
        return RawHardware(hid, "AMD Ryzen 7 5800X", "Cpu", [
            s(hid, "Temperature", 2, "Core (Tctl/Tdie)", v["cpu"]),
            s(hid, "Temperature", 3, "CCD1 (Tdie)", v["cpu"] - 2.5),
            s(hid, "Load", 0, "CPU Total", min(100.0, 100 * load + self._noise(2))),
            s(hid, "Load", 1, "CPU Core Max", min(100.0, 100 * load + 12)),
            s(hid, "Clock", 1, "Cores (Average)", 3600 + 1000 * min(1.0, load * 1.3) + self._noise(25)),
            s(hid, "Power", 0, "Package", 32 + 90 * load + self._noise(2)),
            s(hid, "Voltage", 0, "Core (SVI2 TFN)", 1.0 + 0.35 * load),
        ])

    def _gpu_hw(self) -> RawHardware:
        v, hid, s = self._v, "/gpu-nvidia/0", self._sensor
        load, temp = v["gpu_load"], v["gpu"]
        spinning = temp > 50 or load > 0.3          # 低溫時零轉速模式
        rpm = 1000 + 1500 * load + self._noise(20) if spinning else 0.0
        used = 2400 + 9600 * load
        return RawHardware(hid, "NVIDIA GeForce RTX 5070 Ti", "GpuNvidia", [
            s(hid, "Temperature", 0, "GPU Core", temp),
            s(hid, "Temperature", 2, "GPU Hot Spot", temp + 8 + 6 * load),
            s(hid, "Temperature", 3, "GPU Memory Junction", temp + 10 + 4 * load),
            s(hid, "Load", 0, "GPU Core", min(100.0, 100 * load + self._noise(1))),
            s(hid, "Load", 3, "GPU Memory", 100 * used / GPU_VRAM_MB),
            s(hid, "Clock", 0, "GPU Core", 2100 + 700 * load + self._noise(15)),
            s(hid, "Clock", 1, "GPU Memory", 14001.0),
            s(hid, "Power", 0, "GPU Package", 25 + 260 * load + self._noise(3)),
            s(hid, "Fan", 1, "GPU Fan 1", rpm),
            s(hid, "Fan", 2, "GPU Fan 2", rpm * 0.98),
            s(hid, "Control", 1, "GPU Fan 1", 30 + 40 * load if spinning else 0.0),
            s(hid, "Control", 2, "GPU Fan 2", 30 + 40 * load if spinning else 0.0),
            s(hid, "SmallData", 0, "GPU Memory Free", GPU_VRAM_MB - used),
            s(hid, "SmallData", 1, "GPU Memory Used", used),
            s(hid, "SmallData", 2, "GPU Memory Total", GPU_VRAM_MB),
        ])

    def _board_hw(self) -> RawHardware:
        v, lid, s = self._v, "/lpc/nct6798d/0", self._sensor
        load = v["cpu_load"]
        sensors = [
            s(lid, "Temperature", 0, "CPU", v["cpu"] - 7),
            s(lid, "Temperature", 1, "Motherboard", v["board"]),
            s(lid, "Temperature", 2, "Chipset", v["chipset"]),
            s(lid, "Voltage", 0, "Vcore", 1.0 + 0.35 * load),
            s(lid, "Voltage", 4, "+12V", 12.1),
            s(lid, "Voltage", 1, "+5V", 5.04),
            s(lid, "Voltage", 3, "+3.3V", 3.34),
        ]
        cpu_heat = min(1.0, max(0.0, (v["cpu"] - 45) / 40))
        for i, (name, idle, extra) in enumerate(BOARD_FANS):
            rpm = idle + extra * (cpu_heat if name == "CPU Fan" else load) + (self._noise(8) if idle else 0)
            sensors.append(s(lid, "Fan", i, name, rpm))
            sensors.append(s(lid, "Control", i, name, 100 * rpm / 2600 if idle else 0.0))
        superio = RawHardware(lid, "Nuvoton NCT6798D", "SuperIO", sensors)
        return RawHardware("/motherboard", "ASUS TUF GAMING X570-PLUS (WI-FI)", "Motherboard", [], [superio])

    def _memory_hw(self) -> list[RawHardware]:
        s = self._sensor
        used = 36.0 + 6.0 * self._v["cpu_load"]
        return [
            RawHardware("/ram", "Total Memory", "Memory", [
                s("/ram", "Load", 0, "Memory", 100 * used / RAM_GB),
                s("/ram", "Data", 0, "Memory Used", used),
                s("/ram", "Data", 1, "Memory Available", RAM_GB - used),
            ]),
            RawHardware("/vram", "Virtual Memory", "Memory", [
                s("/vram", "Load", 1, "Memory", 52.0),
                s("/vram", "Data", 2, "Memory Used", 48.0),
                s("/vram", "Data", 3, "Memory Available", 44.0),
            ]),
        ]

    def _drives_hw(self) -> list[RawHardware]:
        out = []
        for i, (hid, model, _idle, _extra, used, total) in enumerate(DRIVES):
            s = self._sensor
            temp = self._v[f"drive{i}"]
            nvme = hid.startswith("/nvme")
            busy = max(0.0, 3 + 20 * self._v["cpu_load"] * (1 if i < 2 else 0.2) + self._noise(2))
            sensors = [
                s(hid, "Temperature", 0, "Composite Temperature" if nvme else "Temperature", temp),
                s(hid, "Load", 30, "Used Space", used),
                s(hid, "Load", 53, "Total Activity", busy),
                s(hid, "Data", 31, "Free Space", total * (1 - used / 100)),
                s(hid, "Data", 32, "Total Space", total),
            ]
            if nvme:
                sensors += [s(hid, "Temperature", 10, "Warning Temperature", 83.0),
                            s(hid, "Temperature", 11, "Critical Temperature", 85.0)]
            out.append(RawHardware(hid, model, "Storage", sensors))
        return out
