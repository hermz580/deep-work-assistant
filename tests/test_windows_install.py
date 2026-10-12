"""The Windows install path: one line, no admin, Python found or installed."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL = (ROOT / "scripts" / "install_windows.ps1").read_text(encoding="utf-8")
BOOT = (ROOT / "scripts" / "bootstrap_windows.ps1").read_text(encoding="utf-8")
VBS = (ROOT / "Launch Deep Work Assistant.vbs").read_text(encoding="utf-8")


def test_scripts_are_ascii_so_windows_powershell_reads_them_right():
    # Windows PowerShell 5.1 reads BOM-less files as ANSI; ASCII is always safe.
    for text in (INSTALL, BOOT, VBS):
        text.encode("ascii")


def test_installer_downloads_this_repo_and_keeps_user_state():
    assert "hermz580/deep-work-assistant" in INSTALL
    assert "/XD .venv" in INSTALL and "/XF local_config.bat" in INSTALL
    assert "--scope user" in INSTALL and "InstallAllUsers=0" in INSTALL   # never needs admin
    assert "-SetupOnly" in INSTALL and "-Python $python" in INSTALL
    assert '"/os"' in INSTALL                                             # a DWA OS shortcut


def test_installer_never_closes_the_window_it_was_piped_into():
    # "irm | iex" runs in the user's own window: exit only from a saved script
    assert "if ($MyInvocation.MyCommand.Path) { exit 1 }" in INSTALL


def test_bootstrap_requires_311_and_rebuilds_a_broken_venv():
    for text in (INSTALL, BOOT):
        assert "sys.version_info >= (3, 11)" in text
    assert "Remove-Item -Recurse -Force $venvRoot" in BOOT
    assert "py.exe -3 -m venv" not in BOOT                                # py -3 may be too old


def test_native_tool_output_cannot_abort_setup_on_powershell_51():
    assert "*>> $logPath" not in BOOT.replace("try { & $command *>> $logPath }", "")


def test_launcher_can_open_dwa_os():
    assert "/os" in VBS and "-OpenOS" in VBS
    assert '$uiArgs += "--os"' in BOOT
