"""示範資料：照這台電腦（Ryzen 7 5800X、RTX 5070 Ti、TUF GAMING X570-PLUS、6 顆硬碟）模擬讀數。

硬體與感測器的名稱、Identifier、排列順序都照 2026-10-31 在這台跑的 probe（LHM v0.9.6），
所以後面的整理流程跟真的完全相同。數值也取真機附近：晶片組平常就 75～80°C、KIOXIA 閒置也有 55°C 上下、
RTX 50 系列沒有熱點溫度、CCD 溫度每秒亂跳。規格（specs）照同一天的 LHM 報告與 HWMonitor 報告。
負載在閒置與高負載之間交替（像開遊戲、關遊戲），溫度用一階延遲慢慢追，像真的散熱一樣。
開發介面（python -m hwglance --serve --demo）與測試用。

gpu_reset_at＝開啟後第幾秒模擬顯示卡重設（--demo-gpu-reset）：顯示卡讀取「存取違規」→ 跟真的一樣交給
guard.py 停讀、1 分鐘後重新連上，用來看畫面上的提醒與「—」。
"""
from __future__ import annotations

import math
import random
import time
from collections.abc import Callable

from .guard import Guard
from .model import RawHardware, RawSensor

# (Identifier, 型號, 閒置溫度, 高負載時多幾度, 已用 %, 總容量 GB)；順序與編號照真機（LHM 用實體磁碟編號）
DRIVES = (
    ("/nvme/5", "KIOXIA-EXCERIA G2 SSD", 55.0, 6.0, 44.4, 2000.4),
    ("/nvme/4", "KINGSTON SA2000M8500G", 40.0, 8.0, 56.8, 500.1),
    ("/ssd/0", "CT1000MX500SSD1", 27.0, 3.0, 53.5, 1000.2),
    ("/hdd/3", "TOSHIBA DT02ABA400", 29.0, 2.0, 48.0, 4000.8),
    ("/hdd/1", "ST20000DM001-3Y3103", 37.0, 2.0, 36.1, 20000.6),
    ("/ssd/2", "CT500MX500SSD1", 37.0, 3.0, 40.4, 500.1),
)
NVME_LIMITS = {"/nvme/5": (71.0, 89.0), "/nvme/4": (74.0, 79.0)}   # 硬碟自己回報的警告／危險溫度
NVME_HEALTH = {"/nvme/5": (100.0, 5.0, 2.0), "/nvme/4": (100.0, 10.0, 18.0)}   # 備用空間、下限、已用壽命 %
DRIVE_SMART = {  # (剩餘壽命 %, 總讀取 GB, 總寫入 GB, 通電次數, 通電時數)；None＝這種硬碟沒有
    "/nvme/5": (98.0, 38351.0, 16868.0, 333.0, 3881.0),
    "/nvme/4": (82.0, 68681.0, 60078.0, 1905.0, 25836.0),
    "/ssd/0": (98.0, None, 7249.0, 553.0, 7679.0),
    "/hdd/3": (None, None, None, 1575.0, 19476.0),
    "/hdd/1": (None, None, None, 93.0, 1213.0),
    "/ssd/2": (94.0, None, 6784.0, 1784.0, 12864.0),
}
DISK_INFO = {  # Windows（MSFT_PhysicalDisk）的資訊：(韌體, 介面, 轉速)；這兩顆機械硬碟 Windows 回報「不知道轉速」
    "/nvme/5": ("ECFA17.1", "NVMe", None), "/nvme/4": ("S5Z42105", "NVMe", None),
    "/ssd/0": ("M3CR046", "SATA", None), "/hdd/3": ("KQ000A", "SATA", None),
    "/hdd/1": ("DN02", "SATA", None), "/ssd/2": ("M3CR023", "SATA", None),
}
BOARD_FANS = (  # (名稱, LHM 編號, 閒置轉速, 高負載時多幾轉, 全速轉速)；全速 0 表示接頭沒接
    ("CPU Fan", 1, 1000.0, 650.0, 2160.0),
    ("CPU Optional Fan", 6, 1490.0, 0.0, 1490.0),
    ("Chassis Fan #1", 0, 1150.0, 300.0, 1800.0),
    ("Chassis Fan #2", 2, 1350.0, 300.0, 2100.0),
    ("Chassis Fan #3", 3, 0.0, 0.0, 0.0),
    ("Chipset Fan", 4, 3600.0, 150.0, 5070.0),
    ("AIO Pump", 5, 0.0, 0.0, 0.0),
)
BOARD_VOLTAGES = ((0, "Vcore", None), (1, "+5V", 5.04), (2, "AVSB", 3.408), (3, "+3.3V", 3.328), (4, "+12V", 12.192),
                  (7, "+3V Standby", 3.408), (8, "CMOS Battery", 3.36), (9, "CPU Termination", 0.912))
