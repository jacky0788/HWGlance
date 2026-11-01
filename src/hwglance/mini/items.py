"""迷你模式（疊加層）能顯示什麼：分組（一組畫成一行，像 RivaTuner 的「GPU 65°C 98% 2805MHz」）、
每一項從快照哪裡拿值、怎麼寫。純函式，tests/test_mini.py 用示範資料測。

settings.py 用 GROUP_IDS／ITEM_IDS 檢查設定；api 把 catalog() 給設定頁勾選；render.py 把 rows() 畫出來。
一段字帶一個 tone（顏色的意思，不是顏色本身）：kind:gpu…、level:ok/warm/hot/none、load、clock、power、fan、data、
fps、plain、unit、muted；render.py 照深色主題換成顏色。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

# (分組, 設定頁與畫面上的名稱, 沒有中文字型時的名稱, 種類色)
GROUPS: tuple[tuple[str, str, str, str | None], ...] = (
    ("gpu", "GPU", "GPU", "gpu"),
    ("cpu", "CPU", "CPU", "cpu"),
    ("ram", "記憶體", "RAM", "memory"),
    ("board", "主機板", "MB", "board"),
    ("disk", "硬碟", "DISK", "storage"),
    ("fps", "FPS", "FPS", None),
    ("other", "其他", "ALERT", None),
    ("time", "時間", "TIME", None),
)
# (項目, 分組, 設定頁的名稱)；同一組裡照這個順序畫
ITEMS: tuple[tuple[str, str, str], ...] = (
    ("gpu.temp", "gpu", "溫度"), ("gpu.hotspot", "gpu", "熱點"), ("gpu.memtemp", "gpu", "顯存溫度"),
    ("gpu.load", "gpu", "負載"), ("gpu.clock", "gpu", "時脈"), ("gpu.power", "gpu", "功耗"),
    ("gpu.vram", "gpu", "顯存用量"), ("gpu.fan", "gpu", "風扇"),
    ("cpu.temp", "cpu", "溫度"), ("cpu.load", "cpu", "負載"), ("cpu.clock", "cpu", "時脈"), ("cpu.power", "cpu", "功耗"),
    ("ram.used", "ram", "已用"), ("ram.load", "ram", "使用率"),
    ("board.max", "board", "最熱的感測器"),
    ("disk.max", "disk", "最熱的一顆"),
    ("fps.fps", "fps", "FPS"), ("fps.frametime", "fps", "幀時間"), ("fps.low1", "fps", "1% Low"),
    ("other.worst", "other", "最需要注意的硬體"),
    ("time.clock", "time", "時鐘"),
)
GROUP_IDS = tuple(g for g, *_ in GROUPS)
ITEM_IDS = tuple(i for i, *_ in ITEMS)
GROUP_OF = {i: g for i, g, _ in ITEMS}
# 預設：以前的迷你視窗（CPU、GPU 的溫度與負載＋其他硬體最需要注意的）再加上 FPS
DEFAULT_ITEMS = ("gpu.temp", "gpu.load", "cpu.temp", "cpu.load", "fps.fps", "fps.frametime", "other.worst")
DEFAULT_ORDER = GROUP_IDS
SEVERITY = {"hot": 2, "warm": 1, "ok": 0, "none": -1}


@dataclass(frozen=True)
class Seg:
    text: str
    tone: str
    small: bool = False         # 單位、小標：字小一點


@dataclass(frozen=True)
class Row:
    group: str
    label: str
    tone: str                   # 組名的顏色
    segs: tuple[Seg, ...]


class FpsSource(Protocol):
    def status(self) -> str | None: ...
    def stats(self): ...


def catalog() -> list[dict]:
    """設定頁用：[{group, label, items: [{id, label}]}]，照預設順序。"""
    return [{"group": g, "label": name, "items": [{"id": i, "label": text} for i, grp, text in ITEMS if grp == g]}
            for g, name, _, _ in GROUPS]


def _device(snap: dict, kind: str) -> dict | None:
    devices = [d for d in snap.get("devices", []) if d["kind"] == kind]
    # 有內顯＋獨顯時挑有溫度的那張（內顯沒有自己的溫度）
    return next((d for d in devices if d.get("headline")), devices[0] if devices else None)


def _stat(dev: dict | None, key: str) -> dict | None:
    return next((s for s in dev["stats"] if s["key"] == key), None) if dev else None


def _temp(dev: dict | None, role: str | None = None) -> dict | None:
    if dev is None:
        return None
    if role is None:
        return next((t for t in dev["temps"] if t["id"] == dev.get("headline")), None)
    return next((t for t in dev["temps"] if t["role"] == role), None)


def _degrees(t: dict | None, unit: str) -> tuple[Seg, ...]:
    if t is None or t.get("value") is None:
        return (Seg("—", "muted"),)
    value = t["value"] * 9 / 5 + 32 if unit == "F" else t["value"]
    return Seg(f"{round(value)}", f"level:{t.get('level', 'none')}"), Seg(f"°{unit}", "unit", True)


def _number(value: float | None, text: str, unit: str, tone: str) -> tuple[Seg, ...]:
    if value is None:
        return (Seg("—", "muted"),)
    return Seg(text, tone), Seg(unit, "unit", True)


def _clock(stat: dict | None, cpu: bool) -> tuple[Seg, ...]:
    if stat is None:
        return (Seg("—", "muted"),)
    v = stat["value"]
    if cpu and v >= 1000:
        return Seg(f"{v / 1000:.2f}", "clock"), Seg("GHz", "unit", True)
    return Seg(f"{round(v)}", "clock"), Seg("MHz", "unit", True)


def _worst(snap: dict, unit: str) -> tuple[Seg, ...]:
    """以前迷你視窗最下面那一行：CPU、GPU 以外最需要注意的那個。"""
    rest = [t for t in snap.get("overview", []) if t["kind"] not in ("cpu", "gpu")]
    if not rest:
        return (Seg("沒有其他溫度", "muted"),)
    worst = sorted(rest, key=lambda t: (SEVERITY[t["level"]], t["value"] or 0), reverse=True)[0]
    if SEVERITY[worst["level"]] <= 0:
        return (Seg("都正常", "level:ok"),)
    word = "過熱" if worst["level"] == "hot" else "偏高"
    return (Seg(f"{word} {worst['label']}", f"level:{worst['level']}"), *_degrees(worst, unit))


def _hottest(temps: list[dict]) -> dict | None:
    known = [t for t in temps if t and t.get("value") is not None]
    return max(known, key=lambda t: (SEVERITY[t["level"]], t["value"])) if known else None


def item_segs(item: str, snap: dict, fps, unit: str, now: float) -> tuple[Seg, ...]:
    cpu, gpu, ram = _device(snap, "cpu"), _device(snap, "gpu"), _device(snap, "memory")
    if item == "gpu.temp":
        return _degrees(_temp(gpu), unit)
    if item == "gpu.hotspot":
        return (Seg("熱點", "muted", True), *_degrees(_temp(gpu, "gpu_hotspot"), unit))
    if item == "gpu.memtemp":
        return (Seg("顯存", "muted", True), *_degrees(_temp(gpu, "gpu_memory"), unit))
    if item in ("gpu.load", "cpu.load", "ram.load"):
        s = _stat({"gpu.load": gpu, "cpu.load": cpu, "ram.load": ram}[item], "load")
        return _number(s and s["value"], f"{round(s['value'])}" if s else "", "%", "load")
    if item in ("gpu.clock", "cpu.clock"):
        return _clock(_stat(gpu if item == "gpu.clock" else cpu, "clock"), item == "cpu.clock")
    if item in ("gpu.power", "cpu.power"):
        s = _stat(gpu if item == "gpu.power" else cpu, "power")
        return _number(s and s["value"], f"{round(s['value'])}" if s else "", "W", "power")
    if item == "gpu.vram":
        s = _stat(gpu, "vram")
        if s is None:
            return (Seg("—", "muted"),)
        total = f"/{s['total'] / 1024:.1f}" if s.get("total") else ""
        return Seg(f"{s['value'] / 1024:.1f}{total}", "data"), Seg("GB", "unit", True)
    if item == "gpu.fan":
        s = _stat(gpu, "fan")
        return _number(s and s["value"], f"{round(s['value'])}" if s else "", "RPM", "fan")
    if item == "cpu.temp":
        return _degrees(_temp(cpu), unit)
    if item == "ram.used":
        s = _stat(ram, "used")
        return _number(s and s["value"], f"{s['value']:.1f}" if s else "", "GB", "data")
    if item == "board.max":
        board = _device(snap, "board")
        t = _hottest(board["temps"] if board else [])
        return (Seg(t["label"], "muted", True), *_degrees(t, unit)) if t else (Seg("—", "muted"),)
    if item == "disk.max":
        disks = [d for d in snap.get("devices", []) if d["kind"] == "storage"]
        return _degrees(_hottest([_temp(d) for d in disks]), unit)
    if item.startswith("fps."):
        return _fps(item, fps)
    if item == "other.worst":
        return _worst(snap, unit)
    if item == "time.clock":
        return (Seg(time.strftime("%H:%M", time.localtime(now)), "plain"),)
    return ()


def _fps(item: str, fps) -> tuple[Seg, ...]:
    stats = fps.stats() if fps is not None and fps.status() is None else None
    if stats is None:
        return (Seg("—", "muted"),)
    if item == "fps.fps":
        return Seg(f"{round(stats.fps)}", "fps"), Seg("FPS", "unit", True)
    if item == "fps.frametime":
        return Seg(f"{stats.frametime:.1f}", "fps"), Seg("ms", "unit", True)
    if stats.low1 is None:
        return Seg("1% Low", "muted", True), Seg("—", "muted")
    return Seg("1% Low", "muted", True), Seg(f"{round(stats.low1)}", "fps")


def rows(snap: dict, items: tuple[str, ...], order: tuple[str, ...], fps=None, *, unit: str = "C",
         now: float | None = None, cjk: bool = True) -> list[Row]:
    """照設定的分組順序，一組一行；組裡沒勾任何項目就不畫。FPS 讀不到時那一行寫原因。"""
    now = time.time() if now is None else now
    names = {g: (zh if cjk else en, kind) for g, zh, en, kind in GROUPS}
    chosen = set(items)
    out = []
    for group in order:
        picked = [i for i, g, _ in ITEMS if g == group and i in chosen]
        if not picked:
            continue
        label, kind = names[group]
        tone = f"kind:{kind}" if kind else ("fps" if group == "fps" else "muted")
        if group == "fps" and fps is not None and (reason := fps.status()):
            out.append(Row(group, label, tone, (Seg(reason, "muted", True),)))
            continue
        segs: list[Seg] = []
        for item in picked:
            segs.extend(item_segs(item, snap, fps, unit, now))
        out.append(Row(group, label, tone, tuple(segs)))
    return out


def wants_fps(items: tuple[str, ...]) -> bool:
    return any(GROUP_OF.get(i) == "fps" for i in items)
