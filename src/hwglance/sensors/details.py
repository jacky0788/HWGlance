"""點硬體跳出的「詳細資訊」：一個硬體的所有讀數＋規格，分成幾段（規格、溫度、負載…），每項中文名稱與單位，
專有名詞附一句說明。純函式、不碰硬體，tests/test_details.py 用示範資料測。

輸出就是 GET /api/device?id= 的內容（欄位改了要同步 frontend/src/types.ts 的 DeviceDetail）：
    {id, kind, name, sub, sections: [{title, note?, rows?, table?, bars?}]}
    row   = {label, value, unit?, note?, temp?, level?, min?, max?}   溫度一律 °C、temp: true（前端換 °F）
    table = {columns: [{label, unit?}], rows: [{label, cells: [...]}]}
    bars  = {label, unit, max, items: [{label, value}]}
挑剩的感測器放在最後的「其他讀數」，新硬體的讀數也不會漏掉。
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from .labels import label
from .model import RawHardware, RawSensor
from .normalize import CPU_HEADLINE, DISTANCE, STORAGE_HEADLINE, cpu_tjmax, shown_min, valid_temp

UNITS = {"Voltage": "V", "Current": "A", "Power": "W", "Clock": "MHz", "Load": "%", "Frequency": "Hz", "Fan": "RPM",
         "Flow": "L/h", "Control": "%", "Level": "%", "Data": "GB", "SmallData": "MB", "Throughput": "B/s",
         "TimeSpan": "s", "Timing": "ns", "Energy": "mWh", "Noise": "dBA", "Humidity": "%"}
DIGITS = {"Voltage": 3, "Current": 2, "Power": 1, "Clock": 0, "Load": 0, "Fan": 0, "Control": 0, "Level": 0, "Factor": 0,
          "SmallData": 0}
MEDIA_NAMES = {"nvme": "NVMe SSD", "ssd": "SATA SSD", "hdd": "機械硬碟"}
PROCESS_MIN_BYTES = 100 * 2**20               # 顯存被誰佔用：只列超過 100 MB 的程式（使用者 2026-10-31）
HEALTH_NAMES = {"healthy": "良好", "warning": "警告", "unhealthy": "不良"}

CPU_HEAD_NOTES = {  # CPU 主溫度：(括號裡的名稱, 說明)
    "Core (Tctl/Tdie)": ("Tctl/Tdie", "AMD 官方的 CPU 溫度，已經平滑過；總覽與系統匣顯示的就是這個"),
    "Core (Tctl)": ("Tctl", "AMD 的控制溫度，有些型號會加偏移量，比實際高"),
    "Core (Tdie)": ("Tdie", "CPU 晶片的實際溫度"),
    "CPU Package": ("封裝", "整顆 CPU 最熱的地方；總覽與系統匣顯示的就是這個"),
    "Core Max": ("核心最高", "所有核心裡最熱的一個"),
    "Core Average": ("核心平均", "所有核心的平均"),
}
CORE_COLUMNS = (  # 每個核心一列的表：(感測器種類, 名稱樣式, 欄名, 單位, 小數位)；Intel 混合架構是 P-Core／E-Core
    ("Clock", r"(?:CPU |[PE]-)?Core #(\d+)", "時脈", "MHz", 0),
    ("Clock", r"Core #(\d+) \(Effective\)", "實際時脈", "MHz", 0),
    ("Factor", r"Core #(\d+)", "倍頻", "", 1),
    ("Power", r"Core #(\d+) \(SMU\)", "功耗", "W", 1),
    ("Voltage", r"Core #(\d+) VID", "VID", "V", 3),
    ("Voltage", r"(?:CPU |[PE]-)?Core #(\d+)", "電壓", "V", 3),
)
CORE_KINDS = {"P": (0, "效能核"), "E": (1, "節能核"), None: (0, "核心")}   # 表裡的順序與名稱
GPU_ENGINES = (  # 顯示卡各部分的負載：(名稱, 感測器名稱樣式)；同一種有好幾個（D3D Copy）取最高
    ("3D", r"D3D 3D"), ("影片編碼", r"D3D Video Encode.*"), ("影片解碼", r"D3D Video Decode.*"),
    ("複製", r"D3D Copy.*"), ("顯存控制器", r"GPU Memory Controller"), ("影片引擎", r"GPU Video Engine"),
    ("PCIe 匯流排", r"GPU Bus"),
)
BOARD_TEMPS = {  # 主機板溫度：(中文, 說明)
    "Motherboard": ("主機板", "主機板上的溫度感測器"),
    "System": ("主機板", "主機板上的溫度感測器"),
    "Chipset": ("晶片組", "主機板晶片組的溫度"),
    "PCH": ("晶片組", "主機板晶片組的溫度"),
    "CPU": ("CPU（主機板量）", "主機板感測器量到的 CPU 溫度，跟 CPU 自己回報的不一樣是正常的"),
    "VRM": ("供電", "CPU 供電模組的溫度"),
    "VRM MOS": ("供電", "CPU 供電模組的溫度"),
}
VOLTAGES = {  # 主機板電壓：(中文, 說明)
    "Vcore": ("CPU 核心", "主機板供給 CPU 核心的電壓"),
    "AVSB": ("待機 3.3V", "關機時也有電的待機電源"),
    "+3V Standby": ("待機 3V", None),
    "CMOS Battery": ("主機板電池", "保存 BIOS 設定的鈕扣電池"),
    "CPU Termination": ("CPU 終端（VTT）", "CPU 與記憶體之間傳訊號用的電壓"),
}


def _round(v: float | None, digits: int) -> float | int | None:
    if v is None:
        return None
    return round(v, digits) if digits else round(v)


def _scaled(s: RawSensor) -> tuple[float | int | None, str]:
    """數值＋單位；太大的資料量、傳輸量換成好讀的單位。"""
    v = s.value
    if s.type == "Data" and v is not None and v >= 1000:
        return round(v / 1000, 1), "TB"
    if s.type == "SmallData" and v is not None and v >= 1024:
        return round(v / 1024, 1), "GB"
    if s.type == "Throughput" and v is not None:
        for size, unit in ((1e9, "GB/s"), (1e6, "MB/s"), (1e3, "KB/s")):
            if v >= size:
                return round(v / size, 1), unit
        return round(v), "B/s"
    return _round(v, DIGITS.get(s.type, 1)), UNITS.get(s.type, "")


def _row(title: str, value: float | int | str | None, unit: str = "", note: str | None = None) -> dict:
    row: dict = {"label": title, "value": value}
    if unit:
        row["unit"] = unit
    if note:
        row["note"] = note
    return row


def _reading(s: RawSensor | None, title: str | None = None, note: str | None = None) -> dict | None:
    """一個感測器變成一行；沒有這個感測器就不列。"""
    if s is None:
        return None
    value, unit = _scaled(s)
    return _row(title or label(s.type, s.name), value, unit, note)


def _temp(s: RawSensor | None, title: str | None, levels: dict[str, str], note: str | None = None) -> dict | None:
    if s is None or not valid_temp(s):
        return None
    row = {"label": title or label("Temperature", s.name), "value": _round(s.value, 1), "unit": "°C", "temp": True,
           "min": _round(shown_min(s), 1), "max": _round(s.max, 1)}
    if s.id in levels:
        row["level"] = levels[s.id]
    if note:
        row["note"] = note
    return row


def _limit(s: RawSensor | None, title: str, note: str) -> dict | None:
    """硬碟自己回報的警告／危險溫度：是門檻不是讀數，不給最低最高、不分等級。"""
    if s is None or not s.value or s.value <= 0:
        return None
    return {"label": title, "value": _round(s.value, 0), "unit": "°C", "temp": True, "note": note}


def _section(title: str, rows: Iterable[dict | None] = (), note: str | None = None, *,
             table: dict | None = None, bars: dict | None = None, keep: bool = False) -> dict | None:
    """沒有內容的段落不列；keep=True 時只有說明也列（例如「目前沒有程式超過 100 MB」）。"""
    rows = [r for r in rows if r is not None]
    if not rows and table is None and bars is None and not keep:
        return None
    sec: dict = {"title": title}
    if note:
        sec["note"] = note
    if rows:
        sec["rows"] = rows
    if table is not None:
        sec["table"] = table
    if bars is not None:
        sec["bars"] = bars
    return sec


class _Picker:
    """從硬體（含子硬體）挑感測器；挑過的記下來，剩下的放進「其他讀數」。"""

    def __init__(self, parts: Iterable[RawHardware]) -> None:
        self.sensors = [s for hw in parts for part in hw.walk() for s in part.sensors]
        self._used: set[int] = set()

    def one(self, sensor_type: str, *names: str) -> RawSensor | None:
        for name in names:
            for s in self.sensors:
                if s.type == sensor_type and s.name == name and id(s) not in self._used:
                    self._used.add(id(s))
                    return s
        return None

    def many(self, sensor_type: str, pattern: str = r".*") -> list[RawSensor]:
        rx = re.compile(pattern + r"\Z")
        found = [s for s in self.sensors if s.type == sensor_type and id(s) not in self._used and rx.match(s.name)]
        self._used.update(id(s) for s in found)
        return found

    def rest(self) -> list[RawSensor]:
        return [s for s in self.sensors if id(s) not in self._used]


def _others(p: _Picker, levels: dict[str, str]) -> dict | None:
    rows = [_temp(s, None, levels) if s.type == "Temperature" else _reading(s) for s in p.rest()]
    return _section("其他讀數", rows, "沒有歸類的感測器，認不得的名稱照硬體回報的原文")


def _fan_table(p: _Picker) -> dict | None:
    fans = p.many("Fan")
    controls = {s.name: s for s in p.many("Control")}
    rows = []
    for f in fans:
        c = controls.pop(f.name, None)
        unplugged = (f.value or 0) <= 0 and (f.max or 0) <= 0
        status = "沒接" if unplugged else "停轉" if (f.value or 0) <= 0 else ""
        rows.append({"label": label("Fan", f.name),
                     "cells": [_round(f.value, 0), None if unplugged or c is None else _round(c.value, 0), status]})
    for name, c in controls.items():                  # 只有輸出、沒有轉速的
        rows.append({"label": label("Fan", name), "cells": [None, _round(c.value, 0), ""]})
    if not rows:
        return None
    return {"columns": [{"label": "轉速", "unit": "RPM"}, {"label": "輸出", "unit": "%"}, {"label": "狀態"}], "rows": rows}


def _size(n: int) -> str:
    """快取大小：Intel 混合架構加起來常不是整數 MB（12700H 的 L1 是 1,248 KB），MB 取到小數一位。"""
    return f"{round(n / 2**20, 1):g} MB" if n >= 2**20 else f"{n // 1024} KB"


def _caches(caches: dict) -> str:
    return "・".join(f"L{int(lvl)} {_size(size)}" for lvl, size in sorted(caches.items(), key=lambda kv: int(kv[0])))


def _board_cpu(tree: list[RawHardware]) -> RawSensor | None:
    """主機板感測器量到的 CPU 溫度（放進 CPU 的詳細資訊對照）。"""
    for hw in tree:
        if hw.type == "Motherboard":
            for part in hw.walk():
                for s in part.sensors:
                    if s.type == "Temperature" and s.name == "CPU":
                        return s
    return None


def _per_core(p: _Picker) -> dict | None:
    columns: list[dict] = []
    cores: dict[tuple[int, int, str], dict[int, float | int | None]] = {}   # (順序, 編號, 名稱) → {欄: 值}
    for sensor_type, pattern, title, unit, digits in CORE_COLUMNS:
        found = p.many(sensor_type, pattern)
        if not found:
            continue
        col = len(columns)
        columns.append({"label": title, "unit": unit} if unit else {"label": title})
        for s in found:
            m = re.search(r"(?:([PE])-)?Core #(\d+)", s.name)
            order, word = CORE_KINDS[m.group(1)]        # 效能核 1 與節能核 1 是不同的核心，不能疊在一起
            cores.setdefault((order, int(m.group(2)), word), {})[col] = _round(s.value, digits)
    if not columns:
        return None
    return {"columns": columns,
            "rows": [{"label": f"{word} {n}", "cells": [cells.get(i) for i in range(len(columns))]}
                     for (_, n, word), cells in sorted(cores.items())]}


def _cpu(dev: dict, parts: list[RawHardware], tree: list[RawHardware], specs: dict,
         levels: dict[str, str]) -> list[dict | None]:
    p = _Picker(parts)
    cpu, caches = specs.get("cpu") or {}, specs.get("caches") or {}
    spec = [
        _row("型號", dev["name"]),
        _row("腳位", cpu["socket"]) if cpu.get("socket") else None,
        _row("核心／執行緒", f'{cpu["cores"]} 核心、{cpu["threads"]} 執行緒') if cpu.get("cores") and cpu.get("threads") else None,
        _row("最高時脈", round(cpu["maxMHz"] / 1000, 2), "GHz", "BIOS 回報的值，不一定等於官方規格") if cpu.get("maxMHz") else None,
        _row("快取", _caches(caches)) if caches else None,
    ]
    temps = []
    head = p.one("Temperature", *CPU_HEADLINE)
    if head is not None:
        short, note = CPU_HEAD_NOTES.get(head.name, (label("Temperature", head.name), None))
        temps.append(_temp(head, f"CPU 溫度（{short}）", levels, note))
    temps.append(_temp(_board_cpu(tree), "主機板量的 CPU 溫度", levels, "主機板感測器量到的，跟 CPU 自己回報的不一樣是正常的"))
    p.many("Temperature", r".*" + re.escape(DISTANCE))   # Intel 每個核心「離 TjMax 還差幾度」：不是溫度，換成下面那一行
    tjmax = cpu_tjmax(parts[0])
    if tjmax is not None:
        temps.append({"label": "降頻溫度（TjMax）", "value": tjmax, "unit": "°C", "temp": True,
                      "note": "到這個溫度 CPU 就會降頻保護自己（從 CPU 讀的）；HWGlance 的門檻照它算"})
    for i, s in enumerate(p.many("Temperature", r"CCD.*")):
        temps.append(_temp(s, f"{label('Temperature', s.name)}（瞬間）", levels,
                           None if i else "CPU 晶片當下的溫度，每秒會跳 20～30°C，是正常的；看 CPU 溫度就好"))
    temps += [_temp(s, None, levels) for s in p.many("Temperature")]
    threads = p.many("Load", r"CPU Core #\d+(?: Thread #\d+)?")
    loads = [_reading(p.one("Load", "CPU Total"), "總負載"),
             _reading(p.one("Load", "CPU Core Max"), "最忙的執行緒",
                      f"{len(threads)} 個執行緒裡最忙的那一個" if threads else None)]
    bars = {"label": "各執行緒", "unit": "%", "max": 100,
            "items": [{"label": "#" + "-".join(re.findall(r"#(\d+)", s.name)), "value": _round(s.value, 0)} for s in threads]}
    clocks = [_reading(p.one("Clock", "Cores (Average)"), "平均時脈"),
              _reading(p.one("Clock", "Cores (Average Effective)"), "平均實際時脈", "扣掉核心休息的時間，比較接近實際在跑的速度"),
              _reading(p.one("Clock", "Bus Speed"), "匯流排")]
    power = [_reading(p.one("Power", "Package", "CPU Package"), "整顆 CPU 功耗"),
             _reading(p.one("Power", "CPU Cores"), "核心功耗", "只算 CPU 核心，不含內顯與其他部分"),       # Intel
             _reading(p.one("Voltage", "Core (SVI2 TFN)", "CPU Core"), "核心電壓", "CPU 回報給主機板供電的核心電壓"),
             _reading(p.one("Voltage", "SoC (SVI2 TFN)"), "SoC 電壓", "記憶體控制器、I/O 那一塊的電壓")]
    return [_section("規格", spec), _section("溫度", temps),
            _section("負載", loads, bars=bars if threads else None),
            _section("時脈", clocks, table=_per_core(p)), _section("功耗與電壓", power), _others(p, levels)]


def _gpu(dev: dict, parts: list[RawHardware], tree: list[RawHardware], specs: dict,
         levels: dict[str, str]) -> list[dict | None]:
    hw = parts[0]
    p = _Picker(parts)
    info = (specs.get("gpus") or {}).get(hw.name) or {}
    total = p.one("SmallData", "GPU Memory Total")
    released = f'{info["driverDate"]} 發行' if info.get("driverDate") else None
    spec = [_row("型號", hw.name),
            _row("顯存容量", round(total.value / 1024), "GB") if total is not None and total.value else None,
            _row("驅動版本", info["driver"], note=released) if info.get("driver") else None]
    temps = [_temp(p.one("Temperature", "GPU Core"), "核心", levels, "顯示卡晶片的溫度"),
             _temp(p.one("Temperature", "GPU Hot Spot"), "熱點", levels, "晶片上最熱的一點，比核心高 10～15°C 是正常的"),
             _temp(p.one("Temperature", "GPU Memory Junction"), "顯存", levels, "顯存晶片最熱的地方，比核心高 10～20°C 是正常的")]
    temps += [_temp(s, None, levels) for s in p.many("Temperature")]
    engines = []
    for title, pattern in GPU_ENGINES:
        values = [s.value for s in p.many("Load", pattern) if s.value is not None]
        if values:
            engines.append({"label": title, "value": _round(max(values), 0)})
    p.many("Load", r"D3D .*")                           # 其他 D3D 引擎（VR、JPEG…）幾乎都是 0，不列
    loads = [_reading(p.one("Load", "GPU Core"), "核心負載")]
    bars = {"label": "各部分", "unit": "%", "max": 100, "items": engines}
    clocks = [_reading(p.one("Clock", "GPU Core"), "核心時脈"), _reading(p.one("Clock", "GPU Memory"), "顯存時脈")]
    vram = [_reading(p.one("SmallData", "GPU Memory Used"), "已用"), _reading(p.one("SmallData", "GPU Memory Free"), "可用"),
            _reading(p.one("Load", "GPU Memory"), "使用率"),
            _reading(p.one("SmallData", "D3D Dedicated Memory Used"), "程式用掉的顯存", "Windows 統計各程式用掉的顯存"),
            _reading(p.one("SmallData", "D3D Shared Memory Used"), "借用的系統記憶體", "顯存不夠時借用系統記憶體的量")]
    power = [_reading(p.one("Power", "GPU Package", "GPU Power"), "功耗"),
             _reading(p.one("Load", "GPU Power"), "功耗佔上限", "目前功耗是功耗上限的幾 %"),
             _reading(p.one("Load", "GPU Board Power"), "整卡功耗佔上限"),
             _reading(p.one("Voltage", "GPU Core Voltage", "GPU Core"), "核心電壓")]
    users = None
    if specs.get("gpuProcesses") is not None:              # 只有服務查得到時才有（device_details 的 procs）
        big = [x for x in specs["gpuProcesses"] if x["bytes"] >= PROCESS_MIN_BYTES]
        rows = [_row(x["name"], *((round(x["bytes"] / 2**30, 1), "GB") if x["bytes"] >= 2**30
                                  else (round(x["bytes"] / 2**20), "MB")), f'PID {x["pid"]}') for x in big]
        note = "Windows 統計各程式用掉的專用顯存（跟工作管理員一樣），只列超過 100 MB 的"
        users = _section("顯存被誰佔用", rows, note if rows else note + "；目前沒有程式超過", keep=True)
    fans = _fan_table(p)
    stopped = fans is not None and all(not r["cells"][0] for r in fans["rows"])
    pcie = [_reading(p.one("Throughput", "GPU PCIe Rx"), "接收"), _reading(p.one("Throughput", "GPU PCIe Tx"), "傳送")]
    return [_section("規格", spec), _section("溫度", temps),
            _section("負載", loads, "3D 是遊戲與繪圖，複製是搬資料，影片編碼／解碼是錄影與播放", bars=bars if engines else None),
            _section("時脈", clocks), _section("顯存", vram), users, _section("功耗與電壓", power),
            _section("風扇", (), "溫度低時風扇會停轉（零轉速模式），是正常的" if stopped else None, table=fans),
            _section("PCIe 傳輸", pcie, "顯示卡跟主機板之間的資料量"), _others(p, levels)]


def _board(dev: dict, parts: list[RawHardware], tree: list[RawHardware], specs: dict,
           levels: dict[str, str]) -> list[dict | None]:
    hw = parts[0]
    p = _Picker(parts)
    board, bios = specs.get("board") or {}, specs.get("bios") or {}
    chips = [part.name for part in hw.walk() if part is not hw]
    spec = [_row("型號", board.get("product") or hw.name),
            _row("廠商", board["maker"]) if board.get("maker") else None,
            _row("版本", board["version"]) if board.get("version") else None,
            _row("BIOS 版本", bios["version"]) if bios.get("version") else None,
            _row("BIOS 日期", bios["date"]) if bios.get("date") else None,
            _row("感測晶片", "、".join(chips), note="主機板上量溫度、電壓、風扇的晶片") if chips else None]
    temps = []
    for s in p.many("Temperature"):
        title, note = BOARD_TEMPS.get(s.name, (None, None))
        temps.append(_temp(s, title, levels, note))
    volts = []
    for s in p.many("Voltage"):
        title, note = VOLTAGES.get(s.name, (None, None))
        volts.append(_reading(s, title, note))
    return [_section("規格", spec), _section("溫度", temps), _section("風扇", table=_fan_table(p)),
            _section("電壓", volts, "+12V、+5V、+3.3V 在 ±5% 內都算正常"), _others(p, levels)]


def _memory(dev: dict, parts: list[RawHardware], tree: list[RawHardware], specs: dict,
            levels: dict[str, str]) -> list[dict | None]:
    ram = next((hw for hw in parts if hw.id == "/ram" or hw.name == "Total Memory"), None)
    virtual = next((hw for hw in parts if hw.id == "/vram" or hw.name == "Virtual Memory"), None)
    dimms = [hw for hw in parts if hw is not ram and hw is not virtual]
    pr, pv = _Picker([ram] if ram else []), _Picker([virtual] if virtual else [])
    used, free = pr.one("Data", "Memory Used"), pr.one("Data", "Memory Available")
    total = used.value + free.value if used and free and used.value is not None and free.value is not None else None
    slots = specs.get("memory") or []
    filled = [m for m in slots if m.get("sizeMB")]
    kinds = sorted({m["type"] for m in filled if m.get("type")})
    installed = round(sum(m["sizeMB"] for m in filled) / 1024) if filled else None
    spec = [_row("總容量", installed, "GB") if installed else None,
            _row("Windows 可用" if installed else "總容量", round(total, 1), "GB",
                 "比插的少一點是正常的，有一小塊保留給硬體" if installed else None) if total else None,
            _row("種類", "、".join(kinds)) if kinds else None,
            _row("插了幾條", f"{len(filled)} 條（共 {len(slots)} 個插槽）") if slots else None]
    table = None
    if slots:
        table = {"columns": [{"label": "廠牌"}, {"label": "型號"}, {"label": "容量", "unit": "GB"},
                             {"label": "速度", "unit": "MT/s"}, {"label": "電壓", "unit": "V"}],
                 "rows": [{"label": m.get("slot") or f"插槽 {i + 1}",
                           "cells": [m.get("maker"), m.get("part"), round(m["sizeMB"] / 1024), m.get("speed"),
                                     round(m["voltageMV"] / 1000, 2) if m.get("voltageMV") else None]
                           if m.get("sizeMB") else ["（空）", None, None, None, None]}
                          for i, m in enumerate(slots)]}
    temps = [_temp(s, None, levels) for hw in dimms for s in hw.sensors if s.type == "Temperature"]
    return [_section("規格", spec, None if temps else "這組記憶體沒有溫度感測器（DDR4 大多沒有）", table=table),
            _section("實體記憶體", [_reading(used, "已用"), _reading(free, "可用"), _reading(pr.one("Load", "Memory"), "使用率")]),
            _section("虛擬記憶體", [_reading(pv.one("Data", "Memory Used"), "已用"),
                                   _reading(pv.one("Data", "Memory Available"), "可用"),
                                   _reading(pv.one("Load", "Memory"), "使用率")],
                     "實體記憶體加上分頁檔（硬碟上的延伸空間）"),
            _section("溫度", temps)]


def _storage(dev: dict, parts: list[RawHardware], tree: list[RawHardware], specs: dict,
             levels: dict[str, str]) -> list[dict | None]:
    hw = parts[0]
    p = _Picker(parts)
    disks = specs.get("disks") or {}
    disk = disks.get(hw.id.rstrip("/").rsplit("/", 1)[-1]) or next(
        (d for d in disks.values() if d.get("model") == hw.name), {})
    total = p.one("Data", "Total Space")
    spec = [_row("型號", hw.name), _row("類型", MEDIA_NAMES.get(dev.get("media"), "硬碟")),
            _row("介面", disk["bus"]) if disk.get("bus") else None,
            _row("容量", *_scaled(total)) if total is not None and total.value else None,
            _row("韌體版本", disk["firmware"]) if disk.get("firmware") else None,
            _row("轉速", disk["rpm"], "RPM") if disk.get("rpm") else None]
    floor = p.one("Level", "Available Spare Threshold")
    hours = p.one("Factor", "Power On Hours")
    health = [_row("Windows 判斷", HEALTH_NAMES.get(disk["health"], disk["health"])) if disk.get("health") else None,
              _reading(p.one("Level", "Life"), "剩餘壽命", "SSD 的寫入壽命，100% 是全新"),
              _reading(p.one("Level", "Percentage Used"), "已用壽命", "廠商保證的寫入量用掉多少"),
              _reading(p.one("Level", "Available Spare"), "備用空間",
                       f"低於 {floor.value:g}% 要注意" if floor is not None and floor.value else None),
              _reading(p.one("Data", "Data Written"), "總寫入量"), _reading(p.one("Data", "Data Read"), "總讀取量"),
              _reading(p.one("Factor", "Power On Count"), "通電次數"),
              _row("通電時數", _round(hours.value, 0), "小時", f"約 {hours.value / 24:,.0f} 天")
              if hours is not None and hours.value is not None else None]
    temps = [_temp(p.one("Temperature", *STORAGE_HEADLINE), "目前溫度", levels)]
    temps += [_temp(s, None, levels) for s in p.many("Temperature", r"Temperature #\d+")]
    temps += [_limit(p.one("Temperature", "Warning Temperature"), "警告溫度", "超過這個溫度，硬碟會降速保護自己"),
              _limit(p.one("Temperature", "Critical Temperature"), "危險溫度", "超過這個溫度，硬碟可能自動關機")]
    space = [_reading(p.one("Load", "Used Space"), "已用"), _reading(p.one("Data", "Free Space"), "可用")]
    activity = [_reading(p.one("Throughput", "Read Rate"), "讀取速度"), _reading(p.one("Throughput", "Write Rate"), "寫入速度"),
                _reading(p.one("Load", "Total Activity"), "忙碌", "有多少時間在讀寫"),
                _reading(p.one("Load", "Read Activity"), "讀取忙碌"), _reading(p.one("Load", "Write Activity"), "寫入忙碌")]
    return [_section("規格", spec), _section("健康", health), _section("溫度", temps, "硬碟每 10 秒讀一次"),
            _section("空間", space), _section("讀寫", activity), _others(p, levels)]


def _generic(dev: dict, parts: list[RawHardware], tree: list[RawHardware], specs: dict,
             levels: dict[str, str]) -> list[dict | None]:
    p = _Picker(parts)
    return [_section("規格", [_row("名稱", dev["name"])]), _section("溫度", [_temp(s, None, levels) for s in p.many("Temperature")]),
            _section("風扇", table=_fan_table(p)), _others(p, levels)]


BUILDERS: dict[str, Callable[..., list[dict | None]]] = {
    "cpu": _cpu, "gpu": _gpu, "board": _board, "memory": _memory, "storage": _storage}


def device_details(tree: list[RawHardware], snapshot: dict, device_id: str, specs: dict | None = None,
                   procs: list[dict] | None = None) -> dict | None:
    """device_id 是快照 devices 的 id（記憶體是 /ram，代表所有記憶體硬體）。找不到回傳 None。
    procs：佔用顯存的程式 [{pid, name, bytes}]（顯示卡才用得到；None 表示查不到，不列那一段）。"""
    if procs is not None:
        specs = {**(specs or {}), "gpuProcesses": procs}
    dev = next((d for d in snapshot.get("devices", []) if d["id"] == device_id), None)
    if dev is None:
        return None
    if dev["kind"] == "memory":
        parts = [hw for hw in tree if hw.type == "Memory"]
    else:
        parts = [hw for hw in tree if hw.id == device_id]
    if not parts:
        return None
    levels = {t["id"]: t["level"] for d in snapshot["devices"] for t in d["temps"]}
    sections = BUILDERS.get(dev["kind"], _generic)(dev, parts, tree, specs or {}, levels)
    return {"id": dev["id"], "kind": dev["kind"], "name": dev["name"], "sub": dev["sub"],
            "sections": [s for s in sections if s]}
