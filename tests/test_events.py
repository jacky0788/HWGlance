"""事件記錄：解析、每種事件的解讀、歸成一件件事、快取。用示範資料（照這台 2026-06～10 的真實記錄）與假事件，
不讀真的 Windows 事件記錄。"""
from __future__ import annotations

import time
from calendar import timegm

import pytest

from hwglance.events import catalog
from hwglance.events.catalog import classify, describe_bugcheck, dump_time, systemtime
from hwglance.events.demo import DemoEventLog
from hwglance.events.incidents import report
from hwglance.events.model import RawEvent
from hwglance.events.reader import build_query, parse_event_xml, parse_time
from hwglance.events.service import EventService

NOW = 1_793_420_000.0
DISKS = {"3": "TOSHIBA DT02ABA400", "5": "KIOXIA-EXCERIA G2 SSD"}
NS = 'xmlns="http://schemas.microsoft.com/win/2004/08/events/event"'


def ev(provider: str, event_id: int, *, level: int = 2, t: float = NOW, log: str = "System", fields=None, args=(),
       binary=None, message=None) -> RawEvent:
    fields = dict(fields or {})
    return RawEvent(log, provider, event_id, level, t, 1, fields, tuple(args) or tuple(fields.values()), binary, message)


def demo_report(days: int | None) -> dict:
    return EventService(DemoEventLog(now=NOW), lambda: DISKS, clock=lambda: NOW).report(days)


# ---- 讀事件記錄 ----

def test_parse_event_xml_named_data():
    xml = (f'<Event {NS}><System><Provider Name="Microsoft-Windows-Kernel-Power" Guid="{{331c3b3a}}"/>'
           '<EventID>41</EventID><Level>1</Level><TimeCreated SystemTime="2026-10-30T18:08:31.1234567Z"/>'
           '<EventRecordID>59064</EventRecordID><Channel>System</Channel></System>'
           '<EventData><Data Name="BugcheckCode">307</Data><Data Name="SleepInProgress">0</Data></EventData></Event>')
    e = parse_event_xml(xml)
    assert (e.log, e.provider, e.event_id, e.level, e.record) == ("System", "Microsoft-Windows-Kernel-Power", 41, 1, 59064)
    assert e.time == pytest.approx(timegm((2026, 10, 30, 18, 8, 31)) + 0.1234567)
    assert e.fields == {"BugcheckCode": "307", "SleepInProgress": "0"} and e.args == ("307", "0")


def test_parse_event_xml_classic_and_user_data():
    classic = (f'<Event {NS}><System><Provider Name="nvlddmkm"/><EventID Qualifiers="49322">13</EventID><Level>2</Level>'
               '<TimeCreated SystemTime="2026-10-31T02:10:01Z"/><EventRecordID>1</EventRecordID><Channel>System</Channel>'
               '</System><EventData><Data>\\Device\\000000ec</Data><Data>Graphics FECS Exception: UCODE Fatal Error</Data>'
               '<Binary>0000000002003000</Binary></EventData></Event>')
    e = parse_event_xml(classic)
    assert e.event_id == 13 and e.fields == {} and e.arg(1) == "Graphics FECS Exception: UCODE Fatal Error"
    assert e.binary == "0000000002003000" and e.arg(5) == ""
    user = (f'<Event {NS}><System><Provider Name="X"/><EventID>7</EventID><Level>3</Level>'
            '<TimeCreated SystemTime="2026-10-31T02:10:01Z"/><Channel>System</Channel></System>'
            '<UserData><Thing xmlns="urn:x"><Name>abc</Name><Count>2</Count></Thing></UserData></Event>')
    assert parse_event_xml(user).fields == {"Name": "abc", "Count": "2"}
    assert parse_event_xml("<not-xml") is None


def test_build_query_escapes_and_splits_by_provider():
    q = build_query("System", {"disk": (7, 51), "nvlddmkm": None}, 90)
    assert q.count("<Select ") == 2 and "&lt;= 7776000000" in q and "<=" not in q.replace("&lt;=", "")
    assert "(EventID=7 or EventID=51)" in q and "(Level=1 or Level=2 or Level=3)" in q
    assert "timediff" not in build_query("System", {"disk": (7,)}, None)


