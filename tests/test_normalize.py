from __future__ import annotations

from conftest import sensor

from hwglance.sensors.model import RawHardware
from hwglance.sensors.normalize import (clean_cpu, history_values, normalize, short_chip, short_drive,
                                        short_gpu)
from hwglance.sensors.thresholds import Band


def by_kind(snap: dict, kind: str) -> list[dict]:
    return [d for d in snap["devices"] if d["kind"] == kind]


def test_overview_has_every_important_temperature(tree):
    snap = normalize(tree)
    labels = [t["label"] for t in snap["overview"]]
    assert labels == ["CPU", "GPU 核心", "GPU 熱點", "主機板", "晶片組",
                      "KIOXIA-EXCERIA G2", "KINGSTON SA2000M8500G", "CT1000MX500", "TOSHIBA DT02ABA400",
                      "WireView Pro"]
    cpu = snap["overview"][0]
    assert cpu["sub"] == "Tctl/Tdie" and cpu["value"] == 85.2 and cpu["max"] == 88.0
    assert snap["overview"][1]["sub"] == "RTX 5070 Ti"
    assert snap["overview"][3]["sub"] == "NCT6798D"
    assert [t["sub"] for t in snap["overview"][5:9]] == ["NVMe", "NVMe", "SATA SSD", "HDD"]


def test_levels_follow_each_kind(tree):
    levels = {t["label"]: t["level"] for t in normalize(tree)["overview"]}
    assert levels["CPU"] == "warm"                     # 85：CPU 80 起偏高、90 過熱
    assert levels["GPU 核心"] == "ok"                  # 70
    assert levels["GPU 熱點"] == "ok"                  # 熱點的門檻比核心高
    assert levels["KINGSTON SA2000M8500G"] == "warm"   # 56：SSD 55 起偏高
    assert levels["TOSHIBA DT02ABA400"] == "warm"      # 47：機械硬碟 45 起偏高
    assert levels["CT1000MX500"] == "ok"


def test_custom_bands_override_defaults(tree):
    snap = normalize(tree, {"cpu": Band(70, 85)})
    assert snap["overview"][0]["level"] == "hot"


def test_devices_are_ordered_and_memory_is_merged(tree):
    snap = normalize(tree)
    kinds = [d["kind"] for d in snap["devices"]]
    assert kinds == ["cpu", "gpu", "board", "memory", "storage", "storage", "storage", "storage", "other"]
    assert "network" not in kinds                      # 網路卡跟溫度無關，第一版不顯示


def test_cpu_card(tree):
    cpu = by_kind(normalize(tree), "cpu")[0]
    assert cpu["name"] == "AMD Ryzen 7 5800X"
    assert cpu["headline"] == "/amdcpu/0/temperature/2"
    assert [t["label"] for t in cpu["temps"]] == ["核心 Tctl/Tdie", "CCD1"]
    stats = {s["key"]: (s["value"], s["unit"]) for s in cpu["stats"]}
    assert stats == {"load": (64, "%"), "clock": (4452, "MHz"), "power": (119, "W")}


def test_gpu_card(tree):
    gpu = by_kind(normalize(tree), "gpu")[0]
    roles = {t["label"]: t["role"] for t in gpu["temps"]}
    assert roles == {"核心": "gpu", "熱點": "gpu_hotspot", "顯存接面": "gpu_memory"}
    stats = {s["key"]: s for s in gpu["stats"]}
    assert stats["fan"]["value"] == 1650 and stats["fan"]["note"] == "68%"
    assert stats["vram"]["value"] == 12030 and stats["vram"]["total"] == 16303
    assert stats["power"]["value"] == 282


def test_board_lists_fans_that_spin_and_skips_junk_channels(tree):
    board = by_kind(normalize(tree), "board")[0]
    assert board["name"] == "ASUS TUF GAMING X570-PLUS (WI-FI)" and board["sub"] == "NCT6798D"
    assert [t["label"] for t in board["temps"]] == ["CPU（主機板量）", "主機板", "晶片組"]
    assert {t["label"]: t["role"] for t in board["temps"]}["晶片組"] == "chipset"
    assert [f["label"] for f in board["fans"]] == ["CPU 風扇", "機殼風扇 1", "機殼風扇 2", "晶片組風扇"]
    assert board["fans"][0]["percent"] == 45
    assert board["unplugged"] == 3                     # CPU 副風扇、機殼風扇 3、水冷幫浦


