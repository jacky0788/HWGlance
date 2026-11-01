"""遊戲 FPS：用 Intel 官方的 PresentMon（MIT，scripts/fetch_presentmon.py 下載到 vendor/presentmon）讀 Windows 的
ETW 事件。不注入遊戲（不會被反作弊誤判），所以疊加層是置頂視窗、不是畫進遊戲裡（SPEC D29）。
要系統管理員（HWGlance 本來就是）；只在迷你模式開著、有勾 FPS 的項目時才跑，關掉就停。

PresentMon 一行一個畫面（CSV，--output_stdout）：取 ProcessID、Application、CPUStartTime（開始記錄後的毫秒；
1.x 是 TimeInSeconds）、MsBetweenPresents（沒有就用 MsCPUBusy＋MsCPUWait）。前景視窗的程式：
FPS＝1000 ÷ 最近 1 秒的平均幀時間；1% Low＝最近 10 秒第 99 百分位的幀時間換成 FPS。
FrameLog 與 parse_header／parse_row 是純函式，tests/test_fps.py 用假的輸出測；agent 不自己跑真的 PresentMon。
"""
from __future__ import annotations

import csv
import ctypes
import math
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

SESSION = "HWGlance"
ARGS = ("--output_stdout", "--no_console_stats", "--stop_existing_session", "--session_name", SESSION,
        # 只要 CPU 這邊的畫面間隔：不追蹤畫面顯示、GPU、輸入，PresentMon 自己也比較省
        "--no_track_display", "--no_track_gpu", "--no_track_input")
CREATE_NO_WINDOW = 0x08000000
WINDOW_MS = 1000.0                   # FPS、幀時間看最近幾毫秒
LOW_MS = 10_000.0                    # 1% Low 看最近幾毫秒
STALE_MS = 1500.0                    # 前景程式這麼久沒有新畫面＝沒在畫（暫停、切到別的視窗）
SILENT_S = 3.0                       # PresentMon 這麼久沒有任何輸出＝資料太舊，先不顯示
RESTART_GAP = 10.0                   # PresentMon 意外結束後，隔多久才再試


@dataclass(frozen=True)
class FpsStats:
    app: str
    fps: float
    frametime: float                 # 毫秒
    low1: float | None               # 1% Low（FPS）；畫面太少時沒有


@dataclass(frozen=True)
class Columns:
    pid: int
    app: int
    time: int | None
    time_scale: float                # 換成毫秒
    between: int | None              # MsBetweenPresents
    busy: int | None                 # MsCPUBusy
    wait: int | None                 # MsCPUWait
    count: int


def parse_header(cells: list[str]) -> Columns | None:
    names = {c.strip().lower(): i for i, c in enumerate(cells)}
    if "processid" not in names or "application" not in names:
        return None
    time_col, scale = None, 1.0
    for name, factor in (("cpustarttime", 1.0), ("timeinseconds", 1000.0), ("timeinms", 1.0)):
        if name in names:
            time_col, scale = names[name], factor
            break
    between = names.get("msbetweenpresents")
    busy, wait = names.get("mscpubusy"), names.get("mscpuwait")
    if between is None and (busy is None or wait is None):
        return None
    return Columns(names["processid"], names["application"], time_col, scale, between, busy, wait, len(cells))


def _float(cells: list[str], index: int | None) -> float | None:
    if index is None or index >= len(cells):
        return None
    try:
        value = float(cells[index])
    except ValueError:                                    # 'NA'
        return None
    return value if math.isfinite(value) else None


def parse_row(cols: Columns, cells: list[str]) -> tuple[int, str, float | None, float] | None:
    """(pid, 程式名稱, 時間毫秒或 None, 幀時間毫秒)；不是畫面的行回傳 None。"""
    if len(cells) < cols.count:
        return None
    try:
        pid = int(cells[cols.pid])
    except ValueError:
        return None
    frame = _float(cells, cols.between)
    if frame is None:
        busy, wait = _float(cells, cols.busy), _float(cells, cols.wait)
        frame = busy + wait if busy is not None and wait is not None else None
    if frame is None or frame <= 0:
        return None
    t = _float(cells, cols.time)
    return pid, cells[cols.app], (t * cols.time_scale if t is not None else None), frame


class FrameLog:
    """每個程式最近 10 秒的畫面：(時間毫秒, 幀時間毫秒)。時間用 PresentMon 自己的（開始記錄後的毫秒）。"""

    def __init__(self, keep_ms: float = LOW_MS) -> None:
        self.keep_ms = keep_ms
        self.latest = 0.0
        self._frames: dict[int, deque[tuple[float, float]]] = {}
        self._apps: dict[int, str] = {}
        self._lock = threading.Lock()

    def add(self, pid: int, app: str, t: float | None, frame: float) -> None:
        with self._lock:
            if t is None:                                 # 沒有時間欄：用累加的幀時間
                t = self.latest + frame
            self.latest = max(self.latest, t)
            frames = self._frames.setdefault(pid, deque())
            frames.append((t, frame))
            self._apps[pid] = app
            limit = self.latest - self.keep_ms
            while frames and frames[0][0] < limit:
                frames.popleft()
            if len(self._frames) > 64:                    # 很多程式來來去去：丟掉最久沒畫的
                for old in [p for p, f in self._frames.items() if not f or f[-1][0] < limit]:
                    self._frames.pop(old, None)
                    self._apps.pop(old, None)

    def stats(self, pid: int) -> FpsStats | None:
        with self._lock:
            frames = list(self._frames.get(pid, ()))
            latest = self.latest
        if not frames or frames[-1][0] < latest - STALE_MS:
            return None
        recent = [f for t, f in frames if t > latest - WINDOW_MS] or [frames[-1][1]]
        frametime = sum(recent) / len(recent)
        low = None
        if len(frames) >= 20:
            ordered = sorted(f for _, f in frames)
            low = 1000 / ordered[min(len(ordered) - 1, math.ceil(len(ordered) * 0.99) - 1)]
        return FpsStats(self._apps.get(pid, ""), 1000 / frametime, frametime, low)


