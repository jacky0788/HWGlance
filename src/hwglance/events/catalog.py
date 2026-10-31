"""要讀哪些事件、每一種代表什麼：一筆事件 → 一筆 Entry（類別、嚴重程度、中文說明）。

這裡只看單一筆事件；一串事件怎麼歸成一件事、標題怎麼下在 incidents.py。依據：
- 事件的定義與訊息：這台 Windows（Get-WinEvent -ListProvider）與 2026-06～10 的真實記錄
- 藍屏代碼的名稱：Microsoft Learn 的 Bug Check Code Reference、Kernel Live Dump Code Reference
序號一律遮掉（Ntfs 140 的訊息裡有硬碟序號）。
"""
from __future__ import annotations

import re
import struct
import time
from calendar import timegm
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from .model import RawEvent
from .reader import parse_time

CRASH, GPU, STORAGE, HARDWARE, SYSTEM, APP, MARKER = "crash", "gpu", "storage", "hardware", "system", "app", "marker"
HOT, WARM, INFO = "hot", "warm", "info"


@dataclass
class Entry:
    time: float                # 這件事發生的時間（6008 用的是 Windows 記下的最後運作時間）
    logged: float              # Windows 記下這筆的時間
    category: str              # crash｜gpu｜storage｜hardware｜system｜app｜marker
    role: str                  # 在這一類裡的角色（reboot、alive、bugcheck、disconnect…），歸類與下標題用
    level: str                 # hot（紅）｜warm（黃）｜info（灰）
    key: str                   # 同一件事裡，key 一樣的合成一行
    text: str                  # 這一行的中文說明
    short: str                 # 串成摘要時的說法（「FECS 致命錯誤」）；空的不放進摘要
    source: str                # 「nvlddmkm 13」
    log: str
    message: str | None
    info: dict = field(default_factory=dict)


# ---- 藍屏代碼：(名稱, 中文, 方向)；方向決定建議（gpu／driver／memory／storage／hardware／power／system） ----

