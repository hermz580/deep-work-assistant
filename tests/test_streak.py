"""Tests for focus streak tracking."""

import json
import tempfile
from datetime import date
from pathlib import Path

from deep_work_assistant.engine import (
    FocusStreak,
    advance_streak,
    load_streak,
    save_streak,
)


class TestFocusStreak:
    def test_new_streak_starts_at_zero(self):
        streak = FocusStreak()
        assert streak.current_streak == 0
        assert streak.longest_streak == 0
        assert streak.last_session_date == ''
        assert streak.daily_session_count == 0

    def test_first_session_sets_streak_to_one(self):
        streak = advance_streak(FocusStreak(), date(2026, 6, 2))
        assert streak.current_streak == 1
        assert streak.longest_streak == 1
        assert streak.last_session_date == '2026-06-02'
        assert streak.daily_session_count == 1

    def test_consecutive_days_increment_streak(self):
        streak = FocusStreak(current_streak=1, longest_streak=1, last_session_date='2026-06-01', daily_session_count=1)
        streak = advance_streak(streak, date(2026, 6, 2))
        assert streak.current_streak == 2
        assert streak.longest_streak == 2

    def test_three_day_streak(self):
        streak = FocusStreak(current_streak=2, longest_streak=2, last_session_date='2026-06-01', daily_session_count=1)
        streak = advance_streak(streak, date(2026, 6, 2))
        assert streak.current_streak == 3
        assert streak.longest_streak == 3

    def test_missed_day_breaks_streak(self):
        streak = FocusStreak(current_streak=5, longest_streak=5, last_session_date='2026-06-01', daily_session_count=1)
        streak = advance_streak(streak, date(2026, 6, 3))
        assert streak.current_streak == 1  # Reset to 1
        assert streak.longest_streak == 5  # Longest preserved

    def test_same_day_multiple_sessions_increments_count(self):
        streak = FocusStreak(current_streak=3, longest_streak=3, last_session_date='2026-06-02', daily_session_count=1)
        streak = advance_streak(streak, date(2026, 6, 2))
        assert streak.current_streak == 3  # Unchanged
        assert streak.daily_session_count == 2  # Incremented

    def test_to_record_roundtrip(self):
        streak = FocusStreak(current_streak=5, longest_streak=10, last_session_date='2026-06-02', daily_session_count=3)
        record = streak.to_record()
        restored = FocusStreak.from_record(record)
        assert restored.current_streak == 5
        assert restored.longest_streak == 10
        assert restored.last_session_date == '2026-06-02'
        assert restored.daily_session_count == 3


class TestStreakPersistence:
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'streak.json'
            streak = FocusStreak(current_streak=3, longest_streak=7, last_session_date='2026-06-02', daily_session_count=2)
            save_streak(streak, path)

            assert path.exists()
            data = json.loads(path.read_text())
            assert data['current_streak'] == 3
            assert data['longest_streak'] == 7

            loaded = load_streak(path)
            assert loaded.current_streak == 3
            assert loaded.longest_streak == 7
            assert loaded.last_session_date == '2026-06-02'

    def test_load_missing_file_returns_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'nonexistent.json'
            streak = load_streak(path)
            assert streak.current_streak == 0
            assert streak.longest_streak == 0