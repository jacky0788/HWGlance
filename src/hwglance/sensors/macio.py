"""macOS 的低階讀取（ctypes，不加套件）：SMC、HID 溫度感測器、每個核心的 CPU 時間、GPU 使用率、記憶體、系統資訊。

全部唯讀、一般權限就能用（2026-11-11 在 MacBook Pro 14 吋 M6、macOS 27.2 驗證）。SMC、HID 溫度、IOAccelerator 的統計
是 Apple 沒有公開文件的介面（Stats、iStat Menus 也是讀這些），macOS 改版可能要跟著修。
只有 sensors/mac.py 用這個檔；函式庫在第一次用到時才載入，所以 Windows 照樣 import 得起來。測試不呼叫這裡（用假的讀數）。
"""
from __future__ import annotations

import ctypes
import functools
import json
import os
import struct
import subprocess
import time

vp = ctypes.c_void_p
u32 = ctypes.c_uint32
UTF8 = 0x08000100                       # kCFStringEncodingUTF8
CF_NUMBER_DOUBLE = 13                   # kCFNumberDoubleType
MAIN_PORT = 0                           # kIOMainPortDefault
SMC_CALL = 2                            # kSMCHandleYPCEvent
SMC_READ, SMC_INDEX, SMC_INFO = 5, 8, 9  # 讀值、第幾個鍵、鍵的型別與長度
TEMPERATURE_EVENT = 15                  # kIOHIDEventTypeTemperature
CPU_LOAD_INFO = 2                       # PROCESSOR_CPU_LOAD_INFO：每個核心 user、system、idle、nice 的時間
VM_INFO64, VM_INFO64_COUNT = 4, 38      # HOST_VM_INFO64、sizeof(vm_statistics64) / 4


@functools.cache
def _cf():
    cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    cf.CFStringCreateWithCString.restype = vp
    cf.CFStringCreateWithCString.argtypes = [vp, ctypes.c_char_p, u32]
    cf.CFStringGetCString.restype = ctypes.c_bool
    cf.CFStringGetCString.argtypes = [vp, ctypes.c_char_p, ctypes.c_long, u32]
    cf.CFNumberCreate.restype = vp
    cf.CFNumberCreate.argtypes = [vp, ctypes.c_long, vp]
    cf.CFNumberGetValue.restype = ctypes.c_bool
    cf.CFNumberGetValue.argtypes = [vp, ctypes.c_long, vp]
    cf.CFDictionaryCreate.restype = vp
    cf.CFDictionaryCreate.argtypes = [vp, vp, vp, ctypes.c_long, vp, vp]
    cf.CFDictionaryGetValue.restype = vp
    cf.CFDictionaryGetValue.argtypes = [vp, vp]
    cf.CFArrayGetCount.restype = ctypes.c_long
    cf.CFArrayGetCount.argtypes = [vp]
    cf.CFArrayGetValueAtIndex.restype = vp
    cf.CFArrayGetValueAtIndex.argtypes = [vp, ctypes.c_long]
    cf.CFGetTypeID.restype = ctypes.c_ulong
    cf.CFGetTypeID.argtypes = [vp]
    cf.CFNumberGetTypeID.restype = ctypes.c_ulong
    cf.CFStringGetTypeID.restype = ctypes.c_ulong
    cf.CFRetain.restype = vp
    cf.CFRetain.argtypes = [vp]
    cf.CFRelease.argtypes = [vp]
    return cf


