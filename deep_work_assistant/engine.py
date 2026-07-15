from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Sequence
from uuid import uuid4

from .messages import build_reminder_message, build_session_start_message


DEFAULT_REMINDER_PLAN = (60, 120, 180)
DEFAULT_RESPONSE_WINDOW_MINUTES = 10
STREAK_FILE = 'deep_work_streak.json'

# Idle threshold (seconds of no *human* input) above which foreground/window
# changes are attributed to automated agents rather than the human.
AGENT_IDLE_THRESHOLD_SECONDS = 120
# Sessions with more than this fraction of agent-active time are tagged as
# agent sessions instead of personal deep work.
AGENT_SESSION_FRACTION = 0.8


def clamp(value: float, low: int, high: int) -> int:
    return int(max(low, min(high, round(value))))


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _session_value(session: Any, key: str, default: Any = None) -> Any:
    if isinstance(session, dict):
        return session.get(key, default)
    return getattr(session, key, default)


def _reminder_list(session: Any) -> list[dict[str, Any]]:
    reminders = _session_value(session, 'reminder_outcomes', []) or []
    normalized: list[dict[str, Any]] = []
    for reminder in reminders:
        if isinstance(reminder, dict):
            normalized.append(reminder)
        else:
            normalized.append(dict(reminder))
    return normalized


@dataclass(frozen=True)
class ActivitySample:
    captured_at: datetime
    process_name: str
    window_title: str
    idle_seconds: int


def classify_sample(
    previous: 'ActivitySample | None',
    sample: 'ActivitySample',
    agent_idle_threshold: int = AGENT_IDLE_THRESHOLD_SECONDS,
) -> str:
    """Classify a sample as 'human-active', 'agent-active', or 'idle'.

    ``idle_seconds`` comes from ``GetLastInputInfo`` and only reflects REAL
    human input (physical keyboard/mouse).  AI agents driving the machine in
    the background change window titles and the foreground window without
    producing human input.  So:

    - recent human input               -> human-active
    - no human input, but the window
      title / foreground app changed   -> agent-active
    - no human input, nothing changed  -> idle
    """
    if sample.idle_seconds <= agent_idle_threshold:
        return 'human-active'
    if previous is not None:
        window_changed = sample.window_title != previous.window_title
        process_changed = sample.process_name != previous.process_name
        idle_rising = sample.idle_seconds >= previous.idle_seconds
        if (window_changed or process_changed) and idle_rising:
            return 'agent-active'
    return 'idle'


@dataclass(frozen=True)
class ReminderPlan:
    hydration_minutes: int = 60
    stretch_minutes: int = 120
    eat_minutes: int = 180

    def as_stages(self) -> list[tuple[str, int]]:
        return [
            ('hydration', self.hydration_minutes),
            ('stretch', self.stretch_minutes),
            ('eat', self.eat_minutes),
        ]


@dataclass(frozen=True)
class LaptopUseProfile:
    """Local-only understanding inferred from completed laptop sessions."""

    dominant_category: str = 'general'
    flow_style: str = 'balanced'
    break_response_style: str = 'unknown'
    top_apps: tuple[str, ...] = ()
    suggested_plan: ReminderPlan = field(default_factory=ReminderPlan)

    def to_record(self) -> dict[str, Any]:
        return {
            'dominant_category': self.dominant_category,
            'flow_style': self.flow_style,
            'break_response_style': self.break_response_style,
            'top_apps': list(self.top_apps),
            'suggested_plan': {
                'hydration_minutes': self.suggested_plan.hydration_minutes,
                'stretch_minutes': self.suggested_plan.stretch_minutes,
                'eat_minutes': self.suggested_plan.eat_minutes,
            },
        }


