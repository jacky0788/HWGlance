"""`python -m hwglance`：

    python -m hwglance                  # 開監控（本體＋系統匣＋主畫面）；run.bat 會先要系統管理員權限
    python -m hwglance --mode mini      # 一開始就是迷你視窗
    python -m hwglance --hidden         # 只開系統匣，不開視窗（之後的開機自動啟動用）
    python -m hwglance --serve --demo --data data/demo   # 只跑伺服器＋示範資料（開發、預覽介面用；
                                                         # 設定另外存，不會改到真的）
    python -m hwglance --serve --demo --demo-gpu-reset 20   # 示範資料，開啟 20 秒後模擬顯示卡重設
                                                            # （看暫停讀取、1 分鐘後重新連上的畫面）
    python -m hwglance --serve          # 只跑伺服器、真的讀數，用瀏覽器看
    python -m hwglance --activate       # 已經開著：叫出它的視窗並回傳 0；沒開著回傳 1（run.bat 用）
    python -m hwglance.probe            # 探測：列出讀得到的所有感測器（probe.bat）

伺服器一律只綁 127.0.0.1，預設埠 8796（被占用就換一個）。CPU、主機板、硬碟的溫度要 PawnIO 驅動
＋系統管理員才讀得到；沒有的話照樣能開，只是少了那些。
"""
from __future__ import annotations

import argparse
import os
import sys

from . import paths
from .core import DEFAULT_PORT


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m hwglance", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--serve", action="store_true", help="只跑伺服器，不開視窗")
    ap.add_argument("--demo", action="store_true", help="用示範資料（不碰硬體）")
    ap.add_argument("--demo-gpu-reset", type=float, default=None, metavar="SECONDS",
                    help="示範資料：開啟幾秒後模擬顯示卡重設（看暫停與重新連上的畫面）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"埠（預設 {DEFAULT_PORT}）")
    ap.add_argument("--mode", choices=("main", "mini"), default="main", help="開哪一種視窗")
    ap.add_argument("--hidden", action="store_true", help="只開系統匣")
    ap.add_argument("--activate", action="store_true", help="叫出已經在跑的那一份")
    ap.add_argument("--data", default="", help="設定、log 的資料夾（預設 data/；相對路徑以 HWGlance 資料夾為起點）")
    ap.add_argument("--ui", action="store_true", help=argparse.SUPPRESS)        # 視窗子程序（內部用）
    a = ap.parse_args(argv)
    if a.data:                          # paths.py 從環境變數讀；視窗子程序會繼承這個環境變數
        os.environ["HWGLANCE_DATA"] = str(paths.APP_ROOT / a.data.strip())
    if a.ui:
        from .ui import run as run_ui
        return run_ui(a.port, a.mode)
    if a.activate:
        from .instance import activate
        return 0 if activate(a.mode) else 1
    from .core import run
    return run(a.port, demo=a.demo, serve_only=a.serve, mode=a.mode, hidden=a.hidden, demo_gpu_reset=a.demo_gpu_reset)


if __name__ == "__main__":
    sys.exit(main())