@functools.cache
def _iokit():
    io = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/IOKit.framework/IOKit")
    io.IOServiceMatching.restype = vp
    io.IOServiceMatching.argtypes = [ctypes.c_char_p]
    io.IOServiceGetMatchingService.restype = u32
    io.IOServiceGetMatchingService.argtypes = [u32, vp]
    io.IOServiceGetMatchingServices.argtypes = [u32, vp, ctypes.POINTER(u32)]
    io.IOIteratorNext.restype = u32
    io.IOIteratorNext.argtypes = [u32]
    io.IOObjectRelease.argtypes = [u32]
    io.IOServiceOpen.argtypes = [u32, u32, u32, ctypes.POINTER(u32)]
    io.IOServiceClose.argtypes = [u32]
    io.IOConnectCallStructMethod.argtypes = [u32, u32, vp, ctypes.c_size_t, vp, ctypes.POINTER(ctypes.c_size_t)]
    io.IORegistryEntryCreateCFProperty.restype = vp
    io.IORegistryEntryCreateCFProperty.argtypes = [u32, vp, vp, u32]
    io.IOHIDEventSystemClientCreate.restype = vp
    io.IOHIDEventSystemClientCreate.argtypes = [vp]
    io.IOHIDEventSystemClientSetMatching.argtypes = [vp, vp]
    io.IOHIDEventSystemClientCopyServices.restype = vp
    io.IOHIDEventSystemClientCopyServices.argtypes = [vp]
    io.IOHIDServiceClientCopyProperty.restype = vp
    io.IOHIDServiceClientCopyProperty.argtypes = [vp, vp]
    io.IOHIDServiceClientCopyEvent.restype = vp
    io.IOHIDServiceClientCopyEvent.argtypes = [vp, ctypes.c_int64, ctypes.c_int32, ctypes.c_int64]
    io.IOHIDEventGetFloatValue.restype = ctypes.c_double
    io.IOHIDEventGetFloatValue.argtypes = [vp, ctypes.c_int32]
    return io


@functools.cache
def _libc():
    c = ctypes.cdll.LoadLibrary("/usr/lib/libSystem.B.dylib")
    c.mach_host_self.restype = u32
    c.host_processor_info.argtypes = [u32, ctypes.c_int, ctypes.POINTER(u32), ctypes.POINTER(vp), ctypes.POINTER(u32)]
    c.host_statistics64.argtypes = [u32, ctypes.c_int, vp, ctypes.POINTER(u32)]
    c.vm_deallocate.argtypes = [u32, vp, ctypes.c_size_t]
    c.sysctlbyname.argtypes = [ctypes.c_char_p, vp, ctypes.POINTER(ctypes.c_size_t), vp, ctypes.c_size_t]
    return c


@functools.cache
def _host() -> int:
    """mach_host_self() 每叫一次就多一個 port 參照，只叫一次。"""
    return _libc().mach_host_self()


def _task() -> int:
    return u32.in_dll(_libc(), "mach_task_self_").value


# ---- CoreFoundation 小工具 ----

def _cfstr(text: str) -> int:
    """要自己 CFRelease。"""
    return _cf().CFStringCreateWithCString(None, text.encode(), UTF8)


def _to_str(ref: int | None) -> str | None:
    if not ref or _cf().CFGetTypeID(ref) != _cf().CFStringGetTypeID():
        return None
    buf = ctypes.create_string_buffer(512)
    return buf.value.decode(errors="replace") if _cf().CFStringGetCString(ref, buf, 512, UTF8) else None


def _to_number(ref: int | None) -> float | None:
    if not ref or _cf().CFGetTypeID(ref) != _cf().CFNumberGetTypeID():
        return None
    out = ctypes.c_double()
    return out.value if _cf().CFNumberGetValue(ref, CF_NUMBER_DOUBLE, ctypes.byref(out)) else None


def _dict_value(d: int, key: str) -> int | None:
    k = _cfstr(key)
    try:
        return _cf().CFDictionaryGetValue(d, k)
    finally:
        _cf().CFRelease(k)


def _number(n: int) -> int:
    v = ctypes.c_int32(n)
    return _cf().CFNumberCreate(None, 3, ctypes.byref(v))       # kCFNumberSInt32Type


# ---- SMC（系統管理控制器）：溫度、風扇 ----

class _Vers(ctypes.Structure):
    _fields_ = [("major", ctypes.c_char), ("minor", ctypes.c_char), ("build", ctypes.c_char),
                ("reserved", ctypes.c_char), ("release", ctypes.c_uint16)]


class _PLimit(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint16), ("length", ctypes.c_uint16), ("cpu", u32), ("gpu", u32), ("mem", u32)]


class _KeyInfo(ctypes.Structure):
    _fields_ = [("dataSize", u32), ("dataType", u32), ("dataAttributes", ctypes.c_uint8)]


