"""系統匣圖示：直接畫出 CPU 溫度（沒有就畫 GPU），底色跟著等級；滑過看 CPU、GPU 與有沒有偏高的硬體。

用 pystray（Windows 的 Shell_NotifyIcon）＋Pillow 畫圖。pystray 每換一次圖示會寫一個暫存 ICO，
所以只有數字或顏色變了才換。左鍵＝開主畫面；右鍵＝選單（主畫面、迷你模式（打勾＝開著）、
鎖定迷你模式（鎖定後滑鼠點不到它，只能從這裡或設定頁解除）、結束）。
畫圖與提示文字是純函式（tests/test_tray.py 測），Tray 類別才碰 pystray。
"""
from __future__ import annotations

import threading
import traceback
from collections.abc import Callable
from pathlib import Path

ICON_SIZE = 64
COLORS = {"ok": (13, 138, 95), "warm": (194, 116, 0), "hot": (198, 40, 40), "none": (95, 102, 115)}
FONTS = (r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf")
TIP_LIMIT = 127                      # Windows 的提示文字最多 128 個字元（含結尾）
SEVERITY = {"hot": 2, "warm": 1}


def display(value: float, unit: str) -> float:
    return value * 9 / 5 + 32 if unit == "F" else value


def degrees(value: float | None, unit: str) -> str:
    return "—" if value is None else f"{round(display(value, unit))}°"


def _first(tiles: list[dict], kind: str) -> dict | None:
    return next((t for t in tiles if t["kind"] == kind and t["value"] is not None), None)


def icon_text(snap: dict) -> tuple[str, str]:
    """圖示上的數字與等級：CPU 優先，沒有 CPU 溫度（例如沒裝 PawnIO）就用 GPU 核心。"""
    tiles = snap.get("overview", [])
    tile = _first(tiles, "cpu") or _first(tiles, "gpu")
    if tile is None:
        return "--", "none"
    return str(round(display(tile["value"], snap.get("prefs", {}).get("unit", "C")))), tile["level"]


def tooltip(snap: dict) -> str:
    unit = snap.get("prefs", {}).get("unit", "C")
    tiles = snap.get("overview", [])
    parts = [f"{t['label']} {degrees(t['value'], unit)}" for t in (_first(tiles, "cpu"), _first(tiles, "gpu")) if t]
    lines = ["HWGlance", "・".join(parts) or "讀不到 CPU／GPU 溫度"]
    worst = sorted((t for t in tiles if t["level"] in SEVERITY),
                   key=lambda t: (SEVERITY[t["level"]], t["value"] or 0), reverse=True)
    if worst:
        word = "過熱" if worst[0]["level"] == "hot" else "偏高"
        lines.append(f"{word}：" + "、".join(f"{t['label']} {degrees(t['value'], unit)}" for t in worst[:2]))
    elif tiles:
        lines.append(f"{len(tiles)} 個溫度都正常")
    return "\n".join(lines)[:TIP_LIMIT]


def _font(size: int):
    from PIL import ImageFont
    for path in FONTS:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def render(text: str, level: str, size: int = ICON_SIZE):
    """圓角方塊＋白色數字。數字越多字型越小，縮到工具列大小（16～36 像素）還看得清楚。"""
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=round(size * 0.2), fill=COLORS.get(level, COLORS["none"]))
    font = _font(round(size * {1: 0.8, 2: 0.72}.get(len(text), 0.52)))
    x0, y0, x1, y1 = d.textbbox((0, 0), text, font=font)
    d.text(((size - (x1 - x0)) / 2 - x0, (size - (y1 - y0)) / 2 - y0), text, font=font, fill="white")
    return im


class Tray:
    def __init__(self, on_open: Callable[[str], None], on_quit: Callable[[], None], mini=None) -> None:
        import pystray
        self._shown: tuple[str, str] | None = None
        self._tip = "HWGlance"

        def toggle_mini() -> None:
            if mini is not None and mini.visible:
                mini.hide()
            else:
                on_open("mini")

        items = [pystray.MenuItem("開啟主畫面", lambda: on_open("main"), default=True),
                 pystray.MenuItem("迷你模式", toggle_mini, checked=lambda _: bool(mini and mini.visible))]
        if mini is not None:
            items.append(pystray.MenuItem("鎖定迷你模式（滑鼠穿透）", lambda: mini.toggle_lock(),
                                          checked=lambda _: mini.locked))
        menu = pystray.Menu(*items, pystray.Menu.SEPARATOR, pystray.MenuItem("結束 HWGlance", lambda: on_quit()))
        self.icon = pystray.Icon("HWGlance", render("--", "none"), self._tip, menu)

    def refresh(self) -> None:
        """迷你模式開關、鎖定改了：選單的勾勾跟著變。"""
        try:
            self.icon.update_menu()
        except Exception:                                 # noqa: BLE001 - 選單沒更新不影響監控
            traceback.print_exc()

    def start(self) -> None:
        threading.Thread(target=self.icon.run, daemon=True, name="hwglance-tray").start()

    def update(self, snap: dict) -> None:
        """感測執行緒每一輪呼叫。只有變了才真的去動系統匣。"""
        shown = icon_text(snap)
        if shown != self._shown:
            self.icon.icon = render(*shown)
            self._shown = shown
        tip = tooltip(snap)
        if tip != self._tip:
            self.icon.title = tip
            self._tip = tip

    def notify(self, message: str, title: str) -> None:
        try:
            self.icon.notify(message, title)
        except Exception:                                 # noqa: BLE001 - 通知失敗不影響監控
            traceback.print_exc()

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:                                 # noqa: BLE001
            traceback.print_exc()
