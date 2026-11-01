"""迷你模式（疊加層）：要顯示什麼（mini/items）、畫出來的圖（mini/render）、顏色跟 tokens.css 一樣、
設定的存讀、MiniMode 跟著設定開關 FPS。不開真的視窗（Win32 那一層用假的）。"""
from __future__ import annotations

import json
import re
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from hwglance import paths
from hwglance.api.app import create_app
from hwglance.fps import DemoFps
from hwglance.mini import MiniMode
from hwglance.mini.items import DEFAULT_ITEMS, DEFAULT_ORDER, ITEM_IDS, catalog, rows
from hwglance.mini.render import (BG, FG, LEVELS, MEASURES, MUTED, SWATCHES, _hex, _oklab, kind_color, lighten,
                                  render)
from hwglance.mini.window import fit
from hwglance.sensors.demo import DemoSource
from hwglance.sensors.normalize import normalize
from hwglance.sensors.service import SensorService
from hwglance.settings import COLOR_DEFAULTS, MiniSettings, SettingsStore, parse

TOKENS = paths.APP_ROOT / "frontend" / "src" / "styles" / "tokens.css"
HEADERS = {"X-HWGlance-Token": "t"}


def snap() -> dict:
    return normalize(DemoSource(seed=1).read())


def texts(row) -> list[str]:
    return [seg.text for seg in row.segs]


class NoFps:
    def status(self):
        return "FPS 要用系統管理員執行 HWGlance"

    def stats(self):
        return None


def test_default_is_the_old_mini_plus_fps():
    found = rows(snap(), DEFAULT_ITEMS, DEFAULT_ORDER, DemoFps(clock=lambda: 0.0))
    assert [r.group for r in found] == ["gpu", "cpu", "fps", "other"]
    gpu = found[0]
    assert gpu.tone == "kind:gpu" and gpu.segs[0].tone.startswith("level:") and gpu.segs[1].text == "°C"
    assert gpu.segs[2].tone == "load" and gpu.segs[3].text == "%"
    assert texts(found[2])[1::2] == ["FPS", "ms"] and 130 <= int(texts(found[2])[0]) <= 150
    assert texts(found[3]) == ["都正常"] or found[3].segs[0].tone.startswith("level:")


def test_groups_follow_the_order_and_skip_empty_ones():
    s = snap()
    found = rows(s, ("cpu.clock", "time.clock", "ram.used", "gpu.vram", "gpu.fan"), ("time", "ram", "cpu", "gpu"),
                 now=0.0)
    assert [r.group for r in found] == ["time", "ram", "cpu", "gpu"]
    assert re.fullmatch(r"\d\d:\d\d", texts(found[0])[0])
    assert texts(found[1])[1] == "GB" and texts(found[2])[1] == "GHz"
    assert texts(found[3])[1] == "GB" and "/15.9" in texts(found[3])[0] and texts(found[3])[3] == "RPM"
    assert found[3].segs[0].tone == "data" and found[3].segs[2].tone == "fan"


def test_fahrenheit_missing_values_and_english_labels():
    found = rows(snap(), ("gpu.temp", "gpu.hotspot", "ram.used"), DEFAULT_ORDER, unit="F", cjk=False)
    gpu = texts(found[0])
    assert gpu[1] == "°F" and gpu[2:] == ["熱點", "—"]           # RTX 50 沒有熱點
    assert found[1].label == "RAM"                               # 沒有中文字型時用英文


def test_fps_problem_replaces_the_row():
    found = rows(snap(), ("fps.fps", "fps.low1", "cpu.temp"), DEFAULT_ORDER, NoFps())
    fps_row = next(r for r in found if r.group == "fps")
    assert texts(fps_row) == ["FPS 要用系統管理員執行 HWGlance"]


def test_worst_other_hardware_names_the_problem():
    s = snap()
    chipset = next(t for t in s["overview"] if t["kind"] == "board" and "晶片組" in t["label"])
    chipset["level"] = "hot"
    worst = rows(s, ("other.worst",), DEFAULT_ORDER)[0]
    assert worst.segs[0].text == f"過熱 {chipset['label']}" and worst.segs[0].tone == "level:hot"


def test_catalog_lists_every_item_once():
    ids = [i["id"] for g in catalog() for i in g["items"]]
    assert sorted(ids) == sorted(ITEM_IDS) and [g["group"] for g in catalog()] == list(DEFAULT_ORDER)


