"""這台以外的硬體（Intel CPU、Intel 內顯、筆電、AMD 顯示卡）：感測器名稱照 LHM v0.9.6 原始碼，用示範的 Intel 筆電
（sensors/demo.py 的 IntelLaptopDemo）與假的 AMD 顯示卡測。這台（AMD＋NVIDIA）的結果不變由其他測試保證。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from hwglance import paths
from hwglance.api.app import create_app
from hwglance.sensors.demo import DemoSource, IntelLaptopDemo
from hwglance.sensors.details import device_details
from hwglance.sensors.model import RawHardware, RawSensor
from hwglance.sensors.normalize import clean_cpu, cpu_tjmax, normalize, short_cpu
from hwglance.sensors.service import SensorService
from hwglance.sensors.thresholds import DEFAULTS, Band
from hwglance.settings import SettingsStore

ENV = {"admin": True, "pawnio": "2.2.0"}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def laptop(minutes: int = 3) -> IntelLaptopDemo:
    clock = Clock()
    demo = IntelLaptopDemo(seed=2, clock=clock)
    for i in range(minutes * 60):                   # 跑一陣子：負載高的時候封裝會到 90°C 上下
        clock.now += 1
        demo.update(slow=i % 10 == 0)
    return demo


def device(snap: dict, device_id: str) -> dict:
    return next(d for d in snap["devices"] if d["id"] == device_id)


def test_intel_cpu_keeps_the_card_short_and_uses_its_tjmax():
    tree = laptop().read()
    cpu_hw = next(hw for hw in tree if hw.type == "Cpu")
    assert cpu_tjmax(cpu_hw) == 100.0                     # 核心溫度＋離 TjMax 還差幾度
    snap = normalize(tree)
    cpu = device(snap, "/intelcpu/0")
    assert [t["name"] for t in cpu["temps"]] == ["Core Max", "Core Average", "CPU Package"]   # 每核、Distance 都不列
    assert cpu["name"] == "Intel Core i7-12700H" and {s["key"] for s in cpu["stats"]} == {"load", "clock", "power"}
    package = next(t for t in cpu["temps"] if t["name"] == "CPU Package")
    expected = "hot" if package["value"] >= 95 else "warm" if package["value"] >= 85 else "ok"
    assert package["level"] == expected                  # TjMax 100 → 85／95，不是 AMD 的 80／90
    tile = snap["overview"][0]
    assert (tile["label"], tile["sub"]) == ("CPU", "Core i7-12700H")


def test_cpu_threshold_the_user_changed_wins_over_tjmax():
    tree = laptop().read()
    package = next(s for s in tree[1].sensors if s.name == "CPU Package")
    package.value = 86.0
    assert next(t for t in device(normalize(tree), "/intelcpu/0")["temps"] if t["name"] == "CPU Package")["level"] == "warm"
    custom = {**DEFAULTS, "cpu": Band(70, 80)}
    assert next(t for t in device(normalize(tree, custom), "/intelcpu/0")["temps"]
                if t["name"] == "CPU Package")["level"] == "hot"


def test_laptop_gpus_battery_and_board():
    snap = normalize(laptop().read())
    igpu = device(snap, "/gpu-intel-integrated/0")
    assert igpu["temps"] == [] and {s["key"]: s["unit"] for s in igpu["stats"]} == {"load": "%", "power": "W"}
    dgpu = device(snap, "/gpu-nvidia/0")
    assert dgpu["sub"] == "RTX 4060 Laptop GPU" and "fan" not in {s["key"] for s in dgpu["stats"]}
    assert [t["label"] for t in snap["overview"]] == ["CPU", "GPU 核心", "SAMSUNG MZVL21T0HCLR-00BL2"]
    assert all(d["kind"] != "battery" for d in snap["devices"])                 # 電池不顯示
    board = device(snap, "/motherboard")
    assert board["temps"] == [] and board["fans"] == []                         # 筆電沒有感測晶片：卡片寫說明


def test_intel_details_split_performance_and_efficient_cores():
    demo = laptop()
    tree = demo.read()
    d = device_details(tree, normalize(tree), "/intelcpu/0", demo.specs(), None)
    sections = {s["title"]: s for s in d["sections"]}
    rows = [r["label"] for r in sections["時脈"]["table"]["rows"]]
    assert rows == [f"效能核 {i}" for i in range(1, 7)] + [f"節能核 {i}" for i in range(1, 9)]
    temps = {r["label"]: r for r in sections["溫度"]["rows"]}
    assert temps["降頻溫度（TjMax）"]["value"] == 100.0 and temps["降頻溫度（TjMax）"]["temp"] is True
    assert "效能核 1" in temps and not any("Distance" in r["label"] for r in sections["溫度"]["rows"])
    spec = {r["label"]: r["value"] for r in sections["規格"]["rows"]}
    assert spec["快取"] == "L1 1.2 MB・L2 11.5 MB・L3 24 MB" and spec["核心／執行緒"] == "14 核心、20 執行緒"


def test_amd_graphics_card_roles_and_labels():
    hid = "/gpu-amd/0"

    def t(i: int, name: str, value: float) -> RawSensor:
        return RawSensor(f"{hid}/temperature/{i}", name, "Temperature", value, value, value)
    card = RawHardware(hid, "AMD Radeon RX 7900 XTX", "GpuAmd", [
        t(0, "GPU Core", 62.0), t(1, "GPU Memory", 70.0), t(2, "GPU VR VDDC", 66.0), t(5, "GPU Liquid", 38.0),
        t(7, "GPU Hot Spot", 81.0), RawSensor(f"{hid}/power/3", "GPU Package", "Power", 310.0),
        RawSensor(f"{hid}/power/0", "GPU Core", "Power", 250.0), RawSensor(f"{hid}/load/0", "GPU Core", "Load", 97.0)])
    gpu = normalize([card])["devices"][0]
    roles = {x["name"]: (x["label"], x["role"]) for x in gpu["temps"]}
    assert roles == {"GPU Core": ("核心", "gpu"), "GPU Memory": ("顯存", "gpu_memory"),
                     "GPU VR VDDC": ("核心供電", "vrm"), "GPU Liquid": ("水溫", "liquid"),
                     "GPU Hot Spot": ("熱點", "gpu_hotspot")}
    assert {s["key"]: s["value"] for s in gpu["stats"]}["power"] == 310           # 整張卡的 GPU Package
    assert gpu["sub"] == "RX 7900 XTX"


def test_cpu_names_from_other_vendors():
    assert clean_cpu("13th Gen Intel(R) Core(TM) i9-13900K") == "Intel Core i9-13900K"
    assert short_cpu("Intel(R) Core(TM) Ultra 7 155H") == "Core Ultra 7 155H"
    assert short_cpu("Intel(R) Core(TM) i7-9700K CPU @ 3.60GHz") == "Core i7-9700K"
    assert clean_cpu("AMD Ryzen 7 5800X 8-Core Processor") == "AMD Ryzen 7 5800X"          # 這台照舊


def test_settings_show_the_tjmax_based_cpu_default(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "frontend_dist", lambda: tmp_path / "dist")
    store = SettingsStore(tmp_path / "settings.json")

    def client(source) -> TestClient:
        service = SensorService(source, env=ENV)
        assert service.open()
        service.tick()
        return TestClient(create_app(service, "t", settings=store), base_url="http://127.0.0.1:8796")

    intel = client(laptop(1))
    s = intel.get("/api/settings").json()
    assert s["defaults"]["cpu"] == [85, 95] and s["bands"]["cpu"] == [85, 95] and s["labels"]["cpu"] == "CPU（TjMax 100°C）"
    body = {"unit": "C", "interval": 1, "closeToTray": True, "bands": s["bands"]}
    assert intel.put("/api/settings", json=body, headers={"X-HWGlance-Token": "t"}).status_code == 200
    assert "cpu" not in store.get().bands                # 跟照 TjMax 算的預設一樣：當成沒改
    amd = client(DemoSource(seed=1)).get("/api/settings").json()
    assert amd["defaults"]["cpu"] == [80, 90] and amd["labels"]["cpu"] == "CPU"     # 這台照舊