@dataclass
class FocusStreak:
    """Tracks consecutive days with at least one completed focus session."""

    current_streak: int = 0
    longest_streak: int = 0
    last_session_date: str = ''  # ISO date string (YYYY-MM-DD)
    daily_session_count: int = 0

    def to_record(self) -> dict[str, Any]:
        return {
            'current_streak': self.current_streak,
            'longest_streak': self.longest_streak,
            'last_session_date': self.last_session_date,
            'daily_session_count': self.daily_session_count,
        }

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> 'FocusStreak':
        return cls(
            current_streak=int(record.get('current_streak', 0)),
            longest_streak=int(record.get('longest_streak', 0)),
            last_session_date=str(record.get('last_session_date', '')),
            daily_session_count=int(record.get('daily_session_count', 0)),
        )


def load_streak(path: Path | None = None) -> FocusStreak:
    """Load streak data from JSON file."""
    path = path or Path.home() / '.deep_work_assistant' / STREAK_FILE
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            return FocusStreak.from_record(data)
        except (json.JSONDecodeError, KeyError, ValueError):
            pass
    return FocusStreak()


def save_streak(streak: FocusStreak, path: Path | None = None) -> None:
    """Save streak data to JSON file."""
    path = path or Path.home() / '.deep_work_assistant' / STREAK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(streak.to_record(), indent=2), encoding='utf-8')


def advance_streak(streak: FocusStreak, session_date: date | None = None) -> FocusStreak:
    """Advance the focus streak by one session."""
    session = session_date or datetime.now(timezone.utc).astimezone().date()
    today = session.isoformat()

    if streak.last_session_date == today:
        # Same day — just increment the daily count
        return FocusStreak(
            current_streak=streak.current_streak,
            longest_streak=streak.longest_streak,
            last_session_date=today,
            daily_session_count=streak.daily_session_count + 1,
        )

    yesterday = (session - timedelta(days=1)).isoformat()
    if streak.last_session_date == yesterday or not streak.last_session_date:
        # Consecutive day (or first session ever)
        new_streak = streak.current_streak + 1
    else:
        # Streak broken
        new_streak = 1

    return FocusStreak(
        current_streak=new_streak,
        longest_streak=max(new_streak, streak.longest_streak),
        last_session_date=today,
        daily_session_count=1,
    )


@dataclass(frozen=True)
class EngineEvent:
    kind: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReminderRecord:
    stage: str
    scheduled_minutes: int
    due_at: datetime
    sent_at: datetime | None = None
    response: str | None = None  # interactive popup response: confirmed/skipped/timeout/overridden/completed

    def to_record(self, outcome: str, resolved_at: datetime) -> dict[str, Any]:
        return {
            'stage': self.stage,
            'scheduled_minutes': self.scheduled_minutes,
            'due_at': _utc_iso(self.due_at),
            'sent_at': _utc_iso(self.sent_at) if self.sent_at else None,
            'outcome': outcome,
            'resolved_at': _utc_iso(resolved_at),
        }


@dataclass
class ActiveSession:
    session_id: str
    started_at: datetime
    primary_app: str
    last_sample_at: datetime
    focus_sample_count: int = 0
    idle_total_seconds: int = 0
    sample_count: int = 0
    inactive_streak: int = 0
    human_active_seconds: int = 0
    agent_active_seconds: int = 0
    reminders: list[ReminderRecord] = field(default_factory=list)


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    started_at: datetime
    ended_at: datetime
    primary_app: str
    duration_seconds: int
    focus_sample_count: int
    average_idle_seconds: int
    ended_reason: str
    human_active_seconds: int = 0
    agent_active_seconds: int = 0
    agent_dominated: bool = False
    reminder_outcomes: list[dict[str, Any]] = field(default_factory=list)

    def to_record(self) -> dict[str, Any]:
        return {
            'session_id': self.session_id,
            'started_at': _utc_iso(self.started_at),
            'ended_at': _utc_iso(self.ended_at),
            'primary_app': self.primary_app,
            'duration_seconds': self.duration_seconds,
            'focus_sample_count': self.focus_sample_count,
            'average_idle_seconds': self.average_idle_seconds,
            'ended_reason': self.ended_reason,
            'human_active_seconds': self.human_active_seconds,
            'agent_active_seconds': self.agent_active_seconds,
            'agent_dominated': self.agent_dominated,
            'reminder_outcomes': self.reminder_outcomes,
        }

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> 'SessionSummary':
        return cls(
            session_id=str(record['session_id']),
            started_at=_parse_dt(str(record['started_at'])),
            ended_at=_parse_dt(str(record['ended_at'])),
            primary_app=str(record.get('primary_app', '')),
            duration_seconds=int(record.get('duration_seconds', 0)),
            focus_sample_count=int(record.get('focus_sample_count', 0)),
            average_idle_seconds=int(record.get('average_idle_seconds', 0)),
            ended_reason=str(record.get('ended_reason', 'unknown')),
            human_active_seconds=int(record.get('human_active_seconds', 0)),
            agent_active_seconds=int(record.get('agent_active_seconds', 0)),
            agent_dominated=bool(record.get('agent_dominated', False)),
            reminder_outcomes=list(record.get('reminder_outcomes', [])),
        )