CORES, THREADS = 8, 16
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

    def __init__(self, seed: int | None = None, clock: Callable[[], float] = time.monotonic,
                 gpu_reset_at: float | None = None, guard: Guard | None = None) -> None:
        self._rng = random.Random(seed)
        self._clock = clock
        self.guard = guard or Guard()
        self._gpu_reset_at = gpu_reset_at
        self._hidden: dict[int, float] = {}            # 示範「關閉」過的程式：pid → 什麼時候關的（2 分鐘後再出現）
        self._t0 = self._last = clock()
        self._cpu = _Lag(52.0, 6.0)
        self._gpu = _Lag(48.0, 14.0)
        self._board = _Lag(44.0, 90.0)
        self._chipset = _Lag(76.0, 60.0)
        self._drives = [_Lag(idle, 120.0) for _, _, idle, *_ in DRIVES]
        self._extremes: dict[tuple[str, str], tuple[float, float]] = {}
        self._v: dict[str, float] = {}
        self.update()

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def specs(self) -> dict:
        """規格：照這台 2026-10-31 的 SMBIOS（LHM 報告）與 Windows 的硬碟、顯示卡資訊（HWMonitor 報告）。"""
        empty = {"maker": None, "part": None, "sizeMB": 0, "speed": None, "voltageMV": None, "type": None}
        dimm = {"maker": "A-DATA", "part": "AX4U360032G18I-BWHD3", "sizeMB": 32768, "speed": 3600,
                "voltageMV": 1200, "type": "DDR4"}
        return {
            "bios": {"vendor": "American Megatrends Inc.", "version": "5021", "date": "2024-09-29"},
            "board": {"maker": "ASUSTeK COMPUTER INC.", "product": "TUF GAMING X570-PLUS (WI-FI)", "version": "Rev X.0x"},
            "cpu": {"socket": "AM4", "cores": CORES, "threads": THREADS, "maxMHz": 4850},
            "caches": {1: 512 * 1024, 2: 4 * 2**20, 3: 32 * 2**20},
            "memory": [{"slot": "DIMM_A1", **empty}, {"slot": "DIMM_A2", **dimm},
                       {"slot": "DIMM_B1", **empty}, {"slot": "DIMM_B2", **dimm}],
            "disks": {hid.rsplit("/", 1)[-1]: {"model": model, "firmware": DISK_INFO[hid][0], "bus": DISK_INFO[hid][1],
                                               "rpm": DISK_INFO[hid][2], "health": "healthy", "bytes": None}
                      for hid, model, *_ in DRIVES},
            "gpus": {"NVIDIA GeForce RTX 5070 Ti": {"driver": "610.88", "driverDate": "2026-07-22"}},
        }

    def gpu_processes(self) -> list[dict]:
        """佔用顯存的程式：照 2026-10-31 這台的樣子（dwm 3 GB 最多），再加一個跟著負載變的 ComfyUI。"""
        load = self._v["gpu_load"]
        procs = [(1300, "桌面視窗管理員（Windows 畫面）", 3048), (25000, "python（ComfyUI\\main.py）", 400 + 7000 * load),
                 (31108, "SnippingTool", 441), (30548, "ChatGPT", 343), (27496, "Edge 瀏覽器", 285),
                 (12416, "檔案總管", 178), (20432, "RazerAppEngine", 114), (8812, "Discord", 64), (4120, "Steam", 22)]
        procs += [(11676, "HWGlance 這個視窗（WebView2）", 322), (32972, "pythonw（-m projecthub） 的視窗（WebView2）", 130)]
        now = self._clock()
        system_ones = ("桌面視窗管理員", "檔案總管", "HWGlance 這個視窗")     # 跟真的一樣：系統程式、自己不給關
        return sorted(({"pid": pid, "created": f"demo-{pid}", "name": name, "bytes": int(mb * 2**20),
                        "protected": "這是 Windows 的一部分，關掉畫面或系統會出問題" if name.startswith(system_ones) else None}
                       for pid, name, mb in procs if now - self._hidden.get(pid, -1e9) > 120),
                      key=lambda p: -p["bytes"])

    def hide_process(self, pid: int) -> None:
        """processes.DemoProcesses 示範「關閉」：從清單拿掉 2 分鐘（不碰任何真的程式）。"""
        self._hidden[pid] = self._clock()

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
        for kind in self.guard.due():                # 示範：重開一定成功
            self.guard.reopened(kind)
        if self._gpu_reset_at is not None and t >= self._gpu_reset_at:
            self._gpu_reset_at = None
            self.guard.trip("gpu", "模擬讀取時存取違規")
        v = self._v
        cpu_load = self._busy(t, 150.0, 0.0)
        gpu_load = self._busy(t, 150.0, 0.02)
        v["cpu_load"] = cpu_load
        v["gpu_load"] = gpu_load
        # Ryzen 一有負載就拉高時脈，溫度上得快（真機 30% 負載就 77°C），所以用開根號
        v["cpu"] = self._cpu.step(48.0 + 38.0 * math.sqrt(cpu_load), dt) + self._noise(0.4)
        v["gpu"] = self._gpu.step(48.0 + 26.0 * gpu_load, dt) + self._noise(0.2)
        v["board"] = self._board.step(44.0 + 4.0 * cpu_load, dt)
        v["chipset"] = self._chipset.step(76.0 + 4.0 * cpu_load, dt)
        if slow:
            for i, (_, _, idle, extra, *_rest) in enumerate(DRIVES):
                v[f"drive{i}"] = self._drives[i].step(idle + extra * cpu_load, dt * 10)

    def _sensor(self, hw_id: str, kind: str, index: int, name: str, value: float | None) -> RawSensor:
        sid = f"{hw_id}/{kind.lower()}/{index}"
        if value is None:
            return RawSensor(sid, name, kind, None)
        key = (sid, name)                   # LHM 有重複的 Identifier（GPU Bus 與 GPU Memory），最高最低分開記
        lo, hi = self._extremes.get(key, (value, value))
        lo, hi = min(lo, value), max(hi, value)
        self._extremes[key] = (lo, hi)
        return RawSensor(sid, name, kind, value, lo, hi)

    def read(self) -> list[RawHardware]:
        # 順序照真機：主機板、CPU、記憶體、顯示卡、硬碟
        gpu = self._gpu_hw()
        if self.guard.paused("gpu"):
            gpu = gpu.without_values()
        return [self._board_hw(), self._cpu_hw(), *self._memory_hw(), gpu, *self._drives_hw()]

    def notes(self) -> list[str]:
        return self.guard.notes()

    def _cpu_hw(self) -> RawHardware:
        v, hid, s = self._v, "/amdcpu/0", self._sensor
        load = v["cpu_load"]
        threads = [min(100.0, max(0.0, 100 * load + self._noise(18))) for _ in range(THREADS)]
        sensors = [s(hid, "Load", 2 + i, f"CPU Core #{i + 1}", x) for i, x in enumerate(threads)]
        clocks = [3600 + 1100 * min(1.0, load * 1.3) + self._noise(60) for _ in range(CORES)]
        busy = [min(1.0, 0.1 + (threads[2 * c] + threads[2 * c + 1]) / 200) for c in range(CORES)]
        sensors += [
            s(hid, "Load", 0, "CPU Total", sum(threads) / THREADS),
            s(hid, "Load", 1, "CPU Core Max", max(threads)),
            s(hid, "Power", 0, "Package", 35 + 95 * math.sqrt(load) + self._noise(2)),
            s(hid, "Clock", 1, "Cores (Average)", sum(clocks) / CORES),
            s(hid, "Clock", 2, "Cores (Average Effective)", sum(c * b for c, b in zip(clocks, busy)) / CORES),
        ]
        for c in range(CORES):
            sensors += [
                s(hid, "Clock", 3 + 2 * c, f"Core #{c + 1}", clocks[c]),
                s(hid, "Clock", 4 + 2 * c, f"Core #{c + 1} (Effective)", clocks[c] * busy[c]),
                s(hid, "Factor", c, f"Core #{c + 1}", clocks[c] / 100),
                s(hid, "Power", 1 + c, f"Core #{c + 1} (SMU)", max(0.5, 2 + 11 * load + self._noise(0.8))),
                s(hid, "Voltage", 2 + c, f"Core #{c + 1} VID", 1.1 + 0.33 * load),
            ]
        sensors += [
            s(hid, "Temperature", 2, "Core (Tctl/Tdie)", v["cpu"]),
            # CCD 是瞬間值：負載低時常常比 Tctl 低一大截、每秒亂跳（真機 48～78°C）
            s(hid, "Temperature", 3, "CCD1 (Tdie)", v["cpu"] - 2.0 - self._rng.uniform(0, 25 * (1 - load))),
            s(hid, "Voltage", 0, "Core (SVI2 TFN)", 1.0 + 0.35 * load),
            s(hid, "Voltage", 1, "SoC (SVI2 TFN)", 1.0875),
            s(hid, "Clock", 0, "Bus Speed", 100.0),
        ]
        return RawHardware(hid, "AMD Ryzen 7 5800X", "Cpu", sensors)

    def _gpu_hw(self) -> RawHardware:
        v, hid, s = self._v, "/gpu-nvidia/0", self._sensor
        load, temp = v["gpu_load"], v["gpu"]
        rpm = 600 + 1700 * load + self._noise(15)       # 真機閒置 50°C 時風扇也在轉（30%）
        used = 5000 + 7000 * load
        return RawHardware(hid, "NVIDIA GeForce RTX 5070 Ti", "GpuNvidia", [
            s(hid, "Temperature", 0, "GPU Core", temp),
            s(hid, "Clock", 0, "GPU Core", 210 + 2600 * min(1.0, load * 1.25) + self._noise(15)),
            s(hid, "Clock", 4, "GPU Memory", 14001.0),
            s(hid, "Fan", 1, "GPU Fan 1", rpm * 0.95),
            s(hid, "Fan", 2, "GPU Fan 2", rpm),
            s(hid, "Control", 1, "GPU Fan 1", 30 + 50 * load),
            s(hid, "Control", 2, "GPU Fan 2", 30 + 50 * load),
            s(hid, "Load", 0, "GPU Core", min(100.0, 100 * load + self._noise(1))),
            s(hid, "Load", 1, "GPU Memory Controller", 2 + 45 * load),
            s(hid, "Load", 2, "GPU Video Engine", 0.0),
            s(hid, "Load", 3, "GPU Bus", 5 + 40 * load),
            s(hid, "Load", 4, "GPU Power", 7 + 85 * load),
            s(hid, "Load", 5, "GPU Board Power", 6 + 80 * load),
            s(hid, "Voltage", 0, "GPU Core Voltage", 0.8 + 0.25 * load),
            s(hid, "SmallData", 3, "D3D Dedicated Memory Used", used - 290),
            s(hid, "SmallData", 4, "D3D Shared Memory Used", 290.0),
            s(hid, "Load", 7, "D3D 3D", min(100.0, 100 * load)),
            s(hid, "Load", 17, "D3D Video Decode", 0.0),
            s(hid, "Load", 8, "D3D Copy", 3 * load),
            s(hid, "Load", 9, "D3D Copy", 0.0),
            s(hid, "Load", 18, "D3D Video Encode", 0.0),
            s(hid, "Temperature", 3, "GPU Memory Junction", temp + 12 + 4 * load),   # 沒有 GPU Hot Spot
            s(hid, "SmallData", 2, "GPU Memory Total", GPU_VRAM_MB),
            s(hid, "SmallData", 0, "GPU Memory Free", GPU_VRAM_MB - used),
            s(hid, "SmallData", 1, "GPU Memory Used", used),
            s(hid, "Load", 3, "GPU Memory", 100 * used / GPU_VRAM_MB),    # 真機就跟 GPU Bus 同一個 Identifier
            s(hid, "Power", 0, "GPU Package", 20 + 270 * load + self._noise(3)),
            s(hid, "Throughput", 0, "GPU PCIe Rx", 1.5e6 + 4e7 * load),
            s(hid, "Throughput", 1, "GPU PCIe Tx", 9e5 + 2e7 * load),
        ])

    def _board_hw(self) -> RawHardware:
        v, lid, s = self._v, "/lpc/nct6798d/0", self._sensor
        load = v["cpu_load"]
        cpu_heat = min(1.0, max(0.0, (v["cpu"] - 45) / 40))
        fans, controls = [], []
        for name, index, idle, extra, full in BOARD_FANS:
            rpm = idle + extra * (cpu_heat if name == "CPU Fan" else load) + (self._noise(8) if full else 0)
            fans.append(s(lid, "Fan", index, name, rpm))
            # 沒接的接頭照樣有輸出（真機 95～100%）
            controls.append(s(lid, "Control", index, name, min(100.0, 100 * rpm / full) if full else 100.0))
        sensors = [
            *controls,
            *(s(lid, "Voltage", i, name, 1.0 + 0.35 * load if value is None else value) for i, name, value in BOARD_VOLTAGES),
            s(lid, "Temperature", 22, "CPU", v["cpu"] - 11.5),
            s(lid, "Temperature", 2, "Motherboard", v["board"]),
            s(lid, "Temperature", 10, "Chipset", v["chipset"]),
            *fans,
        ]
        superio = RawHardware(lid, "Nuvoton NCT6798D", "SuperIO", sensors)
        return RawHardware("/motherboard", "ASUS TUF GAMING X570-PLUS (WI-FI)", "Motherboard", [], [superio])

    def _memory_hw(self) -> list[RawHardware]:
        s = self._sensor
        used = 29.0 + 6.0 * self._v["cpu_load"]
        dimms = [RawHardware(f"/memory/dimm/{i}", f"A-DATA Technology - AX4U360032G18I-BWHD (#{i})", "Memory",
                             [s(f"/memory/dimm/{i}", "Data", 50, "Capacity", 32.0)])     # DDR4 沒有溫度感測器
                 for i in (2, 3)]
        return [
            RawHardware("/vram", "Virtual Memory", "Memory", [
                s("/vram", "Data", 2, "Memory Used", 45.0),
                s("/vram", "Data", 3, "Memory Available", 55.0),
                s("/vram", "Load", 1, "Memory", 45.0),
            ]),
            RawHardware("/ram", "Total Memory", "Memory", [
                s("/ram", "Data", 0, "Memory Used", used),
                s("/ram", "Data", 1, "Memory Available", RAM_GB - used),
                s("/ram", "Load", 0, "Memory", 100 * used / RAM_GB),
            ]),
            *dimms,
        ]

    def _drives_hw(self) -> list[RawHardware]:
        out = []
        s = self._sensor
        for i, (hid, model, _idle, _extra, used, total) in enumerate(DRIVES):
            temp = self._v[f"drive{i}"]
            nvme = hid.startswith("/nvme")
            busy = max(0.0, 3 + 20 * self._v["cpu_load"] * (1 if i < 2 else 0.2) + self._noise(2))
            life, read, written, count, hours = DRIVE_SMART[hid]
            sensors = [s(hid, "Temperature", 0, "Composite Temperature" if nvme else "Temperature", temp)]
            if nvme:
                warning, critical = NVME_LIMITS[hid]
                sensors += [s(hid, "Temperature", 10, "Warning Temperature", warning),
                            s(hid, "Temperature", 11, "Critical Temperature", critical)]
            if life is not None:
                sensors.append(s(hid, "Level", 20, "Life", life))
            if read is not None:
                sensors.append(s(hid, "Data", 21, "Data Read", read))
            if written is not None:
                sensors.append(s(hid, "Data", 22, "Data Written", written))
            sensors += [
                s(hid, "Factor", 23, "Power On Count", count),
                s(hid, "Factor", 24, "Power On Hours", hours),
                s(hid, "Load", 30, "Used Space", used),
                s(hid, "Data", 31, "Free Space", total * (1 - used / 100)),
                s(hid, "Data", 32, "Total Space", total),
                s(hid, "Load", 51, "Read Activity", busy * 0.3),
                s(hid, "Load", 52, "Write Activity", busy * 0.7),
                s(hid, "Load", 53, "Total Activity", busy),
                s(hid, "Throughput", 54, "Read Rate", busy * 2e6),
                s(hid, "Throughput", 55, "Write Rate", busy * 5e6),
            ]
            if nvme:
                spare, floor, worn = NVME_HEALTH[hid]
                sensors += [s(hid, "Level", 100, "Available Spare", spare),
                            s(hid, "Level", 101, "Available Spare Threshold", floor),
                            s(hid, "Level", 102, "Percentage Used", worn)]
            out.append(RawHardware(hid, model, "Storage", sensors))
        return out


