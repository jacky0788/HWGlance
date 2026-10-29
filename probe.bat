@echo off
rem ---------------------------------------------------------------
rem  HWGlance - sensor probe. Lists every sensor LibreHardwareMonitor
rem  can read on this PC and writes the result to data\probe\.
rem  CPU, motherboard and SMART readings need administrator rights
rem  (and the PawnIO driver), so this asks for rights (UAC) and then
rem  runs itself again, elevated.
rem
rem  ASCII only, CRLF line endings.
rem ---------------------------------------------------------------
setlocal
set "ROOT=%~dp0"
net session >nul 2>&1
if not errorlevel 1 goto elevated

call "%ROOT%setup.bat"
if errorlevel 1 goto failed
echo [HWGlance] Asking for administrator rights...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Start-Process -FilePath '%~f0' -Verb RunAs -ErrorAction Stop } catch { exit 1 }"
if errorlevel 1 goto denied
exit /b 0

:elevated
cd /d "%ROOT%"
if exist "%ROOT%.venv\Scripts\python.exe" goto run_probe
call "%ROOT%setup.bat"
if errorlevel 1 goto failed
:run_probe
"%ROOT%.venv\Scripts\python.exe" -m hwglance.probe
echo.
pause
exit /b 0

:denied
echo [HWGlance] Administrator rights were not granted.
pause
exit /b 1

:failed
pause
exit /b 1
