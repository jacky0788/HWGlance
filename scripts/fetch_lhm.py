"""取得 LibreHardwareMonitor 官方 release，只把函式庫與相依的 DLL 解到 vendor/lhm/（不含它的圖形介面）。

    .venv\\Scripts\\python.exe scripts\\fetch_lhm.py               # 從 GitHub 下載（6.3 MB）
    .venv\\Scripts\\python.exe scripts\\fetch_lhm.py --zip X.zip   # 用已經下載好的 zip
    .venv\\Scripts\\python.exe scripts\\fetch_lhm.py --force       # 已經有了也重做

zip 先比對 SHA-256，對不上就不解壓。用的是 net472 版（.NET Framework 4.8，Windows 內建），
pythonnet 以 netfx 載入。LHM 是 MPL-2.0 授權，原始碼在 https://github.com/LibreHardwareMonitor/LibreHardwareMonitor
"""
from __future__ import annotations

import argparse
import hashlib
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

VERSION = "0.9.6"
URL = f"https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/download/v{VERSION}/LibreHardwareMonitor.zip"
SHA256 = "086d9f1b5a99e643edc2cfaaac16051685b551e4c5ac0b32a57c58c0e529c001"
# LHM 圖形介面自己用的，函式庫不需要
SKIP = {"LibreHardwareMonitor.exe", "Aga.Controls.dll", "OxyPlot.dll", "OxyPlot.WindowsForms.dll",
        "Microsoft.Win32.TaskScheduler.dll"}
TARGET = Path(__file__).resolve().parents[1] / "vendor" / "lhm"
NOTICE = f"""LibreHardwareMonitor {VERSION}（net472），從官方 release 解出來的函式庫與相依 DLL。
來源：{URL}
SHA-256：{SHA256}
授權：Mozilla Public License 2.0（https://mozilla.org/MPL/2.0/）
原始碼：https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/tree/v{VERSION}
重建：.venv\\Scripts\\python.exe scripts\\fetch_lhm.py
"""


def installed() -> bool:
    stamp = TARGET / "VERSION"
    return (TARGET / "LibreHardwareMonitorLib.dll").is_file() and stamp.is_file() and \
        stamp.read_text(encoding="utf-8").strip() == VERSION


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zip", type=Path, help="已經下載好的 LibreHardwareMonitor.zip")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if installed() and not a.force:
        print(f"vendor/lhm 已經是 {VERSION}")
        return 0
    if a.zip:
        data = a.zip.read_bytes()
    else:
        print(f"下載 {URL}")
        with urllib.request.urlopen(URL, timeout=120) as r:
            data = r.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != SHA256:
        print(f"SHA-256 對不上，不解壓：{digest}")
        return 1
    TARGET.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            name = info.filename
            if "/" in name or not name.lower().endswith(".dll") or name in SKIP:
                continue
            (TARGET / name).write_bytes(z.read(info))
            count += 1
    (TARGET / "NOTICE.txt").write_text(NOTICE, encoding="utf-8")
    (TARGET / "VERSION").write_text(VERSION + "\n", encoding="utf-8")
    print(f"已解出 {count} 個 DLL 到 {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
