"""API 與單一執行個體：服務用示範資料、視窗用假的回呼，不開程式也不碰硬體。"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from hwglance import instance, paths
from hwglance.api.app import create_app
from hwglance.core import pick_port
from hwglance.events.demo import DemoEventLog
from hwglance.events.service import EventService
from hwglance.sensors.demo import DemoSource
from hwglance.sensors.service import SensorService

ENV = {"admin": True, "pawnio": "2.2.0"}
TOKEN = "test-token"
NOW = 1_793_420_000.0


@pytest.fixture
def opened() -> list[str]:
    return []


@pytest.fixture
def client(opened, tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    monkeypatch.setattr(paths, "frontend_dist", lambda: dist)
    service = SensorService(DemoSource(seed=1), env=ENV)
    assert service.open()
    service.tick()
    service.load_specs()
    events = EventService(DemoEventLog(now=NOW), service.disk_models, clock=lambda: NOW)
    app = create_app(service, TOKEN, open_ui=opened.append, events=events)
    return TestClient(app, base_url="http://127.0.0.1:8796")


def test_snapshot(client):
    r = client.get("/api/snapshot")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    snap = r.json()
    assert snap["seq"] == 1 and len(snap["overview"]) == 10
    assert snap["source"] == {"kind": "demo", "version": "demo", "admin": True, "pawnio": "2.2.0",
                              "notes": ["示範資料：數字是模擬的，不是這台電腦的讀數"]}


def test_history_and_sensors(client):
    h = client.get("/api/history", params={"seconds": 60}).json()
    assert len(h["t"]) == 1 and "/amdcpu/0/temperature/2" in h["series"]
    assert client.get("/api/history", params={"seconds": 5}).status_code == 422     # 最少 10 秒
    tree = client.get("/api/sensors").json()
    assert tree[0]["id"] == "/motherboard" and tree[0]["children"][0]["type"] == "SuperIO"   # 順序照 LHM
    assert tree[1]["id"] == "/amdcpu/0"


def test_device_details(client):
    r = client.get("/api/device", params={"id": "/nvme/5"})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    d = r.json()
    assert d["name"] == "KIOXIA-EXCERIA G2 SSD" and d["sections"][0]["title"] == "規格"
    assert client.get("/api/device", params={"id": "/nope"}).status_code == 404
    assert client.get("/api/device").status_code == 422


def test_events(client):
    r = client.get("/api/events", params={"days": 90})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    d = r.json()
    assert d["days"] == 90 and d["counts"]["bluescreen"] == 4 and d["source"] == "demo"
    disk = next(i for i in d["incidents"] if i["category"] == "storage")
    assert disk["title"] == "G:（TOSHIBA DT02ABA400）突然斷線"          # 型號從示範資料的規格來
    assert client.get("/api/events", params={"days": 0}).json()["days"] is None    # 0 是全部
    assert client.get("/api/events", params={"days": -1}).status_code == 422
    assert client.get("/api/events", params={"days": 99999}).status_code == 422


def test_events_without_event_log(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "frontend_dist", lambda: tmp_path / "dist")
    app = create_app(SensorService(DemoSource(seed=1), env=ENV), TOKEN)
    assert TestClient(app, base_url="http://127.0.0.1:8796").get("/api/events").status_code == 404


def test_status(client):
    s = client.get("/api/status").json()
    assert s["app"] == "HWGlance" and s["pid"] == os.getpid() and s["canOpenWindow"] is True


def test_foreign_host_is_rejected(client):
    assert client.get("/api/snapshot", headers={"host": "evil.example:8796"}).status_code == 403


def test_open_window_needs_token(client, opened):
    assert client.post("/api/ui/open", json={"mode": "main"}).status_code == 403
    r = client.post("/api/ui/open", json={"mode": "main"}, headers={"X-HWGlance-Token": TOKEN})
    assert r.json() == {"ok": True} and opened == ["main"]


def test_index_injects_token_or_explains_build(client, tmp_path):
    r = client.get("/")
    assert r.status_code == 503 and "npm run build" in r.text
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><head><title>x</title></head><body></body></html>", encoding="utf-8")
    r = client.get("/")
    assert r.status_code == 200 and f'<meta name="hwglance-token" content="{TOKEN}" />' in r.text


def test_instance_file_roundtrip_and_stale_file():
    assert instance.read() is None and instance.activate(timeout=0.2) is False
    instance.write(8796, "abc")
    info = instance.read()
    assert info["pid"] == os.getpid() and info["port"] == 8796 and info["token"] == "abc"
    instance.remove()
    assert instance.read() is None
    paths.instance_file().write_text('{"pid": 999999999, "port": 1, "token": "x"}', encoding="utf-8")
    assert instance.activate(timeout=0.2) is False          # 那個程序早就不在了：馬上放棄，不等連線逾時


def test_pid_exists():
    assert instance.pid_exists(os.getpid()) is True
    assert instance.pid_exists(999999999) is False


def test_pick_port_falls_back_when_taken():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        taken = s.getsockname()[1]
        assert pick_port(taken) != taken