class _KeyData(ctypes.Structure):
    """SMCKeyData_t（80 位元組），跟 smcFanControl、Stats 用的同一個。"""
    _fields_ = [("key", u32), ("vers", _Vers), ("pLimitData", _PLimit), ("keyInfo", _KeyInfo),
                ("result", ctypes.c_uint8), ("status", ctypes.c_uint8), ("data8", ctypes.c_uint8),
                ("data32", u32), ("bytes", ctypes.c_uint8 * 32)]


def fourcc(text: str) -> int:
    return struct.unpack(">I", text.encode("ascii"))[0]


def unfourcc(value: int) -> str:
    return struct.pack(">I", value).decode("ascii", errors="replace")


def decode(dtype: str, raw: bytes) -> float | None:
    """SMC 的值 → 數字。Apple 晶片的溫度、風扇是 flt（小端序 float）；Intel Mac 時代的 sp78、fpe2 是大端序定點數。
    認不得的型別回傳 None（不猜）。"""
    try:
        if dtype == "flt ":
            return struct.unpack("<f", raw[:4])[0]
        if dtype == "sp78":
            return struct.unpack(">h", raw[:2])[0] / 256
        if dtype == "fpe2":
            return struct.unpack(">H", raw[:2])[0] / 4
        if dtype == "ui8 ":
            return float(raw[0])
        if dtype == "ui16":
            return float(struct.unpack(">H", raw[:2])[0])
        if dtype == "ui32":
            return float(struct.unpack(">I", raw[:4])[0])
    except (struct.error, IndexError):
        return None
    return None


class Smc:
    """AppleSMC 的使用者端連線。只送讀取的指令（讀值、第幾個鍵、鍵的資訊），不寫任何鍵。"""

    def __init__(self) -> None:
        self._conn = 0
        self._info: dict[str, tuple[str, int]] = {}      # 鍵 → (型別, 長度)

    def open(self) -> None:
        io = _iokit()
        service = io.IOServiceGetMatchingService(MAIN_PORT, io.IOServiceMatching(b"AppleSMC"))
        if not service:
            raise OSError("找不到 AppleSMC")
        conn = u32()
        try:
            kr = io.IOServiceOpen(service, _task(), 0, ctypes.byref(conn))
        finally:
            io.IOObjectRelease(service)
        if kr != 0:
            raise OSError(f"打不開 AppleSMC（{kr:#x}）")
        self._conn = conn.value

    def close(self) -> None:
        if self._conn:
            _iokit().IOServiceClose(self._conn)
            self._conn = 0

    def _call(self, request: _KeyData) -> _KeyData:
        out = _KeyData()
        size = ctypes.c_size_t(ctypes.sizeof(out))
        kr = _iokit().IOConnectCallStructMethod(self._conn, SMC_CALL, ctypes.byref(request), ctypes.sizeof(request),
                                                ctypes.byref(out), ctypes.byref(size))
        if kr != 0:
            raise OSError(f"SMC 呼叫失敗（{kr:#x}）")
        return out

    def _key_info(self, key: str) -> tuple[str, int]:
        hit = self._info.get(key)
        if hit is None:
            info = self._call(_KeyData(key=fourcc(key), data8=SMC_INFO)).keyInfo
            hit = self._info[key] = (unfourcc(info.dataType), int(info.dataSize))
        return hit

    def keys(self, prefixes: tuple[str, ...]) -> list[str]:
        """所有鍵裡，開頭是 prefixes 之一的（這台 3209 個鍵，列一次約 0.1 秒）。"""
        count = self.read("#KEY")
        found = []
        for i in range(int(count or 0)):
            try:
                key = unfourcc(self._call(_KeyData(data8=SMC_INDEX, data32=i)).key)
            except OSError:
                continue
            if key.startswith(prefixes):
                found.append(key)
        return found

    def read(self, key: str) -> float | None:
        try:
            dtype, size = self._key_info(key)
            request = _KeyData(key=fourcc(key), data8=SMC_READ)
            request.keyInfo.dataSize = size
            out = self._call(request)
        except OSError:
            return None
        if out.result != 0 or size <= 0:
            return None
        return decode(dtype, bytes(out.bytes[:size]))