BUGCHECKS: dict[int, tuple[str, str, str]] = {
    0x0A: ("IRQL_NOT_LESS_OR_EQUAL", "驅動程式存取了不該存取的記憶體", "driver"),
    0x19: ("BAD_POOL_HEADER", "核心記憶體區塊損毀", "memory"),
    0x1A: ("MEMORY_MANAGEMENT", "記憶體管理出錯", "memory"),
    0x1E: ("KMODE_EXCEPTION_NOT_HANDLED", "核心程式發生沒處理的例外", "driver"),
    0x24: ("NTFS_FILE_SYSTEM", "NTFS 檔案系統出錯", "storage"),
    0x3B: ("SYSTEM_SERVICE_EXCEPTION", "系統服務發生例外", "driver"),
    0x50: ("PAGE_FAULT_IN_NONPAGED_AREA", "存取了不存在的記憶體", "memory"),
    0x77: ("KERNEL_STACK_INPAGE_ERROR", "從硬碟讀回核心資料失敗", "storage"),
    0x7A: ("KERNEL_DATA_INPAGE_ERROR", "從硬碟讀回核心資料失敗", "storage"),
    0x7E: ("SYSTEM_THREAD_EXCEPTION_NOT_HANDLED", "系統執行緒發生沒處理的例外", "driver"),
    0x7F: ("UNEXPECTED_KERNEL_MODE_TRAP", "CPU 遇到核心處理不了的狀況", "hardware"),
    0x80: ("NMI_HARDWARE_FAILURE", "硬體送出無法忽略的錯誤訊號（NMI）", "hardware"),
    0x8E: ("KERNEL_MODE_EXCEPTION_NOT_HANDLED", "核心程式發生沒處理的例外", "driver"),
    0x9C: ("MACHINE_CHECK_EXCEPTION", "CPU 回報硬體錯誤", "hardware"),
    0x9F: ("DRIVER_POWER_STATE_FAILURE", "睡眠或喚醒時有驅動程式沒回應", "power"),
    0xA0: ("INTERNAL_POWER_ERROR", "電源管理出錯", "power"),
    0xC2: ("BAD_POOL_CALLER", "驅動程式錯用核心記憶體", "driver"),
    0xC5: ("DRIVER_CORRUPTED_EXPOOL", "驅動程式弄壞了核心記憶體", "driver"),
    0xD1: ("DRIVER_IRQL_NOT_LESS_OR_EQUAL", "驅動程式存取了不該存取的記憶體", "driver"),
    0xE2: ("MANUALLY_INITIATED_CRASH", "手動觸發的藍屏", "system"),
    0xEA: ("THREAD_STUCK_IN_DEVICE_DRIVER", "顯示卡驅動卡住", "gpu"),
    0xED: ("UNMOUNTABLE_BOOT_VOLUME", "開機磁碟掛載不了", "storage"),
    0xEF: ("CRITICAL_PROCESS_DIED", "重要的系統程式結束了", "system"),
    0xF4: ("CRITICAL_OBJECT_TERMINATION", "重要的系統程式結束了（常是讀不到硬碟）", "storage"),
    0xFC: ("ATTEMPTED_EXECUTE_OF_NOEXECUTE_MEMORY", "執行了不能執行的記憶體", "driver"),
    0x101: ("CLOCK_WATCHDOG_TIMEOUT", "有 CPU 核心沒有回應", "hardware"),
    0x109: ("CRITICAL_STRUCTURE_CORRUPTION", "核心資料被改壞", "memory"),
    0x10E: ("VIDEO_MEMORY_MANAGEMENT_INTERNAL", "顯示記憶體管理出錯", "gpu"),
    0x113: ("VIDEO_DXGKRNL_FATAL_ERROR", "DirectX 核心出錯", "gpu"),
    0x116: ("VIDEO_TDR_FAILURE", "顯示卡驅動重設失敗", "gpu"),
    0x117: ("VIDEO_TDR_TIMEOUT_DETECTED", "顯示卡驅動逾時", "gpu"),
    0x119: ("VIDEO_SCHEDULER_INTERNAL_ERROR", "顯示卡排程出錯", "gpu"),
    0x124: ("WHEA_UNCORRECTABLE_ERROR", "硬體回報無法修正的錯誤", "hardware"),
    0x133: ("DPC_WATCHDOG_VIOLATION", "驅動程式卡住太久", "driver"),
    0x139: ("KERNEL_SECURITY_CHECK_FAILURE", "核心資料檢查失敗", "driver"),
    0x13A: ("KERNEL_MODE_HEAP_CORRUPTION", "核心記憶體損毀", "memory"),
    0x144: ("BUGCODE_USB3_DRIVER", "USB 3 驅動出錯", "driver"),
    0x14F: ("PDC_WATCHDOG_TIMEOUT", "電源管理元件逾時", "power"),
    0x154: ("UNEXPECTED_STORE_EXCEPTION", "記憶體壓縮存放區出錯（常是硬碟）", "storage"),
    0x1C8: ("MANUALLY_INITIATED_POWER_BUTTON_HOLD", "長按電源鍵觸發的藍屏", "power"),
    0x1CA: ("SYNTHETIC_WATCHDOG_TIMEOUT", "整個系統卡住", "system"),
    0x1DB: ("IPI_WATCHDOG_TIMEOUT", "CPU 核心之間沒有回應", "hardware"),
    0x1E4: ("VIDEO_DXGKRNL_SYSMM_FATAL_ERROR", "顯示卡記憶體管理出錯", "gpu"),
}
# 即時傾印（LiveKernelEvent）：不會重開機，Windows 只存一份傾印。方向 gpu／hardware／system
LIVE_DUMPS: dict[int, tuple[str, str, str]] = {
    0x117: ("VIDEO_TDR_TIMEOUT_DETECTED", "顯示卡驅動逾時，重設後恢復", "gpu"),
    0x124: ("WHEA_UNCORRECTABLE_ERROR", "硬體回報錯誤", "hardware"),
    0x141: ("VIDEO_ENGINE_TIMEOUT_DETECTED", "顯示卡某個引擎逾時，重設後恢復", "gpu"),
    0x142: ("VIDEO_TDR_APPLICATION_BLOCKED", "程式被暫停使用顯示卡", "gpu"),
    0x144: ("BUGCODE_USB3_DRIVER", "USB 3 控制器或裝置出錯", "hardware"),
    0x15F: ("CONNECTED_STANDBY_WATCHDOG_TIMEOUT_LIVEDUMP", "待機時逾時", "system"),
    0x161: ("LIVE_SYSTEM_DUMP", "手動或工具要求的即時傾印", "system"),
    0x187: ("VIDEO_DWMINIT_TIMEOUT_FALLBACK_BDD", "顯示卡驅動啟動逾時，改用基本顯示", "gpu"),
    0x193: ("VIDEO_DXGKRNL_LIVEDUMP", "DirectX 核心記下異常", "gpu"),
    0x1A1: ("WIN32K_CALLOUT_WATCHDOG_LIVEDUMP", "畫面元件卡住", "system"),
    0x1A8: ("VIDEO_DXGKRNL_BLACK_SCREEN_LIVEDUMP", "偵測到黑畫面", "gpu"),
    0x1B0: ("VIDEO_MINIPORT_FAILED_LIVEDUMP", "顯示卡驅動出錯", "gpu"),
    0x1B8: ("VIDEO_MINIPORT_BLACK_SCREEN_LIVEDUMP", "顯示卡驅動偵測到黑畫面", "gpu"),
    0x1C9: ("USER_MODE_HEALTH_MONITOR_LIVEDUMP", "系統程式沒有回應", "system"),
    0x1F5: ("APPLICATION_HANG_KERNEL_LIVEDUMP", "程式卡住", "system"),
}
# 程式當掉的例外代碼
EXCEPTIONS = {
    0xC0000005: "存取違規", 0xC0000006: "讀不到程式要的檔案（硬碟或網路磁碟問題）", 0xC000001D: "不合法的指令",
    0xC00000FD: "堆疊溢位", 0xC0000374: "記憶體堆積損毀", 0xC0000409: "程式發現錯誤後自己中止",
    0xC0000420: "程式的檢查沒通過", 0xE0434352: ".NET 例外", 0xE06D7363: "C++ 例外", 0x80000003: "中斷點",
    0x40000015: "程式自己中止",
}
# 讀寫失敗的狀態碼（NTSTATUS）
STATUSES = {
    0xC000000E: "裝置不見了（斷線）", 0xC000009D: "裝置沒有連接", 0xC000009C: "資料錯誤（可能是壞軌）",
    0xC0000185: "裝置讀寫錯誤", 0xC00000B5: "逾時沒有回應", 0xC000026E: "磁碟區已經卸載", 0xC000020C: "連線中斷",
}
DISCONNECTED = frozenset({0xC000000E, 0xC000009D})

