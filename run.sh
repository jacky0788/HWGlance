#!/bin/bash
# HWGlance（macOS）：run.bat 的 Mac 版。第一次會建 .venv、裝套件、建介面；已經開著就叫出原本的視窗。
# Mac 一般權限就讀得到溫度（SMC），不用 sudo、不用驅動。
#
#   ./run.sh           開（在背景跑，這個終端機可以關）
#   ./run.sh --wait    在前景跑（HWGlance.app 用：App 的程序就是本體）
#
# 想從 Launchpad、Spotlight、Dock 打開：執行一次 scripts/make-mac-app.sh，會在「應用程式」建好 HWGlance.app。
# 打不開時看 data/logs/hwglance.log（本體）、data/logs/ui.log（視窗）、data/logs/run.log（這支腳本）。
set -u
cd "$(dirname "$0")" || exit 1

# 從 Finder、Dock 開的 App 只有系統的 PATH；Homebrew（含 keg-only 的 node@22 這類）要自己補
for d in /opt/homebrew/opt/node*/bin /usr/local/opt/node*/bin /opt/homebrew/bin /usr/local/bin; do
    [ -d "$d" ] && PATH="$d:$PATH"
done
export PATH

# 執行時資料：跟程式一樣，HWGLANCE_DATA 優先（測試、示範用別的資料夾），不然是 data/
DATA="${HWGLANCE_DATA:-data}"
mkdir -p "$DATA/logs"
LOG="$DATA/logs/run.log"

note() { echo "[HWGlance] $*"; }
fail() {
    note "$*"
    echo "$(date '+%F %T') run.sh: $*" >> "$LOG"
    if [ -n "${HWGLANCE_GUI:-}" ]; then           # 從 App 開的沒有終端機，改跳對話框
        /usr/bin/osascript -e 'on run argv' -e 'display alert "HWGlance 打不開" message (item 1 of argv) as critical' \
            -e 'end run' "$*" >/dev/null 2>&1
    fi
    exit 1
}

find_python() {
    local c p
    for c in python3.14 python3.13 python3.12 python3.11 python3; do
        p=$(command -v "$c" 2>/dev/null) || continue
        "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null && { echo "$p"; return 0; }
    done
    return 1
}

if [ ! -x .venv/bin/python ]; then
    PY=$(find_python) || fail "需要 Python 3.11 以上。在終端機執行：brew install python@3.12"
    note "建立虛擬環境（$PY）…"
    "$PY" -m venv .venv || fail "建立虛擬環境失敗"
fi

if ! .venv/bin/python -c "import fastapi, uvicorn, webview, PIL, hwglance" 2>/dev/null; then
    note "安裝 Python 套件…"
    .venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt ||
        fail "安裝 Python 套件失敗，詳情在終端機輸出"
fi

if [ ! -f frontend/dist/index.html ]; then
    command -v npm >/dev/null 2>&1 || fail "需要 Node.js 20 以上。在終端機執行：brew install node"
    note "建置介面…"
    # npm ci 照 package-lock.json 裝、不改寫它
    (cd frontend && npm ci --no-audit --no-fund && npm run build) || fail "建置介面失敗（需要 Node.js 20 以上）"
fi

# 已經開著：叫出它的視窗，不開第二份
.venv/bin/python -m hwglance --activate && exit 0

if [ "${1:-}" = "--wait" ]; then
    if [ -n "${HWGLANCE_APP:-}" ] && [ -d "$HWGLANCE_APP/Contents/MacOS" ]; then
        # 從 HWGlance.app 開：本體與視窗程序都用 App 裡指向 Python 的連結執行，系統才會把它們當成 HWGlance
        # （選單列、Dock 上是 HWGlance 跟它的圖示，不是「Python」）。__PYVENV_LAUNCHER__ 是 Homebrew／python.org 的
        # Python 本來就用來帶 venv 的方式；HWGLANCE_APP_PYTHON 給 core.window_python 開視窗程序用。
        real="$(.venv/bin/python -c 'import sys; print(sys.base_prefix)')/Resources/Python.app/Contents/MacOS/Python"
        if [ -x "$real" ] && ln -sfn "$real" "$HWGLANCE_APP/Contents/MacOS/Python" 2>/dev/null; then
            export __PYVENV_LAUNCHER__="$PWD/.venv/bin/python"
            export HWGLANCE_APP_PYTHON="$HWGLANCE_APP/Contents/MacOS/Python"
            exec "$HWGLANCE_APP_PYTHON" -m hwglance >> "$DATA/logs/hwglance.log" 2>&1
        fi
    fi
    exec .venv/bin/python -m hwglance >> "$DATA/logs/hwglance.log" 2>&1
fi
nohup .venv/bin/python -m hwglance >> "$DATA/logs/hwglance.log" 2>&1 &
disown
note "已開啟（選單列上有溫度；這個終端機可以關）"
