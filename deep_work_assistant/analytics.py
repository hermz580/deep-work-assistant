"""Analytics module — weekly reports, productivity scoring, trends, and insights.

Provides data models, an AnalyticsEngine for computing metrics from session
history and pomodoro data, an AnalyticsStore for caching daily aggregates in
SQLite, and display helpers for terminal output.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any

from .engine import SessionSummary, _categorize_app, load_streak
from .history import HistoryStore
from .pomodoro import load_history


# ── Constants ─────────────────────────────────────────────────────────────────

ANALYTICS_DB_PATH = Path.home() / '.deep_work_assistant' / 'analytics.db'
STREAK_FILE_PATH = Path.home() / '.deep_work_assistant' / 'deep_work_streak.json'
FOCUS_TARGET_MINUTES = 240  # 4 hours per day = 100% focus_time score
BREAK_REMINDER_STAGES = {'hydration', 'stretch', 'eat'}


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class WeeklyReport:
    year: int
    week: int
    start_date: str          # ISO date
    end_date: str            # ISO date
    total_focus_minutes: int
    total_sessions: int
    best_day: str            # Day name (Monday, Tuesday, etc.)
    best_hour: str           # e.g. "09:00-10:00"
    dominant_category: str
    average_session_minutes: float
    current_streak: int
    longest_streak: int
    break_effectiveness: float  # 0.0-1.0
    productivity_score: int     # 0-100
    top_apps: list[str] = field(default_factory=list)
    pomodoro_count: int = 0


@dataclass
class ProductivityScore:
    score: int                      # 0-100
    components: dict[str, float]    # focus_time, consistency, break_adherence, variety
    recommendations: list[str] = field(default_factory=list)


@dataclass
class DailyAggregate:
    date: str
    focus_minutes: int
    session_count: int
    avg_session_minutes: float
    primary_category: str
    pomodoro_count: int
    break_count: int
    break_taken_count: int


# ── Analytics SQLite store ────────────────────────────────────────────────────

class AnalyticsStore:
    """SQLite-backed cache for daily aggregates and generated insights."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path or ANALYTICS_DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS daily_aggregates (
                date TEXT PRIMARY KEY,
                focus_minutes INTEGER NOT NULL DEFAULT 0,
                session_count INTEGER NOT NULL DEFAULT 0,
                avg_session_minutes REAL NOT NULL DEFAULT 0.0,
                primary_category TEXT NOT NULL DEFAULT '',
                pomodoro_count INTEGER NOT NULL DEFAULT 0,
                break_count INTEGER NOT NULL DEFAULT 0,
                break_taken_count INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS insights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                generated_at TEXT NOT NULL,
                insight_type TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL
            );
        """)
        self._conn.commit()

    def save_daily(self, agg: DailyAggregate) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO daily_aggregates
               (date, focus_minutes, session_count, avg_session_minutes,
                primary_category, pomodoro_count, break_count, break_taken_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                agg.date, agg.focus_minutes, agg.session_count,
                agg.avg_session_minutes, agg.primary_category,
                agg.pomodoro_count, agg.break_count, agg.break_taken_count,
            ),
        )
        self._conn.commit()

    def load_daily_range(self, start: str, end: str) -> list[DailyAggregate]:
        rows = self._conn.execute(
            """SELECT * FROM daily_aggregates
               WHERE date >= ? AND date <= ?
               ORDER BY date ASC""",
            (start, end),
        ).fetchall()
        results: list[DailyAggregate] = []
        for row in rows:
            results.append(
                DailyAggregate(
                    date=str(row['date']),
                    focus_minutes=int(row['focus_minutes']),
                    session_count=int(row['session_count']),
                    avg_session_minutes=float(row['avg_session_minutes']),
                    primary_category=str(row['primary_category']),
                    pomodoro_count=int(row['pomodoro_count']),
                    break_count=int(row['break_count']),
                    break_taken_count=int(row['break_taken_count']),
                )
            )
        return results

    def save_insight(self, insight_type: str, title: str, body: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """INSERT INTO insights (generated_at, insight_type, title, body)
               VALUES (?, ?, ?, ?)""",
            (now, insight_type, title, body),
        )
        self._conn.commit()

    def load_insights(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT * FROM insights ORDER BY generated_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        self._conn.close()


# ── Analytics engine ──────────────────────────────────────────────────────────

class AnalyticsEngine:
    """Computes productivity metrics, weekly reports, trends, and insights."""

    def __init__(self, history_store: HistoryStore | None = None) -> None:
        self.history_store = history_store or HistoryStore.default()
        self._analytics_store: AnalyticsStore | None = None

    @property
    def analytics_store(self) -> AnalyticsStore:
        if self._analytics_store is None:
            self._analytics_store = AnalyticsStore()
        return self._analytics_store

    # ── Weekly report ───────────────────────────────────────────────────────

    def weekly_report(self, year: int | None = None, week: int | None = None) -> WeeklyReport:
        """Compute a full weekly report, defaulting to the current ISO week."""
        today = date.today()
        if year is None:
            year = today.isocalendar()[0]
        if week is None:
            week = today.isocalendar()[1]

        # ISO week boundaries (Monday = 1)
        start_date = date.fromisocalendar(year, week, 1)
        end_date = start_date + timedelta(days=6)

        sessions = self._load_sessions_for_range(start_date, end_date)
        pomodoros = self._load_pomodoros_for_range(start_date, end_date)

        if not sessions:
            streak = load_streak()
            return WeeklyReport(
                year=year, week=week,
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
                total_focus_minutes=0,
                total_sessions=0,
                best_day='N/A',
                best_hour='N/A',
                dominant_category='general',
                average_session_minutes=0.0,
                current_streak=streak.current_streak,
                longest_streak=streak.longest_streak,
                break_effectiveness=0.0,
                productivity_score=0,
                top_apps=[],
                pomodoro_count=len(pomodoros),
            )

        # Daily aggregates
        daily = self._compute_daily_aggregates(sessions, pomodoros)

        # Total focus minutes
        total_focus_minutes = sum(d.focus_minutes for d in daily)

        # Average session duration
        durations = [
            s.duration_seconds / 60.0
            for s in sessions
            if s.duration_seconds > 0
        ]
        avg_session_minutes = round(mean(durations), 1) if durations else 0.0

        # Best day — the day of the week with the most focus minutes
        day_focus: dict[str, int] = defaultdict(int)
        for d in daily:
            try:
                day_name = date.fromisoformat(d.date).strftime('%A')
            except (ValueError, TypeError):
                day_name = 'Unknown'
            day_focus[day_name] += d.focus_minutes
        best_day = max(day_focus, key=lambda k: day_focus[k]) if day_focus else 'N/A'

        # Best hour bucket
        hour_minutes: Counter[int] = Counter()
        for s in sessions:
            try:
                hour_start = s.started_at.astimezone().hour
                hour_minutes[hour_start] += s.duration_seconds // 60
            except (ValueError, TypeError):
                pass
        best_hour_num = hour_minutes.most_common(1)[0][0] if hour_minutes else 0
        best_hour = f'{best_hour_num:02d}:00-{best_hour_num + 1:02d}:00'

        # Dominant category
        category_minutes: Counter[str] = Counter()
        app_counts: Counter[str] = Counter()
        for s in sessions:
            cat = _categorize_app(s.primary_app)
            mins = s.duration_seconds // 60
            category_minutes[cat] += mins
            app_counts[s.primary_app] += 1
        dominant = category_minutes.most_common(1)[0][0] if category_minutes else 'general'

        # Top apps
        top_apps = [app for app, _ in app_counts.most_common(5)]

        # Break effectiveness
        break_reminder_count = 0
        break_taken_count = 0
        for s in sessions:
            for ro in s.reminder_outcomes:
                outcome = str(ro.get('outcome', '')).lower()
                stage = str(ro.get('stage', '')).lower()
                if stage in BREAK_REMINDER_STAGES and outcome != 'not_sent':
                    break_reminder_count += 1
                    if outcome == 'break':
                        break_taken_count += 1
        break_effectiveness = (
            round(break_taken_count / break_reminder_count, 2)
            if break_reminder_count > 0
            else 0.0
        )

        # Streak
        streak = load_streak()
        current_streak = streak.current_streak
        longest_streak = streak.longest_streak

        # Productivity score across the week
        pscore = self.productivity_score(days=7)

        return WeeklyReport(
            year=year, week=week,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            total_focus_minutes=round(total_focus_minutes),
            total_sessions=len(sessions),
            best_day=best_day,
            best_hour=best_hour,
            dominant_category=dominant,
            average_session_minutes=avg_session_minutes,
            current_streak=current_streak,
            longest_streak=longest_streak,
            break_effectiveness=break_effectiveness,
            productivity_score=pscore.score,
            top_apps=top_apps,
            pomodoro_count=len(pomodoros),
        )

    # ── Productivity score ──────────────────────────────────────────────────

    def productivity_score(self, days: int = 30) -> ProductivityScore:
        """Compute a 0-100 productivity score from four weighted components.

        Components:
          - focus_time (40%): avg daily focus minutes, 4h/day = 100
          - consistency (30%): fraction of days with ≥1 session
          - break_adherence (20%): fraction of break reminders honoured
          - variety (10%): how many distinct app categories were used
        """
        today = date.today()
        start = today - timedelta(days=days - 1)
        sessions = self._load_sessions_for_range(start, today)

        if not sessions:
            return ProductivityScore(
                score=0,
                components={
                    'focus_time': 0.0,
                    'consistency': 0.0,
                    'break_adherence': 0.0,
                    'variety': 0.0,
                },
                recommendations=['Complete at least one focus session to start tracking.'],
            )

        pomodoros = self._load_pomodoros_for_range(start, today)
        daily = self._compute_daily_aggregates(sessions, pomodoros)

        # ── focus_time ────────────────────────────────────────────────────
        avg_daily = (
            sum(d.focus_minutes for d in daily) / len(daily)
            if daily
            else 0.0
        )
        focus_time_score = min(100.0, round((avg_daily / FOCUS_TARGET_MINUTES) * 100, 1))

        # ── consistency ──────────────────────────────────────────────────
        # Count how many days in the range had at least one session
        session_dates = {s.started_at.astimezone().date().isoformat() for s in sessions}
        total_days_in_range = max(1, (today - start).days + 1)
        days_with_sessions = len(session_dates)
        consistency_score = round((days_with_sessions / total_days_in_range) * 100, 1)

        # ── break_adherence ─────────────────────────────────────────────
        total_reminders = 0
        total_breaks_taken = 0
        for s in sessions:
            for ro in s.reminder_outcomes:
                outcome = str(ro.get('outcome', '')).lower()
                stage = str(ro.get('stage', '')).lower()
                if stage in BREAK_REMINDER_STAGES and outcome != 'not_sent':
                    total_reminders += 1
                    if outcome == 'break':
                        total_breaks_taken += 1
        break_adherence_score = (
            round((total_breaks_taken / total_reminders) * 100, 1)
            if total_reminders > 0
            else 50.0  # neutral when no data
        )

        # ── variety ─────────────────────────────────────────────────────
        categories_used: set[str] = set()
        for s in sessions:
            categories_used.add(_categorize_app(s.primary_app))
        variety_score = round(min(100.0, len(categories_used) * 20.0), 1)

        # Weighted average
        score = round(
            focus_time_score * 0.40
            + consistency_score * 0.30
            + break_adherence_score * 0.20
            + variety_score * 0.10
        )
        score = max(0, min(100, score))

        components = {
            'focus_time': focus_time_score,
            'consistency': consistency_score,
            'break_adherence': break_adherence_score,
            'variety': variety_score,
        }

        # Auto-generate recommendations from lowest components
        recommendations = self._generate_recommendations(components)

        return ProductivityScore(
            score=score,
            components=components,
            recommendations=recommendations,
        )

    @staticmethod
    def _generate_recommendations(components: dict[str, float]) -> list[str]:
        """Create actionable suggestions based on the weakest components."""
        recs: list[str] = []

        if components.get('focus_time', 100) < 50:
            recs.append(
                'Your average daily focus is below 2 hours. Try blocking '
                'a dedicated 90-minute deep work window each morning.'
            )
        elif components.get('focus_time', 100) < 75:
            recs.append(
                'You are building good focus momentum. Aim to add one more '
                '30-minute session to your best day each week.'
            )

        if components.get('consistency', 100) < 50:
            recs.append(
                'You have sessions on fewer than half of days. Even a short '
                '25-minute pomodoro helps maintain the streak.'
            )
        elif components.get('consistency', 100) < 75:
            recs.append(
                'You are fairly consistent. Try to make every day a focus '
                'day — even 15 minutes counts.'
            )

        if components.get('break_adherence', 100) < 50:
            recs.append(
                'You tend to skip or ignore break reminders. Taking a real '
                'stretch or hydration break can boost your next session by '
                'restoring energy.'
            )

        if components.get('variety', 100) < 40:
            recs.append(
                'Your app usage is narrowly focused on one category. '
                'Consider mixing in different types of deep work to build '
                'broader skills.'
            )

        if not recs:
            recs.append(
                'You are in a strong groove. Challenge yourself with a '
                'longer session or a new deep work category.'
            )

        return recs

    # ── Focus trend ────────────────────────────────────────────────────────

    def focus_trend(self, days: int = 30) -> list[dict[str, Any]]:
        """Return daily focus minutes for charting (newest last)."""
        today = date.today()
        start = today - timedelta(days=days - 1)
        sessions = self._load_sessions_for_range(start, today)
        pomodoros = self._load_pomodoros_for_range(start, today)
        daily = self._compute_daily_aggregates(sessions, pomodoros)

        # Build a complete date range so missing days appear as 0
        by_date: dict[str, int] = {d.date: d.focus_minutes for d in daily}
        trend: list[dict[str, Any]] = []
        cursor = start
        while cursor <= today:
            iso = cursor.isoformat()
            trend.append({'date': iso, 'focus_minutes': by_date.get(iso, 0)})
            cursor += timedelta(days=1)
        return trend

    # ── Best hours ─────────────────────────────────────────────────────────

    def best_hours(self, days: int = 30) -> list[tuple[int, int]]:
        """Return top 3 hour buckets (hour, total_minutes) sorted desc."""
        today = date.today()
        start = today - timedelta(days=days - 1)
        sessions = self._load_sessions_for_range(start, today)

        hour_minutes: Counter[int] = Counter()
        for s in sessions:
            try:
                hour_start = s.started_at.astimezone().hour
                hour_minutes[hour_start] += s.duration_seconds // 60
            except (ValueError, TypeError):
                pass

        return hour_minutes.most_common(3)

    # ── Category breakdown ─────────────────────────────────────────────────

    def category_breakdown(self, days: int = 30) -> dict[str, int]:
        """Map category -> total focus minutes for the period."""
        today = date.today()
        start = today - timedelta(days=days - 1)
        sessions = self._load_sessions_for_range(start, today)

        category_minutes: Counter[str] = Counter()
        for s in sessions:
            cat = _categorize_app(s.primary_app)
            category_minutes[cat] += s.duration_seconds // 60

        return dict(category_minutes.most_common())

    # ── Break effectiveness ────────────────────────────────────────────────

    def break_effectiveness(self, days: int = 30) -> float:
        """Ratio of sessions where the user took a break after a reminder."""
        today = date.today()
        start = today - timedelta(days=days - 1)
        sessions = self._load_sessions_for_range(start, today)

        total_reminders = 0
        total_breaks = 0
        for s in sessions:
            for ro in s.reminder_outcomes:
                outcome = str(ro.get('outcome', '')).lower()
                stage = str(ro.get('stage', '')).lower()
                if stage in BREAK_REMINDER_STAGES and outcome != 'not_sent':
                    total_reminders += 1
                    if outcome == 'break':
                        total_breaks += 1

        if total_reminders == 0:
            return 0.0
        return round(total_breaks / total_reminders, 2)

    # ── Generate insights ──────────────────────────────────────────────────

    def generate_insights(self, days: int = 30) -> list[str]:
        """Analyse data and return 3-5 natural language insights."""
        today = date.today()
        start = today - timedelta(days=days - 1)
        sessions = self._load_sessions_for_range(start, today)
        pomodoros = self._load_pomodoros_for_range(start, today)
        daily = self._compute_daily_aggregates(sessions, pomodoros)

        insights: list[str] = []

        if not sessions:
            insights.append('No focus session data available for this period.')
            return insights

        # 1. Best time-of-day window
        hour_minutes: Counter[int] = Counter()
        for s in sessions:
            try:
                h = s.started_at.astimezone().hour
                hour_minutes[h] += s.duration_seconds // 60
            except (ValueError, TypeError):
                pass
        if hour_minutes:
            peak_hour = hour_minutes.most_common(1)[0][0]
            if peak_hour < 12:
                insights.append(
                    f'You focus best in the morning ({peak_hour}-{peak_hour + 1})'
                    f' — protect that window for deep work.'
                )
            elif peak_hour < 17:
                insights.append(
                    f'Your peak focus time is in the afternoon '
                    f'({peak_hour}-{peak_hour + 1}) — schedule important tasks then.'
                )
            else:
                insights.append(
                    f'You are most productive in the evening '
                    f'({peak_hour}-{peak_hour + 1}) — use this time for creative work.'
                )

        # 2. Best day of the week
        if daily:
            day_focus: dict[str, int] = defaultdict(int)
            day_counts: dict[str, int] = defaultdict(int)
            for d in daily:
                try:
                    day_name = date.fromisoformat(d.date).strftime('%A')
                except (ValueError, TypeError):
                    day_name = 'Unknown'
                day_focus[day_name] += d.focus_minutes
                day_counts[day_name] += 1

            best_day_name = max(day_focus, key=lambda k: day_focus[k])
            avg_minutes_per_weekday = (
                round(day_focus[best_day_name] / day_counts[best_day_name])
            )
            insights.append(
                f'{best_day_name} is your most productive day (avg '
                f'{avg_minutes_per_weekday} min of focus).'
            )

        # 3. Break insight
        break_effectiveness_val = self.break_effectiveness(days)
        if break_effectiveness_val < 0.3 and sessions:
            # Find the category where breaks are skipped most
            cat_skip: Counter[str] = Counter()
            cat_total: Counter[str] = Counter()
            for s in sessions:
                cat = _categorize_app(s.primary_app)
                for ro in s.reminder_outcomes:
                    outcome = str(ro.get('outcome', '')).lower()
                    stage = str(ro.get('stage', '')).lower()
                    if stage in BREAK_REMINDER_STAGES and outcome != 'not_sent':
                        cat_total[cat] += 1
                        if outcome != 'break':
                            cat_skip[cat] += 1
            if cat_total:
                worst_cat = max(cat_total, key=lambda c: cat_skip.get(c, 0) / cat_total[c])
                insights.append(
                    f'You tend to skip breaks during {worst_cat} sessions — '
                    f'set a timer to stand up between tasks.'
                )
        elif break_effectiveness_val >= 0.7:
            insights.append(
                'You consistently take breaks when reminded — this sustains '
                'your focus throughout the day.'
            )

        # 4. Category trend / variety
        cats = self.category_breakdown(days)
        if len(cats) >= 3:
            insights.append(
                f'Your work spans {len(cats)} categories ({", ".join(list(cats.keys())[:3])}'
                f'{", ..." if len(cats) > 3 else ""}). '
                f'Balanced deep work builds versatile skills.'
            )
        elif len(cats) == 1:
            dom = list(cats.keys())[0]
            insights.append(
                f'Your sessions are entirely in the "{dom}" category — '
                f'consider mixing in other types of deep work.'
            )

        # 5. Session duration insight
        durations = [s.duration_seconds / 60.0 for s in sessions if s.duration_seconds > 0]
        if durations and len(durations) >= 3:
            median_dur = median(durations)
            if median_dur >= 120:
                insights.append(
                    f'Your sessions average {round(median_dur)} min — '
                    f'you are achieving deep-flow states.'
                )
            elif median_dur <= 30:
                insights.append(
                    f'Your sessions tend to be short ({round(median_dur)} min) — '
                    f'try extending one session this week by 10 minutes.'
                )
            else:
                insights.append(
                    f'Sessions with a planned break average '
                    f'{round(median_dur)} min — your pacing is effective.'
                )

        # Trim to at most 5
        return insights[:5]

    # ── Streak history ─────────────────────────────────────────────────────

    def streak_history(self) -> list[dict[str, Any]]:
        """Read streak transitions from the streak JSON file for charting."""
        if not STREAK_FILE_PATH.exists():
            return []

        try:
            data = json.loads(STREAK_FILE_PATH.read_text(encoding='utf-8'))
            # Return the raw record wrapped in a list (single snapshot)
            return [dict(data)]
        except (json.JSONDecodeError, OSError):
            return []

    # ── Internal helpers ───────────────────────────────────────────────────

    def _load_sessions_for_range(self, start: date, end: date) -> list[SessionSummary]:
        """Load all session summaries falling within the given date range.

        Iterates backward through history until all sessions are older than
        *start*.
        """
        all_sessions = self.history_store.load_recent(limit=5000)
        # HistoryStore.load_recent returns newest last, so the last entry is
        # the most recent session. We scan forward from end to find the cut.
        matched: list[SessionSummary] = []
        for s in all_sessions:
            try:
                s_date = s.started_at.astimezone().date()
            except (ValueError, TypeError):
                continue
            if start <= s_date <= end:
                matched.append(s)
        return matched

    def _load_pomodoros_for_range(self, start: date, end: date) -> list[dict[str, Any]]:
        """Load all pomodoro records within the date range."""
        all_pomodoros = load_history(limit=5000)
        matched: list[dict[str, Any]] = []
        for p in all_pomodoros:
            raw = str(p.get('completed_at', '') or '')
            if not raw:
                continue
            try:
                p_date = datetime.fromisoformat(raw).astimezone().date()
            except (ValueError, TypeError, AttributeError):
                continue
            if start <= p_date <= end:
                matched.append(p)
        return matched

    def _compute_daily_aggregates(
        self,
        sessions: list[SessionSummary],
        pomodoros: list[dict[str, Any]],
    ) -> list[DailyAggregate]:
        """Aggregate sessions and pomodoros into one record per day.

        Days with zero data are omitted — callers should fill gaps as needed.
        """
        # Group sessions by date
        daily_sessions: dict[str, list[SessionSummary]] = defaultdict(list)
        for s in sessions:
            try:
                d = s.started_at.astimezone().date().isoformat()
            except (ValueError, TypeError):
                continue
            daily_sessions[d].append(s)

        # Group pomodoros by date
        daily_pomodoros: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for p in pomodoros:
            raw = str(p.get('completed_at', '') or '')
            if not raw:
                continue
            try:
                d = datetime.fromisoformat(raw).astimezone().date().isoformat()
            except (ValueError, TypeError, AttributeError):
                continue
            daily_pomodoros[d].append(p)

        all_dates = set(daily_sessions) | set(daily_pomodoros)
        aggregates: list[DailyAggregate] = []

        for d in sorted(all_dates):
            day_sessions = daily_sessions.get(d, [])
            day_pomodoros = daily_pomodoros.get(d, [])

            total_minutes = round(sum(s.duration_seconds for s in day_sessions) / 60.0)
            session_count = len(day_sessions)

            durations = [
                s.duration_seconds / 60.0 for s in day_sessions if s.duration_seconds > 0
            ]
            avg_minutes = round(mean(durations), 1) if durations else 0.0

            # Primary category for the day
            cat_minutes: Counter[str] = Counter()
            for s in day_sessions:
                cat_minutes[_categorize_app(s.primary_app)] += s.duration_seconds // 60
            primary_cat = cat_minutes.most_common(1)[0][0] if cat_minutes else 'general'

            pomo_count = len(day_pomodoros)

            # Break stats
            break_count = 0
            break_taken_count = 0
            for s in day_sessions:
                for ro in s.reminder_outcomes:
                    outcome = str(ro.get('outcome', '')).lower()
                    stage = str(ro.get('stage', '')).lower()
                    if stage in BREAK_REMINDER_STAGES and outcome != 'not_sent':
                        break_count += 1
                        if outcome == 'break':
                            break_taken_count += 1

            aggregates.append(
                DailyAggregate(
                    date=d,
                    focus_minutes=total_minutes,
                    session_count=session_count,
                    avg_session_minutes=avg_minutes,
                    primary_category=primary_cat,
                    pomodoro_count=pomo_count,
                    break_count=break_count,
                    break_taken_count=break_taken_count,
                )
            )

        # Cache in the analytics store
        store = self.analytics_store
        for agg in aggregates:
            store.save_daily(agg)

        return aggregates


# ── Display helpers ───────────────────────────────────────────────────────────

def _minutes_to_hours(m: int) -> str:
    """Convert minutes to a human-readable 'Xh Ym' string."""
    hours = m // 60
    mins = m % 60
    if hours:
        return f'{hours}h {mins:02d}m'
    return f'{mins}m'


def format_weekly_report(report: WeeklyReport) -> str:
    """Multi-line formatted weekly report with ASCII bar chart."""
    lines: list[str] = [
        '═' * 52,
        f'  Weekly Deep Work Report — W{report.week} ({report.start_date} to {report.end_date})',
        '═' * 52,
        '',
        f'  Focus time    : {_minutes_to_hours(report.total_focus_minutes)}',
        f'  Sessions      : {report.total_sessions}',
        f'  Avg session   : {report.average_session_minutes:.1f} min',
        f'  Pomodoros     : {report.pomodoro_count}',
        f'  Best day      : {report.best_day}',
        f'  Best hour     : {report.best_hour}',
        f'  Top category  : {report.dominant_category}',
        f'  Top apps      : {", ".join(report.top_apps[:3]) if report.top_apps else "N/A"}',
        f'  Current streak: {report.current_streak} day(s)',
        f'  Longest streak: {report.longest_streak} day(s)',
        f'  Break effect. : {report.break_effectiveness:.0%}',
        f'  Productivity  : {report.productivity_score}/100',
        '',
        '  ── Category breakdown ──',
    ]

    # Recompute category breakdown for ASCII bar chart
    # (we don't have the raw sessions here, so the bar chart is a stub that
    #  callers can populate from category_breakdown; we include the dominant.)
    lines.append(f'    {report.dominant_category}  (dominant)')

    lines.append('')
    lines.append('─' * 52)
    return '\n'.join(lines)


def format_productivity_score(score: ProductivityScore) -> str:
    """Render a ProductivityScore with component breakdown."""
    lines: list[str] = [
        '═' * 52,
        f'  Productivity Score: {score.score}/100',
        '═' * 52,
        '',
    ]
    component_labels = {
        'focus_time': 'Focus time     (40%)',
        'consistency': 'Consistency    (30%)',
        'break_adherence': 'Break adherence (20%)',
        'variety': 'Variety        (10%)',
    }

    for key, label in component_labels.items():
        val = score.components.get(key, 0.0)
        bar_len = max(1, round(val / 10))
        bar = '█' * bar_len
        lines.append(f'  {label}: {bar} {val:.0f}/100')

    if score.recommendations:
        lines.append('')
        lines.append('  ── Recommendations ──')
        for rec in score.recommendations:
            lines.append(f'    • {rec}')

    lines.append('')
    lines.append('─' * 52)
    return '\n'.join(lines)


def format_insights(insights: list[str]) -> str:
    """Format a list of natural language insights as bullet points."""
    if not insights:
        return '  (no insights available)'
    lines = ['  Insights:', '']
    for insight in insights:
        lines.append(f'    • {insight}')
    return '\n'.join(lines)


def format_trend(trend: list[dict[str, Any]]) -> str:
    """Render a daily focus trend as a simple ASCII sparkline.

    Each character represents roughly 10 minutes of focus.
    """
    if not trend:
        return '  (no trend data)'

    lines: list[str] = ['  Focus Trend (last {} days):'.format(len(trend)), '']

    # Sparkline bars
    bar_chars = ['▁', '▂', '▃', '▄', '▅', '▆', '▇', '█']
    max_minutes = max(d['focus_minutes'] for d in trend) or 1
    sparkline = ''
    for d in trend:
        idx = min(7, (d['focus_minutes'] * 8) // (max_minutes + 1))
        sparkline += bar_chars[idx]

    # Date labels at start, middle, end
    first_date = trend[0]['date']
    mid_date = trend[len(trend) // 2]['date']
    last_date = trend[-1]['date']
    padding = len(trend) - len(first_date) - len(mid_date) - len(last_date) - 4
    label_row = f'  {first_date}  {" " * max(0, padding // 2)}{mid_date}  {" " * max(0, padding // 2)}{last_date}'

    lines.append(f'  {sparkline}')
    lines.append(label_row)

    # Legend
    lines.append('')
    lines.append(
        f'  ▁={round(max_minutes / 8)}m  █={max_minutes}m  '
        f'  (bars show focus minutes per day)'
    )

    return '\n'.join(lines)
