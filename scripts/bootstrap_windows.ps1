param(
    [switch]$Console
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$venvRoot = Join-Path $repoRoot ".venv"
$python = Join-Path $venvRoot "Scripts\python.exe"
$pythonw = Join-Path $venvRoot "Scripts\pythonw.exe"
$fingerprintPath = Join-Path $venvRoot ".dwa-pyproject.sha256"
$dataRoot = Join-Path $env:LOCALAPPDATA "DeepWorkAssistant"
$logPath = Join-Path $dataRoot "bootstrap.log"

New-Item -ItemType Directory -Force -Path $dataRoot | Out-Null

function Write-LaunchLog([string]$message) {
    $timestamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ssK"
    Add-Content -Path $logPath -Value "[$timestamp] $message"
}

try {
    Set-Location $repoRoot
    Write-LaunchLog "bootstrap start"

    if (-not (Test-Path $python)) {
        $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($launcher) {
            & py.exe -3 -m venv $venvRoot
        } else {
            $fallback = Get-Command python.exe -ErrorAction SilentlyContinue
            if (-not $fallback) {
                throw "Python 3.11 or newer was not found. Install Python from python.org and try again."
            }
            & python.exe -m venv $venvRoot
        }
        if ($LASTEXITCODE -ne 0) { throw "Python could not create the local environment." }
    }

    $pyprojectHash = (Get-FileHash (Join-Path $repoRoot "pyproject.toml") -Algorithm SHA256).Hash
    $savedHash = if (Test-Path $fingerprintPath) { (Get-Content $fingerprintPath -Raw).Trim() } else { "" }
    if ($savedHash -ne $pyprojectHash) {
        Write-LaunchLog "installing project dependencies"
        & $python -m pip install --disable-pip-version-check -e $repoRoot *>> $logPath
        if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed. See $logPath" }
        Set-Content -Path $fingerprintPath -Value $pyprojectHash
    }

    & $python -m deep_work_assistant doctor --json *>> $logPath
    if ($LASTEXITCODE -ne 0) { throw "Readiness check failed. See $logPath" }

    if ($Console) {
        & $python -m deep_work_assistant.web_ui_v2
        exit $LASTEXITCODE
    }

    Start-Process -FilePath $pythonw -ArgumentList @("-m", "deep_work_assistant.web_ui_v2") -WorkingDirectory $repoRoot -WindowStyle Hidden
    Write-LaunchLog "launch requested"
} catch {
    Write-LaunchLog "ERROR: $($_.Exception.Message)"
    $shell = New-Object -ComObject WScript.Shell
    $shell.Popup("Deep Work Assistant could not start.`n`n$($_.Exception.Message)`n`nLog: $logPath", 0, "Deep Work Assistant", 16) | Out-Null
    exit 1
}
