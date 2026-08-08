from pathlib import Path

from deep_work_assistant.web_ui_v2 import (
    DEFAULT_SETTINGS,
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