# ---- HID 溫度感測器（只拿 SSD 的「NAND」那幾個；其他溫度 SMC 都有，而且有分哪一塊） ----

class HidTemps:
    def __init__(self, prefix: str = "NAND") -> None:
        self.prefix = prefix
        self._client = None
        self._services = None
        self._picked: list[tuple[str, int]] = []         # (名稱, 服務)

    def open(self) -> None:
        cf, io = _cf(), _iokit()
        keys = (vp * 2)(_cfstr("PrimaryUsagePage"), _cfstr("PrimaryUsage"))
        values = (vp * 2)(_number(0xFF00), _number(5))   # Apple 自訂頁 / 溫度感測器
        key_cb = ctypes.addressof(ctypes.c_char.in_dll(cf, "kCFTypeDictionaryKeyCallBacks"))
        value_cb = ctypes.addressof(ctypes.c_char.in_dll(cf, "kCFTypeDictionaryValueCallBacks"))
        match = cf.CFDictionaryCreate(None, keys, values, 2, key_cb, value_cb)
        for ref in (*keys, *values):
            cf.CFRelease(ref)
        self._client = io.IOHIDEventSystemClientCreate(None)
        io.IOHIDEventSystemClientSetMatching(self._client, match)
        cf.CFRelease(match)
        self._services = io.IOHIDEventSystemClientCopyServices(self._client)
        product = _cfstr("Product")
        try:
            for i in range(cf.CFArrayGetCount(self._services) if self._services else 0):
                service = cf.CFArrayGetValueAtIndex(self._services, i)
                ref = io.IOHIDServiceClientCopyProperty(service, product)
                name = _to_str(ref) or ""
                if ref:
                    cf.CFRelease(ref)
                if name.startswith(self.prefix):
                    self._picked.append((name, service))
        finally:
            cf.CFRelease(product)

    def read(self) -> dict[str, float]:
        io, out = _iokit(), {}
        for name, service in self._picked:
            event = io.IOHIDServiceClientCopyEvent(service, TEMPERATURE_EVENT, 0, 0)
            if not event:
                continue
            out[name] = io.IOHIDEventGetFloatValue(event, TEMPERATURE_EVENT << 16)
            _cf().CFRelease(event)
        return out

    def close(self) -> None:
        for ref in (self._services, self._client):
            if ref:
                _cf().CFRelease(ref)
        self._services = self._client = None
        self._picked = []


# ---- CPU、GPU、記憶體 ----

def cpu_ticks() -> list[tuple[int, int, int, int]]:
    """每個邏輯核心開機以來的 (user, system, idle, nice) 時間（tick）。兩次相減才是使用率。"""
    c = _libc()
    count, info, info_count = u32(), vp(), u32()
    if c.host_processor_info(_host(), CPU_LOAD_INFO, ctypes.byref(count), ctypes.byref(info), ctypes.byref(info_count)):
        raise OSError("讀不到 CPU 使用時間")
    try:
        arr = ctypes.cast(info, ctypes.POINTER(u32))
        return [tuple(arr[i * 4 + j] for j in range(4)) for i in range(count.value)]   # type: ignore[misc]
    finally:
        c.vm_deallocate(_task(), info, info_count.value * 4)


class Accelerator:
    """GPU（IOAccelerator）：PerformanceStatistics 的使用率，跟「活動監視器」的 GPU 歷史記錄同一個來源。"""

    def __init__(self) -> None:
        self._entry = 0

    def open(self) -> None:
        io = _iokit()
        it = u32()
        if io.IOServiceGetMatchingServices(MAIN_PORT, io.IOServiceMatching(b"IOAccelerator"), ctypes.byref(it)):
            raise OSError("找不到 GPU（IOAccelerator）")
        self._entry = io.IOIteratorNext(it.value)
        io.IOObjectRelease(it.value)
        if not self._entry:
            raise OSError("找不到 GPU（IOAccelerator）")

    def _property(self, name: str) -> int | None:
        key = _cfstr(name)
        try:
            return _iokit().IORegistryEntryCreateCFProperty(self._entry, key, None, 0)
        finally:
            _cf().CFRelease(key)

    def utilization(self) -> float | None:
        stats = self._property("PerformanceStatistics")
        if not stats:
            return None
        try:
            return _to_number(_dict_value(stats, "Device Utilization %"))
        finally:
            _cf().CFRelease(stats)

    def cores(self) -> int | None:
        ref = self._property("gpu-core-count")
        if not ref:
            return None
        try:
            value = _to_number(ref)
            return int(value) if value else None
        finally:
            _cf().CFRelease(ref)

    def close(self) -> None:
        if self._entry:
            _iokit().IOObjectRelease(self._entry)
            self._entry = 0


