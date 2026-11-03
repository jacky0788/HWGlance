"""暫存資料夾裡被鎖住的檔（SPEC D33，第 10 段③）：掃使用者暫存與 Windows 暫存，只管放超過 1 天的檔，
列出刪不掉的檔被哪個程式鎖住；可以刪的一次刪掉，鎖住的「關掉那個程式」（走 processes.py 的關閉流程）或「下次開機刪除」。
**不做強制關控制代碼**（提案說明過：程式以為檔案還開著，可能當掉或寫壞資料）。

本體以系統管理員執行、而且會刪檔，所以：
- 只刪最近一次掃描列出來的檔（API 只收掃描編號，不收路徑）；刪之前再確認一次放超過 1 天。
- 掃描不跟進捷徑、連結、junction（重新剖析點一律跳過）。刪的時候先把檔案打開（不跟進重新剖析點），
  用打開的那個控制代碼查真正的路徑，確定還在暫存資料夾裡、也不是重新剖析點，才對那個控制代碼標記刪除——
  掃完到刪之間就算有人把某個資料夾換成指到別處的 junction，也刪不到暫存資料夾外面（C:\\Windows\\Temp 誰都能寫，特別要擋）。
- 「下次開機刪除」（MoveFileEx）只給使用者自己的暫存：它到開機時才照路徑刪，C:\\Windows\\Temp 擋不了那時候的 junction。要系統管理員。
- 誰鎖住：Windows 的重新啟動管理員（Restart Manager，安裝程式要你先關程式時用的就是它）。它直接給程式的啟動時間，
  跟 processes.py 認程式的方式一樣，所以鎖住檔案的程式記進 Offers，就能用同一套「關閉」（系統程式不給關）。
- 寫 data/actions.log 只記件數與大小，不記檔名（路徑裡有使用者名稱）。

`DemoTemp` 給示範資料用：檔案與程式都是編的，刪除、排程都只改示範的數字。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import stat
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from . import system

OLD_AFTER = 86400.0                       # 放超過 1 天才算
MAX_ENTRIES = 200_000                     # 掃太多就停（結果標「沒掃完」）
MAX_SECONDS = 20.0
MAX_LOOKUPS = 300                         # 最多幫幾個鎖住的檔查是誰鎖的
SAMPLES = 4                               # 每個程式列幾個檔名
REPARSE = 0x400                           # FILE_ATTRIBUTE_REPARSE_POINT

DELETE, FILE_READ_ATTRIBUTES = 0x00010000, 0x80
SHARE_ALL = 0x7
OPEN_EXISTING = 3
FLAG_OPEN_REPARSE_POINT = 0x00200000
FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_DISPOSITION_INFO = 4
MOVEFILE_DELAY_UNTIL_REBOOT = 0x4
ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION, ERROR_ACCESS_DENIED = 32, 33, 5
ERROR_MORE_DATA = 234
INVALID_HANDLE = ctypes.c_void_p(-1).value


@dataclass
class Root:
    path: str
    label: str
    boot_delete: bool                     # 能不能「下次開機刪除」（只有使用者自己的暫存）


@dataclass
class _Scan:
    id: str
    roots: list[Root]
    finals: list[str]                     # 每個資料夾真正的路徑（小寫、結尾有 \）
    deletable: list[tuple[int, str, int]] = field(default_factory=list)   # (第幾個資料夾, 路徑, 大小)
    locked: dict[str, dict] = field(default_factory=dict)                 # 群組鍵 → {info, files: [(root, path, size)]}
    dirs: list[tuple[int, str]] = field(default_factory=list)              # 舊的子資料夾（刪完檔後空了就刪）


# ---- Windows API ----

def _k32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.restype = wt.HANDLE
    k.CreateFileW.argtypes = (wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE)
    k.CloseHandle.argtypes = (wt.HANDLE,)
    k.GetFinalPathNameByHandleW.argtypes = (wt.HANDLE, wt.LPWSTR, wt.DWORD, wt.DWORD)
    k.GetFileAttributesW.argtypes = (wt.LPCWSTR,)
    k.SetFileInformationByHandle.argtypes = (wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD)
    k.MoveFileExW.argtypes = (wt.LPCWSTR, wt.LPCWSTR, wt.DWORD)
    k.GetTempPathW.argtypes = (wt.DWORD, wt.LPWSTR)
    return k


class _ByHandleInfo(ctypes.Structure):
    _fields_ = [("dwFileAttributes", wt.DWORD), ("ftCreationTime", wt.FILETIME), ("ftLastAccessTime", wt.FILETIME),
                ("ftLastWriteTime", wt.FILETIME), ("dwVolumeSerialNumber", wt.DWORD), ("nFileSizeHigh", wt.DWORD),
                ("nFileSizeLow", wt.DWORD), ("nNumberOfLinks", wt.DWORD), ("nFileIndexHigh", wt.DWORD),
                ("nFileIndexLow", wt.DWORD)]


def _open(path: str, directory: bool = False) -> tuple[int | None, int]:
    """打開（不跟進重新剖析點），回傳（控制代碼, 錯誤碼）。只要求刪除與讀屬性的權限，其他程式怎麼開都不擋它。"""
    k = _k32()
    flags = FLAG_OPEN_REPARSE_POINT | (FLAG_BACKUP_SEMANTICS if directory else 0)
    access = FILE_READ_ATTRIBUTES if directory else DELETE | FILE_READ_ATTRIBUTES
    h = k.CreateFileW(path, access, SHARE_ALL, None, OPEN_EXISTING, flags, None)
    if h in (None, INVALID_HANDLE):
        return None, ctypes.get_last_error()
    return h, 0


def _final(h) -> str | None:
    k = _k32()
    buf = ctypes.create_unicode_buffer(1024)
    n = k.GetFinalPathNameByHandleW(h, buf, len(buf), 0)
    if not n or n >= len(buf):
        return None
    return normalize(buf.value)


def normalize(path: str) -> str:
    """\\\\?\\C:\\Users\\X → c:\\users\\x（比較用）。網路路徑 \\\\?\\UNC\\ 也拿掉前綴。"""
    p = path
    if p.startswith("\\\\?\\UNC\\"):
        p = "\\\\" + p[8:]
    elif p.startswith("\\\\?\\"):
        p = p[4:]
    return os.path.normcase(os.path.normpath(p))


def inside(final: str, root_final: str) -> bool:
    """final 在 root_final 底下（不含 root 本身）。兩個都要是 normalize 過的。"""
    base = root_final.rstrip("\\") + "\\"
    return final.startswith(base) and len(final) > len(base)


def root_final(path: str) -> str | None:
    if sys.platform != "win32":
        return normalize(os.path.realpath(path))
    h, _ = _open(path, directory=True)
    if h is None:
        return None
    try:
        return _final(h)
    finally:
        _k32().CloseHandle(h)


def probe(path: str) -> str:
    """這個檔現在刪不刪得掉：ok／locked（別的程式開著、沒讓人刪）／denied（沒權限）／gone。不刪。"""
    h, err = _open(path)
    if h is None:
        if err in (ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION):
            return "locked"
        if err == ERROR_ACCESS_DENIED:
            return "denied"
        return "gone"
    _k32().CloseHandle(h)
    return "ok"


def delete_safely(path: str, root: str) -> str:
    """刪一個檔：先打開、用控制代碼查真的路徑還在 root 底下、不是重新剖析點，再對同一個控制代碼標記刪除。
    回傳 deleted／locked／denied／outside／gone／failed。"""
    h, err = _open(path)
    if h is None:
        return {ERROR_SHARING_VIOLATION: "locked", ERROR_LOCK_VIOLATION: "locked", ERROR_ACCESS_DENIED: "denied"}.get(err, "gone")
    k = _k32()
    try:
        final = _final(h)
        if final is None or not inside(final, root):
            return "outside"
        info = _ByHandleInfo()
        if not ctypes.windll.kernel32.GetFileInformationByHandle(ctypes.c_void_p(h), ctypes.byref(info)):
            return "failed"
        if info.dwFileAttributes & REPARSE:
            return "outside"
        flag = ctypes.c_ubyte(1)
        if not k.SetFileInformationByHandle(h, FILE_DISPOSITION_INFO, ctypes.byref(flag), 1):
            e = ctypes.get_last_error()
            return "denied" if e == ERROR_ACCESS_DENIED else "locked" if e == ERROR_SHARING_VIOLATION else "failed"
        return "deleted"
    finally:
        k.CloseHandle(h)


def schedule_boot_delete(path: str, root: str) -> bool:
    """下次開機刪除（要系統管理員）。排之前同樣確認真的路徑在 root 底下：檔案打得開就看檔案的，
    被鎖住打不開就看它所在資料夾的真路徑（資料夾打得開；中間有 junction 指到外面的話真路徑就不在 root 底下）。"""
    h, _ = _open(path)
    if h is not None:
        try:
            final = _final(h)
        finally:
            _k32().CloseHandle(h)
        if final is None or not inside(final, root):
            return False
    else:
        parent = root_final(os.path.dirname(path))
        if parent is None or not (parent == root or inside(parent, root)):
            return False
        attrs = _k32().GetFileAttributesW(path)
        if attrs == 0xFFFFFFFF or attrs & REPARSE:
            return False
    return bool(_k32().MoveFileExW(path, None, MOVEFILE_DELAY_UNTIL_REBOOT))


class _RmUniqueProcess(ctypes.Structure):
    _fields_ = [("dwProcessId", wt.DWORD), ("ProcessStartTime", wt.FILETIME)]


class _RmProcessInfo(ctypes.Structure):
    _fields_ = [("Process", _RmUniqueProcess), ("strAppName", wt.WCHAR * 256), ("strServiceShortName", wt.WCHAR * 64),
                ("ApplicationType", ctypes.c_int), ("AppStatus", wt.ULONG), ("TSSessionId", wt.DWORD),
                ("bRestartable", wt.BOOL)]


def lockers(path: str) -> list[tuple[int, str, str]]:
    """誰開著這個檔：[(pid, 啟動時間, 名稱)]（重新啟動管理員）。查不到回傳空的。"""
    if sys.platform != "win32":
        return []
    rm = ctypes.WinDLL("rstrtmgr")
    session = wt.DWORD()
    key = ctypes.create_unicode_buffer(33)
    if rm.RmStartSession(ctypes.byref(session), 0, key) != 0:
        return []
    try:
        files = (wt.LPCWSTR * 1)(path)
        if rm.RmRegisterResources(session, 1, files, 0, None, 0, None) != 0:
            return []
        need, count, reasons = wt.UINT(0), wt.UINT(0), wt.DWORD(0)
        status = rm.RmGetList(session, ctypes.byref(need), ctypes.byref(count), None, ctypes.byref(reasons))
        if status not in (0, ERROR_MORE_DATA) or need.value == 0:
            return []
        infos = (_RmProcessInfo * need.value)()
        count = wt.UINT(need.value)
        if rm.RmGetList(session, ctypes.byref(need), ctypes.byref(count), infos, ctypes.byref(reasons)) != 0:
            return []
        out = []
        for info in infos[:count.value]:
            t = info.Process.ProcessStartTime
            out.append((info.Process.dwProcessId, str((t.dwHighDateTime << 32) | t.dwLowDateTime), info.strAppName))
        return out
    finally:
        rm.RmEndSession(session)


def default_roots() -> list[Root]:
    roots = []
    if sys.platform == "win32":
        buf = ctypes.create_unicode_buffer(1024)
        n = _k32().GetTempPathW(len(buf), buf)
        if n:
            roots.append(Root(os.path.normpath(buf.value), "你的暫存資料夾", True))
    windows = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Temp")
    roots.append(Root(windows, "Windows 暫存資料夾", False))
    return roots


# ---- 掃描與動作 ----

def _is_old(st: os.stat_result, now: float) -> bool:
    born = getattr(st, "st_birthtime", st.st_ctime)
    return max(st.st_mtime, born) < now - OLD_AFTER


def _skip(st: os.stat_result) -> bool:
    return bool(getattr(st, "st_file_attributes", 0) & REPARSE) or stat.S_ISLNK(st.st_mode)


class TempCleaner:
    def __init__(self, processes=None, log=None, roots: Callable[[], list[Root]] = default_roots,
                 probe_fn: Callable[[str], str] = probe, lockers_fn: Callable[[str], list] = lockers,
                 delete_fn: Callable[[str, str], str] = delete_safely,
                 boot_fn: Callable[[str, str], bool] = schedule_boot_delete,
                 final_fn: Callable[[str], str | None] = root_final,
                 table: Callable[[], dict[int, tuple[str, int]]] = system.process_table,
                 admin: Callable[[], bool] = system.is_admin, clock: Callable[[], float] = time.time) -> None:
        self.processes, self.log = processes, log
        self._roots, self._probe, self._lockers, self._delete, self._boot = roots, probe_fn, lockers_fn, delete_fn, boot_fn
        self._final, self._table, self._admin, self._clock = final_fn, table, admin, clock
        self._lock = threading.Lock()
        self._scan: _Scan | None = None

    def scan(self) -> dict:
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("正在掃描或刪除，等一下")
        try:
            return self._do_scan()
        finally:
            self._lock.release()

    def _do_scan(self) -> dict:
        from .processes import protected_reason
        t0, now = time.perf_counter(), self._clock()
        roots = self._roots()
        finals = [self._final(r.path) for r in roots]
        scan = _Scan(uuid.uuid4().hex[:12], roots, [f or "" for f in finals])
        summary = [{"label": r.label, "path": r.path, "files": 0, "bytes": 0, "old": 0, "oldBytes": 0,
                    "readable": f is not None} for r, f in zip(roots, finals)]
        denied = {"files": 0, "bytes": 0}
        locked_files: list[tuple[int, str, int]] = []
        entries, truncated = 0, False
        for i, root in enumerate(roots):
            if not finals[i]:
                continue
            stack = [root.path]
            while stack and not truncated:
                folder = stack.pop()
                try:
                    with os.scandir(folder) as it:
                        children = list(it)
                except OSError:
                    continue
                for entry in children:
                    entries += 1
                    if entries > MAX_ENTRIES or time.perf_counter() - t0 > MAX_SECONDS:
                        truncated = True
                        break
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if _skip(st):
                        continue                         # 捷徑、連結、junction：不跟進、不刪
                    if stat.S_ISDIR(st.st_mode):
                        stack.append(entry.path)
                        if _is_old(st, now):
                            scan.dirs.append((i, entry.path))
                        continue
                    s = summary[i]
                    s["files"] += 1
                    s["bytes"] += st.st_size
                    if not _is_old(st, now):
                        continue
                    s["old"] += 1
                    s["oldBytes"] += st.st_size
                    state = self._probe(entry.path)
                    if state == "ok":
                        scan.deletable.append((i, entry.path, st.st_size))
                    elif state == "locked":
                        locked_files.append((i, entry.path, st.st_size))
                    elif state == "denied":
                        denied["files"] += 1
                        denied["bytes"] += st.st_size
        table = self._table() if locked_files else {}
        me = getattr(self.processes, "me", os.getpid())    # 跟「關閉」用同一個「自己」，兩邊的判斷才一致
        for n, (i, path, size) in enumerate(locked_files):
            found = self._lockers(path) if n < MAX_LOOKUPS else []
            pid, created, rm_name = found[0] if found else (0, "", "")    # 好幾個程式一起開著的少見，歸給第一個
            key = f"{pid}:{created}" if pid else "unknown"
            g = scan.locked.get(key)
            if g is None:
                reason = protected_reason(pid, table, me) if pid else None
                if not pid:
                    name = "不知道是哪個程式"
                elif pid in table:
                    name = system.process_name(pid, table, me)
                else:
                    name = rm_name or f"程式 {pid}"
                process = {"pid": pid, "created": created} if pid and not reason else None
                g = scan.locked[key] = {"info": {"key": key, "name": name, "protected": reason, "process": process},
                                        "files": []}
                if process and self.processes is not None:
                    self.processes.offers.offer(pid, created)     # 「關掉這個程式」走 processes.py 同一套
            g["files"].append((i, path, size))
        self._scan = scan
        return self._report(scan, summary, denied, truncated, time.perf_counter() - t0)

    def _report(self, scan: _Scan, summary: list[dict], denied: dict, truncated: bool, took: float) -> dict:
        groups = []
        for g in scan.locked.values():
            files = g["files"]
            groups.append({**g["info"], "files": len(files), "bytes": sum(f[2] for f in files),
                           "samples": [os.path.relpath(p, scan.roots[i].path) for i, p, _ in files[:SAMPLES]],
                           "bootDelete": any(scan.roots[i].boot_delete for i, _, _ in files)})
        groups.sort(key=lambda g: (g["key"] == "unknown", -g["bytes"]))
        return {"id": scan.id, "time": self._clock(), "took": round(took, 2), "truncated": truncated,
                "roots": summary, "deletable": {"files": len(scan.deletable), "bytes": sum(f[2] for f in scan.deletable)},
                "locked": groups, "denied": denied, "admin": self._admin()}

    def _current(self, scan_id: str) -> _Scan:
        if self._scan is None or self._scan.id != scan_id:
            raise LookupError("這次的掃描結果過期了，重新掃描再試")
        return self._scan

    def clean(self, scan_id: str) -> dict:
        """刪掉掃描時可以刪的檔；刪完把空了的舊子資料夾也刪掉。做完這份掃描就作廢（要重新掃描）。"""
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("正在掃描或刪除，等一下")
        try:
            scan = self._current(scan_id)
            now = self._clock()
            counts: dict[str, int] = {}
            freed = 0
            for i, path, size in scan.deletable:
                try:
                    if not _is_old(os.stat(path, follow_symlinks=False), now):
                        counts["skipped"] = counts.get("skipped", 0) + 1     # 掃完之後又被改過：不刪
                        continue
                except OSError:
                    counts["gone"] = counts.get("gone", 0) + 1
                    continue
                result = self._delete(path, scan.finals[i])
                counts[result] = counts.get(result, 0) + 1
                if result == "deleted":
                    freed += size
            dirs = 0
            for i, folder in sorted(scan.dirs, key=lambda d: -len(d[1])):    # 深的先
                try:
                    st = os.stat(folder, follow_symlinks=False)
                    real = self._final(folder)                               # 真路徑（短檔名、junction 都解開）
                    if _skip(st) or real is None or not inside(real, scan.finals[i]) or os.listdir(folder):
                        continue
                    os.rmdir(folder)                                          # 只刪空的
                    dirs += 1
                except OSError:
                    pass
            self._scan = None
            result = {"deleted": counts.get("deleted", 0), "bytes": freed, "dirs": dirs,
                      "failed": sum(v for k, v in counts.items() if k not in ("deleted", "gone")),
                      "locked": counts.get("locked", 0)}
            if self.log is not None:
                self.log.write(action="clean-temp", **result)
            return result
        finally:
            self._lock.release()

    def boot_delete(self, scan_id: str, key: str) -> dict:
        """被鎖住的那組檔排到下次開機刪除（只有使用者自己的暫存、要系統管理員）。"""
        if not self._admin():
            raise PermissionError("要用系統管理員執行 HWGlance 才能排下次開機刪除")
        with self._lock:
            scan = self._current(scan_id)
            group = scan.locked.get(key)
            if group is None:
                raise LookupError("找不到這組檔，重新掃描再試")
            done = skipped = 0
            for i, path, _ in group["files"]:
                if not scan.roots[i].boot_delete:
                    skipped += 1
                    continue
                if self._boot(path, scan.finals[i]):
                    done += 1
                else:
                    skipped += 1
            result = {"scheduled": done, "skipped": skipped}
            if self.log is not None:
                self.log.write(action="boot-delete-temp", name=group["info"]["name"], **result)
            return result


class DemoTemp:
    """示範資料：檔名、程式都是編的；刪除、排程只改示範的數字（不碰這台的檔案）。鎖住檔案的程式登記到 DemoProcesses。"""

    MB = 2**20
    LOCKERS = [  # (pid, 名稱, 檔數, MB, 範例檔名, 系統程式？)
        (30548, "ChatGPT", 3, 48, ["ChatGPT\\cache\\data_1", "ChatGPT\\cache\\index"], False),
        (8812, "Discord", 2, 12, ["discord_crash_7f2a.dmp"], False),
        (5120, "svchost（Windows 服務）", 4, 6, ["MpCmdRun.log", "~DF8C21.tmp"], True),
    ]

    def __init__(self, processes=None, clock: Callable[[], float] = time.time) -> None:
        self.processes, self._clock = processes, clock
        self._cleaned = False
        self._booted: set[str] = set()
        self._id = ""

    def scan(self) -> dict:
        self._id = uuid.uuid4().hex[:12]
        groups = []
        for pid, name, files, mb, samples, protected in self.LOCKERS:
            key = f"{pid}:demo-{pid}"
            if key in self._booted or self._gone(pid):
                continue
            proc = None if protected else {"pid": pid, "created": f"demo-{pid}"}
            if proc and self.processes is not None:
                self.processes.register(pid, name)
                self.processes.offers.offer(pid, proc["created"])
            groups.append({"key": key, "name": name, "protected": "這是 Windows 服務（背景程式），不從這裡關" if protected else None,
                           "process": proc, "files": files, "bytes": mb * self.MB, "samples": samples,
                           "bootDelete": True})
        if "unknown" not in self._booted:
            groups.append({"key": "unknown", "name": "不知道是哪個程式", "protected": None, "process": None, "files": 1,
                           "bytes": 3 * self.MB, "samples": ["setup_4b1e\\setup.log"], "bootDelete": True})
        deletable = (0, 0) if self._cleaned else (812, 1240 * self.MB)
        return {"id": self._id, "time": self._clock(), "took": 1.42, "truncated": False,
                "roots": [{"label": "你的暫存資料夾", "path": "C:\\Users\\示範\\AppData\\Local\\Temp", "files": 1034,
                           "bytes": 1810 * self.MB, "old": 830, "oldBytes": 1320 * self.MB, "readable": True},
                          {"label": "Windows 暫存資料夾", "path": "C:\\Windows\\Temp", "files": 96, "bytes": 140 * self.MB,
                           "old": 12, "oldBytes": 9 * self.MB, "readable": True}],
                "deletable": {"files": deletable[0], "bytes": deletable[1]}, "locked": groups,
                "denied": {"files": 5, "bytes": 2 * self.MB}, "admin": True}

    def _gone(self, pid: int) -> bool:
        """示範的「關掉這個程式」之後，那組檔就不再被鎖（DemoProcesses.closed）。"""
        return self.processes is not None and pid in getattr(self.processes, "closed", set())

    def clean(self, scan_id: str) -> dict:
        if scan_id != self._id:
            raise LookupError("這次的掃描結果過期了，重新掃描再試")
        self._cleaned, self._id = True, ""
        return {"deleted": 812, "bytes": 1240 * self.MB, "dirs": 37, "failed": 0, "locked": 0}

    def boot_delete(self, scan_id: str, key: str) -> dict:
        if scan_id != self._id:
            raise LookupError("這次的掃描結果過期了，重新掃描再試")
        self._booted.add(key)
        files = next((g[2] for g in self.LOCKERS if f"{g[0]}:demo-{g[0]}" == key), 1)
        return {"scheduled": files, "skipped": 0}
