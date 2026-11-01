"""這台 Windows 的狀態：是不是系統管理員、PawnIO 裝了沒、CPU 快取多大、哪些程式佔了顯存。只讀，不改任何東西。"""
from __future__ import annotations

import ctypes
import os
import re
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


# ---- 每個程式用了多少顯存：Windows 的效能計數器「GPU Process Memory」（工作管理員的「專用 GPU 記憶體」）----

GPU_MEMORY_COUNTER = r"\GPU Process Memory(*)\Dedicated Usage"   # 英文名稱，中文版 Windows 也通用
PDH_MORE_DATA = 0x800007D2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_COMMAND_LINE_INFORMATION = 60
# 常見程式的中文說明（只是讓名單好認；認不得的照程式檔名）
KNOWN_PROGRAMS = {
    "dwm": "桌面視窗管理員（Windows 畫面）", "explorer": "檔案總管", "msedgewebview2": "WebView2（內嵌網頁）",
    "msedge": "Edge 瀏覽器", "chrome": "Chrome 瀏覽器", "firefox": "Firefox 瀏覽器",
    "searchhost": "Windows 搜尋", "startmenuexperiencehost": "開始功能表", "textinputhost": "輸入法",
    "csrss": "Windows 子系統", "shellexperiencehost": "Windows 介面（通知、工作列）",
    "cefviewwing": "StreamDock 的介面", "razerappengine": "Razer Synapse", "snippingtool": "剪取工具",
}
SCRIPT_HOSTS = frozenset({"python", "pythonw", "node", "java", "javaw", "dotnet", "electron"})


class _FileTime(ctypes.Structure):
    _fields_ = [("Low", ctypes.c_uint32), ("High", ctypes.c_uint32)]


class _PdhRawCounter(ctypes.Structure):
    _fields_ = [("CStatus", ctypes.c_uint32), ("TimeStamp", _FileTime), ("FirstValue", ctypes.c_longlong),
                ("SecondValue", ctypes.c_longlong), ("MultiCount", ctypes.c_uint32)]


class _PdhRawCounterItem(ctypes.Structure):
    _fields_ = [("szName", ctypes.c_wchar_p), ("RawValue", _PdhRawCounter)]


class _UnicodeString(ctypes.Structure):
    _fields_ = [("Length", ctypes.c_ushort), ("MaximumLength", ctypes.c_ushort), ("Buffer", ctypes.c_wchar_p)]


class _ProcessEntry(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_uint32), ("cntUsage", ctypes.c_uint32), ("th32ProcessID", ctypes.c_uint32),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", ctypes.c_uint32),
                ("cntThreads", ctypes.c_uint32), ("th32ParentProcessID", ctypes.c_uint32),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", ctypes.c_uint32), ("szExeFile", ctypes.c_wchar * 260)]


def process_table() -> dict[int, tuple[str, int]]:
    """{pid: (執行檔名稱, 父程式 pid)}。用程式清單快照，不必打開程式，系統保護的程式（dwm、csrss）也拿得到名稱。"""
    if sys.platform != "win32":
        return {}
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    snap = kernel32.CreateToolhelp32Snapshot(2, 0)                  # TH32CS_SNAPPROCESS
    if not snap or snap == ctypes.c_void_p(-1).value:
        return {}
    names: dict[int, tuple[str, int]] = {}
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Process32FirstW(ctypes.c_void_p(snap), ctypes.byref(entry))
        while ok:
            names[entry.th32ProcessID] = (entry.szExeFile, entry.th32ParentProcessID)
            ok = kernel32.Process32NextW(ctypes.c_void_p(snap), ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(snap))
    return names


def pid_of_instance(name: str) -> int | None:
    """計數器的實例名稱 pid_1300_luid_0x…_phys_0 → 1300。"""
    m = re.match(r"pid_(\d+)_", name)
    return int(m.group(1)) if m else None


def gpu_memory_by_pid() -> dict[int, int]:
    """{pid: 專用顯存位元組}。好幾張顯示卡的會加在一起（這台只有一張）。讀不到回傳空的。"""
    if sys.platform != "win32":
        return {}
    pdh = ctypes.windll.pdh
    query, counter = ctypes.c_void_p(), ctypes.c_void_p()
    if pdh.PdhOpenQueryW(None, None, ctypes.byref(query)) != 0:
        return {}
    try:
        if pdh.PdhAddEnglishCounterW(query, GPU_MEMORY_COUNTER, None, ctypes.byref(counter)) != 0:
            return {}
        if pdh.PdhCollectQueryData(query) != 0:
            return {}
        size, count = ctypes.c_uint32(0), ctypes.c_uint32(0)
        status = pdh.PdhGetRawCounterArrayW(counter, ctypes.byref(size), ctypes.byref(count), None)
        if status & 0xFFFFFFFF != PDH_MORE_DATA or size.value == 0:
            return {}
        buf = ctypes.create_string_buffer(size.value)
        if pdh.PdhGetRawCounterArrayW(counter, ctypes.byref(size), ctypes.byref(count), buf) != 0:
            return {}
        items = ctypes.cast(buf, ctypes.POINTER(_PdhRawCounterItem))
        usage: dict[int, int] = {}
        for i in range(count.value):
            pid = pid_of_instance(items[i].szName or "")
            if pid:
                usage[pid] = usage.get(pid, 0) + max(0, items[i].RawValue.FirstValue)
        return usage
    finally:
        pdh.PdhCloseQuery(query)