class _VmStats64(ctypes.Structure):
    _fields_ = [("free_count", u32), ("active_count", u32), ("inactive_count", u32), ("wire_count", u32),
                ("zero_fill_count", ctypes.c_uint64), ("reactivations", ctypes.c_uint64), ("pageins", ctypes.c_uint64),
                ("pageouts", ctypes.c_uint64), ("faults", ctypes.c_uint64), ("cow_faults", ctypes.c_uint64),
                ("lookups", ctypes.c_uint64), ("hits", ctypes.c_uint64), ("purges", ctypes.c_uint64),
                ("purgeable_count", u32), ("speculative_count", u32),
                ("decompressions", ctypes.c_uint64), ("compressions", ctypes.c_uint64), ("swapins", ctypes.c_uint64),
                ("swapouts", ctypes.c_uint64), ("compressor_page_count", u32), ("throttled_count", u32),
                ("external_page_count", u32), ("internal_page_count", u32),
                ("total_uncompressed_pages_in_compressor", ctypes.c_uint64)]


def memory_pages() -> dict[str, int]:
    """記憶體各部分的頁數（vm_statistics64）＋頁大小。mac.memory_used 算成「活動監視器」的「已使用記憶體」。"""
    stats = _VmStats64()
    count = u32(VM_INFO64_COUNT)
    if _libc().host_statistics64(_host(), VM_INFO64, ctypes.byref(stats), ctypes.byref(count)):
        raise OSError("讀不到記憶體統計")
    return {"internal": stats.internal_page_count, "purgeable": stats.purgeable_count, "wired": stats.wire_count,
            "compressed": stats.compressor_page_count, "external": stats.external_page_count,   # 快取的檔案
            "stored": stats.total_uncompressed_pages_in_compressor,                             # 壓縮前有多少
            "page": os.sysconf("SC_PAGE_SIZE")}


def sysctl_int(name: str) -> int | None:
    value, size = ctypes.c_int64(0), ctypes.c_size_t(8)
    if _libc().sysctlbyname(name.encode(), ctypes.byref(value), ctypes.byref(size), None, 0):
        return None
    return value.value if size.value == 8 else ctypes.c_int32.from_buffer(value).value


def sysctl_str(name: str) -> str | None:
    size = ctypes.c_size_t(0)
    if _libc().sysctlbyname(name.encode(), None, ctypes.byref(size), None, 0) or not size.value:
        return None
    buf = ctypes.create_string_buffer(size.value)
    if _libc().sysctlbyname(name.encode(), buf, ctypes.byref(size), None, 0):
        return None
    return buf.value.decode(errors="replace")


def core_levels() -> list[tuple[str, int]]:
    """CPU 的核心種類（快的在前）：[(名稱, 幾個核心)]，例如 M6 是 Super 2、Performance 4、Efficiency 6。"""
    out = []
    for i in range(int(sysctl_int("hw.nperflevels") or 0)):
        name = sysctl_str(f"hw.perflevel{i}.name") or f"#{i}"
        n = sysctl_int(f"hw.perflevel{i}.logicalcpu")
        if n:
            out.append((name, int(n)))
    return out


def profile(data_type: str) -> list[dict]:
    """system_profiler 的 JSON（SPHardwareDataType 約 0.2 秒）。讀不到就空的。"""
    try:
        out = subprocess.run(["/usr/sbin/system_profiler", data_type, "-json"], capture_output=True, timeout=15,
                             check=False).stdout
        return json.loads(out or b"{}").get(data_type) or []
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []


