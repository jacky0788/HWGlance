"""使用者設定：data/settings.json。門檻只存跟預設不一樣的；檔案壞掉或值不認得就用預設，不會讓程式開不起來。

    unit         溫度單位 C／F（只影響顯示，門檻一律以 °C 存）
    interval     幾秒讀一次：1、2、5
    closeToTray  關掉視窗時留在系統匣繼續監控（False＝整個結束）
    bands        {種類: [偏高, 過熱]}，°C
"""
from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import paths
from .sensors.thresholds import DEFAULTS, Band

UNITS = ("C", "F")
INTERVALS = (1, 2, 5)
BAND_RANGE = (20.0, 130.0)


@dataclass(frozen=True)
class Settings:
    unit: str = "C"
    interval: int = 1
    close_to_tray: bool = True
    bands: dict[str, tuple[float, float]] = field(default_factory=dict)   # 只放改過的
    tray_hint_shown: bool = False                                        # 「還在系統匣」的提示顯示過了沒

    def merged_bands(self) -> dict[str, Band]:
        out = dict(DEFAULTS)
        for kind, (warm, hot) in self.bands.items():
            if kind in out:
                out[kind] = Band(warm, hot)
        return out


def valid_band(warm: float, hot: float) -> bool:
    return BAND_RANGE[0] <= warm < hot <= BAND_RANGE[1]


def only_changes(bands: dict[str, tuple[float, float]]) -> dict[str, tuple[float, float]]:
    """跟預設一樣的門檻不存：之後調整預設時，沒改過的人會跟著新預設走。"""
    out = {}
    for kind, (warm, hot) in bands.items():
        default = DEFAULTS.get(kind)
        if default is not None and (warm, hot) != (default.warm, default.hot):
            out[kind] = (float(warm), float(hot))
    return out


def parse(data: object) -> Settings:
    s = Settings()
    if not isinstance(data, dict):
        return s
    unit = data.get("unit") if data.get("unit") in UNITS else s.unit
    interval = data.get("interval") if data.get("interval") in INTERVALS else s.interval
    close = data.get("closeToTray") if isinstance(data.get("closeToTray"), bool) else s.close_to_tray
    hint = data.get("trayHintShown") if isinstance(data.get("trayHintShown"), bool) else False
    bands: dict[str, tuple[float, float]] = {}
    raw = data.get("bands")
    if isinstance(raw, dict):
        for kind, pair in raw.items():
            try:
                warm, hot = (float(x) for x in pair)
            except (TypeError, ValueError):
                continue
            if kind in DEFAULTS and valid_band(warm, hot):
                bands[kind] = (warm, hot)
    return Settings(unit=unit, interval=interval, close_to_tray=close, bands=only_changes(bands),
                    tray_hint_shown=hint)


def to_json(s: Settings) -> dict:
    return {"unit": s.unit, "interval": s.interval, "closeToTray": s.close_to_tray,
            "bands": {k: list(v) for k, v in s.bands.items()}, "trayHintShown": s.tray_hint_shown}


class SettingsStore:
    """設定的唯一來源：API 改、本體讀。改了會通知訂閱者（例如感測服務換門檻、換更新頻率）。"""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or paths.settings_file()
        self._lock = threading.Lock()
        self._listeners: list[Callable[[Settings], None]] = []
        try:
            self._settings = parse(json.loads(self._path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            self._settings = Settings()

    def get(self) -> Settings:
        with self._lock:
            return self._settings

    def subscribe(self, listener: Callable[[Settings], None]) -> None:
        self._listeners.append(listener)

    def update(self, **changes) -> Settings:
        """changes 用 Settings 的欄位名；bands 會先去掉跟預設一樣的。值不合法丟 ValueError。"""
        if "bands" in changes:
            for kind, (warm, hot) in changes["bands"].items():
                if kind not in DEFAULTS:
                    raise ValueError(f"不認得的種類：{kind}")
                if not valid_band(warm, hot):
                    raise ValueError(f"{kind} 的門檻不合理：偏高要小於過熱，而且在 "
                                     f"{BAND_RANGE[0]:.0f}～{BAND_RANGE[1]:.0f}°C 之間")
            changes["bands"] = only_changes(changes["bands"])
        if "unit" in changes and changes["unit"] not in UNITS:
            raise ValueError(f"溫度單位只能是 {'、'.join(UNITS)}")
        if "interval" in changes and changes["interval"] not in INTERVALS:
            raise ValueError(f"更新頻率只能是 {'、'.join(map(str, INTERVALS))} 秒")
        with self._lock:
            self._settings = replace(self._settings, **changes)
            current = self._settings
            self._save(current)
        for listener in self._listeners:
            listener(current)
        return current

    def _save(self, s: Settings) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(to_json(s), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)
