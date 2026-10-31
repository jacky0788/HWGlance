"""事件記錄的示範資料：照這台電腦 2026-06～10 的真實記錄（--demo、介面預覽、測試用）。

每一件都照真的那一次排：來源、編號、資料欄位、先後與間隔都一樣，時間改成相對於現在（例如「3 小時前」是
10/31 10:10 那次顯示卡錯誤、驅動自己恢復，「11 小時前」是 02:06 那次藍屏）。路徑與序號換成假的。
8/22 那顆硬碟斷線照樣有 6,400 筆分頁錯誤，事件頁才看得出合併成一行的樣子。這台沒有 WHEA 硬體錯誤，示範資料也沒有。
"""
from __future__ import annotations

import struct
import time
from collections.abc import Iterable, Mapping

from .model import RawEvent

D, H, M = 86400.0, 3600.0, 60.0
SYSTEM, APPLICATION = "System", "Application"
NV_DEVICE = "\\Device\\000000ec"
FECS = "Graphics FECS Exception: UCODE Fatal Error"
OLDEST = 142 * D                       # 這台的系統記錄（20 MB）最早到 6/11


def _systemtime(st: time.struct_time) -> bytes:
    return struct.pack("<8H", st.tm_year, st.tm_mon, (st.tm_wday + 1) % 7, st.tm_mday, st.tm_hour, st.tm_min, st.tm_sec, 0)


class _Builder:
    def __init__(self) -> None:
        self.events: list[RawEvent] = []
        self._record = 0

    def add(self, log: str, provider: str, event_id: int, level: int, t: float, *, fields: Mapping[str, str] | None = None,
            args: Iterable[str] | None = None, binary: str | None = None, message: str | None = None) -> None:
        self._record += 1
        fields = dict(fields or {})
        self.events.append(RawEvent(log, provider, event_id, level, t, self._record, fields,
                                    tuple(args) if args is not None else tuple(fields.values()), binary, message))

    # ---- 顯示卡 ----

    def nv(self, t: float, event_id: int, desc: str) -> None:
        self.add(SYSTEM, "nvlddmkm", event_id, 2, t, args=(NV_DEVICE, desc), binary="0000000002003000000000000D00AAC0")

    def tdrs(self, t: float, count: int, every: float, text: str = "GpuRcReset TDR occurred on GPUID:a00") -> None:
        for i in range(count):
            self.nv(t + i * every, 153, text)

    def live(self, t: float, code: str) -> None:
        stamp = time.strftime("%Y%m%d-%H%M", time.localtime(t))
        self.add(APPLICATION, "Windows Error Reporting", 1001, 4, t, fields={
            "Bucket": f"LKD_0x{code}_demo", "BucketType": "0", "EventName": "LiveKernelEvent", "Response": "無法使用",
            "CabId": "0", "P1": code, "P2": "aaaaaaaaaaaaaaaa", "P3": "", "P4": "", "P5": "", "P6": "10_0_26200",
            "P7": "", "P8": "", "P9": "", "P10": "",
            "AttachedFiles": f"\n\\\\?\\C:\\WINDOWS\\LiveKernelReports\\WATCHDOG\\WATCHDOG-{stamp}.dmp"})

    # ---- 程式 ----

    def app_crash(self, t: float, app: str, module: str, code: str, path: str) -> None:
        self.add(APPLICATION, "Application Error", 1000, 2, t, fields={
            "AppName": app, "AppVersion": "1.0.0.0", "AppTimeStamp": "00000000", "ModuleName": module,
            "ModuleVersion": "10.0.26100.9711", "ModuleTimeStamp": "00000000", "ExceptionCode": code,
            "FaultingOffset": "0000000000016a74", "ProcessId": "0x2e7c", "ProcessCreationTime": "0x1dd6899e8b669ab",
            "AppPath": path, "ModulePath": f"C:\\WINDOWS\\SYSTEM32\\{module}"},
            message=f"失敗的應用程式名稱： {app}，版本： 1.0.0.0\n錯誤模組名稱： {module}\n例外狀況代碼： 0x{code}\n"
                    f"失敗的應用程式路徑： {path}")

    def app_hang(self, t: float, app: str) -> None:
        self.add(APPLICATION, "Application Hang", 1002, 2, t, args=(app, "10.0.26100.9711", "1c34", "01dd6899"),
                 message=f"程式 {app} 版本 10.0.26100.9711 已停止與 Windows 互動，且已關閉。")

    def dotnet(self, t: float, app: str, exception: str, frames: Iterable[str]) -> None:
        body = (f"應用程式: {app}\nFramework 版本: v4.0.30319\n描述: 處理序已終止，因為有未處理的例外狀況。\n"
                f"例外狀況資訊: {exception}\n" + "".join(f"   於 {f}\n" for f in frames))
        self.add(APPLICATION, ".NET Runtime", 1026, 2, t, args=(body,), message=body.strip())

    # ---- 當機 ----

    def crash(self, alive: float, boot: float, *, bugcheck: int = 0, button: bool = False, sleep: bool = False) -> None:
        """一次當機：開機（Kernel-General 12）→ 傾印檔 → Kernel-Power 41 → 藍屏代碼 → 6008，間隔照真的。"""
        start = time.strftime("%Y-%m-%dT%H:%M:%S.5000000Z", time.gmtime(boot - 4))
        self.add(SYSTEM, "Microsoft-Windows-Kernel-General", 12, 4, boot - 4,
                 fields={"MajorVersion": "10", "MinorVersion": "0", "BuildVersion": "26100", "StartTime": start},
                 message=f"作業系統已經在系統時間 {start} 啟動。")
        if bugcheck:
            self.add(SYSTEM, "volmgr", 162, 2, boot, args=("\\Device\\HarddiskVolume3",), message="備份檔案生成成功。")
        self.add(SYSTEM, "Microsoft-Windows-Kernel-Power", 41, 1, boot, fields={
            "BugcheckCode": str(bugcheck), "BugcheckParameter1": "0x1" if bugcheck else "0x0",
            "BugcheckParameter2": "0x1e00" if bugcheck else "0x0", "BugcheckParameter3": "0x0", "BugcheckParameter4": "0x0",
            "SleepInProgress": "6" if sleep else "0", "PowerButtonTimestamp": "134317761909712855" if button else "0",
            "BootAppStatus": "3221225684" if sleep else "0", "Checkpoint": "16", "LongPowerButtonPressDetected": "false"},
            message="系統已重新開機，但未先正常關機。若系統停止回應、當機或電力意外中斷，就可能會造成此錯誤。")
        if bugcheck:
            params = f"0x{bugcheck:08x} (0x0000000000000001, 0x0000000000001e00, 0xfffff802ac1c53c8, 0x0000000000000000)"
            self.add(SYSTEM, "Microsoft-Windows-WER-SystemErrorReporting", 1001, 2, boot + 17,
                     fields={"param1": params, "param2": "C:\\WINDOWS\\Minidump\\demo-01.dmp", "param3": "demo"},
                     message=f"電腦已經在檢查錯誤後重新啟動。檢查的錯誤是: {params}。傾印的資料已儲存於: C:\\WINDOWS\\Minidump\\demo-01.dmp。")
        binary = (_systemtime(time.localtime(alive)) + _systemtime(time.gmtime(alive)) + bytes(16)).hex().upper()
        local = time.strftime("%Y/%m/%d %H:%M:%S", time.localtime(alive))
        self.add(SYSTEM, "EventLog", 6008, 2, boot + 18, args=(local[11:], local[:10], "", "", "91531", "", ""),
                 binary=binary, message=f"{local} 的系統上次發生意外的關機。")


