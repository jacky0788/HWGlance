"""背景執行緒：每秒向感測來源要一次讀數，整理成快照（normalize），並記住最近的歷史。

API 只讀這裡快取好的快照，不會因為有人一直重新整理就多讀硬體。硬碟的 SMART 讀得慢、溫度也變得慢，
每 slow_every 輪才讀一次（預設 10 秒）。
"""
from __future__ import annotations

import json
import threading
import time
import traceback
from collections.abc import Callable
from typing import Protocol

from .. import system
from .details import device_details
from .history import History
from .model import RawHardware
from .normalize import cpu_tjmax, history_values, normalize
from .thresholds import Band


class Source(Protocol):
    kind: str
    version: str | None

    def open(self) -> None: ...
    def update(self, slow: bool = True) -> None: ...
    def read(self) -> list[RawHardware]: ...
    def close(self) -> None: ...
    # 可以沒有：errors（讀失敗的訊息）、notes()（畫面上方的提醒）、specs()、gpu_processes()


def dumps(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class SensorService:
    def __init__(self, source: Source, *, interval: float = 1.0, slow_every: int = 10,
                 bands: dict[str, Band] | None = None, history_seconds: int = 600,
                 env: dict | None = None) -> None:
        """env：{"admin": bool, "pawnio": 版本或 None}；不給就在 open() 時查這台電腦（測試用假的）。

        interval、bands、prefs 可以在執行中直接換（設定頁存檔時），下一輪就生效。"""
        self.source = source
        self.interval = interval
        self.slow_every = max(1, slow_every)
        self.bands = bands
        self.prefs: dict = {"unit": "C", "colors": {}}   # 原樣放進快照，給前端決定怎麼顯示
        self._listeners: list[Callable[[dict], None]] = []
        self.history = History(max(10, round(history_seconds / interval)))
        self._env = env
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ticks = 0
        self._state = "starting"
        self._error: str | None = None
        self._raw: list[RawHardware] = []
        self._specs: dict = {}
        self._gpu_procs: tuple[float, list[dict] | None] | None = None
        self._snap = self._empty()
        self._json = dumps(self._snap)

    # ---- 給 API 用（任何執行緒都可以呼叫） ----

    def snapshot(self) -> dict:
        with self._lock:
            return self._snap

    def snapshot_json(self) -> bytes:
        with self._lock:
            return self._json

    def raw(self) -> list[dict]:
        with self._lock:
            tree = self._raw
        return [hw.to_dict() for hw in tree]

    def details(self, device_id: str) -> dict | None:
        """點硬體跳出的詳細資訊（details.py）；用最近一輪的讀數與開啟時讀的規格，不會多讀硬體。
        顯示卡另外附上佔用顯存的程式（只有詳細視窗開著時才查，2 秒內重複用上一次的）。"""
        with self._lock:
            snap, tree, specs = self._snap, self._raw, self._specs
        kind = next((d["kind"] for d in snap["devices"] if d["id"] == device_id), None)
        procs = self.gpu_processes() if kind == "gpu" else None
        return device_details(tree, snap, device_id, specs, procs)

    def cpu_tjmax(self) -> float | None:
        """這台 CPU 的 TjMax（Intel 才有，normalize.cpu_tjmax）；設定頁的 CPU 預設門檻照它算。"""
        with self._lock:
            tree = self._raw
        return next((t for hw in tree if hw.type == "Cpu" and (t := cpu_tjmax(hw)) is not None), None)

    def disk_models(self) -> dict[str, str]:
        """{磁碟編號: 型號}（開啟後讀的規格）；事件記錄拿來把「硬碟 3」寫成型號。規格還沒讀到就是空的。"""
        with self._lock:
            disks = self._specs.get("disks") or {}
        return {number: d["model"] for number, d in disks.items() if d.get("model")}

    def forget_gpu_processes(self) -> None:
        """關掉了某個程式：下一次詳細資訊重查，不等 2 秒的快取（排行馬上少一行）。"""
        self._gpu_procs = None

    def gpu_processes(self) -> list[dict] | None:
        reader = getattr(self.source, "gpu_processes", None)
        if not callable(reader):
            return None
        now = time.monotonic()
        cached = self._gpu_procs
        if cached is not None and now - cached[0] < 2.0:
            return cached[1]
        try:
            procs = reader()
        except Exception:                            # noqa: BLE001 - 查不到就不列那一段
            traceback.print_exc()
            procs = None
        self._gpu_procs = (now, procs)
        return procs

    def subscribe(self, listener: Callable[[dict], None]) -> None:
        """每讀完一輪就在感測執行緒裡呼叫 listener(快照)；要很快做完（例如系統匣換圖示）。"""
        self._listeners.append(listener)

    # ---- 執行緒 ----

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="hwglance-sensors")
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def open(self) -> bool:
        if self._env is None:
            self._env = {"admin": system.is_admin(), "pawnio": system.pawnio_version()}
        try:
            self.source.open()
        except Exception as e:                       # noqa: BLE001 - 開不起來要顯示在畫面上，不是當掉
            traceback.print_exc()
            self._set_failed(f"感測器開不起來：{e}")
            return False
        self._state = "running"
        return True

    def load_specs(self) -> None:
        """規格（BIOS、CPU 核心數、硬碟韌體…）只讀一次；在感測執行緒裡、第一輪讀數之後，不拖慢第一個畫面。"""
        reader = getattr(self.source, "specs", None)
        if not callable(reader):
            return
        try:
            specs = reader() or {}
        except Exception:                            # noqa: BLE001 - 沒有規格只是詳細資訊少幾行
            traceback.print_exc()
            return
        with self._lock:
            self._specs = specs

    def _run(self) -> None:
        if not self.open():
            return
        try:
            next_at = time.monotonic()
            while True:
                self.tick()
                if self._ticks == 1:
                    self.load_specs()
                next_at += self.interval
                delay = next_at - time.monotonic()
                if delay < -self.interval:           # 落後太多（例如電腦剛從睡眠醒來）就重新對時
                    next_at, delay = time.monotonic(), 0.0
                if self._stop.wait(max(0.0, delay)):
                    break
        finally:
            try:
                self.source.close()
            except Exception:                        # noqa: BLE001 - 結束時的錯誤不重要
                traceback.print_exc()

    def tick(self) -> None:
        """讀一輪：更新 → 讀樹 → 整理 → 記歷史 → 換掉快照。讀失敗就保留上一份快照。"""
        slow = self._ticks % self.slow_every == 0
        self._ticks += 1
        started = time.perf_counter()
        try:
            self.source.update(slow=slow)
            tree = self.source.read()
        except Exception as e:                       # noqa: BLE001
            traceback.print_exc()
            self._error = f"讀取失敗：{e}"
            with self._lock:                         # 保留上一份讀數，但讓畫面看得到錯誤
                snap = {**self._snap, "state": "error", "source": self._source_info()}
                self._snap, self._json = snap, dumps(snap)
            return
        took_ms = (time.perf_counter() - started) * 1000
        self._error = None
        snap = normalize(tree, self.bands)
        now = time.time()
        snap.update(seq=self._ticks, time=round(now, 3), interval=self.interval, state=self._state,
                    source=self._source_info(), perf={"readMs": round(took_ms, 1)}, prefs=dict(self.prefs))
        self.history.push(now, history_values(snap))
        encoded = dumps(snap)
        with self._lock:
            self._snap, self._json, self._raw = snap, encoded, tree
        for listener in self._listeners:
            try:
                listener(snap)
            except Exception:                        # noqa: BLE001 - 訂閱者出錯不能拖垮讀數
                traceback.print_exc()

    # ---- 快照的其他欄位 ----

    def _source_info(self) -> dict:
        env = self._env or {"admin": False, "pawnio": None}
        notes = system.hints(self.source.kind, env["admin"], env["pawnio"])
        source_notes = getattr(self.source, "notes", None)          # 哪一類暫停讀取、重新連上（guard.py）
        if callable(source_notes):
            notes.extend(source_notes())
        errors = getattr(self.source, "errors", None) or []
        if self._error:
            notes.append(self._error)
        notes.extend(f"讀取失敗：{e}" for e in errors[:3])
        return {"kind": self.source.kind, "version": self.source.version, "admin": env["admin"],
                "pawnio": env["pawnio"], "notes": notes}

    def _empty(self) -> dict:
        return {"seq": 0, "time": None, "interval": self.interval, "state": self._state,
                "source": {"kind": self.source.kind, "version": None, "admin": False, "pawnio": None,
                           "notes": [self._error] if self._error else []},
                "perf": {}, "prefs": dict(self.prefs), "devices": [], "overview": []}

    def _set_failed(self, message: str) -> None:
        self._state = "error"
        self._error = message
        snap = self._empty()
        snap["source"] = self._source_info()
        with self._lock:
            self._snap, self._json = snap, dumps(snap)
