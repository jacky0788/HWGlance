"""共用的假資料。測試絕不碰真的硬體，也不載入 LHM：一律用這裡的假樹或 DemoSource。"""
from __future__ import annotations

import pytest

from hwglance.sensors.model import RawHardware, RawSensor


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    """data/ 換到暫存區，測試不會寫到專案裡。"""
    monkeypatch.setenv("HWGLANCE_DATA", str(tmp_path / "data"))
    return tmp_path / "data"


def sensor(hw_id: str, kind: str, index: int, name: str, value: float | None,
           lo: float | None = None, hi: float | None = None) -> RawSensor:
    return RawSensor(f"{hw_id}/{kind.lower()}/{index}", name, kind, value,
                     value if lo is None else lo, value if hi is None else hi)


def machine_tree() -> list[RawHardware]:
    """以這台電腦（Ryzen 7 5800X、RTX 5070 Ti、TUF X570、兩顆 NVMe＋SATA SSD＋HDD）為底的固定讀數。
    刻意放了：沒接的風扇、Super I/O 的垃圾通道、NVMe 的門檻值、不認得的硬體種類、GPU 熱點（RTX 50 其實沒有，
    舊顯卡有）。編號與數值不一定跟真機一樣；真機的樣子看 DemoSource（照 2026-10-31 的 probe）。"""
    cpu = "/amdcpu/0"
    gpu = "/gpu-nvidia/0"
    lpc = "/lpc/nct6798d/0"
    return [
        RawHardware(cpu, "AMD Ryzen 7 5800X", "Cpu", [
            sensor(cpu, "Temperature", 2, "Core (Tctl/Tdie)", 85.2, 40.1, 88.0),
            sensor(cpu, "Temperature", 3, "CCD1 (Tdie)", 82.7),
            sensor(cpu, "Load", 0, "CPU Total", 64.4),
            sensor(cpu, "Clock", 1, "Cores (Average)", 4452.3),
            sensor(cpu, "Clock", 2, "Core #1", 4600.0),
            sensor(cpu, "Power", 0, "Package", 118.6),
        ]),
        RawHardware(gpu, "NVIDIA GeForce RTX 5070 Ti", "GpuNvidia", [
            sensor(gpu, "Temperature", 0, "GPU Core", 70.0),
            sensor(gpu, "Temperature", 2, "GPU Hot Spot", 81.4),
            sensor(gpu, "Temperature", 3, "GPU Memory Junction", 84.0),
            sensor(gpu, "Load", 0, "GPU Core", 98.0),
            sensor(gpu, "Clock", 0, "GPU Core", 2797.0),
            sensor(gpu, "Power", 0, "GPU Package", 281.76),
            sensor(gpu, "Fan", 1, "GPU Fan 1", 1650.0),
            sensor(gpu, "Fan", 2, "GPU Fan 2", 1610.0),
            sensor(gpu, "Control", 1, "GPU Fan 1", 68.0),
            sensor(gpu, "SmallData", 1, "GPU Memory Used", 12030.0),
            sensor(gpu, "SmallData", 2, "GPU Memory Total", 16303.0),
        ]),
        RawHardware("/motherboard", "ASUS TUF GAMING X570-PLUS (WI-FI)", "Motherboard", [], [
            RawHardware(lpc, "Nuvoton NCT6798D", "SuperIO", [
                sensor(lpc, "Temperature", 0, "CPU", 77.0),
                sensor(lpc, "Temperature", 1, "Motherboard", 38.0),
                sensor(lpc, "Temperature", 2, "Chipset", 58.5),
                sensor(lpc, "Temperature", 6, "Temperature #6", 127.0),       # 空著的腳位
                sensor(lpc, "Fan", 0, "CPU Fan", 1180.0),
                sensor(lpc, "Fan", 1, "CPU Optional Fan", 0.0),
                sensor(lpc, "Fan", 2, "Chassis Fan #1", 905.0),
                sensor(lpc, "Fan", 3, "Chassis Fan #2", 0.0, 0.0, 860.0),     # 現在停著，但轉過
                sensor(lpc, "Fan", 4, "Chassis Fan #3", 0.0),
                sensor(lpc, "Fan", 5, "Chipset Fan", 2350.0),
                sensor(lpc, "Fan", 6, "AIO Pump", 0.0),
                sensor(lpc, "Control", 0, "CPU Fan", 45.0),
                sensor(lpc, "Voltage", 0, "Vcore", 1.3),
            ]),
        ]),
        RawHardware("/ram", "Total Memory", "Memory", [
            sensor("/ram", "Load", 0, "Memory", 64.2),
            sensor("/ram", "Data", 0, "Memory Used", 41.0),
            sensor("/ram", "Data", 1, "Memory Available", 22.9),
        ]),
        RawHardware("/vram", "Virtual Memory", "Memory", [
            sensor("/vram", "Load", 1, "Memory", 50.0),
        ]),
        RawHardware("/nvme/0", "KIOXIA-EXCERIA G2 SSD", "Storage", [
            sensor("/nvme/0", "Temperature", 0, "Composite Temperature", 44.0),
            sensor("/nvme/0", "Temperature", 1, "Temperature #1", 51.0),
            sensor("/nvme/0", "Temperature", 10, "Warning Temperature", 83.0),
            sensor("/nvme/0", "Temperature", 11, "Critical Temperature", 85.0),
            sensor("/nvme/0", "Load", 30, "Used Space", 61.0),
            sensor("/nvme/0", "Data", 31, "Free Space", 726.0),
            sensor("/nvme/0", "Data", 32, "Total Space", 1863.0),
        ]),
        RawHardware("/nvme/1", "KINGSTON SA2000M8500G", "Storage", [
            sensor("/nvme/1", "Temperature", 0, "Composite Temperature", 56.0),
        ]),
        RawHardware("/ssd/2", "CT1000MX500SSD1", "Storage", [
            sensor("/ssd/2", "Temperature", 0, "Temperature", 36.0),
        ]),
        RawHardware("/hdd/4", "TOSHIBA DT02ABA400", "Storage", [
            sensor("/hdd/4", "Temperature", 0, "Temperature", 47.0),
        ]),
        RawHardware("/nic/0", "Ethernet", "Network", [
            sensor("/nic/0", "Load", 1, "Network Utilization", 1.0),
        ]),
        RawHardware("/powermonitor/0", "WireView Pro", "PowerMonitor", [
            sensor("/powermonitor/0", "Temperature", 0, "Connector", 41.0),
        ]),
    ]


@pytest.fixture
def tree() -> list[RawHardware]:
    return machine_tree()
