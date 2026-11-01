"""取得 Intel PresentMon 官方 release 的命令列版（讀遊戲 FPS 用），放到 vendor/presentmon/。

    .venv\\Scripts\\python.exe scripts\\fetch_presentmon.py               # 從 GitHub 下載（1 MB）
    .venv\\Scripts\\python.exe scripts\\fetch_presentmon.py --exe X.exe   # 用已經下載好的
    .venv\\Scripts\\python.exe scripts\\fetch_presentmon.py --force       # 已經有了也重做

exe 先比對 SHA-256（GitHub release 公布的），對不上就不放。授權是 MIT，LICENSE.txt 與 THIRD_PARTY.txt
一起放進去（可攜版的第三方授權也照抄）。沒有它也能用 HWGlance，只是迷你模式的 FPS 讀不到。
原始碼：https://github.com/GameTechDev/PresentMon
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

VERSION = "2.6.0"
EXE = f"PresentMon-{VERSION}-x64.exe"
URL = f"https://github.com/GameTechDev/PresentMon/releases/download/v{VERSION}/{EXE}"
SHA256 = "b2a706bc6ad475749e3b7e3409263aa1e6906d45bdcf993f6dbc0f660188f1af"
TEXTS = {name: f"https://raw.githubusercontent.com/GameTechDev/PresentMon/v{VERSION}/{name}"
         for name in ("LICENSE.txt", "THIRD_PARTY.txt")}
TARGET = Path(__file__).resolve().parents[1] / "vendor" / "presentmon"
NOTICE = f"""Intel PresentMon {VERSION}（命令列版），從官方 release 下載。HWGlance 用它讀遊戲 FPS（ETW，不注入遊戲）。
來源：{URL}
SHA-256：{SHA256}
授權：MIT（LICENSE.txt）；它用到的第三方元件見 THIRD_PARTY.txt
原始碼：https://github.com/GameTechDev/PresentMon/tree/v{VERSION}
重建：.venv\\Scripts\\python.exe scripts\\fetch_presentmon.py
"""


def installed() -> bool:
    stamp = TARGET / "VERSION"
    return (TARGET / EXE).is_file() and stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == VERSION


def download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", type=Path, help=f"已經下載好的 {EXE}")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if installed() and not a.force:
        print(f"vendor/presentmon 已經是 {VERSION}")
        return 0
    if a.exe:
        data = a.exe.read_bytes()
    else:
        print(f"下載 {URL}")
        data = download(URL)
    digest = hashlib.sha256(data).hexdigest()
    if digest != SHA256:
        print(f"SHA-256 對不上，不放：{digest}")
        return 1
    TARGET.mkdir(parents=True, exist_ok=True)
    for old in TARGET.glob("PresentMon*.exe"):           # 換版本時舊的拿掉，免得找到兩個
        if old.name != EXE:
            old.unlink()
    (TARGET / EXE).write_bytes(data)
    for name, url in TEXTS.items():
        try:
            (TARGET / name).write_bytes(download(url))
        except OSError as e:
            print(f"{name} 下載失敗（{e}），授權見 {url}")
    (TARGET / "NOTICE.txt").write_text(NOTICE, encoding="utf-8")
    (TARGET / "VERSION").write_text(VERSION + "\n", encoding="utf-8")
    print(f"已放好 {TARGET / EXE}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
