"""HTTP API＋介面檔案。只綁 127.0.0.1；讀數都從 SensorService 快取好的快照拿，請求不會多讀硬體。

    GET  /api/snapshot          目前的快照（normalize.py 的輸出＋seq、time、source、perf、prefs）
    GET  /api/history?seconds=  最近幾秒的溫度、負載、功耗（折線圖用，10～3600 秒）
    GET  /api/sensors           LHM 的原始硬體樹（除錯、「全部感測器」用）
    GET  /api/device?id=        一個硬體的詳細資訊（規格＋所有讀數，分段、中文；sensors/details.py）
    GET  /api/events?days=      硬體相關的 Windows 事件（當機、顯示卡、硬碟…）歸成一件件事；days=0 是全部（events/）
    GET  /api/blackbox?start=&end=  黑盒子：那段時間每 2 秒的主要讀數（最多 6 小時，sensors/blackbox.py）
    GET  /api/status            程式資訊（--activate 用來確認是不是自己）
    GET  /api/settings          設定＋預設值與名稱（設定頁用）
    PUT  /api/settings          存設定（要 token），下一輪讀數就生效
    POST /api/ui/open           打開或叫出視窗：main／mini（要 token）
    GET  /api/ui/commands       視窗程序的長輪詢：等本體的指令（切換、結束），最多 timeout 秒（要 token）
    POST /api/app/quit          結束 HWGlance（要 token）

會動作的只有存設定、開視窗、結束三個，而且只動自己：存自己的設定、開自己的視窗、結束自己。
"""
from __future__ import annotations

import os
import secrets
import time
from collections.abc import Callable

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import APP_NAME, __version__, paths
from ..events.service import EventService
from ..sensors.blackbox import BlackBox, DemoBlackBox
from ..sensors.service import SensorService
from ..sensors.thresholds import DEFAULTS, LABELS, cpu_band
from ..mini.items import catalog
from ..settings import (BAND_RANGE, COLOR_DEFAULTS, COLOR_LABELS, COLOR_PRESETS, INTERVALS, MINI_OPACITY, MINI_SCALE,
                        MiniSettings, Settings, SettingsStore, mini_json)
from . import security

NO_STORE = {"Cache-Control": "no-store"}
BLACKBOX_SPAN = 6 * 3600              # 黑盒子一次最多看 6 小時（8/22 那次硬碟斷線錯了 3 小時）
NOT_BUILT = """<!doctype html><meta charset="utf-8"><title>HWGlance</title>
<p style="font-family:sans-serif;margin:2rem">介面還沒建置：在 frontend 資料夾執行
<code>npm install</code> 與 <code>npm run build</code>，或直接雙擊 run.bat。</p>"""


class OpenRequest(BaseModel):
    mode: str = "main"


class SettingsBody(BaseModel):
    unit: str
    interval: int
    closeToTray: bool
    bands: dict[str, tuple[float, float]]
    colors: dict[str, str] | None = None      # 沒帶就不改（舊版介面存檔時不會把顏色洗掉）
    mini: dict | None = None                  # 同上


def settings_payload(s: Settings, tjmax: float | None = None, mini: dict | None = None) -> dict:
    """tjmax：這台 CPU 的 TjMax（Intel 才知道）。CPU 門檻沒改過時，畫面上用的是照它算的（normalize._cpu），
    設定頁也顯示同一組，「恢復預設」也回到這一組。mini：迷你模式的狀態 {fps: 讀不到的原因或 None, hotkeys}。"""
    mini = mini or {"fps": "這個模式沒有迷你模式", "hotkeys": []}
    defaults = {**DEFAULTS, "cpu": cpu_band(tjmax)}
    bands = s.merged_bands()
    if "cpu" not in s.bands:
        bands["cpu"] = defaults["cpu"]
    labels = {**LABELS, "cpu": f"CPU（TjMax {tjmax:g}°C）"} if tjmax else LABELS
    return {
        "unit": s.unit,
        "interval": s.interval,
        "closeToTray": s.close_to_tray,
        "bands": {k: [b.warm, b.hot] for k, b in bands.items()},
        "defaults": {k: [b.warm, b.hot] for k, b in defaults.items()},
        "labels": labels,
        "intervals": list(INTERVALS),
        "range": list(BAND_RANGE),
        "colors": {**COLOR_DEFAULTS, **s.colors},
        "colorDefaults": dict(COLOR_DEFAULTS),
        "colorLabels": dict(COLOR_LABELS),
        "presets": list(COLOR_PRESETS),
        "mini": mini_json(s.mini),
        "miniDefaults": mini_json(MiniSettings()),
        "miniCatalog": catalog(),
        "miniRange": {"opacity": list(MINI_OPACITY), "scale": list(MINI_SCALE)},
        "fps": mini.get("fps"),
        "hotkeys": mini.get("hotkeys", []),
    }


