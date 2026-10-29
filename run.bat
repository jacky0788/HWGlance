@echo off
rem ---------------------------------------------------------------
rem  HWGlance - starts the monitor (desktop window).
rem  CPU, motherboard and SMART readings need administrator rights,
rem  so this asks for them (UAC). Saying no still opens the window
rem  with what a normal user can read (GPU, usage).
rem
rem  First run: setup.bat builds .venv, vendor\lhm and frontend\dist.
rem  Logs: data\logs\hwglance.log (monitor), data\logs\ui.log (window)
rem
rem  ASCII only, CRLF line endings: cmd.exe reads .bat files in the
rem  system ANSI codepage, so non-ASCII bytes break commands apart.
rem ---------------------------------------------------------------
setlocal
set "ROOT=%~dp0"
call "%ROOT%setup.bat" ui
if errorlevel 1 goto failed

rem Already running: bring its window up instead of starting a second copy.
"%ROOT%.venv\Scripts\python.exe" -m hwglance --activate
if not errorlevel 1 exit /b 0

powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Start-Process -FilePath '%ROOT%.venv\Scripts\pythonw.exe' -ArgumentList '-m','hwglance' -WorkingDirectory '%ROOT%' -Verb RunAs -ErrorAction Stop } catch { exit 1 }"
if not errorlevel 1 exit /b 0

echo [HWGlance] No administrator rights: starting with GPU and usage readings only.
start "" "%ROOT%.venv\Scripts\pythonw.exe" -m hwglance
exit /b 0

:failed
pause
exit /b 1
