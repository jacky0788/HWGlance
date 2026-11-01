"""詳細資訊（點硬體跳出的視窗）：用示範資料＋示範規格整理，不碰硬體。"""
from __future__ import annotations

from conftest import sensor

from hwglance.sensors.demo import DemoSource
from hwglance.sensors.details import device_details
from hwglance.sensors.model import RawHardware
from hwglance.sensors.normalize import normalize


def build(device_id: str, specs: dict | None = None) -> dict | None:
    demo = DemoSource(seed=1)
    tree = demo.read()
    return device_details(tree, normalize(tree), device_id, demo.specs() if specs is None else specs)


def section(detail: dict, title: str) -> dict:
    return next(s for s in detail["sections"] if s["title"] == title)


def rows(sec: dict) -> dict[str, dict]:
    return {r["label"]: r for r in sec.get("rows", [])}


def test_cpu_has_specs_temperatures_and_per_core_table():
    d = build("/amdcpu/0")
    assert [s["title"] for s in d["sections"]] == ["規格", "溫度", "負載", "時脈", "功耗與電壓"]   # 全部都有歸類
    spec = rows(section(d, "規格"))
    assert spec["腳位"]["value"] == "AM4" and spec["核心／執行緒"]["value"] == "8 核心、16 執行緒"
    assert spec["最高時脈"]["value"] == 4.85 and spec["快取"]["value"] == "L1 512 KB・L2 4 MB・L3 32 MB"
    temps = rows(section(d, "溫度"))
    head = temps["CPU 溫度（Tctl/Tdie）"]
    assert head["temp"] and head["unit"] == "°C" and head["level"] in ("ok", "warm", "hot") and head["note"]
    assert "主機板量的 CPU 溫度" in temps and "CCD1（瞬間）" in temps          # CCD 只在詳細資訊出現，附說明
    load = section(d, "負載")
    assert len(load["bars"]["items"]) == 16 and load["bars"]["max"] == 100
    table = section(d, "時脈")["table"]
    assert [c["label"] for c in table["columns"]] == ["時脈", "實際時脈", "倍頻", "功耗", "VID"]
    assert len(table["rows"]) == 8 and table["rows"][0]["label"] == "核心 1"


def test_gpu_lists_engines_vram_fans_and_driver():
    d = build("/gpu-nvidia/0")
    spec = rows(section(d, "規格"))
    assert spec["顯存容量"]["value"] == 16 and spec["驅動版本"]["value"] == "610.88"
    assert "熱點" not in rows(section(d, "溫度"))                              # RTX 50 沒有
    engines = [i["label"] for i in section(d, "負載")["bars"]["items"]]
    assert engines[:4] == ["3D", "影片編碼", "影片解碼", "複製"]
    assert rows(section(d, "顯存"))["借用的系統記憶體"] == {"label": "借用的系統記憶體", "value": 290, "unit": "MB",
                                                       "note": "顯存不夠時借用系統記憶體的量"}
    assert len(section(d, "風扇")["table"]["rows"]) == 2
    assert rows(section(d, "PCIe 傳輸"))["接收"]["unit"] in ("KB/s", "MB/s")
    assert all(s["title"] != "其他讀數" for s in d["sections"])               # 其他 D3D 引擎不列


def test_gpu_memory_users_only_over_100_mb():
    demo = DemoSource(seed=1)
    tree = demo.read()
    d = device_details(tree, normalize(tree), "/gpu-nvidia/0", demo.specs(), demo.gpu_processes())
    titles = [s["title"] for s in d["sections"]]
    assert titles.index("顯存被誰佔用") == titles.index("顯存") + 1
    sec = section(d, "顯存被誰佔用")
    users = sec["ranking"]["items"]                       # 排行，不是一般讀數的「名稱：數值」
    assert "rows" not in sec and sec["measure"] == "data" and sec["ranking"]["total"] == {"value": 15.9, "unit": "GB"}
    assert all(r["unit"] in ("MB", "GB") for r in users) and "Discord" not in [r["label"] for r in users]   # 64 MB 不列
    assert users[0]["label"].startswith("桌面視窗管理員") or users[0]["label"].startswith("python")
    razer = next(r for r in users if r["label"] == "RazerAppEngine")
    assert (razer["value"], razer["unit"], razer["note"]) == (114, "MB", "PID 20432") and 0 < razer["share"] < 0.01
    assert users[0]["share"] == max(r["share"] for r in users) <= 1
    none = device_details(tree, normalize(tree), "/gpu-nvidia/0", {}, [{"pid": 1, "name": "x", "bytes": 5}])
    assert "目前沒有程式超過" in section(none, "顯存被誰佔用")["note"] and "ranking" not in section(none, "顯存被誰佔用")
    assert "顯存被誰佔用" not in [s["title"] for s in build("/gpu-nvidia/0")["sections"]]   # 查不到就不列


