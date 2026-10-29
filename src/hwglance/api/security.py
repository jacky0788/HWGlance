"""兩道防線（跟 ProjectHub 同一套），避免別的網頁透過瀏覽器呼叫這個 API：

1. Host 檢查：只接受 127.0.0.1／localhost／[::1]，擋掉 DNS rebinding。
2. 會改東西的請求（POST…）要帶 X-HWGlance-Token。token 每次啟動隨機產生，只寫進我們自己送出的
   index.html 與 data/instance.json；別的網站送不出自訂標頭（瀏覽器會先問 preflight，而這裡不開 CORS）。

本體以系統管理員執行，所以這裡更要守好：API 只讀感測資料，唯一會動作的是「打開自己的視窗」。
"""
from __future__ import annotations

import secrets

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]"})
TOKEN_HEADER = "x-hwglance-token"


def host_name(header: str) -> str:
    h = header.strip().lower()
    if h.startswith("["):
        return h[: h.find("]") + 1]
    return h.split(":", 1)[0]


def install(app: FastAPI, token: str, allowed_hosts: frozenset[str]) -> None:
    @app.middleware("http")
    async def guard(request: Request, call_next):
        if host_name(request.headers.get("host", "")) not in allowed_hosts:
            return PlainTextResponse("Forbidden host", status_code=403)
        if request.method not in SAFE_METHODS and not secrets.compare_digest(
                request.headers.get(TOKEN_HEADER, ""), token):
            return JSONResponse({"detail": "缺少或錯誤的 token，請重新整理頁面"}, status_code=403)
        return await call_next(request)
