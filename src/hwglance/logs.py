"""沒有主控台（雙擊 run.bat → pythonw）時，sys.stdout／sys.stderr 是 None，uvicorn 一初始化就會當掉，
而且什麼錯誤都看不到（MusicHub 2026-10-21 踩過）。改寫到 data/logs/<名稱>.log。"""
from __future__ import annotations

import os
import sys

from . import paths

MAX_LOG = 2 * 1024 * 1024


def streams_to_log(name: str) -> None:
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        logdir = paths.log_dir()
        logdir.mkdir(parents=True, exist_ok=True)
        log = logdir / f"{name}.log"
        if log.exists() and log.stat().st_size > MAX_LOG:
            os.replace(log, logdir / f"{name}.log.1")
        stream = open(log, "a", encoding="utf-8", buffering=1)
    except OSError:
        stream = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream
