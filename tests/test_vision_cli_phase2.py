"""Phase 2 CLI/run-loop integration tests (no physical camera required)."""
from __future__ import annotations

from datetime import datetime, timezone

import json

import pytest

from deep_work_assistant.cli import _append_vision_event, _collect_vision_events, _handle_events, build_parser
from deep_work_assistant.engine import ActivitySample, DeepWorkAssistant, EngineEvent
from deep_work_assistant.history import HistoryStore
from deep_work_assistant.vision_runtime import VisionObservation


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


def _sample(idle_seconds: int = 1) -> ActivitySample:
    return ActivitySample(NOW, "code.exe", "Focused work", idle_seconds)


def test_run_parser_requires_explicit_vision_opt_in() -> None:
    disabled = build_parser().parse_args(["run"])
    enabled = build_parser().parse_args(
        ["run", "--vision", "--vision-sample-interval", "45", "--vision-frame-interval", "0.25"]
    )

    assert disabled.vision is False
    assert enabled.vision is True
    assert enabled.vision_sample_interval == 45.0
    assert enabled.vision_frame_interval == 0.25


def test_model_provisioning_is_a_separate_explicit_command() -> None:
    args = build_parser().parse_args(["vision", "provision"])
    assert args.command == "vision"
    assert args.vision_command == "provision"


@pytest.mark.parametrize("value", ["1", "29.9", "60.1", "999", "nan", "inf", "-inf"])
def test_parser_rejects_vision_cadence_outside_privacy_contract(value) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "--vision", "--vision-sample-interval", value])


@pytest.mark.parametrize("value", ["-1", "0", "0.09", "5.01", "999", "nan", "inf", "-inf"])
def test_parser_rejects_unbounded_frame_comparison_interval(value) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "--vision", "--vision-frame-interval", value])


def test_run_loop_feeds_due_vision_observation_into_engine() -> None:
    assistant = DeepWorkAssistant(start_streak_required=1)
    sample = _sample()
    assistant.process_sample(sample)

    class FakeSampler:
        def poll(self, captured_at, *, activity_state, session_id):
            assert activity_state == "human-active"
            assert session_id is not None
            return VisionObservation(
                captured_at=captured_at,
                activity="reading",
                motion=2.0,
                posture={"fwd_head_deg": 9.0, "visible": True},
            )

    events = _collect_vision_events(
        assistant=assistant,
        sampler=FakeSampler(),
        previous_sample=None,
        sample=sample,
    )

    assert [event.kind for event in events] == ["vision_sample"]
    assert assistant.latest_vision_observation["activity"] == "reading"


def test_vision_event_log_persists_metrics_but_never_frame_payloads(tmp_path) -> None:
    path = tmp_path / "vision_events.jsonl"
    _append_vision_event(
        {
            "captured_at": NOW.isoformat(),
            "activity": "reading",
            "motion": 2.0,
            "posture": {"fwd_head_deg": 12.0, "frame": [[1, 2], [3, 4]]},
            "posture_alert": False,
            "error": None,
            "frame": "raw pixels must never persist",
        },
        path=path,
    )

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["activity"] == "reading"
    assert "frame" not in record
    assert "frame" not in record["posture"]


def test_vision_event_log_rejects_nested_payload_hidden_in_allowed_metric(tmp_path) -> None:
    path = tmp_path / "vision_events.jsonl"
    _append_vision_event(
        {
            "captured_at": NOW.isoformat(),
            "activity": "reading",
            "motion": 2.0,
            "posture": {
                "fwd_head_deg": {"frame": [[1, 2], [3, 4]]},
                "shoulder_tilt_deg": 3.0,
                "visible": True,
            },
        },
        path=path,
    )

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["posture"]["fwd_head_deg"] is None
    assert record["posture"]["shoulder_tilt_deg"] == 3.0
    assert "frame" not in path.read_text(encoding="utf-8")


def test_posture_alert_reaches_the_health_notifier(tmp_path) -> None:
    calls = []

    class FakeNotifier:
        def notify_reminder(self, stage, title, message, category):
            calls.append((stage, title, message, category))

    _handle_events(
        [
            EngineEvent(
                "posture_alert",
                {
                    "title": "Posture reset",
                    "message": "Sit back and reset your neck.",
                    "category": "health",
                    "fwd_head_deg": 31.0,
                },
            )
        ],
        FakeNotifier(),
        HistoryStore(tmp_path / "history.jsonl"),
        DeepWorkAssistant(),
        None,
    )

    assert calls == [("posture", "Posture reset", "Sit back and reset your neck.", "health")]