def _categorize_app(process_name: str) -> str:
    name = process_name.lower()
    if any(token in name for token in ('code', 'devenv', 'pycharm', 'cursor', 'terminal', 'powershell', 'cmd', 'bash')):
        return 'coding'
    if any(token in name for token in ('winword', 'word', 'obsidian', 'notion', 'onenote', 'excel', 'powerpnt')):
        return 'writing-admin'
    if any(token in name for token in ('chrome', 'edge', 'firefox', 'brave')):
        return 'browser-research'
    if any(token in name for token in ('teams', 'slack', 'discord', 'zoom', 'outlook')):
        return 'communication'
    if any(token in name for token in ('photoshop', 'illustrator', 'gimp', 'figma', 'penpot', 'blender')):
        return 'creative'
    return 'general'


def analyze_laptop_use(history: Sequence[Any], window: int = 24) -> LaptopUseProfile:
    sessions = [session for session in list(history)[-window:] if _session_value(session, 'duration_seconds', 0)]
    if not sessions:
        return LaptopUseProfile(suggested_plan=ReminderPlan(*DEFAULT_REMINDER_PLAN))

    app_counts: Counter[str] = Counter()
    category_minutes: Counter[str] = Counter()
    durations: list[float] = []
    reminder_outcomes: Counter[str] = Counter()

    for session in sessions:
        app = str(_session_value(session, 'primary_app', '') or 'unknown')
        minutes = float(_session_value(session, 'duration_seconds', 0)) / 60.0
        app_counts[app] += 1
        category_minutes[_categorize_app(app)] += minutes
        durations.append(minutes)
        for reminder in _reminder_list(session):
            outcome = str(reminder.get('outcome', '')).lower()
            if outcome:
                reminder_outcomes[outcome] += 1

    dominant_category = category_minutes.most_common(1)[0][0] if category_minutes else 'general'
    middle_duration = median(durations) if durations else 0
    if middle_duration >= 135:
        flow_style = 'deep-flow'
    elif middle_duration <= 55:
        flow_style = 'short-sprints'
    else:
        flow_style = 'balanced'

    continued = reminder_outcomes['continued'] + reminder_outcomes['ignored']
    breaks = reminder_outcomes['break']
    if continued > breaks:
        break_response_style = 'pushes-through-reminders'
    elif breaks > continued:
        break_response_style = 'breaks-on-reminder'
    else:
        break_response_style = 'unknown'

    base_plan = build_adaptive_plan(sessions)
    if break_response_style == 'pushes-through-reminders':
        suggested_plan = ReminderPlan(
            hydration_minutes=clamp(base_plan.hydration_minutes - 5, 40, 90),
            stretch_minutes=clamp(base_plan.stretch_minutes - 15, 60, 165),
            eat_minutes=clamp(base_plan.eat_minutes - 10, 110, 240),
        )
    elif break_response_style == 'breaks-on-reminder' and flow_style == 'short-sprints':
        suggested_plan = ReminderPlan(
            hydration_minutes=clamp(base_plan.hydration_minutes + 5, 45, 95),
            stretch_minutes=clamp(base_plan.stretch_minutes + 10, 75, 175),
            eat_minutes=base_plan.eat_minutes,
        )
    else:
        suggested_plan = base_plan

    return LaptopUseProfile(
        dominant_category=dominant_category,
        flow_style=flow_style,
        break_response_style=break_response_style,
        top_apps=tuple(app for app, _ in app_counts.most_common(5)),
        suggested_plan=suggested_plan,
    )


