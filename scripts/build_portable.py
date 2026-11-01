"""產生可攜版：解壓到另一台電腦就能用，不用裝 Python、Node.js、git。

    .venv\\Scripts\\python.exe scripts\\build_portable.py           # → build\\HWGlance-版本-portable-win64.zip
    .venv\\Scripts\\python.exe scripts\\build_portable.py --no-zip  # 只產生資料夾 build\\HWGlance（驗證用）

裡面有：
- python\\：python.org 官方的嵌入版 Python（跟這個 venv 同一版，比對 SHA-256），加上執行要用的套件
  （版本照這個 venv，pip freeze 當限制；測試用的 pytest、httpx 不放）
- src\\hwglance、vendor\\lhm（LibreHardwareMonitor，MPL-2.0）、frontend\\dist（建好的介面）、assets\\app.ico
- HWGlance.bat（開監控，會要系統管理員）、probe.bat（探測）、使用說明.txt、第三方授權.txt
設定、log、黑盒子存在解壓位置的 data\\。這台的開發方式（.venv、run.bat、setup.bat）不受影響。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
CACHE = BUILD / "cache"
# 嵌入版 Python：版本 → SHA-256（python.org 下載頁公布的，2026-11-01 核對過）。venv 換版本就在這裡加一行
EMBED = {"3.14.3": "ad4961a479dedbeb7c7d113253f8db1b1935586b73c27488712beec4f2c894e6"}
EMBED_URL = "https://www.python.org/ftp/python/{v}/python-{v}-embed-amd64.zip"
# 執行要用的套件（相依的會一起裝）；版本照 venv
RUNTIME = ("fastapi", "uvicorn", "pythonnet", "pywebview", "pystray", "Pillow")

HWGLANCE_BAT = r"""@echo off
rem ---------------------------------------------------------------
rem  HWGlance (portable) - starts the monitor.
rem  CPU, motherboard and disk temperatures need administrator rights
rem  and the PawnIO driver (https://pawnio.eu), so this asks for
rem  administrator rights (UAC). Saying no still opens the window with
rem  GPU and usage readings. Settings, logs and the black box are kept
rem  in the data folder next to this file.
rem
rem  ASCII only, CRLF line endings: cmd.exe reads .bat files in the
rem  system ANSI codepage.
rem ---------------------------------------------------------------
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%python\python.exe"
set "PYW=%ROOT%python\pythonw.exe"
if not exist "%PY%" goto broken

rem Already running: bring its window up instead of starting a second copy.
"%PY%" -m hwglance --activate
if not errorlevel 1 exit /b 0

powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Start-Process -FilePath '%PYW%' -ArgumentList '-m','hwglance' -WorkingDirectory '%ROOT%' -Verb RunAs -ErrorAction Stop } catch { exit 1 }"
if not errorlevel 1 exit /b 0

echo [HWGlance] No administrator rights: starting with GPU and usage readings only.
start "" "%PYW%" -m hwglance
exit /b 0

:broken
echo [HWGlance] python\python.exe is missing. Unzip the whole HWGlance folder again.
pause
exit /b 1
"""

PROBE_BAT = r"""@echo off
rem ---------------------------------------------------------------
rem  HWGlance (portable) - sensor probe. Lists every sensor that
rem  LibreHardwareMonitor can read on this PC and writes the result to
rem  data\probe\. Bring that file back to improve support for this PC.
rem  Asks for administrator rights (UAC), then runs itself again.
rem
rem  ASCII only, CRLF line endings.
rem ---------------------------------------------------------------
setlocal
set "ROOT=%~dp0"
net session >nul 2>&1
if not errorlevel 1 goto elevated
echo [HWGlance] Asking for administrator rights...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Start-Process -FilePath '%~f0' -Verb RunAs -ErrorAction Stop } catch { exit 1 }"
if errorlevel 1 goto denied
exit /b 0

:elevated
cd /d "%ROOT%"
"%ROOT%python\python.exe" -m hwglance.probe
echo.
pause
exit /b 0

:denied
echo [HWGlance] Administrator rights were not granted.
pause
exit /b 1
"""

GUIDE = """HWGlance 可攜版 {version}
==========================

