# ---------------------------------------------------------------
#  Creates (or updates) the desktop shortcut "HWGlance".
#  It starts run.bat minimized, so the short console flash is hidden;
#  run.bat then asks for administrator rights (UAC) by itself.
#  Icon: assets\app.ico (made by scripts\make_icon.py).
#
#  Run it from anywhere:
#    powershell -NoProfile -ExecutionPolicy Bypass -File F:\Desktop\claude_project\HWGlance\scripts\make-shortcut.ps1
#
#  ASCII only: Windows PowerShell 5.1 reads .ps1 files without a BOM
#  in the system ANSI codepage.
# ---------------------------------------------------------------
$root = Split-Path -Parent $PSScriptRoot
$desktop = [Environment]::GetFolderPath('Desktop')
$link = Join-Path $desktop 'HWGlance.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($link)
$shortcut.TargetPath = Join-Path $root 'run.bat'
$shortcut.WorkingDirectory = $root
$shortcut.IconLocation = (Join-Path $root 'assets\app.ico') + ',0'
$shortcut.WindowStyle = 7
$shortcut.Description = 'HWGlance - hardware temperatures at a glance'
$shortcut.Save()
Write-Output "Shortcut saved: $link"