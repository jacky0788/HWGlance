"""讀取的保險：某一類硬體讀失敗，或顯示卡驅動在事件記錄報錯時，先停讀那一類，穩定了再關掉重開。

為什麼：2026-10-31 10:10:01 顯示卡驅動報 FECS 致命錯誤、TDR 一直重設，10:11:15 整張卡重設（PF FLR），
10:11:24 HWGlance 照常讀顯示卡功耗，LHM 在 nvml.dll 裡存取違規，整個本體跟著結束（同時當掉的還有 HWiNFO）。

現在：
- 讀失敗的例外接得住（lhm.py 在 .NET 啟動前設 legacyCorruptedStateExceptionsPolicy），出錯的那一類先停讀；
- 顯示卡驅動一報錯（events/watch.py 訂閱系統記錄），顯示卡也先停讀，不等到重設才出事；
- 最後一次出錯滿 1 分鐘沒有新錯誤，就把那一類關掉重開（LHM 重新抓硬體、重新初始化 NVML）再讀；
  重開失敗就 2、4、8 分鐘後再試，最多隔 10 分鐘。

只管「什麼時候停、什麼時候重開」，真的關開由 lhm.py 做。執行緒安全（事件訂閱在另一個執行緒呼叫 trip）；
tests/test_guard.py 用假時鐘測。
"""
from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

QUIET = 60.0             # 最後一次出錯後，穩定多久才重開
RETRY_MAX = 600.0        # 重開失敗後最多隔多久再試
RECOVERED_NOTE = 300.0   # 重新連上後，畫面上的提醒留 5 分鐘

# 哪一類（lhm.COMPONENTS 的名稱）→ 畫面上的名稱
LABELS = {"cpu": "CPU", "gpu": "顯示卡", "motherboard": "主機板", "controller": "水冷／風扇控制器", "memory": "記憶體",
          "storage": "硬碟", "psu": "電源供應器", "network": "網路", "battery": "電池"}


def _hm(t: float) -> str:
    return time.strftime("%H:%M", time.localtime(t))


@dataclass
class _Trip:
    reason: str
    since: float          # 第一次出錯
    retry_at: float       # 這時候以後才重開；還在出錯就一直往後延
    failures: int = 0     # 重開失敗幾次
    error: str = ""       # 上一次重開失敗的原因


class Guard:
    def __init__(self, quiet: float = QUIET, clock: Callable[[], float] = time.time) -> None:
        self.quiet = quiet
        self._clock = clock
        self._lock = threading.Lock()
        self._trips: dict[str, _Trip] = {}
        self._recovered: dict[str, tuple[float, _Trip]] = {}

    def trip(self, kind: str, reason: str) -> bool:
        """這一類出錯（讀失敗或驅動報錯）：停讀，等最後一次出錯滿 quiet 秒再重開。這次才開始停的回傳 True。"""
        now = self._clock()
        with self._lock:
            trip = self._trips.get(kind)
            if trip is not None:
                trip.retry_at = max(trip.retry_at, now + self.quiet)
                return False
            self._trips[kind] = _Trip(reason, now, now + self.quiet)
            self._recovered.pop(kind, None)
        print(f"{LABELS.get(kind, kind)}暫停讀取：{reason}", flush=True)       # → data/logs/hwglance.log
        return True

    def paused(self, kind: str) -> bool:
        with self._lock:
            return kind in self._trips

    def due(self) -> list[str]:
        """現在該重開的種類。"""
        now = self._clock()
        with self._lock:
            return [kind for kind, trip in self._trips.items() if now >= trip.retry_at]

    def reopened(self, kind: str, error: str | None = None) -> None:
        """重開的結果：成功就恢復讀取（畫面上留一陣子「重新連上了」）；失敗就晚一點再試。"""
        now = self._clock()
        label = LABELS.get(kind, kind)
        with self._lock:
            trip = self._trips.get(kind)
            if trip is None:
                return
            if error is None:
                del self._trips[kind]
                self._recovered[kind] = (now, trip)
            else:
                trip.failures += 1
                trip.error = error
                trip.retry_at = now + min(RETRY_MAX, self.quiet * 2 ** trip.failures)
        print(f"{label}重新連上了" if error is None else f"{label}重新連上失敗：{error}", flush=True)

    def notes(self) -> list[str]:
        """畫面上方的提醒（快照的 source.notes）。"""
        now = self._clock()
        out: list[str] = []
        with self._lock:
            for kind, trip in self._trips.items():
                label = LABELS.get(kind, kind)
                if trip.failures:
                    wait = math.ceil(max(0.0, trip.retry_at - now) / 60)
                    out.append(f"{label}重新連上失敗（{trip.error}），{wait} 分鐘後再試；"
                               f"一直連不上就重開 HWGlance。")
                else:
                    out.append(f"{label} {_hm(trip.since)} 出錯（{trip.reason}），先暫停讀取；"
                               f"穩定 {self.quiet / 60:g} 分鐘後自動重新連上。")
            for kind, (at, trip) in list(self._recovered.items()):
                if now - at > RECOVERED_NOTE:
                    del self._recovered[kind]
                    continue
                out.append(f"{LABELS.get(kind, kind)} {_hm(at)} 重新連上了（{_hm(trip.since)} 出錯：{trip.reason}）。")
        return out
