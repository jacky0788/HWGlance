"""暫存資料夾裡被鎖住的檔（tempfiles.py，SPEC D33）。

全部在 pytest 的暫存資料夾裡做，**不掃、不刪這台真的暫存資料夾**：假的「暫存資料夾」裡放舊檔、新檔、
一個被測試自己開的假程式鎖住的檔、一個指到外面的 junction（外面那個檔絕對不能被刪）。
「下次開機刪除」不真的呼叫 MoveFileEx（會真的排進開機流程），用假的函式測。API 用 DemoTemp。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from hwglance import processes, tempfiles
from hwglance.api.app import create_app
from hwglance.processes import DemoProcesses, WindowsProcesses
from hwglance.sensors.demo import DemoSource
from hwglance.sensors.service import SensorService
from hwglance.tempfiles import DemoTemp, Root, TempCleaner, inside, normalize

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows 才有")
LATER = 3 * 86400                       # 假時鐘：比現在晚 3 天，剛建的檔都算「放超過 1 天」


def test_paths():
    assert normalize("\\\\?\\C:\\Users\\X\\Temp") == os.path.normcase("C:\\Users\\X\\Temp")
    root = normalize("C:\\Temp")
    assert inside(normalize("C:\\Temp\\a\\b.txt"), root) and not inside(root, root)
    assert not inside(normalize("C:\\Temp2\\x"), root) and not inside(normalize("C:\\Te"), root)


class FakeLog:
    def __init__(self) -> None:
        self.lines: list[dict] = []

    def write(self, **fields) -> None:
        self.lines.append(fields)


def hold_open(path) -> subprocess.Popen:
    """測試自己開的假程式：把檔案開著不放（Python 的 open 不允許別人刪），等著被關。"""
    python = getattr(sys, "_base_executable", sys.executable)
    code = f"f = open(r'{path}', 'a'); print('ready', flush=True); import time; time.sleep(60)"
    proc = subprocess.Popen([python, "-c", code], stdout=subprocess.PIPE, text=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert proc.stdout.readline().strip() == "ready"
    return proc


@pytest.fixture
def fake_temp(tmp_path):
    temp, outside = tmp_path / "temp", tmp_path / "outside"
    (temp / "sub").mkdir(parents=True)
    outside.mkdir()
    (temp / "old.txt").write_bytes(b"x" * 100)
    (temp / "sub" / "old2.txt").write_bytes(b"y" * 50)
    new = temp / "new.txt"
    new.write_bytes(b"z")
    os.utime(new, (time.time() + LATER, time.time() + LATER))        # 對假時鐘來說是剛剛才改的
    (outside / "keep.txt").write_bytes(b"must stay")
    subprocess.run(["cmd", "/c", "mklink", "/J", str(temp / "link"), str(outside)], check=True,
                   capture_output=True)                               # junction 不用系統管理員
    return temp, outside


@windows_only
def test_scan_clean_and_never_follow_junctions(fake_temp):
    temp, outside = fake_temp
    locked = temp / "busy.log"
    locked.write_bytes(b"busy")
    proc = hold_open(locked)
    try:
        wp = WindowsProcesses()
        wp.me = -1                         # 假程式是 pytest 開的：不改會被當成 HWGlance 自己的而不給關
        log = FakeLog()
        cleaner = TempCleaner(wp, log, roots=lambda: [Root(str(temp), "測試暫存", True)],
                              clock=lambda: time.time() + LATER, admin=lambda: False)
        r = cleaner.scan()
        root = r["roots"][0]
        assert root["files"] == 4 and root["old"] == 3                 # 新檔不算舊；junction 後面的不算
        assert r["deletable"] == {"files": 2, "bytes": 150}
        [group] = r["locked"]
        created = processes.created_at(proc.pid)
        assert group["process"] == {"pid": proc.pid, "created": created} and group["samples"] == ["busy.log"]
        assert wp.offers.allowed(proc.pid, created)                     # 「關掉這個程式」走同一套關閉

        out = cleaner.clean(r["id"])
        assert out["deleted"] == 2 and out["bytes"] == 150 and out["dirs"] == 1     # sub 空了也刪
        assert not (temp / "old.txt").exists() and not (temp / "sub").exists()
        assert (temp / "new.txt").exists() and locked.exists() and (outside / "keep.txt").exists()
        assert log.lines[0]["action"] == "clean-temp" and "path" not in log.lines[0]   # 不記檔名
        with pytest.raises(LookupError):
            cleaner.clean(r["id"])                                       # 刪過就要重新掃描
        with pytest.raises(PermissionError):
            cleaner.boot_delete(cleaner.scan()["id"], group["key"])     # 下次開機刪除要系統管理員

        assert wp.close(proc.pid, created, force=True)["result"] == "killed"
        r2 = cleaner.scan()
        assert r2["locked"] == [] and r2["deletable"]["files"] == 1     # 程式關了，就可以刪了
        cleaner.clean(r2["id"])
        assert not locked.exists() and (outside / "keep.txt").exists()
    finally:
        proc.kill()


@windows_only
def test_delete_safely_refuses_anything_outside(fake_temp, tmp_path):
    temp, outside = fake_temp
    root = tempfiles.root_final(str(temp))
    assert tempfiles.delete_safely(str(outside / "keep.txt"), root) == "outside"
    assert tempfiles.delete_safely(str(temp / "link" / "keep.txt"), root) == "outside"   # 穿過 junction 也不行
    assert (outside / "keep.txt").exists()
    assert tempfiles.delete_safely(str(temp / "old.txt"), root) == "deleted"
    assert tempfiles.delete_safely(str(temp / "old.txt"), root) == "gone"


@windows_only
def test_lockers_reports_pid_and_start_time(tmp_path):
    f = tmp_path / "held.tmp"
    f.write_bytes(b"1")
    proc = hold_open(f)
    try:
        assert tempfiles.probe(str(f)) == "locked"
        found = tempfiles.lockers(str(f))
        assert (proc.pid, processes.created_at(proc.pid)) in [(pid, created) for pid, created, _ in found]
    finally:
        proc.kill()


@windows_only
def test_boot_delete_only_for_roots_that_allow_it(tmp_path):
    a, b = tmp_path / "mine", tmp_path / "windows"
    for d in (a, b):
        d.mkdir()
        (d / "x.tmp").write_bytes(b"1")
    scheduled: list[str] = []
    cleaner = TempCleaner(None, None, roots=lambda: [Root(str(a), "你的", True), Root(str(b), "Windows", False)],
                          probe_fn=lambda p: "locked", lockers_fn=lambda p: [],
                          boot_fn=lambda p, root: scheduled.append(p) or True,
                          clock=lambda: time.time() + LATER, admin=lambda: True)
    r = cleaner.scan()
    [unknown] = r["locked"]
    assert unknown["key"] == "unknown" and unknown["process"] is None and unknown["files"] == 2
    out = cleaner.boot_delete(r["id"], "unknown")
    assert out == {"scheduled": 1, "skipped": 1} and scheduled == [str(a / "x.tmp")]


# ---- API（示範資料，DemoTemp＋DemoProcesses）----

TOKEN = "t"
H = {"X-HWGlance-Token": TOKEN}


def api_client(with_temp: bool = True):
    source = DemoSource(seed=1)
    service = SensorService(source, env={"admin": True, "pawnio": "2.2.0"})
    assert service.open()
    service.tick()
    procs = DemoProcesses(source)
    app = create_app(service, TOKEN, processes=procs, temp=DemoTemp(procs) if with_temp else None)
    return TestClient(app, base_url="http://127.0.0.1:8796")


def test_api_temp_flow_with_demo():
    c = api_client()
    assert c.post("/api/temp/scan").status_code == 403                                   # 沒帶 token
    r = c.post("/api/temp/scan", headers=H).json()
    names = {g["name"]: g for g in r["locked"]}
    assert names["svchost（Windows 服務）"]["process"] is None and names["svchost（Windows 服務）"]["protected"]
    chat = names["ChatGPT"]["process"]
    plan = c.get("/api/processes/plan", params=chat).json()
    assert plan["blocked"] is None and plan["target"]["name"] == "ChatGPT"
    assert c.post("/api/processes/close", json={**chat, "force": True}, headers=H).json()["result"] == "killed"
    r = c.post("/api/temp/scan", headers=H).json()
    assert "ChatGPT" not in {g["name"] for g in r["locked"]}                              # 關掉就不再鎖
    assert c.post("/api/temp/boot-delete", json={"id": r["id"], "key": "unknown"}, headers=H).json()["scheduled"] == 1
    out = c.post("/api/temp/clean", json={"id": r["id"]}, headers=H).json()
    assert out["deleted"] == 812
    stale = c.post("/api/temp/clean", json={"id": r["id"]}, headers=H)
    assert stale.status_code == 409 and "重新掃描" in stale.json()["detail"]
    none = api_client(with_temp=False)
    assert none.post("/api/temp/scan", headers=H).status_code == 404