def foreground_pid() -> int | None:
    if sys.platform != "win32":
        return None
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value or None


def find_exe(folder: Path) -> Path | None:
    found = sorted(folder.glob("PresentMon*.exe")) if folder.is_dir() else []
    return found[-1] if found else None


class PresentMonFps:
    """跑 PresentMon、讀它的輸出。want(True／False) 由迷你模式每次重畫時呼叫：要就開、不要就關。"""

    def __init__(self, folder: Path, admin: bool, *, spawn: Callable[[list[str]], subprocess.Popen] | None = None,
                 cleanup: Callable[[list[str]], object] | None = None,
                 foreground: Callable[[], int | None] = foreground_pid,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.folder = folder
        self.admin = admin
        self._spawn = spawn or self._popen
        self._cleanup = cleanup or self._run_quietly
        self._foreground = foreground
        self._clock = clock
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._log = FrameLog()
        self._error: str | None = None
        self._last_start = -RESTART_GAP
        self._last_line = 0.0

    def status(self) -> str | None:
        """None＝可以讀；不然是原因（迷你模式那一行直接顯示）。"""
        if not self.admin:
            return "FPS 要用系統管理員執行 HWGlance"
        if find_exe(self.folder) is None:
            return "還沒有 PresentMon：重新執行 run.bat 會下載"
        return self._error

    def want(self, on: bool) -> None:
        with self._lock:
            running = self._proc is not None and self._proc.poll() is None
            if on and not running and self.status_for_start() and self._clock() - self._last_start >= RESTART_GAP:
                self._start()
            elif not on and self._proc is not None:
                proc, self._proc = self._proc, None
                threading.Thread(target=self._stop_process, args=(proc,), daemon=True,
                                 name="hwglance-fps-stop").start()

    def status_for_start(self) -> bool:
        return self.admin and find_exe(self.folder) is not None

    def stats(self) -> FpsStats | None:
        if self._proc is None or self._clock() - self._last_line > SILENT_S:
            return None
        pid = self._foreground()
        return self._log.stats(pid) if pid else None

    def stop(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
        if proc is not None:
            self._stop_process(proc)

    def _start(self) -> None:
        exe = find_exe(self.folder)
        self._last_start = self._clock()
        self._log = FrameLog()
        try:
            proc = self._spawn([str(exe), *ARGS])
        except OSError as e:
            self._error = f"PresentMon 開不起來：{e}"
            return
        self._proc, self._error = proc, None
        print(f"[{time.strftime('%X')}] 開始讀 FPS（PresentMon pid {proc.pid}）", flush=True)
        threading.Thread(target=self._read, args=(proc,), daemon=True, name="hwglance-fps").start()

    def _read(self, proc: subprocess.Popen) -> None:
        cols: Columns | None = None
        noise: list[str] = []
        try:
            for raw in proc.stdout:                      # type: ignore[union-attr]
                line = raw.rstrip("\r\n")
                if not line:
                    continue
                cells = next(csv.reader([line]))
                if cols is None:
                    cols = parse_header(cells)
                    if cols is None and len(noise) < 5:
                        noise.append(line[:200])          # 錯誤訊息（例如沒權限）：記到 log
                    continue
                row = parse_row(cols, cells)
                if row is not None:
                    self._log.add(*row)
                    self._last_line = self._clock()
        except (OSError, ValueError):
            traceback.print_exc()
        code = proc.wait()
        with self._lock:
            mine = self._proc is proc
            if mine:
                self._proc = None
        if mine:                                          # 不是我們叫它停的
            self._error = f"PresentMon 結束了（代碼 {code}），{RESTART_GAP:.0f} 秒後再試"
            print(f"[{time.strftime('%X')}] {self._error}：{' / '.join(noise)}", flush=True)

    def _stop_process(self, proc: subprocess.Popen) -> None:
        """砍掉之後 ETW 記錄工作階段可能還在（不是正常結束），再叫 PresentMon 把它停掉。"""
        try:
            proc.terminate()
            proc.wait(5)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
        exe = find_exe(self.folder)
        if exe is not None:
            self._cleanup([str(exe), "--terminate_existing_session", "--session_name", SESSION])
        print(f"[{time.strftime('%X')}] 停止讀 FPS", flush=True)

    @staticmethod
    def _popen(args: list[str]) -> subprocess.Popen:
        return subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", bufsize=1,
                                creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0)

    @staticmethod
    def _run_quietly(args: list[str]) -> None:
        try:
            subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=10, creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        except (OSError, subprocess.SubprocessError):
            pass


class DemoFps:
    """示範資料：像一個在跑的遊戲（140 FPS 上下），不用 PresentMon。"""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock

    def status(self) -> str | None:
        return None

    def want(self, on: bool) -> None:
        pass

    def stats(self) -> FpsStats:
        t = self._clock()
        fps = 140 + 6 * math.sin(t / 3) + 2 * math.sin(t * 1.7)
        return FpsStats("DemoGame.exe", fps, 1000 / fps, 112 + 4 * math.sin(t / 7))

    def stop(self) -> None:
        pass
