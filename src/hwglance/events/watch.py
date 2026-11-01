"""盯著系統記錄裡顯示卡驅動的錯誤（EvtSubscribe，只收開始盯之後的新事件；唯讀）。

驅動一報錯就通知（core.py 交給 sensors/guard.py 先停讀顯示卡）：2026-10-31 那次驅動 10:10:01 報 FECS 致命錯誤，
整張卡 10:11:15 才重設，中間 74 秒來得及停手；HWGlance 是 10:11:24 讀顯示卡功耗時被拖垮的。
零星的「Error occurred on GPUID」也算：停讀 1 分鐘、重新連上，代價只是顯示卡的數字空一下。

訂閱用「拉」的：系統記錄有新的符合事件時 Windows 設一個 event，這邊每秒醒來看一次，有就用 EvtNext 拿。
盯不了（例如 wevtapi 出問題）只是少了提早停讀，讀失敗時的保險（guard）照樣有效。
"""
from __future__ import annotations

import ctypes
import sys
import threading
import traceback
from collections.abc import Callable

from .catalog import GPU_DRIVERS, classify
from .model import RawEvent
from .reader import _Api, build_query, parse_event_xml

SUBSCRIBE_FLAGS = 1            # EvtSubscribeToFutureEvents；驗證時換成 2（從最舊的開始）就能拿現有的記錄來跑
WAIT_OBJECT_0 = 0
CHANNEL = "System"
# 顯示卡驅動的錯誤與警告（None＝那個來源的錯誤與警告都要），加上 Windows 記的 TDR（驅動停止回應後恢復）
DRIVER_EVENTS: dict[str, tuple[int, ...] | None] = {**dict.fromkeys(GPU_DRIVERS), "Display": (4101,)}


def describe(ev: RawEvent) -> str:
    """一筆驅動事件 → 一句話（「FECS 致命錯誤」「TDR 重設」…），跟事件頁的寫法一樣。"""
    entry = classify(ev)
    if entry is None:
        return f"{ev.provider} {ev.event_id}"
    return entry.short or entry.text


class DriverWatch:
    def __init__(self, on_error: Callable[[str], None]) -> None:
        """on_error(一句話)：在盯的那個執行緒裡呼叫，要很快做完。"""
        self._on_error = on_error
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.seen = 0                 # 收到幾筆（驗證用）

    def start(self) -> bool:
        if sys.platform != "win32":
            return False
        self._thread = threading.Thread(target=self._run, daemon=True, name="hwglance-driver-watch")
        self._thread.start()
        return True

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        try:
            self._watch()
        except Exception:                            # noqa: BLE001 - 盯不了不影響其他功能
            traceback.print_exc()

    def _watch(self) -> None:
        from ctypes import wintypes
        api = _Api()
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateEventW.restype = wintypes.HANDLE
        kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.ResetEvent.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        wevtapi = ctypes.WinDLL("wevtapi.dll", use_last_error=True)
        subscribe = wevtapi.EvtSubscribe
        subscribe.restype = ctypes.c_void_p
        subscribe.argtypes = [ctypes.c_void_p, wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p,
                              ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD]

        signal = kernel32.CreateEventW(None, True, True, None)       # 手動重設、一開始就是有訊號（先拿一次）
        if not signal:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            handle = subscribe(None, signal, CHANNEL, build_query(CHANNEL, DRIVER_EVENTS, None), None, None, None,
                               SUBSCRIBE_FLAGS)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                while not self._stop.is_set():
                    if kernel32.WaitForSingleObject(signal, 1000) != WAIT_OBJECT_0:
                        continue
                    kernel32.ResetEvent(signal)                         # 先重設再拿，拿的時候進來的新事件會再設一次
                    self._drain(api, handle)
            finally:
                api.close(handle)
        finally:
            kernel32.CloseHandle(signal)

    def _drain(self, api: _Api, subscription: int) -> None:
        try:
            for event in api.handles(subscription, size=16):
                try:
                    ev = parse_event_xml(api.xml(event))
                finally:
                    api.close(event)
                if ev is None:
                    continue
                self.seen += 1
                try:
                    self._on_error(describe(ev))
                except Exception:                    # noqa: BLE001 - 通知的人出錯不能讓這裡停
                    traceback.print_exc()
        except OSError:
            traceback.print_exc()                    # 這一批拿不到就算了，下一秒再試
