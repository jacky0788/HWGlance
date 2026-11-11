"""Mac（Apple 晶片）的感測來源：SMC 的溫度與風扇、HID 的 SSD 溫度、CPU／GPU 使用率、記憶體、內建 SSD 的空間，
整理成跟 LHM 一樣形狀的硬體樹（RawHardware），後面的 normalize、details、歷史、黑盒子都照用。

真機（2026-11-11，MacBook Pro 14 吋 M6、macOS 27.2，一般權限就讀得到）：
- SMC 有 3209 個鍵、159 個溫度。T 開頭是溫度，第二個字是哪一塊：Tp＝效能核心區（21 個點）、Te＝節能核心區（9 個）、
  Tg＝GPU（18 個）、TB0T～TB2T＝電池。Apple 沒公開對照、每代晶片不一樣（M3 用 Tf），這裡照 Tp／Te／Tg 分；
  一個點不是一個核心（M6 只有 12 核心），所以卡片只放最高與平均，每個點放在詳細資訊。
- 風扇：FNum 個，F0Ac 現在轉速、F0Mn／F0Mx 最低與最高轉速；MacBook Pro 閒置時風扇會停（0 RPM），不是沒接。
- SSD 溫度：HID 感測器「NAND CH0 temp」（Apple 自己的名稱）；SSD 型號與韌體查 system_profiler（不讀序號）。
- 一般權限讀不到時脈與功耗（要 IOReport，之後再做），所以 CPU、GPU 卡片沒有時脈、功耗。
- 記憶體的「已使用」照「活動監視器」：App 記憶體（internal − purgeable）＋聯動＋已壓縮。

低階讀取在 macio.py；這個檔把讀數變成硬體樹（build_tree 是純函式，tests/test_mac.py 用假的讀數測）。
"""
from __future__ import annotations

import platform
import re
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from statistics import fmean

from .model import RawHardware, RawSensor

CPU_ID, GPU_ID, BOARD_ID, RAM_ID, SSD_ID = "/applesoc/0", "/applegpu/0", "/applesmc/0", "/ram", "/nvme/0"
SMC_PREFIXES = ("Tp", "Te", "Tg", "TB")
BATTERY_KEY = re.compile(r"TB\dT")
VALID = (1.0, 150.0)                  # SMC 沒在用的鍵會回 0 或很怪的數字
GIB = 1024 ** 3                       # 記憶體用 GiB（跟 LHM、活動監視器一樣，32 GB 的 Mac 顯示 32.0）
GB = 1000 ** 3                        # 硬碟用十進位 GB（跟 LHM、Finder 一樣）
LEVEL_NAMES = {"Super": "超級核", "Performance": "效能核", "Efficiency": "節能核"}
# Mac 上會出現的溫度種類（thresholds.py）：設定頁只列這幾種的門檻，主機板、晶片組、機械硬碟這些 Mac 沒有
MAC_ROLES = ("cpu", "gpu", "nvme", "battery")


@dataclass(slots=True)
class Readings:
    """一輪讀到的東西（真的來自 macio，示範與測試直接填）。"""
    chip: str = "Apple"
    model: str = "Mac"
    temps: dict[str, float] = field(default_factory=dict)          # SMC 溫度鍵 → °C
    fans: list[tuple[float | None, float | None, float | None]] = field(default_factory=list)  # (現在, 最低, 最高) RPM
    cpu_loads: list[float] | None = None                           # 每個邏輯核心的使用率 %（第一輪還算不出來）
    gpu_load: float | None = None
    memory: tuple[float, float] | None = None                      # (已使用, 總共) 位元組
    ssd: str | None = None                                         # 內建 SSD 型號
    ssd_temp: float | None = None
    disk: tuple[float, float] | None = None                        # (可用, 總共) 位元組


SensorMaker = Callable[[str, str, str, str, float | None], RawSensor]


class Extremes:
    """跟 LHM 一樣記每個感測器開啟以來的最低、最高（normalize、詳細資訊、走勢都看這兩個）。"""

    def __init__(self) -> None:
        self._seen: dict[str, tuple[float, float]] = {}

    def __call__(self, hw_id: str, kind: str, key: str, name: str, value: float | None) -> RawSensor:
        sid = f"{hw_id}/{kind.lower()}/{key}"
        if value is None:
            lo_hi = self._seen.get(sid)
            return RawSensor(sid, name, kind, None, *(lo_hi or (None, None)))
        lo, hi = self._seen.get(sid, (value, value))
        lo, hi = min(lo, value), max(hi, value)
        self._seen[sid] = (lo, hi)
        return RawSensor(sid, name, kind, value, lo, hi)


def valid(value: float | None) -> bool:
    return value is not None and VALID[0] < value < VALID[1]


def points(temps: dict[str, float], prefix: str) -> list[tuple[str, float]]:
    return sorted((k, v) for k, v in temps.items() if k.startswith(prefix) and valid(v))


