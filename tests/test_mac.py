"""Mac 版（sensors/mac.py、macio.py 的純函式、MacDemo）：全部用假的讀數與假的 io，不碰這台 Mac 的硬體，
所以 Windows 上也跑得了。數字照 2026-11-11 那台 MacBook Pro 14 吋 M6 的真機讀數。"""
from __future__ import annotations

import struct

import pytest

from hwglance import system
from hwglance.sensors import macio
from hwglance.sensors.demo import MacDemo
from hwglance.sensors.details import device_details
from hwglance.sensors.mac import (BOARD_ID, CPU_ID, GPU_ID, Extremes, MacSource, Readings, build_tree, core_layout,
                                  cpu_loads, memory_used, short_model)
from hwglance.sensors.normalize import APPLE_TJMAX, normalize
from hwglance.sensors.service import ALL_FEATURES, SensorService
from hwglance.sensors.demo import DemoSource

ENV = {"admin": False, "pawnio": None}


def readings(**over) -> Readings:
    temps = {f"Tp{i:02X}": 34.0 + i * 0.1 for i in range(21)}          # 效能核心區 21 個點（最高 36.0）
    temps |= {f"Te{i:02X}": 33.0 for i in range(9)}
    temps |= {f"Tg{i:02X}": 35.0 + (i == 3) for i in range(18)}        # GPU 18 個點，最高 36.0
    temps |= {"TB0T": 26.5, "TB1T": 26.6, "TB2T": 26.1, "TaLR": 27.6, "Tp99": 0.0}   # 0 是沒在用的鍵
    base = dict(chip="Apple M6", model="MacBook Pro", temps=temps, fans=[(0.0, 2000.0, 8250.0)],
                cpu_loads=[10.0, 30.0] + [5.0] * 10, gpu_load=31.0, memory=(24.7 * 2**30, 32 * 2**30),
                ssd="APPLE SSD AP1024Z", ssd_temp=30.0, disk=(863.9e9, 994.6e9))
    return Readings(**(base | over))


# ---- macio 的純函式 ----

def test_smc_values_decode_like_the_real_keys():
    assert macio.decode("flt ", struct.pack("<f", 36.5625)) == 36.5625                # Apple 晶片：小端序 float
    assert macio.decode("sp78", struct.pack(">h", int(45.5 * 256))) == 45.5         # Intel Mac 的溫度
    assert macio.decode("fpe2", struct.pack(">H", 2000 * 4)) == 2000.0              # Intel Mac 的風扇
    assert macio.decode("ui8 ", b"\x01") == 1.0
    assert macio.decode("hex_", b"\x00\x02") is None and macio.decode("flt ", b"\x00") is None
    assert macio.unfourcc(macio.fourcc("F0Ac")) == "F0Ac"


def test_cpu_load_is_the_busy_share_of_ticks_and_survives_wraparound():
    assert cpu_loads(None, [(1, 1, 1, 0)]) is None                                   # 第一輪還算不出來
    prev = [(100, 50, 850, 0), (2**32 - 10, 0, 0, 0)]
    now = [(130, 70, 900, 0), (10, 0, 80, 0)]                                         # 第二個核心 user 繞回去了
    assert cpu_loads(prev, now) == [50.0, 20.0]
    assert cpu_loads(prev, now[:1]) is None                                           # 核心數變了：重新算


def test_memory_used_matches_activity_monitor():
    pages = {"internal": 790_726, "purgeable": 3_665, "wired": 237_207, "compressed": 545_178, "page": 16384}
    assert memory_used(pages) == (790_726 - 3_665 + 237_207 + 545_178) * 16384


def test_names():
    assert short_model("MacBook Pro (14-inch, M6)") == "MacBook Pro"
    assert core_layout([("Super", 2), ("Performance", 4), ("Efficiency", 6)]) == "2 超級核＋4 效能核＋6 節能核"
    assert core_layout([]) is None


# ---- 硬體樹 → 畫面 ----

