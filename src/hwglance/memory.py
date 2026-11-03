"""記憶體的組成與「釋放記憶體」（SPEC D32，第 10 段②；使用者說的「記憶體壓縮」照提案解讀成 Mem Reduct 那種釋放）。

組成（`read_composition`，只讀、不用系統管理員）：GlobalMemoryStatusEx 的總量＋效能計數器——
    程式在用＝總量－可用－壓縮區－待寫回；壓縮區＝Memory Compression 程式的工作集（Windows 內建記憶體壓縮放壓縮資料的地方，
    沒有這個程式＝壓縮沒開）；待寫回＝Modified Page List；待命快取＝三個 Standby Cache 加總（隨時可以挪用，算在「可用」裡）；
    完全空著＝Free & Zero Page List。2026-11-03 這台：64 GB、可用 27.9（待命 24.5＋空著 3.4）、壓縮區 2.9、待寫回 0.6。

釋放（`MemoryTool.release`，要 token、20 秒最多一次、寫 data/actions.log）：
    1. 收回各程式暫時沒在用的記憶體（EmptyWorkingSet）：跳過 Windows 核心、壓縮區、**目前在前景的程式**（例如遊戲），
       沒有系統管理員時只收得到自己帳號的程式。
    2. 清掉待命快取（NtSetSystemInformation 的 MemoryPurgeStandbyList）：要系統管理員（SeProfileSingleProcessPrivilege），沒有就跳過並說明。
    不清「待寫回」（要寫硬碟）、不動 Windows 的壓縮設定（改設定要系統管理員、重開機才生效，提案說只顯示）。
    實話：多半是讓「可用」變多；被收回的東西程式一用到就再載回來，遊戲中按可能短暫卡頓（介面確認時有寫）。

`DemoMemory` 給示範資料用：數字是編的，釋放只改示範的數字，不碰這台的記憶體。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import math
import os
import sys
import threading
import time
from collections.abc import Callable

from . import system

COOLDOWN = 20.0                         # 兩次釋放至少隔幾秒
SETTLE = 1.0                            # 釋放完等一下再量
SKIP_NAMES = frozenset({"system", "idle", "registry", "secure system", "memory compression", "vmmem", "vmmemws"})
COUNTERS = {
    "available": r"\Memory\Available Bytes",
    "standby_normal": r"\Memory\Standby Cache Normal Priority Bytes",
    "standby_reserve": r"\Memory\Standby Cache Reserve Bytes",
    "standby_core": r"\Memory\Standby Cache Core Bytes",
    "modified": r"\Memory\Modified Page List Bytes",
    "free": r"\Memory\Free & Zero Page List Bytes",
    "compressed": r"\Process(Memory Compression)\Working Set",
}
SEGMENTS = (                            # (key, 名稱, 說明)；順序就是長條由左到右
    # 不叫「使用中」：卡片上面 LHM 的「已用」＝總量－可用（含壓縮區、待寫回），兩個名字太像會以為數字對不上
    ("used", "程式在用", "程式正在用的（上面的「已用」還多算了壓縮區與待寫回）"),
    ("compressed", "已壓縮", "Windows 把暫時不用的記憶體壓縮起來放在這裡，比寫到硬碟快"),
    ("modified", "待寫回", "改過、還沒寫回硬碟的，寫完就變成待命快取"),
    ("standby", "待命快取", "最近讀過的檔案與程式，留著下次用比較快；程式要用時馬上可以挪用，所以也算「可用」"),
    ("free", "完全空著", "沒有任何東西"),
)
PDH_FMT_LARGE = 0x00000400
SYSTEM_MEMORY_LIST_INFORMATION = 80
MEMORY_PURGE_STANDBY_LIST = 4
SE_PRIVILEGE_ENABLED = 0x2
TOKEN_ADJUST_PRIVILEGES, TOKEN_QUERY = 0x20, 0x8
PROCESS_SET_QUOTA = 0x0100
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def compose(total: int, values: dict[str, int], compression_on: bool) -> dict:
    """計數器 → 給介面的組成（純函式）。各段加起來等於總量（四捨五入的誤差算進「使用中」）。"""
    standby = sum(values.get(k, 0) for k in ("standby_normal", "standby_reserve", "standby_core"))
    free = values.get("free", 0)
    # 「可用」本來就是待命＋空著；計數器各讀一次、時間差一點點，直接加起來各段才會剛好等於總量（2026-11-03 實測差 8 KB）
    available = standby + free if "free" in values and "standby_normal" in values else values.get("available", 0)
    compressed = values.get("compressed", 0) if compression_on else 0
    modified = values.get("modified", 0)
    used = max(0, total - available - compressed - modified)
    parts = {"used": used, "compressed": compressed, "modified": modified, "standby": standby, "free": free}
    return {"total": total, "available": available,
            "segments": [{"key": k, "label": label, "note": note, "bytes": int(parts[k])} for k, label, note in SEGMENTS],
            "compression": {"enabled": compression_on, "bytes": int(compressed)}}


# ---- 讀數字 ----

class _MemStatus(ctypes.Structure):
    _fields_ = [("dwLength", wt.DWORD), ("dwMemoryLoad", wt.DWORD), ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


class _PdhValue(ctypes.Structure):
    _fields_ = [("CStatus", wt.DWORD), ("pad", wt.DWORD), ("largeValue", ctypes.c_longlong)]


def total_physical() -> int:
    if sys.platform != "win32":
        return 0
    m = _MemStatus()
    m.dwLength = ctypes.sizeof(m)
    return int(m.ullTotalPhys) if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)) else 0


class Counters:
    """一直開著的 PDH 查詢（一次約 30 毫秒）。讀不到的計數器就不在結果裡。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._query = None
        self._handles: dict[str, ctypes.c_void_p] = {}

    def _open(self) -> None:
        pdh = ctypes.windll.pdh
        q = ctypes.c_void_p()
        if pdh.PdhOpenQueryW(None, None, ctypes.byref(q)) != 0:
            raise OSError("PdhOpenQuery 失敗")
        for key, path in COUNTERS.items():
            h = ctypes.c_void_p()
            if pdh.PdhAddEnglishCounterW(q, path, None, ctypes.byref(h)) == 0:
                self._handles[key] = h
        self._query = q

    def read(self) -> dict[str, int]:
        if sys.platform != "win32":
            return {}
        pdh = ctypes.windll.pdh
        with self._lock:
            if self._query is None:
                self._open()
            if pdh.PdhCollectQueryData(self._query) != 0:
                return {}
            out = {}
            for key, h in self._handles.items():
                v = _PdhValue()
                if pdh.PdhGetFormattedCounterValue(h, PDH_FMT_LARGE, None, ctypes.byref(v)) == 0:
                    out[key] = max(0, int(v.largeValue))
            return out


