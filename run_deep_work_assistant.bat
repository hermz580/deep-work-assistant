@echo off
setlocal

cd /d %~dp0

rem ── Load personal config (gitignored) ──
if exist "startup\local_config.bat" call "startup\local_config.bat"

if exist .venv\Scripts\python.exe (
    set PY=.venv\Scripts\python.exe
) else (
    set PY=python
)

set MODE=run
if /I "%~1"=="simulate" set MODE=simulate
if /I "%~1"=="plan" set MODE=plan
if /I "%~1"=="run" set MODE=run

rem ── Obsidian vault detection ──
rem 1. Environment variable OBSIDIAN_VAULT wins if set
rem 2. Common default locations
if defined OBSIDIAN_VAULT goto :vault_set
if exist "%USERPROFILE%\OneDrive\Documents\Obsidian Vault" set "OBSIDIAN_VAULT=%USERPROFILE%\OneDrive\Documents\Obsidian Vault"
if not defined OBSIDIAN_VAULT if exist "%USERPROFILE%\Documents\Obsidian Vault" set "OBSIDIAN_VAULT=%USERPROFILE%\Documents\Obsidian Vault"
if not defined OBSIDIAN_VAULT if exist "%USERPROFILE%\Desktop\Obsidian Vault" set "OBSIDIAN_VAULT=%USERPROFILE%\Desktop\Obsidian Vault"
if not defined OBSIDIAN_VAULT set "OBSIDIAN_VAULT="
:vault_set

if /I "%MODE%"=="run" (
    if defined OBSIDIAN_VAULT (
        %PY% -m deep_work_assistant run --obsidian-vault "%OBSIDIAN_VAULT%" %~2 %~3 %~4 %~5 %~6 %~7 %~8 %~9
    ) else (
        %PY% -m deep_work_assistant run %~2 %~3 %~4 %~5 %~6 %~7 %~8 %~9
    )
) else (
    %PY% -m deep_work_assistant %MODE% %~2 %~3 %~4 %~5 %~6 %~7 %~8 %~9
)