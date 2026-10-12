@echo off
rem Installs (or updates) Deep Work Assistant for this Windows user.
rem Installs from this folder. To download the latest instead: irm https://raw.githubusercontent.com/hermz580/deep-work-assistant/main/scripts/install_windows.ps1 ^| iex
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_windows.ps1" -SourceDir "%~dp0." %*
echo.
pause
