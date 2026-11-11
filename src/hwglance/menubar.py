"""Mac 的選單列（Windows 系統匣的 Mac 版）：選單列上直接寫 CPU 溫度（沒有就寫 GPU），偏高變橘、過熱變紅；
點開是選單：每個溫度一行（偏高、過熱的標出來）、開啟主畫面、結束 HWGlance。

Mac 的本體程序是「輔助程式」（不在 Dock 出現，只在選單列），主執行緒跑 AppKit 的事件迴圈（run）；
感測執行緒每輪呼叫 update()，只有字變了才排到主執行緒去換。再從 Launchpad、Spotlight 打開 HWGlance.app 時，
系統會叫本體「重新打開」（applicationShouldHandleReopen）→ 開主畫面。
文字是純函式（tests/test_menubar.py 測），MenuBar 類別才碰 AppKit（PyObjC，pywebview 在 Mac 本來就會裝）。
"""
from __future__ import annotations

import subprocess
import threading
import traceback
from collections.abc import Callable

from .tray import degrees, icon_text

MARK = {"warm": "偏高", "hot": "過熱"}


def title_text(snap: dict) -> tuple[str, str]:
    """選單列上的字與等級：「41°」；CPU 優先，沒有 CPU 溫度就用 GPU 核心；什麼都沒有是「--°」。"""
    text, level = icon_text(snap)
    return f"{text}°", level


def menu_lines(snap: dict) -> list[tuple[str, str]]:
    """選單裡的溫度：[(文字, 等級)]，跟主畫面的總覽同一排、同樣的順序，例如「CPU（Apple M6）　41°」。"""
    unit = snap.get("prefs", {}).get("unit", "C")
    out = []
    for t in snap.get("overview", []):
        name = f"{t['label']}（{t['sub']}）" if t.get("sub") and t["sub"] != t["label"] else t["label"]
        mark = f"・{MARK[t['level']]}" if t["level"] in MARK else ""
        out.append((f"{name}　{degrees(t['value'], unit)}{mark}", t["level"]))
    return out or [("還沒有讀數", "none")]


_target_class = None


def _target():
    """選單的動作要掛在 Objective-C 物件上；類別在一個程序裡只能定義一次。"""
    global _target_class
    if _target_class is None:
        from Foundation import NSObject

        class HWGlanceMenuTarget(NSObject):
            def initWithBar_(self, bar):
                self = self.init()
                if self is not None:
                    self.bar = bar
                return self

            def openMain_(self, _sender):
                self.bar.open_main()

            def quitApp_(self, _sender):
                self.bar.quit()

            def applicationShouldHandleReopen_hasVisibleWindows_(self, _app, _visible):
                self.bar.open_main()          # 已經開著時再從 Launchpad、Dock、Spotlight 打開
                return True

        _target_class = HWGlanceMenuTarget
    return _target_class


