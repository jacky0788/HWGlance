"""事件記錄的快取：打開事件頁時才讀，60 秒內重複用上一次的結果。

讀的時候在呼叫的執行緒（API 的 worker）裡做，不碰感測執行緒；同時只讀一份（90 天的記錄這台約 1 秒）。
"""
from __future__ import annotations

import threading
import time
import traceback
from collections.abc import Callable, Iterable, Mapping
from typing import Protocol

from .catalog import WATCHED, classify
from .incidents import report
from .model import RawEvent

CHANNEL_NAMES = {"System": "系統", "Application": "應用程式"}


class EventLog(Protocol):
    kind: str

    def read(self, channel: str, providers: Mapping[str, Iterable[int] | None], days: float | None) -> list[RawEvent]: ...
    def oldest(self, channel: str) -> float | None: ...


class EventService:
    def __init__(self, log: EventLog, disks: Callable[[], Mapping[str, str]] = dict, *, ttl: float = 60.0,
                 clock: Callable[[], float] = time.time) -> None:
        """disks：{磁碟編號: 型號}（感測服務讀到的規格），拿來把「硬碟 3」寫成型號。"""
        self.log = log
        self.disks = disks
        self.ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._cache: dict[int | None, tuple[float, dict]] = {}

    def report(self, days: int | None) -> dict:
        """days=None（或 0）是全部。"""
        key = days or None
        with self._lock:
            now = self._clock()
            hit = self._cache.get(key)
            if hit is not None and now - hit[0] < self.ttl:
                return hit[1]
            payload = self._build(key, now)
            self._cache[key] = (now, payload)
            return payload

    def _build(self, days: int | None, now: float) -> dict:
        entries_of = getattr(self.log, "entries", None)
        if callable(entries_of):                     # Mac（events/mac.py）：直接給整理好的 Entry，沒有 Windows 的頻道與事件代號
            try:
                entries, errors = entries_of(days), list(getattr(self.log, "errors", []))
            except OSError as e:
                entries, errors = [], [f"讀不到記錄：{e.strerror or e}"]
            return {"source": self.log.kind,
                    **report(entries, days=days, oldest=self.log.oldest("System"), now=now, errors=errors)}
        disks = dict(self.disks())
        entries, errors = [], []
        for channel, providers in WATCHED.items():
            try:
                raw = self.log.read(channel, providers, days)
            except OSError as e:                     # 讀不到其中一個記錄，另一個照樣顯示
                errors.append(f"讀不到{CHANNEL_NAMES.get(channel, channel)}記錄：{e.strerror or e}")
                continue
            for ev in raw:
                try:
                    entry = classify(ev, disks)
                except Exception:                    # noqa: BLE001 - 一筆解讀失敗不該讓整頁空白
                    traceback.print_exc()
                    continue
                if entry is not None:
                    entries.append(entry)
        try:
            oldest = self.log.oldest("System")
        except OSError:
            oldest = None
        return {"source": self.log.kind, **report(entries, days=days, oldest=oldest, now=now, errors=errors)}
