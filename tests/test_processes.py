"""關閉顯存排行上的程式（processes.py，SPEC D31）。

規則用假的程式清單測；真的 Windows 部分只關測試自己開的假程式（python 睡覺、一個開在螢幕外的 Tk 小視窗），
絕不碰使用者的程式。API 用示範資料＋DemoProcesses（示範的 pid 是編的，不能拿去關真的）。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from hwglance import processes
from hwglance.api.app import create_app
from hwglance.processes import (ActionLog, DemoProcesses, Offers, ProcessError, WindowsProcesses, pick_target,
                                protected_reason)
from hwglance.sensors.demo import DemoSource
from hwglance.sensors.service import SensorService

ME = 500
TABLE = {                      # pid: (執行檔, 父程式)
    4: ("System", 0), 100: ("explorer.exe", 4), 200: ("cmd.exe", 100), ME: ("python.exe", 200),
    510: ("pythonw.exe", ME), 511: ("msedgewebview2.exe", 510), 600: ("dwm.exe", 4), 700: ("chrome.exe", 100),
    701: ("chrome.exe", 700), 800: ("game.exe", 100), 900: ("svchost.exe", 4), 950: ("helper.exe", 100),
    960: ("python.exe", 970), 970: ("WindowsTerminal.exe", 100), 980: ("pythonw.exe", 100),
    981: ("msedgewebview2.exe", 980), 982: ("msedgewebview2.exe", 981),
}
WINDOWS_ON = {100, 700, 800, 970, 980}


def reason(pid, session=lambda p: 1, critical=lambda p: False):
    return protected_reason(pid, TABLE, ME, session=session, critical=critical)


def test_protected_programs():
    assert reason(4) == "這是 Windows 核心"
    assert "HWGlance 自己" in reason(ME) and "HWGlance 自己" in reason(511)        # 自己開的 WebView2 也算
    assert reason(200) == "這是啟動 HWGlance 的程式"
    assert "Windows 的一部分" in reason(600) and "Windows 的一部分" in reason(900)
    assert "Windows 服務" in reason(950, session=lambda p: 0)
    assert "關鍵程式" in reason(950, critical=lambda p: True)
    assert reason(800) is None and reason(701) is None


def test_close_the_program_that_owns_the_window():
    has = WINDOWS_ON.__contains__
    assert pick_target(701, TABLE, has) == 700          # 瀏覽器放顯存的子程式 → 關有視窗的主程式
    assert pick_target(800, TABLE, has) == 800
    assert pick_target(982, TABLE, has) == 980          # WebView2 的子程式 → 用它的程式
    # 不是同一個程式就不往上找：父程式（檔案總管、終端機）有視窗也不關它
    assert pick_target(950, TABLE, has) == 950
    assert pick_target(960, TABLE, has) == 960


def test_offers_expire():
    now = [0.0]
    offers = Offers(clock=lambda: now[0])
    offers.offer(800, "123")
    offers.offer(801, None)                              # 查不到啟動時間的不收
    assert offers.allowed(800, "123") and not offers.allowed(800, "124") and not offers.allowed(801, "None")
    now[0] = processes.OFFER_TTL + 1
    assert not offers.allowed(800, "123")


def test_action_log_lines_and_rotation(tmp_path, monkeypatch):
    monkeypatch.setattr(processes, "LOG_LIMIT", 120)
    log = ActionLog(tmp_path / "actions.log")
    for i in range(4):
        log.write(action="close", pid=i, name="遊戲", result="closed")
    lines = (tmp_path / "actions.log").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-1])["name"] == "遊戲" and (tmp_path / "actions.log.1").exists()


# ---- 真的 Windows：只碰測試自己開的程式 ----

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows 才有")
TK_WINDOW = ("import tkinter as tk; r = tk.Tk(); r.title('hwglance-test'); r.geometry('120x60+-4000+-4000'); "
             "r.after(200, lambda: print('ready', flush=True)); r.mainloop()")


def spawn(code: str) -> subprocess.Popen:
    # 用真的 python（venv 的 python.exe 只是轉一手，視窗、睡覺都在它開的子程式裡，pid 對不上）；只用標準函式庫
    python = getattr(sys, "_base_executable", sys.executable)
    return subprocess.Popen([python, "-c", code], stdout=subprocess.PIPE, text=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def controller(tmp_path) -> WindowsProcesses:
    wp = WindowsProcesses(ActionLog(tmp_path / "actions.log"), gentle_wait=5.0)
    wp.me = -1          # 假程式是 pytest 開的子程式：不改的話會被當成「HWGlance 自己開的」而擋下
    return wp


@windows_only
def test_force_close_a_program_without_window(tmp_path):
    proc = spawn("import time; time.sleep(60)")
    try:
        wp = controller(tmp_path)
        created = processes.created_at(proc.pid)
        assert created and created.isdigit()
        assert "不在清單上" in wp.plan(proc.pid, created)["blocked"]          # 沒列出來過的不收
        wp.offers.offer(proc.pid, created)
        wp.offers.offer(proc.pid, str(int(created) + 1))
        assert "已經結束" in wp.plan(proc.pid, str(int(created) + 1))["blocked"]   # 同 pid、啟動時間不同＝別的程式
        plan = wp.plan(proc.pid, created)
        assert plan["blocked"] is None and plan["gentle"] is False and plan["target"]["pid"] == proc.pid
        assert wp.close(proc.pid, created, force=False)["result"] == "no_window"
        with pytest.raises(ProcessError):
            wp.close(proc.pid, str(int(created) + 1), force=True)         # 啟動時間對不上：不動
        assert proc.poll() is None
        assert wp.close(proc.pid, created, force=True)["result"] == "killed"
        assert proc.wait(timeout=5) == 1
        log = [json.loads(x) for x in (tmp_path / "actions.log").read_text(encoding="utf-8").splitlines()]
        assert [x["result"] for x in log] == ["no_window", "killed"]
    finally:
        proc.kill()


@windows_only
def test_gentle_close_a_program_with_window(tmp_path):
    pytest.importorskip("tkinter")
    proc = spawn(TK_WINDOW)
    try:
        assert proc.stdout.readline().strip() == "ready"
        deadline = time.time() + 5
        while not processes.windows_of(proc.pid) and time.time() < deadline:
            time.sleep(0.1)
        wp = controller(tmp_path)
        created = processes.created_at(proc.pid)
        wp.offers.offer(proc.pid, created)
        plan = wp.plan(proc.pid, created)
        assert plan["gentle"] is True
        assert wp.close(proc.pid, created, force=False)["result"] == "closed"
        assert proc.wait(timeout=5) == 0                  # 自己關的，不是被砍的
    finally:
        proc.kill()


# ---- API（示範資料，DemoProcesses）----

TOKEN = "t"


def api_client(with_processes: bool = True):
    source = DemoSource(seed=1)
    service = SensorService(source, env={"admin": True, "pawnio": "2.2.0"})
    assert service.open()
    service.tick()
    app = create_app(service, TOKEN, processes=DemoProcesses(source) if with_processes else None)
    return TestClient(app, base_url="http://127.0.0.1:8796")


def ranking(client) -> list[dict]:
    d = client.get("/api/device", params={"id": "/gpu-nvidia/0"}).json()
    return next(s for s in d["sections"] if s["title"] == "顯存被誰佔用")["ranking"]["items"]


def by_name(items, prefix):
    return next(x["process"] for x in items if x["label"].startswith(prefix))


def test_api_close_flow_with_demo():
    c = api_client()
    items = ranking(c)
    closable = [x for x in items if "process" in x]
    assert closable and all(x["process"]["created"].startswith("demo-") for x in closable)
    # 系統程式、HWGlance 自己沒有按鈕；硬送也不收（沒列出來過）
    assert {x["label"][:6] for x in items if "process" not in x} == {"桌面視窗管理", "檔案總管", "HWGlan"}
    dwm = c.get("/api/processes/plan", params={"pid": 1300, "created": "demo-1300"}).json()
    assert "不在清單上" in dwm["blocked"]
    snip = by_name(items, "SnippingTool")
    plan = c.get("/api/processes/plan", params=snip).json()
    assert plan["blocked"] is None and plan["gentle"] is True
    target = {k: plan["target"][k] for k in ("pid", "created")}
    assert c.post("/api/processes/close", json={**target, "force": False}).status_code == 403    # 沒帶 token
    r = c.post("/api/processes/close", json={**target, "force": False}, headers={"X-HWGlance-Token": TOKEN})
    assert r.json()["result"] == "closed"
    c.app.state  # noqa: B018 - 確認 app 還在
    assert not any(x["label"].startswith("SnippingTool") for x in ranking(c))                  # 清單上拿掉了

    gpt = by_name(ranking(c), "ChatGPT")                 # 示範「還開著（在問要不要存檔）」→ 強制結束
    c.get("/api/processes/plan", params=gpt)
    headers = {"X-HWGlance-Token": TOKEN}
    assert c.post("/api/processes/close", json={**gpt, "force": False}, headers=headers).json()["result"] == "running"
    assert c.post("/api/processes/close", json={**gpt, "force": True}, headers=headers).json()["result"] == "killed"

    web = c.get("/api/processes/plan", params=by_name(ranking(c), "pythonw（-m projecthub）")).json()
    assert web["target"]["pid"] != web["pid"]            # 顯存在 WebView2，關的是它的主程式

    r = c.post("/api/processes/close", json={"pid": 4242, "created": "demo-4242", "force": True}, headers=headers)
    assert r.status_code == 409 and "不在清單上" in r.json()["detail"]                       # 沒列出來的不收


def test_api_without_processes_has_no_close_buttons():
    c = api_client(with_processes=False)
    assert all("process" not in x for x in ranking(c))
    assert c.get("/api/processes/plan", params={"pid": 1300, "created": "demo-1300"}).status_code == 404
    r = c.post("/api/processes/close", json={"pid": 1300, "created": "demo-1300"}, headers={"X-HWGlance-Token": TOKEN})
    assert r.status_code == 404
