"""Mac 的事件記錄（events/mac.py）：核心錯誤報告的兩種格式、上次關機原因的代號、`last` 跨年、歸成一件件事、
每次開機只記一次、示範資料。全部是編的資料（不讀這台的 /Library/Logs、不跑 last），Windows 上也跑得了。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from hwglance.events.mac import (MacDemoEventLog, MacEventLog, build_entries, classify_reason, describe_panic,
                                 parse_last, parse_panic, reason_text)
from hwglance.events.incidents import report
from hwglance.events.service import EventService

NORMAL = "rst_in,reset_in_rise rst_in,reset_in_fall ap_restart"
T0 = datetime(2026, 11, 1, 10, 23, 45, tzinfo=timezone(timedelta(hours=8))).timestamp()


def panic_file(text: str, bug_type: str = "210") -> str:
    header = {"bug_type": bug_type, "timestamp": "2026-11-01 10:23:45.00 +0800", "os_version": "macOS 27.1 (27B00)",
              "incident_id": "00000000-0000-0000-0000-000000000000"}
    body = {"build": "macOS 27.1 (27B00)", "product": "Mac00,0", "crashReporterKey": "編的",
            "panicString": f"panic(cpu 4 caller 0xfffffe0012345678): {text}\nDebugger message: panic\nMemory ID: 0x6\n"}
    return json.dumps(header) + "\n" + json.dumps(body, indent=2)


def test_panic_report_formats():
    p = parse_panic(panic_file("userspace watchdog timeout: no successful checkins from WindowServer in 120 seconds"))
    assert p == {"time": T0, "panic": "userspace watchdog timeout: no successful checkins from WindowServer in 120 seconds",
                 "build": "macOS 27.1 (27B00)"}
    assert parse_panic(panic_file("x", bug_type="309")) is None           # App 當掉的報告不算
    old = 'Sat Mar  2 10:00:00 2024\npanic(cpu 0 caller 0xffffff7f8): "Sleep transition timed out"\nBacktrace...'
    assert parse_panic(old) == {"time": None, "panic": '"Sleep transition timed out"', "build": None}   # 舊的純文字（時間用檔案的）
    assert parse_panic("not a report") is None


def test_panic_titles_say_what_went_wrong():
    watchdog = describe_panic("userspace watchdog timeout: no successful checkins from WindowServer in 120 seconds")
    assert watchdog["title"] == "核心錯誤：系統卡住，WindowServer 太久沒回應" and watchdog["badge"] == "核心錯誤"
    assert describe_panic("AGX: GPU hang detected")["kind"] == "gpu"
    assert describe_panic('"Sleep transition timed out"')["kind"] == "sleep"
    assert describe_panic("zone_require failed: kalloc.1024")["kind"] == "memory"
    plain = describe_panic("something odd")
    assert plain["title"] == "核心錯誤：核心遇到無法處理的錯誤" and "something odd" in plain["explain"][-1]


def test_shutdown_reasons():
    assert classify_reason(NORMAL) is None and reason_text(NORMAL) == "正常重新開機"
    assert classify_reason("pwr_btn long_press")["badge"] == "強制關機"
    assert classify_reason("wdog,reset_in1 ap_restart")["how"] == "watchdog"   # 看門狗優先於後面的 ap_restart
    assert classify_reason(f"{NORMAL} panic")["badge"] == "核心錯誤"
    assert classify_reason("something_new") is None and reason_text("something_new") == "原因代號 something_new"
    assert reason_text("") == ""


def test_last_output_across_new_year():
    text = ("reboot    time                                Mon Jan  5 09:00\n"
            "shutdown  time                                Sun Dec 28 22:00\n"
            "reboot    time                                Sun Dec 28 08:00\n\n"
            "wtmp begins Mon Dec  1 10:00:00 CST 2025\n")
    boots, downs, start = parse_last(text)
    assert boots == [datetime(2025, 12, 28, 8, 0).timestamp(), datetime(2026, 1, 5, 9, 0).timestamp()]
    assert downs == [datetime(2025, 12, 28, 22, 0).timestamp()]
    assert start == datetime(2025, 12, 1, 10, 0).timestamp()


def test_panic_and_reboot_become_one_incident():
    panic = {"time": T0, "panic": "AGX: GPU hang detected", "build": "macOS 27.1"}
    boots = [{"boot": T0 + 70, "shutdownReason": f"{NORMAL} panic"},
             {"boot": T0 + 86400, "shutdownReason": "pwr_btn long_press"},
             {"boot": T0 + 2 * 86400, "shutdownReason": NORMAL}]
    wtmp_boots = [T0 + 60, T0 + 86400 - 30, T0 + 2 * 86400]
    r = report(build_entries([panic], boots, wtmp_boots, [T0 + 2 * 86400 - 120]), days=None, oldest=None,
               now=T0 + 3 * 86400)
    assert r["counts"]["bluescreen"] == 1 and r["counts"]["reset"] == 1
    forced, crash = r["incidents"]                                     # 新的在前
    assert crash["title"] == "核心錯誤：顯示晶片（GPU）出錯" and crash["start"] == round(T0, 3)
    assert len(crash["lines"]) == 2 and crash["major"]                 # 報告＋開機時記下的原因合成一件
    assert forced["badge"] == "強制關機" and "pwr_btn long_press" in forced["explain"][-1]
    texts = [m["text"] for m in r["markers"]]
    assert "開機（上次：正常重新開機）" in texts and "關機" in texts and "開機（上次：按住電源鍵強制關機）" in texts


class FakeIo:
    def __init__(self, boot=None):
        self.boot_info = boot or {"boot": T0, "shutdownReason": NORMAL, "bootReason": "auto_wakeup"}

    def boot(self):
        return self.boot_info

    @staticmethod
    def panics():
        return [(panic_file("userspace watchdog timeout: no successful checkins from WindowServer"), 0.0)], \
               ["沒有權限讀 /Library/Logs/DiagnosticReports/Retired（核心錯誤報告），這部分看不到"]

    @staticmethod
    def last():
        return "reboot    time                                Sun Nov  1 10:25\n\nwtmp begins Tue Oct 27 09:18:55 CST 2026\n"


def test_event_log_records_each_boot_once_and_reports(tmp_path):
    MacEventLog(tmp_path, io=FakeIo(), clock=lambda: T0 + 5)
    MacEventLog(tmp_path, io=FakeIo(), clock=lambda: T0 + 9)                  # 同一次開機再開 HWGlance：不重記
    log = MacEventLog(tmp_path, io=FakeIo({"boot": T0 + 3600, "shutdownReason": "pwr_btn"}), clock=lambda: T0 + 3700)
    lines = (tmp_path / "macboots.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["boot"] for line in lines] == [T0, T0 + 3600]
    (tmp_path / "macboots.jsonl").write_text("\n".join(lines) + '\n{"boot": 1', encoding="utf-8")   # 寫到一半的行
    r = EventService(log, clock=lambda: T0 + 7200).report(None)
    assert r["source"] == "mac" and r["errors"] == FakeIo.panics()[1]
    assert [i["badge"] for i in r["incidents"]] == ["強制關機", "核心錯誤"]
    assert r["oldest"] == round(datetime(2026, 10, 27, 9, 18, 55).timestamp(), 3)


def test_demo_log_looks_like_a_mac():
    log = MacDemoEventLog(now=T0)
    r = EventService(log, clock=lambda: T0).report(30)
    assert r["source"] == "mac-demo" and r["counts"]["bluescreen"] == 2 and r["counts"]["reset"] == 1
    assert {i["badge"] for i in r["incidents"]} == {"核心錯誤", "強制關機"}
    assert len(log.downs()) == 3 and all(a < b for a, b in log.downs())
