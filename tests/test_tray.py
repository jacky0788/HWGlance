"""系統匣：圖示上的數字、顏色、提示文字（不開真的系統匣）。"""
from __future__ import annotations

from hwglance.tray import COLORS, TIP_LIMIT, icon_text, render, tooltip


def tile(kind: str, label: str, value: float | None, level: str = "ok") -> dict:
    return {"kind": kind, "label": label, "value": value, "level": level, "id": label, "device": kind,
            "sub": "", "max": value, "role": kind}


def snap(*tiles: dict, unit: str = "C") -> dict:
    return {"overview": list(tiles), "prefs": {"unit": unit}}


def test_icon_prefers_cpu_then_gpu():
    assert icon_text(snap(tile("gpu", "GPU 核心", 70.4), tile("cpu", "CPU", 61.6, "ok"))) == ("62", "ok")
    assert icon_text(snap(tile("gpu", "GPU 核心", 84.0, "warm"))) == ("84", "warm")     # 沒裝 PawnIO
    assert icon_text(snap()) == ("--", "none")
    assert icon_text(snap(tile("cpu", "CPU", 62.0), unit="F")) == ("144", "ok")


def test_tooltip_lists_cpu_gpu_and_the_worst_ones():
    text = tooltip(snap(tile("cpu", "CPU", 85.0, "warm"), tile("gpu", "GPU 核心", 70.0),
                        tile("storage", "KINGSTON SA2000M8500G", 56.0, "warm"),
                        tile("board", "晶片組", 81.0, "hot")))
    assert text.splitlines() == ["HWGlance", "CPU 85°・GPU 核心 70°", "過熱：晶片組 81°、CPU 85°"]
    assert tooltip(snap(tile("cpu", "CPU", 50.0))).splitlines()[-1] == "1 個溫度都正常"
    long = snap(*[tile("storage", "很長的硬碟名稱" * 5, 60.0, "warm") for _ in range(5)])
    assert len(tooltip(long)) <= TIP_LIMIT


def test_render_draws_a_colored_square_with_white_digits():
    im = render("62", "warm")
    assert im.size == (64, 64) and im.mode == "RGBA"
    assert im.getpixel((6, 32))[:3] == COLORS["warm"]          # 邊緣是底色
    colors = {im.getpixel((x, 32))[:3] for x in range(8, 56)}
    assert (255, 255, 255) in colors                              # 中間有白色數字
    assert render("144", "hot").size == (64, 64)