輕量的硬體溫度監控：一眼看到 CPU、顯示卡、主機板、每顆硬碟的溫度，另外有事件記錄（當機、顯示卡、硬碟出錯）
與黑盒子（當機前幾分鐘的讀數）。不用安裝 Python、Node.js；整個資料夾放在哪裡都可以。

怎麼開
------
1. 把整個 HWGlance 資料夾解壓到想放的地方（例如 D:\\Tools\\HWGlance）。路徑不要有單引號（'）。
2. 雙擊 HWGlance.bat。會跳出「是否允許變更」（系統管理員權限）：按「是」才讀得到 CPU、主機板、硬碟的溫度；
   按「否」也能開，只是只有顯示卡溫度與各種用量。
3. Windows 若跳出「Windows 已保護您的電腦」：按「其他資訊」→「仍要執行」。或先在 zip 檔上按右鍵 →
   內容 →「解除封鎖」再解壓。
4. 關掉視窗後還在右下角系統匣繼續監控，圖示上的數字是 CPU 溫度。想開機就跑，可以把 HWGlance.bat 的捷徑
   放進「啟動」資料夾（Win+R 輸入 shell:startup）。

要讀 CPU、主機板溫度：裝 PawnIO
-------------------------------
CPU 與主機板的溫度要 PawnIO 驅動。到 https://pawnio.eu 下載 PawnIO_setup.exe 安裝（要系統管理員），
裝好後重開 HWGlance。顯示卡溫度、事件記錄、黑盒子不需要 PawnIO。

需要
----
- Windows 10／11，64 位元（x64）
- Microsoft Edge WebView2 Runtime：Windows 11 內建，Windows 10 通常也有。沒有的話 HWGlance 會改用預設瀏覽器開畫面
- .NET Framework 4.8：Windows 10 1903 以後內建

其他
----
- 設定、log、黑盒子都存在這個資料夾的 data 裡；換新版時把舊的 data 資料夾複製過去就好。
- probe.bat：列出這台讀得到的所有感測器，結果在 data\\probe。有讀數不對或缺的，把那個檔帶回來就能補規則。
- 公司電腦若不能用系統管理員或不能裝驅動，還是看得到顯示卡溫度、各種用量、事件記錄與黑盒子。
- 筆電有獨立顯示卡時，HWGlance 每秒讀顯示卡，可能讓它沒辦法休眠、比較耗電；用電池又不需要監控時，
  在系統匣的圖示按右鍵結束 HWGlance。
