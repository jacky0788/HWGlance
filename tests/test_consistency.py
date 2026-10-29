"""專案規則：違反的話這裡會擋下來。"""
from __future__ import annotations

import re

from hwglance import paths

FRONTEND_SRC = paths.APP_ROOT / "frontend" / "src"
# 單位規則：前端不寫 px（只有 0／0.5／1px 的細線可以），要用 rem、em、%、fr、vw／vh
PX = re.compile(r"(?<![\w.-])(\d*\.?\d+)px\b")
ALLOWED_PX = {"0", "0.5", ".5", "1"}
EFFECT_EXPRESSION = re.compile(r"use(?:Layout)?Effect\(\(\) => (?!\{|\(\) =>)")


def frontend_files(*suffixes: str):
    if not FRONTEND_SRC.is_dir():
        return []
    return [f for f in FRONTEND_SRC.rglob("*") if f.suffix in suffixes]


def test_bat_files_are_ascii_crlf():
    bats = list(paths.APP_ROOT.glob("*.bat"))
    assert bats, "專案第一層應該有 .bat"
    for f in bats:
        data = f.read_bytes()
        assert all(b < 128 for b in data), f"{f.name} 只能有 ASCII（cmd.exe 用系統碼頁讀 .bat）"
        assert b"\n" not in data.replace(b"\r\n", b""), f"{f.name} 要用 CRLF 換行"


def test_no_px_in_frontend():
    bad = []
    for f in frontend_files(".ts", ".tsx", ".css"):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            for m in PX.finditer(line):
                if m.group(1) not in ALLOWED_PX:
                    bad.append(f"{f.relative_to(paths.APP_ROOT)}:{n}: {line.strip()}")
    assert not bad, "前端不寫 px（改用 rem／em／%／fr）：\n" + "\n".join(bad)


def test_effects_use_block_bodies():
    """useEffect(() => 某個運算式) 會把運算式的值當成 cleanup 交給 React；
    新版 Chromium 的 window.scrollTo() 回傳 Promise，這樣寫會讓頁面當掉（ProjectHub 2026-10-23 踩過）。"""
    bad = []
    for f in frontend_files(".ts", ".tsx"):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if EFFECT_EXPRESSION.search(line):
                bad.append(f"{f.relative_to(paths.APP_ROOT)}:{n}: {line.strip()}")
    assert not bad, "effect 要用大括號包起來：\n" + "\n".join(bad)


def test_only_lhm_adapter_touches_dotnet():
    """pythonnet（clr）只能出現在 sensors/lhm.py：其他地方都要能在沒有 .NET 的測試裡跑。"""
    offenders = []
    for f in (paths.APP_ROOT / "src" / "hwglance").rglob("*.py"):
        if f.name == "lhm.py":
            continue
        text = f.read_text(encoding="utf-8")
        if re.search(r"^\s*(import clr|import pythonnet|from System|from LibreHardwareMonitor)", text, re.M):
            offenders.append(str(f.relative_to(paths.APP_ROOT)))
    assert not offenders, "只有 sensors/lhm.py 可以碰 .NET：" + "、".join(offenders)
