"""溫度等級：正常（ok）／偏高（warm）／過熱（hot）。門檻依硬體種類不同，單位 °C，到達就算。

數字的依據（2026-10-29 定，之後設定頁可以改）：
- CPU：Ryzen 5000 的 Tjmax 是 90°C，到了會降頻；80 以上算偏高。
- GPU 核心：RTX 50 系列大約 88°C 開始降頻。熱點、顯存本來就比核心高 10～20°C，門檻另外算。
- 硬碟：NVMe／SATA SSD 70°C 左右開始降速；機械硬碟超過 45°C 就不理想。
- 2026-10-31 依真機調高（使用者同意）：晶片組 70/80 → 80/90（X570 晶片組平常就 70～80°C，真機閒置 78°C）；
  NVMe 55/70 → 60/70（真機 KIOXIA 閒置 57°C，它自己回報的警告溫度是 71°C）。
- 2026-11-11 Mac：電池 40／45（Apple 建議的使用環境是 35°C 以下，電池長時間 45°C 以上會加速老化）；
  Apple 晶片的 CPU 照暫定的降頻溫度 105°C 算（normalize.APPLE_TJMAX → 90／100）。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Band:
    warm: float
    hot: float


DEFAULTS: dict[str, Band] = {
    "cpu": Band(80, 90),
    "gpu": Band(80, 88),
    "gpu_hotspot": Band(95, 105),
    "gpu_memory": Band(95, 105),
    "board": Band(50, 65),
    "chipset": Band(80, 90),
    "vrm": Band(85, 100),
    "nvme": Band(60, 70),
    "ssd": Band(55, 70),
    "hdd": Band(45, 55),
    "dimm": Band(60, 75),
    "liquid": Band(40, 45),
    "psu": Band(60, 75),
    "battery": Band(40, 45),
    "other": Band(70, 85),
}


# 設定頁上的名稱與順序
LABELS: dict[str, str] = {
    "cpu": "CPU",
    "gpu": "GPU 核心",
    "gpu_hotspot": "GPU 熱點",
    "gpu_memory": "GPU 顯存",
    "board": "主機板",
    "chipset": "晶片組",
    "vrm": "供電（VRM）",
    "nvme": "NVMe SSD",
    "ssd": "SATA SSD",
    "hdd": "機械硬碟",
    "dimm": "記憶體（DIMM）",
    "liquid": "水溫",
    "psu": "電源",
    "battery": "電池",
    "other": "其他",
}


def cpu_band(tjmax: float | None) -> Band:
    """CPU 的預設門檻。知道 TjMax（Intel，normalize.cpu_tjmax）就照它：差 15 度偏高、差 5 度過熱
    （TjMax 100 → 85／95；Intel 桌機滿載本來就 90 以上，用 80／90 會一直亮紅燈）。
    不知道（AMD，LHM 不給）就用上面的 80／90（照這台 Ryzen 5000 的 Tjmax 90°C 定的）。"""
    return Band(tjmax - 15, tjmax - 5) if tjmax else DEFAULTS["cpu"]


def level(value: float | None, band: Band) -> str:
    if value is None:
        return "none"
    if value >= band.hot:
        return "hot"
    if value >= band.warm:
        return "warm"
    return "ok"
