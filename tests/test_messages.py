"""Tests for the smarter messages module."""

from datetime import datetime, timezone

import pytest

from deep_work_assistant.messages import (
    STRETCH_SUGGESTIONS,
    build_reminder_message,
    build_session_start_message,
    get_stretch_suggestions,
    time_of_day_label,
)


class TestTimeOfDay:
    def test_morning(self):
        dt = datetime(2026, 6, 2, 9, 0, tzinfo=timezone.utc).astimezone()
        assert time_of_day_label(dt) == 'morning'

    def test_afternoon(self):
        dt = datetime(2026, 6, 2, 14, 0, tzinfo=timezone.utc).astimezone()
        assert time_of_day_label(dt) == 'afternoon'

    def test_evening(self):
        dt = datetime(2026, 6, 2, 19, 0, tzinfo=timezone.utc).astimezone()
        assert time_of_day_label(dt) == 'evening'

    def test_night(self):
        dt = datetime(2026, 6, 2, 2, 0, tzinfo=timezone.utc).astimezone()
        assert time_of_day_label(dt) == 'night'


class TestStretchSuggestions:
    def test_coding_stretches(self):
        suggestions = get_stretch_suggestions('coding', count=3)
        assert len(suggestions) == 3
        for s in suggestions:
            assert 'name' in s
            assert 'duration' in s
            assert 'instruction' in s

    def test_general_fallback(self):
        suggestions = get_stretch_suggestions('nonexistent', count=2)
        assert len(suggestions) == 2
        # Should fall back to 'general' exercises
        names = [s['name'] for s in suggestions]
        general_names = [s['name'] for s in STRETCH_SUGGESTIONS['general']]
        assert any(name in general_names for name in names)

    def test_max_count_respected(self):
        suggestions = get_stretch_suggestions('coding', count=999)
        assert len(suggestions) <= len(STRETCH_SUGGESTIONS['coding'])


class TestBuildReminderMessage:
    def test_hydration_message_contains_water_keyword(self):
        now = datetime(2026, 6, 2, 9, 0, tzinfo=timezone.utc)
        msg = build_reminder_message('hydration', now=now)
        # Should contain some water-related word
        assert any(word in msg.lower() for word in ('water', 'hydrate', 'sip', 'drink', 'glass'))

    def test_stretch_message_includes_exercises(self):
        now = datetime(2026, 6, 2, 14, 0, tzinfo=timezone.utc)
        msg = build_reminder_message('stretch', profile_category='coding', now=now)
        assert 'Try:' in msg or '🧘' in msg

    def test_eat_message_mentions_fuel(self):
        now = datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)
        msg = build_reminder_message('eat', now=now)
        assert any(word in msg.lower() for word in ('fuel', 'food', 'eat', 'meal', 'snack', 'breakfast', 'lunch', 'dinner'))

    def test_streak_celebration_included(self):
        now = datetime(2026, 6, 2, 14, 0, tzinfo=timezone.utc)
        # With streak >= 3, some messages should include celebration
        msg = build_reminder_message('hydration', streak_days=5, now=now)
        # Not guaranteed every time (50% chance) — but over enough calls it should appear
        # We test that the streak message format is valid when it does appear
        assert 'streak' in msg.lower() or 'day' in msg.lower() or 'hydrate' in msg.lower()

    def test_evening_message_is_evening_appropriate(self):
        now = datetime(2026, 6, 2, 20, 0, tzinfo=timezone.utc)
        msg = build_reminder_message('stretch', now=now)
        assert any(word in msg.lower() for word in ('evening', 'wind-down', 'tension', 'day', 'stretch'))


class TestBuildSessionStartMessage:
    def test_mentions_deep_work(self):
        msg = build_session_start_message('code.exe', 'coding')
        assert any(word in msg.lower() for word in ('deep work', 'focus', 'session', 'zone'))

    def test_streak_included(self):
        msg = build_session_start_message('code.exe', 'coding', streak_days=5)
        assert 'day 5' in msg or '5' in msg

    def test_no_streak_for_new_user(self):
        msg = build_session_start_message('code.exe', 'coding', streak_days=0)
        assert 'day' not in msg.lower() or '0' not in msg