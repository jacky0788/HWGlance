"""Mac 的事件記錄（Mac 版第 3 段）：核心錯誤（kernel panic）報告、每次開機時記下的「上次關機原因」、開關機時間，
整理成跟 Windows 一樣的 Entry（catalog.Entry），後面的歸類、摘要、事件頁都照用（incidents.py 看 info["mac"] 下標題）。

來源（2026-11-11 在 MacBook Pro 14 吋 M6、macOS 27.2 查過，一般權限讀得到，全部唯讀）：
- /Library/Logs/DiagnosticReports（含 Retired/）的 *.panic、panic-*.ips：第一行是 JSON 標頭（bug_type 210＝核心錯誤、
  timestamp），後面是 JSON 本文（panicString、build）。只拿 panicString、時間、系統版本，不讀裝置金鑰、序號。舊版是純文字。
- sysctl kern.shutdownreason：上一次關機的原因（Apple 晶片；這台正常重新開機是「rst_in,reset_in_rise rst_in,reset_in_fall
  ap_restart」）。只有最近一次，所以 HWGlance 每次啟動時記一筆到 data/macboots.jsonl（同一次開機只記一次），之後才看得到歷史。
  Apple 沒公開這些代號：這裡只認看門狗、電源鍵、斷電、過熱、核心錯誤幾種；認不出的照原文寫在開機標記上，不當成出事。
- `last`（wtmp）：開機、正常關機的時間，當事件頁時間軸上的標記。
- 統一記錄（log show）的「Previous shutdown cause」這台查不到，而且查 10 天要 85 秒，不用。
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import traceback
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path

from .catalog import CRASH, HOT, INFO, MARKER, Entry

PANIC_DIRS = (Path("/Library/Logs/DiagnosticReports"), Path("/Library/Logs/DiagnosticReports/Retired"))
PANIC_FILE = re.compile(r"(?:\.panic$|^panic-.*\.ips$)", re.I)
PANIC_PREFIX = re.compile(r"^panic\(cpu \d+ caller 0x[0-9a-f]+\):\s*", re.I)
WATCHDOG_WHO = re.compile(r"no successful checkins from (\S+)")
LAST_LINE = re.compile(r"^(reboot|shutdown)\s+\S+\s+\w{3} (\w{3})\s+(\d{1,2}) (\d{2}):(\d{2})")
WTMP_BEGINS = re.compile(r"wtmp begins \w{3} (\w{3})\s+(\d{1,2}) (\d{2}):(\d{2}):(\d{2}) \S+ (\d{4})")
MONTHS = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}
BOOT_MATCH = 180.0            # wtmp 的開機時間只到分鐘：跟記下的開機時間差 3 分鐘內算同一次
PANIC_TO_BOOT = 12 * 3600.0   # 核心錯誤之後多久內的開機算是它造成的重開

BASE_EXPLAIN = "macOS 的核心遇到沒辦法繼續的錯誤，存了一份報告後自動重新開機（跟 Windows 的藍屏是同一種事）。"
COMMON_ADVICE = ["先更新 macOS（系統設定 › 一般 › 軟體更新）",
                 "最近裝過的驅動、系統延伸功能（例如外接裝置附的軟體）先移除試試"]
HARDWARE_ADVICE = "常常發生就用「Apple 診斷」檢查硬體（關機後按住電源鍵到出現選項，再按 ⌘D），還是會出錯就送修"
PANIC_KINDS = {  # kind → (標題的說法, 多的說明, 建議)
    "watchdog": ("系統卡住太久", "macOS 的看門狗發現有系統程式太久沒回應，就讓核心停下來重開。",
                 ["看標題裡是哪個程式卡住：WindowServer 是畫面，常跟外接螢幕、轉接器、顯示晶片有關"]),
    "gpu": ("顯示晶片（GPU）出錯", "錯誤發生在顯示晶片的驅動（AGX 是 Apple 晶片的 GPU）。",
            ["拔掉外接螢幕、轉接器、擴充座再觀察", "常在同一個 App 裡發生，就先更新或換掉那個 App"]),
    "sleep": ("睡眠或喚醒卡住", "睡眠或喚醒的過程有裝置沒回應。", ["拔掉睡眠時接著的外接裝置再觀察"]),
    "storage": ("儲存裝置（SSD）出錯", "錯誤發生在儲存裝置或檔案系統（NVMe、APFS）。",
                ["用「磁碟工具程式」的「急救」檢查內建磁碟", "重要資料先備份"]),
    "memory": ("核心記憶體出錯", "核心的記憶體區塊壞掉了，可能是驅動的錯，也可能是硬體。", []),
    "thermal": ("溫度過高", "溫度超過安全範圍，核心停下來保護硬體。",
                ["出風口別擋住、別放在棉被或軟的表面上，看 HWGlance 的溫度與風扇"]),
    "kernel": ("核心遇到無法處理的錯誤", "", []),
}
# kern.shutdownreason 裡認得出的代號：(樣式, how, 標題, 標籤, 說明, 建議)。其他（例如 ap_restart）當成正常
REASONS: tuple[tuple[re.Pattern, str, str, str, str, list[str]], ...] = (
    (re.compile(r"panic", re.I), "panic", "核心錯誤後重新開機", "核心錯誤",
     "上次關機是核心錯誤造成的；這次沒有找到那次的報告（可能已經被系統清掉）。", COMMON_ADVICE + [HARDWARE_ADVICE]),
    (re.compile(r"wdog|wdt|watchdog", re.I), "watchdog", "系統卡住，被看門狗重開", "重開",
     "整台卡住太久，硬體的看門狗直接把電腦重開，來不及留下核心錯誤報告。", COMMON_ADVICE + [HARDWARE_ADVICE]),
    (re.compile(r"btn|button|long_?press|power_?key", re.I), "button", "按住電源鍵強制關機", "強制關機",
     "上次是按住電源鍵強制關機的，通常是畫面或整台卡住了。", ["看看強制關機前發生了什麼：有沒有核心錯誤或程式沒回應"]),
    (re.compile(r"therm|temp", re.I), "thermal", "溫度過高，自動關機", "過熱",
     "溫度超過安全範圍，系統為了保護硬體自動關機。", ["出風口別擋住、別放在軟的表面上，看 HWGlance 的溫度與風扇"]),
    (re.compile(r"uvlo|brown_?out|power_?loss|pwr_?loss|ocp|ovp|batt", re.I), "power", "突然斷電或電源保護", "斷電",
     "上次關機是電源造成的：電池沒電、斷電，或電源保護啟動。", ["檢查電池健康度（系統設定 › 電池）與充電器、線材"]),
)


# ---- 純函式（tests/test_mac_events.py） ----

def stamp(text: str | None) -> float | None:
    """2026-11-01 10:23:45.00 +0800 → epoch 秒；認不得回傳 None。"""
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f %z", "%Y-%m-%d %H:%M:%S %z"):
        try:
            return datetime.strptime(text.strip(), fmt).timestamp()
        except ValueError:
            continue
    return None


def parse_panic(text: str) -> dict | None:
    """核心錯誤報告 → {"time", "panic"（第一行，去掉 panic(cpu … caller …): 前綴）, "build"}；不是核心錯誤回傳 None。"""
    head, _, body = text.partition("\n")
    try:
        header = json.loads(head) if head.lstrip().startswith("{") else {}
    except ValueError:
        header = {}
    if header.get("bug_type") not in (None, "210", 210):
        return None                                      # 其他種類的報告（App 當掉 309、卡住 288…）
    try:
        data = json.loads(body) if body.lstrip().startswith("{") else {}
    except ValueError:
        data = {}
    panic = data.get("panicString") or (text if "panic(cpu" in text else "")
    if not panic:
        return None
    first = next((line.strip() for line in panic.splitlines() if "panic(" in line), panic.strip().splitlines()[0])
    first = PANIC_PREFIX.sub("", first[first.find("panic("):] if "panic(" in first else first)
    return {"time": stamp(header.get("timestamp") or data.get("date")), "panic": first[:300],
            "build": header.get("os_version") or data.get("build")}


def panic_kind(panic: str) -> str:
    low = panic.lower()
    if "watchdog" in low:
        return "watchdog"
    if re.search(r"\bagx|\bgpu", low):
        return "gpu"
    if "sleep" in low or "wake" in low:
        return "sleep"
    if re.search(r"nvme|apfs|\bans\d?\b", low):
        return "storage"
    if re.search(r"thermal|temperature", low):
        return "thermal"
    if re.search(r"zone|kalloc|memory", low):
        return "memory"
    return "kernel"


def describe_panic(panic: str) -> dict:
    """incidents.py 用的標題、標籤、說明、建議。"""
    kind = panic_kind(panic)
    what, more, advice = PANIC_KINDS[kind]
    if kind == "watchdog" and (who := WATCHDOG_WHO.search(panic)):
        what = f"系統卡住，{who.group(1)} 太久沒回應"
    explain = [BASE_EXPLAIN] + ([more] if more else []) + [f"報告的第一行：{panic}"]
    return {"title": f"核心錯誤：{what}", "badge": "核心錯誤", "kind": kind, "explain": explain,
            "advice": list(dict.fromkeys(advice + COMMON_ADVICE + [HARDWARE_ADVICE]))}


def classify_reason(reason: str | None) -> dict | None:
    """kern.shutdownreason → 不正常的關機（標題、標籤、說明、建議、how）；正常或認不出的回傳 None。"""
    for rx, how, title, badge, explain, advice in REASONS:
        if reason and rx.search(reason):
            return {"how": how, "title": title, "badge": badge, "explain": [explain, f"系統記下的原因代號：{reason}"],
                    "advice": list(advice)}
    return None


def reason_text(reason: str | None) -> str:
    """開機標記上的說法。"""
    if not reason:
        return ""
    found = classify_reason(reason)
    if found:
        return found["title"]
    if re.search(r"restart", reason, re.I):
        return "正常重新開機"
    if re.search(r"poweroff|power_off|shutdown", reason, re.I):
        return "正常關機"
    return f"原因代號 {reason}"


def parse_last(text: str) -> tuple[list[float], list[float], float | None]:
    """`last` 的輸出 → (開機時間, 正常關機時間, wtmp 從哪時開始)。每行只有月日時分沒有年份：
    從最後一行的「wtmp begins … 年份」往回推，月份變小就是跨年（輸出是新的在前）。"""
    begins = WTMP_BEGINS.search(text)
    rows = [(m.group(1), MONTHS[m.group(2)], int(m.group(3)), int(m.group(4)), int(m.group(5)))
            for line in text.splitlines() if (m := LAST_LINE.match(line)) and m.group(2) in MONTHS]
    year = int(begins.group(6)) if begins else datetime.now().year
    start = None
    if begins:
        start = datetime(year, MONTHS[begins.group(1)], int(begins.group(2)), int(begins.group(3)),
                         int(begins.group(4)), int(begins.group(5))).timestamp()
    boots, downs = [], []
    last_month = MONTHS[begins.group(1)] if begins else None
    for kind, month, day, hour, minute in reversed(rows):         # 舊的在前
        if last_month is not None and month < last_month:
            year += 1
        last_month = month
        t = datetime(year, month, day, hour, minute).timestamp()
        (boots if kind == "reboot" else downs).append(t)
    return boots, downs, start


def _entry(t: float, logged: float, role: str, key: str, text: str, short: str, source: str, log: str,
           message: str | None, info: dict, level: str = HOT, category: str = CRASH) -> Entry:
    return Entry(time=t, logged=logged, category=category, role=role, level=level, key=key, text=text, short=short,
                 source=source, log=log, message=message, info=info)


def build_entries(panics: Iterable[dict], boots: Iterable[dict], wtmp_boots: Iterable[float],
                  wtmp_downs: Iterable[float]) -> list[Entry]:
    """panics：parse_panic 的結果（含 time）；boots：記下的 {boot, shutdownReason}；wtmp：開機、正常關機時間。"""
    boots = sorted(boots, key=lambda b: b["boot"])
    wtmp_boots, wtmp_downs = sorted(wtmp_boots), sorted(wtmp_downs)
    boot_times = sorted({*wtmp_boots, *(b["boot"] for b in boots)})
    out: list[Entry] = []
    for p in panics:
        t = p["time"]
        after = next((b for b in boot_times if t <= b <= t + PANIC_TO_BOOT), t)
        mac = describe_panic(p["panic"])
        out.append(_entry(t, after, "panic", "panic", mac["title"], "核心錯誤", "kernel panic", "診斷報告",
                          f"{p['panic']}\n（{p['build']}）" if p.get("build") else p["panic"], {"mac": mac, "kind": mac["kind"]}))
    for b in boots:
        found = classify_reason(b.get("shutdownReason"))
        if found:
            # 核心錯誤後的這次開機通常跟報告合成一件：摘要寫「→ 70 秒後重新開機」，不要再寫一次「核心錯誤」
            short = "重新開機" if found["how"] == "panic" else found["badge"]
            text = "核心錯誤後重新開機（開機時記下的原因）" if found["how"] == "panic" else found["title"]
            out.append(_entry(b["boot"], b["boot"], "macreset", f"reset:{found['how']}", text, short,
                              "kern.shutdownreason", "開機記錄", b.get("shutdownReason"), {"mac": found, "how": found["how"]}))
    for t in wtmp_boots:
        rec = next((b for b in boots if abs(b["boot"] - t) <= BOOT_MATCH), None)
        why = reason_text(rec.get("shutdownReason")) if rec else ""
        out.append(_entry(t, t, "boot", "marker:boot", f"開機（上次：{why}）" if why else "開機", "", "wtmp", "開機記錄",
                          None, {}, INFO, MARKER))
    for b in boots:                                   # wtmp 沒有的（被系統輪替掉了）也放標記
        if not any(abs(b["boot"] - t) <= BOOT_MATCH for t in wtmp_boots):
            why = reason_text(b.get("shutdownReason"))
            out.append(_entry(b["boot"], b["boot"], "boot", "marker:boot", f"開機（上次：{why}）" if why else "開機", "",
                              "kern.shutdownreason", "開機記錄", None, {}, INFO, MARKER))
    for t in wtmp_downs:
        out.append(_entry(t, t, "shutdown", "marker:shutdown", "關機", "", "wtmp", "開機記錄", None, {}, INFO, MARKER))
    return out


# ---- 讀這台 Mac ----

class MacIo:
    """真的讀取（測試換成假的）。"""

    @staticmethod
    def panics() -> tuple[list[tuple[str, float]], list[str]]:
        """[(內容, 修改時間)]、讀不到的原因。"""
        out, errors = [], []
        for folder in PANIC_DIRS:
            try:
                names = [p for p in folder.iterdir() if p.is_file() and PANIC_FILE.search(p.name)]
            except FileNotFoundError:
                continue
            except PermissionError:
                errors.append(f"沒有權限讀 {folder}（核心錯誤報告），這部分看不到")
                continue
            for p in names:
                try:
                    out.append((p.read_text(encoding="utf-8", errors="replace"), p.stat().st_mtime))
                except OSError:
                    continue
        return out, errors

    @staticmethod
    def last() -> str:
        try:
            return subprocess.run(["/usr/bin/last", "reboot", "shutdown"], capture_output=True, text=True, timeout=10,
                                  check=False).stdout
        except (OSError, subprocess.TimeoutExpired):
            return ""

    @staticmethod
    def boot() -> dict | None:
        """這次開機的時間與上次關機的原因（sysctl）。"""
        from ..sensors import macio
        boot = macio.boot_time()
        if not boot:
            return None
        return {"boot": boot, "shutdownReason": (macio.sysctl_str("kern.shutdownreason") or "").strip(),
                "bootReason": (macio.sysctl_str("kern.bootreason") or "").strip()}


class MacEventLog:
    """EventService 的來源（Mac）：有 entries() 就不走 Windows 的頻道與事件代號（events/service.py）。"""

    kind = "mac"

    def __init__(self, data_dir: Path, io=None, clock: Callable[[], float] = time.time) -> None:
        self.io = io or MacIo()
        self.file = Path(data_dir) / "macboots.jsonl"
        self.errors: list[str] = []
        self._clock = clock
        self.record_boot()

    def boots(self) -> list[dict]:
        out = []
        try:
            lines = self.file.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return out
        except OSError:
            traceback.print_exc()
            return out
        for line in lines:
            try:
                rec = json.loads(line)
            except ValueError:
                continue                              # 寫到一半當機的那一行
            if isinstance(rec, dict) and isinstance(rec.get("boot"), (int, float)):
                out.append(rec)
        return out

    def record_boot(self) -> None:
        """每次 HWGlance 啟動時記下這次開機的「上次關機原因」；同一次開機只記一次。"""
        try:
            now = self.io.boot()
        except Exception:                             # noqa: BLE001 - 記不到只是少一筆歷史
            traceback.print_exc()
            return
        if not now or any(abs(b["boot"] - now["boot"]) < 2 for b in self.boots()):
            return
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.file, "a", encoding="utf-8") as f:
                f.write(json.dumps({**now, "recorded": round(self._clock(), 3)}, ensure_ascii=False) + "\n")
        except OSError:
            traceback.print_exc()

    def entries(self, days: int | None = None) -> list[Entry]:
        raw, errors = self.io.panics()
        self.errors = list(errors)
        panics = []
        for text, mtime in raw:
            p = parse_panic(text)
            if p is not None:
                p["time"] = p["time"] or mtime
                panics.append(p)
        boots, downs, self._wtmp_start = parse_last(self.io.last())
        return build_entries(panics, self.boots(), boots, downs)

    def oldest(self, channel: str | None = None) -> float | None:
        """事件頁底下的「記錄最早到」：wtmp 開始的時間（或第一筆記下的開機）。"""
        starts = [t for t in (getattr(self, "_wtmp_start", None), *(b["boot"] for b in self.boots()[:1])) if t]
        return min(starts) if starts else None


# ---- 示範資料（--demo-profile mac） ----

DEMO_PANICS = (  # (幾天前, panic 第一行)：看畫面用，不是哪台電腦的真實記錄
    (2.3, "userspace watchdog timeout: no successful checkins from WindowServer (2 induced crashes) in 120 seconds"),
    (11.6, "AGX: GPU hang detected: firmware timeout waiting for the command queue"),
)


class MacDemoEventLog:
    """照 Mac 的樣子編的事件：兩次核心錯誤（看門狗、GPU）、一次按住電源鍵強制關機、幾次正常開關機。"""

    kind = "mac-demo"

    def __init__(self, now: float | None = None) -> None:
        self._now = now if now is not None else time.time()
        self.errors: list[str] = []

    def _data(self) -> tuple[list[dict], list[dict], list[float], list[float]]:
        now, day = self._now, 86400.0
        panics = [{"time": now - d * day, "panic": text, "build": "macOS 27.2"} for d, text in DEMO_PANICS]
        normal = "rst_in,reset_in_rise rst_in,reset_in_fall ap_restart"
        boots = [{"boot": now - 2.3 * day + 70, "shutdownReason": f"{normal} panic"},
                 {"boot": now - 5.1 * day, "shutdownReason": "pwr_btn long_press"},
                 {"boot": now - 0.4 * day, "shutdownReason": normal}]
        wtmp_boots = [now - 11.6 * day + 65, now - 9 * day, now - 5.1 * day, now - 2.3 * day + 70, now - 0.4 * day]
        wtmp_downs = [now - 9 * day - 120, now - 0.4 * day - 90]
        return panics, boots, wtmp_boots, wtmp_downs

    def entries(self, days: int | None = None) -> list[Entry]:
        return build_entries(*self._data())

    def oldest(self, channel: str | None = None) -> float | None:
        return self._now - 30 * 86400.0

    def downs(self) -> list[tuple[float, float]]:
        """示範黑盒子（DemoBlackBox）裡沒有讀數的時段：核心錯誤到重新開機、強制關機前一分鐘到開機。"""
        panics, boots, _, _ = self._data()
        out = [(p["time"], p["time"] + 70) for p in panics]
        out += [(b["boot"] - 600, b["boot"]) for b in boots if classify_reason(b["shutdownReason"])
                and classify_reason(b["shutdownReason"])["how"] == "button"]
        return sorted(out)
