"""標籤、門檻、歷史、LHM 轉換。"""
from __future__ import annotations

import math

from hwglance.sensors.history import History
from hwglance.sensors.labels import label
from hwglance.sensors.lhm import SLOW_TYPES, convert, update_tree
from hwglance.sensors.thresholds import DEFAULTS, Band, level


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
