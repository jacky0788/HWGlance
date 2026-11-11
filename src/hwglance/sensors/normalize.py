"""把硬體樹（RawHardware）整理成畫面要的樣子。純函式、不碰硬體，tests/test_normalize.py 用假的樹測。

輸出就是 /api/snapshot 的主體（欄位改了要同步 frontend/src/types.ts）：
- devices：一個硬體一張卡（CPU、GPU、主機板、水冷、記憶體、每顆硬碟、電源、其他），
  每張卡有 temps（全部溫度）、stats（負載、時脈、功耗…）、fans（有接的風扇）。
- overview：總覽那一排磚塊，每個重要的溫度一塊（「一眼看出所有硬體的溫度」就靠這一排）。

感測器名稱照 LHM v0.9.6 原始碼（Amd17Cpu.cs、IntelCpu.cs、GenericCpu.cs、NvidiaGpu.cs、AmdGpu.cs、IntelIntegratedGpu.cs、
SuperIOHardware.cs、StorageDevice.cs、TotalMemory.cs）。這台是 AMD＋NVIDIA；Intel、AMD 顯示卡、內顯的部分照原始碼寫、
用假的樹測（tests/test_normalize.py），示範資料另有一台 Intel 筆電（--demo-profile intel-laptop）。
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

from .labels import label
from .model import RawHardware, RawSensor
from .thresholds import DEFAULTS, Band, cpu_band, level

KIND_BY_TYPE = {
    "Cpu": "cpu",
    "GpuNvidia": "gpu", "GpuAmd": "gpu", "GpuIntel": "gpu", "GpuApple": "gpu",
    "Motherboard": "board", "SuperIO": "board", "EmbeddedController": "board",
    "Cooler": "cooler",
    "Memory": "memory",
    "Storage": "storage",
    "Psu": "psu",
    "Network": "network", "Battery": "battery",
}
KIND_ORDER = ("cpu", "gpu", "board", "cooler", "memory", "storage", "psu", "other")
SKIPPED_KINDS = frozenset({"network", "battery"})       # 跟溫度無關，第一版不顯示
MEDIA = {"nvme": "NVMe", "ssd": "SATA SSD", "hdd": "HDD"}

# Super I/O 空著的腳位常回 -55、127、255 之類的值：超出這個範圍視為沒接
VALID_TEMP = (-40.0, 125.0)
# 硬碟回報的這兩個是「門檻」，不是讀數
STORAGE_LIMITS = frozenset({"Warning Temperature", "Critical Temperature"})

CPU_HEADLINE = ("Core (Tctl/Tdie)", "Core (Tdie)", "Core (Tctl)", "CPU Package", "Core Max", "Core Average")
# Intel 每個核心一個：CPU Core #1，混合架構是 P-Core #1（效能核）、E-Core #1（節能核）
PER_CORE = re.compile(r"(?:CPU |[PE]-)?Core #\d+")
# Intel 每個核心「離 TjMax（降頻溫度）還差幾度」：LHM 把它當溫度給，但它不是溫度
DISTANCE = " Distance to TjMax"
# Mac（sensors/mac.py）：硬體的 id 都是 /apple 開頭；SMC 的每一個溫度點只放詳細資訊，卡片放最高與平均
APPLE = "/apple"
APPLE_POINT = re.compile(r"(?:P-Cluster|E-Cluster|GPU Cluster) #\d+")
# Apple 沒公開降頻溫度；Apple 晶片滿載常到 95～105°C 才降頻，暫定 105（門檻 90／100，2026-11-11 定，之後照真機調）
APPLE_TJMAX = 105.0
GPU_TILES = (("GPU Core", "GPU 核心"), ("GPU Hot Spot", "GPU 熱點"))
# 主機板上哪些溫度放進總覽：(LHM 名稱, 磚塊標題)；同一個標題只放一次。Battery 是 Mac 機身的電池溫度
BOARD_TILES = (("Motherboard", "主機板"), ("System", "主機板"), ("Chipset", "晶片組"), ("PCH", "晶片組"),
               ("VRM", "供電"), ("VRM MOS", "供電"), ("Battery", "電池"))
STORAGE_HEADLINE = ("Composite Temperature", "Temperature")


def _round(v: float | None, digits: int = 1) -> float | None:
    if v is None:
        return None
    return round(v, digits) if digits else round(v)


def _of(hw: RawHardware, sensor_type: str) -> list[RawSensor]:
    return [s for s in hw.sensors if s.type == sensor_type]


def _find(hw: RawHardware, sensor_type: str, *names: str) -> RawSensor | None:
    """照 names 的順序找第一個有值的感測器。"""
    for name in names:
        for s in hw.sensors:
            if s.type == sensor_type and s.name == name and s.value is not None:
                return s
    return None


def valid_temp(s: RawSensor) -> bool:
    if s.value is None:
        return s.max is not None and VALID_TEMP[0] < s.max < VALID_TEMP[1]
    return VALID_TEMP[0] < s.value < VALID_TEMP[1]


def shown_min(s: RawSensor) -> float | None:
    """LHM 讀到硬碟 SMART 之前是 0，這個 0 會一直留在最低值（2026-10-31 真機）；電腦零件不會到 0°C 以下。"""
    return s.min if s.min is not None and s.min > 0 else None


def short_gpu(name: str) -> str:
    return re.sub(r"\b(NVIDIA|GeForce|AMD|Radeon|Intel\(R\)|Intel)\s+", "", name).strip() or name


def short_drive(model: str) -> str:
    """KIOXIA-EXCERIA G2 SSD → KIOXIA-EXCERIA G2；CT1000MX500SSD1 → CT1000MX500。"""
    return re.sub(r"[\s_-]*SSD\d*$", "", model.strip()) or model


def clean_cpu(name: str) -> str:
    """AMD Ryzen 7 5800X 8-Core Processor → AMD Ryzen 7 5800X；12th Gen Intel(R) Core(TM) i7-12700H → Intel Core i7-12700H。"""
    name = re.sub(r"\(R\)|\(TM\)", "", name)
    name = re.sub(r"^\d+(?:st|nd|rd|th) Gen\s+", "", name.strip())
    name = re.sub(r"\s+\d+-Core Processor$|\s+Processor$|\s+CPU @.*$", "", name)
    return re.sub(r"\s{2,}", " ", name).strip()


def short_cpu(name: str) -> str:
    """總覽磚塊的小字：AMD Ryzen 7 5800X → Ryzen 7 5800X。"""
    name = clean_cpu(name)
    return re.sub(r"^(AMD|Intel)\s+", "", name) or name


def short_board(name: str) -> str:
    """ASUS TUF GAMING X570-PLUS (WI-FI) → TUF X570-PLUS（拿掉廠牌、括號裡的版本與 GAMING，磚塊放得下）。"""
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name.strip())
    name = re.sub(r"^(ASUSTeK COMPUTER INC\.|ASUS|MSI|Micro-Star International Co\., Ltd\.|"
                  r"Gigabyte Technology Co\., Ltd\.|GIGABYTE|ASRock|BIOSTAR)\s+", "", name, flags=re.I)
    short = re.sub(r"\s+", " ", re.sub(r"\bGAMING\b", "", name, flags=re.I)).strip()
    return short or name.strip() or name


def chipset_of(board: str) -> str | None:
    """從主機板型號抓晶片組：TUF GAMING X570-PLUS → X570；認不出來（例如 A520M）就 None。"""
    m = re.search(r"(?<![A-Za-z0-9])([ABHXZ]\d{3}E?)(?![A-Za-z0-9])", board)
    return m.group(1) if m else None


class _Device:
    def __init__(self, hw: RawHardware, kind: str, bands: dict[str, Band], name: str | None = None,
                 sub: str = "") -> None:
        self.bands = bands
        self.data: dict = {"id": hw.id, "kind": kind, "name": name or hw.name, "sub": sub,
                           "headline": None, "temps": [], "stats": [], "fans": [], "unplugged": 0}
        self.tiles: list[dict] = []
        self._tile_labels: set[str] = set()

    def temp(self, s: RawSensor, role: str) -> dict | None:
        if not valid_temp(s):
            return None
        band = self.bands.get(role, self.bands["other"])
        t = {"id": s.id, "name": s.name, "label": label(s.type, s.name), "role": role,
             "value": _round(s.value), "min": _round(shown_min(s)), "max": _round(s.max),
             "level": level(s.value, band)}
        self.data["temps"].append(t)
        return t

    def tile(self, t: dict | None, title: str, sub: str) -> None:
        if t is None or title in self._tile_labels:
            return
        self._tile_labels.add(title)
        self.tiles.append({"id": t["id"], "device": self.data["id"], "kind": self.data["kind"],
                           "label": title, "sub": sub, "value": t["value"], "max": t["max"],
                           "level": t["level"], "role": t["role"]})

    def headline(self, t: dict | None) -> None:
        if t is not None and self.data["headline"] is None:
            self.data["headline"] = t["id"]

    def stat(self, key: str, title: str, value: float | None, unit: str, digits: int = 0,
             total: float | None = None, note: str | None = None) -> None:
        if value is None:
            return
        item = {"key": key, "label": title, "value": _round(value, digits), "unit": unit}
        if total is not None:
            item["total"] = _round(total, digits)
        if note:
            item["note"] = note
        self.data["stats"].append(item)

    def fans_of(self, hw: RawHardware) -> None:
        """有轉過的風扇才列出來；從沒轉過的（沒接）只計數。Mac 的風扇一定有接（SMC 說有幾個），閒置時會停在 0。"""
        always = hw.id.startswith(APPLE)
        for s in _of(hw, "Fan"):
            if not always and (s.value or 0) <= 0 and (s.max or 0) <= 0:
                self.data["unplugged"] += 1
                continue
            control = _find(hw, "Control", s.name)
            self.data["fans"].append({"id": s.id, "label": label("Fan", s.name), "value": _round(s.value, 0),
                                      "max": _round(s.max, 0), "unit": "RPM",
                                      "percent": _round(control.value, 0) if control else None})

    def hottest(self) -> dict | None:
        temps = [t for t in self.data["temps"] if t["value"] is not None]
        return max(temps, key=lambda t: t["value"]) if temps else None

    def done(self) -> dict:
        if not self.tiles:                  # 沒有規則對到：至少把最熱的那個放進總覽
            t = self.hottest()
            if t is not None:
                self.tile(t, self.data["name"], self.data["sub"] or "溫度")
        self.headline(self.hottest())
        self.data["tiles"] = [tile["id"] for tile in self.tiles]
        return self.data


def cpu_tjmax(hw: RawHardware) -> float | None:
    """Intel 的 TjMax（降頻溫度）＝核心溫度＋「離 TjMax 還差幾度」（LHM 從 MSR 讀了 TjMax 但不直接給）。
    每個核心應該一樣，取最多核心算出來的那個；AMD 沒有這個感測器就 None。Apple 晶片用暫定的 APPLE_TJMAX。"""
    if hw.id.startswith(APPLE):
        return APPLE_TJMAX
    temps = {s.name: s.value for s in _of(hw, "Temperature")}
    found = [round(temps[core] + s.value) for s in _of(hw, "Temperature")
             if s.name.endswith(DISTANCE) and s.value is not None
             and temps.get(core := s.name[: -len(DISTANCE)]) is not None]
    return float(Counter(found).most_common(1)[0][0]) if found else None


def _cpu(hw: RawHardware, bands: dict[str, Band]) -> _Device:
    tjmax = cpu_tjmax(hw)
    if tjmax is not None and bands.get("cpu") == DEFAULTS["cpu"]:
        # 使用者沒改過 CPU 門檻（設定只存改過的）：Intel 照它自己的 TjMax（TjMax 100 → 85／95）
        bands = {**bands, "cpu": cpu_band(tjmax)}
    dev = _Device(hw, "cpu", bands, name=clean_cpu(hw.name))
    sensors = [s for s in _of(hw, "Temperature") if not s.name.endswith(DISTANCE)]
    if any(s.name in CPU_HEADLINE and valid_temp(s) for s in sensors):
        # AMD 的 CCD 溫度是瞬間值，真機每秒在 48～78°C 之間跳（2026-10-31）；有平滑過的 Tctl/Tdie 就不列。
        # Intel 每個核心一個溫度（24 核就 24 個），有封裝／核心最高就不列（詳細資訊裡還看得到）；Mac 的 SMC 溫度點也是
        sensors = [s for s in sensors if not s.name.startswith("CCD") and not PER_CORE.fullmatch(s.name)
                   and not APPLE_POINT.fullmatch(s.name)]
    temps = {s.name: t for s in sensors if (t := dev.temp(s, "cpu"))}
    head = next((temps[n] for n in CPU_HEADLINE if n in temps), None)
    if head is not None:
        dev.tile(head, "CPU", short_cpu(hw.name))
        dev.headline(head)
    load = _find(hw, "Load", "CPU Total")
    dev.stat("load", "負載", load and load.value, "%")
    clock = _find(hw, "Clock", "Cores (Average)")
    if clock is None:
        cores = [s.value for s in _of(hw, "Clock") if PER_CORE.fullmatch(s.name) and s.value]
        clock_value = max(cores) if cores else None
    else:
        clock_value = clock.value
    dev.stat("clock", "時脈", clock_value, "MHz")
    power = _find(hw, "Power", "Package", "CPU Package")
    dev.stat("power", "功耗", power and power.value, "W", digits=1 if hw.id.startswith(APPLE) else 0)
    return dev


def _gpu_role(name: str) -> str:
    if name == "GPU Hot Spot":
        return "gpu_hotspot"
    if name.startswith("GPU Memory"):
        return "gpu_memory"
    if name.startswith("GPU VR"):                     # AMD：核心、顯存、SoC 的供電（VRM）
        return "vrm"
    if name == "GPU Liquid":                          # AMD 水冷卡的水溫
        return "liquid"
    if name == "GPU PLX":                             # AMD 雙晶片卡的橋接晶片
        return "other"
    return "gpu"


def _gpu(hw: RawHardware, bands: dict[str, Band]) -> _Device:
    dev = _Device(hw, "gpu", bands, sub=short_gpu(hw.name))
    temps: dict[str, dict] = {}
    for s in _of(hw, "Temperature"):
        if APPLE_POINT.fullmatch(s.name):            # Mac 的 GPU 有 18 個溫度點：卡片放最高與平均，每個點看詳細資訊
            continue
        if t := dev.temp(s, _gpu_role(s.name)):
            temps.setdefault(s.name, t)
    for name, title in GPU_TILES:
        dev.tile(temps.get(name), title, dev.data["sub"])
    dev.headline(temps.get("GPU Core"))
    # Intel 內顯沒有「GPU Core」負載，用 D3D 的 3D 引擎（工作管理員看的也是這個）
    load = _find(hw, "Load", "GPU Core") or next(
        (s for s in _of(hw, "Load") if s.name.endswith("3D") and s.value is not None), None)
    dev.stat("load", "負載", load and load.value, "%")
    clock = _find(hw, "Clock", "GPU Core")
    dev.stat("clock", "時脈", clock and clock.value, "MHz")
    power = _find(hw, "Power", "GPU Package", "GPU Power") or next(iter(_of(hw, "Power")), None)
    dev.stat("power", "功耗", power and power.value, "W", digits=1 if hw.id.startswith(APPLE) else 0)
    fans = [s for s in _of(hw, "Fan") if s.value is not None]
    controls = [s for s in _of(hw, "Control") if s.value is not None]
    if fans:
        note = f"{round(controls[0].value)}%" if controls else None
        dev.stat("fan", "風扇", max(s.value for s in fans), "RPM", note=note)
    elif controls:
        dev.stat("fan", "風扇", controls[0].value, "%")
    used = _find(hw, "SmallData", "GPU Memory Used", "D3D Dedicated Memory Used")
    total = _find(hw, "SmallData", "GPU Memory Total")
    dev.stat("vram", "顯存", used and used.value, "MB", total=total and total.value)
    return dev


def _board_role(name: str) -> str:
    if name in ("Chipset", "PCH") or name.startswith("PCH"):
        return "chipset"
    if name.startswith("VRM"):
        return "vrm"
    if name in ("CPU", "CPU Socket") or name.startswith("PECI"):
        return "cpu"
    if name == "Battery":                             # Mac 機身的電池（sensors/mac.py）
        return "battery"
    return "board"


def _board(hw: RawHardware, bands: dict[str, Band]) -> _Device:
    parts = [p for p in hw.walk() if p is not hw] if hw.type == "Motherboard" else [hw]
    dev = _Device(hw, "board", bands)            # 感測晶片的代號（NCT6798D）只放在詳細資訊，卡片上不寫
    found: dict[str, dict] = {}
    for part in [hw, *parts] if hw.type == "Motherboard" else parts:
        for s in _of(part, "Temperature"):
            if t := dev.temp(s, _board_role(s.name)):
                found.setdefault(s.name, t)
        dev.fans_of(part)
    board = short_board(hw.name)
    for name, title in BOARD_TILES:
        if name in found:
            dev.tile(found[name], title, (chipset_of(board) or board) if title == "晶片組" else board)
    dev.headline(found.get("Motherboard"))
    return dev


def _cooler(hw: RawHardware, bands: dict[str, Band]) -> _Device:
    dev = _Device(hw, "cooler", bands)
    for s in _of(hw, "Temperature"):
        role = "liquid" if re.search(r"Liquid|Coolant|Water", s.name) else "other"
        if (t := dev.temp(s, role)) and role == "liquid":
            dev.tile(t, "水溫", hw.name)
            dev.headline(t)
    dev.fans_of(hw)
    return dev


def _storage(hw: RawHardware, bands: dict[str, Band]) -> _Device:
    media = hw.id.strip("/").split("/")[0]
    role = media if media in MEDIA else "ssd"
    dev = _Device(hw, "storage", bands, name=hw.name, sub=MEDIA.get(media, ""))
    dev.data["media"] = media if media in MEDIA else "unknown"
    temps: dict[str, dict] = {}
    for s in _of(hw, "Temperature"):
        if s.name in STORAGE_LIMITS:
            continue
        if t := dev.temp(s, role):
            temps.setdefault(s.name, t)
    head = next((temps[n] for n in STORAGE_HEADLINE if n in temps), None) or dev.hottest()
    dev.tile(head, short_drive(hw.name), dev.data["sub"] or "硬碟")
    dev.headline(head)
    used = _find(hw, "Load", "Used Space")
    dev.stat("used", "已用", used and used.value, "%")
    activity = _find(hw, "Load", "Total Activity")
    dev.stat("activity", "忙碌", activity and activity.value, "%")
    free = _find(hw, "Data", "Free Space")
    total = _find(hw, "Data", "Total Space")
    dev.stat("free", "可用", free and free.value, "GB", total=total and total.value)
    return dev


def _memory(parts: list[RawHardware], bands: dict[str, Band]) -> _Device:
    main = next((p for p in parts if p.id == "/ram" or p.name == "Total Memory"), parts[0])
    dev = _Device(main, "memory", bands, name="記憶體")
    used = _find(main, "Data", "Memory Used")
    free = _find(main, "Data", "Memory Available")
    if used is not None:
        total = used.value + free.value if free is not None else None
        dev.stat("used", "已用", used.value, "GB", digits=1, total=total)
    load = _find(main, "Load", "Memory")
    dev.stat("load", "使用率", load and load.value, "%")
    for part in parts:
        if part.id in ("/ram", "/vram") or part.name in ("Total Memory", "Virtual Memory"):
            continue
        for s in _of(part, "Temperature"):
            if t := dev.temp(s, "dimm"):
                dev.tile(t, label("Temperature", s.name), "記憶體")
    return dev


def _psu(hw: RawHardware, bands: dict[str, Band]) -> _Device:
    dev = _Device(hw, "psu", bands)
    for s in _of(hw, "Temperature"):
        if t := dev.temp(s, "psu"):
            dev.tile(t, "電源", hw.name)
    dev.fans_of(hw)
    return dev


def _other(hw: RawHardware, bands: dict[str, Band]) -> _Device:
    dev = _Device(hw, "other", bands)
    for part in hw.walk():
        for s in _of(part, "Temperature"):
            dev.temp(s, "other")
        dev.fans_of(part)
    return dev


BUILDERS = {"cpu": _cpu, "gpu": _gpu, "board": _board, "cooler": _cooler, "storage": _storage, "psu": _psu}


def normalize(tree: Iterable[RawHardware], bands: dict[str, Band] | None = None) -> dict:
    merged = {**DEFAULTS, **(bands or {})}
    devices: list[_Device] = []
    memory: list[RawHardware] = []
    for hw in tree:
        kind = KIND_BY_TYPE.get(hw.type, "other")
        if kind in SKIPPED_KINDS:
            continue
        if kind == "memory":
            memory.append(hw)
            continue
        devices.append(BUILDERS.get(kind, _other)(hw, merged))
    if memory:
        devices.append(_memory(memory, merged))
    devices.sort(key=lambda d: KIND_ORDER.index(d.data["kind"]))
    finished = [d.done() for d in devices]           # done() 可能補上總覽磚塊，要先跑完
    return {"devices": finished, "overview": [tile for d in devices for tile in d.tiles]}


def history_values(snapshot: dict) -> dict[str, float | None]:
    """要記歷史（折線圖）的數值：每個溫度，加上各硬體的負載與功耗（鍵是「硬體 id#項目」）。"""
    values: dict[str, float | None] = {}
    for d in snapshot["devices"]:
        for t in d["temps"]:
            values[t["id"]] = t["value"]
        for s in d["stats"]:
            if s["key"] in ("load", "power"):
                values[f'{d["id"]}#{s["key"]}'] = s["value"]
    return values
