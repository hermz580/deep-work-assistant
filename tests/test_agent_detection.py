"""Tests for human-vs-agent activity classification and session accounting."""

from datetime import datetime, timedelta, timezone

from deep_work_assistant.engine import (
    ActivitySample,
    DeepWorkAssistant,
    ReminderPlan,
    classify_sample,
)

BASE = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)


def sample(minutes, process='code.exe', title='Focused work', idle=30):
    return ActivitySample(
        captured_at=BASE + timedelta(minutes=minutes),
        process_name=process,
        window_title=title,
        idle_seconds=idle,
    )


class TestClassifySample:
    def test_recent_human_input_is_human_active(self):
        prev = sample(0, idle=10)
        cur = sample(1, title='Other window', idle=20)
        assert classify_sample(prev, cur) == 'human-active'

    def test_window_changes_while_idle_is_agent_active(self):
        prev = sample(0, title='hermes — terminal', idle=200)
        cur = sample(1, title='hermes — running build', idle=260)
        assert classify_sample(prev, cur) == 'agent-active'

    def test_process_change_while_idle_is_agent_active(self):
        prev = sample(0, process='code.exe', idle=300)
        cur = sample(1, process='chrome.exe', idle=360)
        assert classify_sample(prev, cur) == 'agent-active'

    def test_no_change_while_idle_is_idle(self):
        prev = sample(0, idle=300)
        cur = sample(1, idle=360)
        assert classify_sample(prev, cur) == 'idle'

    def test_idle_reset_means_human_touched_input(self):
        # Window changed but idle dropped: real human input arrived.
        prev = sample(0, title='A', idle=500)
        cur = sample(1, title='B', idle=5)
        assert classify_sample(prev, cur) == 'human-active'

    def test_no_previous_sample_idle(self):
        assert classify_sample(None, sample(0, idle=500)) == 'idle'
        assert classify_sample(None, sample(0, idle=10)) == 'human-active'

    def test_boundary_threshold(self):
        prev = sample(0, idle=100)
        assert classify_sample(prev, sample(1, title='X', idle=120)) == 'human-active'
        assert classify_sample(prev, sample(1, title='X', idle=121)) == 'agent-active'


def _assistant(**kwargs):
    return DeepWorkAssistant(
        reminder_plan=ReminderPlan(60, 120, 180),
        start_streak_required=1,
        stop_streak_required=100,
        start_idle_threshold_seconds=180,
        stop_idle_threshold_seconds=900,
        **kwargs,
    )


class TestSessionSplit:
    def test_human_and_agent_seconds_accumulate(self):
        assistant = _assistant()
        assistant.process_sample(sample(0, idle=10))  # starts session
        # 10 minutes of human activity (idle stays low)
        for m in range(1, 11):
            assistant.process_sample(sample(m, idle=10))
        # 10 minutes of agent activity: titles change, idle rises
        for m in range(11, 21):
            assistant.process_sample(sample(m, title=f'agent step {m}', idle=200 + m * 60))
        summary = assistant.finalize_session(BASE + timedelta(minutes=21))
        assert summary is not None
        assert summary.human_active_seconds == 600
        assert summary.agent_active_seconds == 600
        assert summary.agent_dominated is False

    def test_agent_dominated_session_tagged(self):
        assistant = _assistant()
        assistant.process_sample(sample(0, idle=10))
        assistant.process_sample(sample(1, idle=10))  # 1 min human
        for m in range(2, 32):  # 30 min agent
            assistant.process_sample(sample(m, title=f'agent {m}', idle=200 + m * 60))
        summary = assistant.finalize_session(BASE + timedelta(minutes=32))
        assert summary is not None
        assert summary.agent_active_seconds > summary.human_active_seconds
        assert summary.agent_dominated is True
        assert summary.ended_reason == 'agent-session'

    def test_summary_record_roundtrip_includes_split(self):
        from deep_work_assistant.engine import SessionSummary, summary_from_record

        assistant = _assistant()
        assistant.process_sample(sample(0, idle=10))
        assistant.process_sample(sample(1, idle=10))
        for m in range(2, 12):
            assistant.process_sample(sample(m, title=f'agent {m}', idle=200 + m * 60))
        summary = assistant.finalize_session(BASE + timedelta(minutes=12))
        record = summary.to_record()
        assert 'human_active_seconds' in record
        assert 'agent_active_seconds' in record
        assert 'agent_dominated' in record
        restored = summary_from_record(record)
        assert isinstance(restored, SessionSummary)
        assert restored.human_active_seconds == summary.human_active_seconds
        assert restored.agent_active_seconds == summary.agent_active_seconds
        assert restored.agent_dominated == summary.agent_dominated


class TestReminderTimersUseHumanTime:
    def test_reminder_pauses_while_agent_active(self):
        """Hydration is due after 60 min of HUMAN time, not wall-clock."""
        assistant = _assistant()
        assistant.process_sample(sample(0, idle=10))
        events = []
        # 59 minutes human active — no reminder yet
        for m in range(1, 60):
            events += assistant.process_sample(sample(m, idle=10))
        assert all(e.kind != 'reminder_due' for e in events)
        # 120 minutes of agent-driven activity — timer must stay paused
        for m in range(60, 180):
            events += assistant.process_sample(sample(m, title=f'agent {m}', idle=200 + m))
        assert all(e.kind != 'reminder_due' for e in events)
        # Human returns: one more human minute crosses the 60-min human threshold
        events = assistant.process_sample(sample(180, idle=5))
        kinds = [e.kind for e in events]
        assert 'reminder_due' in kinds

    def test_reminder_fires_on_pure_human_time(self):
        assistant = _assistant()
        assistant.process_sample(sample(0, idle=10))
        fired = []
        for m in range(1, 61):
            for event in assistant.process_sample(sample(m, idle=10)):
                if event.kind == 'reminder_due':
                    fired.append((m, event.data['stage']))
        assert fired == [(60, 'hydration')]


class TestReminderResponseWiring:
    def test_record_reminder_response_maps_to_outcome(self):
        assistant = _assistant()
        assistant.process_sample(sample(0, idle=10))
        for m in range(1, 61):
            assistant.process_sample(sample(m, idle=10))
        assert assistant.record_reminder_response('hydration', 'confirmed') is True
        summary = assistant.finalize_session(BASE + timedelta(minutes=61))
        outcomes = {r['stage']: r['outcome'] for r in summary.reminder_outcomes}
        assert outcomes['hydration'] == 'confirmed'

    def test_response_for_unsent_reminder_is_rejected(self):
        assistant = _assistant()
        assistant.process_sample(sample(0, idle=10))
        assistant.process_sample(sample(1, idle=10))
        assert assistant.record_reminder_response('eat', 'confirmed') is False

    def test_response_without_session_is_rejected(self):
        assistant = _assistant()
        assert assistant.record_reminder_response('hydration', 'confirmed') is False
