"""專案裡的固定位置。測試用環境變數把資料夾換到暫存區，不碰真的。"""
from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
APP_ROOT = PACKAGE_DIR.parents[1]                  # HWGlance/


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name, "").strip()
    return Path(value) if value else default


def data_dir() -> Path:
    """執行時資料（log、探測結果、WebView 快取、執行中的埠）：`HWGLANCE_DATA` 優先，否則 HWGlance/data。"""
    return _env_path("HWGLANCE_DATA", APP_ROOT / "data")


def log_dir() -> Path:
    return data_dir() / "logs"


def probe_dir() -> Path:
    return data_dir() / "probe"


def instance_file() -> Path:
    """執行中的那一份寫下自己的 pid、埠、token；再開一次時靠它找到正在跑的那一份。"""
    return data_dir() / "instance.json"


def webview_dir() -> Path:
    return data_dir() / "webview"


def settings_file() -> Path:
    return data_dir() / "settings.json"


def ui_state_file() -> Path:
    """視窗程序自己記的東西（迷你視窗的位置）。"""
    return data_dir() / "ui-state.json"


def lhm_dir() -> Path:
    """LibreHardwareMonitorLib 與相依的 DLL（scripts/fetch_lhm.py 解出來）：`HWGLANCE_LHM` 優先。"""
    return _env_path("HWGLANCE_LHM", APP_ROOT / "vendor" / "lhm")


def frontend_dist() -> Path:
    return APP_ROOT / "frontend" / "dist"


def icon_file() -> Path:
    return APP_ROOT / "assets" / "app.ico"