def build_adaptive_plan(history: Sequence[Any], window: int = 12) -> ReminderPlan:
    sessions = list(history)[-window:]
    if not sessions:
        return ReminderPlan(*DEFAULT_REMINDER_PLAN)

    durations = [
        float(_session_value(session, 'duration_seconds', 0)) / 60.0
        for session in sessions
        if float(_session_value(session, 'duration_seconds', 0)) > 0
    ]
    if not durations:
        return ReminderPlan(*DEFAULT_REMINDER_PLAN)

    flow_anchor = median(durations)
    scale = max(0.75, min(1.35, flow_anchor / 120.0))
    multiplier = 0.85 + 0.15 * scale

    session_scores: list[float] = []
    for session in sessions:
        reminder_scores: list[float] = []
        for reminder in _reminder_list(session):
            outcome = str(reminder.get('outcome', '')).lower()
            if outcome == 'continued':
                reminder_scores.append(1.0)
            elif outcome == 'break':
                reminder_scores.append(-1.0)
            elif outcome == 'ignored':
                reminder_scores.append(-0.5)
        if reminder_scores:
            session_scores.append(sum(reminder_scores) / len(reminder_scores))

    response_score = sum(session_scores) / len(session_scores) if session_scores else 0.0
    bias = clamp(response_score * 12.0, -12, 12)

    hydration = clamp(60 * multiplier + bias * 0.25, 45, 90)
    stretch = clamp(120 * multiplier + bias * 0.5, 75, 165)
    eat = clamp(180 * multiplier + bias * 0.75, 120, 240)
    return ReminderPlan(hydration, stretch, eat)