class IntelLaptopDemo(DemoSource):
    """另一種電腦的示範（--demo-profile intel-laptop）：看這台以外的硬體在畫面上長怎樣，不碰硬體。
    12 代 Intel 筆電：6 個效能核（2 執行緒）＋8 個節能核、Intel 內顯＋筆電版 RTX 4060（沒有風扇讀數）、一顆 NVMe、
    電池（不顯示）；主機板沒有感測器（筆電常見）。感測器名稱照 LHM v0.9.6 原始碼（IntelCpu.cs、GenericCpu.cs、
    IntelIntegratedGpu.cs、NvidiaGpu.cs），Intel 每個核心有「離 TjMax 還差幾度」（TjMax 100）。型號是常見的，不是誰的電腦。"""

    P_CORES, E_CORES, TJMAX = 6, 8, 100.0

    def read(self) -> list[RawHardware]:
        s = self._sensor
        return [RawHardware("/motherboard", "LENOVO LNVNB161216", "Motherboard"), self._intel_cpu(),
                self._laptop_memory(), self._intel_gpu(), self._laptop_gpu(), self._laptop_drive(),
                RawHardware("/battery/0", "Primary Battery", "Battery", [s("/battery/0", "Level", 0, "Charge Level", 76.0)])]

    def specs(self) -> dict:
        return {"cpu": {"socket": "BGA1744", "cores": self.P_CORES + self.E_CORES, "threads": 2 * self.P_CORES + self.E_CORES,
                        "maxMHz": 4700}, "caches": {1: 1248 * 1024, 2: 11776 * 1024, 3: 24 * 2**20}}

    def gpu_processes(self) -> list[dict]:
        return []

    def _intel_cpu(self) -> RawHardware:
        v, hid, s = self._v, "/intelcpu/0", self._sensor
        load = v["cpu_load"]
        package = 42 + (v["cpu"] - 48) * 50 / 38              # 筆電滿載常到 90°C 以上（TjMax 100）
        names = [f"P-Core #{i + 1}" for i in range(self.P_CORES)] + [f"E-Core #{i + 1}" for i in range(self.E_CORES)]
        sensors = []
        for i, name in enumerate(names):
            temp = package - (1.5 if name[0] == "P" else 6.0) - self._rng.uniform(0, 3)
            clock = (3000 + 1700 * load if name[0] == "P" else 2400 + 1100 * load) + self._noise(40)
            sensors += [s(hid, "Temperature", i, name, temp),
                        s(hid, "Temperature", 30 + i, f"{name} Distance to TjMax", self.TJMAX - temp),
                        s(hid, "Clock", 1 + i, name, clock),
                        s(hid, "Voltage", 1 + i, name, 0.75 + 0.45 * load)]
        threads = [f"CPU Core #{c + 1} Thread #{t + 1}" for c in range(self.P_CORES) for t in range(2)]
        threads += [f"CPU Core #{self.P_CORES + c + 1}" for c in range(self.E_CORES)]
        sensors += [s(hid, "Load", 2 + i, name, min(100.0, max(0.0, 100 * load + self._noise(15))))
                    for i, name in enumerate(threads)]
        sensors += [
            s(hid, "Temperature", 14, "Core Max", package - 1),
            s(hid, "Temperature", 15, "Core Average", package - 5),
            s(hid, "Temperature", 16, "CPU Package", package),
            s(hid, "Load", 0, "CPU Total", 100 * load),
            s(hid, "Load", 1, "CPU Core Max", min(100.0, 100 * load + 8)),
            s(hid, "Clock", 0, "Bus Speed", 100.0),
            s(hid, "Power", 0, "CPU Package", 8 + 37 * math.sqrt(load) + self._noise(1)),
            s(hid, "Power", 1, "CPU Cores", 4 + 30 * math.sqrt(load)),
            s(hid, "Voltage", 0, "CPU Core", 0.75 + 0.45 * load),
        ]
        return RawHardware(hid, "12th Gen Intel(R) Core(TM) i7-12700H", "Cpu", sensors)

    def _intel_gpu(self) -> RawHardware:
        hid, s = "/gpu-intel-integrated/0", self._sensor
        load = self._v["cpu_load"] * 0.3
        return RawHardware(hid, "Intel(R) Iris(R) Xe Graphics", "GpuIntel", [
            s(hid, "Power", 0, "GPU Power", 0.5 + 6 * load),
            s(hid, "Load", 0, "D3D 3D", 100 * load),
            s(hid, "Load", 1, "D3D Copy", 2.0),
            s(hid, "Load", 2, "D3D Video Decode", 0.0),
            s(hid, "SmallData", 0, "D3D Shared Memory Used", 650 + 400 * load),
        ])

    def _laptop_gpu(self) -> RawHardware:
        v, hid, s = self._v, "/gpu-nvidia/0", self._sensor
        load, used = v["gpu_load"], 1200 + 5000 * v["gpu_load"]
        return RawHardware(hid, "NVIDIA GeForce RTX 4060 Laptop GPU", "GpuNvidia", [
            s(hid, "Temperature", 0, "GPU Core", 44 + (v["gpu"] - 48) * 1.2),
            s(hid, "Clock", 0, "GPU Core", 210 + 2100 * load),
            s(hid, "Clock", 4, "GPU Memory", 8001.0),
            s(hid, "Load", 0, "GPU Core", 100 * load),
            s(hid, "Power", 0, "GPU Package", 5 + 105 * load),
            s(hid, "SmallData", 1, "GPU Memory Used", used),
            s(hid, "SmallData", 2, "GPU Memory Total", 8188.0),
        ])

    def _laptop_memory(self) -> RawHardware:
        s, used = self._sensor, 9.5 + 3.0 * self._v["cpu_load"]
        return RawHardware("/ram", "Total Memory", "Memory", [
            s("/ram", "Data", 0, "Memory Used", used),
            s("/ram", "Data", 1, "Memory Available", 15.7 - used),
            s("/ram", "Load", 0, "Memory", 100 * used / 15.7),
        ])

    def _laptop_drive(self) -> RawHardware:
        hid, s = "/nvme/0", self._sensor
        return RawHardware(hid, "SAMSUNG MZVL21T0HCLR-00BL2", "Storage", [
            s(hid, "Temperature", 0, "Composite Temperature", 38 + 12 * self._v["cpu_load"]),
            s(hid, "Temperature", 10, "Warning Temperature", 82.0),
            s(hid, "Temperature", 11, "Critical Temperature", 85.0),
            s(hid, "Load", 30, "Used Space", 61.0),
            s(hid, "Data", 31, "Free Space", 372.0),
            s(hid, "Data", 32, "Total Space", 953.9),
        ])