def short_model(name: str) -> str:
    """MacBook Pro (14-inch, M6) → MacBook Pro。"""
    return re.sub(r"\s*\(.*\)\s*$", "", name.strip()) or name


def memory_used(pages: dict[str, int]) -> int:
    """「活動監視器」的已使用記憶體＝App 記憶體（internal − purgeable）＋聯動（wired）＋已壓縮（compressor）。"""
    app = max(0, pages["internal"] - pages["purgeable"])
    return (app + pages["wired"] + pages["compressed"]) * pages["page"]


def cpu_loads(prev: list[tuple[int, ...]] | None, now: list[tuple[int, ...]]) -> list[float] | None:
    """兩次 (user, system, idle, nice) 相減 → 每個核心的使用率 %。計數器是 32 位元，繞回去也算得對。"""
    if prev is None or len(prev) != len(now):
        return None
    out = []
    for a, b in zip(prev, now):
        d = [(y - x) % 2**32 for x, y in zip(a, b)]
        total = sum(d)
        out.append(0.0 if total == 0 else 100.0 * (total - d[2]) / total)
    return out


def build_tree(r: Readings, sensor: SensorMaker) -> list[RawHardware]:
    """讀數 → 硬體樹。順序照 LHM：主機板（這裡是機身）、CPU、記憶體、GPU、硬碟。"""
    p_points, e_points, g_points = points(r.temps, "Tp"), points(r.temps, "Te"), points(r.temps, "Tg")

    cpu: list[RawSensor] = []
    cores = [v for _, v in p_points + e_points]
    if cores:
        cpu += [sensor(CPU_ID, "Temperature", "max", "Core Max", max(cores)),
                sensor(CPU_ID, "Temperature", "avg", "Core Average", fmean(cores))]
    if p_points:
        cpu.append(sensor(CPU_ID, "Temperature", "pmax", "P-Cores Max", max(v for _, v in p_points)))
    if e_points:
        cpu.append(sensor(CPU_ID, "Temperature", "emax", "E-Cores Max", max(v for _, v in e_points)))
    cpu += [sensor(CPU_ID, "Temperature", k, f"P-Cluster #{i}", v) for i, (k, v) in enumerate(p_points, 1)]
    cpu += [sensor(CPU_ID, "Temperature", k, f"E-Cluster #{i}", v) for i, (k, v) in enumerate(e_points, 1)]
    if r.cpu_loads:
        cpu += [sensor(CPU_ID, "Load", "total", "CPU Total", fmean(r.cpu_loads)),
                sensor(CPU_ID, "Load", "coremax", "CPU Core Max", max(r.cpu_loads))]
        cpu += [sensor(CPU_ID, "Load", f"core{i}", f"CPU Core #{i}", v) for i, v in enumerate(r.cpu_loads, 1)]

    gpu: list[RawSensor] = []
    if g_points:
        values = [v for _, v in g_points]
        gpu += [sensor(GPU_ID, "Temperature", "max", "GPU Core", max(values)),
                sensor(GPU_ID, "Temperature", "avg", "GPU Average", fmean(values))]
        gpu += [sensor(GPU_ID, "Temperature", k, f"GPU Cluster #{i}", v) for i, (k, v) in enumerate(g_points, 1)]
    if r.gpu_load is not None:
        gpu.append(sensor(GPU_ID, "Load", "core", "GPU Core", r.gpu_load))

    board: list[RawSensor] = []
    battery = [v for k, v in r.temps.items() if BATTERY_KEY.fullmatch(k) and valid(v)]
    if battery:
        board.append(sensor(BOARD_ID, "Temperature", "battery", "Battery", max(battery)))
    for i, (now, _lo, _hi) in enumerate(r.fans):
        board.append(sensor(BOARD_ID, "Fan", f"fan{i}", f"Fan #{i + 1}", now))

    ram: list[RawSensor] = []
    if r.memory is not None:
        used, total = r.memory
        ram += [sensor(RAM_ID, "Data", "used", "Memory Used", used / GIB),
                sensor(RAM_ID, "Data", "available", "Memory Available", max(0.0, total - used) / GIB),
                sensor(RAM_ID, "Load", "memory", "Memory", 100.0 * used / total if total else None)]

    ssd: list[RawSensor] = []
    if valid(r.ssd_temp):
        ssd.append(sensor(SSD_ID, "Temperature", "nand", "Temperature", r.ssd_temp))
    if r.disk is not None and r.disk[1]:
        free, total = r.disk
        ssd += [sensor(SSD_ID, "Load", "used", "Used Space", 100.0 * (total - free) / total),
                sensor(SSD_ID, "Data", "free", "Free Space", free / GB),
                sensor(SSD_ID, "Data", "total", "Total Space", total / GB)]

    tree = [RawHardware(BOARD_ID, short_model(r.model), "Motherboard", board),
            RawHardware(CPU_ID, r.chip, "Cpu", cpu),
            RawHardware(RAM_ID, "Total Memory", "Memory", ram),
            RawHardware(GPU_ID, f"{r.chip} GPU", "GpuApple", gpu)]
    if ssd:
        tree.append(RawHardware(SSD_ID, r.ssd or "內建 SSD", "Storage", ssd))
    return tree


