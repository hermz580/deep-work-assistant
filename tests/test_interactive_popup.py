"""Tests for interactive confirmable reminders (pure logic — no GUI)."""

import json
from pathlib import Path

from deep_work_assistant.interactive_popup import (
    ReminderResponseWatcher,
    append_response,
    build_popup_script,
    consecutive_skips,
    load_skip_state,
    parse_responses,
    record_response,
    save_skip_state,
    should_escalate,
)


class TestResponseFile:
    def test_append_and_parse_roundtrip(self, tmp_path: Path):
        path = tmp_path / 'responses.jsonl'
        append_response('hydration', 'confirmed', path=path)
        append_response('stretch', 'skipped', path=path)
        append_response('eat', 'timeout', path=path)

        records = parse_responses(path)
        assert len(records) == 3
        assert records[0]['stage'] == 'hydration'
        assert records[0]['response'] == 'confirmed'
        assert 'timestamp' in records[0]
        assert records[1]['response'] == 'skipped'
        assert records[2]['response'] == 'timeout'

    def test_parse_missing_file_returns_empty(self, tmp_path: Path):
        assert parse_responses(tmp_path / 'nope.jsonl') == []

    def test_parse_skips_malformed_lines(self, tmp_path: Path):
        path = tmp_path / 'responses.jsonl'
        path.write_text(
            'not json\n'
            '{"stage": "hydration", "response": "confirmed", "timestamp": "t"}\n'
            '{"missing": "fields"}\n'
            '\n',
            encoding='utf-8',
        )
        records = parse_responses(path)
        assert len(records) == 1
        assert records[0]['stage'] == 'hydration'

    def test_watcher_returns_only_new_records(self, tmp_path: Path):
        path = tmp_path / 'responses.jsonl'
        append_response('hydration', 'confirmed', path=path)
        watcher = ReminderResponseWatcher(path)
        # Existing records at construction time are not replayed.
        assert watcher.poll() == []

        append_response('stretch', 'skipped', path=path)
        new = watcher.poll()
        assert len(new) == 1
        assert new[0]['stage'] == 'stretch'
        # Nothing new on the next poll.
        assert watcher.poll() == []

    def test_watcher_handles_missing_file(self, tmp_path: Path):
        watcher = ReminderResponseWatcher(tmp_path / 'later.jsonl')
        assert watcher.poll() == []
        append_response('eat', 'timeout', path=tmp_path / 'later.jsonl')
        assert [r['response'] for r in watcher.poll()] == ['timeout']


class TestSkipCounter:
    def test_skips_increment_per_stage(self):
        state: dict[str, int] = {}
        state = record_response(state, 'stretch', 'skipped')
        state = record_response(state, 'stretch', 'timeout')
        state = record_response(state, 'hydration', 'skipped')
        assert consecutive_skips(state, 'stretch') == 2
        assert consecutive_skips(state, 'hydration') == 1
        assert consecutive_skips(state, 'eat') == 0

    def test_confirmed_resets_counter(self):
        state = {'stretch': 3}
        state = record_response(state, 'stretch', 'confirmed')
        assert consecutive_skips(state, 'stretch') == 0

    def test_completed_resets_counter(self):
        # Overlay completion resets the stretch skip counter.
        state = {'stretch': 4}
        state = record_response(state, 'stretch', 'completed')
        assert consecutive_skips(state, 'stretch') == 0

    def test_overridden_counts_as_skip(self):
        state = record_response({}, 'stretch', 'overridden')
        assert consecutive_skips(state, 'stretch') == 1

    def test_unknown_response_leaves_state_alone(self):
        state = record_response({'stretch': 1}, 'stretch', 'weird')
        assert consecutive_skips(state, 'stretch') == 1

    def test_state_roundtrip(self, tmp_path: Path):
        path = tmp_path / 'skip_state.json'
        save_skip_state({'stretch': 2, 'hydration': 0}, path=path)
        assert load_skip_state(path) == {'stretch': 2, 'hydration': 0}

    def test_load_corrupt_state_returns_empty(self, tmp_path: Path):
        path = tmp_path / 'skip_state.json'
        path.write_text('{{corrupt', encoding='utf-8')
        assert load_skip_state(path) == {}


class TestEscalation:
    def test_escalates_at_two_stretch_skips(self):
        assert should_escalate({'stretch': 2}, 'stretch') is True
        assert should_escalate({'stretch': 3}, 'stretch') is True

    def test_no_escalation_below_threshold(self):
        assert should_escalate({'stretch': 1}, 'stretch') is False
        assert should_escalate({}, 'stretch') is False

    def test_only_stretch_escalates(self):
        assert should_escalate({'hydration': 5}, 'hydration') is False
        assert should_escalate({'eat': 5}, 'eat') is False

    def test_escalation_sequence(self):
        """skip, skip -> escalate; complete -> back to normal popup."""
        state: dict[str, int] = {}
        state = record_response(state, 'stretch', 'skipped')
        assert not should_escalate(state, 'stretch')
        state = record_response(state, 'stretch', 'skipped')
        assert should_escalate(state, 'stretch')
        state = record_response(state, 'stretch', 'completed')
        assert not should_escalate(state, 'stretch')


class TestPopupScript:
    def test_script_embeds_stage_and_response_file(self, tmp_path: Path):
        script = build_popup_script('hydration', 'Drink up!', response_file=tmp_path / 'r.jsonl')
        assert "'hydration'" in script
        assert 'did you drink water' in script
        assert 'confirmed' in script
        assert 'skipped' in script
        assert 'timeout' in script
        assert 'r.jsonl' in script
        # Script must be valid Python.
        compile(script, '<popup>', 'exec')

    def test_overlay_script_compiles_and_has_escape_hatch(self, tmp_path: Path):
        from deep_work_assistant.overlay import build_overlay_script

        script = build_overlay_script(
            suggestions=[{'name': 'Neck Roll', 'duration': '30s', 'instruction': 'Roll it.'}],
            response_file=tmp_path / 'r.jsonl',
        )
        assert 'Stretch break' in script
        assert 'overridden' in script
        assert 'completed' in script
        assert 'skip' in script
        assert 'Neck Roll' in script
        compile(script, '<overlay>', 'exec')