def test_sections_say_what_they_measure():
    """前端照 measure 配圖示與數值顏色：同一個視窗裡負載、時脈、功耗…一眼分得開（使用者 2026-11-01）。"""
    measures = {s["title"]: s.get("measure") for s in build("/gpu-nvidia/0")["sections"]}
    assert measures == {"規格": None, "溫度": "temp", "負載": "load", "時脈": "clock", "顯存": "data",
                        "功耗與電壓": "power", "風扇": "fan", "PCIe 傳輸": "flow"}
    assert section(build("/motherboard"), "電壓")["measure"] == "power"
    assert section(build("/ram"), "實體記憶體")["measure"] == "data"
    assert section(build("/nvme/5"), "讀寫")["measure"] == "flow"


def test_board_shows_bios_fans_with_unplugged_and_voltages():
    d = build("/motherboard")
    spec = rows(section(d, "規格"))
    assert spec["BIOS 版本"]["value"] == "5021" and spec["感測晶片"]["value"] == "Nuvoton NCT6798D"
    fans = {r["label"]: r["cells"] for r in section(d, "風扇")["table"]["rows"]}
    assert fans["水冷幫浦"] == [0, None, "沒接"] and fans["CPU 風扇"][2] == ""
    volts = rows(section(d, "電壓"))
    assert volts["+12V"]["value"] == 12.192 and volts["主機板電池"]["note"]


def test_memory_lists_slots_including_empty_ones():
    spec = section(build("/ram"), "規格")
    assert rows(spec)["總容量"]["value"] == 64 and rows(spec)["Windows 可用"]["value"] < 64
    assert rows(spec)["插了幾條"]["value"] == "2 條（共 4 個插槽）"
    slots = {r["label"]: r["cells"] for r in spec["table"]["rows"]}
    assert slots["DIMM_A1"][0] == "（空）" and slots["DIMM_A2"][:4] == ["A-DATA", "AX4U360032G18I-BWHD3", 32, 3600]
    assert "沒有溫度感測器" in spec["note"]


def test_storage_health_temperatures_and_units():
    d = build("/nvme/5")
    spec = rows(section(d, "規格"))
    assert spec["韌體版本"]["value"] == "ECFA17.1" and (spec["容量"]["value"], spec["容量"]["unit"]) == (2.0, "TB")
    health = rows(section(d, "健康"))
    assert health["Windows 判斷"]["value"] == "良好" and health["總寫入量"]["unit"] == "TB"
    assert health["備用空間"]["note"] == "低於 5% 要注意" and health["通電時數"]["note"] == "約 162 天"
    temps = rows(section(d, "溫度"))
    assert temps["警告溫度"]["value"] == 71 and "level" not in temps["警告溫度"]       # 門檻不是讀數
    hdd = rows(section(build("/hdd/1"), "規格"))
    assert hdd["類型"]["value"] == "機械硬碟" and "轉速" not in hdd           # 這台 Windows 回報不知道轉速
    with_rpm = rows(section(build("/hdd/1", specs={"disks": {"1": {"rpm": 7200}}}), "規格"))
    assert with_rpm["轉速"]["value"] == 7200


def test_without_specs_and_unknown_devices():
    d = build("/amdcpu/0", specs={})
    assert [r["label"] for r in section(d, "規格")["rows"]] == ["型號"]          # 讀不到規格就只剩型號
    assert build("/nope") is None


def test_unknown_sensors_end_up_in_others():
    hw = RawHardware("/powermonitor/0", "WireView Pro", "PowerMonitor", [
        sensor("/powermonitor/0", "Temperature", 0, "Connector", 41.0),
        sensor("/powermonitor/0", "Current", 0, "12V", 24.5)])
    d = device_details([hw], normalize([hw]), "/powermonitor/0")
    assert rows(section(d, "溫度"))["Connector"]["value"] == 41.0
    other = section(d, "其他讀數")
    assert rows(other)["12V"]["unit"] == "A" and rows(other)["12V"]["measure"] == "power"   # 什麼都有，每行自己帶
    assert "measure" not in other
