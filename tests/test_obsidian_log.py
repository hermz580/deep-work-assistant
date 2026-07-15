from datetime import datetime, timezone
from pathlib import Path

from deep_work_assistant.engine import ReminderPlan, SessionSummary
from deep_work_assistant.obsidian_log import append_session_log


def make_summary(session_id: str = 'session-1') -> SessionSummary:
    return SessionSummary(
        session_id=session_id,
        started_at=datetime(2026, 6, 2, 9, 0, tzinfo=timezone.utc),
        ended_at=datetime(2026, 6, 2, 11, 10, tzinfo=timezone.utc),
        primary_app='code.exe',
        duration_seconds=7800,
        focus_sample_count=10,
        average_idle_seconds=25,
        ended_reason='break',
        reminder_outcomes=[
            {'stage': 'hydration', 'outcome': 'continued'},
            {'stage': 'stretch', 'outcome': 'break'},
        ],
    )


def test_append_session_log_creates_daily_note(tmp_path: Path):
    summary = make_summary()
    expected_time = summary.ended_at.astimezone().strftime('%H:%M')
    note_path = append_session_log(tmp_path, summary, ReminderPlan(64, 128, 192))

    assert note_path == tmp_path / '2026-06-02.md'
    text = note_path.read_text(encoding='utf-8')
    assert '# 2026-06-02' in text
    # New format uses Obsidian callouts instead of raw headings
    assert '> [!success]+' in text
    assert '**primary app:** `code.exe`' in text
    assert '> [!tip]- Reminder plan' in text
    assert 'hydration=64m, stretch=128m, eat=192m' in text
    assert '> [!note]- Reminder outcomes (2)' in text
    assert '**hydration:** continued' in text
    assert '**stretch:** break' in text


def test_append_session_log_appends_to_existing_note(tmp_path: Path):
    note = tmp_path / '2026-06-02.md'
    note.write_text('# 2026-06-02\n\nExisting note\n', encoding='utf-8')

    append_session_log(tmp_path, make_summary('session-2'))
    text = note.read_text(encoding='utf-8')

    assert 'Existing note' in text
    assert 'session-2' in text
    assert text.count('> [!success]+') == 1
