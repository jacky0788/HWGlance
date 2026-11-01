"""感測器資料的形狀。LHM 與示範資料都先轉成 RawHardware，後面的整理（normalize.py）只認這個。"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict, dataclass, field


@dataclass(slots=True)
class RawSensor:
    id: str                      # LHM 的 Identifier，例如 /amdcpu/0/temperature/2
    name: str                    # LHM 的英文名稱，例如 Core (Tctl/Tdie)
    type: str                    # LHM 的 SensorType：Temperature、Load、Clock、Power、Fan、Control、Data…
    value: float | None
    min: float | None = None
    max: float | None = None


@dataclass(slots=True)
class RawHardware:
    id: str                      # 例如 /amdcpu/0、/lpc/nct6798d/0、/nvme/0
    name: str
    type: str                    # LHM 的 HardwareType：Cpu、GpuNvidia、Motherboard、SuperIO、Storage…
    sensors: list[RawSensor] = field(default_factory=list)
    children: list[RawHardware] = field(default_factory=list)

    def walk(self) -> Iterator[RawHardware]:
        """自己＋所有子硬體（例如主機板底下的 Super I/O 晶片）。"""
        yield self
        for child in self.children:
            yield from child.walk()

    def without_values(self) -> RawHardware:
        """暫停讀取的硬體（sensors/guard.py）：結構照舊、現在的值清掉（最低最高留著），
        畫面顯示「—」，不會把出錯前的舊數字當成現在的。"""
        return RawHardware(self.id, self.name, self.type,
                           [RawSensor(s.id, s.name, s.type, None, s.min, s.max) for s in self.sensors],
                           [child.without_values() for child in self.children])

    def to_dict(self) -> dict:
        return asdict(self)
