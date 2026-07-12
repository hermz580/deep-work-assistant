"""Tests for the Pomodoro timer state machine and persistence."""

import json
from datetime import datetime, timedelta, timezone

from deep_work_assistant.pomodoro import (
    PomodoroTimer,
    PomodoroConfig,
    PomodoroState,
    load_history,
)

BASE = datetime(2026, 7, 12, 9, 0, tzinfo=timezone.utc)


class TestPomodoroStateMachine:
    """Tests for the PomodoroTimer state machine transitions."""

    def test_initial_state_is_idle(self):
        timer = PomodoroTimer()
        assert timer.session is None
        status = timer.status()
        assert status['state'] == 'idle'

    def test_start_creates_working_session(self):
        timer = PomodoroTimer(PomodoroConfig(work_minutes=25))
        timer.start(now=BASE)
        assert timer.session is not None
        assert timer.session.state == PomodoroState.WORKING
        assert timer.session.phase_started_at == BASE

    def test_tick_before_elapsed_returns_no_events(self):
        timer = PomodoroTimer(PomodoroConfig(work_minutes=25))
        timer.start(now=BASE)
        events = timer.tick(now=BASE + timedelta(minutes=1))
        assert events == []

    def test_work_completes_after_duration(self):
        """With auto_start_breaks=False, tick at the boundary emits work_elapsed."""
        timer = PomodoroTimer(PomodoroConfig(work_minutes=25, auto_start_breaks=False))
        timer.start(now=BASE)
        events = timer.tick(now=BASE + timedelta(minutes=25))
        kinds = [e.kind for e in events]
        assert 'work_elapsed' in kinds
        assert timer.session.state == PomodoroState.WORKING  # still working

    def test_short_break_transitions_back_to_work(self, tmp_path):
        """With auto_start_breaks + auto_start_work, a full cycle returns to WORKING."""
        import deep_work_assistant.pomodoro as pm
        pm.POMODORO_LOG_PATH = tmp_path / 'pomodoros.jsonl'

        config = PomodoroConfig(
            work_minutes=25,
            short_break_minutes=5,
            auto_start_breaks=True,
            auto_start_work=True,
        )
        timer = PomodoroTimer(config)
        timer.start(now=BASE)

        # Complete work → auto-start short break
        timer.tick(now=BASE + timedelta(minutes=25))
        assert timer.session.state == PomodoroState.SHORT_BREAK

        # Complete short break → auto-start work
        timer.tick(now=BASE + timedelta(minutes=30))
        assert timer.session.state == PomodoroState.WORKING

    def test_long_break_after_four_pomodoros(self, tmp_path):
        """After 4 completed pomodoros, the next break is a LONG_BREAK."""
        import deep_work_assistant.pomodoro as pm
        pm.POMODORO_LOG_PATH = tmp_path / 'pomodoros.jsonl'

        config = PomodoroConfig(
            work_minutes=25,
            short_break_minutes=5,
            long_break_minutes=15,
            pomodoros_before_long=4,
            auto_start_breaks=True,
            auto_start_work=True,
        )
        timer = PomodoroTimer(config)
        timer.start(now=BASE)

        # Cycle through 4 pomodoros: 25 min work + 5 min short break each
        for i in range(3):
            # Complete work → short break
            timer.tick(now=BASE + timedelta(minutes=30 * i + 25))
            # Complete short break → work
            timer.tick(now=BASE + timedelta(minutes=30 * (i + 1)))

        # 4th work phase completes at 9:00 + 30*3 + 25 = 10:55
        # Should transition to LONG_BREAK
        timer.tick(now=BASE + timedelta(minutes=30 * 3 + 25))
        assert timer.session.state == PomodoroState.LONG_BREAK

    def test_manual_transition_from_work_to_break(self, tmp_path):
        """Calling transition() from WORKING completes the pomodoro and starts a break."""
        import deep_work_assistant.pomodoro as pm
        pm.POMODORO_LOG_PATH = tmp_path / 'pomodoros.jsonl'

        timer = PomodoroTimer(PomodoroConfig(work_minutes=25, auto_start_breaks=False))
        timer.start(now=BASE)

        events = timer.transition(now=BASE + timedelta(minutes=10))
        kinds = [e.kind for e in events]
        assert 'pomodoro_completed' in kinds
        assert 'phase_transition' in kinds
        assert timer.session.state == PomodoroState.SHORT_BREAK

    def test_manual_transition_from_break_to_work(self, tmp_path):
        """Calling transition() from a break state starts the next work phase."""
        import deep_work_assistant.pomodoro as pm
        pm.POMODORO_LOG_PATH = tmp_path / 'pomodoros.jsonl'

        timer = PomodoroTimer(PomodoroConfig(work_minutes=25, auto_start_breaks=False))
        timer.start(now=BASE)

        # Move from work → break
        timer.transition(now=BASE + timedelta(minutes=10))
        assert timer.session.state == PomodoroState.SHORT_BREAK

        # Move from break → work
        events = timer.transition(now=BASE + timedelta(minutes=10))
        kinds = [e.kind for e in events]
        assert 'started' in kinds
        assert timer.session.state == PomodoroState.WORKING

    def test_skip_break_goes_to_work(self, tmp_path):
        """skip_break() during a break immediately starts the next work phase."""
        import deep_work_assistant.pomodoro as pm
        pm.POMODORO_LOG_PATH = tmp_path / 'pomodoros.jsonl'

        timer = PomodoroTimer(PomodoroConfig(work_minutes=25, auto_start_breaks=False))
        timer.start(now=BASE)
        timer.transition(now=BASE + timedelta(minutes=10))
        assert timer.session.state == PomodoroState.SHORT_BREAK

        event = timer.skip_break(now=BASE + timedelta(minutes=10))
        assert event is not None
        assert event.kind == 'started'
        assert timer.session.state == PomodoroState.WORKING

    def test_stop_logs_completed_pomodoros(self, tmp_path):
        """stop() returns a summary with the count of completed pomodoros."""
        import deep_work_assistant.pomodoro as pm
        pm.POMODORO_LOG_PATH = tmp_path / 'pomodoros.jsonl'

        timer = PomodoroTimer(PomodoroConfig(work_minutes=25, auto_start_breaks=False))
        timer.start(now=BASE)
        # Complete one pomodoro via transition
        timer.transition(now=BASE + timedelta(minutes=25))

        summary = timer.stop(now=BASE + timedelta(minutes=30))
        assert summary['pomodoros_completed'] == 1
        assert summary['state'] == 'idle'
        assert summary['session_id'] is not None
        assert summary['total_work_minutes'] == 25

    def test_pause_and_resume(self):
        """pause() freezes the timer and resume() restarts it with remaining time."""
        timer = PomodoroTimer(PomodoroConfig(work_minutes=25))
        timer.start(now=BASE)

        pause_event = timer.pause()
        assert pause_event is not None
        assert pause_event.kind == 'paused'
        status = timer.status()
        assert status['is_paused'] is True

        resume_event = timer.resume(now=BASE + timedelta(minutes=5))
        assert resume_event is not None
        assert resume_event.kind == 'resumed'
        status = timer.status()
        assert status['is_paused'] is False
        assert timer.session.state == PomodoroState.WORKING

    def test_link_card(self):
        """link_card() associates the session with a card ID."""
        timer = PomodoroTimer(PomodoroConfig(work_minutes=25))
        timer.start(now=BASE, card_id='card-123')
        assert timer.session.active_card_id == 'card-123'

        # link_card can also update after start
        timer.link_card('card-456')
        assert timer.session.active_card_id == 'card-456'


