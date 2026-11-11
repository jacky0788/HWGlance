"""黑盒子（sensors/blackbox.py）：每 2 秒記一筆、換日換檔、留 7 天、讀出一段時間（斷開、尖峰、寫一半的行）。
快照用示範資料，寫在 tmp_path，不開執行緒的地方直接呼叫 drain()／stop()。"""
from __future__ import annotations

import json
import time
from datetime import date, datetime

from hwglance.events.demo import DemoEventLog
from hwglance.sensors.blackbox import BlackBox, DemoBlackBox, summarize
from hwglance.sensors.demo import DemoSource
from hwglance.sensors.normalize import normalize

NOW = 1_793_420_000.0
# 寫檔的測試用固定日期（2026-10-31、11-01）：開新的一天的檔時會順便刪掉「離今天超過 7 天」的檔，
# 過了那幾天就會把測試剛寫的檔刪掉（Mac 連開著的檔都刪得掉）。刪舊檔另外用 prune(today=…) 測
KEEP_ALL = 100_000
CPU = "/amdcpu/0/temperature/2"
GPU = "/gpu-nvidia/0/temperature/0"


def snapshot(t: float, *, gpu: bool = True) -> dict:
    snap = normalize(DemoSource(seed=1).read())
    if not gpu:                                         # 例如顯示卡被拔掉：鍵變了
        snap["devices"] = [d for d in snap["devices"] if d["kind"] != "gpu"]
    snap["time"] = t
    return snap


def lines(path) -> list:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_records_every_two_seconds_with_a_header(tmp_path):
    box = BlackBox(tmp_path, keep_days=KEEP_ALL)
    t0 = datetime(2026, 11, 1, 12, 0, 0).timestamp()
    for i in range(5):                                  # 讀數每秒一份
        box.record(snapshot(t0 + i))
    box.stop()
    [path] = list(tmp_path.iterdir())
    assert path.name == "2026-11-01.jsonl"
    head, *records = lines(path)
    assert [r[0] for r in records] == [t0, t0 + 2, t0 + 4]
    assert head["keys"][0] == CPU and len(head["keys"]) == len(records[0]) - 1 == 17
    meta = dict(zip(head["keys"], head["meta"]))
    assert meta[CPU] == {"label": "CPU", "unit": "°C", "kind": "cpu", "role": "cpu", "device": "/amdcpu/0"}
    assert meta["/gpu-nvidia/0#power"]["label"] == "GPU 功耗" and meta["/nvme/5/temperature/0"]["label"] == "KIOXIA-EXCERIA G2"


def test_new_header_when_hardware_changes_and_a_new_file_each_day(tmp_path):
    box = BlackBox(tmp_path, keep_days=KEEP_ALL)
    t0 = datetime(2026, 10, 31, 23, 59, 56).timestamp()
    box.record(snapshot(t0))
    box.record(snapshot(t0 + 2, gpu=False))
    box.record(snapshot(t0 + 6))                        # 隔天 00:00:02
    box.stop()
    first, second = sorted(tmp_path.iterdir())
    assert (first.name, second.name) == ("2026-10-31.jsonl", "2026-11-01.jsonl")
    kinds = [type(x).__name__ for x in lines(first)]
    assert kinds == ["dict", "list", "dict", "list"]      # 鍵變了就再寫一次說明
    window = box.read(t0 - 1, t0 + 10)
    assert window["count"] == 3 and window["series"][GPU][1] is None


