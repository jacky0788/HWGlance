"""HTTP API＋介面檔案。只綁 127.0.0.1；讀數都從 SensorService 快取好的快照拿，請求不會多讀硬體。

    GET  /api/snapshot          目前的快照（normalize.py 的輸出＋seq、time、source、perf、prefs）
    GET  /api/history?seconds=  最近幾秒的溫度、負載、功耗（折線圖用，10～3600 秒）
    GET  /api/sensors           LHM 的原始硬體樹（除錯、「全部感測器」用）
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
from collections.abc import Callable

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import APP_NAME, __version__, paths
from ..sensors.service import SensorService
from ..sensors.thresholds import DEFAULTS, LABELS
from ..settings import BAND_RANGE, INTERVALS, Settings, SettingsStore
from . import security

NO_STORE = {"Cache-Control": "no-store"}
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


def settings_payload(s: Settings) -> dict:
    return {
        "unit": s.unit,
        "interval": s.interval,
        "closeToTray": s.close_to_tray,
        "bands": {k: [b.warm, b.hot] for k, b in s.merged_bands().items()},
        "defaults": {k: [b.warm, b.hot] for k, b in DEFAULTS.items()},
        "labels": LABELS,
        "intervals": list(INTERVALS),
        "range": list(BAND_RANGE),
    }


def create_app(service: SensorService, token: str | None = None,
               open_ui: Callable[[str], None] | None = None,
               ui_commands: Callable[[float], list[dict]] | None = None,
               settings: SettingsStore | None = None,
               on_quit: Callable[[], None] | None = None,
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

    @app.get("/api/status")
    def status() -> dict:
        snap = service.snapshot()
        return {"app": APP_NAME, "version": __version__, "pid": os.getpid(), "state": snap["state"],
                "source": snap["source"], "canOpenWindow": open_ui is not None}

    @app.get("/api/settings")
    def get_settings() -> JSONResponse:
        return JSONResponse(settings_payload(store.get()), headers=NO_STORE)

    @app.put("/api/settings")
    def put_settings(body: SettingsBody) -> JSONResponse:
        try:
            saved = store.update(unit=body.unit, interval=body.interval, close_to_tray=body.closeToTray,
                                 bands={k: (float(w), float(h)) for k, (w, h) in body.bands.items()})
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=400)
        return JSONResponse(settings_payload(saved), headers=NO_STORE)

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
