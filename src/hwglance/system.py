"""這台 Windows 的狀態：是不是系統管理員、PawnIO 裝了沒、CPU 快取多大。只讀，不改任何東西。"""
from __future__ import annotations

import ctypes
import sys
from collections.abc import Iterable

PAWNIO_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO"
RELATION_CACHE = 2                          # LOGICAL_PROCESSOR_RELATIONSHIP 的 RelationCache


class _CacheDescriptor(ctypes.Structure):
    _fields_ = [("Level", ctypes.c_ubyte), ("Associativity", ctypes.c_ubyte), ("LineSize", ctypes.c_ushort),
                ("Size", ctypes.c_uint32), ("Type", ctypes.c_int)]


class _ProcessorInfo(ctypes.Union):
    _fields_ = [("Flags", ctypes.c_ubyte), ("NodeNumber", ctypes.c_uint32), ("Cache", _CacheDescriptor),
                ("Reserved", ctypes.c_ulonglong * 2)]


class _LogicalProcessorInformation(ctypes.Structure):
    _fields_ = [("ProcessorMask", ctypes.c_size_t), ("Relationship", ctypes.c_int), ("Info", _ProcessorInfo)]


def cpu_caches() -> dict[int, int]:
    """每一層快取的總大小（位元組），例如 {1: 524288, 2: 4194304, 3: 33554432}。
    用 Windows 的 GetLogicalProcessorInformation（不用系統管理員）；SMBIOS 的數字有時不準（這台 L3 報 32.5 MB）。"""
    if sys.platform != "win32":
        return {}
    try:
        get = ctypes.windll.kernel32.GetLogicalProcessorInformation
        length = ctypes.c_uint32(0)
        get(None, ctypes.byref(length))
        count = length.value // ctypes.sizeof(_LogicalProcessorInformation)
        if count <= 0:
            return {}
        items = (_LogicalProcessorInformation * count)()
        if not get(items, ctypes.byref(length)):
            return {}
    except (AttributeError, OSError):
        return {}
    return cache_totals((i.Info.Cache.Level, i.Info.Cache.Size) for i in items if i.Relationship == RELATION_CACHE)


def cache_totals(caches: Iterable[tuple[int, int]]) -> dict[int, int]:
    """(層級, 大小) → 每層加總。每筆是一顆實體快取（L1 的指令、資料各算一筆）。"""
    totals: dict[int, int] = {}
    for lvl, size in caches:
        totals[lvl] = totals.get(lvl, 0) + size
    return dict(sorted(totals.items()))


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
