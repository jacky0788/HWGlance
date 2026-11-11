"""黑盒子：每 2 秒把主要讀數寫進 data/blackbox/日期.jsonl，每 5 秒確實存到硬碟（fsync），留 7 天。

當機（藍屏、斷電、強制關機）後回頭看出事前幾分鐘的溫度、功耗、負載；記錄停在幾點，差不多就是當機的時間
（Windows 記的「最後運作時間」常常早半小時以上，見 events/incidents.py）。事件頁點開一件事時讀（GET /api/blackbox）。

記哪些：跟折線圖同一組（normalize.history_values）：每個溫度，加上各硬體的負載與功耗，這台 17 個值。
檔案一行一個 JSON：
  {"keys": [...], "meta": [{"label", "unit", "kind", "role", "device"}...]}   ← 檔案開頭、硬體有變時
  [時間, 值1, 值2, …]                                                        ← 每 2 秒一行，順序照最近的 keys
當機時最後一行可能只寫了一半，讀的時候跳過。這台一天約 5 MB，7 天約 35 MB。
寫檔在自己的執行緒：感測執行緒只把讀數排進佇列，硬碟卡住（例如 8/22 那種斷線）也不會拖住讀數。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import traceback
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import IO

from .normalize import history_values
from .thresholds import DEFAULTS, Band, level

INTERVAL = 2.0           # 幾秒記一筆
SYNC_EVERY = 5.0         # 幾秒 fsync 一次：當機時最多少這麼久
KEEP_DAYS = 7
MAX_POINTS = 360         # 一次最多回傳幾個點；超過就分段取最大值（看得到尖峰）
GAP = 3 * INTERVAL       # 兩筆隔超過這麼久＝中間沒在記錄（HWGlance 關了、電腦關機或當機），折線要斷開
FILE = re.compile(r"^(\d{4})-(\d\d)-(\d\d)\.jsonl$")
KIND_NAMES = {"cpu": "CPU", "gpu": "GPU", "board": "主機板", "memory": "記憶體", "cooler": "水冷", "psu": "電源"}


def describe(snapshot: dict) -> dict[str, dict]:
    """快照 → {鍵: 說明}，鍵跟 history_values 一樣。名稱用總覽磚塊的（CPU、GPU 核心、晶片組、硬碟型號），
    其他的加上硬體：「GPU 顯存接面」「CPU 功耗」。"""
    tiles = {t["id"]: t["label"] for t in snapshot.get("overview", [])}
    out: dict[str, dict] = {}
    for d in snapshot["devices"]:
        short = KIND_NAMES.get(d["kind"], d["name"])
        for t in d["temps"]:
            label = tiles.get(t["id"]) or (t["label"] if d["kind"] == "board" else f"{short} {t['label']}")
            out[t["id"]] = {"label": label, "unit": "°C", "kind": d["kind"], "role": t["role"], "device": d["id"]}
        for s in d["stats"]:
            if s["key"] in ("load", "power"):
                out[f'{d["id"]}#{s["key"]}'] = {"label": f"{short} {s['label']}", "unit": s["unit"], "kind": d["kind"],
                                               "role": s["key"], "device": d["id"]}
    return out


def _day(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(t))


def _round(v: float | None) -> float | None:
    return None if v is None else round(v, 1)


class BlackBox:
    def __init__(self, folder: Path, *, interval: float = INTERVAL, sync_every: float = SYNC_EVERY,
                 keep_days: int = KEEP_DAYS) -> None:
        self.folder = Path(folder)
        self.interval = interval
        self.sync_every = sync_every
        self.keep_days = keep_days
        self.error: str | None = None
        self._pending: deque[tuple[float, dict, dict]] = deque(maxlen=600)   # 寫不出去時最多留 20 分鐘
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last = 0.0
        self._file: IO[str] | None = None
        self._file_day: str | None = None
        self._keys: list[str] | None = None
        self._synced = 0.0

    # ---- 感測執行緒呼叫（SensorService.subscribe）：只排進佇列，不碰硬碟 ----

    def record(self, snapshot: dict) -> None:
        t = snapshot.get("time")
        if t is None or t - self._last < self.interval - 0.05:
            return
        self._last = t
        self._pending.append((t, history_values(snapshot), describe(snapshot)))
        self._wake.set()

    # ---- 寫檔的執行緒 ----

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="hwglance-blackbox")
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """結束：佇列裡剩下的寫完、存好、關檔。"""
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
        else:                                        # 沒開執行緒（測試）：在這裡寫完
            self.drain()
            self._close()

    def _run(self) -> None:
        self.prune()
        try:
            while True:
                self._wake.wait(1.0)
                self._wake.clear()
                stopping = self._stop.is_set()
                try:
                    self.drain()
                    if stopping or time.monotonic() - self._synced >= self.sync_every:
                        self.sync()
                except OSError as e:                  # 硬碟滿了、斷線…：記下來，關掉檔案，下一輪重開再試
                    if self.error is None:
                        traceback.print_exc()
                    self.error = f"黑盒子寫不進去：{e}"
                    self._close()
                    if self._stop.wait(5.0):
                        break
                if stopping:
                    break
        finally:
            self._close()

    def drain(self) -> None:
        """把佇列裡的讀數寫出去（還沒 fsync）。換日就換檔、刪掉太舊的。"""
        while self._pending:
            t, values, meta = self._pending[0]
            day = _day(t)
            if day != self._file_day:
                self._close()
                self.folder.mkdir(parents=True, exist_ok=True)
                self._file = open(self.folder / f"{day}.jsonl", "a", encoding="utf-8", newline="\n")
                self._file_day, self._keys = day, None
                self.prune()
            assert self._file is not None
            keys = list(values)
            if keys != self._keys:
                self._file.write(json.dumps({"keys": keys, "meta": [meta.get(k, {"label": k}) for k in keys]},
                                            ensure_ascii=False, separators=(",", ":")) + "\n")
                self._keys = keys
            self._file.write(json.dumps([round(t, 1), *(_round(values[k]) for k in keys)], separators=(",", ":")) + "\n")
            self._pending.popleft()
            self.error = None

    def sync(self) -> None:
        if self._file is not None:
            self._file.flush()
            os.fsync(self._file.fileno())
        self._synced = time.monotonic()

    def _close(self) -> None:
        """盡量存好再關；硬碟不見了就算了（緩衝裡那幾秒的紀錄沒了），不能讓寫檔的執行緒跟著停。"""
        f, self._file, self._file_day, self._keys = self._file, None, None, None
        if f is None:
            return
        try:
            f.flush()
            os.fsync(f.fileno())
        except OSError:
            traceback.print_exc()
        try:
            f.close()
        except OSError:
            pass

    def prune(self, today: date | None = None) -> list[str]:
        """刪掉超過 keep_days 天的檔（只動 日期.jsonl 這種名字的）。回傳刪了哪些。"""
        oldest = (today or date.today()) - timedelta(days=self.keep_days)
        removed = []
        try:
            names = os.listdir(self.folder)
        except OSError:
            return removed
        for name in names:
            m = FILE.match(name)
            if m and date(*map(int, m.groups())) < oldest:
                try:
                    os.remove(self.folder / name)
                    removed.append(name)
                except OSError:
                    traceback.print_exc()
        return removed

    # ---- 讀（API 的執行緒） ----

    def read(self, start: float, end: float, *, max_points: int = MAX_POINTS,
             bands: Mapping[str, Band] | None = None) -> dict:
        rows: list[tuple[float, dict[str, float | None]]] = []
        meta: dict[str, dict] = {}
        for path in self._files(start, end):
            rows += _read_file(path, start, end, meta)
        rows.sort(key=lambda r: r[0])
        return summarize(rows, meta, start, end, max_points=max_points, bands=bands, keep_days=self.keep_days)

    def _files(self, start: float, end: float) -> Iterable[Path]:
        day, last = datetime.fromtimestamp(start).date(), datetime.fromtimestamp(end).date()
        while day <= last:
            path = self.folder / f"{day.isoformat()}.jsonl"
            if path.is_file():
                yield path
            day += timedelta(days=1)


def _read_file(path: Path, start: float, end: float, meta: dict[str, dict]) -> list[tuple[float, dict]]:
    """一個檔裡 [start, end] 之間的紀錄。先只解析每行開頭的時間，範圍內的才整行解析（一天 4 萬多行）。"""
    out = []
    keys: list[str] | None = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("{"):
                try:
                    head = json.loads(line)
                    keys = list(head["keys"])
                    meta.update(zip(keys, head.get("meta") or []))
                except (ValueError, KeyError, TypeError):
                    keys = None                       # 說明那一行壞了：到下一個說明之前的都不知道是什麼，跳過
                continue
            if keys is None or not line.startswith("["):
                continue
            comma = line.find(",")
            try:
                t = float(line[1:comma])
            except ValueError:
                continue
            if t < start or t > end:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue                              # 當機時只寫了一半的最後一行
            out.append((t, dict(zip(keys, record[1:]))))
    return out


def summarize(rows: list[tuple[float, dict]], meta: Mapping[str, dict], start: float, end: float, *,
              max_points: int = MAX_POINTS, bands: Mapping[str, Band] | None = None,
              keep_days: int = KEEP_DAYS) -> dict:
    """給 GET /api/blackbox 的內容：每個值一條線（時間對齊 t），隔太久的地方斷開；太多點就分段取最大值。
    gaps＝中間沒有紀錄的時段 [最後一筆, 下一筆]：當機的話，第一筆就是「記錄停在幾點」。"""
    keys = list(dict.fromkeys(k for _, values in rows for k in values))
    # 平常的間隔（更新頻率設 5 秒時就是 5 秒）；隔超過三倍才算斷掉
    diffs = sorted(b - a for (a, _), (b, _) in zip(rows, rows[1:]))
    spacing = diffs[len(diffs) // 2] if diffs else INTERVAL
    gap = max(GAP, 3 * spacing)
    gaps = [[a, b] for (a, _), (b, _) in zip(rows, rows[1:]) if b - a > gap]
    times: list[float] = []
    series: dict[str, list[float | None]] = {k: [] for k in keys}
    if len(rows) <= max_points:
        step = spacing
        previous: float | None = None
        for t, values in rows:
            if previous is not None and t - previous > gap:
                times.append(round(previous + spacing, 1))
                for k in keys:
                    series[k].append(None)
            times.append(t)
            for k in keys:
                series[k].append(values.get(k))
            previous = t
    else:
        step = (end - start) / max_points
        buckets: list[dict[str, float]] = [{} for _ in range(max_points)]
        for t, values in rows:
            bucket = buckets[min(max_points - 1, int((t - start) / step))]
            for k, v in values.items():
                if v is not None and (k not in bucket or v > bucket[k]):
                    bucket[k] = v
        for i, bucket in enumerate(buckets):
            times.append(round(start + (i + 0.5) * step, 1))
            for k in keys:
                series[k].append(bucket.get(k))
    bands = bands or DEFAULTS
    described = []
    for k in keys:
        info = {"label": k, "unit": "", "kind": "other", "role": "", "device": ""} | dict(meta.get(k) or {})
        peak = max((v for _, values in rows if (v := values.get(k)) is not None), default=None)
        entry = {"key": k, **info, "max": peak}
        if info["unit"] == "°C":
            entry["level"] = level(peak, bands.get(info["role"], bands.get("other", DEFAULTS["other"])))
        described.append(entry)
    return {"from": start, "to": end, "first": rows[0][0] if rows else None, "last": rows[-1][0] if rows else None,
            "count": len(rows), "step": round(step, 1), "keepDays": keep_days, "gaps": gaps, "t": times,
            "keys": described, "series": series}


class DemoBlackBox:
    """示範：照 DemoSource 的模擬算出任何時段的紀錄（不寫檔）。跟真的一樣只有最近 7 天有；
    downs()＝示範事件裡電腦當掉到重新開機的時段：記錄停在當機那一刻，開機 2 分鐘後（重新打開 HWGlance）才又有。"""

    keep_days = KEEP_DAYS
    REOPEN = 120.0

    def __init__(self, downs: Callable[[], list[tuple[float, float]]], clock: Callable[[], float] = time.time,
                 source: Callable | None = None) -> None:
        """source：哪一種示範電腦（DemoSource 或它的子類別，例如 Mac 的 MacDemo）；沒給就是 DemoSource。"""
        self._downs = downs
        self._clock = clock
        self._source = source

    def read(self, start: float, end: float, *, max_points: int = MAX_POINTS,
             bands: Mapping[str, Band] | None = None) -> dict:
        from .demo import DemoSource
        from .normalize import normalize
        now = self._clock()
        lo, hi = max(start, now - self.keep_days * 86400), min(end, now)
        rows: list[tuple[float, dict]] = []
        meta: dict[str, dict] = {}
        if hi > lo:
            step = max(INTERVAL, (hi - lo) / 900)
            clock = _Clock(lo - 300)                 # 先跑 5 分鐘，溫度才不是從預設值開始
            demo = (self._source or DemoSource)(seed=int(lo) % 997, clock=clock)
            downs = self._downs()
            t = lo - 300
            while t <= hi:
                clock.now = t
                demo.update(slow=True)
                if t >= lo and not any(a <= t < b + self.REOPEN for a, b in downs):
                    snap = normalize(demo.read(), dict(bands) if bands else None)
                    if not meta:
                        meta = describe(snap)
                    rows.append((round(t, 1), {k: _round(v) for k, v in history_values(snap).items()}))
                t += step
        return summarize(rows, meta, start, end, max_points=max_points, bands=bands, keep_days=self.keep_days)


class _Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now