GPU_DRIVERS = ("nvlddmkm", "amdkmdag", "amdwddmg", "igfx", "igfxn")
CONTROLLERS = {"storahci": "SATA 控制器", "stornvme": "NVMe 控制器", "UASPStor": "USB 硬碟（UASP）",
               "iaStorAC": "Intel 儲存控制器", "iaStorAVC": "Intel 儲存控制器", "iaStorVD": "Intel 儲存控制器",
               "iaStorA": "Intel 儲存控制器", "secnvme": "Samsung NVMe 控制器", "amdsata": "AMD SATA 控制器",
               "rcraid": "AMD RAID 控制器"}
# disk 的事件：(說明, 摘要說法, 嚴重程度, 角色)
DISK_EVENTS = {
    7: ("有壞軌（讀不到某個區塊）", "壞軌", HOT, "bad"),
    11: ("的控制器回報錯誤", "控制器錯誤", HOT, "io"),
    15: ("還沒準備好就被存取", "硬碟沒準備好", WARM, "io"),
    51: ("在分頁（虛擬記憶體）讀寫時出錯", "分頁錯誤", HOT, "io"),
    52: ("自己預測快要故障了（SMART）", "預測故障", HOT, "predict"),
    153: ("的讀寫要重試才成功", "讀寫重試", WARM, "io"),
    154: ("的讀寫因為硬體錯誤失敗", "讀寫失敗", HOT, "io"),
    157: ("突然被移除（斷線）", "硬碟斷線", HOT, "disconnect"),
}
WHEA_PARTS = {
    16: "PCIe 裝置", 17: "PCIe 裝置", 18: "CPU 核心", 19: "CPU 核心", 28: "CPU 核心", 29: "CPU 核心",
    20: "AMD 北橋（記憶體控制器、Infinity Fabric）", 21: "AMD 北橋（記憶體控制器、Infinity Fabric）",
    22: "記憶體", 23: "記憶體", 46: "記憶體", 47: "記憶體", 48: "記憶體", 49: "記憶體",
    24: "PCI 匯流排", 25: "PCI 匯流排", 42: "PCI 匯流排", 43: "PCI 匯流排",
    26: "PCI 裝置", 27: "PCI 裝置", 44: "PCI 裝置", 45: "PCI 裝置",
}
WHEA_FATAL = frozenset({1, 16, 18, 20, 22, 24, 26, 29, 40, 42, 44, 46, 48})
WHEA_CORRECTED = frozenset({2, 17, 19, 21, 23, 25, 27, 28, 41, 43, 45, 47, 49})

# 要讀哪些事件：{記錄: {來源: 事件編號；None＝那個來源的錯誤與警告都要}}
WATCHED: dict[str, dict[str, tuple[int, ...] | None]] = {
    "System": {
        "Microsoft-Windows-Kernel-Power": (41, 42, 86, 87, 88, 107),
        "EventLog": (6008,),
        "Microsoft-Windows-WER-SystemErrorReporting": (1001,),
        "volmgr": (45, 46, 161, 162),
        "Microsoft-Windows-Kernel-Boot": (29,),
        "Microsoft-Windows-Kernel-General": (12, 13),
        **dict.fromkeys(GPU_DRIVERS),
        "Display": (4101,),
        "disk": tuple(DISK_EVENTS),
        "Ntfs": (50, 55, 57, 140),
        "Microsoft-Windows-Ntfs": (55, 57, 140),
        **dict.fromkeys(CONTROLLERS, (11, 129)),
        "Microsoft-Windows-WHEA-Logger": None,
        "Microsoft-Windows-Kernel-Processor-Power": (35, 37),
        "Microsoft-Windows-Resource-Exhaustion-Detector": (2004,),
    },
    "Application": {
        "Application Error": (1000, 1005),
        "Application Hang": (1002,),
        ".NET Runtime": (1026,),
        "Windows Error Reporting": (1001,),
    },
}

HEX = re.compile(r"0x([0-9A-Fa-f]+)")
SERIAL = re.compile(r"((?:裝置序號|配接器序號|[Ss]erial [Nn]umber)\s*[:：])[^\S\n]*\S+")
DISK_NUMBER = re.compile(r"Harddisk(\d+)", re.I)
RAID_PORT = re.compile(r"RaidPort(\d+)", re.I)
DRIVE = re.compile(r"^([A-Za-z]):")
DUMP_STAMP = re.compile(r"-(\d{8})-(\d{4})(?:\.\d+)?\.dmp", re.I)