def compression_enabled(table: dict[int, tuple[str, int]]) -> bool:
    """Windows 記憶體壓縮有沒有開：開著的話會有一個叫 Memory Compression 的程式（工作管理員看得到）。"""
    return any(name.lower() == "memory compression" for name, _ in table.values())


def read_composition(counters: Counters) -> dict:
    return compose(total_physical(), counters.read(), compression_enabled(system.process_table()))


# ---- 釋放 ----

def foreground_pid() -> int | None:
    if sys.platform != "win32":
        return None
    u = ctypes.windll.user32
    hwnd = u.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wt.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value or None


def trim_targets(table: dict[int, tuple[str, int]], skip: set[int]) -> list[int]:
    """要收回哪些程式的記憶體（純函式）：跳過核心、壓縮區、skip（前景程式）。"""
    out = []
    for pid, (name, _) in table.items():
        stem = name.lower().removesuffix(".exe")
        if pid in (0, 4) or pid in skip or stem in SKIP_NAMES:
            continue
        out.append(pid)
    return out


def empty_working_set(pid: int) -> bool:
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wt.HANDLE
    k.OpenProcess.argtypes = (wt.DWORD, wt.BOOL, wt.DWORD)
    k.K32EmptyWorkingSet.argtypes = (wt.HANDLE,)
    k.CloseHandle.argtypes = (wt.HANDLE,)
    h = k.OpenProcess(PROCESS_SET_QUOTA | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    try:
        return bool(k.K32EmptyWorkingSet(h))
    finally:
        k.CloseHandle(h)


class _Luid(ctypes.Structure):
    _fields_ = [("LowPart", wt.DWORD), ("HighPart", wt.LONG)]


class _TokenPrivileges(ctypes.Structure):
    _fields_ = [("PrivilegeCount", wt.DWORD), ("Luid", _Luid), ("Attributes", wt.DWORD)]


def _enable_privilege(name: str) -> bool:
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k = ctypes.windll.kernel32
    token = wt.HANDLE()
    if not adv.OpenProcessToken(ctypes.c_void_p(k.GetCurrentProcess()), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
                                ctypes.byref(token)):
        return False
    try:
        tp = _TokenPrivileges()
        if not adv.LookupPrivilegeValueW(None, name, ctypes.byref(tp.Luid)):
            return False
        tp.PrivilegeCount, tp.Attributes = 1, SE_PRIVILEGE_ENABLED
        ctypes.set_last_error(0)
        ok = adv.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None)
        return bool(ok) and ctypes.get_last_error() == 0            # 1300＝這個帳號沒有這個權限
    finally:
        k.CloseHandle(token)


