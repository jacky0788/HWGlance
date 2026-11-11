"""產生 Mac 的 App 圖示：assets/app-mac.png（1024×1024）與 assets/AppIcon.icns（scripts/make-mac-app.sh 放進 HWGlance.app）。

圖案跟 Windows 的 assets/app.ico 一樣（make_icon.py：深色底上的弧形溫度儀表），只是照 macOS 的圖示格線：
1024 的畫布裡 824 的圓角方塊（圓角 185）、底下一點陰影、底色上淺下深。.icns 用系統的 iconutil 包（只有 Mac 有）。

    .venv/bin/python scripts/make_mac_icon.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_icon import FILL, START, SWEEP, TRACK, VALUE, _cap  # noqa: E402 - 同一個資料夾的 Windows 圖示

ROOT = Path(__file__).resolve().parents[1]
PNG = ROOT / "assets" / "app-mac.png"
ICNS = ROOT / "assets" / "AppIcon.icns"
CANVAS, BODY, RADIUS = 1024, 824, 185          # Apple 的 App 圖示格線
TOP, BOTTOM = (36, 40, 50), (17, 19, 25)       # 底色：上淺下深（tokens.css 深色的 --surface 附近）
SUPERSAMPLE = 2
ICONSET = (16, 32, 128, 256, 512)              # 每個再加 @2x


def draw() -> Image.Image:
    s = SUPERSAMPLE
    size, body, radius = CANVAS * s, BODY * s, RADIUS * s
    x0 = y0 = (size - body) / 2
    box = (x0, y0, x0 + body, y0 + body)
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    shadow = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((box[0], box[1] + 12 * s, box[2], box[3] + 12 * s), radius=radius,
                                             fill=(0, 0, 0, 90))
    im.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(14 * s)))

    gradient = Image.new("RGBA", (1, 256))
    for y in range(256):
        t = y / 255
        gradient.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)) + (255,))
    gradient = gradient.resize((round(body), round(body)))
    mask = Image.new("L", gradient.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, body - 1, body - 1), radius=radius, fill=255)
    im.paste(gradient, (round(x0), round(y0)), mask)

    d = ImageDraw.Draw(im)
    width, r = body * 0.15, body * 0.31
    cx = cy = size / 2
    arc_box = (cx - r - width / 2, cy - r - width / 2, cx + r + width / 2, cy + r + width / 2)
    end = START + SWEEP * FILL
    d.arc(arc_box, START, START + SWEEP, fill=TRACK, width=round(width))
    for a in (START, START + SWEEP):
        _cap(d, cx, cy, r, a, width, TRACK)
    d.arc(arc_box, START, end, fill=VALUE, width=round(width))
    for a in (START, end):
        _cap(d, cx, cy, r, a, width, VALUE)
    return im.resize((CANVAS, CANVAS), Image.LANCZOS)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    PNG.parent.mkdir(parents=True, exist_ok=True)
    icon = draw()
    icon.save(PNG)
    print(f"已產生 {PNG}")
    iconutil = shutil.which("iconutil")
    if iconutil is None:
        print("沒有 iconutil（不是 Mac）：只產生 PNG")
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "AppIcon.iconset"
        folder.mkdir()
        for px in ICONSET:
            icon.resize((px, px), Image.LANCZOS).save(folder / f"icon_{px}x{px}.png")
            icon.resize((px * 2, px * 2), Image.LANCZOS).save(folder / f"icon_{px}x{px}@2x.png")
        subprocess.run([iconutil, "-c", "icns", str(folder), "-o", str(ICNS)], check=True)
    print(f"已產生 {ICNS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