def volume_space(path: str = "/System/Volumes/Data") -> tuple[int, int]:
    """(可用位元組, 總位元組)：使用者資料所在的 APFS 宗卷（容量是整個容器的，跟「關於這台 Mac」一樣）。"""
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize, st.f_blocks * st.f_frsize


class _TimeVal(ctypes.Structure):
    _fields_ = [("sec", ctypes.c_int64), ("usec", ctypes.c_int32)]


def boot_time() -> float | None:
    """這次開機的時間（sysctl kern.boottime，epoch 秒）；events/mac.py 用來記每次開機的上次關機原因。"""
    tv, size = _TimeVal(), ctypes.c_size_t(ctypes.sizeof(_TimeVal))
    if _libc().sysctlbyname(b"kern.boottime", ctypes.byref(tv), ctypes.byref(size), None, 0):
        return None
    return tv.sec + tv.usec / 1e6 if tv.sec > 0 else None


# ---- IOReport（Apple 沒公開的框架，一般權限就能用）：時脈（各效能狀態的停留時間）與 GPU 能量 ----
# 2026-11-11 在 M6 查的：CPU Stats/CPU Core Performance States 每顆核心一個通道（EACC_ECPU0～5、PACC0_PCPU0～1、
# PACC0_MCPU2～5），狀態是 IDLE＋V0P6…（只是編號、沒有頻率）；頻率表在 IODeviceTree 的 pmgr 節點
# （voltage-states1-sram＝節能核 7 階、voltage-states5-sram＝效能／超級核 20 階，單位 kHz；voltage-states9＝GPU，單位 Hz）。
# Energy Model 在 M6 只有 GPU Energy（nJ），沒有 CPU；PMP/Energy 是功率直方圖，跟 GPU Energy 對不上，不用。

IOREPORT_GROUPS = (("Energy Model", None), ("CPU Stats", "CPU Core Performance States"),
                   ("GPU Stats", "GPU Performance States"))
FREQ_TABLES = ("voltage-states1-sram", "voltage-states5-sram", "voltage-states9")


@functools.cache
def _ioreport():
    ior = ctypes.cdll.LoadLibrary("/usr/lib/libIOReport.dylib")
    for name, res, args in (
            ("IOReportCopyChannelsInGroup", vp, [vp, vp, ctypes.c_uint64, ctypes.c_uint64, ctypes.c_uint64]),
            ("IOReportMergeChannels", None, [vp, vp, vp]),
            ("IOReportCreateSubscription", vp, [vp, vp, ctypes.POINTER(vp), ctypes.c_uint64, vp]),
            ("IOReportCreateSamples", vp, [vp, vp, vp]),
            ("IOReportCreateSamplesDelta", vp, [vp, vp, vp]),
            ("IOReportChannelGetGroup", vp, [vp]),
            ("IOReportChannelGetChannelName", vp, [vp]),
            ("IOReportChannelGetUnitLabel", vp, [vp]),
            ("IOReportChannelGetFormat", ctypes.c_uint8, [vp]),
            ("IOReportSimpleGetIntegerValue", ctypes.c_int64, [vp, ctypes.c_int32]),
            ("IOReportStateGetCount", ctypes.c_int32, [vp]),
            ("IOReportStateGetNameForIndex", vp, [vp, ctypes.c_int32]),
            ("IOReportStateGetResidency", ctypes.c_int64, [vp, ctypes.c_int32])):
        f = getattr(ior, name)
        f.restype, f.argtypes = res, args
    cf = _cf()
    cf.CFDictionaryGetCount.restype = ctypes.c_long
    cf.CFDictionaryGetCount.argtypes = [vp]
    cf.CFDictionaryCreateMutableCopy.restype = vp
    cf.CFDictionaryCreateMutableCopy.argtypes = [vp, ctypes.c_long, vp]
    return ior


