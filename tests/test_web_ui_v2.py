from datetime import datetime, timedelta, timezone
from pathlib import Path

import subprocess

from deep_work_assistant.web_ui_v2 import (
    DEFAULT_SETTINGS,
    EnhancedAppState,
    SettingsStore,
    UI_DIR,
    _same_origin_mutation_allowed,
    _tail_lines,
    _vision_payload,
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
    assert DEFAULT_SETTINGS["vision_enabled"] is False


def test_vision_settings_are_clamped_and_build_explicit_opt_in_command(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    assert store.save({"vision_enabled": "true"})["vision_enabled"] is False
    settings = store.save(
        {
            "vision_enabled": True,
            "vision_sample_interval": 12,
            "vision_frame_interval": 9,
        }
    )

    assert settings["vision_sample_interval"] == 30.0
    assert settings["vision_frame_interval"] == 5.0
    command = EnhancedAppState(store).build_assistant_command(settings)
    assert command[-5:] == [
        "--vision",
        "--vision-sample-interval",
        "30.0",
        "--vision-frame-interval",
        "5.0",
    ]


def test_vision_payload_reports_latest_metrics_without_frames(tmp_path: Path) -> None:
    events = tmp_path / "vision_events.jsonl"
    events.write_text(
        "{not-json}\n"
        '{"captured_at":"2026-08-11T10:00:00+00:00","activity":"reading",'
        '"motion":2.5,"posture_alert":false,"posture":{"fwd_head_deg":12.0,'
        '"shoulder_tilt_deg":1.5,"visible":true},"frame":"forbidden"}\n'
        '{"captured_at":"2026-08-11T10:01:00+00:00","activity":"typing",'
        '"motion":8.0,"posture_alert":true,"posture":{"fwd_head_deg":31.0,'
        '"shoulder_tilt_deg":2.0,"visible":true}}\n',
        encoding="utf-8",
    )

    payload = _vision_payload(
        events,
        enabled=True,
        assistant_running=True,
        now=datetime(2026, 8, 11, 10, 2, tzinfo=timezone.utc),
    )

    assert payload["status"] == "attention"
    assert payload["sample_count"] == 2
    assert payload["latest"]["activity"] == "typing"
    assert payload["latest"]["posture"]["fwd_head_deg"] == 31.0
    assert "frame" not in repr(payload)
    assert payload["privacy"] == "Metrics only · no images stored"


def test_vision_panel_assets_are_present() -> None:
    html = (UI_DIR / "index.html").read_text(encoding="utf-8")
    js = (UI_DIR / "app.v2.js").read_text(encoding="utf-8")

    assert 'id="visionPanel"' in html
    assert 'id="settingVision"' in html
    assert "renderVision" in js
    assert "/api/vision" in js


def test_vision_payload_rejects_non_finite_metrics(tmp_path: Path) -> None:
    events = tmp_path / "vision_events.jsonl"
    events.write_text(
        '{"activity":"reading","motion":NaN,"posture":{"fwd_head_deg":Infinity,'
        '"shoulder_tilt_deg":-Infinity,"visible":true}}\n',
        encoding="utf-8",
    )

    latest = _vision_payload(events, enabled=True, assistant_running=True)["latest"]

    assert latest["motion"] is None
    assert latest["posture"]["fwd_head_deg"] is None
    assert latest["posture"]["shoulder_tilt_deg"] is None


def test_disabled_vision_redacts_historical_metrics(tmp_path: Path) -> None:
    events = tmp_path / "vision_events.jsonl"
    events.write_text('{"activity":"reading","motion":2.0}\n', encoding="utf-8")

    payload = _vision_payload(events, enabled=False, assistant_running=True)

    assert payload["status"] == "disabled"
    assert payload["latest"] is None
    assert payload["recent"] == []
    assert payload["sample_count"] == 0
    assert "path" not in payload


def test_vision_status_requires_running_assistant_and_fresh_sample(tmp_path: Path) -> None:
    events = tmp_path / "vision_events.jsonl"
    now = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    events.write_text(
        '{"captured_at":"2026-08-11T11:50:00+00:00","activity":"typing",'
        '"motion":3.0,"posture_alert":false}\n',
        encoding="utf-8",
    )

    stopped = _vision_payload(events, enabled=True, assistant_running=False, now=now)
    stale = _vision_payload(events, enabled=True, assistant_running=True, now=now)

    assert stopped["status"] == "stopped"
    assert stale["status"] == "waiting"
    assert stale["fresh"] is False


def test_tail_lines_reads_only_bounded_recent_records(tmp_path: Path) -> None:
    events = tmp_path / "vision_events.jsonl"
    events.write_text("".join(f'{{"n":{index}}}\n' for index in range(500)), encoding="utf-8")

    lines = _tail_lines(events, max_lines=200)

    assert len(lines) == 200
    assert lines[0] == '{"n":300}'
    assert lines[-1] == '{"n":499}'


def test_mutation_guard_rejects_simple_cross_origin_posts() -> None:
    assert not _same_origin_mutation_allowed(
        content_type="text/plain",
        origin="https://attacker.example",
        host="127.0.0.1:8791",
        requires_json=True,
    )
    assert not _same_origin_mutation_allowed(
        content_type="application/json",
        origin="https://attacker.example",
        host="127.0.0.1:8791",
        requires_json=True,
    )
    assert _same_origin_mutation_allowed(
        content_type="application/json; charset=utf-8",
        origin="http://127.0.0.1:8791",
        host="127.0.0.1:8791",
        requires_json=True,
    )


def test_start_assistant_uses_persisted_vision_consent_only(tmp_path: Path, monkeypatch) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    store.save({"vision_enabled": False})
    state = EnhancedAppState(store)
    state.log_path = tmp_path / "assistant.log"
    captured = {}

    class FakeProcess:
        pid = 123

        @staticmethod
        def poll():
            return None

    def fake_popen(command, **kwargs):
        captured["command"] = command
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    state.start_assistant({"vision_enabled": True})

    assert "--vision" not in captured["command"]
    state.assistant_process = None
    state._close_log()


def test_changing_vision_consent_stops_managed_assistant(tmp_path: Path, monkeypatch) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    store.save({"vision_enabled": True})
    state = EnhancedAppState(store)
    stopped = []
    state.assistant_process = object()
    monkeypatch.setattr(state, "assistant_status", lambda: {"running": True})
    monkeypatch.setattr(state, "stop_assistant", lambda: stopped.append(True) or {"running": False})

    saved = state.update_settings({"vision_enabled": False})

    assert saved["vision_enabled"] is False
    assert stopped == [True]
