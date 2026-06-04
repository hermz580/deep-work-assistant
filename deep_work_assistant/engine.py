from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Iterable, Sequence
from uuid import uuid4


DEFAULT_REMINDER_PLAN = (60, 120, 180)
DEFAULT_RESPONSE_WINDOW_MINUTES = 10


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
class EngineEvent:
    kind: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReminderRecord:
    stage: str
    scheduled_minutes: int
    due_at: datetime
    sent_at: datetime | None = None

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
            reminder_outcomes=list(record.get('reminder_outcomes', [])),
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
        session_id_factory: Any | None = None,
    ) -> None:
        self.reminder_plan = reminder_plan or ReminderPlan(*DEFAULT_REMINDER_PLAN)
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
                events.append(
                    EngineEvent(
                        'session_started',
                        {
                            'session_id': self.current_session.session_id,
                            'started_at': _utc_iso(self.current_session.started_at),
                            'primary_app': self.current_session.primary_app,
                        },
                    )
                )
                self._start_streak = 0
        else:
            session = self.current_session
            session.last_sample_at = sample.captured_at
            session.sample_count += 1
            session.idle_total_seconds += max(0, int(sample.idle_seconds))
            if sample.idle_seconds > self.stop_idle_threshold_seconds:
                session.inactive_streak += 1
            else:
                session.inactive_streak = 0

            for reminder in session.reminders:
                if reminder.sent_at is None and sample.captured_at >= reminder.due_at:
                    reminder.sent_at = sample.captured_at
                    events.append(
                        EngineEvent(
                            'reminder_due',
                            {
                                'stage': reminder.stage,
                                'title': self._title_for_stage(reminder.stage),
                                'message': self._message_for_stage(reminder.stage),
                                'sent_at': _utc_iso(sample.captured_at),
                                'due_at': _utc_iso(reminder.due_at),
                                'session_id': session.session_id,
                            },
                        )
                    )

            if session.inactive_streak >= self.stop_streak_required:
                summary = self._finalize_current_session(sample.captured_at, ended_reason='break')
                events.append(EngineEvent('session_ended', {'summary': summary.to_record()}))

        self._previous_sample = sample
        return events

    def finalize_session(self, finished_at: datetime | None = None, ended_reason: str = 'manual') -> SessionSummary | None:
        if self.current_session is None:
            return None
        finished_at = finished_at or self.current_session.last_sample_at
        summary = self._finalize_current_session(finished_at, ended_reason=ended_reason)
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

        summary = SessionSummary(
            session_id=session.session_id,
            started_at=session.started_at,
            ended_at=finished_at,
            primary_app=session.primary_app,
            duration_seconds=max(0, int((finished_at - session.started_at).total_seconds())),
            focus_sample_count=session.focus_sample_count,
            average_idle_seconds=round(session.idle_total_seconds / session.sample_count) if session.sample_count else 0,
            ended_reason=ended_reason,
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
    def _message_for_stage(stage: str) -> str:
        messages = {
            'hydration': 'You have been in flow for a while. Drink some water.',
            'stretch': 'Time to stand up, stretch, and reset your posture.',
            'eat': 'You have been pushing for a long stretch. Grab food if you can.',
        }
        return messages.get(stage, 'Check in with your body and keep the flow intentional.')


def summary_from_record(record: dict[str, Any]) -> SessionSummary:
    return SessionSummary.from_record(record)



def format_plan(plan: ReminderPlan) -> str:
    return (
        f'hydration={plan.hydration_minutes}m, '
        f'stretch={plan.stretch_minutes}m, '
        f'eat={plan.eat_minutes}m'
    )
