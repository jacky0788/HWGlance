"""LibreHardwareMonitorLib（LHM）的介面卡。整個程式只有這個檔碰 .NET。

用 pythonnet 以 .NET Framework 4.8（Windows 內建）載入 vendor/lhm 裡的 net472 版 DLL
（scripts/fetch_lhm.py 從官方 release 解出來）。CPU、主機板的溫度要 PawnIO 驅動＋系統管理員；
不是系統管理員時 LHM 照樣開得起來，只是讀得到的東西少（大概只剩顯示卡與各種用量）。

LHM 的物件只在感測執行緒裡用（open／update／read 都由 SensorService 的同一個執行緒呼叫）。
"""
from __future__ import annotations

import math
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .model import RawHardware, RawSensor

# 名稱 → LHM Computer 上的開關
COMPONENTS = {
    "cpu": "IsCpuEnabled",
    "gpu": "IsGpuEnabled",
    "motherboard": "IsMotherboardEnabled",
    "controller": "IsControllerEnabled",     # USB 水冷頭、風扇控制器（NZXT Kraken、Corsair…）
    "memory": "IsMemoryEnabled",
    "storage": "IsStorageEnabled",
    "psu": "IsPsuEnabled",
    "network": "IsNetworkEnabled",
    "battery": "IsBatteryEnabled",
}
DEFAULT_COMPONENTS = ("cpu", "gpu", "motherboard", "controller", "memory", "storage", "psu")
ALL_COMPONENTS = tuple(COMPONENTS)
# 這些硬體讀得慢、溫度也變得慢：只在 slow 的那一輪更新（硬碟 SMART 每次要幾十毫秒）
SLOW_TYPES = frozenset({"Storage"})


def _text(x: Any) -> str:
    """.NET 的列舉、Identifier 用 ToString() 取名稱；測試用的假物件直接是字串。"""
    to_string = getattr(x, "ToString", None)
    return str(to_string()) if callable(to_string) else str(x)


def _num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def convert(hw: Any) -> RawHardware:
    """LHM 的 IHardware（或長得一樣的假物件）轉成 RawHardware。不會呼叫 Update。"""
    return RawHardware(
        id=_text(hw.Identifier), name=str(hw.Name), type=_text(hw.HardwareType),
        sensors=[RawSensor(id=_text(s.Identifier), name=str(s.Name), type=_text(s.SensorType),
                           value=_num(s.Value), min=_num(s.Min), max=_num(s.Max)) for s in hw.Sensors],
        children=[convert(sub) for sub in hw.SubHardware])


def update_tree(hw: Any, slow: bool, errors: list[str]) -> None:
    """LHM 的 Motherboard.Update() 是空的，子硬體（Super I/O、EC）要自己一層層更新。"""
    if slow or _text(hw.HardwareType) not in SLOW_TYPES:
        try:
            hw.Update()
        except Exception as e:                       # noqa: BLE001 - 一個硬體讀失敗不該拖垮其他的
            errors.append(f"{hw.Name}: {e}")
    for sub in hw.SubHardware:
        update_tree(sub, slow, errors)


class LhmSource:
    kind = "lhm"

    def __init__(self, lhm_dir: Path, components: Iterable[str] = DEFAULT_COMPONENTS) -> None:
        self.lhm_dir = Path(lhm_dir)
        self.components = tuple(components)
        self.version: str | None = None
        self.errors: list[str] = []
        self._computer: Any = None

    def open(self) -> None:
        dll = self.lhm_dir / "LibreHardwareMonitorLib.dll"
        if not dll.is_file():
            raise FileNotFoundError(f"找不到 {dll}，先執行 scripts\\fetch_lhm.py")
        if str(self.lhm_dir) not in sys.path:
            sys.path.append(str(self.lhm_dir))        # pythonnet 從 sys.path 找相依的 DLL
        import pythonnet
        if pythonnet.get_runtime_info() is None:
            pythonnet.load("netfx")
        import clr                                    # pythonnet 的入口，import 之後才能 AddReference
        clr.AddReference("LibreHardwareMonitorLib")
        from LibreHardwareMonitor.Hardware import Computer   # type: ignore[import-not-found]
        from System.Diagnostics import FileVersionInfo        # type: ignore[import-not-found]
        version = str(FileVersionInfo.GetVersionInfo(str(dll)).FileVersion or "")
        self.version = version.removesuffix(".0") or None
        computer = Computer()
        for name in self.components:
            setattr(computer, COMPONENTS[name], True)
        computer.Open()
        self._computer = computer

    def update(self, slow: bool = True) -> None:
        errors: list[str] = []
        for hw in self._computer.Hardware:
            update_tree(hw, slow, errors)
        self.errors = errors

    def read(self) -> list[RawHardware]:
        return [convert(hw) for hw in self._computer.Hardware]

    def report(self) -> str:
        """LHM 自己的完整報告（晶片型號、驅動狀態…），探測時存檔用。"""
        return str(self._computer.GetReport())

    def close(self) -> None:
        if self._computer is not None:
            self._computer.Close()
            self._computer = None