def create_app(service: SensorService, token: str | None = None,
               open_ui: Callable[[str], None] | None = None,
               ui_commands: Callable[[float], list[dict]] | None = None,
               settings: SettingsStore | None = None,
               on_quit: Callable[[], None] | None = None,
               events: EventService | None = None,
               blackbox: BlackBox | DemoBlackBox | None = None,
               mini_info: Callable[[], dict] | None = None,
               allowed_hosts: frozenset[str] = security.LOCAL_HOSTS) -> FastAPI:
    app = FastAPI(title=APP_NAME, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.token = token or secrets.token_urlsafe(24)
    store = settings or SettingsStore()
    security.install(app, app.state.token, allowed_hosts)

    @app.get("/api/snapshot")
    def snapshot() -> Response:
        return Response(service.snapshot_json(), media_type="application/json", headers=NO_STORE)

    @app.get("/api/history")
    def history(seconds: int = Query(300, ge=10, le=3600)) -> JSONResponse:
        return JSONResponse(service.history.window(seconds), headers=NO_STORE)

    @app.get("/api/sensors")
    def sensors() -> JSONResponse:
        return JSONResponse(service.raw(), headers=NO_STORE)

    @app.get("/api/device")
    def device(device_id: str = Query(..., alias="id", min_length=1, max_length=200)) -> JSONResponse:
        detail = service.details(device_id)
        if detail is None:
            return JSONResponse({"detail": "找不到這個硬體"}, status_code=404)
        return JSONResponse(detail, headers=NO_STORE)

    @app.get("/api/events")
    def event_log(days: int = Query(30, ge=0, le=3650)) -> JSONResponse:
        if events is None:
            return JSONResponse({"detail": "這個模式沒有事件記錄"}, status_code=404)
        return JSONResponse(events.report(days or None), headers=NO_STORE)

    @app.get("/api/blackbox")
    def blackbox_window(start: float = Query(..., ge=0), end: float = Query(..., ge=0)) -> JSONResponse:
        if blackbox is None:
            return JSONResponse({"detail": "這個模式沒有黑盒子"}, status_code=404)
        if not (0 < end - start <= BLACKBOX_SPAN and end <= time.time() + 86400):
            return JSONResponse({"detail": "時間範圍要在 6 小時以內"}, status_code=422)
        return JSONResponse(blackbox.read(start, end, bands=service.bands), headers=NO_STORE)

    @app.get("/api/status")
    def status() -> dict:
        snap = service.snapshot()
        return {"app": APP_NAME, "version": __version__, "pid": os.getpid(), "state": snap["state"],
                "source": snap["source"], "canOpenWindow": open_ui is not None}

    def mini_state() -> dict | None:
        return mini_info() if mini_info is not None else None

    @app.get("/api/settings")
    def get_settings() -> JSONResponse:
        return JSONResponse(settings_payload(store.get(), service.cpu_tjmax(), mini_state()), headers=NO_STORE)

    @app.put("/api/settings")
    def put_settings(body: SettingsBody) -> JSONResponse:
        tjmax = service.cpu_tjmax()
        bands = {k: (float(w), float(h)) for k, (w, h) in body.bands.items()}
        if tjmax and bands.get("cpu") == (cpu_band(tjmax).warm, cpu_band(tjmax).hot):
            bands.pop("cpu")                         # 跟這台照 TjMax 算的預設一樣：當成沒改，換 CPU 也會跟著變
        changes = {"unit": body.unit, "interval": body.interval, "close_to_tray": body.closeToTray, "bands": bands}
        if body.colors is not None:
            changes["colors"] = body.colors
        if body.mini is not None:
            changes["mini"] = body.mini
        try:
            saved = store.update(**changes)
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=400)
        return JSONResponse(settings_payload(saved, tjmax, mini_state()), headers=NO_STORE)

    @app.post("/api/ui/open")
    def ui_open(body: OpenRequest) -> dict:
        if open_ui is None:
            return {"ok": False, "detail": "這個模式沒有視窗（--serve）"}
        open_ui(body.mode)
        return {"ok": True}

    @app.get("/api/ui/commands")
    def commands(request: Request, timeout: float = Query(25.0, ge=0.0, le=60.0)) -> JSONResponse:
        # GET 本來不檢查 token；這個會把指令拿走，所以也要 token（別的網頁送不出自訂標頭）
        if not secrets.compare_digest(request.headers.get(security.TOKEN_HEADER, ""), app.state.token):
            return JSONResponse({"detail": "缺少或錯誤的 token"}, status_code=403)
        return JSONResponse({"commands": ui_commands(timeout) if ui_commands else []}, headers=NO_STORE)

    @app.post("/api/app/quit")
    def app_quit() -> dict:
        if on_quit is None:
            return {"ok": False}
        on_quit()
        return {"ok": True}

    _mount_ui(app)
    return app


def _mount_ui(app: FastAPI) -> None:
    dist = paths.frontend_dist()
    app.mount("/assets", StaticFiles(directory=dist / "assets", check_dir=False), name="assets")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index() -> HTMLResponse:
        f = dist / "index.html"
        if not f.is_file():
            return HTMLResponse(NOT_BUILT, status_code=503)
        meta = f'<meta name="hwglance-token" content="{app.state.token}" />'
        html = f.read_text(encoding="utf-8").replace("</head>", f"  {meta}\n  </head>", 1)
        return HTMLResponse(html, headers=NO_STORE)