def process_info(pid: int) -> tuple[str | None, str | None]:
    """(執行檔完整路徑, 命令列)。本體以系統管理員執行，大部分程式都查得到；查不到就是 None。"""
    if sys.platform != "win32":
        return None, None
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None, None
    handle = ctypes.c_void_p(handle)
    try:
        path = None
        size = ctypes.c_uint32(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            path = buf.value
        cmdline = None
        need = ctypes.c_uint32(0)
        nt = ctypes.windll.ntdll.NtQueryInformationProcess
        nt(handle, PROCESS_COMMAND_LINE_INFORMATION, None, 0, ctypes.byref(need))
        if need.value:
            raw = ctypes.create_string_buffer(need.value)
            if nt(handle, PROCESS_COMMAND_LINE_INFORMATION, raw, need.value, ctypes.byref(need)) == 0:
                us = ctypes.cast(raw, ctypes.POINTER(_UnicodeString)).contents
                cmdline = ctypes.wstring_at(ctypes.cast(us.Buffer, ctypes.c_void_p), us.Length // 2) if us.Buffer else None
        return path, cmdline
    finally:
        kernel32.CloseHandle(handle)


def describe_process(path: str | None, cmdline: str | None, pid: int) -> str:
    """畫面上的程式名稱：python（ComfyUI\\main.py）、dwm → 桌面視窗管理員（Windows 畫面）…"""
    if not path:
        return f"程式 {pid}"
    exe = re.split(r"[\\/]", path)[-1]
    stem = re.sub(r"\.exe$", "", exe, flags=re.I)
    if stem.lower() in KNOWN_PROGRAMS:
        return KNOWN_PROGRAMS[stem.lower()]
    if stem.lower() in SCRIPT_HOSTS and cmdline:
        args = re.findall(r'"([^"]*)"|(\S+)', cmdline)
        words = [a or b for a, b in args][1:]
        for i, w in enumerate(words):
            if w == "-m" and i + 1 < len(words):
                return f"{stem}（-m {words[i + 1]}）"
            if re.search(r"\.(py|pyw|js|mjs|jar)$", w, re.I):
                parts = re.split(r"[\\/]", w)
                return f"{stem}（{'\\'.join(parts[-2:])}）"
    return stem


def ancestors(pid: int, table: dict[int, tuple[str, int]]) -> list[int]:
    """pid 的父、祖父…（到清單裡找不到為止；防止 pid 重複使用造成的迴圈）。"""
    chain: list[int] = []
    cur = table.get(pid, ("", 0))[1]
    while cur in table and cur not in chain and cur != pid:
        chain.append(cur)
        cur = table[cur][1]
    return chain


def process_name(pid: int, table: dict[int, tuple[str, int]], me: int,
                 info=process_info) -> str:
    """名單上的名稱。WebView2 是別的程式借用的內嵌網頁，改寫成「哪個程式的」：往上找第一個不是 WebView2 的父程式。"""
    path, cmdline = info(pid)
    exe = path or table.get(pid, ("", 0))[0] or None
    if exe and re.search(r"msedgewebview2(\.exe)?$", exe, re.I):
        chain = ancestors(pid, table)
        host = next((p for p in chain if not table[p][0].lower().startswith("msedgewebview2")), None)
        if host is not None:
            if host == me or me in ancestors(host, table):
                return "HWGlance 這個視窗（WebView2）"
            host_path, host_cmdline = info(host)
            return f"{describe_process(host_path or table[host][0], host_cmdline, host)} 的視窗（WebView2）"
    return describe_process(exe, cmdline, pid)


def gpu_processes() -> list[dict]:
    """用了顯存的程式：[{pid, name, path, bytes}]，大的在前。"""
    out = []
    usage = gpu_memory_by_pid()
    table = process_table() if usage else {}
    me = os.getpid()
    for pid, used in usage.items():
        if used <= 0:
            continue
        out.append({"pid": pid, "name": process_name(pid, table, me), "bytes": used})
    return sorted(out, key=lambda p: -p["bytes"])


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


WEBVIEW2_KEY = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
PAWNIO_SITE = "https://pawnio.eu"         # 官方網站，下載鈕連到 GitHub namazso/PawnIO.Setup 的 PawnIO_setup.exe


def webview2_version() -> str | None:
    """Edge WebView2 Runtime 的版本（跟 pywebview 一樣看 EdgeUpdate 的登錄；這台在 WOW6432Node 底下）。沒裝回傳 None。
    沒有的話 pywebview 會退回舊的 IE 引擎，介面會是一片空白，所以 core.Window 改用預設瀏覽器開。"""
    if sys.platform != "win32":
        return None
    import winreg
    for hive, path in ((winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\{WEBVIEW2_KEY}"),
                       (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\{WEBVIEW2_KEY}"),
                       (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\{WEBVIEW2_KEY}")):
        try:
            with winreg.OpenKey(hive, path) as k:
                value = str(winreg.QueryValueEx(k, "pv")[0] or "")
        except OSError:
            continue
        if value and value != "0.0.0.0":
            return value
    return None


def hints(kind: str, admin: bool, pawnio: str | None) -> list[str]:
    """畫面上方要提醒的事：為什麼有些溫度讀不到、怎麼補（網址在畫面上是可以點的連結）。
    別台第一次開最常碰到這兩個：沒按「是」給權限、沒裝 PawnIO。"""
    if kind == "demo":
        return ["示範資料：數字是模擬的，不是這台電腦的讀數"]
    out = []
    if not admin:
        out.append("沒有以系統管理員執行：CPU、主機板、硬碟的溫度讀不到。重新開 HWGlance，跳出權限視窗時按「是」")
    if pawnio is None:
        out.append(f"還沒安裝 PawnIO 驅動：CPU 與主機板的溫度讀不到。到 {PAWNIO_SITE} 下載安裝（要系統管理員），"
                   "裝好後重開 HWGlance")
    return out
