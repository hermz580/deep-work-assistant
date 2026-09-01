from pathlib import Path
import re
import threading
from urllib.request import urlopen

from deep_work_assistant.web_ui_v2 import (
    DEFAULT_SETTINGS,
    EnhancedAppState,
    EnhancedServer,
    SettingsStore,
    UI_DIR,
    build_parser,
)


def test_v2_ui_assets_exist() -> None:
    for filename in ("index.html", "app.css", "app.v2.css", "app.v2.js"):
        path = UI_DIR / filename
        assert path.exists(), f"missing UI asset: {path}"
        assert path.stat().st_size > 100


def test_v2_parser_defaults_to_localhost() -> None:
    args = build_parser().parse_args(["--no-browser"])
    assert args.host == "127.0.0.1"
    assert args.port == 8791
    assert args.no_browser is True


def test_settings_store_validates_and_persists(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    saved = store.save(
        {
            "theme": "core",
            "motion": "reduced",
            "default_focus_minutes": 999,
            "auto_refresh_seconds": 1,
            "voice": True,
        }
    )
    assert saved["theme"] == "core"
    assert saved["motion"] == "reduced"
    assert saved["default_focus_minutes"] == 240
    assert saved["auto_refresh_seconds"] == 5
    assert saved["voice"] is True
    assert store.load() == saved


def test_default_settings_are_local_and_conservative() -> None:
    assert DEFAULT_SETTINGS["auto_start_assistant"] is False
    assert DEFAULT_SETTINGS["auto_refresh_seconds"] >= 5
    assert DEFAULT_SETTINGS["stop_idle_threshold"] > DEFAULT_SETTINGS["start_idle_threshold"]
    assert DEFAULT_SETTINGS["tutorial_seen_version"] == 0
    assert DEFAULT_SETTINGS["tutorial_completed_version"] == 0


def test_every_asset_referenced_by_index_is_served() -> None:
    html = (UI_DIR / "index.html").read_text(encoding="utf-8")
    assets = set(re.findall(r'(?:src|href)="(/[^"]+\.(?:css|js))"', html))
    server = EnhancedServer(("127.0.0.1", 0), EnhancedAppState())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        for asset in assets:
            with urlopen(f"http://127.0.0.1:{port}{asset}", timeout=2) as response:
                assert response.status == 200, asset
                assert response.read(), asset
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_help_and_tutorial_copy_are_present() -> None:
    html = (UI_DIR / "index.html").read_text(encoding="utf-8")
    script = (UI_DIR / "app.v2.js").read_text(encoding="utf-8")
    assert "Questions, comments &amp; concerns" in html
    assert "Nothing is sent automatically" in html
    assert "Two controls, two jobs" in script
    assert "does <strong>not</strong> yet learn your personal posture baseline" in script


def test_windows_launchers_use_the_canonical_v2_environment() -> None:
    root = UI_DIR.parent.parent
    batch = (root / "run_focus_command_center.bat").read_text(encoding="utf-8")
    bootstrap = (root / "scripts" / "bootstrap_windows.ps1").read_text(encoding="utf-8")
    vbs = root / "Launch Deep Work Assistant.vbs"
    assert "deep_work_assistant.web_ui_v2" in batch
    assert '.venv\\Scripts\\python.exe' in batch
    assert "deep_work_assistant.web_ui_v2" in bootstrap
    assert "pythonw.exe" in bootstrap
    assert vbs.exists()
