"""把一串事件歸成一件件事，算摘要。純函式（tests/test_events.py 用示範資料與假事件測）。

歸法：
1. 當機：開機後 Windows 補記的幾筆（Kernel-Power 41、6008、藍屏代碼、傾印檔）彼此在 3 分鐘內 → 一次當機；
   開機時的快速啟動失敗（Kernel-Boot 29）也併進去。
2. 顯示卡、硬碟、CPU／記憶體、系統：同一類、前後相隔 5 分鐘以內 → 同一件事。
3. 從 6008 記的最後運作時間（往前 5 分鐘）到重新開機之間有發生的顯示卡／硬碟／硬體事件，併進那次當機，
   標題寫「（顯示卡出錯之後）」：只說先後，不說是它造成的。6008 的時間只代表「至少活到這時候」，
   這台 6/17、7/4 兩次都比真的當機早了半小時以上，所以不能拿它當當機時間。
4. 程式當掉：某件事開始前 30 秒到結束後 2 分鐘內的併進那件事；其他的前後 1 分鐘內算同一件。
5. 同一件事裡同一種事件合成一行、寫次數（8/22 那 6,400 筆分頁錯誤是一行）。
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from .catalog import (ADVICE, APP, CRASH, EXPLAIN, GPU, HARDWARE, HOT, INFO, LIVE_DUMPS, MARKER, STORAGE, SYSTEM, WARM,
                      Entry, describe_bugcheck, hexcode, join)

CRASH_WINDOW = 180.0
GAPS = {GPU: 300.0, STORAGE: 300.0, HARDWARE: 300.0, SYSTEM: 120.0, APP: 60.0}
CAUSE_BEFORE = 300.0           # 最後運作記錄之前 5 分鐘也算
CAUSE_MAX = 12 * 3600.0        # 保險：重新開機前 12 小時以外的不算（最後運作記錄壞掉時）
APP_BEFORE, APP_AFTER = 30.0, 120.0
MAX_STEPS = 6
RANK = {INFO: 0, WARM: 1, HOT: 2}
CAUSES = {GPU: "顯示卡出錯", STORAGE: "硬碟出錯", HARDWARE: "硬體錯誤"}
DOTNET_EXCEPTION = 0xE0434352


@dataclass
class Group:
    category: str
    entries: list[Entry] = field(default_factory=list)
    causes: list[str] = field(default_factory=list)

    @property
    def start(self) -> float:
        """開始時間。當機前有別的事件時，從那些事件算起，不算 6008 的最後運作記錄（可能早了半小時以上）。"""
        if self.category == CRASH:
            boot = self.boot
            before = [e.time for e in self.entries if e.role != "alive" and e.time < boot]
            if before:
                return min(before)
        return min(e.time for e in self.entries)

    @property
    def end(self) -> float:
        return max(e.time for e in self.entries)

    @property
    def boot(self) -> float:
        """當機：Windows 開機後補記的第一筆時間。"""
        return min(e.logged for e in self.entries if e.category == CRASH)

    @property
    def alive(self) -> float:
        """當機：6008 記的最後運作時間（至少活到這時候）；沒有就用開機補記的時間。"""
        alive = [e.time for e in self.entries if e.role == "alive"]
        return min(alive) if alive else self.boot


def _by_gap(category: str, items: Iterable[Entry], gap: float) -> list[Group]:
    out: list[Group] = []
    last = 0.0
    for e in sorted(items, key=lambda e: e.time):
        if out and e.time - last <= gap:
            out[-1].entries.append(e)
        else:
            out.append(Group(category, [e]))
        last = e.time
    return out


def _crashes(items: Iterable[Entry]) -> list[Group]:
    out: list[Group] = []
    first = 0.0
    for e in sorted(items, key=lambda e: e.logged):
        if out and e.logged - first <= CRASH_WINDOW:
            out[-1].entries.append(e)
        else:
            out.append(Group(CRASH, [e]))
            first = e.logged
    return out


def build(entries: Iterable[Entry]) -> tuple[list[Group], list[Entry]]:
    """Entry → (一件件事（新的在前）, 開關機、睡眠的標記)。"""
    items = sorted(entries, key=lambda e: (e.time, e.logged))
    markers = [e for e in items if e.category == MARKER]
    crashes = _crashes(e for e in items if e.category == CRASH)
    boots = [(c, c.boot) for c in crashes]
    system: list[Entry] = []
    for e in (e for e in items if e.category == SYSTEM):
        host = next((c for c, b in boots if b - 60 <= e.logged <= b + CRASH_WINDOW), None) if e.role == "boot" else None
        (host.entries if host else system).append(e)
    groups = [g for cat in (GPU, STORAGE, HARDWARE) for g in _by_gap(cat, (e for e in items if e.category == cat), GAPS[cat])]
    groups += _by_gap(SYSTEM, system, GAPS[SYSTEM])
    for crash in crashes:
        boot = crash.boot
        lo = max(crash.alive - CAUSE_BEFORE, boot - CAUSE_MAX)
        for g in [g for g in groups if g.category in CAUSES and any(lo <= e.time <= boot for e in g.entries)]:
            crash.entries += g.entries
            crash.causes.append(g.category)
            groups.remove(g)
    groups = crashes + groups
    spans = [(g, g.start - APP_BEFORE, g.boot if g.category == CRASH else g.end + APP_AFTER) for g in groups]
    loose: list[Entry] = []
    for e in (e for e in items if e.category == APP):
        hosts = [(abs(e.time - lo), g) for g, lo, hi in spans if lo <= e.time <= hi]
        if hosts:
            min(hosts, key=lambda h: h[0])[1].entries.append(e)
        else:
            loose.append(e)
    groups += _by_gap(APP, loose, GAPS[APP])
    return sorted(groups, key=lambda g: g.start, reverse=True), markers


# ---- 一件事 → 給前端的樣子 ----

def _lines(entries: list[Entry]) -> list[dict]:
    """同一種事件合成一行（照第一次出現的時間排）。.NET 程式當掉時 Windows 會記兩筆，留 .NET 那筆（有例外的名字）。"""
    by_key: dict[str, dict] = {}
    for e in entries:
        line = by_key.get(e.key)
        if line is None:
            line = by_key[e.key] = {"time": e.time, "last": e.time, "count": 0, "text": e.text, "source": e.source,
                                    "log": e.log, "level": e.level, "message": e.message,
                                    "_short": e.short, "_category": e.category, "_role": e.role, "_info": e.info}
        line["count"] += 1
        line["last"] = max(line["last"], e.time)
        if line["message"] is None and e.message:
            line["message"] = e.message
        if RANK[e.level] > RANK[line["level"]]:
            line["level"] = e.level
    dotnet = {key.split(":", 1)[1] for key in by_key if key.startswith("dotnet:")}
    return [line for key, line in by_key.items()
            if not (key.startswith("app:") and key[4:] in dotnet and line["_info"].get("code") == DOTNET_EXCEPTION)]


def _gap(seconds: float) -> str:
    if seconds < 120:
        return f"{round(seconds)} 秒"
    if seconds < 7200:
        return f"{round(seconds / 60)} 分鐘"
    if seconds < 172_800:
        return f"{round(seconds / 3600)} 小時"
    return f"{round(seconds / 86400)} 天"


def _apps(lines: list[dict]) -> list[str]:
    return list(dict.fromkeys(line["_info"].get("app") or line["text"] for line in lines if line["_category"] == APP))


def _summary(lines: list[dict]) -> str:
    """一串事件的先後：「FECS 致命錯誤 → TDR 重設 ×101 → 74 秒後驅動重設顯示卡 → 4 個程式當掉」。
    「N 秒後」是從上一步開始算；當掉的程式合成一步，放在最後一個當掉的時候；有別的事件時不寫最後運作記錄。"""
    apps = [line for line in lines if line["_category"] == APP]
    causes = any(line["_category"] in CAUSES for line in lines)
    steps: list[tuple[float, str]] = []
    for line in lines:
        if line["_category"] == APP or not line["_short"] or (line["_role"] == "alive" and causes):
            continue
        steps.append((line["time"], line["_short"] + (f" ×{line['count']:,}" if line["count"] > 1 else "")))
    if apps:
        names = _apps(apps)
        hang = all(line["_role"] == "hang" for line in apps)
        label = f"{len(names)} 個程式當掉" if len(names) > 1 else f"{names[0]} {'沒有回應' if hang else '當掉'}"
        steps.append((max(line["last"] for line in apps), label))
    steps.sort(key=lambda s: s[0])
    out: list[str] = []
    for i, (at, label) in enumerate(steps):
        if i and at - steps[i - 1][0] >= 30:
            label = f"{_gap(at - steps[i - 1][0])}後{' ' if label[:1].isascii() else ''}{label}"
        out.append(label)
    if len(out) > MAX_STEPS:
        out = out[:MAX_STEPS - 1] + ["…"]
    return " → ".join(out)


def _gpu_story(entries: list[Entry]) -> tuple[str, list[str], bool]:
    """顯示卡出錯的標題、說明、有沒有恢復。"""
    gpu = [e for e in entries if e.category == GPU]
    fecs = next((e for e in gpu if e.key.startswith("nvidia:13:fecs")), None)
    worst = fecs or next((e for e in gpu if e.level == HOT), None)
    tdr = any(e.key in ("nvidia:153:tdr", "display:4101") for e in gpu)
    live = next((e for e in gpu if e.role == "live"), None)
    recovered = any(e.role in ("recovery", "live") for e in gpu)
    if worst is not None:
        title = worst.text
    elif tdr:
        title = "顯示卡驅動逾時，重設顯示卡（TDR）"
    elif live is not None:
        title = f"顯示卡異常：{LIVE_DUMPS.get(live.info.get('live'), ('', '即時傾印', ''))[1]}"
    else:
        title = gpu[0].text if gpu else "顯示卡出錯"
    explain = [EXPLAIN[k] for k, hit in (("fecs", fecs is not None), ("tdr", tdr),
                                         ("flr", any("PF FLR" in e.text for e in gpu)), ("live", live is not None)) if hit]
    return title, explain, recovered


def _where(entries: list[Entry]) -> str:
    """硬碟出錯的是哪一顆：交易記錄的事件有磁碟區＋型號最準，其次是硬碟編號，再來是磁碟區。"""
    for e in entries:
        if e.info.get("volume") and e.info.get("model"):
            return f"{e.info['volume']}（{e.info['model']}）"
    for e in entries:
        if e.info.get("disk"):
            model = e.info.get("model")
            return f"硬碟 {e.info['disk']}（{model}）" if model else f"硬碟 {e.info['disk']}"
    volume = next((e.info["volume"] for e in entries if e.info.get("volume")), None)
    if volume:
        return f"磁碟區 {volume}"
    return next((e.info["controller"] for e in entries if e.info.get("controller")), "硬碟")


def _describe(g: Group, entries: list[Entry]) -> tuple[str, str, str, list[str], list[str]]:
    """(標題, 標籤, 嚴重程度, 說明, 建議)"""
    roles = {(e.category, e.role) for e in entries}
    explain: list[str] = []
    advice: list[str] = []
    if g.category == CRASH:
        reboot = next((e for e in entries if e.role == "reboot"), None)
        how = reboot.info.get("how") if reboot else None
        code = next((e.info["bugcheck"] for e in entries if e.info.get("bugcheck")), None)
        causes = list(dict.fromkeys(g.causes))
        after = f"（{'、'.join(CAUSES[c] for c in causes)}之後）" if causes else ""
        if GPU in causes:
            explain += _gpu_story(entries)[1]
            advice += ADVICE["gpu"]
        if STORAGE in causes:
            explain.append(EXPLAIN["disconnect"])
            advice += ADVICE["storage"]
        if HARDWARE in causes:
            advice += ADVICE["hardware"]
        if (CRASH, "thermal") in roles:
            title, badge = f"溫度過高，自動關機{after}", "過熱"
            explain.append(EXPLAIN["thermal"])
            advice += ADVICE["thermal"]
        elif code:
            name, zh, way = describe_bugcheck(code)
            title, badge = (f"藍屏 {hexcode(code)}{after}" if after else f"藍屏 {hexcode(code)}：{zh}"), "藍屏"
            explain.insert(0, f"{hexcode(code)} {name}：{zh}。")
            advice += ADVICE.get(way) or ADVICE["system"]
        elif how == "button":
            title, badge = f"按電源鍵強制關機{after}", "強制關機"
            explain.append(EXPLAIN["button"])
            advice += ADVICE["button"]
        elif how == "sleep":
            title, badge = "睡眠或快速啟動恢復失敗，重新開機", "重開"
            explain.append(EXPLAIN["sleep"])
            advice += ADVICE["power"]
        else:
            title, badge = f"無預警重開{after or '（沒有藍屏代碼）'}", "重開"
            explain.append(EXPLAIN["nocode"])
            advice += ADVICE["nocode"]
        return title, badge, HOT, explain, list(dict.fromkeys(advice))
    if g.category == GPU:
        title, explain, recovered = _gpu_story(entries)
        if recovered and "恢復" not in title:
            title += "，驅動自己恢復"
        return title, ("已恢復" if recovered else "錯誤"), WARM, explain, list(ADVICE["gpu"])
    if g.category == STORAGE:
        where = _where(entries)
        if any(e.info.get("disk") for e in entries):
            explain.append(EXPLAIN["disk_number"])
        if (STORAGE, "disconnect") in roles:
            return join(where, "突然斷線"), "斷線", HOT, [EXPLAIN["disconnect"], *explain], list(ADVICE["storage"])
        if (STORAGE, "predict") in roles:
            return join(where, "預測快要故障（SMART）"), "預測故障", HOT, explain, ADVICE["predict"] + ADVICE["storage"]
        if (STORAGE, "bad") in roles:
            return join(where, "有壞軌"), "壞軌", HOT, explain, ADVICE["predict"] + ADVICE["storage"]
        if all(e.role == "reset" for e in entries if e.category == STORAGE):
            controller = next((e.info["controller"] for e in entries if e.info.get("controller")), "控制器")
            return f"{controller}重設了裝置", "重設", WARM, [EXPLAIN["reset"]], list(ADVICE["reset"])
        level = HOT if any(e.level == HOT for e in entries) else WARM
        return join(where, "讀寫錯誤"), "錯誤", level, explain, list(ADVICE["storage"])
    if g.category == HARDWARE:
        first = next(e for e in entries if e.category == HARDWARE)
        if (HARDWARE, "fatal") in roles:
            part = next(e.info["part"] for e in entries if e.role == "fatal")
            return f"嚴重硬體錯誤（{part}）", "嚴重", HOT, [EXPLAIN["whea"]], ADVICE["whea_fatal"] + ADVICE["hardware"]
        if (HARDWARE, "corrected") in roles:
            part = next(e.info["part"] for e in entries if e.role == "corrected")
            return f"已修正的硬體錯誤（{part}）", "已修正", WARM, [EXPLAIN["whea"]], list(ADVICE["whea_corrected"])
        if (HARDWARE, "throttle") in roles:
            return "CPU 被系統韌體限速", "限速", WARM, [EXPLAIN["throttle"]], list(ADVICE["throttle"])
        return first.text, "注意", WARM, [], list(ADVICE["hardware"])
    if g.category == SYSTEM:
        first = next(e for e in entries if e.category == SYSTEM)
        key = {"memory": "lowmem", "dumpconfig": "dumpconfig", "boot": "power"}.get(first.role, "system")
        return first.text, "注意", WARM, [], list(ADVICE[key])
    apps = list(dict.fromkeys(e.info.get("app") or e.text for e in entries if e.category == APP))
    hang_only = all(e.role == "hang" for e in entries if e.category == APP)
    if len(apps) == 1:
        title = f"{apps[0]} {'沒有回應' if hang_only else '當掉'}"
    else:
        title = f"{len(apps)} 個程式當掉：{'、'.join(apps[:3])}{'…' if len(apps) > 3 else ''}"
    return title, "程式", WARM, [], []


def major(g: Group) -> bool:
    """要特別注意的：當機、紅色的（硬碟斷線、顯示卡致命錯誤…）、任何 CPU／記憶體硬體錯誤、害程式當掉的顯示卡錯誤。
    其他（零星的顯示卡錯誤、控制器重設、單獨的程式當掉）事件頁可以收成一行。"""
    if g.category in (CRASH, HARDWARE):
        return True
    if any(e.level == HOT for e in g.entries):
        return True
    return g.category == GPU and any(e.category == APP for e in g.entries)


def incident(g: Group) -> dict:
    entries = sorted(g.entries, key=lambda e: (e.time, e.logged))
    lines = _lines(entries)
    title, badge, level, explain, advice = _describe(g, entries)
    start, end = g.start, g.end
    return {
        "id": f"{g.category}-{int(start)}", "category": g.category, "level": level, "badge": badge, "title": title,
        "major": major(g),
        "summary": _summary(lines), "explain": explain, "advice": advice, "start": round(start, 3), "end": round(end, 3),
        "involves": sorted({e.category for e in entries}),
        "disks": sorted({e.info["disk"] for e in entries if e.info.get("disk")}),
        "lines": [{k: (round(v, 3) if k in ("time", "last") else v) for k, v in line.items() if not k.startswith("_")}
                  for line in lines],
    }


def counts(groups: Iterable[Group]) -> dict[str, int]:
    """摘要的數字：藍屏、沒有藍屏代碼的重開（含強制關機、睡眠失敗、過熱）、牽涉顯示卡／硬碟／硬體的事件數
    （gpuMajor：其中要特別注意的）、程式當掉的次數。"""
    out = {"bluescreen": 0, "reset": 0, GPU: 0, "gpuMajor": 0, STORAGE: 0, HARDWARE: 0, APP: 0}
    for g in groups:
        cats = {e.category for e in g.entries}
        if g.category == CRASH:
            out["bluescreen" if any(e.info.get("bugcheck") for e in g.entries) else "reset"] += 1
        for cat in (GPU, STORAGE, HARDWARE):
            out[cat] += cat in cats
        out["gpuMajor"] += GPU in cats and major(g)
        out[APP] += sum(1 for e in g.entries if e.category == APP and not e.info.get("dotnet"))
    return out


def report(entries: Iterable[Entry], *, days: int | None, oldest: float | None, now: float,
           errors: Iterable[str] = ()) -> dict:
    """給 GET /api/events 的內容。days=None 是全部。"""
    groups, markers = build(entries)
    since = now - days * 86400 if days else None
    if since is not None:
        groups = [g for g in groups if g.end >= since]
        markers = [m for m in markers if m.time >= since]
    return {
        "days": days, "since": round(since, 3) if since is not None else None,
        "oldest": round(oldest, 3) if oldest else None, "generated": round(now, 3),
        "counts": counts(groups), "incidents": [incident(g) for g in groups],
        "markers": [{"time": round(m.time, 3), "kind": m.role, "text": m.text} for m in reversed(markers)],
        "errors": list(errors),
    }