def test_prune_keeps_seven_days_and_only_touches_its_own_files(tmp_path):
    for name in ("2026-10-21.jsonl", "2026-10-24.jsonl", "2026-10-25.jsonl", "2026-10-30.jsonl", "notes.txt"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    removed = BlackBox(tmp_path).prune(today=date(2026, 11, 1))
    assert sorted(removed) == ["2026-10-21.jsonl", "2026-10-24.jsonl"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["2026-10-25.jsonl", "2026-10-30.jsonl", "notes.txt"]


def test_read_marks_gaps_and_skips_a_half_written_line(tmp_path):
    box = BlackBox(tmp_path, keep_days=KEEP_ALL)
    t0 = datetime(2026, 11, 1, 2, 0, 0).timestamp()
    for i in range(11):
        box.record(snapshot(t0 + 2 * i))
    for i in range(11):                                 # 中間停了 2 分鐘（當機、重新開機）
        box.record(snapshot(t0 + 140 + 2 * i))
    box.stop()
    path = tmp_path / "2026-11-01.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"[{t0 + 200},52.")                    # 當機時只寫了一半
    w = box.read(t0, t0 + 300)
    assert w["count"] == 22 and (w["first"], w["last"]) == (t0, t0 + 160)
    assert w["gaps"] == [[t0 + 20, t0 + 140]]
    gap_at = w["t"].index(t0 + 22)                      # 斷開的地方補一個空的點，折線才會斷
    assert w["series"][CPU][gap_at] is None and w["series"][CPU][gap_at + 1] is not None
    cpu = next(k for k in w["keys"] if k["key"] == CPU)
    assert cpu["label"] == "CPU" and cpu["level"] in ("ok", "warm", "hot") and cpu["max"] is not None
    assert "level" not in next(k for k in w["keys"] if k["unit"] == "W")


def test_bad_header_skips_records_until_the_next_one(tmp_path):
    t0 = datetime(2026, 11, 1, 3, 0, 0).timestamp()
    (tmp_path / "2026-11-01.jsonl").write_text(
        '{"keys": ["a"], "meta": [{"label": "A", "unit": "°C", "role": "cpu"}]}\n'
        f'[{t0}, 50]\n'
        '{"keys": [壞掉\n'
        f'[{t0 + 2}, 99]\n'
        '{"keys": ["a"]}\n'
        f'[{t0 + 4}, 51]\n', encoding="utf-8")
    w = BlackBox(tmp_path).read(t0, t0 + 10)
    assert w["count"] == 2 and w["series"]["a"] == [50, 51] and w["keys"][0]["max"] == 51


def test_many_points_are_bucketed_keeping_peaks():
    rows = [(1000.0 + 2 * i, {"cpu": 50.0 + (40 if i == 777 else 0)}) for i in range(1000)]
    w = summarize(rows, {"cpu": {"label": "CPU", "unit": "°C", "role": "cpu"}}, 1000.0, 3000.0, max_points=100)
    assert len(w["t"]) == 100 and w["step"] == 20.0 and max(w["series"]["cpu"]) == 90.0
    assert w["keys"][0]["level"] == "hot" and w["gaps"] == []


def test_write_errors_do_not_stop_the_writer(tmp_path):
    blocked = tmp_path / "blackbox"
    blocked.write_text("這是檔案，不是資料夾", encoding="utf-8")
    box = BlackBox(blocked)
    box.start()
    box.record(snapshot(NOW))
    for _ in range(50):
        if box.error:
            break
        time.sleep(0.05)
    assert box.error and box.error.startswith("黑盒子寫不進去")
    box.stop(timeout=2.0)
    assert not box._thread.is_alive()


def test_demo_black_box_stops_at_crashes_and_keeps_seven_days():
    log = DemoEventLog(now=NOW)
    box = DemoBlackBox(log.downs, clock=lambda: NOW)
    crash, boot = log.downs()[0]                        # 11 小時前那次藍屏
    w = box.read(crash - 300, boot + 300)
    assert w["count"] > 100 and w["gaps"] and crash - 4 <= w["gaps"][0][0] < crash
    assert w["gaps"][0][1] >= boot + DemoBlackBox.REOPEN
    assert box.read(NOW - 70 * 86400, NOW - 70 * 86400 + 600)["count"] == 0      # 黑盒子只留 7 天
