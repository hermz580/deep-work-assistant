"""Pomodoro timer module — state machine for deep work sessions.

Provides a PomodoroTimer with automatic phase transitions, pause/resume,
card linking, and JSONL-based persistence of completed pomodoros.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4


# ── Constants ─────────────────────────────────────────────────────────────────

POMODORO_LOG_PATH = Path.home() / '.deep_work_assistant' / 'pomodoros.jsonl'


# ── Enums ─────────────────────────────────────────────────────────────────────

class PomodoroState(Enum):
    IDLE = 'idle'
    WORKING = 'working'
    SHORT_BREAK = 'short_break'
    LONG_BREAK = 'long_break'


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class PomodoroConfig:
    work_minutes: int = 25
    short_break_minutes: int = 5
    long_break_minutes: int = 15
    pomodoros_before_long: int = 4
    auto_start_breaks: bool = True
    auto_start_work: bool = False


@dataclass
class PomodoroSession:
    session_id: str
    state: PomodoroState = PomodoroState.IDLE
    current_pomodoro: int = 1  # 1-based
    work_started_at: datetime | None = None
    phase_started_at: datetime | None = None
    phase_duration_minutes: int = 0
    active_card_id: str | None = None
    completed_pomodoros: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class PomodoroEvent:
    kind: str
    data: dict[str, Any] = field(default_factory=dict)


# ── History loader ────────────────────────────────────────────────────────────

def load_history(limit: int = 50) -> list[dict[str, Any]]:
    """Read the last *limit* completed pomodoro records from JSONL (newest last)."""
    path = POMODORO_LOG_PATH
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open('r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records[-limit:] if limit else records


# ── PomodoroTimer ─────────────────────────────────────────────────────────────

class PomodoroTimer:
    """Pomodoro timer with state machine, auto-transitions, pause/resume, and JSONL persistence."""

    def __init__(self, config: PomodoroConfig | None = None) -> None:
        self.config = config or PomodoroConfig()
        self.session: PomodoroSession | None = None
        self._paused_remaining_seconds: float | None = None
        self._events: list[PomodoroEvent] = []
        self._phase_completed_emitted: bool = False

    # ── Public API ───────────────────────────────────────────────────────────

    def start(self, card_id: str | None = None, now: datetime | None = None) -> PomodoroEvent:
        """Begin a WORKING phase, creating a new session.

        Returns a 'started' event.  If a previous session was still active it
        is implicitly replaced.
        """
        now = now or datetime.now(timezone.utc)
        session_id = f'pomo-{uuid4().hex[:8]}'

        session = PomodoroSession(
            session_id=session_id,
            state=PomodoroState.WORKING,
            current_pomodoro=1,
            work_started_at=now,
            phase_started_at=now,
            phase_duration_minutes=self.config.work_minutes,
            active_card_id=card_id,
        )
        self.session = session
        self._paused_remaining_seconds = None
        self._events = []
        self._phase_completed_emitted = False

        event = PomodoroEvent('started', self._status_dict(now))
        self._events.append(event)
        return event

    def tick(self, now: datetime | None = None) -> list[PomodoroEvent]:
        """Check if the current phase has elapsed.

        When the phase duration is reached:
        * WORKING  → auto-start break  if *auto_start_breaks* is set
        * BREAK    → auto-start work   if *auto_start_work* is set

        When auto-transition is disabled an event (``work_elapsed`` /
        ``break_elapsed``) is emitted exactly once per phase so callers can
        react (e.g. prompt the user, then call :meth:`transition`).

        Returns a list of events emitted during this tick.
        """
        if self.session is None or self.session.state == PomodoroState.IDLE:
            return []
        if self._paused_remaining_seconds is not None:
            return []

        now = now or datetime.now(timezone.utc)
        events: list[PomodoroEvent] = []

        if self._phase_elapsed(now):
            if not self._phase_completed_emitted:
                self._phase_completed_emitted = True

                if self.session.state == PomodoroState.WORKING:
                    if self.config.auto_start_breaks:
                        events.extend(self._complete_pomodoro(now))
                        events.append(self._start_break(now))
                    else:
                        events.append(
                            PomodoroEvent(
                                'work_elapsed',
                                {
                                    'session_id': self.session.session_id,
                                    'pomodoro_number': self.session.current_pomodoro,
                                    'work_minutes': self.session.phase_duration_minutes,
                                },
                            )
                        )
                elif self.session.state in (
                    PomodoroState.SHORT_BREAK,
                    PomodoroState.LONG_BREAK,
                ):
                    if self.config.auto_start_work:
                        events.append(self._start_next_pomodoro(now))
                    else:
                        events.append(
                            PomodoroEvent(
                                'break_elapsed',
                                {
                                    'session_id': self.session.session_id,
                                    'state': self.session.state.value,
                                },
                            )
                        )

        return events

    def transition(self, now: datetime | None = None) -> list[PomodoroEvent]:
        """Manually move to the next phase.

        WORKING → appropriate break
        BREAK   → next WORKING phase

        Returns one or more events representing the transition.
        """
        if self.session is None or self.session.state == PomodoroState.IDLE:
            return []

        now = now or datetime.now(timezone.utc)
        self._phase_completed_emitted = False
        events: list[PomodoroEvent] = []

        if self.session.state == PomodoroState.WORKING:
            events.extend(self._complete_pomodoro(now))
            events.append(self._start_break(now))
        elif self.session.state in (
            PomodoroState.SHORT_BREAK,
            PomodoroState.LONG_BREAK,
        ):
            events.append(self._start_next_pomodoro(now))

        return events

    def skip_break(self, now: datetime | None = None) -> PomodoroEvent | None:
        """Skip the current break and immediately start the next work phase.

        Returns a 'started' event, or None if not currently in a break state.
        """
        if self.session is None:
            return None
        if self.session.state not in (
            PomodoroState.SHORT_BREAK,
            PomodoroState.LONG_BREAK,
        ):
            return None

        now = now or datetime.now(timezone.utc)
        self._phase_completed_emitted = False
        return self._start_next_pomodoro(now)

    def pause(self) -> PomodoroEvent | None:
        """Pause the current phase, saving remaining time for a later :meth:`resume`.

        Returns a 'paused' event, or None if already paused or no active session.
        """
        if self.session is None or self.session.state == PomodoroState.IDLE:
            return None
        if self._paused_remaining_seconds is not None:
            return None

        now = datetime.now(timezone.utc)
        if self.session.phase_started_at is None:
            return None

        elapsed = (now - self.session.phase_started_at).total_seconds()
        total_phase_seconds = self.session.phase_duration_minutes * 60
        self._paused_remaining_seconds = max(0.0, total_phase_seconds - elapsed)

        event = PomodoroEvent('paused', self._status_dict(now))
        self._events.append(event)
        return event

    def resume(self, now: datetime) -> PomodoroEvent | None:
        """Resume from a paused state.

        Recalculates the phase duration to match the remaining time and sets
        *now* as the new phase start.

        Returns a 'resumed' event, or None if not paused.
        """
        if self.session is None or self.session.state == PomodoroState.IDLE:
            return None
        if self._paused_remaining_seconds is None:
            return None

        self.session.phase_duration_minutes = max(
            1, round(self._paused_remaining_seconds / 60)
        )
        self.session.phase_started_at = now
        self._paused_remaining_seconds = None
        self._phase_completed_emitted = False

        event = PomodoroEvent('resumed', self._status_dict(now))
        self._events.append(event)
        return event

    def stop(self, now: datetime | None = None) -> dict[str, Any]:
        """End the session, flush completed pomodoros to JSONL, and return a summary dict.

        The returned dict includes:
        * session_id
        * state ('idle')
        * pomodoros_completed — count
        * total_work_minutes — sum of completed work phase lengths
        * active_card_id
        * completed_pomodoros — list of logged records
        """
        now = now or datetime.now(timezone.utc)
        session = self.session

        if session is None:
            return {'session_id': None, 'state': 'idle'}

        summary: dict[str, Any] = {
            'session_id': session.session_id,
            'state': 'idle',
            'pomodoros_completed': len(session.completed_pomodoros),
            'total_work_minutes': sum(
                p.get('work_minutes', 0) for p in session.completed_pomodoros
            ),
            'active_card_id': session.active_card_id,
            'completed_pomodoros': list(session.completed_pomodoros),
        }

        event = PomodoroEvent(
            'stopped',
            {**summary, 'stopped_at': _utc_iso(now)},
        )
        self._events.append(event)
        self.session = None
        self._paused_remaining_seconds = None
        self._phase_completed_emitted = False
        return summary

    def status(self) -> dict[str, Any]:
        """Return a snapshot of the current timer state.

        Keys: ``state``, ``current_pomodoro``, ``pomodoros_completed``,
        ``pomodoros_this_session``, ``phase_duration_minutes``,
        ``elapsed_minutes``, ``remaining_minutes``, ``active_card_id``,
        ``is_paused``.
        """
        now = datetime.now(timezone.utc)
        if self.session is None:
            return {
                'state': 'idle',
                'pomodoros_completed': 0,
                'pomodoros_this_session': 0,
                'current_pomodoro': 0,
            }
        return self._status_dict(now)

    def link_card(self, card_id: str) -> None:
        """Associate the current session with a kanban card.

        Has no effect when no session is active.
        """
        if self.session is not None:
            self.session.active_card_id = card_id

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _phase_elapsed(self, now: datetime) -> bool:
        """Check whether the current phase duration has been reached."""
        if self.session is None or self.session.phase_started_at is None:
            return False
        elapsed = (now - self.session.phase_started_at).total_seconds()
        return elapsed >= self.session.phase_duration_minutes * 60

    def _complete_pomodoro(self, now: datetime) -> list[PomodoroEvent]:
        """Mark the current work phase as a completed pomodoro and persist it.

        Appends the record to the session's completed list and writes it to
        the JSONL log immediately.  Emits a ``pomodoro_completed`` event and
        a ``phase_transition`` event indicating the next break type.
        """
        session = self.session
        if session is None or session.state != PomodoroState.WORKING:
            return []

        phase_start = session.phase_started_at or now

        record: dict[str, Any] = {
            'session_id': session.session_id,
            'pomodoro_number': session.current_pomodoro,
            'work_minutes': session.phase_duration_minutes,
            'started_at': _utc_iso(phase_start),
            'completed_at': _utc_iso(now),
            'card_id': session.active_card_id or '',
            'notes': '',
        }
        session.completed_pomodoros.append(record)
        _append_log(record)

        pomodoro_number = session.current_pomodoro
        session.current_pomodoro += 1

        next_break = self._next_break_type(pomodoro_number)

        return [
            PomodoroEvent('pomodoro_completed', self._status_dict(now)),
            PomodoroEvent(
                'phase_transition',
                {
                    'from_state': PomodoroState.WORKING.value,
                    'to_state': next_break.value,
                    'pomodoro_number': pomodoro_number,
                },
            ),
        ]

    def _start_break(self, now: datetime) -> PomodoroEvent:
        """Transition from WORKING to the appropriate break state."""
        session = self.session
        if session is None:
            raise RuntimeError('No active session')

        pomodoro_number = session.current_pomodoro - 1  # already incremented
        break_state = self._next_break_type(pomodoro_number)
        session.state = break_state
        session.phase_started_at = now
        session.phase_duration_minutes = (
            self.config.long_break_minutes
            if break_state == PomodoroState.LONG_BREAK
            else self.config.short_break_minutes
        )
        self._phase_completed_emitted = False

        event = PomodoroEvent(
            break_state.value,
            {**self._status_dict(now), 'from_state': PomodoroState.WORKING.value},
        )
        self._events.append(event)
        return event

    def _start_next_pomodoro(self, now: datetime) -> PomodoroEvent:
        """Transition from a break to the next WORKING phase."""
        session = self.session
        if session is None:
            raise RuntimeError('No active session')

        prev_state = session.state

        # A completed long break resets the pomodoro counter
        if prev_state == PomodoroState.LONG_BREAK:
            session.current_pomodoro = 1

        session.state = PomodoroState.WORKING
        session.phase_started_at = now
        session.phase_duration_minutes = self.config.work_minutes
        self._phase_completed_emitted = False

        event = PomodoroEvent(
            'started',
            {
                **self._status_dict(now),
                'from_state': prev_state.value,
            },
        )
        self._events.append(event)
        return event

    def _next_break_type(self, pomodoro_number: int) -> PomodoroState:
        """Determine the break type after completing *pomodoro_number*."""
        if pomodoro_number % self.config.pomodoros_before_long == 0:
            return PomodoroState.LONG_BREAK
        return PomodoroState.SHORT_BREAK

    def _status_dict(self, now: datetime) -> dict[str, Any]:
        """Build a full status snapshot for events and :meth:`status` queries."""
        session = self.session
        if session is None:
            return {
                'state': 'idle',
                'pomodoros_completed': 0,
                'pomodoros_this_session': 0,
                'current_pomodoro': 0,
            }

        elapsed_minutes = 0.0
        remaining_minutes = 0.0

        if session.phase_started_at is not None and session.state != PomodoroState.IDLE:
            if self._paused_remaining_seconds is not None:
                total = session.phase_duration_minutes * 60
                remaining_minutes = self._paused_remaining_seconds / 60.0
                elapsed_minutes = max(0.0, (total - self._paused_remaining_seconds) / 60.0)
            else:
                elapsed = (now - session.phase_started_at).total_seconds()
                elapsed_minutes = elapsed / 60.0
                remaining_minutes = max(
                    0.0, session.phase_duration_minutes - elapsed_minutes
                )

        return {
            'session_id': session.session_id,
            'state': session.state.value,
            'current_pomodoro': session.current_pomodoro,
            'pomodoros_completed': len(session.completed_pomodoros),
            'pomodoros_this_session': len(session.completed_pomodoros)
            + (1 if session.state == PomodoroState.WORKING else 0),
            'phase_duration_minutes': session.phase_duration_minutes,
            'elapsed_minutes': round(elapsed_minutes, 1),
            'remaining_minutes': round(remaining_minutes, 1),
            'active_card_id': session.active_card_id or '',
            'is_paused': self._paused_remaining_seconds is not None,
        }


# ── JSONL persistence ─────────────────────────────────────────────────────────

def _append_log(record: dict[str, Any]) -> None:
    """Append a single JSON line to the pomodoro log, creating the directory if needed."""
    POMODORO_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with POMODORO_LOG_PATH.open('a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')


# ── Helpers ───────────────────────────────────────────────────────────────────

def _utc_iso(value: datetime) -> str:
    """Format a datetime as a UTC ISO-8601 string."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()
