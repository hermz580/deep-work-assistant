param(
    [switch]$Console,
    [switch]$SetupOnly,            # install + readiness check, do not launch (used by the installer)
    [string]$Python = "",          # a known Python 3.11+ to build .venv from
    [switch]$OpenOS                # open DWA OS (/os) instead of the classic Command Center
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

function Invoke-Logged([scriptblock]$command) {
    # Windows PowerShell 5.1 turns any redirected stderr line (a pip warning)
    # into a terminating error under "Stop"; judge native tools by exit code.
    $saved = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $command *>> $logPath } finally { $ErrorActionPreference = $saved }
}

function Test-Python([string]$exe, [string[]]$prefix = @()) {
    try {
        & $exe @prefix -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    } catch { return $false }
}

function Find-Python {
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        foreach ($v in "3.13", "3.12", "3.11") {
            if (Test-Python $py.Source @("-$v")) {
                return (& $py.Source "-$v" -c "import sys; print(sys.executable)").Trim()
            }
        }
    }
    $candidates = @()
    $onPath = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($onPath) { $candidates += $onPath.Source }
    $candidates += Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA "Programs\Python\Python3*\python.exe") `
        -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | ForEach-Object FullName
    foreach ($exe in $candidates) { if (Test-Python $exe) { return $exe } }
    return $null
}

try {
    Set-Location $repoRoot
    Write-LaunchLog "bootstrap start"

    # A .venv whose Python was uninstalled or upgraded away is rebuilt, not trusted.
    if ((Test-Path $python) -and -not (Test-Python $python)) {
        Write-LaunchLog "existing .venv is broken or too old; rebuilding"
        Remove-Item -Recurse -Force $venvRoot
    }
    if (-not (Test-Path $python)) {
        $base = if ($Python -and (Test-Python $Python)) { $Python } else { Find-Python }
        if (-not $base) {
            throw "Python 3.11 or newer was not found. Run the installer (scripts\install_windows.ps1) or install Python from python.org, then try again."
        }
        Write-LaunchLog "creating .venv from $base"
        Invoke-Logged { & $base -m venv $venvRoot }
        if ($LASTEXITCODE -ne 0) { throw "Python could not create the local environment." }
        if (Test-Path $fingerprintPath) { Remove-Item $fingerprintPath }
    }

    $pyprojectHash = (Get-FileHash (Join-Path $repoRoot "pyproject.toml") -Algorithm SHA256).Hash
    # a fresh .venv may carry an old pip that cannot read pyproject-only projects
    if (-not (Test-Path $fingerprintPath)) {
        Invoke-Logged { & $python -m pip install --disable-pip-version-check --upgrade pip }
    }
    $savedHash = if (Test-Path $fingerprintPath) { (Get-Content $fingerprintPath -Raw).Trim() } else { "" }
    if ($savedHash -ne $pyprojectHash) {
        Write-LaunchLog "installing project dependencies"
        Invoke-Logged { & $python -m pip install --disable-pip-version-check -e $repoRoot }
        if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed. See $logPath" }
        # Optional camera support for the Body app. Posture sampling stays off
        # until the user consents; a failure here never blocks the core app.
        Write-LaunchLog "installing optional camera support"
        Invoke-Logged { & $python -m pip install --disable-pip-version-check -e ($repoRoot + "[vision]") }
        if ($LASTEXITCODE -ne 0) {
            Write-LaunchLog "camera support unavailable on this Python; core features still work"
        }
        Set-Content -Path $fingerprintPath -Value $pyprojectHash
    }

    Invoke-Logged { & $python -m deep_work_assistant doctor --json }
    if ($LASTEXITCODE -ne 0) { throw "Readiness check failed. See $logPath" }

    if ($SetupOnly) {
        Write-LaunchLog "setup complete"
        exit 0
    }

    $uiArgs = @("-m", "deep_work_assistant.web_ui_v2")
    if ($OpenOS) { $uiArgs += "--os" }

    if ($Console) {
        & $python @uiArgs
        exit $LASTEXITCODE
    }

    Start-Process -FilePath $pythonw -ArgumentList $uiArgs -WorkingDirectory $repoRoot -WindowStyle Hidden
    Write-LaunchLog "launch requested"
} catch {
    Write-LaunchLog "ERROR: $($_.Exception.Message)"
    if ($SetupOnly -or $Console) {
        Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "Log: $logPath"
        exit 1
    }
    $shell = New-Object -ComObject WScript.Shell
    $shell.Popup("Deep Work Assistant could not start.`n`n$($_.Exception.Message)`n`nLog: $logPath", 0, "Deep Work Assistant", 16) | Out-Null
    exit 1
}