def core_layout(levels: list[tuple[str, int]]) -> str | None:
    """[('Super', 2), ('Performance', 4), ('Efficiency', 6)] → 2 超級核＋4 效能核＋6 節能核。"""
    parts = [f"{n} {LEVEL_NAMES.get(name, name)}" for name, n in levels if n]
    return "＋".join(parts) or None


class MacSource:
    """真的 Mac。io 給測試換成假的（tests/test_mac.py）；預設是 macio。"""

    kind = "mac"
    # tray＝選單列（第 2 段，menubar.py）、events＝事件記錄（第 3 段，events/mac.py）；暫存檔、迷你模式 Mac 版沒有
    features: tuple[str, ...] = ("tray", "events")
    roles = MAC_ROLES

    def __init__(self, io=None) -> None:
        if io is None:
            from . import macio as io
        self.io = io
        self.version: str | None = platform.mac_ver()[0] or None
        self.errors: list[str] = []
        self._sensor = Extremes()
        self._r = Readings()
        self._smc = self._hid = self._gpu = None
        self._keys: list[str] = []
        self._ticks = None
        self._specs: dict = {}

    def _try(self, what: str, fn, default=None):
        """一部分讀不到（例如新晶片沒有某個鍵），其他照樣讀；錯誤顯示在畫面上方。"""
        try:
            return fn()
        except Exception as e:                       # noqa: BLE001
            message = f"{what}：{e}"
            if message not in self.errors:
                traceback.print_exc()
                self.errors.append(message)
            return default

    def open(self) -> None:
        io = self.io
        hw = (self._try("Mac 型號", lambda: io.profile("SPHardwareDataType"), []) or [{}])[0]
        self._r.chip = hw.get("chip_type") or io.sysctl_str("machdep.cpu.brand_string") or "Apple"
        self._r.model = hw.get("machine_name") or "Mac"
        nvme = [item for group in self._try("SSD 型號", lambda: io.profile("SPNVMeDataType"), [])
                for item in group.get("_items", [])]
        ssd = nvme[0] if nvme else {}
        self._r.ssd = ssd.get("device_model") or ssd.get("_name")

        def smc():
            s = io.Smc()
            s.open()
            self._keys = s.keys(SMC_PREFIXES)
            self._fans = int(s.read("FNum") or 0)
            return s
        self._fans = 0
        self._smc = self._try("SMC（溫度、風扇）", smc)

        def hid():
            h = io.HidTemps("NAND")
            h.open()
            return h
        self._hid = self._try("SSD 溫度", hid)

        def gpu():
            g = io.Accelerator()
            g.open()
            return g
        self._gpu = self._try("GPU 使用率", gpu)
        levels = self._try("讀不到核心種類", io.core_levels, [])
        cores = sum(n for _, n in levels) or None
        self._specs = {
            "cpu": {"cores": cores, "threads": cores, "layout": core_layout(levels)},
            "gpus": {f"{self._r.chip} GPU": {"cores": self._gpu.cores() if self._gpu else None}},
            "disks": {SSD_ID.rsplit("/", 1)[-1]: {"model": self._r.ssd, "firmware": ssd.get("device_revision"),
                                                  "bus": "Apple Fabric（內建）"}} if ssd else {},
        }

    def update(self, slow: bool = True) -> None:
        io, r = self.io, self._r
        if self._smc is not None:
            r.temps = {k: v for k in self._keys if k[0] == "T" and (v := self._smc.read(k)) is not None}
            r.fans = [(self._smc.read(f"F{i}Ac"), self._smc.read(f"F{i}Mn"), self._smc.read(f"F{i}Mx"))
                      for i in range(self._fans)]
        ticks = self._try("CPU 使用率", io.cpu_ticks)
        if ticks is not None:
            r.cpu_loads = cpu_loads(self._ticks, ticks)
            self._ticks = ticks
        if self._gpu is not None:
            r.gpu_load = self._try("GPU 使用率", self._gpu.utilization)

        def memory():
            total = io.sysctl_int("hw.memsize")
            return (memory_used(io.memory_pages()), total) if total else None
        r.memory = self._try("讀不到記憶體", memory)
        if slow:                                      # SSD 跟 Windows 的硬碟一樣每 10 秒讀一次
            if self._hid is not None:
                nand = self._try("SSD 溫度", self._hid.read, {}) or {}
                r.ssd_temp = max(nand.values()) if nand else None
            r.disk = self._try("SSD 空間", io.volume_space)

    def read(self) -> list[RawHardware]:
        return build_tree(self._r, self._sensor)

    def specs(self) -> dict:
        return self._specs

    def close(self) -> None:
        for part in (self._smc, self._hid, self._gpu):
            if part is not None:
                try:
                    part.close()
                except Exception:                    # noqa: BLE001 - 結束時的錯誤不重要
                    traceback.print_exc()
        self._smc = self._hid = self._gpu = None