class TestPomodoroPersistence:
    """Tests for JSONL persistence of completed pomodoros."""

    def test_load_history_returns_ordered_sessions(self, tmp_path):
        """load_history returns records in order, newest last."""
        import deep_work_assistant.pomodoro as pm
        log_path = tmp_path / 'pomodoros.jsonl'
        pm.POMODORO_LOG_PATH = log_path

        # Write three records
        records = [
            {'session_id': 's1', 'pomodoro_number': 1, 'work_minutes': 25},
            {'session_id': 's1', 'pomodoro_number': 2, 'work_minutes': 25},
            {'session_id': 's2', 'pomodoro_number': 1, 'work_minutes': 25},
        ]
        with log_path.open('w') as f:
            for r in records:
                f.write(json.dumps(r) + '\n')

        loaded = load_history()
        assert len(loaded) == 3
        assert loaded[0]['pomodoro_number'] == 1
        assert loaded[1]['pomodoro_number'] == 2
        assert loaded[2]['session_id'] == 's2'

    def test_load_history_missing_file(self, tmp_path):
        """load_history returns an empty list when the log file does not exist."""
        import deep_work_assistant.pomodoro as pm
        pm.POMODORO_LOG_PATH = tmp_path / 'nonexistent.jsonl'

        result = load_history()
        assert result == []


class TestPomodoroEdgeCases:
    """Edge cases and defensive behaviours of the PomodoroTimer."""

    def test_tick_without_session_returns_empty(self):
        timer = PomodoroTimer()
        assert timer.tick(now=BASE) == []

    def test_double_start_resets_session(self):
        timer = PomodoroTimer(PomodoroConfig(work_minutes=25))
        timer.start(now=BASE)
        first_id = timer.session.session_id

        timer.start(now=BASE + timedelta(hours=1))
        assert timer.session.session_id != first_id
        assert timer.session.state == PomodoroState.WORKING

    def test_stop_without_session_returns_idle(self):
        timer = PomodoroTimer()
        result = timer.stop(now=BASE)
        assert result == {'session_id': None, 'state': 'idle'}

    def test_skip_break_in_working_state_returns_none(self):
        timer = PomodoroTimer(PomodoroConfig(work_minutes=25))
        timer.start(now=BASE)
        result = timer.skip_break(now=BASE + timedelta(minutes=5))
        assert result is None
        assert timer.session.state == PomodoroState.WORKING

    def test_transition_in_idle_returns_empty(self):
        timer = PomodoroTimer()
        assert timer.transition(now=BASE) == []