"""把迷你模式的幾行字（items.rows）畫成一張有透明度的圖（Pillow，RGBA、不是預乘），window.py 再貼到分層視窗上。

背景是深色圓角框，透明度照設定（0％＝只剩字）；字一律不透明、外面描一圈深色邊，疊在亮的遊戲畫面上也看得清楚。
顏色照 tokens.css 的深色主題（疊加層一律深色）；種類色可以在設定裡換，自訂色跟網頁一樣調亮到看得清楚
（oklch 亮度至少 0.72，tests/test_mini.py 會比對 tokens.css）。
"""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

from .items import Row

BG = (15, 17, 21)                   # --bg
BORDER = (53, 59, 71)               # --border-strong
FG = "#e9ecf2"                      # --fg
MUTED = "#aab1be"                   # --fg-muted
LEVELS = {"ok": "#3ddc97", "warm": "#f5b544", "hot": "#ff6b6b", "none": MUTED}
MEASURES = {"load": "#5ccfe6", "clock": "#a5b4fc", "power": "#ffa266", "fan": "#5eead4", "data": "#f9a8d4"}
SWATCHES = {"blue": "#6b96ff", "indigo": "#9d8fff", "purple": "#c38bff", "pink": "#f57cba", "teal": "#34d1bd",
            "sky": "#3fc8eb", "lime": "#a6d96a", "silver": "#c4cad4"}
KIND_DEFAULTS = {"cpu": "blue", "gpu": "purple", "board": "teal", "memory": "pink", "storage": "sky"}
OUTLINE = (0, 0, 0, 190)
FONT_DIR = Path(r"C:\Windows\Fonts")
LATIN = ("seguisb.ttf", "segoeuib.ttf", "arialbd.ttf")
CJK = (("msjhbd.ttc", 1), ("msjh.ttc", 1), ("msyhbd.ttc", 1), ("YuGothB.ttc", 0))   # 1＝「UI」那一套，行距比較緊
BASE_PX = 15                        # 100％、96 DPI 時的字級（像素）


