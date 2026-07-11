from datetime import datetime, timedelta, timezone

from deep_work_assistant.engine import (
    ActivitySample,
    DeepWorkAssistant,
    ReminderPlan,
    SessionSummary,
    analyze_laptop_use,
)

BASE = datetime(2026, 6, 2, 9, 0, tzinfo=timezone.utc)


def summary(primary_app, minutes, outcomes=None, average_idle=25):
    outcomes = outcomes or []
    return SessionSummary(
        session_id=f'{primary_app}-{minutes}',
        started_at=BASE,
        ended_at=BASE + timedelta(minutes=minutes),
        primary_app=primary_app,
        duration_seconds=minutes * 60,
        focus_sample_count=minutes,
        average_idle_seconds=average_idle,
        ended_reason='manual',
        reminder_outcomes=[
            {'stage': stage, 'outcome': outcome}
            for stage, outcome in zip(['hydration', 'stretch', 'eat'], outcomes)
        ],
    )


def sample(minutes, process='Code.exe', title='Hermes repo - Visual Studio Code', idle=20):
    return ActivitySample(
        captured_at=BASE + timedelta(minutes=minutes),
        process_name=process,
        window_title=title,
        idle_seconds=idle,
    )


def test_analyze_laptop_use_identifies_coding_deep_flow_and_break_resistance():
    profile = analyze_laptop_use([
        summary('Code.exe', 180, ['continued', 'continued', 'continued']),
        summary('Code.exe', 160, ['continued', 'continued']),
        summary('WindowsTerminal.exe', 150, ['continued', 'ignored']),
        summary('Teams.exe', 35, ['break']),
    ])

    assert profile.dominant_category == 'coding'
    assert profile.flow_style == 'deep-flow'
    assert profile.break_response_style == 'pushes-through-reminders'
    assert profile.suggested_plan.stretch_minutes < 120
    assert 'Code.exe' in profile.top_apps


def test_reminder_message_uses_laptop_profile_context():
    profile = analyze_laptop_use([
        summary('Code.exe', 180, ['continued', 'continued', 'continued']),
        summary('Code.exe', 160, ['continued', 'continued']),
    ])
    assistant = DeepWorkAssistant(
        reminder_plan=ReminderPlan(1, 2, 3),
        laptop_use_profile=profile,
        start_streak_required=1,
        stop_streak_required=3,
    )

    assert [event.kind for event in assistant.process_sample(sample(0))] == ['session_started']
    events = assistant.process_sample(sample(2))
    stretch = [event for event in events if event.data.get('stage') == 'stretch'][0]

    # New messages use time-of-day context + activity-specific stretch suggestions
    # For coding in the morning, stretch suggestions include coding-specific exercises
    assert 'coding' in stretch.data['category']
    assert stretch.data['laptop_use_profile']['dominant_category'] == 'coding'
    # The message should include stretch suggestions for the profile category
    message = stretch.data['message']
    assert 'Try:' in message or 'Start' in message
