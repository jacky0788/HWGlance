"""最近一段時間的讀數（每個感測器一條），給折線圖用。只放在記憶體，關掉就沒了。"""
from __future__ import annotations

import threading
from collections import deque
from collections.abc import Mapping


class History:
    def __init__(self, capacity: int = 600) -> None:
        self.capacity = capacity
        self._times: deque[float] = deque(maxlen=capacity)
        self._series: dict[str, deque[float | None]] = {}
        self._lock = threading.Lock()

    def push(self, t: float, values: Mapping[str, float | None]) -> None:
        """每一輪呼叫一次；每條序列都剛好多一格，所以跟時間軸永遠對齊（這一輪沒值的補 None）。"""
        with self._lock:
            self._times.append(t)
            n = len(self._times)
            for key, d in self._series.items():
                d.append(values.get(key))
            for key, v in values.items():
                if key not in self._series:
                    self._series[key] = deque([None] * (n - 1) + [v], maxlen=self.capacity)

    def window(self, seconds: float) -> dict:
        """最近 seconds 秒：{"t": [時間…], "series": {id: [數值…]}}，數值取到小數一位。"""
        with self._lock:
            if not self._times:
                return {"t": [], "series": {}}
            cutoff = self._times[-1] - seconds
            times = list(self._times)
            start = next((i for i, t in enumerate(times) if t >= cutoff), len(times))
            series = {key: [None if v is None else round(v, 1) for v in list(d)[start:]]
                      for key, d in self._series.items()}
        return {"t": [round(t, 1) for t in times[start:]], "series": series}