def hexcode(n: int) -> str:
    return f"0x{n:X}"


def _int(text: str | None, base: int = 10) -> int:
    try:
        return int((text or "").strip(), base)
    except ValueError:
        return 0


def _hex(text: str | None) -> int:
    """0xc000000e、c000000e、-1073741810 都認得。"""
    s = (text or "").strip().lower().removeprefix("0x")
    if s.startswith("-") and s[1:].isdigit():
        return int(s) & 0xFFFFFFFF
    return _int(s, 16)


def describe_bugcheck(code: int) -> tuple[str, str, str]:
    """藍屏代碼 → (名稱, 中文, 方向)。0x1000007E 這類高位元旗標的跟 0x7E 同一種。"""
    found = BUGCHECKS.get(code) or BUGCHECKS.get(code & ~0x10000000)
    return found or ("UNKNOWN", "Windows 判斷無法繼續執行", "system")


def systemtime(binary: str | None, offset: int) -> float | None:
    """事件附加資料裡的 SYSTEMTIME（16 位元組）→ epoch 秒；6008 的第二個是 UTC。"""
    try:
        raw = bytes.fromhex((binary or "")[offset * 2:(offset + 16) * 2])
    except ValueError:
        return None
    if len(raw) < 16:
        return None
    y, mo, _dow, d, h, mi, s, ms = struct.unpack("<8H", raw)
    if not (1601 <= y <= 9999 and 1 <= mo <= 12 and 1 <= d <= 31 and h < 24 and mi < 60 and s < 61):
        return None
    return timegm((y, mo, d, h, mi, s)) + ms / 1000


def mask(message: str | None) -> str | None:
    return SERIAL.sub(r"\1 （不顯示）", message) if message else message


def status_text(code: int) -> str:
    return STATUSES.get(code) or (hexcode(code) if code else "原因不明")


def disk_label(number: str | None, disks: Mapping[str, str]) -> str:
    if number is None:
        return "硬碟"
    model = disks.get(number)
    return f"硬碟 {number}（{model}）" if model else f"硬碟 {number}"


def join(name: str, rest: str) -> str:
    """「硬碟 3」＋「的控制器…」中間空一格；名稱以全形括號結尾就不用。"""
    return f"{name}{rest}" if name.endswith("）") else f"{name} {rest}"


