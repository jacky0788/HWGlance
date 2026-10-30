"""標籤、門檻、歷史、LHM 轉換。"""
from __future__ import annotations

import math

from hwglance.sensors.history import History
from hwglance.sensors.labels import label
from hwglance.sensors.lhm import (SLOW_TYPES, convert, disks_from_wmi, gpus_from_wmi, nvidia_version, smbios_specs,
                                  update_tree)
from hwglance.sensors.thresholds import DEFAULTS, Band, level
from hwglance.system import cache_totals


def test_labels():
    assert label("Temperature", "Core (Tctl/Tdie)") == "核心 Tctl/Tdie"
    assert label("Temperature", "CCD2 (Tdie)") == "CCD2"
    assert label("Fan", "Chassis Fan #3") == "機殼風扇 3"
    assert label("Fan", "GPU Fan 2") == "顯卡風扇 2"
    assert label("Fan", "GPU Fan") == "顯卡風扇"
    assert label("Temperature", "Temperature #4") == "感測器 4"
    assert label("Temperature", "GPU Memory") == "顯存"
    assert label("Load", "GPU Memory") == "GPU Memory"         # 種類不同不套用
    assert label("Temperature", "Something New") == "Something New"


def test_level_edges():
    band = Band(80, 90)
    assert [level(v, band) for v in (None, 79.9, 80, 89.9, 90)] == ["none", "ok", "warm", "warm", "hot"]
    assert set(DEFAULTS) >= {"cpu", "gpu", "board", "nvme", "ssd", "hdd", "other"}
    assert all(b.warm < b.hot for b in DEFAULTS.values())


def test_history_keeps_series_aligned_with_time():
    h = History(capacity=4)
    h.push(1.0, {"a": 10.0})
    h.push(2.0, {"a": 11.0, "b": 20.0})        # b 從第二輪才出現：前面補 None
    h.push(3.0, {"b": 21.0})                   # a 這一輪沒值
    w = h.window(60)
    assert w["t"] == [1.0, 2.0, 3.0]
    assert w["series"] == {"a": [10.0, 11.0, None], "b": [None, 20.0, 21.0]}
    for t in range(4, 9):                      # 超過容量：最舊的被擠掉，長度永遠一致
        h.push(float(t), {"a": float(t), "b": float(t)})
    w = h.window(60)
    assert w["t"] == [5.0, 6.0, 7.0, 8.0]
    assert all(len(s) == 4 for s in w["series"].values())
    assert h.window(2.5)["t"] == [6.0, 7.0, 8.0]


class _Enum:
    """模仿 pythonnet 的 .NET 列舉與 Identifier：名稱要用 ToString() 取。"""

    def __init__(self, text: str) -> None:
        self._text = text

    def ToString(self) -> str:                 # noqa: N802 - .NET 的方法名稱
        return self._text


class _Sensor:
    def __init__(self, ident, name, kind, value, lo=None, hi=None):
        self.Identifier, self.Name, self.SensorType = _Enum(ident), name, _Enum(kind)
        self.Value, self.Min, self.Max = value, lo, hi


class _Hardware:
    def __init__(self, ident, name, kind, sensors=(), sub=()):
        self.Identifier, self.Name, self.HardwareType = _Enum(ident), name, _Enum(kind)
        self.Sensors, self.SubHardware = list(sensors), list(sub)
        self.updates = 0

    def Update(self):                          # noqa: N802
        self.updates += 1


def test_convert_reads_nested_hardware_and_cleans_numbers():
    chip = _Hardware("/lpc/nct6798d/0", "Nuvoton NCT6798D", "SuperIO",
                     [_Sensor("/lpc/nct6798d/0/temperature/1", "Motherboard", "Temperature", 38.0, 30.0, 40.0),
                      _Sensor("/lpc/nct6798d/0/fan/0", "CPU Fan", "Fan", None),
                      _Sensor("/lpc/nct6798d/0/voltage/0", "Vcore", "Voltage", math.nan)])
    board = _Hardware("/motherboard", "ASUS TUF GAMING X570-PLUS (WI-FI)", "Motherboard", [], [chip])
    raw = convert(board)
    assert (raw.id, raw.type, raw.children[0].type) == ("/motherboard", "Motherboard", "SuperIO")
    temp, fan, volt = raw.children[0].sensors
    assert (temp.type, temp.value, temp.min, temp.max) == ("Temperature", 38.0, 30.0, 40.0)
    assert fan.value is None and volt.value is None            # NaN 當成沒有值


