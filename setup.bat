@echo off
rem ---------------------------------------------------------------
rem  HWGlance - first-time setup (safe to run again):
rem    1. Python virtual environment and packages (.venv)
rem    2. LibreHardwareMonitor library (vendor\lhm), taken from the
rem       official release; scripts\fetch_lhm.py checks its SHA-256
rem    3. Intel PresentMon (vendor\presentmon) for game FPS, optional;
rem       scripts\fetch_presentmon.py checks its SHA-256
rem    4. The user interface (frontend\dist), only when called as
rem       "setup.bat ui"
rem
rem  ASCII only, CRLF line endings: cmd.exe reads .bat files in the
rem  system ANSI codepage, so non-ASCII bytes break commands apart.
rem ---------------------------------------------------------------
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%.venv\Scripts\python.exe"

if exist "%PY%" goto have_venv
echo [HWGlance] Creating virtual environment...
python -m venv "%ROOT%.venv"
if errorlevel 1 goto no_python
:have_venv

"%PY%" -c "import fastapi, uvicorn, pythonnet, webview, pystray, PIL, hwglance" 2>nul
if not errorlevel 1 goto have_packages
echo [HWGlance] Installing Python packages...
pushd "%ROOT%"
"%PY%" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto pip_failed
popd
:have_packages

if exist "%ROOT%vendor\lhm\LibreHardwareMonitorLib.dll" goto have_lhm
echo [HWGlance] Getting LibreHardwareMonitor (official release, 6.3 MB)...
"%PY%" "%ROOT%scripts\fetch_lhm.py"
if errorlevel 1 goto lhm_failed
:have_lhm

rem Game FPS for mini mode: Intel PresentMon (official release, 1 MB).
rem Optional: without it everything else still works.
if exist "%ROOT%vendor\presentmon\VERSION" goto have_presentmon
echo [HWGlance] Getting PresentMon for game FPS (official release, 1 MB)...
"%PY%" "%ROOT%scripts\fetch_presentmon.py"
if errorlevel 1 echo [HWGlance] Could not get PresentMon: game FPS will not be shown.
:have_presentmon

if /i not "%~1"=="ui" goto done
if exist "%ROOT%frontend\dist\index.html" goto done
echo [HWGlance] Building the user interface...
pushd "%ROOT%frontend"
call npm install
if errorlevel 1 goto npm_failed
call npm run build
if errorlevel 1 goto npm_failed
popd

:done
exit /b 0

:no_python
echo [HWGlance] Python 3.11 or newer is required.
exit /b 1

:pip_failed
popd
echo [HWGlance] Installing Python packages failed.
exit /b 1

:lhm_failed
echo [HWGlance] Could not get LibreHardwareMonitor.
exit /b 1

:npm_failed
popd
echo [HWGlance] Building the user interface failed. Node.js 20 or newer is required.
exit /b 1
