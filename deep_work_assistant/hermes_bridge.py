"""Read-only bridge to Hermes Companion's signed agent-event log.

Hermes Companion (``hermz580/hermes-companion``) writes one content-free,
HMAC-signed JSON line per Hermes agent lifecycle event to
``<HERMES_HOME>/companion/events.jsonl``. DWA reads that log so it knows
exactly when the agent, not the human, was working.

The rule this enables: DWA credits only human work. Real keyboard/mouse input
is always human. A quiet machine while Hermes is busy is agent time, never
focus time, and never advances streaks or reminder clocks.

Trust and privacy contract (mirrors Hermes Companion's own feed rules):
- read-only: this module never creates the key or the log, never writes;
- fail closed: unsigned, malformed, wrong-key or edited records are ignored;
- content-free: only the event kind, timestamp, category and session id are
  read; records carry no tool arguments, prompts, paths or results.

The signature scheme is copied from ``hermes_companion.core.event_auth`` so
DWA does not depend on the companion package.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AUTH_VERSION = 1
AUTH_ALGORITHM = "hmac-sha256"
KEY_BYTES = 32
_DOMAIN = b"hermes-companion-local-event-v1\x00"

# Events that mean the agent is doing work.
BUSY_EVENTS = frozenset({
    "user.prompt_submitted", "agent.started", "tool.started", "tool.progress",
    "tool.finished", "tool.failed", "approval.approved", "agent.subagent_spawned",
    "agent.subagent_finished", "agent.subagent_failed",
})
# Events that end the agent's busy stretch (done, idle, or waiting on the human).
QUIET_EVENTS = frozenset({
    "task.complete", "agent.idle", "approval.required", "approval.rejected", "idle",
})
# A busy event counts for this long unless a later event says otherwise.
# Hermes sends tool.progress heartbeats during long tools, so this only needs
# to bridge the gap between consecutive events.
BUSY_HOLD_SECONDS = 90.0
READ_LIMIT_BYTES = 512 * 1024
SAFE_CATEGORIES = frozenset({
    "terminal", "web", "browser", "file", "development", "dev", "media", "memory",
    "planning", "delegation", "desktop", "learning", "general",
})


def hermes_home() -> Path:
    """Resolve the Hermes home the same way the companion plugin does."""
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env)
    return Path.home() / "AppData" / "Local" / "hermes"


def default_paths() -> tuple[Path, Path]:
    companion = hermes_home() / "companion"
    return companion / "events.jsonl", companion / "event-auth.key"


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def verify_record(record: Any, key: bytes) -> dict[str, Any] | None:
    """Return the authenticated payload, or None for any invalid record."""
    if not isinstance(record, dict) or not isinstance(key, bytes) or len(key) != KEY_BYTES:
        return None
    auth = record.get("auth")
    if not isinstance(auth, dict) or set(auth) != {"v", "alg", "mac"}:
        return None
    if auth.get("v") != AUTH_VERSION or auth.get("alg") != AUTH_ALGORITHM:
        return None
    supplied = auth.get("mac")
    if not isinstance(supplied, str) or len(supplied) != 64:
        return None
    payload = {name: value for name, value in record.items() if name != "auth"}
    try:
        expected = hmac.new(key, _DOMAIN + _canonical(payload), hashlib.sha256).hexdigest()
    except (TypeError, ValueError):
        return None
    return payload if hmac.compare_digest(supplied, expected) else None


@dataclass(frozen=True)
class AgentEvent:
    ts: float
    kind: str
    category: str
    session_id: str

    @property
    def busy(self) -> bool:
        return self.kind in BUSY_EVENTS

    def to_record(self) -> dict[str, Any]:
        return {
            "at": datetime.fromtimestamp(self.ts, timezone.utc).isoformat(),
            "kind": self.kind,
            "category": self.category,
        }


class HermesBridge:
    """Answers "was the Hermes agent working between these two moments?"."""

    def __init__(self, events_path: Path | None = None, key_path: Path | None = None) -> None:
        default_events, default_key = default_paths()
        self.events_path = Path(events_path) if events_path else default_events
        self.key_path = Path(key_path) if key_path else default_key
        self._events: list[AgentEvent] = []
        self._offset = 0
        self._key: bytes | None = None
        self.rejected = 0

    # ── reading ────────────────────────────────────────────────────
    def connected(self) -> bool:
        return self.events_path.exists() and self.key_path.exists()

    def _load_key(self) -> bytes | None:
        if self._key is None:
            try:
                key = self.key_path.read_bytes()
            except OSError:
                return None
            if len(key) != KEY_BYTES:
                return None
            self._key = key
        return self._key

    def refresh(self) -> int:
        """Read new lines since the last call. Returns how many were accepted."""
        key = self._load_key()
        if key is None:
            return 0
        try:
            size = self.events_path.stat().st_size
        except OSError:
            return 0
        if size < self._offset:  # log rotated or truncated
            self._offset = 0
            self._events.clear()
        if self._offset == 0 and size > READ_LIMIT_BYTES:
            self._offset = size - READ_LIMIT_BYTES
        try:
            with self.events_path.open("rb") as handle:
                handle.seek(self._offset)
                chunk = handle.read(READ_LIMIT_BYTES)
        except OSError:
            return 0
        # Only consume complete lines; a half-written last line waits.
        end = chunk.rfind(b"\n")
        if end < 0:
            return 0
        complete = chunk[: end + 1]
        skip_first = self._offset > 0 and not self._events and self._offset == size - READ_LIMIT_BYTES
        self._offset += len(complete)
        accepted = 0
        for index, raw in enumerate(complete.splitlines()):
            if index == 0 and skip_first:
                continue  # may be a partial line from the tail seek
            event = self._parse(raw, key)
            if event is None:
                self.rejected += 1
                continue
            self._events.append(event)
            accepted += 1
        self._events.sort(key=lambda event: event.ts)
        return accepted

    @staticmethod
    def _parse(raw: bytes, key: bytes) -> AgentEvent | None:
        try:
            record = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        payload = verify_record(record, key)
        if payload is None:
            return None
        kind = payload.get("event")
        ts = payload.get("ts")
        if not isinstance(kind, str) or kind not in BUSY_EVENTS | QUIET_EVENTS:
            return None
        if (isinstance(ts, bool) or not isinstance(ts, (int, float))
                or not math.isfinite(float(ts)) or ts < 0):
            return None
        category = payload.get("category")
        session = payload.get("session_id")
        return AgentEvent(
            ts=float(ts),
            kind=kind,
            category=category if category in SAFE_CATEGORIES else "general",
            session_id=str(session)[:128] if isinstance(session, str) else "",
        )

    # ── questions ──────────────────────────────────────────────────
    def work_between(self, start: datetime, end: datetime) -> dict[str, float]:
        """Agent work inside [start, end] as {category: seconds}.

        This is the shape DWA's engine takes as ``agent_evidence``.
        """
        self.refresh()
        a, b = start.timestamp(), end.timestamp()
        work: dict[str, float] = {}
        if b <= a:
            return work
        for index, event in enumerate(self._events):
            if not event.busy or event.ts > b:
                continue
            following = self._events[index + 1].ts if index + 1 < len(self._events) else float("inf")
            busy_until = min(event.ts + BUSY_HOLD_SECONDS, following)
            overlap = min(b, busy_until) - max(a, event.ts)
            if overlap > 0:
                work[event.category] = work.get(event.category, 0.0) + overlap
        return work

    def busy_seconds(self, start: datetime, end: datetime) -> float:
        """Seconds within [start, end] during which the agent was working."""
        a, b = start.timestamp(), end.timestamp()
        if b <= a:
            return 0.0
        total = 0.0
        for index, event in enumerate(self._events):
            if not event.busy or event.ts > b:
                continue
            following = self._events[index + 1].ts if index + 1 < len(self._events) else float("inf")
            busy_until = min(event.ts + BUSY_HOLD_SECONDS, following)
            overlap = min(b, busy_until) - max(a, event.ts)
            if overlap > 0:
                total += overlap
        return min(total, b - a)

    def busy_between(self, start: datetime, end: datetime, *, min_fraction: float = 0.5) -> bool:
        """True when the agent worked for most of the interval."""
        span = (end - start).total_seconds()
        return span > 0 and self.busy_seconds(start, end) >= span * min_fraction

    def status(self, now: datetime | None = None, limit: int = 12) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        connected = self.connected()
        if connected:
            self.refresh()
        recent = self._events[-limit:]
        last = self._events[-1] if self._events else None
        age = now.timestamp() - last.ts if last is not None else None
        # A future-dated signed record usually means clock skew or a bad
        # timestamp. It is not evidence that Hermes is currently working.
        busy = last is not None and last.busy and 0 <= age <= BUSY_HOLD_SECONDS
        state = (
            "working" if busy else
            "uncertain" if age is not None and age < 0 else
            "quiet" if last is not None else
            "no events yet"
        )
        return {
            "connected": connected,
            "events_path": str(self.events_path),
            "state": state,
            "last_event_at": last.to_record()["at"] if last else None,
            "recent": [event.to_record() for event in reversed(recent)],
            "rejected_records": self.rejected,
            "credited_to_you": False,
        }