def test_render_background_opacity_and_solid_text():
    found = rows(snap(), DEFAULT_ITEMS, DEFAULT_ORDER, DemoFps(clock=lambda: 0.0))
    clear = render(found, opacity=0, scale=1.0, grab=False)
    assert clear.mode == "RGBA" and clear.getpixel((clear.width // 2, 1))[3] == 0      # 只剩字
    assert clear.getchannel("A").getextrema()[1] == 255                                # 字不透明
    grabbable = render(found, opacity=0, scale=1.0)
    assert grabbable.getpixel((grabbable.width // 2, 1))[3] == 1                       # 整個框都拖得動
    half = render(found, opacity=50, scale=2.0)
    assert half.getpixel((half.width // 2, 2))[3] == 128
    assert 1.8 <= half.height / clear.height <= 2.2                                     # 大小跟著倍率
    wide = render(found, opacity=50, scale=1.0, min_width=clear.width + 40)
    assert wide.width == clear.width + 40                                               # 只變寬、不忽寬忽窄


def test_palette_matches_tokens_css():
    dark = TOKENS.read_text(encoding="utf-8").split("prefers-color-scheme: dark", 1)[1]
    var = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-f]{6})", dark))
    assert {k: var[f"swatch-{k}"] for k in SWATCHES} == SWATCHES
    assert {k: var[f"m-{k}"] for k in MEASURES} == MEASURES
    assert {k: var[k] for k in ("ok", "warm", "hot")} == {k: LEVELS[k] for k in ("ok", "warm", "hot")}
    assert (_hex(var["bg"]), var["fg"], var["fg-muted"]) == (BG, FG, MUTED)
    assert kind_color("gpu", {}) == _hex(SWATCHES[COLOR_DEFAULTS["gpu"]])
    assert kind_color("gpu", {"gpu": "lime"}) == _hex(SWATCHES["lime"])


def test_custom_colors_are_lightened_like_the_web_page():
    L, a, b = _oklab(lighten("#b3122e"))                          # 網頁：oklch(0.72 0.189 20.8)
    assert abs(L - 0.72) < 0.01 and abs((a * a + b * b) ** 0.5 - 0.189) < 0.02
    assert lighten("#a6d96a") == (0xA6, 0xD9, 0x6A)                # 已經夠亮的不動


def test_mini_settings_are_lenient_on_disk_and_strict_in_the_api():
    m = parse({"mini": {"items": ["gpu.temp", "nope", "gpu.temp"], "order": ["fps", "zzz"],
                        "opacity": 150, "scale": 40, "locked": "yes"}}).mini
    assert m.items == ("gpu.temp",) and m.order[0] == "fps" and set(m.order) == set(DEFAULT_ORDER)
    assert (m.opacity, m.scale, m.locked) == (100, 70, False)
    store = SettingsStore()
    store.update(unit="F")
    assert "mini" not in json.loads(paths.settings_file().read_text(encoding="utf-8"))   # 預設不存
    store.update(mini={"items": ["cpu.temp", "fps.fps"], "order": list(DEFAULT_ORDER), "opacity": 30,
                       "scale": 150, "locked": True})
    assert SettingsStore().get().mini == MiniSettings(("cpu.temp", "fps.fps"), DEFAULT_ORDER, 30, 150, True)
    with pytest.raises(ValueError, match="不認得的項目"):
        store.update(mini={"items": ["gpu.rgb"]})
    with pytest.raises(ValueError, match="大小要在 70～200%"):
        store.update(mini={"scale": 500})


def test_settings_api_has_the_catalog_and_fps_status():
    service = SensorService(DemoSource(seed=1), env={"admin": True, "pawnio": "2.2.0"})
    assert service.open()
    hotkeys = [{"keys": "Ctrl+Alt+L", "action": "鎖定／解除鎖定", "ok": False}]
    app = create_app(service, "t", settings=SettingsStore(),
                     mini_info=lambda: {"fps": "還沒有 PresentMon", "hotkeys": hotkeys})
    c = TestClient(app, base_url="http://127.0.0.1:8796")
    s = c.get("/api/settings").json()
    assert s["mini"] == s["miniDefaults"] and s["fps"] == "還沒有 PresentMon" and s["hotkeys"] == hotkeys
    assert s["miniCatalog"][0]["group"] == "gpu" and s["miniRange"] == {"opacity": [0, 100], "scale": [70, 200]}
    body = {"unit": "C", "interval": 1, "closeToTray": True, "bands": s["bands"],
            "mini": {**s["mini"], "opacity": 20, "items": ["time.clock"]}}
    r = c.put("/api/settings", json=body, headers=HEADERS).json()
    assert r["mini"]["opacity"] == 20 and r["mini"]["items"] == ["time.clock"]
    r = c.put("/api/settings", json={**body, "mini": {"opacity": "a lot"}}, headers=HEADERS)
    assert r.status_code == 400 and "背景不透明度要是數字" in r.json()["detail"]


class FakeWindow:
    def __init__(self, frame, *, state_file, on_main, on_close, on_lock, locked, on_toggle=None,
                 on_settings=None) -> None:
        self.frame, self.on_close, self.on_lock = frame, on_close, on_lock
        self.on_toggle, self.on_settings = on_toggle, on_settings
        self.visible, self.locked, self.refreshes = False, locked, 0
        self.hotkeys = [{"keys": "Ctrl+Alt+L", "action": "鎖定／解除鎖定", "ok": True},
                        {"keys": "Ctrl+Alt+M", "action": "開關迷你模式", "ok": True}]

    def start(self) -> bool:
        return True

    def show(self) -> None:
        self.visible = True

    def hide(self) -> None:
        self.visible = False

    def refresh(self) -> None:
        self.refreshes += 1

    def set_locked(self, locked: bool) -> None:
        self.locked = locked

    def stop(self) -> None:
        pass


class CountingFps(DemoFps):
    def __init__(self) -> None:
        super().__init__(clock=lambda: 0.0)
        self.wants: list[bool] = []

    def want(self, on: bool) -> None:
        self.wants.append(on)


def test_mini_mode_draws_from_settings_and_runs_fps_only_when_needed():
    service = SensorService(DemoSource(seed=1), env={"admin": True, "pawnio": None})
    assert service.open()
    service.tick()
    store, fps, closed = SettingsStore(), CountingFps(), []
    mini = MiniMode(service, store, fps, on_main=lambda: None, on_close=lambda: closed.append(1),
                    window_factory=FakeWindow)
    assert mini.start() and mini.ready
    window = mini._window
    mini.show()
    image = window.frame(1.0)
    assert image is not None and image.width > 50 and fps.wants[-1] is True        # 預設有勾 FPS
    store.update(mini=replace(store.get().mini, items=("cpu.temp",)))
    assert window.refreshes == 1 and window.frame(1.0) is not None and fps.wants[-1] is False
    mini.toggle_lock()
    assert window.locked and store.get().mini.locked
    window.on_close()                                    # 右鍵「關閉迷你模式」
    assert not mini.visible and closed == [1] and fps.wants[-1] is False


def test_locking_tells_how_to_unlock_once():
    """鎖定後滑鼠碰不到它：第一次鎖定時用系統匣通知說怎麼解除（使用者 2026-11-02：鎖了之後什麼都改不了），
    之後不再跳（同一天：每次都跳很吵）。"""
    service = SensorService(DemoSource(seed=1), env={"admin": True, "pawnio": None})
    store, notes, toggled, settings = SettingsStore(), [], [], []
    mini = MiniMode(service, store, CountingFps(), on_main=lambda: None, on_close=lambda: None,
                    on_toggle=lambda: toggled.append(1), on_settings=lambda: settings.append(1),
                    notify=lambda message, title: notes.append((title, message)), window_factory=FakeWindow)
    assert mini.hotkey("鎖定／解除鎖定") == "Ctrl+Alt+L"
    mini.toggle_lock()
    assert len(notes) == 1 and notes[0][0] == "迷你模式已鎖定" and "Ctrl+Alt+L" in notes[0][1]
    assert store.get().mini_lock_hint_shown and store.get().mini.locked
    store.update(unit="F")                               # 其他設定改了：不提醒
    mini.toggle_lock()
    mini.toggle_lock()
    assert len(notes) == 1                               # 只提醒第一次：每次鎖定都跳很吵（使用者 2026-11-02）
    again = MiniMode(service, SettingsStore(), CountingFps(), on_main=lambda: None, on_close=lambda: None,
                     notify=lambda message, title: notes.append((title, message)), window_factory=FakeWindow)
    again.toggle_lock()
    again.toggle_lock()
    assert len(notes) == 1                               # 重開之後也不再提醒（存在設定檔）
    window = mini._window
    window.on_toggle()
    window.on_settings()
    assert toggled == [1] and settings == [1]            # 快速鍵、右鍵「迷你模式設定…」接到外面


def test_fit_pulls_the_box_back_on_screen():
    work = (0, 0, 1920, 1040)                            # 工作區（扣掉工作列）
    assert fit(100, 100, 300, 200, work) == (100, 100)
    assert fit(4519 - 3840, -113, 300, 200, work) == (679, 0)          # 上面跑出去一截（使用者 2026-11-02 的位置）
    assert fit(1800, 950, 300, 200, work) == (1620, 840)               # 右下角超出
    assert fit(-50, 10, 3000, 200, work) == (0, 10)                    # 比螢幕還寬就對齊左邊
