"""Mac 第 2 段：選單列的字（menubar.py 的純函式）、設定頁只列 Mac 有的門檻、視窗的網址與拖曳範圍（ui.py）、
視窗程序用 App 裡的 Python（core.window_python）。不碰 AppKit，Windows 上也跑得了。"""
from __future__ import annotations

import sys

from hwglance import core
from hwglance.api.app import settings_payload
from hwglance.menubar import menu_lines, title_text
from hwglance.sensors.demo import DemoSource, MacDemo
from hwglance.sensors.mac import MAC_ROLES
from hwglance.sensors.normalize import normalize
from hwglance.sensors.service import SensorService
from hwglance.settings import Settings
from hwglance.ui import MacShell, mac_url


def snap_with(**prefs) -> dict:
    snap = normalize(MacDemo(seed=1).read())
    snap["prefs"] = {"unit": "C", **prefs}
    return snap


def test_title_is_the_cpu_temperature_with_its_level():
    snap = snap_with()
    cpu = next(t for t in snap["overview"] if t["kind"] == "cpu")
    assert title_text(snap) == (f"{round(cpu['value'])}°", cpu["level"])
    fahrenheit = title_text(snap_with(unit="F"))[0]
    assert fahrenheit == f"{round(cpu['value'] * 9 / 5 + 32)}°"
    assert title_text({"overview": []}) == ("--°", "none")


def test_menu_lists_every_overview_tile_and_marks_hot_ones():
    snap = snap_with()
    snap["overview"][0] |= {"value": 96.0, "level": "warm"}
    lines = menu_lines(snap)
    assert [level for _, level in lines] == [t["level"] for t in snap["overview"]]
    assert lines[0][0] == "CPU（Apple M6）　96°・偏高"
    assert lines[2][0].startswith("電池（MacBook Pro）　")
    assert menu_lines({"overview": []}) == [("還沒有讀數", "none")]


def test_settings_page_lists_only_the_kinds_this_machine_has():
    payload = settings_payload(Settings(), tjmax=105.0, roles=MAC_ROLES)
    assert list(payload["labels"]) == ["cpu", "gpu", "nvme", "battery"]
    assert payload["labels"]["cpu"] == "CPU（TjMax 105°C）" and payload["defaults"]["cpu"] == [90, 100]
    assert set(payload["bands"]) >= {"chipset", "hdd"}                 # 沒列的門檻照樣給，存檔時不會被洗掉
    assert "chipset" in settings_payload(Settings())["labels"]          # Windows 照舊全部列
    assert SensorService(MacDemo(seed=1)).roles() == MAC_ROLES
    assert SensorService(DemoSource(seed=1)).roles() is None


def test_mac_window_url_and_drag_region():
    assert mac_url("http://127.0.0.1:8796/", 28) == "http://127.0.0.1:8796/?shell=mac&titlebar=28&lights=78"
    assert mac_url("http://127.0.0.1:8796/#settings/mini", 32, 70) == \
        "http://127.0.0.1:8796/?shell=mac&titlebar=32&lights=70#settings/mini"
    shell = MacShell(28)
    assert shell._drags(300, 10) and not shell._drags(300, 40)          # 網頁還沒回報：只有標題列那一條
    shell.drag_region(30, [[700, 3, 60, 24], [10, 5]])                  # 「設定」按鈕；不完整的長方形不收
    assert shell._drags(300, 15) and not shell._drags(720, 15) and not shell._drags(300, 31)
    assert shell.drag_region(1e9, []) and shell._height == 200.0        # 亂傳的高度有上限


def test_window_process_uses_the_app_python_on_mac(monkeypatch, tmp_path):
    fake = tmp_path / "HWGlance.app" / "Contents" / "MacOS" / "Python"
    fake.parent.mkdir(parents=True)
    fake.write_text("")
    monkeypatch.setenv("HWGLANCE_APP_PYTHON", str(fake))
    monkeypatch.setattr(sys, "platform", "darwin")
    assert core.window_python() == str(fake)
    monkeypatch.setenv("HWGLANCE_APP_PYTHON", str(tmp_path / "missing"))
    assert core.window_python() != str(tmp_path / "missing")            # App 被搬走了：照舊用這個 Python
