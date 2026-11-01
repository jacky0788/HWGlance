"""遊戲 FPS（fps.py）：PresentMon 的 CSV 怎麼讀、FPS／幀時間／1% Low 怎麼算、程序的開關。
不跑真的 PresentMon：用一個印出同樣格式的 Python 小程式代替。"""
from __future__ import annotations

import subprocess
import sys
import textwrap
import time

from hwglance.fps import FrameLog, PresentMonFps, find_exe, parse_header, parse_row

HEADER = ("Application,ProcessID,SwapChainAddress,PresentRuntime,SyncInterval,PresentFlags,FrameType,CPUStartTime,"
          "MsCPUBusy,MsCPUWait,MsBetweenPresents,MsInPresentAPI")


def line(app: str, pid: int, t: float, frame: float) -> list[str]:
    return [app, str(pid), "0x1", "DXGI", "0", "0", "Application", f"{t:.4f}", f"{frame * 0.6:.4f}",
            f"{frame * 0.4:.4f}", f"{frame:.4f}", "0.1"]


def wait_until(cond, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def test_header_variants():
    cols = parse_header(HEADER.split(","))
    assert cols is not None and cols.time_scale == 1.0 and cols.between == 10
    v1 = parse_header("Application,ProcessID,SwapChainAddress,Runtime,TimeInSeconds,msBetweenPresents".split(","))
    assert v1 is not None and v1.time_scale == 1000.0                  # 1.x 的時間是秒
    busy_only = parse_header("Application,ProcessID,CPUStartTime,MsCPUBusy,MsCPUWait".split(","))
    assert busy_only is not None and parse_row(busy_only, ["a.exe", "7", "12.5", "4", "3"]) == (7, "a.exe", 12.5, 7.0)
    assert parse_header(["error: failed to start trace session (access denied)"]) is None
    assert parse_header("Application,ProcessID,CPUStartTime".split(",")) is None    # 沒有幀時間


def test_rows_that_are_not_frames_are_skipped():
    cols = parse_header(HEADER.split(","))
    assert parse_row(cols, ["short"]) is None
    assert parse_row(cols, line("a.exe", 1, 10, 0)) is None                       # 幀時間 0
    bad = line("a.exe", 1, 10, 5)
    bad[10] = bad[8] = "NA"
    assert parse_row(cols, bad) is None


def test_fps_frametime_and_one_percent_low():
    log = FrameLog()
    t = 0.0
    for n in range(720):                                     # 5 秒、144 FPS，每 50 個有一個 20 ms 的卡頓（2%）
        frame = 20.0 if n % 50 == 49 else 6.944
        t += frame
        log.add(1, "game.exe", t, frame)
    log.add(2, "browser.exe", 100.0, 16.7)                   # 很久以前畫過一次
    stats = log.stats(1)
    assert stats is not None and stats.app == "game.exe"
    assert 120 <= stats.fps <= 145 and 6.9 <= stats.frametime <= 8.5
    assert stats.low1 is not None and abs(stats.low1 - 50.0) < 0.5        # 最慢的 1% 是 20 ms
    assert log.stats(2) is None and log.stats(3) is None                    # 停了、沒畫過


def test_frames_without_a_time_column_still_count():
    log = FrameLog()
    for _ in range(50):
        log.add(5, "x.exe", None, 10.0)
    assert round(log.stats(5).fps) == 100


def test_status_reasons(tmp_path):
    assert "系統管理員" in PresentMonFps(tmp_path, admin=False).status()
    assert "還沒有 PresentMon" in PresentMonFps(tmp_path, admin=True).status()
    (tmp_path / "PresentMon-2.6.0-x64.exe").write_bytes(b"")
    assert find_exe(tmp_path).name == "PresentMon-2.6.0-x64.exe"
    assert PresentMonFps(tmp_path, admin=True).status() is None


def fake_presentmon(tmp_path, body: str):
    """假的 PresentMon：在 tmp_path 放一個空的 exe（讓 find_exe 找得到）＋真正會跑的 Python 小程式。"""
    (tmp_path / "PresentMon-2.6.0-x64.exe").write_bytes(b"")
    script = tmp_path / "fake_presentmon.py"
    script.write_text(textwrap.dedent(body), encoding="utf-8")
    started: list[list[str]] = []

    def spawn(args: list[str]) -> subprocess.Popen:
        started.append(args)
        return subprocess.Popen([sys.executable, "-I", str(script)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", bufsize=1)

    return spawn, started


def test_presentmon_output_is_read_for_the_foreground_game_and_stopped(tmp_path):
    spawn, started = fake_presentmon(tmp_path, f"""
        import sys, time
        print("PresentMon 2.6.0 - starting", flush=True)
        print({HEADER!r}, flush=True)
        t = 0.0
        for n in range(400):
            t += 10.0
            print(f"game.exe,4242,0x1,DXGI,0,0,Application,{{t}},6,4,10,0.1", flush=True)
            print(f"other.exe,99,0x2,DXGI,0,0,Application,{{t}},10,6.7,16.7,0.1", flush=True)
        time.sleep(30)
    """)
    cleanup: list[list[str]] = []
    fps = PresentMonFps(tmp_path, admin=True, spawn=spawn, cleanup=cleanup.append, foreground=lambda: 4242)
    fps.want(True)
    assert wait_until(lambda: fps.stats() is not None and fps.stats().app == "game.exe")
    assert "--output_stdout" in started[0] and "--session_name" in started[0]
    assert round(fps.stats().fps) == 100 and fps.status() is None
    fps.want(True)                                           # 已經在跑：不會再開一個
    assert len(started) == 1
    fps.want(False)
    assert wait_until(lambda: cleanup != [])                 # 砍掉之後叫它把 ETW 工作階段停掉
    assert "--terminate_existing_session" in cleanup[0] and fps.stats() is None


def test_presentmon_exit_is_reported_and_retried_later(tmp_path):
    spawn, started = fake_presentmon(tmp_path, """
        import sys
        print("error: failed to start trace session (access denied)", flush=True)
        sys.exit(5)
    """)
    now = [100.0]
    fps = PresentMonFps(tmp_path, admin=True, spawn=spawn, cleanup=lambda args: None, foreground=lambda: 1,
                        clock=lambda: now[0])
    fps.want(True)
    assert wait_until(lambda: fps.status() is not None and "代碼 5" in fps.status())
    fps.want(True)
    assert len(started) == 1                                 # 剛結束：先不重開
    now[0] += 11
    fps.want(True)
    assert len(started) == 2
    fps.stop()
