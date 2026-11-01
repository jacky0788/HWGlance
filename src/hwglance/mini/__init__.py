"""迷你模式：疊加在遊戲上的小視窗（像 RivaTuner），顯示自己勾的項目（溫度、負載、時脈、功耗、FPS…）。

    items.py   能顯示什麼、從快照怎麼拿值（純函式）
    render.py  畫成一張有透明度的圖（Pillow）
    window.py  Win32 分層視窗：背景可以半透明、字照樣清楚；拖曳、右鍵選單、鎖定（滑鼠穿透）
    ../fps.py  遊戲 FPS（PresentMon）

MiniMode 把它們接起來：視窗每 0.5 秒要一張圖時，拿最新的快照、設定、FPS 畫給它。
以前的迷你視窗是 WebView2 網頁（約 195 MB）；這個在本體裡、不用 WebView2（SPEC D29）。
"""
from __future__ import annotations

import traceback
from collections.abc import Callable
from dataclasses import replace

from .. import paths
from .items import rows, wants_fps
from .render import has_cjk_font, render


class MiniMode:
    def __init__(self, service, store, fps, *, on_main: Callable[[], None], on_close: Callable[[], None],
                 on_change: Callable[[], None] | None = None, window_factory=None) -> None:
        self._service, self._store, self._fps = service, store, fps
        self._on_close, self._on_change = on_close, on_change
        self.ready = False                                # 視窗建好了沒（不是 Windows 或建立失敗就一直是 False）
        self._key: tuple | None = None
        self._width = 0
        self._cjk = has_cjk_font()
        if window_factory is None:
            from .window import MiniWindow
            window_factory = MiniWindow
        self._window = window_factory(self._frame, state_file=paths.mini_state_file(), on_main=on_main,
                                      on_close=self._closed, on_lock=self.toggle_lock,
                                      locked=store.get().mini.locked)
        store.subscribe(self._settings_changed)

    @property
    def visible(self) -> bool:
        return self._window.visible

    @property
    def locked(self) -> bool:
        return self._store.get().mini.locked

    def start(self) -> bool:
        self.ready = bool(self._window.start())
        return self.ready

    def show(self) -> None:
        self._window.show()
        self._changed()

    def hide(self) -> None:
        self._window.hide()
        self._fps.want(False)
        self._changed()

    def toggle(self) -> None:
        self.hide() if self.visible else self.show()

    def toggle_lock(self) -> None:
        s = self._store.get()
        self._store.update(mini=replace(s.mini, locked=not s.mini.locked))

    def stop(self) -> None:
        self._window.stop()
        self._fps.stop()

    def _closed(self) -> None:
        """右鍵選單的「關閉迷你模式」：跟以前關掉迷你視窗一樣（留在系統匣，或照設定整個結束）。"""
        self.hide()
        self._on_close()

    def _changed(self) -> None:
        if self._on_change is not None:
            self._on_change()

    def _settings_changed(self, s) -> None:
        self._window.set_locked(s.mini.locked)
        self._window.refresh()
        self._changed()

    def _frame(self, dpi_scale: float):
        """視窗要一張新圖（它自己的執行緒，每 0.5 秒）。出錯就回 None，視窗留著上一張。"""
        try:
            s = self._store.get()
            m = s.mini
            self._fps.want(self.visible and wants_fps(m.items))
            found = rows(self._service.snapshot(), m.items, m.order, self._fps, unit=s.unit, cjk=self._cjk)
            key = (m.items, m.order, m.scale, dpi_scale, s.unit)
            if key != self._key:                          # 換了項目或大小：寬度重新算，不然只變寬、不忽寬忽窄
                self._key, self._width = key, 0
            image = render(found, opacity=m.opacity, scale=dpi_scale * m.scale / 100, colors=s.colors,
                           min_width=self._width, grab=not m.locked)
            self._width = image.width
            return image
        except Exception:                                 # noqa: BLE001
            traceback.print_exc()
            return None
