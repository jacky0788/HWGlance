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

from .. import system
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
    """LHM 的 IHardware（或長得一樣的假物件）轉成 RawHardware。不會呼叫 Update。
    名稱去掉頭尾空白：SATA 硬碟的型號會帶結尾空白（真機的 "CT1000MX500SSD1 "）。"""
    return RawHardware(
        id=_text(hw.Identifier), name=str(hw.Name).strip(), type=_text(hw.HardwareType),
        sensors=[RawSensor(id=_text(s.Identifier), name=str(s.Name).strip(), type=_text(s.SensorType),
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


# ---- 規格（開啟後讀一次）：SMBIOS 由 LHM 讀好；硬碟與顯示卡驅動查 WMI（.NET 的 System.Management，唯讀） ----

STORAGE_NS = r"\\.\root\Microsoft\Windows\Storage"
CIMV2_NS = r"\\.\root\cimv2"
DISK_FIELDS = ("DeviceId", "FriendlyName", "FirmwareVersion", "BusType", "SpindleSpeed", "HealthStatus", "Size")
GPU_FIELDS = ("Name", "DriverVersion", "DriverDate")
BUS_TYPES = {1: "SCSI", 3: "ATA", 7: "USB", 8: "RAID", 10: "SAS", 11: "SATA", 12: "SD", 17: "NVMe"}
DISK_HEALTH = {0: "healthy", 1: "warning", 2: "unhealthy"}
JUNK = frozenset({"", "unknown", "default string", "to be filled by o.e.m.", "not specified", "none"})


def _clean(v: Any) -> str | None:
    """SMBIOS／WMI 的字串：去空白；空的、Unknown、Default string 這類填充字當沒有。"""
    if v is None:
        return None
    s = _text(v).strip()
    return None if s.lower() in JUNK else s


def _int(v: Any) -> int | None:
    try:
        return None if v is None else int(v if isinstance(v, (int, float)) else _text(v))
    except ValueError:
        return None


def _date(v: Any) -> str | None:
    """.NET 的 DateTime → 2024-09-29（測試的假物件直接給字串）。"""
    if v is None:
        return None
    to_string = getattr(v, "ToString", None)
    return str(to_string("yyyy-MM-dd")) if callable(to_string) else str(v)[:10]


def smbios_specs(smbios: Any) -> dict:
    """LHM 讀好的 SMBIOS → {bios, board, cpu, memory}。序號一律不拿。"""
    out: dict = {}
    bios = getattr(smbios, "Bios", None)
    if bios is not None:
        out["bios"] = {"vendor": _clean(bios.Vendor), "version": _clean(bios.Version), "date": _date(bios.Date)}
    board = getattr(smbios, "Board", None)
    if board is not None:
        out["board"] = {"maker": _clean(board.ManufacturerName), "product": _clean(board.ProductName),
                        "version": _clean(board.Version)}
    cpus = list(getattr(smbios, "Processors", None) or [])
    if cpus:
        p = cpus[0]
        out["cpu"] = {"socket": _clean(p.SocketDesignation), "cores": _int(p.CoreCount),
                      "threads": _int(p.ThreadCount), "maxMHz": _int(p.MaxSpeed)}
    out["memory"] = [{"slot": _clean(m.DeviceLocator), "maker": _clean(m.ManufacturerName), "part": _clean(m.PartNumber),
                      "sizeMB": _int(m.Size) or 0, "speed": _int(m.ConfiguredSpeed) or _int(m.Speed) or None,
                      "voltageMV": _int(m.ConfiguredVoltage) or None, "type": _clean(m.Type)}
                     for m in (getattr(smbios, "MemoryDevices", None) or [])]
    return out


def disks_from_wmi(rows: Iterable[dict]) -> dict[str, dict]:
    """MSFT_PhysicalDisk → {磁碟編號: {...}}。磁碟編號就是 LHM Identifier 的最後一段（/nvme/5 → "5"）。"""
    disks: dict[str, dict] = {}
    for r in rows:
        key = _clean(r.get("DeviceId"))
        if key is None:
            continue
        rpm = _int(r.get("SpindleSpeed"))              # SSD 是 0，不知道是 0xFFFFFFFF
        disks[key] = {"model": _clean(r.get("FriendlyName")), "firmware": _clean(r.get("FirmwareVersion")),
                      "bus": BUS_TYPES.get(_int(r.get("BusType"))), "health": DISK_HEALTH.get(_int(r.get("HealthStatus"))),
                      "rpm": rpm if rpm and rpm < 100_000 else None, "bytes": _int(r.get("Size"))}
    return disks


def nvidia_version(windows_version: str | None) -> str | None:
    """Windows 的驅動版本 32.0.16.1088 → NVIDIA 自己的寫法 610.88（最後五位數）。"""
    digits = (windows_version or "").replace(".", "")
    if len(digits) < 5 or not digits.isdigit():
        return windows_version or None
    return f"{int(digits[-5:-2])}.{digits[-2:]}"


def wmi_date(v: str | None) -> str | None:
    """WMI 的日期 20260722000000.000000-000 → 2026-07-22。"""
    s = (v or "").strip()
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) >= 8 and s[:8].isdigit() else None


def gpus_from_wmi(rows: Iterable[dict]) -> dict[str, dict]:
    """Win32_VideoController → {顯示卡名稱: {driver, driverDate}}；NVIDIA 的版本換成 NVIDIA 的寫法。"""
    gpus: dict[str, dict] = {}
    for r in rows:
        name = _clean(r.get("Name"))
        if name is None:
            continue
        version = _clean(r.get("DriverVersion"))
        gpus[name] = {"driver": nvidia_version(version) if "NVIDIA" in name.upper() else version,
                      "driverDate": wmi_date(_clean(r.get("DriverDate")))}
    return gpus


def _py(v: Any) -> Any:
    """WMI 的值：數字、字串直接用，其他 .NET 物件轉成字串。"""
    return v if v is None or isinstance(v, (bool, int, float, str)) else _text(v)


def _wmi(namespace: str, table: str, fields: Iterable[str]) -> list[dict]:
    import clr
    clr.AddReference("System.Management")
    from System.Management import ManagementObjectSearcher, ManagementScope, ObjectQuery  # type: ignore[import-not-found]
    fields = tuple(fields)
    searcher = ManagementObjectSearcher(ManagementScope(namespace), ObjectQuery(f"SELECT {', '.join(fields)} FROM {table}"))
    try:
        return [{f: _py(obj[f]) for f in fields} for obj in searcher.Get()]
    finally:
        searcher.Dispose()


class LhmSource:
    kind = "lhm"

    def __init__(self, lhm_dir: Path, components: Iterable[str] = DEFAULT_COMPONENTS) -> None:
        self.lhm_dir = Path(lhm_dir)
        self.components = tuple(components)
        self.version: str | None = None
        self.errors: list[str] = []
        self.spec_errors: list[str] = []
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

    def specs(self) -> dict:
        """規格（開啟後讀一次）：SMBIOS、硬碟（韌體、轉速、Windows 判斷的健康）、顯示卡驅動、CPU 快取。
        哪一部分讀不到就略過那一部分，不影響讀數。"""
        steps = {
            "SMBIOS": lambda: smbios_specs(self._computer.SMBios),
            "硬碟": lambda: {"disks": disks_from_wmi(_wmi(STORAGE_NS, "MSFT_PhysicalDisk", DISK_FIELDS))},
            "顯示卡驅動": lambda: {"gpus": gpus_from_wmi(_wmi(CIMV2_NS, "Win32_VideoController", GPU_FIELDS))},
        }
        out: dict = {"caches": system.cpu_caches()}
        self.spec_errors = []
        for name, step in steps.items():
            try:
                out.update(step())
            except Exception as e:                   # noqa: BLE001 - 規格讀不到只是少幾行
                self.spec_errors.append(f"{name}：{e}")
                print(f"讀不到規格（{name}）：{e}")
        return out

    def close(self) -> None:
        if self._computer is not None:
            self._computer.Close()
            self._computer = None