def test_times():
    assert parse_time("garbage") == 0.0
    # 6008 的附加資料：本機時間（+08:00）與 UTC 兩個 SYSTEMTIME，取第二個
    binary = "EA070A0006001F00020007002C009A00EA070A0005001E00120007002C009A003C000000"
    assert systemtime(binary, 16) == pytest.approx(timegm((2026, 10, 30, 18, 7, 44)) + 0.154)
    assert systemtime("00", 16) is None
    logged = time.mktime((2026, 6, 17, 20, 4, 29, 0, 0, -1))
    old = "\n\\\\?\\C:\\WINDOWS\\LiveKernelReports\\WATCHDOG\\WATCHDOG-20260329-2344.dmp"
    assert dump_time(old, logged) == time.mktime((2026, 3, 29, 23, 44, 0, 0, 0, -1))   # 晚了幾個月才送的報告
    assert dump_time(old.replace("20260329-2344", "20260617-2004"), logged) is None      # 差不到 2 分鐘：照記下的時間
    assert dump_time(None, logged) is None


# ---- 每種事件的解讀 ----

def test_crash_events():
    blue = classify(ev("Microsoft-Windows-Kernel-Power", 41, level=1, fields={"BugcheckCode": "307",
                                                                               "PowerButtonTimestamp": "0"}))
    assert (blue.category, blue.role, blue.level, blue.info["bugcheck"]) == ("crash", "reboot", "hot", 0x133)
    assert "藍屏 0x133" in blue.text and blue.source == "Kernel-Power 41"
    button = classify(ev("Microsoft-Windows-Kernel-Power", 41, fields={"BugcheckCode": "0",
                                                                        "PowerButtonTimestamp": "134317761909712855"}))
    assert button.info["how"] == "button"
    sleep = classify(ev("Microsoft-Windows-Kernel-Power", 41, fields={"BugcheckCode": "0", "SleepInProgress": "6"}))
    assert sleep.info["how"] == "sleep"
    code = classify(ev("Microsoft-Windows-WER-SystemErrorReporting", 1001,
                       fields={"param1": "0x00000133 (0x1, 0x1e00, 0x0, 0x0)", "param2": "C:\\WINDOWS\\Minidump\\a.dmp"}))
    assert code.info == {"bugcheck": 0x133, "dump": "C:\\WINDOWS\\Minidump\\a.dmp"} and "DPC_WATCHDOG_VIOLATION" in code.text
    assert describe_bugcheck(0x1000007E)[0] == "SYSTEM_THREAD_EXCEPTION_NOT_HANDLED"
    assert describe_bugcheck(0x999)[0] == "UNKNOWN"
    fast = classify(ev("Microsoft-Windows-Kernel-Boot", 29, fields={"FailureStatus": "3221225684"}))
    assert "0xC00000D4" in fast.text


def test_gpu_events():
    fecs = classify(ev("nvlddmkm", 13, args=("\\Device\\000000ec", "Graphics FECS Exception: UCODE Fatal Error")))
    assert (fecs.key, fecs.level, fecs.short) == ("nvidia:13:fecs", "hot", "FECS 致命錯誤")
    assert fecs.message == "Graphics FECS Exception: UCODE Fatal Error"     # nvlddmkm 沒有訊息定義，用驅動寫的說明
    flr = classify(ev("nvlddmkm", 14, args=("x", "GPU recovery action changed from 0x0 (None) to 0x1 (PF FLR)")))
    assert flr.role == "recovery" and "PF FLR" in flr.text
    assert classify(ev("nvlddmkm", 153, args=("x", "GpuRcReset TDR occurred on GPUID:a00"))).key == "nvidia:153:tdr"
    assert classify(ev("nvlddmkm", 153, args=("x", "Error occurred on GPUID: a00"))).text == "NVIDIA 驅動回報顯示卡錯誤"
    live = classify(ev("Windows Error Reporting", 1001, log="Application", level=4,
                       fields={"EventName": "LiveKernelEvent", "P1": "141"}))
    assert (live.category, live.role) == ("gpu", "live") and "VIDEO_ENGINE_TIMEOUT_DETECTED" in live.text
    assert classify(ev("Windows Error Reporting", 1001, log="Application", fields={"EventName": "APPCRASH"})) is None


