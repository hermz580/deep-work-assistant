# Deep Work Assistant - one-line Windows installer.
#
#   irm https://raw.githubusercontent.com/hermz580/deep-work-assistant/main/scripts/install_windows.ps1 | iex
#
# Installs Python 3.12 for this user if no Python 3.11+ is found, downloads
# DWA into %LOCALAPPDATA%\DeepWorkAssistant\app, sets up its private .venv,
# runs the readiness check, adds Desktop and Start Menu shortcuts, and opens
# the Command Center. Running it again updates the app in place and keeps
# your .venv and local_config.bat. Your data lives outside the app folder
# (~\.deep_work_assistant and %LOCALAPPDATA%\DeepWorkAssistant) and is never
# touched. No admin rights are needed.
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "DeepWorkAssistant\app"),
    [string]$Branch = "main",
    [switch]$StartAtLogin,
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"          # Invoke-WebRequest is much faster without the bar
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Repo = "hermz580/deep-work-assistant"
$PythonInstaller = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"

function Step([string]$message) { Write-Host "==> $message" -ForegroundColor Cyan }

function Test-Python([string]$exe, [string[]]$prefix = @()) {
    # True when this interpreter runs and is 3.11 or newer. The Microsoft
    # Store "python.exe" stub fails here, so it is never picked.
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
    $candidates += Get-ChildItem -Path (Join-Path $env:ProgramFiles "Python3*\python.exe") `
        -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | ForEach-Object FullName
    foreach ($exe in $candidates) {
        if (Test-Python $exe) { return $exe }
    }
    return $null
}

function Install-Python {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($winget) {
        Step "Installing Python 3.12 for this user (winget)"
        & $winget.Source install -e --id Python.Python.3.12 --scope user --silent `
            --accept-package-agreements --accept-source-agreements | Out-Host
        $found = Find-Python
        if ($found) { return $found }
    }
    Step "Installing Python 3.12 for this user (python.org)"
    $setup = Join-Path $env:TEMP "dwa-python-setup.exe"
    Invoke-WebRequest -Uri $PythonInstaller -OutFile $setup -UseBasicParsing
    $proc = Start-Process -FilePath $setup -Wait -PassThru -ArgumentList @(
        "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_launcher=1", "Include_test=0")
    Remove-Item $setup -ErrorAction SilentlyContinue
    if ($proc.ExitCode -ne 0) { throw "The Python installer exited with code $($proc.ExitCode)." }
    $found = Find-Python
    if (-not $found) { throw "Python was installed but could not be found. Restart your computer and run the installer again." }
    return $found
}

function New-Shortcut([string]$path, [string]$launcher, [string]$workDir, [string]$extra = "") {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($path)
    $link.TargetPath = Join-Path $env:WINDIR "System32\wscript.exe"
    $link.Arguments = ('"' + $launcher + '" ' + $extra).Trim()
    $link.WorkingDirectory = $workDir
    $link.IconLocation = (Join-Path $env:WINDIR "System32\shell32.dll") + ",43"
    $link.Description = "Deep Work Assistant - local focus and recovery companion"
    $link.Save()
}

try {
    Write-Host ""
    Write-Host "Deep Work Assistant installer" -ForegroundColor Green
    Write-Host "Installing into $InstallDir"
    Write-Host ""

    Step "Looking for Python 3.11 or newer"
    $python = Find-Python
    if (-not $python) { $python = Install-Python }
    Write-Host "    using $python"

    Step "Downloading Deep Work Assistant ($Branch)"
    $work = Join-Path $env:TEMP ("dwa-install-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $work | Out-Null
    $zip = Join-Path $work "dwa.zip"
    Invoke-WebRequest -Uri "https://github.com/$Repo/archive/refs/heads/$Branch.zip" -OutFile $zip -UseBasicParsing
    Expand-Archive -Path $zip -DestinationPath $work -Force
    $source = Get-ChildItem -Path $work -Directory | Select-Object -First 1
    if (-not $source) { throw "The download did not contain the app." }

    Step "Copying files (your .venv and local_config.bat are kept)"
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    & robocopy.exe $source.FullName $InstallDir /E /NFL /NDL /NJH /NJS /NP /XD .venv /XF local_config.bat | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Copying the app files failed (robocopy $LASTEXITCODE)." }
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue

    Step "Setting up the app environment (first run takes a few minutes)"
    $bootstrap = Join-Path $InstallDir "scripts\bootstrap_windows.ps1"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $bootstrap -SetupOnly -Python $python
    if ($LASTEXITCODE -ne 0) {
        throw "Setup failed. See $(Join-Path $env:LOCALAPPDATA 'DeepWorkAssistant\bootstrap.log')"
    }

    Step "Adding shortcuts"
    $launcher = Join-Path $InstallDir "Launch Deep Work Assistant.vbs"
    $desktop = [Environment]::GetFolderPath("Desktop")
    $programs = Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs"
    New-Shortcut (Join-Path $desktop "Deep Work Assistant.lnk") $launcher $InstallDir
    New-Shortcut (Join-Path $programs "Deep Work Assistant.lnk") $launcher $InstallDir
    New-Shortcut (Join-Path $desktop "DWA OS.lnk") $launcher $InstallDir "/os"
    New-Shortcut (Join-Path $programs "DWA OS.lnk") $launcher $InstallDir "/os"
    $startup = Join-Path ([Environment]::GetFolderPath("Startup")) "Deep Work Assistant.lnk"
    if ($StartAtLogin) {
        New-Shortcut $startup $launcher $InstallDir
        Write-Host "    starts when you sign in"
    }

    Write-Host ""
    Write-Host "Deep Work Assistant is installed." -ForegroundColor Green
    Write-Host "  Open it any time from the Desktop or Start Menu shortcut."
    Write-Host "  Command Center: http://127.0.0.1:8791    DWA OS: http://127.0.0.1:8791/os"
    Write-Host ""
    if (-not $NoLaunch) {
        Step "Opening the Command Center"
        Start-Process -FilePath (Join-Path $env:WINDIR "System32\wscript.exe") -ArgumentList ('"' + $launcher + '"')
    }
} catch {
    Write-Host ""
    Write-Host "Install failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Fix the problem above and run the installer again; it is safe to re-run."
    # "exit" would close the window when run through "irm | iex"; only exit from a saved script.
    if ($MyInvocation.MyCommand.Path) { exit 1 }
}
