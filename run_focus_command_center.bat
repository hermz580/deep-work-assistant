@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap_windows.ps1" -Console
  exit /b %errorlevel%
)

".venv\Scripts\python.exe" -m deep_work_assistant.web_ui_v2 %*
if errorlevel 1 (
  echo.
  echo Focus Command Center could not start.
  echo Run: .venv\Scripts\python.exe -m deep_work_assistant doctor
  pause
)
