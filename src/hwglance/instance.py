"""執行中的那一份：data/instance.json 記下 pid、埠、token。

再開一次（run.bat）時先用 --activate 問它：還在就請它把視窗叫出來，不會開出第二份，也不用再跳一次 UAC。
本體以系統管理員執行、run.bat 的這一步不是，所以只用 HTTP 溝通（token 讓別的網頁送不出這個請求）。
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import time
import urllib.request

from . import APP_NAME, paths
from .api.security import TOKEN_HEADER


def write(port: int, token: str) -> None:
    f = paths.instance_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"pid": os.getpid(), "port": port, "token": token, "started": time.time()}),
                 encoding="utf-8")


def read() -> dict | None:
    try:
        data = json.loads(paths.instance_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and {"pid", "port", "token"} <= data.keys() else None


def remove() -> None:
    """只刪自己寫的那份（別的執行個體可能已經寫了新的）。"""
    info = read()
    if info is not None and info.get("pid") == os.getpid():
        try:
            paths.instance_file().unlink()
        except OSError:
            pass


def pid_exists(pid: int) -> bool:
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ctypes.get_last_error() == 5          # 拒絕存取＝程序在，只是權限比較高
    try:
        code = ctypes.c_ulong()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def activate(mode: str = "main", timeout: float = 2.0) -> bool:
    """已經有一份在跑：請它打開視窗並回傳 True。沒有（或那份已經不在了）回傳 False。"""
    info = read()
    if info is None or not pid_exists(int(info["pid"])):
        return False
    base = f"http://127.0.0.1:{int(info['port'])}"
    try:
        with urllib.request.urlopen(f"{base}/api/status", timeout=timeout) as r:
            status = json.loads(r.read())
        if status.get("app") != APP_NAME or status.get("pid") != info["pid"]:
            return False
        if sys.platform == "win32":                  # 讓本體開的視窗可以搶到前景
            ctypes.windll.user32.AllowSetForegroundWindow(-1)
        req = urllib.request.Request(f"{base}/api/ui/open", data=json.dumps({"mode": mode}).encode(),
                                     headers={"Content-Type": "application/json",
                                              TOKEN_HEADER: str(info["token"])}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return bool(json.loads(r.read()).get("ok"))
    except (OSError, ValueError):
        return False
