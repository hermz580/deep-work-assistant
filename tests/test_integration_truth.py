from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from deep_work_assistant.diagnostics import collect_diagnostics
from deep_work_assistant.engine import (
    ActivitySample,
    DeepWorkAssistant,
    FocusStreak,
    ReminderPlan,
    SessionSummary,
    analyze_laptop_use,
    effective_streak,
    qualifies_for_streak,
)
from deep_work_assistant.history import HistoryStore


BASE = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


def _sample(minutes: int, *, idle: int = 1) -> ActivitySample:
    return ActivitySample(BASE + timedelta(minutes=minutes), "code.exe", "Focused work", idle)


def _summary(session_id: str, human_seconds: int, *, agent_dominated: bool = False) -> SessionSummary:
    return SessionSummary(
        session_id=session_id,
        started_at=BASE,
        ended_at=BASE + timedelta(seconds=human_seconds),
        primary_app="code.exe",
        duration_seconds=human_seconds,
        focus_sample_count=2,
        average_idle_seconds=1,
        ended_reason="manual",
        human_active_seconds=human_seconds,
        agent_dominated=agent_dominated,
    )


def test_effective_streak_expires_after_a_missed_day() -> None:
    stored = FocusStreak(4, 8, "2026-08-29", 3)
    projected = effective_streak(stored, date(2026, 9, 1))
    assert projected.current_streak == 0
    assert projected.longest_streak == 8
    assert projected.daily_session_count == 0


def test_streak_requires_ten_human_minutes() -> None:
    assert qualifies_for_streak(_summary("ten", 600)) is True
    assert qualifies_for_streak(_summary("short", 599)) is False
    assert qualifies_for_streak(_summary("agent", 1200, agent_dominated=True)) is False


def test_explicit_reminder_outcomes_teach_profile() -> None:
    resisted = _summary("resisted", 1200).to_record()
    resisted["reminder_outcomes"] = [
        {"stage": "stretch", "outcome": "skipped"},
        {"stage": "stretch", "outcome": "timeout"},
    ]
    adhered = _summary("adhered", 1200).to_record()
    adhered["reminder_outcomes"] = [
        {"stage": "stretch", "outcome": "confirmed"},
    ]
    assert analyze_laptop_use([resisted]).break_response_style == "pushes-through-reminders"
    assert analyze_laptop_use([adhered]).break_response_style == "breaks-on-reminder"


def test_sample_gap_ends_old_session_without_stacked_reminders() -> None:
    assistant = DeepWorkAssistant(start_streak_required=1)
    assistant.process_sample(_sample(0))
    events = assistant.process_sample(_sample(181))
    assert [event.kind for event in events] == ["session_ended", "session_started"]
    assert events[0].data["summary"]["ended_reason"] == "sample-gap"
    assert not any(event.kind == "reminder_due" for event in events)


def test_idle_finalization_owns_streak_advancement() -> None:
    assistant = DeepWorkAssistant(
        start_streak_required=1,
        stop_streak_required=1,
        max_sample_gap_seconds=30 * 60,
    )
    assistant.process_sample(_sample(0))
    assistant.process_sample(_sample(10))
    events = assistant.process_sample(_sample(11, idle=1000))
    ended = next(event for event in events if event.kind == "session_ended")
    assert ended.data["focus_streak"]["current_streak"] == 1
    assert assistant.focus_streak.daily_session_count == 1


def test_immediate_step_away_is_inferred_as_break() -> None:
    assistant = DeepWorkAssistant(
        reminder_plan=ReminderPlan(1, 120, 180),
        start_streak_required=1,
        stop_streak_required=1,
        max_sample_gap_seconds=30 * 60,
    )
    assistant.process_sample(_sample(0))
    assistant.process_sample(_sample(1))
    events = assistant.process_sample(_sample(16, idle=1000))
    summary = next(event.data["summary"] for event in events if event.kind == "session_ended")
    hydration = next(item for item in summary["reminder_outcomes"] if item["stage"] == "hydration")
    assert hydration["outcome"] == "break"


def test_history_rejects_immediate_duplicate_session(tmp_path) -> None:
    history = HistoryStore(tmp_path / "history.jsonl")
    summary = _summary("same", 600)
    assert history.append(summary) is True
    assert history.append(summary) is False
    assert len(history.load_recent()) == 1


def test_doctor_reports_version_and_packaged_ui() -> None:
    report = collect_diagnostics()
    assert report["version"] == "0.5.0"
    assert report["checks"]["ui_assets"] is True
    assert report["vision"]["personal_posture_baseline"] is False