def _hex(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


# ---- 自訂色調亮：sRGB ↔ OKLab（https://bottosson.github.io/posts/oklab/），跟網頁的 oklch(from …) 一樣的意思 ----

def _to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _to_srgb(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def _oklab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    r, g, b = (_to_linear(v / 255) for v in rgb)
    l_ = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m_ = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s_ = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
            1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
            0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_)


def _from_oklab(lab: tuple[float, float, float]) -> tuple[int, int, int]:
    L, a, b = lab
    l_ = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    rgb = (4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
           -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
           -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_)
    return tuple(round(min(1.0, max(0.0, _to_srgb(max(0.0, c)))) * 255) for c in rgb)   # type: ignore[return-value]


def lighten(value: str, floor: float = 0.72) -> tuple[int, int, int]:
    """自訂色在深色背景上至少要這麼亮（oklch 的亮度），色相、彩度不變；超出 sRGB 的部分剪掉。"""
    L, a, b = _oklab(_hex(value))
    if L >= floor:
        return _hex(value)
    chroma, hue = math.hypot(a, b), math.atan2(b, a)
    return _from_oklab((floor, chroma * math.cos(hue), chroma * math.sin(hue)))


def kind_color(kind: str, colors: dict[str, str]) -> tuple[int, int, int]:
    """設定的卡片顏色（只有改過的）→ 疊加層用的顏色。"""
    value = colors.get(kind) or KIND_DEFAULTS.get(kind, "silver")
    return lighten(value) if value.startswith("#") else _hex(SWATCHES.get(value, SWATCHES["silver"]))


def tone_color(tone: str, colors: dict[str, str]) -> tuple[int, int, int]:
    if tone.startswith("kind:"):
        return kind_color(tone[5:], colors)
    if tone.startswith("level:"):
        return _hex(LEVELS.get(tone[6:], MUTED))
    if tone in MEASURES:
        return _hex(MEASURES[tone])
    if tone in ("unit", "muted"):
        return _hex(MUTED)
    return _hex(FG)                     # fps、plain


@lru_cache(maxsize=32)
def _font(px: int, cjk: bool):
    from PIL import ImageFont
    for name, index in CJK if cjk else tuple((name, 0) for name in LATIN):
        path = FONT_DIR / name
        if path.is_file():
            return ImageFont.truetype(str(path), px, index=index)
    return _font(px, False) if cjk else ImageFont.load_default(px)


def has_cjk_font() -> bool:
    return any((FONT_DIR / name).is_file() for name, _ in CJK)


def _is_cjk(text: str) -> bool:
    return any(ord(ch) > 0x2E7F for ch in text)


def _segment_font(text: str, px: int):
    return _font(px, _is_cjk(text))


def _paste(canvas, x: float, baseline: float, text: str, font, color: tuple[int, int, int], stroke: int) -> None:
    """一段字：先描深色邊、再填顏色。用灰階遮罩貼上（直接畫在透明底上，邊緣會變黑）。"""
    from PIL import Image, ImageDraw
    left, top, right, bottom = font.getbbox(text, anchor="ls", stroke_width=stroke)
    w, h = right - left, bottom - top
    if w <= 0 or h <= 0:
        return
    for fill, width in ((OUTLINE, stroke), ((*color, 255), 0)):
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).text((-left, -top), text, font=font, fill=255, anchor="ls",
                                  stroke_width=width, stroke_fill=255)
        layer = Image.new("RGBA", (w, h), fill[:3] + (0,))
        layer.putalpha(mask if fill[3] == 255 else mask.point(lambda v, a=fill[3]: v * a // 255))
        canvas.alpha_composite(layer, (round(x + left), round(baseline + top)))


def render(rows: list[Row], *, opacity: int, scale: float, colors: dict[str, str] | None = None,
           min_width: int = 0, grab: bool = True):
    """rows → RGBA 圖。scale＝DPI 倍率 × 設定的大小（1.0 是 96 DPI、100％）。
    grab：背景至少留 1/255 的不透明，整個框都抓得到、拖得動（全透明的像素滑鼠會穿過去）；鎖定時不用。
    min_width：寬度只變寬、不忽寬忽窄（數字位數變了才會）。"""
    from PIL import Image, ImageDraw
    colors = colors or {}
    px = max(8, round(BASE_PX * scale))
    small = max(7, round(px * 0.68))
    stroke = max(1, round(px / 14))
    pad_x, pad_y = round(px * 0.6), round(px * 0.4)
    gap, unit_gap, label_gap = round(px * 0.55), round(px * 0.12), round(px * 0.7)
    line = round(px * 1.42)

    def width_of(segs) -> float:
        total, prev_small = 0.0, None
        for i, seg in enumerate(segs):
            if i:
                total += unit_gap if seg.small and not prev_small else gap
            total += _segment_font(seg.text, small if seg.small else px).getlength(seg.text)
            prev_small = seg.small
        return total

    label_w = max((_segment_font(r.label, px).getlength(r.label) for r in rows), default=0)
    body_w = max((width_of(r.segs) for r in rows), default=0)
    width = max(min_width, math.ceil(pad_x * 2 + label_w + label_gap + body_w))
    height = pad_y * 2 + line * max(1, len(rows))
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    alpha = round(255 * max(0, min(100, opacity)) / 100)
    if alpha or grab:
        d = ImageDraw.Draw(canvas)
        radius = round(px * 0.5)
        d.rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=(*BG, max(alpha, 1 if grab else 0)))
        if alpha >= 100:                                  # 夠不透明才畫細框，不然只是一圈灰線
            d.rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, outline=(*BORDER, alpha), width=1)
    for n, row in enumerate(rows):
        baseline = pad_y + line * n + round(line * 0.73)
        _paste(canvas, pad_x, baseline, row.label, _segment_font(row.label, px), tone_color(row.tone, colors), stroke)
        x, prev_small = pad_x + label_w + label_gap, None
        for i, seg in enumerate(row.segs):
            if i:
                x += unit_gap if seg.small and not prev_small else gap
            font = _segment_font(seg.text, small if seg.small else px)
            _paste(canvas, x, baseline, seg.text, font, tone_color(seg.tone, colors), stroke)
            x += font.getlength(seg.text)
            prev_small = seg.small
    return canvas