def test_tree_becomes_cpu_gpu_body_memory_and_ssd_cards():
    snap = normalize(build_tree(readings(), Extremes()))
    kinds = {d["kind"]: d for d in snap["devices"]}
    assert list(kinds) == ["cpu", "gpu", "board", "memory", "storage"]
    cpu, gpu, body = kinds["cpu"], kinds["gpu"], kinds["board"]
    # 卡片只放最高、平均與各區最高，21＋9 個溫度點只在詳細資訊
    assert [t["label"] for t in cpu["temps"]] == ["核心最高", "核心平均", "效能核心最高", "節能核心最高"]
    assert cpu["temps"][0]["value"] == 36.0 and cpu["headline"] == f"{CPU_ID}/temperature/max"
    assert {s["key"]: s["value"] for s in cpu["stats"]} == {"load": round(sum([10, 30] + [5] * 10) / 12)}
    assert [t["label"] for t in gpu["temps"]] == ["核心", "平均"] and gpu["temps"][0]["value"] == 36.0
    assert [s["key"] for s in gpu["stats"]] == ["load"]                              # 沒有顯存：Apple 晶片共用記憶體
    assert body["name"] == "MacBook Pro" and [t["label"] for t in body["temps"]] == ["電池"]
    assert body["fans"] == [{"id": f"{BOARD_ID}/fan/fan0", "label": "風扇 1", "value": 0, "max": 0, "unit": "RPM",
                             "percent": None}] and body["unplugged"] == 0          # 停轉不是沒接
    assert [(t["label"], t["sub"]) for t in snap["overview"]] == [
        ("CPU", "Apple M6"), ("GPU 核心", "Apple M6 GPU"), ("電池", "MacBook Pro"), ("APPLE SSD AP1024Z", "NVMe")]
    memory = {s["key"]: (s["value"], s.get("total")) for s in kinds["memory"]["stats"]}
    assert memory == {"used": (24.7, 32.0), "load": (77, None)}
    ssd = {s["key"]: (s["value"], s.get("total")) for s in kinds["storage"]["stats"]}
    assert ssd == {"used": (13, None), "free": (864, 995)}                           # 十進位 GB，跟 Finder 一樣


def test_apple_thresholds():
    """CPU 照暫定的降頻溫度 105°C（90／100），電池 40／45。"""
    hot = readings(temps={"Tp01": 92.0, "TB0T": 41.0})
    snap = normalize(build_tree(hot, Extremes()))
    cpu = next(d for d in snap["devices"] if d["kind"] == "cpu")
    body = next(d for d in snap["devices"] if d["kind"] == "board")
    assert APPLE_TJMAX == 105.0 and cpu["temps"][0]["level"] == "warm"               # Windows 的 80／90 會是紅的
    assert body["temps"][0]["level"] == "warm"


def test_missing_parts_just_leave_cards_smaller():
    """第一輪沒有 CPU 使用率、沒有 SMC（新晶片的鍵不一樣）、沒有 SSD：其他照樣顯示。"""
    snap = normalize(build_tree(readings(temps={}, fans=[], cpu_loads=None, ssd_temp=None, disk=None), Extremes()))
    assert [d["kind"] for d in snap["devices"]] == ["cpu", "gpu", "board", "memory"]
    assert snap["overview"] == []


def test_extremes_track_min_and_max_like_lhm():
    track = Extremes()
    for v in (40.0, 55.0, 47.0):
        s = track(CPU_ID, "Temperature", "max", "Core Max", v)
    assert (s.value, s.min, s.max) == (47.0, 40.0, 55.0)
    assert track(CPU_ID, "Temperature", "max", "Core Max", None).max == 55.0


def test_details_show_points_core_layout_and_apple_notes():
    r = readings()
    tree = build_tree(r, Extremes())
    snap = normalize(tree)
    specs = {"cpu": {"cores": 12, "threads": 12, "layout": "2 超級核＋4 效能核＋6 節能核"},
             "gpus": {"Apple M6 GPU": {"cores": 12}}}
    cpu = device_details(tree, snap, CPU_ID, specs)
    sections = {s["title"]: s for s in cpu["sections"]}
    assert {"label": "核心種類", "value": "2 超級核＋4 效能核＋6 節能核"} in sections["規格"]["rows"]
    temps = [row["label"] for row in sections["溫度"]["rows"]]
    assert "效能核心區 21" in temps and "節能核心區 9" in temps                       # 每個點在這裡
    tjmax = next(row for row in sections["溫度"]["rows"] if row["label"] == "降頻溫度（TjMax）")
    assert tjmax["value"] == APPLE_TJMAX and "Apple 沒有公開" in tjmax["note"]
    assert len(sections["負載"]["bars"]["items"]) == 12
    gpu = device_details(tree, snap, GPU_ID, specs)
    spec = {row["label"]: row for row in gpu["sections"][0]["rows"]}
    assert spec["GPU 核心數"]["value"] == 12 and not any(s.get("ranking") for s in gpu["sections"])


# ---- MacSource（假的 io） ----

