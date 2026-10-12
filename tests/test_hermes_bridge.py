"""Human vs AI attribution from Hermes Companion's signed agent log."""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from deep_work_assistant.engine import ActivitySample, DeepWorkAssistant, SessionSummary, qualifies_for_streak
from deep_work_assistant.hermes_bridge import BUSY_HOLD_SECONDS, HermesBridge, verify_record

KEY = bytes(range(32))
T0 = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)


def _sign(payload: dict, key: bytes = KEY) -> dict:
    """Same envelope as hermes_companion.core.event_auth.sign_record."""
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False).encode()
    mac = hmac.new(key, b"hermes-companion-local-event-v1\x00" + canonical, hashlib.sha256).hexdigest()
    return {**payload, "auth": {"v": 1, "alg": "hmac-sha256", "mac": mac}}


def _bridge(tmp_path: Path, records: list[dict], *, raw_lines: list[str] = ()) -> HermesBridge:
    events = tmp_path / "events.jsonl"
    key = tmp_path / "event-auth.key"
    key.write_bytes(KEY)
    lines = [json.dumps(r) for r in records] + list(raw_lines)
    events.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return HermesBridge(events, key)


def _event(kind: str, minute: float, category: str = "terminal") -> dict:
    return _sign({"event": kind, "ts": (T0 + timedelta(minutes=minute)).timestamp(),
                  "category": category, "session_id": "20261012_090000_abcd",
                  "tool": "terminal", "steps": ["walk"]})


# ── bridge: trust ──────────────────────────────────────────────────

def test_verify_rejects_tampered_unsigned_and_wrong_key() -> None:
    good = _event("tool.started", 0)
    assert verify_record(good, KEY) is not None
    tampered = {**good, "category": "web"}
    assert verify_record(tampered, KEY) is None
    unsigned = {k: v for k, v in good.items() if k != "auth"}
    assert verify_record(unsigned, KEY) is None
    assert verify_record(good, bytes(32)) is None


def test_bridge_ignores_bad_lines_and_never_creates_files(tmp_path: Path) -> None:
    bridge = _bridge(tmp_path, [_event("tool.started", 0), {**_event("tool.started", 1), "ts": 0}],
                     raw_lines=["not json", json.dumps({"event": "tool.started", "ts": 1})])
    assert bridge.refresh() == 1
    assert bridge.rejected == 3
    missing = HermesBridge(tmp_path / "nope" / "events.jsonl", tmp_path / "nope" / "key")
    assert missing.connected() is False
    assert missing.work_between(T0, T0 + timedelta(minutes=1)) == {}
    assert not (tmp_path / "nope").exists()


def test_bridge_reads_only_categories_never_content(tmp_path: Path) -> None:
    record = _sign({"event": "tool.started", "ts": T0.timestamp(), "category": "secret-path/etc",
                    "args": "rm -rf /", "session_id": "s"})
    bridge = _bridge(tmp_path, [record])
    status = bridge.status(now=T0 + timedelta(seconds=10))
    assert status["recent"] == [{"at": T0.isoformat(), "kind": "tool.started", "category": "general"}]
    assert "rm -rf" not in json.dumps(status)
    assert status["state"] == "working"
    assert status["credited_to_you"] is False


# ── bridge: timing ─────────────────────────────────────────────────

def test_work_between_holds_busy_until_next_event(tmp_path: Path) -> None:
    bridge = _bridge(tmp_path, [
        _event("tool.started", 0, "terminal"),
        _event("tool.progress", 1, "terminal"),
        _event("tool.started", 2, "web"),
        _event("task.complete", 2.5, "general"),
    ])
    work = bridge.work_between(T0, T0 + timedelta(minutes=5))
    assert work == {"terminal": 120.0, "web": 30.0}


def test_busy_event_expires_without_follow_up(tmp_path: Path) -> None:
    bridge = _bridge(tmp_path, [_event("tool.started", 0)])
    bridge.refresh()
    span = bridge.busy_seconds(T0, T0 + timedelta(minutes=10))
    assert span == BUSY_HOLD_SECONDS


def test_approval_wait_is_not_agent_work(tmp_path: Path) -> None:
    bridge = _bridge(tmp_path, [_event("tool.started", 0), _event("approval.required", 0.5)])
    assert bridge.work_between(T0, T0 + timedelta(minutes=3)) == {"terminal": 30.0}


