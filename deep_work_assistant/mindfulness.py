"""Guided mindfulness exercises for the Deep Work Assistant.

Provides breathing exercises (box, 4-7-8, simple), body scan
progressive relaxation, gratitude prompts, and countdown timers
via the MindfulnessCoach class.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


# ── Constants ──────────────────────────────────────────────────────────────────

MINDFULNESS_LOG_PATH = Path.home() / '.deep_work_assistant' / 'mindfulness.jsonl'

BREATHING_PATTERNS: dict[str, list[tuple[str, int]]] = {
    'box': [
        ('Inhale', 4),
        ('Hold', 4),
        ('Exhale', 4),
        ('Hold', 4),
    ],
    '4-7-8': [
        ('Inhale', 4),
        ('Hold', 7),
        ('Exhale', 8),
    ],
    'simple': [
        ('Inhale', 4),
        ('Exhale', 6),
    ],
}

BODY_SCAN_PARTS: list[str] = [
    'feet',
    'calves',
    'thighs',
    'hips',
    'stomach',
    'chest',
    'hands',
    'arms',
    'shoulders',
    'face',
]

GRATITUDE_PROMPTS: list[str] = [
    "Think of something you're grateful for today.",
    'Think of a person who made a difference in your life.',
    'Think of something about yourself that you appreciate.',
]


# ── Enums / Dataclasses ───────────────────────────────────────────────────────

class MindfulnessType(Enum):
    BREATHING = 'breathing'
    COUNTDOWN = 'countdown'
    BODY_SCAN = 'body_scan'
    GRATITUDE = 'gratitude'


@dataclass
class MindfulnessSession:
    session_id: str
    session_type: MindfulnessType
    duration_minutes: int
    started_at: datetime | None = None
    completed: bool = False


@dataclass
class MindfulnessEvent:
    kind: str  # 'phase_updated' | 'session_completed' | 'interrupted'
    data: dict = field(default_factory=dict)


# ── MindfulnessCoach ──────────────────────────────────────────────────────────

class MindfulnessCoach:
    """Guides a user through a mindfulness exercise.

    Call ``start()`` to begin a session, then ``tick()`` periodically
    (e.g., every second) to advance the exercise and receive events.
    Use ``get_guidance()`` to display the current instruction text.
    """

    def __init__(self) -> None:
        self._session: MindfulnessSession | None = None
        self._active: bool = False
        self._voice_enabled: bool = False

        # ── breathing sub-state ───────────────────────────────────────────
        self._pattern_name: str = 'box'
        self._phase_index: int = 0
        self._phase_elapsed: float = 0.0

        # ── body-scan sub-state ───────────────────────────────────────────
        self._zone_index: int = 0
        self._zone_elapsed: float = 0.0

        # ── gratitude sub-state ───────────────────────────────────────────
        self._prompt_index: int = 0
        self._prompt_elapsed: float = 0.0

        # ── countdown / general elapsed ────────────────────────────────────
        self._elapsed_seconds: float = 0.0
        self._last_tick: datetime | None = None

    # ── Public API ────────────────────────────────────────────────────────────

    def start(
        self,
        session_type: MindfulnessType,
        duration_minutes: int = 3,
        voice_enabled: bool = False,
        now: datetime | None = None,
    ) -> None:
        """Begin a new mindfulness session.

        Parameters
        ----------
        session_type : MindfulnessType
            The type of exercise to run.
        duration_minutes : int
            Total session length in minutes (default 3).
        voice_enabled : bool
            Whether voice guidance is active (default False).
        now : datetime | None
            Explicit timestamp for deterministic testing. Uses system clock when None.
        """
        now = now or datetime.now(timezone.utc)
        self._session = MindfulnessSession(
            session_id=uuid.uuid4().hex[:12],
            session_type=session_type,
            duration_minutes=duration_minutes,
            started_at=now,
            completed=False,
        )
        self._active = True
        self._voice_enabled = voice_enabled
        self._last_tick = now

        # Reset all sub-state
        self._phase_index = 0
        self._phase_elapsed = 0.0
        self._zone_index = 0
        self._zone_elapsed = 0.0
        self._prompt_index = 0
        self._prompt_elapsed = 0.0
        self._elapsed_seconds = 0.0

    def tick(self, now: datetime | None = None) -> list[MindfulnessEvent]:
        """Advance the exercise and return any events emitted.

        Parameters
        ----------
        now : datetime | None
            Current wall-clock time.  When *None* the system clock is used.
            The delta from the previous tick determines how far to advance.
        """
        if not self._active or self._session is None:
            return []

        now = now or datetime.now(timezone.utc)

        # Compute delta since last tick; default to 1 s on first call.
        if self._last_tick is not None:
            delta = (now - self._last_tick).total_seconds()
        else:
            delta = 1.0
        self._last_tick = now

        # Clamp to avoid large jumps from sleep / pause
        delta = max(0.1, min(delta, 30.0))
        self._elapsed_seconds += delta

        events: list[MindfulnessEvent] = []

        # ── session-complete check ──────────────────────────────────────────
        total_seconds = self._session.duration_minutes * 60
        if self._elapsed_seconds >= total_seconds:
            self._active = False
            self._session.completed = True
            if self._session.started_at is None:
                self._session.started_at = datetime.now(timezone.utc)
            self._log_completed()
            events.append(MindfulnessEvent(
                kind='session_completed',
                data={
                    'session_id': self._session.session_id,
                    'session_type': self._session.session_type.value,
                },
            ))
            return events

        # ── exercise-specific advancement ────────────────────────────────────
        st = self._session.session_type
        if st == MindfulnessType.BREATHING:
            ev = self._tick_breathing(delta)
            if ev is not None:
                events.append(ev)
        elif st == MindfulnessType.BODY_SCAN:
            ev = self._tick_body_scan(delta)
            if ev is not None:
                events.append(ev)
        elif st == MindfulnessType.GRATITUDE:
            ev = self._tick_gratitude(delta)
            if ev is not None:
                events.append(ev)

        return events

    def get_guidance(self) -> str:
        """Return the current instruction text to display."""
        if not self._active or self._session is None:
            return ''

        st = self._session.session_type
        if st == MindfulnessType.BREATHING:
            return self._guidance_breathing()
        if st == MindfulnessType.BODY_SCAN:
            return self._guidance_body_scan()
        if st == MindfulnessType.GRATITUDE:
            return self._guidance_gratitude()
        if st == MindfulnessType.COUNTDOWN:
            return self._guidance_countdown()
        return ''

    def interrupt(self) -> list[MindfulnessEvent]:
        """End the session early. Returns emitted events."""
        if not self._active or self._session is None:
            return []
        self._active = False
        self._session.completed = True
        return [
            MindfulnessEvent(
                kind='interrupted',
                data={'session_id': self._session.session_id},
            ),
        ]

    def status(self) -> dict[str, Any]:
        """Return a snapshot of the current session state."""
        if not self._active or self._session is None:
            return {'active': False}

        total = self._session.duration_minutes * 60
        remaining = max(0, int(total - self._elapsed_seconds))
        return {
            'active': True,
            'session_id': self._session.session_id,
            'session_type': self._session.session_type.value,
            'duration_minutes': self._session.duration_minutes,
            'elapsed_seconds': int(self._elapsed_seconds),
            'remaining_seconds': remaining,
            'voice_enabled': self._voice_enabled,
        }

    # ── Tick helpers (exercise-specific advancement) ─────────────────────────

    def _tick_breathing(self, delta: float) -> MindfulnessEvent | None:
        """Advance the breathing pattern by *delta* seconds.

        Returns a ``phase_updated`` event when the phase changes.
        """
        pattern = BREATHING_PATTERNS.get(self._pattern_name, BREATHING_PATTERNS['box'])
        self._phase_elapsed += delta

        phase_changed = False
        phase_duration = pattern[self._phase_index][1]
        while self._phase_elapsed >= phase_duration:
            self._phase_elapsed -= phase_duration
            self._phase_index = (self._phase_index + 1) % len(pattern)
            phase_changed = True
            phase_duration = pattern[self._phase_index][1]

        if phase_changed:
            phase_name = pattern[self._phase_index][0]
            return MindfulnessEvent(
                kind='phase_updated',
                data={
                    'phase': phase_name,
                    'phase_index': self._phase_index,
                    'remaining': max(0.0, phase_duration - self._phase_elapsed),
                },
            )
        return None

    def _tick_body_scan(self, delta: float) -> MindfulnessEvent | None:
        """Advance the body scan by *delta* seconds.

        Returns a ``phase_updated`` event when the zone changes.
        """
        total = self._session.duration_minutes * 60  # type: ignore[union-attr]
        zone_duration = total / len(BODY_SCAN_PARTS)
        self._zone_elapsed += delta

        zone_changed = False
        while self._zone_elapsed >= zone_duration and self._zone_index < len(BODY_SCAN_PARTS) - 1:
            self._zone_elapsed -= zone_duration
            self._zone_index += 1
            zone_changed = True

        if zone_changed:
            return MindfulnessEvent(
                kind='phase_updated',
                data={
                    'zone': BODY_SCAN_PARTS[self._zone_index],
                    'zone_index': self._zone_index,
                    'remaining': max(0.0, zone_duration - self._zone_elapsed),
                },
            )
        return None

    def _tick_gratitude(self, delta: float) -> MindfulnessEvent | None:
        """Advance the gratitude prompts by *delta* seconds.

        Returns a ``phase_updated`` event when the prompt changes.
        """
        total = self._session.duration_minutes * 60  # type: ignore[union-attr]
        prompt_duration = total / len(GRATITUDE_PROMPTS)
        self._prompt_elapsed += delta

        prompt_changed = False
        while self._prompt_elapsed >= prompt_duration and self._prompt_index < len(GRATITUDE_PROMPTS) - 1:
            self._prompt_elapsed -= prompt_duration
            self._prompt_index += 1
            prompt_changed = True

        if prompt_changed:
            return MindfulnessEvent(
                kind='phase_updated',
                data={
                    'prompt': GRATITUDE_PROMPTS[self._prompt_index],
                    'prompt_index': self._prompt_index,
                    'remaining': max(0.0, prompt_duration - self._prompt_elapsed),
                },
            )
        return None

    # ── Guidance helpers ──────────────────────────────────────────────────────

    def _guidance_breathing(self) -> str:
        pattern = BREATHING_PATTERNS.get(self._pattern_name, BREATHING_PATTERNS['box'])
        phase_name, phase_duration = pattern[self._phase_index]
        remaining = max(0, int(phase_duration - self._phase_elapsed + 1))
        countdown = '... '.join(str(i) for i in range(remaining, 0, -1))
        return f'{phase_name}... {countdown}'

    def _guidance_body_scan(self) -> str:
        part = BODY_SCAN_PARTS[self._zone_index]
        return (
            f'Bring your attention to your {part}. '
            f'Notice any sensations. Relax your {part}...'
        )

    def _guidance_gratitude(self) -> str:
        return GRATITUDE_PROMPTS[self._prompt_index]

    def _guidance_countdown(self) -> str:
        total = self._session.duration_minutes * 60  # type: ignore[union-attr]
        remaining = max(0, int(total - self._elapsed_seconds))
        minutes, seconds = divmod(remaining, 60)
        return f'[{minutes:02d}:{seconds:02d}] remaining'

    # ── Persistence ───────────────────────────────────────────────────────────

    def _log_completed(self) -> None:
        """Append a JSONL record for the completed session."""
        if not self._session:
            return
        MINDFULNESS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {
            'session_id': self._session.session_id,
            'session_type': self._session.session_type.value,
            'duration_minutes': self._session.duration_minutes,
            'started_at': self._session.started_at.isoformat()
            if self._session.started_at
            else None,
            'completed': True,
        }
        with MINDFULNESS_LOG_PATH.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + '\n')