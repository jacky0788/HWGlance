"""產生 assets/app.ico（視窗、工具列、桌面捷徑的圖示）：深色圓角方塊上的弧形溫度儀表，跟介面左上角的 logo 一樣。

    .venv\\Scripts\\python.exe scripts\\make_icon.py

改顏色或圖案後重跑一次，再執行 scripts\\make-shortcut.ps1 更新捷徑。已經開著的視窗要重開才會換圖示。
系統匣的圖示是另外一個（tray.py 每次依溫度畫）。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "app.ico"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
BACKGROUND = (23, 26, 33)          # tokens.css 深色的 --surface
TRACK = (52, 58, 70)
VALUE = (61, 220, 151)             # tokens.css 深色的 --ok
START, SWEEP, FILL = 135.0, 270.0, 0.66     # 從左下角順時針 270°，填 66%
SUPERSAMPLE = 4


def _cap(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float, angle: float, width: float, color) -> None:
    """Pillow 的 arc 兩端是平的；在端點補一個圓，變成圓頭。"""
    x, y = cx + r * math.cos(math.radians(angle)), cy + r * math.sin(math.radians(angle))
    d.ellipse((x - width / 2, y - width / 2, x + width / 2, y + width / 2), fill=color)


def draw(size: int = 256) -> Image.Image:
    big = size * SUPERSAMPLE
    im = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    pad = big * 0.03
    d.rounded_rectangle((pad, pad, big - pad, big - pad), radius=big * 0.22, fill=BACKGROUND)
    width = big * 0.15
    r = big * 0.31                                   # 弧線中心到圖心的距離
    cx = cy = big / 2
    box = (cx - r - width / 2, cy - r - width / 2, cx + r + width / 2, cy + r + width / 2)
    end = START + SWEEP * FILL
    d.arc(box, START, START + SWEEP, fill=TRACK, width=round(width))
    for a in (START, START + SWEEP):
        _cap(d, cx, cy, r, a, width, TRACK)
    d.arc(box, START, end, fill=VALUE, width=round(width))
    for a in (START, end):
        _cap(d, cx, cy, r, a, width, VALUE)
    return im.resize((size, size), Image.LANCZOS)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    draw().save(OUT, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"已產生 {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