def test_refresh_is_incremental_and_waits_for_complete_lines(tmp_path: Path) -> None:
    bridge = _bridge(tmp_path, [_event("tool.started", 0)])
    assert bridge.refresh() == 1
    with bridge.events_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_event("tool.finished", 1))[:20])
    assert bridge.refresh() == 0
    assert bridge.refresh() == 0


# ── engine: who did it ─────────────────────────────────────────────

def _sample(minute: int, *, idle: int, title: str = "editor") -> ActivitySample:
    return ActivitySample(
        captured_at=T0 + timedelta(minutes=minute),
        process_name="code.exe",
        window_title=title,
        idle_seconds=idle,
    )


def _assistant(evidence) -> DeepWorkAssistant:
    return DeepWorkAssistant(start_streak_required=1, stop_streak_required=99,
                             agent_evidence=evidence, max_sample_gap_seconds=3600)


def _hermes_busy(start, end, category="terminal"):
    return {category: (end - start).total_seconds()}


def test_three_way_ledger_you_both_ai() -> None:
    busy_minutes = {2, 3}  # Hermes works during minutes 1->3
    assistant = _assistant(
        lambda start, end: _hermes_busy(start, end) if end.minute in busy_minutes else {}
    )
    assistant.process_sample(_sample(0, idle=0))      # session starts
    assistant.process_sample(_sample(1, idle=5))      # you alone
    assistant.process_sample(_sample(2, idle=5))      # you + Hermes: both
    assistant.process_sample(_sample(3, idle=300))    # no input + Hermes: AI alone
    assistant.process_sample(_sample(4, idle=400))    # no input, nothing: idle
    summary = assistant.finalize_session(T0 + timedelta(minutes=4))
    assert summary.human_active_seconds == 120
    assert summary.together_seconds == 60
    assert summary.agent_active_seconds == 60
    assert summary.agent_work == {"hermes:terminal": 120}


def test_human_input_is_never_reclassified_as_agent() -> None:
    assistant = _assistant(_hermes_busy)
    assistant.process_sample(_sample(0, idle=0))
    for minute in range(1, 6):
        assistant.process_sample(_sample(minute, idle=1))
    summary = assistant.finalize_session(T0 + timedelta(minutes=5))
    assert summary.human_active_seconds == 300
    assert summary.agent_active_seconds == 0
    assert summary.together_seconds == 300


def test_non_hermes_agent_is_still_attributed() -> None:
    assistant = _assistant(lambda start, end: {})
    assistant.process_sample(_sample(0, idle=0, title="a"))
    assistant.process_sample(_sample(1, idle=200, title="b"))
    summary = assistant.finalize_session(T0 + timedelta(minutes=1))
    assert summary.agent_active_seconds == 60
    assert summary.agent_work == {"other-agent": 60}


def test_agent_only_time_never_earns_a_streak() -> None:
    assistant = _assistant(_hermes_busy)
    assistant.process_sample(_sample(0, idle=0))
    for minute in range(1, 31):
        assistant.process_sample(_sample(minute, idle=600 + minute))
    summary = assistant.finalize_session(T0 + timedelta(minutes=30))
    assert summary.human_active_seconds == 0
    assert summary.agent_active_seconds == 1800
    assert qualifies_for_streak(summary) is False


def test_broken_evidence_fails_safe() -> None:
    def broken(start, end):
        raise OSError("log locked")

    assistant = _assistant(broken)
    assistant.process_sample(_sample(0, idle=0))
    assistant.process_sample(_sample(1, idle=5))
    assistant.process_sample(_sample(2, idle=500))
    summary = assistant.finalize_session(T0 + timedelta(minutes=2))
    assert summary.human_active_seconds == 60
    assert summary.agent_active_seconds == 0


def test_summary_record_round_trips_attribution_and_old_records_load() -> None:
    assistant = _assistant(_hermes_busy)
    assistant.process_sample(_sample(0, idle=0))
    assistant.process_sample(_sample(1, idle=5))
    record = assistant.finalize_session(T0 + timedelta(minutes=1)).to_record()
    restored = SessionSummary.from_record(record)
    assert restored.together_seconds == 60
    assert restored.agent_work == {"hermes:terminal": 60}
    legacy = {k: v for k, v in record.items() if k not in {"together_seconds", "agent_work"}}
    old = SessionSummary.from_record(legacy)
    assert old.together_seconds == 0 and old.agent_work == {}
