"""探測：列出這台電腦用 LHM 讀得到的所有感測器，寫到 data/probe/。probe.bat 會先要系統管理員權限再跑這個。

    .venv\\Scripts\\python.exe -m hwglance.probe          # 結果：data/probe/probe-日期-時間.txt（＋ LHM 報告）
    .venv\\Scripts\\python.exe -m hwglance.probe --demo   # 用示範資料跑一遍（檢查輸出格式）

結果檔分三段：總覽（畫面上方那一排會出現什麼）、每個硬體整理後的樣子、LHM 原始讀數。
拿去跟 HWMonitor 對照時看第三段的數字。
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import platform
import sys
import time
from datetime import datetime

from . import __version__, paths, system
from .sensors.demo import DemoSource
from .sensors.lhm import ALL_COMPONENTS, LhmSource
from .sensors.model import RawHardware
from .sensors.normalize import normalize


class _MemoryCounters(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


def working_set_mb() -> float | None:
    """這個程序目前佔用的實體記憶體（工作集），單位 MB。"""
    try:
        counters = _MemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        process = ctypes.windll.kernel32.GetCurrentProcess()
        if ctypes.windll.psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
            return counters.WorkingSetSize / 1024 / 1024
    except (AttributeError, OSError):
        pass
    return None


def _fmt(v: float | None) -> str:
    return "—" if v is None else f"{v:.1f}"


def tree_lines(tree: list[RawHardware]) -> list[str]:
    out: list[str] = []

    def walk(hw: RawHardware, depth: int) -> None:
        pad = "  " * depth
        out.append(f"{pad}[{hw.type}] {hw.name}    {hw.id}")
        for s in hw.sensors:
            out.append(f"{pad}    {s.type:<12} {s.name:<34} {_fmt(s.value):>9}"
                       f"   (最低 {_fmt(s.min)}, 最高 {_fmt(s.max)})   {s.id}")
        for child in hw.children:
            walk(child, depth + 1)

    for hw in tree:
        walk(hw, 0)
    return out


def snapshot_lines(snap: dict) -> list[str]:
    out = ["== 總覽（畫面上方那一排）=="]
    for tile in snap["overview"]:
        out.append(f"  {tile['label']:<22} {_fmt(tile['value']):>6} °C   {tile['level']:<5} {tile['sub']}")
    if not snap["overview"]:
        out.append("  （一個溫度都沒有）")
    out.append("")
    out.append("== 每個硬體 ==")
    for d in snap["devices"]:
        out.append(f"* {d['kind']:<8} {d['name']}  {d['sub']}")
        for t in d["temps"]:
            out.append(f"    溫度  {t['label']:<16} {_fmt(t['value']):>6} °C  {t['level']:<5} ({t['role']})")
        for s in d["stats"]:
            total = f" / {s['total']}" if "total" in s else ""
            out.append(f"    數值  {s['label']:<16} {s['value']}{total} {s['unit']}  {s.get('note', '')}")
        for f in d["fans"]:
            pct = f"  {f['percent']}%" if f["percent"] is not None else ""
            out.append(f"    風扇  {f['label']:<16} {f['value']} RPM{pct}")
        if d["unplugged"]:
            out.append(f"    （另有 {d['unplugged']} 個風扇接頭沒接或沒轉過）")
    return out


def spec_lines(specs: dict, errors: list[str]) -> list[str]:
    out = ["== 規格（詳細資訊視窗用）=="]
    for key, value in specs.items():
        if isinstance(value, list) or (isinstance(value, dict) and all(isinstance(v, dict) for v in value.values())):
            out.append(f"  {key}:")
            items = value if isinstance(value, list) else [f"{k}: {v}" for k, v in value.items()]
            out += [f"    {item}" for item in items]
        else:
            out.append(f"  {key}: {value}")
    out += [f"  讀不到 {e}" for e in errors]
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m hwglance.probe", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", action="store_true", help="用示範資料，不碰硬體")
    ap.add_argument("--bench", type=int, default=10, help="量幾次更新時間（預設 10）")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    started = datetime.now()
    admin, pawnio = system.is_admin(), system.pawnio_version()
    lines = [f"HWGlance {__version__} 探測  {started:%Y-%m-%d %H:%M:%S}",
             f"系統管理員：{'是' if admin else '否'}    PawnIO：{pawnio or '沒有安裝'}",
             f"Python {platform.python_version()}    Windows {platform.version()}", ""]
    source = DemoSource(seed=1) if a.demo else LhmSource(paths.lhm_dir(), ALL_COMPONENTS)
    report = ""
    t0 = time.perf_counter()
    try:
        source.open()
        lines.append(f"來源：{source.kind} {source.version}，開啟花 {time.perf_counter() - t0:.2f} 秒")
        source.update(slow=True)
        time.sleep(1.0)                                  # 負載、時脈要兩次讀數才算得出來
        t1 = time.perf_counter()
        source.update(slow=True)
        lines.append(f"全部更新一次（含硬碟）：{(time.perf_counter() - t1) * 1000:.0f} ms")
        tree = source.read()
        timings = []
        for _ in range(max(0, a.bench)):
            time.sleep(0.2)
            t2 = time.perf_counter()
            source.update(slow=False)
            source.read()
            timings.append((time.perf_counter() - t2) * 1000)
        if timings:
            lines.append(f"每秒那一輪（不含硬碟）：平均 {sum(timings) / len(timings):.1f} ms、最慢 {max(timings):.1f} ms")
        ws = working_set_mb()
        if ws is not None:
            lines.append(f"這個程序的記憶體：{ws:.0f} MB")
        for e in getattr(source, "errors", []):
            lines.append(f"讀取錯誤：{e}")
        lines.append("")
        lines += snapshot_lines(normalize(tree))
        lines += [""] + spec_lines(source.specs(), getattr(source, "spec_errors", []))
        lines += ["", "== LHM 原始讀數 =="] + tree_lines(tree)
        if isinstance(source, LhmSource):
            report = source.report()
    except Exception as e:                               # noqa: BLE001 - 失敗也要留下紀錄
        lines.append(f"失敗：{type(e).__name__}: {e}")
    finally:
        try:
            source.close()
        except Exception:                                # noqa: BLE001
            pass

    out_dir = paths.probe_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"probe-{started:%Y%m%d-%H%M%S}"
    main_file = out_dir / f"{stem}.txt"
    main_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if report:
        (out_dir / f"{stem}-lhm-report.txt").write_text(report, encoding="utf-8")
    print("\n".join(lines[: lines.index("== LHM 原始讀數 ==")] if "== LHM 原始讀數 ==" in lines else lines))
    print(f"\n完整結果：{main_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
