"""這台 Windows 的狀態：是不是系統管理員、PawnIO 裝了沒。只讀，不改任何東西。"""
from __future__ import annotations

import ctypes
import sys

PAWNIO_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO"


def is_admin() -> bool:
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def pawnio_version() -> str | None:
    """跟 LHM 的 PawnIo.cs 同一個判斷：解除安裝清單裡 PawnIO 的 DisplayVersion。沒裝回傳 None。"""
    if sys.platform != "win32":
        return None
    import winreg
    for view in (0, winreg.KEY_WOW64_64KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, PAWNIO_KEY, 0, winreg.KEY_READ | view) as k:
                value = winreg.QueryValueEx(k, "DisplayVersion")[0]
        except OSError:
            continue
        if value:
            return str(value)
    return None


def hints(kind: str, admin: bool, pawnio: str | None) -> list[str]:
    """畫面上方要提醒的事：為什麼有些溫度讀不到。"""
    if kind == "demo":
        return ["示範資料：數字是模擬的，不是這台電腦的讀數"]
    out = []
    if not admin:
        out.append("沒有以系統管理員執行：CPU、主機板、硬碟的溫度讀不到（用 run.bat 開會自動要權限）")
    if pawnio is None:
        out.append("還沒安裝 PawnIO 驅動：CPU 與主機板的溫度讀不到")
    return out