def test_storage_limits_are_not_readings(tree):
    kioxia = by_kind(normalize(tree), "storage")[0]
    assert [t["label"] for t in kioxia["temps"]] == ["溫度", "感測器 1"]
    assert kioxia["headline"] == "/nvme/0/temperature/0"
    assert kioxia["media"] == "nvme"
    stats = {s["key"]: s for s in kioxia["stats"]}
    assert stats["used"]["value"] == 61 and stats["free"]["total"] == 1863


def test_memory_card(tree):
    mem = by_kind(normalize(tree), "memory")[0]
    assert mem["name"] == "記憶體" and mem["temps"] == []
    stats = {s["key"]: s for s in mem["stats"]}
    assert stats["used"]["value"] == 41.0 and stats["used"]["total"] == 63.9
    assert stats["load"]["value"] == 64


def test_dimm_temperatures_show_up_in_overview():
    tree = [RawHardware("/ram", "Total Memory", "Memory", []),
            RawHardware("/memory/dimm/0", "DIMM #1", "Memory",
                        [sensor("/memory/dimm/0", "Temperature", 0, "DIMM #1", 44.5)])]
    snap = normalize(tree)
    assert [t["label"] for t in snap["overview"]] == ["DIMM 1"]
    assert snap["overview"][0]["role"] == "dimm"


def test_unknown_hardware_still_shows_its_hottest_temperature(tree):
    snap = normalize(tree)
    other = by_kind(snap, "other")[0]
    assert other["temps"][0]["label"] == "Connector"   # 沒有中文對照就照原文
    assert snap["overview"][-1]["label"] == "WireView Pro"


def test_without_pawnio_cpu_has_no_temperature_but_keeps_load():
    tree = [RawHardware("/amdcpu/0", "AMD Ryzen 7 5800X 8-Core Processor", "Cpu",
                        [sensor("/amdcpu/0", "Load", 0, "CPU Total", 12.0)]),
            RawHardware("/motherboard", "ASUS TUF GAMING X570-PLUS (WI-FI)", "Motherboard", [], [])]
    snap = normalize(tree)
    assert snap["overview"] == []
    cpu, board = snap["devices"]
    assert cpu["name"] == "AMD Ryzen 7 5800X" and cpu["temps"] == [] and cpu["headline"] is None
    assert cpu["stats"][0]["value"] == 12
    assert board["temps"] == [] and board["sub"] == ""


def test_history_values(tree):
    values = history_values(normalize(tree))
    assert values["/amdcpu/0/temperature/2"] == 85.2
    assert values["/amdcpu/0#load"] == 64 and values["/gpu-nvidia/0#power"] == 282
    assert "/nvme/0/temperature/10" not in values      # 門檻值不記


def test_name_cleanup():
    assert short_gpu("NVIDIA GeForce RTX 5070 Ti") == "RTX 5070 Ti"
    assert short_gpu("AMD Radeon RX 7800 XT") == "RX 7800 XT"
    assert short_chip("Nuvoton NCT6798D") == "NCT6798D" and short_chip("ITE IT8689E") == "IT8689E"
    assert short_drive("KIOXIA-EXCERIA G2 SSD") == "KIOXIA-EXCERIA G2"
    assert short_drive("CT500MX500SSD1") == "CT500MX500"
    assert short_drive("ST20000DM001-3Y3103") == "ST20000DM001-3Y3103"
    assert clean_cpu("AMD Ryzen 7 5800X 8-Core Processor") == "AMD Ryzen 7 5800X"
    assert clean_cpu("Intel(R) Core(TM) i7-9700K CPU @ 3.60GHz") == "Intel Core i7-9700K"