class FakeSmc:
    VALUES = {"#KEY": 5.0, "FNum": 1.0, "Tp05": 36.5, "Te07": 35.4, "Tg01": 34.2, "TB0T": 26.6,
              "F0Ac": 0.0, "F0Mn": 2000.0, "F0Mx": 8250.0}

    def open(self):
        pass

    def keys(self, prefixes):
        return [k for k in self.VALUES if k.startswith(prefixes)]

    def read(self, key):
        return self.VALUES.get(key)

    def close(self):
        FakeIo.closed.append("smc")


class FakeHid:
    def __init__(self, prefix):
        assert prefix == "NAND"

    def open(self):
        pass

    def read(self):
        return {"NAND CH0 temp": 30.0}

    def close(self):
        FakeIo.closed.append("hid")


class BrokenAccelerator:
    def open(self):
        raise OSError("找不到 GPU（IOAccelerator）")


class FakeIo:
    closed: list[str] = []
    Smc, HidTemps, Accelerator = FakeSmc, FakeHid, BrokenAccelerator

    def __init__(self):
        self.ticks = [[(100, 0, 900, 0)] * 2, [(150, 0, 950, 0)] * 2]

    @staticmethod
    def profile(data_type):
        return {"SPHardwareDataType": [{"machine_name": "MacBook Pro", "chip_type": "Apple M6"}],
                "SPNVMeDataType": [{"_items": [{"_name": "APPLE SSD AP1024Z", "device_model": "APPLE SSD AP1024Z",
                                                "device_revision": "3975.40."}]}]}[data_type]

    @staticmethod
    def sysctl_str(name):
        return "Apple M6"

    @staticmethod
    def sysctl_int(name):
        return 32 * 2**30 if name == "hw.memsize" else None

    @staticmethod
    def core_levels():
        return [("Performance", 1), ("Efficiency", 1)]

    def cpu_ticks(self):
        return self.ticks.pop(0)

    @staticmethod
    def memory_pages():
        return {"internal": 1000, "purgeable": 0, "wired": 0, "compressed": 0, "page": 2**20}

    @staticmethod
    def volume_space():
        return (500e9, 1000e9)


def test_mac_source_reads_everything_it_can_and_reports_the_rest():
    FakeIo.closed = []
    src = MacSource(io=FakeIo())
    src.open()
    src.update()
    src.update(slow=False)
    snap = normalize(src.read())
    kinds = {d["kind"]: d for d in snap["devices"]}
    assert kinds["cpu"]["temps"][0]["value"] == 36.5 and kinds["cpu"]["stats"][0]["value"] == 50
    assert kinds["gpu"]["stats"] == [] and kinds["gpu"]["temps"][0]["value"] == 34.2   # 使用率讀不到，溫度照樣有
    assert kinds["storage"]["temps"][0]["value"] == 30.0 and kinds["memory"]["stats"][0]["value"] == pytest.approx(1.0)
    assert src.errors == ["GPU 使用率：找不到 GPU（IOAccelerator）"]          # 畫面上方會列出來
    assert src.specs()["cpu"] == {"cores": 2, "threads": 2, "layout": "1 效能核＋1 節能核"}
    assert src.specs()["disks"]["0"]["firmware"] == "3975.40." and "serial" not in str(src.specs())
    src.close()
    assert FakeIo.closed == ["smc", "hid"]


def test_service_snapshot_carries_features_and_no_windows_hints():
    service = SensorService(MacSource(io=FakeIo()), env=ENV)
    assert service.open()
    service.tick()
    source = service.snapshot()["source"]
    assert source["kind"] == "mac" and source["features"] == []
    assert source["notes"] == ["讀取失敗：GPU 使用率：找不到 GPU（IOAccelerator）"]   # 不提 PawnIO、系統管理員
    assert system.hints("mac", False, None) == []
    windows = SensorService(DemoSource(seed=1), env=ENV).snapshot()["source"]
    assert windows["features"] == list(ALL_FEATURES)                                 # Windows 照舊全部都有


def test_mac_demo_has_the_same_shape_as_the_real_mac():
    demo = MacDemo(seed=1)
    snap = normalize(demo.read())
    assert [d["kind"] for d in snap["devices"]] == ["cpu", "gpu", "board", "memory", "storage"]
    assert [t["label"] for t in snap["overview"]] == ["CPU", "GPU 核心", "電池", "APPLE SSD AP1024Z"]
    assert demo.features == () and demo.gpu_processes is None
    service = SensorService(demo, env=ENV)
    service.tick()
    assert service.details(GPU_ID) is not None and service.snapshot()["source"]["features"] == []