def test_storage_events():
    paging = classify(ev("disk", 51, level=3, args=("\\Device\\Harddisk3\\DR3",)), DISKS)
    assert paging.text == "硬碟 3（TOSHIBA DT02ABA400）在分頁（虛擬記憶體）讀寫時出錯"
    assert paging.info == {"disk": "3", "model": "TOSHIBA DT02ABA400"}
    assert classify(ev("disk", 7, args=("\\Device\\Harddisk1\\DR1",))).text == "硬碟 1 有壞軌（讀不到某個區塊）"
    flush = classify(ev("Microsoft-Windows-Ntfs", 140, level=3,
                        fields={"VolumeId": "G:", "Error": "0xc000000e", "ProductId": "TOSHIBA DT02ABA400"},
                        message="失敗狀態: 指定的裝置不存在。\n裝置序號:         DEMO0SERIAL1\n配接器序號: \n匯流排類型: SATA"))
    assert flush.role == "disconnect" and flush.text == "G:（TOSHIBA DT02ABA400）的資料寫不進交易記錄：裝置不見了（斷線）"
    assert "DEMO0SERIAL1" not in flush.message and "裝置序號: （不顯示）" in flush.message
    assert "配接器序號: \n" in flush.message                                  # 空的不動
    lost = classify(ev("Ntfs", 50, level=3, args=("", "G:\\Projects\\notes\\server")))
    assert lost.text == "延遲寫入失敗：G: 上的檔案資料沒存到硬碟（可能遺失）"
    reset = classify(ev("storahci", 129, level=3, args=("\\Device\\RaidPort1",)))
    assert reset.role == "reset" and reset.text.startswith("SATA 控制器重設了連接埠 1")
    inpage = classify(ev("Windows Error Reporting", 1001, log="Application",
                         fields={"EventName": "InPageError", "P1": "c000000e"}))
    assert (inpage.category, inpage.role) == ("storage", "disconnect")


def test_hardware_and_app_events():
    corrected = classify(ev("Microsoft-Windows-WHEA-Logger", 19, level=3))
    assert (corrected.category, corrected.level, corrected.text) == ("hardware", "warm", "已修正的硬體錯誤（CPU 核心）")
    assert classify(ev("Microsoft-Windows-WHEA-Logger", 18)).level == "hot"
    assert classify(ev("Microsoft-Windows-WHEA-Logger", 3, level=4)) is None
    crash = classify(ev("Application Error", 1000, log="Application",
                        args=("HWInfoSever.exe", "1.0.3.0", "693a7ead", "nvml.dll", "8.17", "6a60", "c0000005")))
    assert crash.text == "HWInfoSever.exe 當掉（nvml.dll）：存取違規" and crash.info["code"] == 0xC0000005
    dotnet = classify(ev(".NET Runtime", 1026, log="Application", args=(
        "應用程式: pythonw.exe\nFramework 版本: v4.0.30319\n例外狀況資訊: System.AccessViolationException\n"
        "   於 LibreHardwareMonitor.Interop.NvidiaML.NvmlDeviceGetPowerUsage(NvmlDevice)\n",)))
    assert dotnet.text == ("pythonw.exe 因為 .NET 例外結束：AccessViolationException"
                           "（在 LibreHardwareMonitor.Interop.NvidiaML.NvmlDeviceGetPowerUsage）")
    assert classify(ev("Some-Unknown-Provider", 1)) is None


def test_watched_covers_every_rule():
    """要讀的來源都有規則可以解讀，有規則的來源也都有讀到。"""
    watched = {(p, i) for providers in catalog.WATCHED.values() for p, ids in providers.items() for i in (ids or ())}
    providers = {p for providers in catalog.WATCHED.values() for p in providers}
    assert {p for p, _ in catalog.RULES} <= providers and set(catalog.PROVIDER_RULES) <= providers
    assert all(key in watched for key in catalog.RULES)


# ---- 歸成一件件事（示範資料＝這台的真實記錄） ----

def test_demo_counts_match_this_pc():
    assert demo_report(90)["counts"] == {"bluescreen": 4, "reset": 2, "gpu": 8, "gpuMajor": 6, "storage": 1,
                                         "hardware": 0, "app": 24}
    whole = demo_report(None)
    assert whole["counts"]["bluescreen"] == 5 and whole["counts"]["reset"] == 4
    assert whole["oldest"] == NOW - 142 * 86400 and whole["source"] == "demo" and whole["errors"] == []
    assert [i["category"] for i in demo_report(7)["incidents"]].count("storage") == 0


def test_gpu_recovered_incident():
    first = demo_report(90)["incidents"][0]
    assert first["title"] == "顯示卡內部控制器（FECS）致命錯誤，驅動自己恢復"
    assert (first["badge"], first["level"], first["major"]) == ("已恢復", "warm", True)
    assert first["summary"] == "FECS 致命錯誤 → TDR 重設 ×101 → 74 秒後驅動重設顯示卡 → 即時傾印 0x117 ×2 → 4 個程式當掉"
    texts = [line["text"] for line in first["lines"]]
    assert any(t.startswith("pythonw.exe 因為 .NET 例外結束") for t in texts)
    assert not any(t.startswith("pythonw.exe 當掉") for t in texts)          # .NET 當掉 Windows 記兩筆，只留一筆
    assert first["involves"] == ["app", "gpu"] and len(first["explain"]) == 4 and first["advice"]


