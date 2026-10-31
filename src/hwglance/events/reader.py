"""讀 Windows 事件記錄（wevtapi.dll，Windows 內建，用 ctypes 呼叫）。只讀：不清除、不寫入、不改設定。

一般權限就讀得到「系統」與「應用程式」記錄（2026-10-31 在這台用一般權限試過），本體以系統管理員執行也一樣。
每筆事件轉成 XML 解析（來源、編號、時間、資料欄位）；訊息文字照事件定義組（跟 Windows 的語言，這台是中文）。
同一種事件一大串時（例如 8/22 那 6,400 筆分頁錯誤），10 分鐘內只組最新 3 筆的訊息，省時間。
"""
from __future__ import annotations

import ctypes
import re
import sys
import xml.etree.ElementTree as ET
from calendar import timegm
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import replace
from xml.sax.saxutils import escape

from .model import RawEvent

NS = "{http://schemas.microsoft.com/win/2004/08/events/event}"
QUERY_CHANNEL, QUERY_REVERSE, QUERY_TOLERATE_ERRORS = 0x1, 0x200, 0x1000
RENDER_XML = 1
FORMAT_EVENT = 1
INFINITE = 0xFFFFFFFF
ERROR_INSUFFICIENT_BUFFER = 122
ERROR_NO_MORE_ITEMS = 259
ERROR_EVT_UNRESOLVED_VALUE_INSERT = 15029
BATCH = 64
MAX_EVENTS = 100_000          # 保險：一次最多讀這麼多筆（這台 20 MB 的系統記錄總共約 4 萬筆）
MESSAGES_PER_RUN = 3
RUN_GAP = 600.0
TIME = re.compile(r"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.(\d+))?")
INVISIBLE = dict.fromkeys(map(ord, "‎‏﻿"))   # 6008 的日期裡夾著從左到右標記


def build_query(channel: str, providers: Mapping[str, Iterable[int] | None], days: float | None) -> str:
    """結構化查詢：每個來源一個 Select（條件分開寫，單一運算式才不會太長）。編號是 None＝那個來源的錯誤與警告都要。
    XPath 放在 XML 裡，<= 要寫成 &lt;=。"""
    when = f" and TimeCreated[timediff(@SystemTime) <= {int(days * 86_400_000)}]" if days else ""
    selects = []
    for provider, ids in providers.items():
        if ids is None:
            what = "(Level=1 or Level=2 or Level=3)"
        else:
            what = "(" + " or ".join(f"EventID={i}" for i in ids) + ")"
        xpath = escape(f"*[System[Provider[@Name='{provider}'] and {what}{when}]]")
        selects.append(f'<Select Path="{channel}">{xpath}</Select>')
    return f'<QueryList><Query Id="0" Path="{channel}">{"".join(selects)}</Query></QueryList>'


def parse_time(text: str | None) -> float:
    """2026-10-30T18:07:44.1540000Z → UTC epoch 秒。"""
    m = TIME.match(text or "")
    if not m:
        return 0.0
    y, mo, d, h, mi, s, frac = m.groups()
    return timegm((int(y), int(mo), int(d), int(h), int(mi), int(s))) + (float("0." + frac) if frac else 0.0)


def clean_message(text: str | None) -> str | None:
    if not text:
        return None
    lines = [line.rstrip() for line in text.translate(INVISIBLE).replace("\r\n", "\n").split("\n")]
    out = "\n".join(lines).strip()
    return out or None


def parse_event_xml(xml: str) -> RawEvent | None:
    """EvtRender 給的事件 XML → RawEvent（沒有訊息；訊息另外組）。"""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    system = root.find(f"{NS}System")
    if system is None:
        return None
    provider = system.find(f"{NS}Provider")
    created = system.find(f"{NS}TimeCreated")
    fields: dict[str, str] = {}
    args: list[str] = []
    binary = None
    data = root.find(f"{NS}EventData")
    if data is not None:
        for d in data.findall(f"{NS}Data"):
            value = (d.text or "").translate(INVISIBLE)
            args.append(value)
            if d.get("Name"):
                fields[d.get("Name")] = value
        binary = data.findtext(f"{NS}Binary") or None
    else:
        user = root.find(f"{NS}UserData")
        holder = next(iter(user), None) if user is not None else None
        for d in holder if holder is not None else ():
            name = d.tag.rsplit("}", 1)[-1]
            value = (d.text or "").translate(INVISIBLE)
            args.append(value)
            fields[name] = value
    try:
        event_id = int(system.findtext(f"{NS}EventID") or 0)
        level = int(system.findtext(f"{NS}Level") or 4)
        record = int(system.findtext(f"{NS}EventRecordID") or 0)
    except ValueError:
        return None
    return RawEvent(log=system.findtext(f"{NS}Channel") or "", provider=provider.get("Name", "") if provider is not None else "",
                    event_id=event_id, level=level, time=parse_time(created.get("SystemTime") if created is not None else None),
                    record=record, fields=fields, args=tuple(args), binary=binary)


