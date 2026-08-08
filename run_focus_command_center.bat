@echo off
setlocal
cd /d "%~dp0"
python -m deep_work_assistant.web_ui
if errorlevel 1 (
  echo.
  echo Focus Command Center could not start.
  echo Confirm Python 3.11+ is installed and run: pip install -e .
  pause
)