- 第三方元件與授權見「第三方授權.txt」。
"""


def run(cmd: list[str], **kw) -> None:
    print("  $", " ".join(cmd))
    subprocess.run(cmd, check=True, **kw)


def embedded_python() -> Path:
    version = ".".join(map(str, sys.version_info[:3]))
    sha = EMBED.get(version)
    if sha is None:
        sys.exit(f"沒有 Python {version} 嵌入版的 SHA-256：到 python.org 的下載頁核對後加進 EMBED")
    path = CACHE / f"python-{version}-embed-amd64.zip"
    if not path.is_file():
        url = EMBED_URL.format(v=version)
        print(f"下載 {url}")
        CACHE.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=120) as r:
            data = r.read()
        path.write_bytes(data)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != sha:
        path.unlink()
        sys.exit(f"嵌入版 Python 的 SHA-256 對不上（{digest}），已刪掉，請重跑")
    return path


def ensure_parts() -> None:
    if not (ROOT / "vendor" / "lhm" / "LibreHardwareMonitorLib.dll").is_file():
        run([sys.executable, str(ROOT / "scripts" / "fetch_lhm.py")])
    if not (ROOT / "frontend" / "dist" / "index.html").is_file():
        run(["npm", "run", "build"], cwd=ROOT / "frontend", shell=True)


def write_text(path: Path, text: str, *, ascii_only: bool = False) -> None:
    """.bat：ASCII＋CRLF；說明文字：UTF-8（加 BOM，記事本一定認得）＋CRLF。"""
    data = text.replace("\r\n", "\n").replace("\n", "\r\n")
    path.write_bytes(data.encode("ascii") if ascii_only else b"\xef\xbb\xbf" + data.encode("utf-8"))


def install_packages(site: Path) -> None:
    freeze = subprocess.run([sys.executable, "-m", "pip", "freeze", "--exclude-editable"], check=True,
                            capture_output=True, text=True).stdout
    constraints = BUILD / "constraints.txt"
    constraints.write_text(freeze, encoding="utf-8")
    # --prefer-binary 不是 --only-binary：proxy_tools（pywebview 要的）只有原始碼包，是純 Python，在這裡打包就好
    run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location",
         "--prefer-binary", "--target", str(site), "-c", str(constraints), *RUNTIME])
    shutil.rmtree(site / "bin", ignore_errors=True)          # uvicorn.exe 這類指令：指著這台的 venv，用不到


def notices(site: Path) -> str:
    lines = ["HWGlance 用到的第三方元件與授權（各套件的授權全文在 python\\Lib\\site-packages\\*.dist-info）", "",
             "- Python（嵌入版）：PSF License，全文在 python\\LICENSE.txt",
             "- LibreHardwareMonitor 0.9.6：Mozilla Public License 2.0，說明在 vendor\\lhm\\NOTICE.txt，"
             "原始碼 https://github.com/LibreHardwareMonitor/LibreHardwareMonitor", ""]
    for dist in sorted(importlib.metadata.distributions(path=[str(site)]), key=lambda d: d.metadata["Name"].lower()):
        meta = dist.metadata
        lic = meta.get("License-Expression") or ""
        if not lic:
            classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
            lic = "、".join(classifiers)
        if not lic:
            first = (meta.get("License") or "").strip().splitlines()
            lic = first[0][:80] if first else "見 dist-info"
        lines.append(f"- {meta['Name']} {meta['Version']}：{lic}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-zip", action="store_true", help="只產生資料夾，不壓縮")
    a = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT / "src"))
    from hwglance import __version__

    ensure_parts()
    embed = embedded_python()
    out = BUILD / "HWGlance"
    if out.exists():
        shutil.rmtree(out)
    python = out / "python"
    print(f"解出嵌入版 Python → {python}")
    with zipfile.ZipFile(embed) as z:
        z.extractall(python)
    [pth] = python.glob("python3*._pth")
    stdlib = pth.with_suffix(".zip").name
    # 嵌入版預設不看 site-packages、也找不到程式：照順序列出要找的地方。不寫 import site：
    # 打開的話會把那台使用者自己裝的套件（AppData\Roaming\Python）也加進來；這裡的套件沒有 .pth 要處理
    pth.write_text(f"{stdlib}\n.\nLib\\site-packages\n..\\src\n", encoding="ascii")
    site = python / "Lib" / "site-packages"
    print("安裝執行用的套件（版本照這個 venv）")
    install_packages(site)

    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(ROOT / "src" / "hwglance", out / "src" / "hwglance", ignore=ignore)
    shutil.copytree(ROOT / "vendor" / "lhm", out / "vendor" / "lhm")
    shutil.copytree(ROOT / "frontend" / "dist", out / "frontend" / "dist")
    (out / "assets").mkdir()
    shutil.copy2(ROOT / "assets" / "app.ico", out / "assets" / "app.ico")
    write_text(out / "HWGlance.bat", HWGLANCE_BAT, ascii_only=True)
    write_text(out / "probe.bat", PROBE_BAT, ascii_only=True)
    write_text(out / "使用說明.txt", GUIDE.format(version=__version__))
    write_text(out / "第三方授權.txt", notices(site))

    files = [p for p in out.rglob("*") if p.is_file()]
    size = sum(p.stat().st_size for p in files)
    print(f"資料夾：{out}（{len(files)} 個檔，{size / 2**20:.1f} MB）")
    if not a.no_zip:
        target = BUILD / f"HWGlance-{__version__}-portable-win64.zip"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for p in files:
                z.write(p, Path("HWGlance") / p.relative_to(out))
        print(f"zip：{target}（{target.stat().st_size / 2**20:.1f} MB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