def test_bluescreen_after_gpu_errors():
    incidents = demo_report(None)["incidents"]
    blue = incidents[1]
    assert (blue["title"], blue["badge"], blue["level"]) == ("藍屏 0x133（顯示卡出錯之後）", "藍屏", "hot")
    assert blue["summary"] == "FECS 致命錯誤 → TDR 重設 ×119 → 2 分鐘後重新開機"
    assert blue["explain"][0].startswith("0x133 DPC_WATCHDOG_VIOLATION")
    titles = [i["title"] for i in incidents]
    # 8/8：最後運作記錄比 FECS 還早，照樣算成「顯示卡出錯之後」；7/4：沒有藍屏代碼的重開
    assert titles.count("藍屏 0x133（顯示卡出錯之後）") == 5
    assert "無預警重開（顯示卡出錯之後）" in titles and "無預警重開（沒有藍屏代碼）" in titles
    assert "按電源鍵強制關機" in titles and "睡眠或快速啟動恢復失敗，重新開機" in titles
    aug8 = next(i for i in incidents if "3 分鐘後 FECS 致命錯誤" in i["summary"])
    assert aug8["start"] < next(line["time"] for line in aug8["lines"] if line["source"] == "EventLog 6008")
    jul4 = next(i for i in incidents if i["title"] == "無預警重開（顯示卡出錯之後）")
    assert "最後的運作記錄" not in jul4["summary"]               # 6008 早了 38 分鐘，有別的事件時不放進摘要


def test_disk_disconnect_is_one_incident():
    disk = next(i for i in demo_report(90)["incidents"] if i["category"] == "storage")
    assert (disk["title"], disk["badge"], disk["level"]) == ("G:（TOSHIBA DT02ABA400）突然斷線", "斷線", "hot")
    counts = {line["source"]: line["count"] for line in disk["lines"]}
    assert counts["disk 51"] == 6400 and counts["Ntfs 50"] == 263 and counts["Ntfs 140"] == 126
    assert disk["disks"] == ["3"] and any("編號" in x for x in disk["explain"])
    assert disk["summary"].startswith("控制器重設裝置 → 讀寫重試 → 94 秒後分頁錯誤 ×6,400")


def test_minor_incidents_and_markers():
    r = demo_report(None)
    minor = [i["title"] for i in r["incidents"] if not i["major"]]
    assert "explorer.exe 沒有回應" in minor and "USB 硬碟（UASP）重設了裝置" in minor
    assert {m["kind"] for m in r["markers"]} >= {"boot", "sleep", "wake"}
    assert r["markers"] == sorted(r["markers"], key=lambda m: -m["time"])


def test_whea_and_app_only_incidents():
    entries = [classify(ev("Microsoft-Windows-WHEA-Logger", 19, level=3, t=NOW - 100)),
               classify(ev("Microsoft-Windows-WHEA-Logger", 19, level=3, t=NOW - 50)),
               classify(ev("Application Hang", 1002, log="Application", t=NOW - 5000, args=("Honeyview.exe",)))]
    r = report(entries, days=None, oldest=None, now=NOW)
    whea, hang = r["incidents"]
    assert (whea["title"], whea["badge"], whea["major"]) == ("已修正的硬體錯誤（CPU 核心）", "已修正", True)
    assert whea["lines"][0]["count"] == 2 and r["counts"]["hardware"] == 1
    assert (hang["title"], hang["major"], hang["advice"]) == ("Honeyview.exe 沒有回應", False, [])


# ---- 快取 ----

class CountingLog:
    kind = "fake"

    def __init__(self, fail: str | None = None) -> None:
        self.reads = 0
        self.fail = fail

    def read(self, channel, providers, days):
        self.reads += 1
        if channel == self.fail:
            raise PermissionError(5, "存取被拒")
        return [ev("nvlddmkm", 13, args=("x", "Graphics FECS Exception: UCODE Fatal Error"))] if channel == "System" else []

    def oldest(self, channel):
        return NOW - 86400


def test_service_caches_and_reports_errors():
    clock = [NOW]
    log = CountingLog(fail="Application")
    svc = EventService(log, clock=lambda: clock[0])
    first = svc.report(30)
    assert log.reads == 2 and first["errors"] == ["讀不到應用程式記錄：存取被拒"] and len(first["incidents"]) == 1
    assert svc.report(30) is first and log.reads == 2             # 60 秒內重複用
    clock[0] += 61
    svc.report(30)
    assert log.reads == 4
    svc.report(0)                                                # 全部 是另一份
    assert log.reads == 6