class DeepWorkAssistant:
    def __init__(
        self,
        reminder_plan: ReminderPlan | None = None,
        *,
        start_streak_required: int = 2,
        stop_streak_required: int = 3,
        start_idle_threshold_seconds: int = 180,
        stop_idle_threshold_seconds: int = 900,
        response_window_minutes: int = DEFAULT_RESPONSE_WINDOW_MINUTES,
        laptop_use_profile: LaptopUseProfile | None = None,
        focus_streak: FocusStreak | None = None,
        session_id_factory: Any | None = None,
        voice_enabled: bool = False,
    ) -> None:
        self.reminder_plan = reminder_plan or (laptop_use_profile.suggested_plan if laptop_use_profile else ReminderPlan(*DEFAULT_REMINDER_PLAN))
        self.laptop_use_profile = laptop_use_profile or LaptopUseProfile(suggested_plan=self.reminder_plan)
        self.focus_streak = focus_streak or FocusStreak()
        self.voice_enabled = voice_enabled
        self.start_streak_required = max(1, int(start_streak_required))
        self.stop_streak_required = max(1, int(stop_streak_required))
        self.start_idle_threshold_seconds = int(start_idle_threshold_seconds)
        self.stop_idle_threshold_seconds = int(stop_idle_threshold_seconds)
        self.response_window_minutes = int(response_window_minutes)
        self.session_id_factory = session_id_factory or (lambda: f'dwa-{uuid4().hex[:8]}')

        self.current_session: ActiveSession | None = None
        self._previous_sample: ActivitySample | None = None
        self._start_streak = 0

    def process_sample(self, sample: ActivitySample) -> list[EngineEvent]:
        events: list[EngineEvent] = []
        if self.current_session is None:
            self._advance_start_streak(sample)
            if self._start_streak >= self.start_streak_required:
                self.current_session = self._start_session(sample)
                # Build motivational session-start message
                start_message = build_session_start_message(
                    primary_app=sample.process_name,
                    profile_category=self.laptop_use_profile.dominant_category,
                    streak_days=self.focus_streak.current_streak,
                )
                events.append(
                    EngineEvent(
                        'session_started',
                        {
                            'session_id': self.current_session.session_id,
                            'started_at': _utc_iso(self.current_session.started_at),
                            'primary_app': self.current_session.primary_app,
                            'message': start_message,
                            'streak_days': self.focus_streak.current_streak,
                            'category': self.laptop_use_profile.dominant_category,
                            'voice_enabled': self.voice_enabled,
                        },
                    )
                )
                self._start_streak = 0
        else:
            session = self.current_session
            elapsed = max(0, int((sample.captured_at - session.last_sample_at).total_seconds()))
            classification = classify_sample(self._previous_sample, sample)
            if classification == 'human-active':
                session.human_active_seconds += elapsed
            elif classification == 'agent-active':
                session.agent_active_seconds += elapsed
            session.last_sample_at = sample.captured_at
            session.sample_count += 1
            session.idle_total_seconds += max(0, int(sample.idle_seconds))
            if sample.idle_seconds > self.stop_idle_threshold_seconds:
                session.inactive_streak += 1
            else:
                session.inactive_streak = 0

            for reminder in session.reminders:
                # Reminder countdowns accrue on HUMAN active time only: while
                # agents drive the machine (or the human is idle), the timers
                # pause instead of counting wall-clock time.
                if reminder.sent_at is None and session.human_active_seconds >= reminder.scheduled_minutes * 60:
                    reminder.sent_at = sample.captured_at
                    # Build smarter, context-aware message
                    smart_message = build_reminder_message(
                        stage=reminder.stage,
                        profile_category=self.laptop_use_profile.dominant_category,
                        now=sample.captured_at,
                        streak_days=self.focus_streak.current_streak,
                    )
                    events.append(
                        EngineEvent(
                            'reminder_due',
                            {
                                'stage': reminder.stage,
                                'title': self._title_for_stage(reminder.stage),
                                'message': smart_message,
                                'sent_at': _utc_iso(sample.captured_at),
                                'due_at': _utc_iso(reminder.due_at),
                                'session_id': session.session_id,
                                'laptop_use_profile': self.laptop_use_profile.to_record(),
                                'streak_days': self.focus_streak.current_streak,
                                'category': self.laptop_use_profile.dominant_category,
                            },
                        )
                    )

            if session.inactive_streak >= self.stop_streak_required:
                summary = self._finalize_current_session(sample.captured_at, ended_reason='break')
                events.append(EngineEvent('session_ended', {'summary': summary.to_record(), 'focus_streak': self.focus_streak.to_record()}))

        self._previous_sample = sample
        return events

    def record_reminder_response(self, stage: str, response: str) -> bool:
        """Attach an interactive popup/overlay response to the active session.

        Returns True when a matching sent-but-unresolved reminder was found.
        """
        if self.current_session is None:
            return False
        for reminder in self.current_session.reminders:
            if reminder.stage == stage and reminder.sent_at is not None and reminder.response is None:
                reminder.response = response
                return True
        return False

    def finalize_session(self, finished_at: datetime | None = None, ended_reason: str = 'manual') -> SessionSummary | None:
        if self.current_session is None:
            return None
        finished_at = finished_at or self.current_session.last_sample_at
        summary = self._finalize_current_session(finished_at, ended_reason=ended_reason)
        # Advance streak on any session that lasted at least 10 minutes
        if summary.duration_seconds >= 600:
            self.focus_streak = advance_streak(self.focus_streak, finished_at.astimezone().date())
        return summary

    def _advance_start_streak(self, sample: ActivitySample) -> None:
        active = sample.idle_seconds <= self.start_idle_threshold_seconds and bool(sample.process_name)
        if not active:
            self._start_streak = 0
            return
        if self._previous_sample and self._previous_sample.process_name == sample.process_name:
            self._start_streak += 1
        else:
            self._start_streak = 1

    def _start_session(self, sample: ActivitySample) -> ActiveSession:
        session = ActiveSession(
            session_id=self.session_id_factory(),
            started_at=sample.captured_at,
            primary_app=sample.process_name,
            last_sample_at=sample.captured_at,
            focus_sample_count=1,
            idle_total_seconds=max(0, int(sample.idle_seconds)),
            sample_count=1,
            reminders=[
                ReminderRecord(
                    stage=stage,
                    scheduled_minutes=minutes,
                    due_at=sample.captured_at + timedelta(minutes=minutes),
                )
                for stage, minutes in self.reminder_plan.as_stages()
            ],
        )
        return session

    def _finalize_current_session(self, finished_at: datetime, ended_reason: str) -> SessionSummary:
        session = self.current_session
        if session is None:
            raise RuntimeError('No active session to finalize')

        reminder_outcomes: list[dict[str, Any]] = []
        for reminder in session.reminders:
            if reminder.sent_at is None:
                outcome = 'not_sent'
                resolved_at = finished_at
            elif reminder.response:
                # Explicit interactive-popup / overlay response wins.
                outcome = reminder.response
                resolved_at = finished_at
            else:
                deadline = reminder.sent_at + timedelta(minutes=self.response_window_minutes)
                resolved_at = finished_at
                if ended_reason == 'break' and finished_at <= deadline:
                    outcome = 'break'
                elif finished_at >= deadline:
                    outcome = 'continued'
                else:
                    outcome = 'ignored'
            reminder_outcomes.append(reminder.to_record(outcome=outcome, resolved_at=resolved_at))

        duration_seconds = max(0, int((finished_at - session.started_at).total_seconds()))
        tracked = session.human_active_seconds + session.agent_active_seconds
        agent_dominated = (
            tracked > 0
            and session.agent_active_seconds / tracked > AGENT_SESSION_FRACTION
        )
        summary = SessionSummary(
            session_id=session.session_id,
            started_at=session.started_at,
            ended_at=finished_at,
            primary_app=session.primary_app,
            duration_seconds=duration_seconds,
            focus_sample_count=session.focus_sample_count,
            average_idle_seconds=round(session.idle_total_seconds / session.sample_count) if session.sample_count else 0,
            ended_reason='agent-session' if agent_dominated else ended_reason,
            human_active_seconds=session.human_active_seconds,
            agent_active_seconds=session.agent_active_seconds,
            agent_dominated=agent_dominated,
            reminder_outcomes=reminder_outcomes,
        )

        self.current_session = None
        self._start_streak = 0
        return summary

    @staticmethod
    def _title_for_stage(stage: str) -> str:
        titles = {
            'hydration': 'Hydrate',
            'stretch': 'Stretch break',
            'eat': 'Eat something',
        }
        return titles.get(stage, 'Deep work reminder')

    @staticmethod
    def _message_for_stage(stage: str, profile: LaptopUseProfile | None = None) -> str:
        messages = {
            'hydration': 'You have been in flow for a while. Drink some water.',
            'stretch': 'Time to stand up, stretch, and reset your posture.',
            'eat': 'You have been pushing for a long stretch. Grab food if you can.',
        }
        message = messages.get(stage, 'Check in with your body and keep the flow intentional.')
        if profile is None or profile.dominant_category == 'general':
            return message

        context = {
            'coding': ' Your laptop pattern says this is usually coding flow, so reset your wrists, neck, and eyes before you dive back in.',
            'writing-admin': ' Your laptop pattern says this is writing/admin work, so loosen your shoulders and unclench your jaw.',
            'browser-research': ' Your laptop pattern says this is research/browser work, so look away from the screen and reset your posture.',
            'communication': ' Your laptop pattern says this is communication-heavy work, so take one quiet minute before the next response.',
            'creative': ' Your laptop pattern says this is creative work, so stand up and give your hands and eyes a reset.',
        }.get(profile.dominant_category, '')
        if stage == 'stretch' and context:
            return f'{message}{context}'
        if stage == 'hydration' and profile.break_response_style == 'pushes-through-reminders':
            return f'{message} You often push through reminders, so take the sip now before continuing.'
        return message


def summary_from_record(record: dict[str, Any]) -> SessionSummary:
    return SessionSummary.from_record(record)



def format_plan(plan: ReminderPlan) -> str:
    return (
        f'hydration={plan.hydration_minutes}m, '
        f'stretch={plan.stretch_minutes}m, '
        f'eat={plan.eat_minutes}m'
    )