def dump_time(attached: str | None, logged: float) -> float | None:
    """即時傾印的報告常常晚很久才送出（6/17 開機時送的報告，傾印檔其實是 3/29 的），真正的時間在傾印檔名裡：
    WATCHDOG-20261031-1011.dmp（本機時間，到分鐘）。比記下的時間早 2 分鐘以上才用，不然照記下的時間。"""
    m = DUMP_STAMP.search(attached or "")
    if not m:
        return None
    try:
        t = time.mktime(time.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M"))
    except (ValueError, OverflowError):
        return None
    return t if logged - 366 * 86400 <= t <= logged - 120 else None


def _entry(ev: RawEvent, category: str, role: str, level: str, key: str, text: str, short: str = "",
           info: dict | None = None, *, time: float | None = None, message: str | None = None) -> Entry:
    return Entry(time=ev.time if time is None else time, logged=ev.time, category=category, role=role, level=level,
                 key=key, text=text, short=short, source=f"{ev.provider.removeprefix('Microsoft-Windows-')} {ev.event_id}",
                 log=ev.log, message=mask(message if message is not None else ev.message), info=info or {})


# ---- 當機與開關機 ----

def _reboot(ev: RawEvent, disks: Mapping[str, str]) -> Entry:
    f = ev.fields
    code = _int(f.get("BugcheckCode", ev.arg(0)))
    pressed = f.get("PowerButtonTimestamp", "0") not in ("", "0") or f.get("LongPowerButtonPressDetected") == "true"
    if code:
        how, text = "bugcheck", f"沒有正常關機就重新開機（藍屏 {hexcode(code)}）"
    elif pressed:
        how, text = "button", "按了電源鍵強制關機，之後重新開機"
    elif f.get("SleepInProgress", "0") not in ("", "0"):
        how, text = "sleep", "睡眠或快速啟動的過程中斷，重新開機"
    else:
        how, text = "reset", "沒有正常關機就重新開機（沒有藍屏代碼）"
    return _entry(ev, CRASH, "reboot", HOT, "crash:reboot", text, "重新開機", {"how": how, "bugcheck": code or None})


def _unexpected(ev: RawEvent, disks: Mapping[str, str]) -> Entry:
    """6008：Windows 定時記下「還在運作」，這是最後一次記的時間。只能當成至少活到這時候：
    這台 6/17、7/4 那兩次比真的當機早了 35、38 分鐘（之後還有顯示卡事件）。"""
    alive = systemtime(ev.binary, 16)
    if alive is None or alive > ev.time:
        alive = ev.time
    return _entry(ev, CRASH, "alive", HOT, "crash:alive", "Windows 最後一次記下「還在運作」的時間，之後某個時候意外關機",
                  "最後的運作記錄", time=alive)


def _bugcheck(ev: RawEvent, disks: Mapping[str, str]) -> Entry:
    m = HEX.search(ev.fields.get("param1") or ev.arg(0))
    code = int(m.group(1), 16) if m else 0
    name, zh, _ = describe_bugcheck(code)
    return _entry(ev, CRASH, "bugcheck", HOT, "crash:bugcheck", f"藍屏代碼 {hexcode(code)} {name}：{zh}", "",
                  {"bugcheck": code, "dump": ev.fields.get("param2") or ev.arg(1) or None})


def _volmgr(ev: RawEvent, disks: Mapping[str, str]) -> Entry:
    if ev.event_id == 162:
        return _entry(ev, CRASH, "dump", INFO, "crash:dump", "藍屏的傾印檔存好了")
    if ev.event_id == 161:
        return _entry(ev, CRASH, "dump", WARM, "crash:dumpfail", "藍屏的傾印檔沒存成功")
    return _entry(ev, SYSTEM, "dumpconfig", WARM, "volmgr:config", "當機傾印功能沒準備好（之後當機可能存不到傾印檔）",
                  "傾印功能沒準備好")


def _thermal(ev: RawEvent, disks: Mapping[str, str]) -> Entry:
    what = "關機" if ev.event_id == 86 else "休眠"
    return _entry(ev, CRASH, "thermal", HOT, "crash:thermal", f"溫度過高，Windows 為了保護硬體自動{what}", "過熱保護")


def _fast_startup(ev: RawEvent, disks: Mapping[str, str]) -> Entry:
    raw = ev.fields.get("FailureStatus") or ev.arg(0)              # 這台記的是十進位：3221225684
    code = _hex(raw) if raw.lower().startswith("0x") else _int(raw)
    return _entry(ev, SYSTEM, "boot", WARM, "boot:29", f"快速啟動失敗（{hexcode(code)}），改成重新開機", "快速啟動失敗")


def _marker(kind: str, text: str) -> Callable[[RawEvent, Mapping[str, str]], Entry]:
    def rule(ev: RawEvent, disks: Mapping[str, str]) -> Entry:
        started = parse_time(ev.fields.get("StartTime")) if kind == "boot" else 0.0
        return _entry(ev, MARKER, kind, INFO, f"marker:{kind}", text, time=started or ev.time)
    return rule


# ---- 顯示卡 ----

def _nvidia(ev: RawEvent, disks: Mapping[str, str]) -> Entry:
    desc = ev.arg(1).strip()
    i = ev.event_id
    level, role = HOT, "error"
    if i == 13:
        if "FECS" in desc:
            fatal = "Fatal" in desc
            kind, text, short = "fecs", f"顯示卡內部控制器（FECS）{'致命錯誤' if fatal else '回報例外'}", \
                f"FECS {'致命錯誤' if fatal else '例外'}"
        elif " SM " in desc or "Warp" in desc:
            kind, text, short = "sm", "顯示卡的運算單元（SM）出錯", "運算單元出錯"
        elif "Shader" in desc:
            kind, text, short = "shader", "顯示卡執行著色器時出錯", "著色器出錯"
        else:
            kind, text, short = "exception", "顯示卡回報硬體例外", "顯示卡例外"
    elif i == 14:
        m = re.search(r"to 0x\w+ \(([^)]+)\)", desc)
        action = {"PF FLR": "整張卡重設 PF FLR", "GPU Reset Required": "需要重設"}.get(m.group(1), m.group(1)) if m else "重設"
        kind, text, short, level, role = "recovery", f"驅動決定重設顯示卡（{action}）", "驅動重設顯示卡", WARM, "recovery"
    elif i == 153 and "TDR" in desc:
        kind, text, short, level, role = "tdr", "驅動重設顯示卡（TDR）", "TDR 重設", WARM, "recovery"
    elif i == 153:
        kind, text, short, level = "error", "NVIDIA 驅動回報顯示卡錯誤", "顯示卡出錯", WARM
    else:
        kind, text, short = str(i), f"NVIDIA 驅動回報錯誤（{i}）", "驅動錯誤"
        level = HOT if ev.level <= 2 else WARM
    return _entry(ev, GPU, role, level, f"nvidia:{i}:{kind}", text, short, {"detail": desc}, message=ev.message or desc or None)


def _gpu_driver(ev: RawEvent, disks: Mapping[str, str]) -> Entry:
    return _entry(ev, GPU, "error", HOT if ev.level <= 2 else WARM, f"{ev.provider}:{ev.event_id}",
                  f"顯示卡驅動回報錯誤（{ev.provider} {ev.event_id}）", "顯示卡驅動錯誤")


def _display_tdr(ev: RawEvent, disks: Mapping[str, str]) -> Entry:
    return _entry(ev, GPU, "recovery", WARM, "display:4101", "顯示卡驅動停止回應，已經自己恢復", "驅動逾時後恢復")


# ---- 硬碟 ----

def _disk(ev: RawEvent, disks: Mapping[str, str]) -> Entry | None:
    found = DISK_EVENTS.get(ev.event_id)
    if found is None:
        return None
    tail, short, level, role = found
    m = DISK_NUMBER.search(ev.arg(0))
    number = m.group(1) if m else None
    return _entry(ev, STORAGE, role, level, f"disk:{ev.event_id}:{number}", join(disk_label(number, disks), tail), short,
                  {"disk": number, "model": disks.get(number) if number else None})


def _volume(path: str | None) -> str | None:
    m = DRIVE.match((path or "").strip())
    return f"{m.group(1).upper()}:" if m else None


def _ntfs(ev: RawEvent, disks: Mapping[str, str]) -> Entry | None:
    f = ev.fields
    i = ev.event_id
    if i == 50:                                           # 舊式來源：資料是 ("", 檔案路徑)
        volume = _volume(ev.arg(1)) or _volume(ev.arg(0))
        where = f"{volume} 上的檔案資料" if volume else "有檔案資料"
        return _entry(ev, STORAGE, "io", HOT, f"ntfs:50:{volume}", f"延遲寫入失敗：{where}沒存到硬碟（可能遺失）",
                      "寫入失敗", {"volume": volume})
    if i == 55:
        volume = _volume(f.get("DriveName") or ev.arg(0)) or (f.get("DriveName") or ev.arg(0) or None)
        return _entry(ev, STORAGE, "io", HOT, f"ntfs:55:{volume}",
                      join(volume or "某個磁碟區", "的檔案系統結構損毀（要用 chkdsk 修復）"), "檔案系統損毀", {"volume": volume})
    if i in (57, 140):
        volume = _volume(f.get("VolumeId") or ev.arg(1))
        model = (f.get("ProductId") or "").strip() or None
        code = _hex(f.get("Error") or ev.arg(4))
        where = f"{volume}（{model}）" if volume and model else (volume or model or "某個磁碟區")
        role = "disconnect" if code in DISCONNECTED else "io"
        return _entry(ev, STORAGE, role, HOT, f"ntfs:140:{volume}", join(where, f"的資料寫不進交易記錄：{status_text(code)}"),
                      "寫不進交易記錄", {"volume": volume, "model": model, "status": code})
    return None


def _app_file(ev: RawEvent, disks: Mapping[str, str]) -> Entry:
    f = ev.fields
    volume = _volume(f.get("FilePath") or ev.arg(0))
    app = f.get("AppName") or ev.arg(1) or "程式"
    code = _hex(f.get("StatusCode") or ev.arg(2))
    role = "disconnect" if code in DISCONNECTED else "io"
    return _entry(ev, STORAGE, role, WARM, f"appfile:{volume}",
                  f"{app} 讀不到 {volume or '硬碟'} 上的檔案，被 Windows 關掉（{status_text(code)}）", "程式讀不到檔案",
                  {"volume": volume, "app": app, "status": code})


def _controller(ev: RawEvent, disks: Mapping[str, str]) -> Entry:
    name = CONTROLLERS.get(ev.provider, ev.provider)
    m = RAID_PORT.search(ev.arg(0))
    port = m.group(1) if m else "?"
    if ev.event_id == 129:
        return _entry(ev, STORAGE, "reset", WARM, f"{ev.provider}:129:{port}",
                      f"{name}重設了連接埠 {port} 上的裝置（裝置太久沒回應）", "控制器重設裝置", {"controller": name})
    return _entry(ev, STORAGE, "io", HOT, f"{ev.provider}:{ev.event_id}:{port}", f"{name}回報錯誤（連接埠 {port}）",
                  "控制器錯誤", {"controller": name})


# ---- CPU／記憶體／PCIe ----

def _whea(ev: RawEvent, disks: Mapping[str, str]) -> Entry | None:
    i = ev.event_id
    fatal = i in WHEA_FATAL
    if not fatal and i not in WHEA_CORRECTED:
        return None
    part = WHEA_PARTS.get(i) or (ev.fields.get("Component") or "").strip() or "硬體"
    text = f"{'嚴重' if fatal else '已修正的'}硬體錯誤（{part}）"
    return _entry(ev, HARDWARE, "fatal" if fatal else "corrected", HOT if fatal else WARM, f"whea:{i}", text,
                  "嚴重硬體錯誤" if fatal else "已修正的硬體錯誤", {"part": part})


def _cpu_power(ev: RawEvent, disks: Mapping[str, str]) -> Entry:
    if ev.event_id == 37:
        return _entry(ev, HARDWARE, "throttle", WARM, "cpu:37", "CPU 被系統韌體限速（可能是溫度、供電或電源設定）",
                      "CPU 被限速")
    return _entry(ev, HARDWARE, "firmware", WARM, "cpu:35", "CPU 的效能電源管理被停用（韌體問題）", "CPU 電源管理停用")


def _low_memory(ev: RawEvent, disks: Mapping[str, str]) -> Entry:
    return _entry(ev, SYSTEM, "memory", WARM, "memory:2004", "虛擬記憶體不足（有程式用掉太多記憶體）", "記憶體不足")


# ---- 程式當掉 ----

def _app_crash(ev: RawEvent, disks: Mapping[str, str]) -> Entry:
    f = ev.fields
    app = f.get("AppName") or ev.arg(0) or "程式"
    module = f.get("ModuleName") or ev.arg(3)
    code = _hex(f.get("ExceptionCode") or ev.arg(6))
    text = f"{app} 當掉"
    if module and module.lower() not in (app.lower(), "unknown"):
        text += f"（{module}）"
    if code in EXCEPTIONS:
        text += f"：{EXCEPTIONS[code]}"
    return _entry(ev, APP, "crash", WARM, f"app:{app.lower()}", text, "程式當掉", {"app": app, "code": code})


def _app_hang(ev: RawEvent, disks: Mapping[str, str]) -> Entry:
    app = ev.fields.get("AppName") or ev.arg(0) or "程式"
    return _entry(ev, APP, "hang", WARM, f"hang:{app.lower()}", f"{app} 沒有回應，被關掉了", "程式沒回應", {"app": app})


def _dotnet(ev: RawEvent, disks: Mapping[str, str]) -> Entry:
    body = ev.arg(0) or ev.message or ""
    m = re.search(r"^[^:：\n]*[:：]\s*(\S+)", body)
    app = m.group(1) if m else "程式"
    exc = re.search(r"\b(?:System\.)?((?:\w+\.)*\w*Exception)\b", body)
    frame = re.search(r"^\s+\S+\s+([A-Za-z_][\w.`]*)\(", body, re.M)
    text = f"{app} 因為 .NET 例外結束：{exc.group(1) if exc else '不明的例外'}"
    if frame:
        text += f"（在 {frame.group(1)}）"
    return _entry(ev, APP, "crash", WARM, f"dotnet:{app.lower()}", text, "程式當掉", {"app": app, "dotnet": True},
                  message=ev.message or body or None)


def _wer(ev: RawEvent, disks: Mapping[str, str]) -> Entry | None:
    f = ev.fields
    name = f.get("EventName") or ev.arg(2)
    p1 = f.get("P1") or ev.arg(5)
    if name == "LiveKernelEvent":
        code = _hex(p1)
        known = LIVE_DUMPS.get(code)
        title, zh, way = known or ("LIVE_DUMP", "Windows 記下核心異常", "system")
        category = {"gpu": GPU, "hardware": HARDWARE}.get(way, SYSTEM)
        lead = "顯示卡異常" if category == GPU else "核心異常"
        return _entry(ev, category, "live", WARM, f"live:{code:x}", f"{lead}，Windows 存了即時傾印：{hexcode(code)} {title}（{zh}）",
                      f"即時傾印 {hexcode(code)}", {"live": code}, time=dump_time(f.get("AttachedFiles"), ev.time))
    if name == "InPageError":
        code = _hex(p1)
        return _entry(ev, STORAGE, "disconnect" if code in DISCONNECTED else "io", HOT, f"inpage:{code:x}",
                      f"程式要從硬碟讀資料時失敗（{status_text(code)}）", "讀不到硬碟資料", {"status": code})
    return None                                   # 其他回報（程式當掉的重複、更新失敗…）不列


RULES: dict[tuple[str, int], Callable[[RawEvent, Mapping[str, str]], Entry | None]] = {
    ("Microsoft-Windows-Kernel-Power", 41): _reboot,
    ("Microsoft-Windows-Kernel-Power", 42): _marker("sleep", "進入睡眠"),
    ("Microsoft-Windows-Kernel-Power", 107): _marker("wake", "從睡眠醒來"),
    ("Microsoft-Windows-Kernel-Power", 86): _thermal,
    ("Microsoft-Windows-Kernel-Power", 87): _thermal,
    ("Microsoft-Windows-Kernel-Power", 88): _thermal,
    ("Microsoft-Windows-Kernel-General", 12): _marker("boot", "開機"),
    ("Microsoft-Windows-Kernel-General", 13): _marker("shutdown", "關機"),
    ("EventLog", 6008): _unexpected,
    ("Microsoft-Windows-WER-SystemErrorReporting", 1001): _bugcheck,
    ("Microsoft-Windows-Kernel-Boot", 29): _fast_startup,
    ("Display", 4101): _display_tdr,
    ("Microsoft-Windows-Resource-Exhaustion-Detector", 2004): _low_memory,
    ("Application Error", 1000): _app_crash,
    ("Application Error", 1005): _app_file,
    ("Application Hang", 1002): _app_hang,
    (".NET Runtime", 1026): _dotnet,
    ("Windows Error Reporting", 1001): _wer,
}
PROVIDER_RULES: dict[str, Callable[[RawEvent, Mapping[str, str]], Entry | None]] = {
    "nvlddmkm": _nvidia,
    **dict.fromkeys(GPU_DRIVERS[1:], _gpu_driver),
    "volmgr": _volmgr,
    "disk": _disk,
    "Ntfs": _ntfs,
    "Microsoft-Windows-Ntfs": _ntfs,
    **dict.fromkeys(CONTROLLERS, _controller),
    "Microsoft-Windows-WHEA-Logger": _whea,
    "Microsoft-Windows-Kernel-Processor-Power": _cpu_power,
}


def classify(ev: RawEvent, disks: Mapping[str, str] | None = None) -> Entry | None:
    """一筆事件 → Entry；不認得的回傳 None。disks：{磁碟編號: 型號}（從規格來，拿來把「硬碟 3」寫成型號）。"""
    rule = RULES.get((ev.provider, ev.event_id)) or PROVIDER_RULES.get(ev.provider)
    return rule(ev, disks or {}) if rule else None


# ---- 一件事的說明與建議（incidents.py 依這件事的種類挑） ----

EXPLAIN = {
    "fecs": "FECS 是 NVIDIA 顯示卡裡負責切換繪圖工作的小控制器，它的韌體出了致命錯誤。常見原因是驅動、時脈或供電不穩，或顯示卡本身。",
    "tdr": "TDR 是 Windows 發現顯示卡太久沒回應時，自動重設顯示卡驅動的機制。偶爾一次通常是程式或驅動的問題，常常發生就要排查顯示卡。",
    "flr": "PF FLR 是把整張顯示卡重設，比一般的 TDR 更重。",
    "live": "Windows 發現顯示卡異常，存了一份即時傾印，沒有重開機；畫面通常會黑一下再回來。",
    "disconnect": "Windows 突然找不到這顆硬碟，正在寫的資料可能沒存到。常見原因是 SATA 線或電源線鬆脫、接觸不良，或硬碟本身。",
    "reset": "控制器等不到硬碟回應，就把它重設。偶爾一次通常不要緊。",
    "disk_number": "「硬碟 3」是 Windows 給的編號；換過硬碟或插拔外接硬碟後，編號可能會變。",
    "whea": "WHEA 是 Windows 的硬體錯誤回報。「已修正」表示硬體自己修正了、系統沒受影響，但常常出現代表不穩。",
    "nocode": "電腦沒有正常關機就重新開機，也沒有留下藍屏代碼：可能是斷電、電源供應器保護、整台卡死，或按了重開機鍵。",
    "button": "Windows 記到電源鍵被按下，通常是畫面或整台卡住後強制關機。",
    "sleep": "睡眠、休眠或快速啟動的過程沒完成就重開機了。",
    "thermal": "溫度超過主機板設定的危險值，Windows 為了保護硬體自動關機或休眠。",
    "throttle": "Windows 記到 CPU 被系統韌體壓低速度；桌機上偶爾會因為電源設定誤報，常常出現再看溫度與供電。",
}
ADVICE = {
    "gpu": ["乾淨安裝顯示卡驅動（安裝時選「執行全新安裝」，或用 DDU 移除後再裝）",
            "檢查顯示卡的供電線與接頭有沒有插緊、電源的功率夠不夠",
            "排查期間只留一個會讀顯卡的監控程式，少一個變數",
            "還是會出錯就降頻測試（例如核心 −150 MHz、顯存 −500 MHz）；降頻後不再出錯，或怎樣都會出錯，就考慮送修"],
    "driver": ["看同一件事裡有沒有顯示卡、硬碟的錯誤，有的話先照那邊的建議排查",
               "更新或重裝最近更新過的驅動（顯示卡、晶片組、儲存控制器）"],
    "memory": ["先關掉記憶體超頻（XMP／DOCP）試試", "用 Windows 記憶體診斷或 MemTest86 測試記憶體"],
    "storage": ["重新插好或換一條那顆硬碟的 SATA 線和電源線", "打開 HWGlance 裡那顆硬碟的詳細資訊，看健康狀態",
                "重要資料先備份"],
    "hardware": ["取消 CPU 與記憶體的超頻（含 PBO、XMP），確認溫度與供電正常", "同一個元件一直出錯，可能是那個零件本身有問題"],
    "power": ["更新晶片組與顯示卡驅動", "試試關閉快速啟動（控制台 → 電源選項）"],
    "system": ["看看同一段時間還有沒有其他事件，再決定往哪個方向查"],
    "nocode": ["看看重開前有沒有其他事件（顯示卡、硬碟）", "排查電源：功率夠不夠、線材有沒有插好；確認溫度與超頻設定"],
    "button": ["看看強制關機前發生了什麼：有沒有顯示卡或硬碟的錯誤"],
    "thermal": ["清灰塵、檢查風扇和散熱器有沒有轉、機殼通風夠不夠"],
    "reset": ["偶爾一次可以先觀察；常常出現就檢查線材、更新晶片組或儲存控制器驅動"],
    "predict": ["盡快備份這顆硬碟的資料，考慮換掉"],
    "whea_corrected": ["偶爾一次可以先觀察；同一個元件常常出現，就取消超頻並檢查溫度與供電"],
    "whea_fatal": ["取消所有超頻、確認溫度；持續發生就送修 CPU、主機板或記憶體"],
    "throttle": ["看 HWGlance 裡 CPU 的溫度與功耗；電源計劃設成「平衡」或「高效能」再觀察"],
    "lowmem": ["展開原始記錄，看是哪個程式用掉最多記憶體", "常常發生就把虛擬記憶體設大一點，或加記憶體"],
    "dumpconfig": ["檢查「系統內容 → 進階 → 啟動及修復」的傾印檔設定，以及系統碟還有沒有空間"],
}