def freq_tables() -> dict[str, list[int]]:
    """IODeviceTree 裡的頻率表：{鍵: [每一階的頻率（原始單位：CPU 是 kHz、GPU 是 Hz）]}。ioreg 讀一次約 0.1 秒。"""
    import plistlib
    try:
        raw = subprocess.run(["/usr/sbin/ioreg", "-a", "-p", "IODeviceTree", "-r", "-k", FREQ_TABLES[1], "-d", "1"],
                             capture_output=True, timeout=15, check=False).stdout
        nodes = plistlib.loads(raw) if raw else []
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}
    node = nodes[0] if isinstance(nodes, list) and nodes else nodes if isinstance(nodes, dict) else {}
    out = {}
    for key in FREQ_TABLES:
        data = node.get(key)
        if isinstance(data, bytes) and len(data) >= 8:
            out[key] = [struct.unpack_from("<I", data, i * 8)[0] for i in range(len(data) // 8)]
    return out


class IOReport:
    """訂閱上面三組通道；sample() 回傳跟上一次的差：{"dt": 秒, "energy": {通道: 焦耳}, "states": {通道: [(狀態, 停留)]}}。"""

    def __init__(self) -> None:
        self._sub = self._channels = self._last = None
        self._t = 0.0

    def open(self) -> None:
        ior, cf = _ioreport(), _cf()
        merged = None
        for group, sub in IOREPORT_GROUPS:
            g, s = _cfstr(group), _cfstr(sub) if sub else None
            chans = ior.IOReportCopyChannelsInGroup(g, s, 0, 0, 0)
            for ref in (g, s):
                if ref:
                    cf.CFRelease(ref)
            if not chans:
                continue
            if merged is None:
                merged = chans
            else:
                ior.IOReportMergeChannels(merged, chans, None)
                cf.CFRelease(chans)
        if merged is None:
            raise OSError("IOReport 沒有時脈與能量的通道")
        mutable = cf.CFDictionaryCreateMutableCopy(None, cf.CFDictionaryGetCount(merged), merged)
        cf.CFRelease(merged)
        subscribed = vp()
        self._sub = ior.IOReportCreateSubscription(None, mutable, ctypes.byref(subscribed), 0, None)
        cf.CFRelease(mutable)
        if not self._sub:
            raise OSError("訂閱 IOReport 失敗")
        self._channels = subscribed.value
        self._last = ior.IOReportCreateSamples(self._sub, self._channels, None)
        self._t = time.monotonic()

    def sample(self) -> dict | None:
        ior, cf = _ioreport(), _cf()
        now = ior.IOReportCreateSamples(self._sub, self._channels, None)
        t = time.monotonic()
        if not now:
            return None
        prev, self._last = self._last, now
        dt, self._t = t - self._t, t
        if not prev:
            return None
        delta = ior.IOReportCreateSamplesDelta(prev, now, None)
        cf.CFRelease(prev)
        if not delta:
            return None
        try:
            return {"dt": dt, **self._parse(delta)}
        finally:
            cf.CFRelease(delta)

    @staticmethod
    def _parse(delta: int) -> dict:
        ior, cf = _ioreport(), _cf()
        energy: dict[str, float] = {}
        states: dict[str, list[tuple[str, int]]] = {}
        arr = _dict_value(delta, "IOReportChannels")
        scale = {"mJ": 1e-3, "uJ": 1e-6, "nJ": 1e-9}
        for i in range(cf.CFArrayGetCount(arr) if arr else 0):
            ch = cf.CFArrayGetValueAtIndex(arr, i)
            name = _to_str(ior.IOReportChannelGetChannelName(ch)) or ""
            group = _to_str(ior.IOReportChannelGetGroup(ch)) or ""
            if group == "Energy Model":
                unit = (_to_str(ior.IOReportChannelGetUnitLabel(ch)) or "").strip()
                value = ior.IOReportSimpleGetIntegerValue(ch, 0)
                if unit in scale and value >= 0:
                    energy[name] = value * scale[unit]
            elif ior.IOReportChannelGetFormat(ch) == 2:              # 狀態通道
                n = ior.IOReportStateGetCount(ch)
                states[name] = [(_to_str(ior.IOReportStateGetNameForIndex(ch, k)) or "", ior.IOReportStateGetResidency(ch, k))
                                for k in range(max(0, n))]
        return {"energy": energy, "states": states}

    def close(self) -> None:
        cf = _cf()
        for ref in (self._last, self._sub):
            if ref:
                cf.CFRelease(ref)
        self._last = self._sub = self._channels = None
