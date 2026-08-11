from datetime import datetime, timedelta, timezone

from deep_work_assistant.engine import (
    ActivitySample,
    DeepWorkAssistant,
    ReminderPlan,
    SessionSummary,
    build_adaptive_plan,
)

BASE = datetime(2026, 6, 2, 9, 0, tzinfo=timezone.utc)


def sample(minutes, process='code.exe', idle=30):
    return ActivitySample(
        captured_at=BASE + timedelta(minutes=minutes),
        process_name=process,
        window_title='Focused work',
        idle_seconds=idle,
    )


def completed_session(duration_minutes, outcomes=None):
    outcomes = outcomes or []
    reminders = []
    for idx, outcome in enumerate(outcomes, start=1):
        reminders.append(
            {
                'stage': ['hydration', 'stretch', 'eat'][idx - 1],
                'sent_at': BASE + timedelta(minutes=[60, 120, 180][idx - 1]),
                'outcome': outcome,
            }
        )
    return SessionSummary(
        session_id=f'session-{duration_minutes}',
        started_at=BASE,
        ended_at=BASE + timedelta(minutes=duration_minutes),
        primary_app='code.exe',
        duration_seconds=int(duration_minutes * 60),
        focus_sample_count=duration_minutes,
        average_idle_seconds=20,
        ended_reason='normal',
        reminder_outcomes=reminders,
    )


def test_session_starts_after_two_stable_samples():
    assistant = DeepWorkAssistant(
        reminder_plan=ReminderPlan(60, 120, 180),
        start_streak_required=2,
        stop_streak_required=2,
        start_idle_threshold_seconds=180,
        stop_idle_threshold_seconds=900,
    )

    assert assistant.process_sample(sample(0)) == []
    events = assistant.process_sample(sample(0.25))

    kinds = [event.kind for event in events]
    assert 'session_started' in kinds
    assert assistant.current_session is not None
    assert assistant.current_session.started_at == BASE + timedelta(minutes=0.25)


def test_reminders_fire_once_at_each_stage():
    assistant = DeepWorkAssistant(
        reminder_plan=ReminderPlan(60, 120, 180),
        start_streak_required=1,
        stop_streak_required=3,
        start_idle_threshold_seconds=180,
        stop_idle_threshold_seconds=900,
    )

    assert [event.kind for event in assistant.process_sample(sample(0))] == ['session_started']
    assert [event.kind for event in assistant.process_sample(sample(59))] == []

    reminder_events = assistant.process_sample(sample(60))
    assert [event.kind for event in reminder_events] == ['reminder_due']
    assert reminder_events[0].data['stage'] == 'hydration'

    assert assistant.process_sample(sample(61)) == []
    assert [event.kind for event in assistant.process_sample(sample(120))] == ['reminder_due']
    assert [event.kind for event in assistant.process_sample(sample(180))] == ['reminder_due']


def test_adaptive_plan_extends_when_focus_sessions_run_long():
    history = [
        completed_session(150, ['continued', 'continued', 'continued']),
        completed_session(165, ['continued', 'continued', 'continued']),
        completed_session(140, ['continued', 'continued']),
    ]

    plan = build_adaptive_plan(history)
    assert plan.hydration_minutes > 60
    assert plan.stretch_minutes > 120
    assert plan.eat_minutes > 180


def test_adaptive_plan_shortens_when_sessions_end_early():
    history = [
        completed_session(48, ['break']),
        completed_session(52, ['break']),
        completed_session(55, ['break']),
    ]

    plan = build_adaptive_plan(history)
    assert plan.hydration_minutes < 60
    assert plan.stretch_minutes <= 120
    assert plan.eat_minutes <= 180


def test_vision_observation_enters_health_engine_and_enriches_reminders():
    assistant = DeepWorkAssistant(
        reminder_plan=ReminderPlan(1, 120, 180),
        start_streak_required=1,
        stop_streak_required=3,
    )
    assert [event.kind for event in assistant.process_sample(sample(0))] == ['session_started']

    vision_events = assistant.record_vision_observation(
        {
            'captured_at': BASE.isoformat(),
            'activity': 'reading',
            'motion': 2.0,
            'posture': {'fwd_head_deg': 12.0, 'visible': True},
            'posture_alert': False,
            'error': None,
        }
    )

    assert [event.kind for event in vision_events] == ['vision_sample']
    reminders = assistant.process_sample(sample(1))
    assert [event.kind for event in reminders] == ['reminder_due']
    assert reminders[0].data['vision_activity'] == 'reading'
    assert reminders[0].data['vision_posture']['fwd_head_deg'] == 12.0


def test_sustained_posture_observation_emits_health_alert():
    assistant = DeepWorkAssistant(start_streak_required=1)
    events = assistant.record_vision_observation(
        {
            'captured_at': BASE.isoformat(),
            'activity': 'reading',
            'motion': 1.0,
            'posture': {'fwd_head_deg': 31.0, 'visible': True},
            'posture_alert': True,
            'error': None,
        }
    )

    assert [event.kind for event in events] == ['vision_sample', 'posture_alert']


def test_vision_observation_is_cleared_when_session_ends():
    assistant = DeepWorkAssistant(
        start_streak_required=1,
        stop_streak_required=1,
        stop_idle_threshold_seconds=900,
    )
    assistant.process_sample(sample(0))
    assistant.record_vision_observation(
        {'activity': 'reading', 'posture': {'fwd_head_deg': 12.0}}
    )

    events = assistant.process_sample(sample(1, idle=901))

    assert [event.kind for event in events] == ['session_ended']
    assert assistant.latest_vision_observation is None


def test_new_session_does_not_inherit_stale_vision_observation():
    assistant = DeepWorkAssistant(start_streak_required=1)
    assistant.latest_vision_observation = {
        'activity': 'reading',
        'posture': {'fwd_head_deg': 12.0},
    }

    events = assistant.process_sample(sample(0))

    assert [event.kind for event in events] == ['session_started']
    assert assistant.latest_vision_observation is None


def test_health_engine_rejects_nested_payload_hidden_in_metric_field():
    assistant = DeepWorkAssistant(start_streak_required=1)

    events = assistant.record_vision_observation(
        {
            'activity': 'reading',
            'motion': 2.0,
            'posture': {
                'fwd_head_deg': {'frame': [[1, 2], [3, 4]]},
                'visible': True,
            },
        }
    )

    assert [event.kind for event in events] == ['vision_sample']
    assert assistant.latest_vision_observation['posture']['fwd_head_deg'] is None
    assert 'frame' not in repr(assistant.latest_vision_observation)