def test_convert_strips_padded_names():
    disk = _Hardware("/ssd/0", "CT1000MX500SSD1 ", "Storage",                  # 真機 LHM 給的名稱
                     [_Sensor("/ssd/0/temperature/0", " Temperature", "Temperature", 27.0)])
    raw = convert(disk)
    assert raw.name == "CT1000MX500SSD1" and raw.sensors[0].name == "Temperature"


def test_update_tree_updates_sub_hardware_and_skips_slow_ones():
    chip = _Hardware("/lpc/x", "chip", "SuperIO")
    board = _Hardware("/motherboard", "board", "Motherboard", [], [chip])
    disk = _Hardware("/nvme/0", "disk", "Storage")
    errors: list[str] = []
    for hw in (board, disk):
        update_tree(hw, slow=False, errors=errors)
    assert (board.updates, chip.updates, disk.updates) == (1, 1, 0)
    update_tree(disk, slow=True, errors=errors)
    assert disk.updates == 1 and errors == [] and "Storage" in SLOW_TYPES


class _Obj:
    def __init__(self, **fields) -> None:
        self.__dict__.update(fields)


def test_smbios_specs_skip_empty_slots_and_filler_text():
    def dimm(slot, maker, part, size, speed, mv, kind):
        return _Obj(DeviceLocator=slot, ManufacturerName=maker, PartNumber=part, Size=size, Speed=speed,
                    ConfiguredSpeed=speed, ConfiguredVoltage=mv, Type=_Enum(kind))
    smbios = _Obj(Bios=_Obj(Vendor="American Megatrends Inc.", Version="5021", Date="2024-09-29T00:00:00"),
                  Board=_Obj(ManufacturerName="ASUSTeK COMPUTER INC.", ProductName="TUF GAMING X570-PLUS (WI-FI)",
                             Version="Rev X.0x"),
                  Processors=[_Obj(SocketDesignation="AM4", CoreCount=8, ThreadCount=16, MaxSpeed=4850)],
                  MemoryDevices=[dimm("DIMM_A1", "Unknown", "Unknown", 0, 0, 0, "Unknown"),
                                 dimm("DIMM_A2", "A-DATA", "AX4U360032G18I-BWHD3 ", 32768, 3600, 1200, "DDR4")])
    specs = smbios_specs(smbios)
    assert specs["bios"] == {"vendor": "American Megatrends Inc.", "version": "5021", "date": "2024-09-29"}
    assert specs["cpu"] == {"socket": "AM4", "cores": 8, "threads": 16, "maxMHz": 4850}
    assert specs["memory"][0] == {"slot": "DIMM_A1", "maker": None, "part": None, "sizeMB": 0, "speed": None,
                                  "voltageMV": None, "type": None}
    assert specs["memory"][1]["part"] == "AX4U360032G18I-BWHD3" and specs["memory"][1]["type"] == "DDR4"


def test_wmi_rows_become_disk_and_gpu_specs():
    disks = disks_from_wmi([
        {"DeviceId": "5", "FriendlyName": "KIOXIA-EXCERIA G2 SSD", "FirmwareVersion": "ECFA17.1", "BusType": 17,
         "SpindleSpeed": 0, "HealthStatus": 0, "Size": 2000398934016},
        {"DeviceId": "1", "FriendlyName": "ST20000DM001-3Y3103", "FirmwareVersion": "DN02", "BusType": "11",
         "SpindleSpeed": 7200, "HealthStatus": 1, "Size": None}])
    assert (disks["5"]["bus"], disks["5"]["rpm"], disks["5"]["health"]) == ("NVMe", None, "healthy")   # SSD 轉速是 0
    assert (disks["1"]["bus"], disks["1"]["rpm"], disks["1"]["health"]) == ("SATA", 7200, "warning")
    gpus = gpus_from_wmi([{"Name": "NVIDIA GeForce RTX 5070 Ti", "DriverVersion": "32.0.16.1088",
                           "DriverDate": "20260722000000.000000-000"}])
    assert gpus["NVIDIA GeForce RTX 5070 Ti"] == {"driver": "610.88", "driverDate": "2026-07-22"}
    assert nvidia_version("32.0.15.7680") == "576.80" and nvidia_version("31.0") == "31.0"


def test_cache_totals_add_up_each_level():
    caches = [(1, 32 * 1024)] * 16 + [(2, 512 * 1024)] * 8 + [(3, 32 * 2**20)]   # 5800X：L1 指令＋資料各 8 顆
    assert cache_totals(caches) == {1: 512 * 1024, 2: 4 * 2**20, 3: 32 * 2**20}
