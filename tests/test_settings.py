"""設定：存檔、讀檔容錯、API、套用到感測服務。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from hwglance import paths
from hwglance.api.app import create_app
from hwglance.core import apply_settings
from hwglance.sensors.demo import DemoSource
from hwglance.sensors.service import SensorService
from hwglance.sensors.thresholds import DEFAULTS, LABELS, Band
from hwglance.settings import Settings, SettingsStore, parse

ENV = {"admin": True, "pawnio": "2.2.0"}
TOKEN = "t"
HEADERS = {"X-HWGlance-Token": TOKEN}


def test_defaults_when_missing_or_broken():
    assert SettingsStore().get() == Settings()
    paths.settings_file().parent.mkdir(parents=True, exist_ok=True)
    paths.settings_file().write_text("{壞掉的 json", encoding="utf-8")
    assert SettingsStore().get() == Settings()


def test_parse_drops_bad_values_and_keeps_good_ones():
    s = parse({"unit": "K", "interval": 3, "closeToTray": "yes",
               "bands": {"cpu": [70, 85], "gpu": [90, 80], "nope": [1, 2], "hdd": "x"}})
    assert s.unit == "C" and s.interval == 1 and s.close_to_tray is True
    assert s.bands == {"cpu": (70.0, 85.0)}
    assert s.merged_bands()["cpu"] == Band(70, 85) and s.merged_bands()["gpu"] == DEFAULTS["gpu"]


def test_update_saves_only_changed_bands_and_notifies():
    store = SettingsStore()
    seen = []
    store.subscribe(seen.append)
    cpu = DEFAULTS["cpu"]
    store.update(unit="F", bands={"cpu": (cpu.warm, cpu.hot), "hdd": (40.0, 50.0)})
    saved = json.loads(paths.settings_file().read_text(encoding="utf-8"))
    assert saved["unit"] == "F" and saved["bands"] == {"hdd": [40.0, 50.0]}     # 跟預設一樣的不存
    assert seen[-1].unit == "F"
    assert SettingsStore().get().bands == {"hdd": (40.0, 50.0)}                 # 重開讀得回來
    with pytest.raises(ValueError):
        store.update(bands={"cpu": (90.0, 80.0)})
    with pytest.raises(ValueError):
        store.update(interval=3)


def test_apply_settings_reaches_the_snapshot():
    service = SensorService(DemoSource(seed=1), env=ENV)
    assert service.open()
    apply_settings(service, Settings(unit="F", interval=2, bands={"cpu": (30.0, 40.0)}))
    service.tick()
    snap = service.snapshot()
    assert snap["prefs"] == {"unit": "F"} and snap["interval"] == 2
    assert snap["overview"][0]["level"] == "hot"          # CPU 門檻被調低到 40°C


@pytest.fixture
def client():
    service = SensorService(DemoSource(seed=1), env=ENV)
    assert service.open()
    service.tick()
    quits = []
    app = create_app(service, TOKEN, settings=SettingsStore(), on_quit=lambda: quits.append(True))
    c = TestClient(app, base_url="http://127.0.0.1:8796")
    c.quits = quits
    return c


def test_settings_api(client):
    s = client.get("/api/settings").json()
    assert s["unit"] == "C" and s["intervals"] == [1, 2, 5] and list(s["labels"]) == list(LABELS)
    assert s["bands"]["cpu"] == [80, 90] and s["defaults"] == s["bands"]
    body = {"unit": "F", "interval": 5, "closeToTray": False, "bands": {**s["bands"], "nvme": [50, 65]}}
    assert client.put("/api/settings", json=body).status_code == 403              # 沒有 token
    r = client.put("/api/settings", json=body, headers=HEADERS)
    assert r.status_code == 200 and r.json()["bands"]["nvme"] == [50, 65] and r.json()["closeToTray"] is False
    bad = {**body, "bands": {"cpu": [95, 90]}}
    r = client.put("/api/settings", json=bad, headers=HEADERS)
    assert r.status_code == 400 and "偏高要小於過熱" in r.json()["detail"]


def test_quit_needs_token(client):
    assert client.post("/api/app/quit").status_code == 403
    assert client.post("/api/app/quit", headers=HEADERS).json() == {"ok": True}
    assert client.quits == [True]