class _Api:
    """wevtapi.dll 的函式（只用到查詢、轉 XML、組訊息、關 handle）。"""

    def __init__(self) -> None:
        from ctypes import wintypes
        dll = ctypes.WinDLL("wevtapi.dll", use_last_error=True)
        H, DW = ctypes.c_void_p, wintypes.DWORD
        self.DWORD = DW

        def fn(name, restype, *argtypes):
            f = getattr(dll, name)
            f.restype, f.argtypes = restype, list(argtypes)
            return f

        self.query = fn("EvtQuery", H, H, wintypes.LPCWSTR, wintypes.LPCWSTR, DW)
        self.next = fn("EvtNext", wintypes.BOOL, H, DW, ctypes.POINTER(H), DW, DW, ctypes.POINTER(DW))
        self.render = fn("EvtRender", wintypes.BOOL, H, H, DW, DW, ctypes.c_void_p, ctypes.POINTER(DW), ctypes.POINTER(DW))
        self.open_meta = fn("EvtOpenPublisherMetadata", H, H, wintypes.LPCWSTR, wintypes.LPCWSTR, DW, DW)
        self.format = fn("EvtFormatMessage", wintypes.BOOL, H, H, DW, DW, ctypes.c_void_p, DW, DW,
                         ctypes.c_wchar_p, ctypes.POINTER(DW))
        self.close = fn("EvtClose", wintypes.BOOL, H)
        self._xml = ctypes.create_unicode_buffer(8192)
        self._msg = ctypes.create_unicode_buffer(2048)

    def handles(self, query: int, size: int = BATCH) -> Iterator[int]:
        """一批批拿事件的 handle；拿到的人負責關。中途不拿了，這一批剩下的在這裡關掉。"""
        batch = (ctypes.c_void_p * size)()
        returned = self.DWORD()
        while self.next(query, size, batch, INFINITE, 0, ctypes.byref(returned)):
            n, i = returned.value, 0
            try:
                while i < n:
                    i += 1
                    yield batch[i - 1]
            finally:
                for j in range(i, n):
                    self.close(batch[j])
        err = ctypes.get_last_error()
        if err != ERROR_NO_MORE_ITEMS:
            raise ctypes.WinError(err)

    def xml(self, event: int) -> str:
        used, count = self.DWORD(), self.DWORD()
        if not self.render(None, event, RENDER_XML, ctypes.sizeof(self._xml), self._xml, ctypes.byref(used), ctypes.byref(count)):
            err = ctypes.get_last_error()
            if err != ERROR_INSUFFICIENT_BUFFER:
                raise ctypes.WinError(err)
            self._xml = ctypes.create_unicode_buffer(used.value // 2 + 64)
            if not self.render(None, event, RENDER_XML, ctypes.sizeof(self._xml), self._xml,
                               ctypes.byref(used), ctypes.byref(count)):
                raise ctypes.WinError(ctypes.get_last_error())
        return self._xml.value

    def message(self, meta: int, event: int) -> str | None:
        used = self.DWORD()
        ok = self.format(meta, event, 0, 0, None, FORMAT_EVENT, len(self._msg), self._msg, ctypes.byref(used))
        err = 0 if ok else ctypes.get_last_error()
        if err == ERROR_INSUFFICIENT_BUFFER:
            self._msg = ctypes.create_unicode_buffer(used.value + 64)
            ok = self.format(meta, event, 0, 0, None, FORMAT_EVENT, len(self._msg), self._msg, ctypes.byref(used))
            err = 0 if ok else ctypes.get_last_error()
        if err not in (0, ERROR_EVT_UNRESOLVED_VALUE_INSERT):   # 有插入值解不開時訊息還是組好了，照用
            return None
        return clean_message(self._msg.value)


class WindowsEventLog:
    """真的 Windows 事件記錄。read() 從新到舊讀，oldest() 給記錄裡最舊一筆的時間（「記錄從哪天起」）。"""

    kind = "windows"

    def __init__(self) -> None:
        self._api = _Api() if sys.platform == "win32" else None

    def read(self, channel: str, providers: Mapping[str, Iterable[int] | None], days: float | None) -> list[RawEvent]:
        api = self._api
        if api is None:
            return []
        query = api.query(None, None, build_query(channel, providers, days),
                          QUERY_CHANNEL | QUERY_REVERSE | QUERY_TOLERATE_ERRORS)
        if not query:
            raise ctypes.WinError(ctypes.get_last_error())
        metas: dict[str, int | None] = {}
        runs: dict[tuple[str, int], tuple[float, int]] = {}      # (來源, 編號) → (上一筆有組訊息的時間, 這一串組了幾筆)
        out: list[RawEvent] = []
        try:
            for handle in api.handles(query):
                try:
                    ev = parse_event_xml(api.xml(handle))
                    if ev is None:
                        continue
                    key = (ev.provider, ev.event_id)
                    last, count = runs.get(key, (float("inf"), 0))
                    if last - ev.time > RUN_GAP:
                        count = 0
                    if count < MESSAGES_PER_RUN:
                        if ev.provider not in metas:
                            metas[ev.provider] = api.open_meta(None, ev.provider, None, 0, 0) or None
                        meta = metas[ev.provider]
                        if meta:
                            ev = replace(ev, message=api.message(meta, handle))
                        runs[key] = (ev.time, count + 1)
                    out.append(ev)
                finally:
                    api.close(handle)
                if len(out) >= MAX_EVENTS:
                    break
        finally:
            for meta in metas.values():
                if meta:
                    api.close(meta)
            api.close(query)
        return out

    def oldest(self, channel: str) -> float | None:
        api = self._api
        if api is None:
            return None
        query = api.query(None, channel, "*", QUERY_CHANNEL)
        if not query:
            return None
        try:
            for handle in api.handles(query, size=1):
                try:
                    ev = parse_event_xml(api.xml(handle))
                    return ev.time if ev else None
                finally:
                    api.close(handle)
        except OSError:
            return None
        finally:
            api.close(query)
        return None