class MenuBar:
    """要在主執行緒建立（core.run）。runs_main_loop：core 用 run() 代替一般的等待迴圈。"""

    runs_main_loop = True

    def __init__(self, on_open: Callable[[str], None], on_quit: Callable[[], None]) -> None:
        import AppKit
        self._ak = AppKit
        self._on_open, self._on_quit = on_open, on_quit
        self._shown: tuple | None = None
        app = AppKit.NSApplication.sharedApplication()
        app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)   # 不在 Dock 出現
        self._handler = _target().alloc().initWithBar_(self)
        app.setDelegate_(self._handler)
        self.item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(AppKit.NSVariableStatusItemLength)
        button = self.item.button()
        image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_("thermometer.medium", "溫度")
        if image is not None:
            image.setTemplate_(True)
            button.setImage_(image)
            button.setImagePosition_(AppKit.NSImageLeading)
        button.setToolTip_("HWGlance")
        self.menu = AppKit.NSMenu.alloc().init()
        self.menu.setAutoenablesItems_(False)
        self.item.setMenu_(self.menu)
        self._apply("--°", "none", [("還沒有讀數", "none")])

    # ---- 主執行緒 ----

    def _font(self):
        ak = self._ak
        return ak.NSFont.monospacedDigitSystemFontOfSize_weight_(ak.NSFont.systemFontSize(), ak.NSFontWeightMedium)

    def _color(self, level: str):
        ak = self._ak
        return {"warm": ak.NSColor.systemOrangeColor(), "hot": ak.NSColor.systemRedColor()}.get(level)

    def _apply(self, title: str, level: str, lines: list[tuple[str, str]]) -> None:
        ak = self._ak
        attrs = {ak.NSFontAttributeName: self._font()}
        color = self._color(level)
        if color is not None:                         # 正常時用選單列本來的顏色（深淺色自動跟著變）
            attrs[ak.NSForegroundColorAttributeName] = color
        self.item.button().setAttributedTitle_(ak.NSAttributedString.alloc().initWithString_attributes_(title, attrs))
        self.menu.removeAllItems()
        for text, line_level in lines:
            entry = ak.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(text, None, "")
            entry.setEnabled_(False)
            line_color = self._color(line_level)
            if line_color is not None:
                entry.setAttributedTitle_(ak.NSAttributedString.alloc().initWithString_attributes_(
                    text, {ak.NSForegroundColorAttributeName: line_color, ak.NSFontAttributeName: ak.NSFont.menuFontOfSize_(0)}))
            self.menu.addItem_(entry)
        self.menu.addItem_(ak.NSMenuItem.separatorItem())
        for text, action, key in (("開啟主畫面", "openMain:", "o"), ("結束 HWGlance", "quitApp:", "q")):
            entry = ak.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(text, action, key)
            entry.setTarget_(self._handler)
            self.menu.addItem_(entry)

    def open_main(self) -> None:
        # 開視窗可能要等上一個視窗程序結束（core.Window 的 respawn_gap），不能卡住選單列
        threading.Thread(target=self._on_open, args=("main",), daemon=True, name="hwglance-open").start()

    def quit(self) -> None:
        self._on_quit()

    def run(self, stop: threading.Event) -> None:
        """跑 AppKit 的事件迴圈到 stop 被設定（結束 HWGlance、API 叫結束、Ctrl+C），然後回到 core 收尾。
        不用 AppHelper.runEventLoop／stopEventLoop：它找不到自己的迴圈時會改叫 terminate，程序直接結束，
        core 的收尾（黑盒子寫完、刪 instance.json）就不會跑（2026-11-11 實測）。"""
        import signal

        from PyObjCTools import AppHelper

        def watch() -> None:
            stop.wait()
            AppHelper.callAfter(self._stop_loop)

        threading.Thread(target=watch, daemon=True, name="hwglance-menubar-stop").start()
        try:                                          # Ctrl+C：Python 的訊號處理在下一個回呼（每秒換字）時跑
            signal.signal(signal.SIGINT, lambda *_: stop.set())
        except ValueError:                            # 不在主執行緒（測試）
            pass
        self._ak.NSApplication.sharedApplication().run()
        stop.set()

    def _stop_loop(self) -> None:
        """（主執行緒）叫事件迴圈停，再丟一個空事件把它叫醒，run() 才會回來。"""
        ak = self._ak
        app = ak.NSApplication.sharedApplication()
        app.stop_(None)
        wake = ak.NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
            ak.NSEventTypeApplicationDefined, ak.NSMakePoint(0, 0), 0, 0, 0, None, 0, 0, 0)
        app.postEvent_atStart_(wake, True)

    # ---- 任何執行緒 ----

    def update(self, snap: dict) -> None:
        """感測執行緒每一輪呼叫。只有字或等級變了才排到主執行緒換。"""
        title, level = title_text(snap)
        lines = menu_lines(snap)
        shown = (title, level, tuple(lines))
        if shown == self._shown:
            return
        self._shown = shown
        from PyObjCTools import AppHelper
        AppHelper.callAfter(self._apply, title, level, lines)

    def refresh(self) -> None:
        """Windows 的系統匣用來更新迷你模式的勾勾；Mac 沒有迷你模式。"""

    def notify(self, message: str, title: str) -> None:
        """第一次關掉視窗時的提醒：用系統的通知（osascript，不用另外要權限）。"""
        script = 'on run argv\ndisplay notification (item 1 of argv) with title (item 2 of argv)\nend run'
        try:
            subprocess.Popen(["/usr/bin/osascript", "-e", script, message, title],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            traceback.print_exc()

    def stop(self) -> None:
        try:
            self._ak.NSStatusBar.systemStatusBar().removeStatusItem_(self.item)
        except Exception:                             # noqa: BLE001 - 結束時的錯誤不重要
            traceback.print_exc()