def purge_standby() -> str:
    """清待命快取。回傳 purged／needs_admin／failed。"""
    if not system.is_admin() or not _enable_privilege("SeProfileSingleProcessPrivilege"):
        return "needs_admin"
    cmd = ctypes.c_int(MEMORY_PURGE_STANDBY_LIST)
    status = ctypes.windll.ntdll.NtSetSystemInformation(SYSTEM_MEMORY_LIST_INFORMATION, ctypes.byref(cmd),
                                                        ctypes.sizeof(cmd))
    return "purged" if status == 0 else "failed"


def free_of(c: dict) -> int:
    return next((s["bytes"] for s in c["segments"] if s["key"] == "free"), 0)


def release_result(before: dict, after: dict, trimmed: int, tried: int, skipped_fg: bool, standby: str) -> dict:
    """釋放的結果。兩個數字分開講才老實：收回程式的記憶體會讓「可用」變多；清待命快取只是把待命變成「完全空著」，
    「可用」不會變（待命本來就算可用）。"""
    return {"before": before["available"], "after": after["available"],
            "freed": max(0, after["available"] - before["available"]),
            "freeBefore": free_of(before), "freeAfter": free_of(after),
            "trimmed": trimmed, "tried": tried, "skippedForeground": skipped_fg, "standby": standby, "time": time.time()}


class MemoryTool:
    """記憶體卡片用：status() 只讀；release() 收回各程式的記憶體＋清待命快取。各個 Windows 呼叫都可以換成假的（測試用）。"""

    def __init__(self, log=None, read: Callable[[], dict] | None = None,
                 table: Callable[[], dict[int, tuple[str, int]]] = system.process_table,
                 trim: Callable[[int], bool] = empty_working_set, purge: Callable[[], str] = purge_standby,
                 foreground: Callable[[], int | None] = foreground_pid, admin: Callable[[], bool] = system.is_admin,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        counters = Counters()
        self._read = read or (lambda: read_composition(counters))
        self._table, self._trim, self._purge = table, trim, purge
        self._foreground, self._admin, self._clock, self._sleep = foreground, admin, clock, sleep
        self.log = log
        self._lock = threading.Lock()
        self._last: tuple[float, dict] | None = None

    def status(self) -> dict:
        out = self._read()
        out["canPurge"] = self._admin()
        out["last"] = self._last[1] if self._last else None
        return out

    def wait_seconds(self) -> float:
        if self._last is None:
            return 0.0
        return max(0.0, COOLDOWN - (self._clock() - self._last[0]))

    def release(self) -> dict:
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("正在釋放，等一下")
        try:
            wait = self.wait_seconds()
            if wait > 0:
                raise RuntimeError(f"剛釋放過，{math.ceil(wait)} 秒後再試")
            before = self._read()
            fg = self._foreground()
            targets = trim_targets(self._table(), {p for p in (fg,) if p} - {os.getpid()})
            trimmed = sum(1 for pid in targets if self._trim(pid))
            standby = self._purge()
            self._sleep(SETTLE)
            after = self._read()
            result = release_result(before, after, trimmed, len(targets), fg is not None, standby)
            self._last = (self._clock(), result)
            if self.log is not None:
                self.log.write(action="release-memory", trimmed=trimmed, tried=len(targets), standby=standby,
                               before=before["available"], after=after["available"])
            return result
        finally:
            self._lock.release()


class DemoMemory(MemoryTool):
    """示範資料：64 GB，數字慢慢飄；釋放後「可用」變多，之後一分鐘內慢慢回去。不碰這台的記憶體。"""

    GB = 2**30

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._boost_at: float | None = None
        super().__init__(read=self._demo_read, table=lambda: {}, trim=lambda pid: True, purge=lambda: "purged",
                         foreground=lambda: None, admin=lambda: True, clock=clock, sleep=lambda s: None)

    def _demo_read(self) -> dict:
        t = self._clock()
        boost = 0.0
        if self._boost_at is not None:
            boost = max(0.0, 1 - (t - self._boost_at) / 60)          # 釋放後一分鐘回到原樣
        g = self.GB
        total, compressed, modified = 64 * g, 2.9 * g, 0.6 * g
        used = (33.5 - 6 * boost + 0.5 * math.sin(t / 17)) * g          # 收回程式的記憶體：可用多 6 GB
        free = (3.4 + 24 * boost) * g                                     # 清待命快取：待命變成完全空著
        available = total - used - compressed - modified
        values = {"available": available, "standby_normal": available - free, "free": free,
                  "compressed": compressed, "modified": modified}
        return compose(int(total), {k: int(v) for k, v in values.items()}, True)

    def release(self) -> dict:
        before = self._demo_read()
        super().release()                                              # 冷卻、鎖照真的走；數字下面換成示範的
        self._boost_at = self._clock()
        self._last = (self._last[0], release_result(before, self._demo_read(), 186, 192, False, "purged"))
        return self._last[1]