def build(now: float) -> list[RawEvent]:
    b = _Builder()
    # 3 小時前（10/31 10:10）：FECS 致命錯誤 → TDR → 74 秒後整張卡重設 → 讀顯卡的程式跟著當掉，沒有藍屏
    t = now - 3 * H
    b.nv(t, 13, FECS)
    b.tdrs(t, 101, 0.75)
    b.app_crash(t + 0.5, "dwm.exe", "dwmcore.dll", "c0000005", "C:\\WINDOWS\\system32\\dwm.exe")
    b.nv(t + 74, 14, "GPU recovery action changed from 0x0 (None) to 0x1 (PF FLR)")
    b.app_crash(t + 80, "HWInfoSever.exe", "nvml.dll", "c0000005", "C:\\Users\\demo\\AppData\\Roaming\\HotSpot\\hwinfo\\HWInfoSever.exe")
    b.live(t + 81, "117")
    b.dotnet(t + 83, "pythonw.exe", "System.AccessViolationException",
             ("LibreHardwareMonitor.Interop.NvidiaML.NvmlDeviceGetPowerUsage(NvmlDevice)",
              "LibreHardwareMonitor.Hardware.Gpu.NvidiaGpu.Update()"))
    b.app_crash(t + 83, "pythonw.exe", "KERNELBASE.dll", "e0434352", "C:\\Python314\\pythonw.exe")
    b.app_crash(t + 84, "Cloudflare WARP.exe", "coremessaging.dll", "c0000005",
                "C:\\Program Files\\Cloudflare\\Cloudflare WARP\\Cloudflare WARP.exe")
    b.live(t + 98, "117")
    # 11 小時前（10/31 02:06）：FECS → TDR 上百次 → 卡死 → 藍屏 0x133
    t = now - 11 * H
    b.nv(t, 13, FECS)
    b.tdrs(t, 119, 0.78)
    b.crash(t + 94, t + 141, bugcheck=0x133)
    # 1 天 10 小時前（10/30 00:39）：同樣的模式
    t = now - 1 * D - 10 * H
    b.nv(t, 13, FECS)
    b.tdrs(t, 88, 1.1)
    b.crash(t + 103, t + 142, bugcheck=0x133)
    # 零星的顯示卡錯誤（驅動自己恢復）與單獨的程式當掉
    b.app_hang(now - 2 * D - 5 * H, "explorer.exe")
    t = now - 5 * D - 7 * H
    b.nv(t, 153, "Error occurred on GPUID: a00")
    b.live(t + 14, "141")
    b.app_crash(now - 6 * D - 2 * H, "Discord.exe", "ffmpeg.dll", "c0000005", "C:\\Users\\demo\\AppData\\Local\\Discord\\Discord.exe")
    t = now - 9 * D - 1 * H
    b.live(t, "1b8")
    b.live(t + 2, "1a8")
    t = now - 15 * D - 3 * H
    b.nv(t, 153, "Error occurred on GPUID: a00")
    b.app_crash(t + 6, "ZenlessZoneZero.exe", "UnityPlayer.dll", "c0000005", "D:\\Games\\ZenlessZoneZero.exe")
    # 70 天前（8/22 11:08～14:01）：G:（TOSHIBA DT02ABA400）斷線，錯誤一直記了三個鐘頭
    t = now - 70 * D
    b.add(SYSTEM, "storahci", 129, 3, t, args=("\\Device\\RaidPort1",), message="重設為裝置 \\Device\\RaidPort1 的指令已發出。")
    b.add(SYSTEM, "disk", 153, 3, t, args=("\\Device\\Harddisk3\\DR3", "0x2e2d8c00", "3"),
          message="對 Disk 3 (PDO 名稱: \\Device\\0000003a) 的邏輯區塊位址 0x2e2d8c00 進行的 IO 作業已重試。")
    span = 3 * H - 94
    for i in range(6400):
        b.add(SYSTEM, "disk", 51, 3, t + 94 + span * i / 6400, args=("\\Device\\Harddisk3\\DR3",),
              message="分頁操作時，裝置 \\Device\\Harddisk3\\DR3 發生錯誤。")
    for i in range(263):
        b.add(SYSTEM, "Ntfs", 50, 3, t + 94 + span * i / 263, args=("", "G:\\Projects\\notes\\server"),
              message="{延遲寫入失敗} Windows 無法儲存檔案 G:\\Projects\\notes\\server 的所有資料。資料已遺失。")
    for i in range(126):
        b.add(SYSTEM, "Microsoft-Windows-Ntfs", 140, 3, t + 95 + span * i / 126, fields={
            "VolumeIdLength": "2", "VolumeId": "G:", "DeviceNameLength": "24", "DeviceName": "\\Device\\HarddiskVolume12",
            "Error": "0xc000000e", "ProductIdLength": "18", "ProductId": "TOSHIBA DT02ABA400", "ProductRevision": "KQ000A",
            "DeviceSerialNumber": "DEMO000000", "BusType": "11"},
            message="系統無法將資料排清到交易記錄。損毀可能發生於 VolumeId: G:，DeviceName: \\Device\\HarddiskVolume12。\n"
                    "失敗狀態: 指定的裝置不存在。\n裝置型號: TOSHIBA DT02ABA400\n裝置序號: DEMO000000\n匯流排類型: SATA")
    for i in range(17):
        at = t + 40 * M + i * 120
        b.app_crash(at, "git.exe", "git.exe", "c0000006", "C:\\Program Files\\Git\\mingw64\\bin\\git.exe")
        b.add(APPLICATION, "Application Error", 1005, 2, at, fields={
            "FilePath": "G:\\Projects\\notes\\SPEC.md", "AppName": "Git for Windows", "StatusCode": "0xc000000e",
            "MediumType": "0x3"}, message="由於下列其中一個原因，Windows 無法存取檔案 G:\\Projects\\notes\\SPEC.md …")
        b.add(APPLICATION, "Windows Error Reporting", 1001, 4, at + 2, fields={
            "Bucket": "1374977797982279498", "BucketType": "5", "EventName": "InPageError", "Response": "無法使用",
            "CabId": "0", "P1": "c000000e", "P2": "00000003"})
    # 同一天晚上（8/22 21:50）：DirectX 核心記下異常 → 藍屏 0x133
    t = now - 70 * D + 10 * H + 42 * M
    b.live(t, "193")
    b.live(t + 17, "193")
    b.crash(t + 37, t + 105, bugcheck=0x133)
    # 71 天前（8/21）：按電源鍵強制關機
    t = now - 71 * D
    b.crash(t, t + 21 * M, button=True)
    # 84 天前（8/8）：零星錯誤 → 3 分鐘後 FECS → 藍屏 0x133（6008 的最後運作時間比真的當機早）
    t = now - 84 * D
    b.nv(t, 153, "Error occurred on GPUID: a00")
    b.live(t + 7, "141")
    b.nv(t + 185, 13, FECS)
    b.tdrs(t + 185, 95, 1.0)
    b.crash(t + 115, t + 329, bugcheck=0x133)
    # 89 天前（8/2）：沒有藍屏代碼的重開
    t = now - 89 * D
    b.crash(t, t + 37 * M)
    # 96 天前（7/27）：E: 斷線；97 天前（7/26）：USB 硬碟被重設
    t = now - 96 * D
    b.add(SYSTEM, "Microsoft-Windows-Ntfs", 140, 3, t, fields={
        "VolumeId": "E:", "DeviceName": "\\Device\\HarddiskVolume9", "Error": "0xc000000e", "ProductId": "DT01ACA200"})
    b.add(SYSTEM, "Ntfs", 50, 3, t + 1, args=("", ""))
    t = now - 97 * D
    for i in range(6):
        b.add(SYSTEM, "UASPStor", 129, 3, t + i * 30, args=("\\Device\\RaidPort10",),
              message="重設為裝置 \\Device\\RaidPort10 的指令已發出。")
    # 101 天前（7/22）：快速啟動恢復失敗（關機 19 小時後才開機）
    t = now - 101 * D
    b.add(SYSTEM, "Microsoft-Windows-Kernel-Boot", 29, 2, t + 19 * H - 4,
          fields={"FailureStatus": "3221225684", "FailureMsg": "A fatal error occurred processing the restoration data."},
          message="Windows 無法執行快速啟動，錯誤狀態為 0xC00000D4。")
    b.crash(t, t + 19 * H, sleep=True)
    # 119 天前（7/4）：顯示卡出錯、畫面一直當 → 沒有藍屏代碼的重開（6008 早了 38 分鐘）
    t = now - 119 * D
    b.nv(t, 153, "Error occurred on GPUID: a00")
    b.nv(t + 1, 13, FECS)
    b.nv(t + 1, 14, "GPU recovery action changed from 0x0 (None) to 0x1 (GPU Reset Required)")
    b.tdrs(t + 4, 6, 1.0)
    b.live(t + 9, "141")
    for i in range(8):
        b.app_crash(t + 37 + i * 4, "dwm.exe", "udwm.dll", "c0000005", "C:\\WINDOWS\\system32\\dwm.exe")
    b.app_hang(t + 57, "explorer.exe")
    b.crash(t - 36 * M, t + 196)
    # 136 天前（6/17）：FECS → TDR → 藍屏 0x133（6008 早了 35 分鐘）
    t = now - 136 * D
    b.nv(t, 13, FECS)
    b.tdrs(t, 66, 0.2)
    b.nv(t + 1, 14, "GPU recovery action changed from 0x0 (None) to 0x1 (GPU Reset Required)")
    b.live(t + 11, "117")
    b.crash(t - 35 * M, t + 140, bugcheck=0x133)
    # 開關機與睡眠：最近兩週每天一輪
    for d in range(1, 15):
        day = now - d * D
        b.add(SYSTEM, "Microsoft-Windows-Kernel-Power", 107, 4, day - 9 * H, message="系統已從睡眠狀態繼續。")
        b.add(SYSTEM, "Microsoft-Windows-Kernel-Power", 42, 4, day - 2 * H, message="系統正在進入睡眠狀態。")
    return sorted(b.events, key=lambda e: e.time, reverse=True)


class DemoEventLog:
    """跟 reader.WindowsEventLog 一樣的介面；now 固定的話每次結果都一樣（測試用）。"""

    kind = "demo"

    def __init__(self, now: float | None = None) -> None:
        self._now = now
        self._cache: tuple[float, list[RawEvent]] | None = None

    def _events(self) -> tuple[float, list[RawEvent]]:
        now = self._now if self._now is not None else time.time()
        if self._cache is None or abs(self._cache[0] - now) > 60:
            self._cache = (now, build(now))
        return self._cache

    def read(self, channel: str, providers: Mapping[str, Iterable[int] | None], days: float | None) -> list[RawEvent]:
        now, events = self._events()
        since = now - days * D if days else None
        wanted = {p: (None if ids is None else frozenset(ids)) for p, ids in providers.items()}
        out = []
        for e in events:
            if e.log != channel or e.provider not in wanted or (since is not None and e.time < since):
                continue
            ids = wanted[e.provider]
            if (ids is None and e.level in (1, 2, 3)) or (ids is not None and e.event_id in ids):
                out.append(e)
        return out

    def oldest(self, channel: str) -> float | None:
        return self._events()[0] - OLDEST
