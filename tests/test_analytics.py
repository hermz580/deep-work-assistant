"""Tests for the analytics and insights engine."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from deep_work_assistant.analytics import AnalyticsEngine
from deep_work_assistant.engine import SessionSummary
from deep_work_assistant.history import HistoryStore

# July 12, 2026 is a Sunday. ISO week boundaries: Monday Jul 6 → Sunday Jul 12
BASE = datetime(2026, 7, 12, 9, 0, tzinfo=timezone.utc)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _session(
    days_offset: int,
    duration_minutes: int,
    *,
    primary_app: str = "code.exe",
    focus_sample_count: int | None = None,
    average_idle: int = 20,
    ended_reason: str = "normal",
    reminder_outcomes: list[dict] | None = None,
) -> SessionSummary:
    """Build a deterministic SessionSummary at *days_offset* days from BASE."""
    start = BASE + timedelta(days=days_offset)
    end = start + timedelta(minutes=duration_minutes)
    return SessionSummary(
        session_id=f"session-d{days_offset}-{duration_minutes}",
        started_at=start,
        ended_at=end,
        primary_app=primary_app,
        duration_seconds=int(duration_minutes * 60),
        focus_sample_count=focus_sample_count or duration_minutes,
        average_idle_seconds=average_idle,
        ended_reason=ended_reason,
        reminder_outcomes=reminder_outcomes or [],
    )


def _populated_store(tmp_path: Path, sessions: list[SessionSummary]) -> HistoryStore:
    """Create a HistoryStore backed by *tmp_path* and populate it with *sessions*."""
    store = HistoryStore(tmp_path / "history.jsonl")
    for s in sessions:
        store.append(s)
    return store


def _empty_store(tmp_path: Path) -> HistoryStore:
    """Create a HistoryStore with no data."""
    return HistoryStore(tmp_path / "history.jsonl")


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestWeeklyReport:
    """Tests for AnalyticsEngine.weekly_report()."""

    def test_weekly_report_computes_total_focus_minutes(self, tmp_path):
        """Total focus minutes should be the sum of all session durations."""
        # Use days within the current ISO week (Jul 6-12, 2026)
        sessions = [
            _session(-5, 120),  # Jul 7 (Tue) — 120 min
            _session(-5, 90),   # Jul 7 (Tue) — 90 min
            _session(-4, 150),  # Jul 8 (Wed) — 150 min
            _session(-3, 60),   # Jul 9 (Thu) — 60 min
        ]
        store = _populated_store(tmp_path, sessions)
        engine = AnalyticsEngine(store)
        # Force week 28 (Jul 6-12, 2026)
        report = engine.weekly_report(year=2026, week=28)
        assert report.total_focus_minutes == 120 + 90 + 150 + 60

    def test_weekly_report_identifies_best_day(self, tmp_path):
        """Best day should be the weekday name with the most focus minutes."""
        sessions = [
            _session(-5, 120),   # Jul 7 (Tue): 120 min
            _session(-5, 90),    # Jul 7 (Tue): 90 min
            _session(-4, 300),   # Jul 8 (Wed): 300 min  ← best
            _session(-3, 45),    # Jul 9 (Thu): 45 min
        ]
        store = _populated_store(tmp_path, sessions)
        engine = AnalyticsEngine(store)
        report = engine.weekly_report(year=2026, week=28)
        assert report.best_day == "Wednesday"

    def test_weekly_report_average_session_duration(self, tmp_path):
        """Average session duration should be the mean of all durations."""
        sessions = [
            _session(-5, 100),
            _session(-5, 120),
            _session(-4, 140),
            _session(-3, 80),
        ]
        store = _populated_store(tmp_path, sessions)
        engine = AnalyticsEngine(store)
        report = engine.weekly_report(year=2026, week=28)
        expected_avg = (100 + 120 + 140 + 80) / 4
        assert report.average_session_minutes == expected_avg

    def test_weekly_report_empty_history(self, tmp_path):
        """Empty history should return safe defaults (zeros)."""
        store = _empty_store(tmp_path)
        engine = AnalyticsEngine(store)
        report = engine.weekly_report(year=2026, week=28)
        assert report.total_focus_minutes == 0
        assert report.total_sessions == 0
        assert report.average_session_minutes == 0.0


class TestProductivityScore:
    """Tests for AnalyticsEngine.productivity_score()."""

    def test_productivity_score_ranges_0_to_100(self, tmp_path):
        """Score should always be an integer between 0 and 100 inclusive."""
        sessions = [
            _session(-5, 60),
            _session(-4, 45),
            _session(-3, 90),
        ]
        store = _populated_store(tmp_path, sessions)
        engine = AnalyticsEngine(store)
        score = engine.productivity_score(days=14)
        assert 0 <= score.score <= 100

    def test_productivity_score_high_focus_time(self, tmp_path):
        """Lots of focused sessions should produce a high score (> 55)."""
        sessions = [
            _session(-5, 240),
            _session(-4, 180),
            _session(-3, 210),
            _session(-2, 200),
            _session(-1, 190),
        ]
        store = _populated_store(tmp_path, sessions)
        engine = AnalyticsEngine(store)
        score = engine.productivity_score(days=7)
        # Score is weighted: focus_time=70, consistency=71, break=50(neutral), variety=20
        # = 70*0.4 + 71*0.3 + 50*0.2 + 20*0.1 = 28 + 21.3 + 10 + 2 = 61.3
        assert score.score > 55

    def test_productivity_score_low_focus_time(self, tmp_path):
        """Very little focus time should produce a low score (< 30)."""
        sessions = [
            _session(-5, 5),
            _session(-3, 3),
            _session(-1, 2),
        ]
        store = _populated_store(tmp_path, sessions)
        engine = AnalyticsEngine(store)
        score = engine.productivity_score(days=7)
        assert score.score < 30


class TestCategoryBreakdown:
    """Tests for AnalyticsEngine.category_breakdown()."""

    def test_category_breakdown_returns_dict(self, tmp_path):
        """Breakdown should be a dict mapping category names to minutes."""
        sessions = [
            _session(-5, 60, primary_app="code.exe"),
            _session(-4, 45, primary_app="obsidian.exe"),
            _session(-3, 30, primary_app="chrome.exe"),
        ]
        store = _populated_store(tmp_path, sessions)
        engine = AnalyticsEngine(store)
        breakdown = engine.category_breakdown(days=14)
        assert isinstance(breakdown, dict)
        assert all(isinstance(v, (int, float)) for v in breakdown.values())

    def test_category_breakdown_sums_to_total(self, tmp_path):
        """The sum of all category minutes should equal total focus minutes."""
        sessions = [
            _session(-5, 120, primary_app="code.exe"),
            _session(-5, 60, primary_app="obsidian.exe"),
            _session(-4, 90, primary_app="code.exe"),
            _session(-4, 30, primary_app="chrome.exe"),
        ]
        total = sum(s.duration_seconds for s in sessions) / 60
        store = _populated_store(tmp_path, sessions)
        engine = AnalyticsEngine(store)
        breakdown = engine.category_breakdown(days=14)
        assert abs(sum(breakdown.values()) - total) < 0.01


class TestBestHours:
    """Tests for AnalyticsEngine.best_hours()."""

    def test_best_hours_returns_top_buckets(self, tmp_path):
        """Best hours should return a list of (hour, minutes) tuples sorted desc."""
        sessions = [
            _session(-5, 60),
            _session(-4, 120),
        ]
        store = _populated_store(tmp_path, sessions)
        engine = AnalyticsEngine(store)
        hours = engine.best_hours(days=14)
        assert isinstance(hours, list)
        if hours:
            hour, minutes = hours[0]
            assert isinstance(hour, int)
            assert isinstance(minutes, (int, float))
            assert 0 <= hour <= 23


class TestInsights:
    """Tests for AnalyticsEngine.generate_insights()."""

    def test_insights_returns_list_of_strings(self, tmp_path):
        """Insights should be a list of human-readable strings."""
        sessions = [
            _session(-5, 120),
            _session(-4, 90),
        ]
        store = _populated_store(tmp_path, sessions)
        engine = AnalyticsEngine(store)
        result = engine.generate_insights(days=14)
        assert isinstance(result, list)
        if result:
            assert all(isinstance(item, str) for item in result)

    def test_insights_not_empty_with_data(self, tmp_path):
        """With session data available, insights should contain at least one item."""
        sessions = [
            _session(-5, 120),
            _session(-4, 90),
            _session(-3, 150),
        ]
        store = _populated_store(tmp_path, sessions)
        engine = AnalyticsEngine(store)
        result = engine.generate_insights(days=14)
        assert len(result) > 0


class TestBreakEffectiveness:
    """Tests for AnalyticsEngine.break_effectiveness()."""

    def test_break_effectiveness_positive_value(self, tmp_path):
        """Given some break-taking sessions, effectiveness should be positive."""
        sessions = [
            _session(
                -5, 120,
                reminder_outcomes=[
                    {"stage": "hydration", "outcome": "break"},
                    {"stage": "stretch", "outcome": "break"},
                ],
            ),
            _session(
                -4, 90,
                reminder_outcomes=[
                    {"stage": "hydration", "outcome": "break"},
                ],
            ),
        ]
        store = _populated_store(tmp_path, sessions)
        engine = AnalyticsEngine(store)
        score = engine.break_effectiveness(days=14)
        assert score > 0

    def test_break_effectiveness_no_data_returns_zero(self, tmp_path):
        """No session data should result in a score of 0."""
        store = _empty_store(tmp_path)
        engine = AnalyticsEngine(store)
        score = engine.break_effectiveness(days=14)
        assert score == 0