class MacDemo(DemoSource):
    """Mac 的示範（--demo-profile mac）：照 2026-11-11 那台 MacBook Pro 14 吋 M6 的 SMC（效能核心區 21 個點、節能核心區 9 個、
    GPU 18 個、電池 3 個、1 個風扇且閒置時停轉）、12 核心（2 超級核＋4 效能核＋6 節能核）、32 GB、1 TB SSD。
    硬體樹跟真的 Mac 一樣走 mac.build_tree；沒有顯存排行、事件記錄、暫存檔、迷你模式（Mac 版還沒有）。"""

    features: tuple[str, ...] = ("tray", "events")      # 跟真的 Mac 一樣（mac.MacSource）
    roles = ("cpu", "gpu", "nvme", "battery")            # mac.MAC_ROLES
    gpu_processes = None                         # Apple 晶片沒有顯存，不列「顯存被誰佔用」
    P_POINTS, E_POINTS, G_POINTS, CORES = 21, 9, 18, 12

    def __init__(self, *args, **kwargs) -> None:
        from .mac import Extremes
        self._mac_sensor = Extremes()
        self._fan = _Lag(0.0, 8.0)
        self._battery = _Lag(27.0, 300.0)
        self._ssd = _Lag(30.0, 120.0)
        super().__init__(*args, **kwargs)

    def update(self, slow: bool = True) -> None:
        super().update(slow)
        v = self._v
        now = self._clock()
        dt = min(5.0, max(0.05, now - v.get("mac_t", now)))
        v["mac_t"] = now
        # Apple 晶片閒置約 35°C、滿載效能核心區 90°C 上下；風扇 70°C 以下停轉
        v["mac_cpu"] = 35.0 + (v["cpu"] - 48.0) * 55.0 / 38.0
        v["mac_gpu"] = 34.0 + (v["gpu"] - 48.0) * 40.0 / 26.0
        v["mac_fan"] = self._fan.step(0.0 if v["mac_cpu"] < 70 else 2000.0 + 150.0 * (v["mac_cpu"] - 70), dt)
        v["mac_battery"] = self._battery.step(27.0 + 6.0 * v["cpu_load"], dt)
        if slow:
            v["mac_ssd"] = self._ssd.step(30.0 + 8.0 * v["cpu_load"], dt * 10)

    def read(self) -> list[RawHardware]:
        from .mac import Readings, build_tree
        v, rng = self._v, self._rng
        temps = {f"Tp{i:02X}": v["mac_cpu"] - rng.uniform(0, 4) for i in range(self.P_POINTS)}
        temps |= {f"Te{i:02X}": v["mac_cpu"] - 8 - rng.uniform(0, 3) for i in range(self.E_POINTS)}
        temps |= {f"Tg{i:02X}": v["mac_gpu"] - rng.uniform(0, 2) for i in range(self.G_POINTS)}
        temps |= {f"TB{i}T": v["mac_battery"] - 0.2 * i for i in range(3)}
        fan = v["mac_fan"] if v["mac_fan"] >= 1500 else 0.0
        loads = [min(100.0, max(0.0, 100 * v["cpu_load"] * (1.0 if i < 6 else 0.5) + self._noise(5)))
                 for i in range(self.CORES)]
        r = Readings(chip="Apple M6", model="MacBook Pro", temps=temps, fans=[(fan, 2000.0, 8250.0)],
                     cpu_loads=loads, gpu_load=100 * v["gpu_load"],
                     memory=((18 + 6 * v["cpu_load"]) * 2**30, 32 * 2**30),
                     ssd="APPLE SSD AP1024Z", ssd_temp=v.get("mac_ssd"), disk=(863.9e9, 994.6e9))
        return build_tree(r, self._mac_sensor)

    def specs(self) -> dict:
        return {"cpu": {"cores": self.CORES, "threads": self.CORES, "layout": "2 超級核＋4 效能核＋6 節能核"},
                "gpus": {"Apple M6 GPU": {"cores": 12}},
                "disks": {"0": {"model": "APPLE SSD AP1024Z", "firmware": "3975.40.", "bus": "Apple Fabric（內建）"}}}


PROFILES = {"this-pc": DemoSource, "intel-laptop": IntelLaptopDemo, "mac": MacDemo}
