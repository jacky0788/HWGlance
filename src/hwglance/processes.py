"""結束別的程式（SPEC D31）：「顯存被誰佔用」排行旁的「關閉」。本體以系統管理員執行，什麼程式都關得掉，所以守得很嚴：

- 只收這個程式自己列出來的程式（`Offers`：pid＋啟動時間，10 分鐘內有效），不收任意 pid、不執行任何指令。
- 動手前再比一次啟動時間：pid 會被 Windows 重複使用，對不上就不動（`created_at`，FILETIME 轉成字串，JavaScript 的數字放不下）。
- 系統程式不給關（`protected_reason`）：Windows 核心、服務（工作階段 0）、Windows 標記為關鍵的程式、`PROTECTED` 那些、
  HWGlance 自己（含它開的視窗）與啟動它的程式。
- 先「好好關」：對它的主視窗送 WM_CLOSE（跟 taskkill 不加 /F 一樣），程式可以先問要不要存檔；等幾秒還在，
  由使用者決定要不要「強制結束」（TerminateProcess，跟 taskkill /F 一樣，不會存檔）。
- 沒有自己視窗的程式（瀏覽器、WebView2 放顯存的子程式）往上找有視窗的父程式，關它；確認時會寫清楚關的是哪個。
- 每次動作寫進 `data/actions.log`（一行一筆 JSON）。

`WindowsProcesses` 是真的；`DemoProcesses` 給示範資料用：示範的 pid 是編的，拿去關會關到這台不相干的程式，所以一律假裝。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import re
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from . import system

OFFER_TTL = 600.0                          # 列出來之後 10 分鐘內可以關
GENTLE_WAIT = 5.0                          # 送了 WM_CLOSE 等幾秒看它關了沒
KILL_WAIT = 3.0
LOG_LIMIT = 1024 * 1024                    # actions.log 超過 1 MB 就換一個
MAX_HOPS = 6                               # 往上找有視窗的父程式最多幾層

# 關掉畫面或系統會出問題的程式（執行檔名稱，小寫、不含 .exe）
PROTECTED = frozenset({
    "system", "idle", "registry", "secure system", "memory compression", "smss", "csrss", "wininit", "winlogon",
    "services", "lsass", "lsaiso", "svchost", "fontdrvhost", "dwm", "explorer", "sihost", "ctfmon", "taskhostw",
    "startmenuexperiencehost", "shellexperiencehost", "searchhost", "textinputhost", "runtimebroker",
    "applicationframehost", "lockapp", "logonui", "userinit", "audiodg", "spoolsv", "conhost",
})

PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0
WM_CLOSE = 0x0010
GW_OWNER = 4


class ProcessError(Exception):
    """不能關（原因給使用者看）。"""


# ---- Windows API ----

def _k32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wt.HANDLE
    k.OpenProcess.argtypes = (wt.DWORD, wt.BOOL, wt.DWORD)
    k.CloseHandle.argtypes = (wt.HANDLE,)
    k.GetProcessTimes.argtypes = (wt.HANDLE,) + (ctypes.POINTER(wt.FILETIME),) * 4
    k.TerminateProcess.argtypes = (wt.HANDLE, wt.UINT)
    k.WaitForSingleObject.argtypes = (wt.HANDLE, wt.DWORD)
    k.WaitForSingleObject.restype = wt.DWORD
    k.ProcessIdToSessionId.argtypes = (wt.DWORD, ctypes.POINTER(wt.DWORD))
    k.IsProcessCritical.argtypes = (wt.HANDLE, ctypes.POINTER(wt.BOOL))
    return k


def _u32():
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.GetWindowThreadProcessId.argtypes = (wt.HWND, ctypes.POINTER(wt.DWORD))
    u.IsWindowVisible.argtypes = (wt.HWND,)
    u.GetWindow.argtypes = (wt.HWND, wt.UINT)
    u.GetWindow.restype = wt.HWND
    u.PostMessageW.argtypes = (wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
    return u


_ENUM_PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def created_at(pid: int) -> str | None:
    """程式的啟動時間（FILETIME，100 奈秒為單位的整數，轉成字串）。程式不在或打不開回傳 None。"""
    if sys.platform != "win32" or pid <= 0:
        return None
    k = _k32()
    h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        times = [wt.FILETIME() for _ in range(4)]
        if not k.GetProcessTimes(h, *(ctypes.byref(t) for t in times)):
            return None
        c = times[0]
        return str((c.dwHighDateTime << 32) | c.dwLowDateTime)
    finally:
        k.CloseHandle(h)


def session_of(pid: int) -> int | None:
    if sys.platform != "win32":
        return None
    sid = wt.DWORD()
    return sid.value if _k32().ProcessIdToSessionId(pid, ctypes.byref(sid)) else None


def is_critical(pid: int) -> bool:
    """Windows 標記為關鍵的程式（關掉會藍屏）。打不開就當成不是（其他條件會再擋）。"""
    if sys.platform != "win32":
        return False
    k = _k32()
    h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    try:
        flag = wt.BOOL()
        return bool(k.IsProcessCritical(h, ctypes.byref(flag)) and flag.value)
    finally:
        k.CloseHandle(h)


def windows_of(pid: int) -> list[int]:
    """這個程式看得到的主視窗（沒有擁有者的頂層視窗）。"""
    if sys.platform != "win32":
        return []
    u = _u32()
    found: list[int] = []

    def cb(hwnd, _):
        owner = wt.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and u.IsWindowVisible(hwnd) and not u.GetWindow(hwnd, GW_OWNER):
            found.append(hwnd)
        return True

    u.EnumWindows(_ENUM_PROC(cb), 0)
    return found


def _wait_exit(pid: int, seconds: float) -> bool:
    """等程式結束；True＝結束了（或本來就不在）。"""
    k = _k32()
    h = k.OpenProcess(SYNCHRONIZE, False, pid)
    if not h:
        return True
    try:
        return k.WaitForSingleObject(h, int(seconds * 1000)) == WAIT_OBJECT_0
    finally:
        k.CloseHandle(h)


def _terminate(pid: int) -> bool:
    k = _k32()
    h = k.OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE, False, pid)
    if not h:
        return False
    try:
        if not k.TerminateProcess(h, 1):
            return False
        return k.WaitForSingleObject(h, int(KILL_WAIT * 1000)) == WAIT_OBJECT_0
    finally:
        k.CloseHandle(h)


def _post_close(hwnds: list[int]) -> None:
    u = _u32()
    for hwnd in hwnds:
        u.PostMessageW(hwnd, WM_CLOSE, 0, 0)


# ---- 規則（純函式，測試用假的程式清單） ----

def exe_stem(name: str) -> str:
    return re.sub(r"\.exe$", "", re.split(r"[\\/]", name or "")[-1], flags=re.I).lower()


def protected_reason(pid: int, table: dict[int, tuple[str, int]], me: int,
                     session: Callable[[int], int | None] = session_of,
                     critical: Callable[[int], bool] = is_critical) -> str | None:
    """不能關的原因；None＝可以關。"""
    if pid in (0, 4):
        return "這是 Windows 核心"
    if pid == me or me in system.ancestors(pid, table):
        return "這是 HWGlance 自己（或它開的視窗）"
    if pid in system.ancestors(me, table):
        return "這是啟動 HWGlance 的程式"
    stem = exe_stem(table.get(pid, ("", 0))[0])
    if stem in PROTECTED:
        return "這是 Windows 的一部分，關掉畫面或系統會出問題"
    if session(pid) == 0:
        return "這是 Windows 服務（背景程式），不從這裡關"
    if critical(pid):
        return "Windows 把它標記為關鍵程式，關掉會當機"
    return None


def pick_target(pid: int, table: dict[int, tuple[str, int]], has_windows: Callable[[int], bool]) -> int:
    """要關哪個程式：自己有視窗就關自己；沒有的話只往上找「同一個程式」的父程式（瀏覽器放顯存的子程式 → 瀏覽器主程式），
    WebView2 另外可以再往上一層找到用它的程式。都找不到還是自己（只能強制結束）。
    不能隨便往上找第一個有視窗的：2026-11-03 測試時一路找到開 pytest 的終端機、甚至 Claude 的視窗。"""
    if has_windows(pid):
        return pid
    stem = exe_stem(table.get(pid, ("", 0))[0])
    for parent in system.ancestors(pid, table)[:MAX_HOPS]:
        parent_stem = exe_stem(table[parent][0])
        if parent_stem != stem:
            if stem == "msedgewebview2" and has_windows(parent):
                return parent                           # WebView2 → 用它的程式
            break
        if has_windows(parent):
            return parent
    return pid


class Offers:
    """這個程式列出來過的程式（pid, 啟動時間）：關閉只收這些，過了 OFFER_TTL 就不算。"""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._seen: dict[tuple[int, str], float] = {}
        self._lock = threading.Lock()

    def offer(self, pid: int, created: str | None) -> None:
        if created is None:
            return
        now = self._clock()
        with self._lock:
            self._seen[(pid, created)] = now
            if len(self._seen) > 2000:              # 不會無限長大
                self._seen = {k: t for k, t in self._seen.items() if now - t < OFFER_TTL}

    def allowed(self, pid: int, created: str) -> bool:
        with self._lock:
            t = self._seen.get((pid, created))
        return t is not None and self._clock() - t < OFFER_TTL


class ActionLog:
    """data/actions.log：做過什麼（時間、動作、程式、結果），一行一筆 JSON。"""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._lock = threading.Lock()

    def write(self, **fields) -> None:
        if self.path is None:
            return
        line = json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **fields}, ensure_ascii=False)
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size > LOG_LIMIT:
                    os.replace(self.path, self.path.with_name(self.path.name + ".1"))
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass


# ---- 真的 ----

class WindowsProcesses:
    def __init__(self, log: ActionLog | None = None, offers: Offers | None = None,
                 gentle_wait: float = GENTLE_WAIT) -> None:
        self.offers = offers or Offers()
        self.log = log or ActionLog(None)
        self.gentle_wait = gentle_wait
        self.me = os.getpid()

    def created(self, pid: int) -> str | None:
        return created_at(pid)

    def _check(self, pid: int, created: str, table: dict[int, tuple[str, int]]) -> None:
        if not self.offers.allowed(pid, created):
            raise ProcessError("這個程式不在清單上了，重新整理再試一次")
        if created_at(pid) != created:
            raise ProcessError("這個程式已經結束了")
        reason = protected_reason(pid, table, self.me)
        if reason:
            raise ProcessError(reason)

    def plan(self, pid: int, created: str) -> dict:
        """按「關閉」時先問：會關哪個程式、能不能好好關、不能關的原因。只查不動。"""
        table = system.process_table()
        name = system.process_name(pid, table, self.me)
        try:
            self._check(pid, created, table)
        except ProcessError as e:
            return {"pid": pid, "name": name, "blocked": str(e)}
        target = pick_target(pid, table, lambda p: bool(windows_of(p)))
        target_created = created if target == pid else created_at(target)
        if target_created is None:
            return {"pid": pid, "name": name, "blocked": "找不到它的主程式"}
        reason = protected_reason(target, table, self.me) if target != pid else None
        target_name = system.process_name(target, table, self.me)
        if reason:
            return {"pid": pid, "name": name, "blocked": f"它是「{target_name}」的一部分：{reason}"}
        self.offers.offer(target, target_created)
        return {"pid": pid, "name": name, "blocked": None,
                "target": {"pid": target, "created": target_created, "name": target_name},
                "gentle": bool(windows_of(target))}

    def close(self, pid: int, created: str, force: bool) -> dict:
        """好好關（force=False）或強制結束。回傳 {result: closed／running／no_window／killed／failed, detail}。"""
        table = system.process_table()
        name = system.process_name(pid, table, self.me)
        self._check(pid, created, table)
        if force:
            ok = _terminate(pid)
            result = "killed" if ok else "failed"
        else:
            hwnds = windows_of(pid)
            if not hwnds:
                result = "no_window"
            else:
                _post_close(hwnds)
                result = "closed" if _wait_exit(pid, self.gentle_wait) else "running"
        self.log.write(action="force-close" if force else "close", pid=pid, name=name,
                       exe=table.get(pid, ("", 0))[0], result=result)
        return {"result": result, "name": name}


# ---- 示範 ----

class DemoProcesses:
    """示範資料用：不碰任何真的程式。關掉的從示範清單拿掉 2 分鐘；ChatGPT 示範「還開著（在問要不要存檔）」。"""

    PROTECTED_NAMES = ("桌面視窗管理員", "檔案總管", "HWGlance 這個視窗")
    STUBBORN = "ChatGPT"
    WEBVIEW_HOST = "pythonw（-m projecthub）"

    def __init__(self, source, clock: Callable[[], float] = time.monotonic) -> None:
        self.source = source
        self.offers = Offers(clock)
        self._clock = clock
        self._extra: dict[int, str] = {}            # 不在顯存清單上的示範程式（tempfiles.DemoTemp 的鎖住檔案的程式）
        self.closed: set[int] = set()               # 示範關掉的（DemoTemp 看這個，那組檔就不再被鎖）

    def created(self, pid: int) -> str:
        return f"demo-{pid}"

    def register(self, pid: int, name: str) -> None:
        self._extra[pid] = name

    def _name(self, pid: int) -> str | None:
        if pid in self._extra:
            return None if pid in self.closed else self._extra[pid]
        return next((p["name"] for p in self.source.gpu_processes() if p["pid"] == pid), None)

    def plan(self, pid: int, created: str) -> dict:
        name = self._name(pid) or f"程式 {pid}"
        if not self.offers.allowed(pid, created) or self._name(pid) is None:
            return {"pid": pid, "name": name, "blocked": "這個程式不在清單上了，重新整理再試一次"}
        if name.startswith(self.PROTECTED_NAMES):
            return {"pid": pid, "name": name, "blocked": "這是 Windows 的一部分，關掉畫面或系統會出問題"}
        target = {"pid": pid, "created": created, "name": name}
        if name.startswith(self.WEBVIEW_HOST):               # 示範「顯存在子程式、關的是有視窗的主程式」
            target = {"pid": 32960, "created": self.created(32960), "name": self.WEBVIEW_HOST}
        self.offers.offer(target["pid"], target["created"])
        return {"pid": pid, "name": name, "blocked": None, "target": target, "gentle": True}

    def close(self, pid: int, created: str, force: bool) -> dict:
        if not self.offers.allowed(pid, created):
            raise ProcessError("這個程式不在清單上了，重新整理再試一次")
        name = self._name(pid) or ("pythonw（-m projecthub）" if pid == 32960 else f"程式 {pid}")
        if not force and name.startswith(self.STUBBORN):
            return {"result": "running", "name": name}
        hide = getattr(self.source, "hide_process", None)
        if callable(hide):
            hide(32972 if pid == 32960 else pid)
        self.closed.add(pid)
        return {"result": "killed" if force else "closed", "name": name}
