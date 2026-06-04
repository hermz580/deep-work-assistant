@echo off
setlocal
cd /d %~dp0
if exist .venv\Scripts\python.exe (
    set PY=.venv\Scripts\python.exe
) else (
    set PY=python
)

set MODE=run
if /I "%~1"=="simulate" set MODE=simulate
if /I "%~1"=="plan" set MODE=plan
if /I "%~1"=="run" set MODE=run

set "OBSIDIAN_VAULT=F:\HermanHarp_Offload\Desktop\Documents\Obsidian Vault"
if not exist "%OBSIDIAN_VAULT%" set "OBSIDIAN_VAULT="

if /I "%MODE%"=="run" (
    if defined OBSIDIAN_VAULT (
        %PY% -m deep_work_assistant run --obsidian-vault "%OBSIDIAN_VAULT%" %~2 %~3 %~4 %~5 %~6 %~7 %~8 %~9
    ) else (
        %PY% -m deep_work_assistant run %~2 %~3 %~4 %~5 %~6 %~7 %~8 %~9
    )
) else (
    %PY% -m deep_work_assistant %MODE% %~2 %~3 %~4 %~5 %~6 %~7 %~8 %~9
)
