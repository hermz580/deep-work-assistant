from __future__ import annotations

from pathlib import Path

from .engine import ReminderPlan, SessionSummary, format_plan


def append_session_log(
    vault_path: Path,
    summary: SessionSummary,
    plan: ReminderPlan | None = None,
) -> Path:
    vault = Path(vault_path)
    vault.mkdir(parents=True, exist_ok=True)

    ended_local = summary.ended_at.astimezone()
    note_path = vault / f'{ended_local.date().isoformat()}.md'
    section_lines = [
        '',
        f'## Deep Work Assistant session {ended_local.strftime("%H:%M")}',
        f'- session id: `{summary.session_id}`',
        f'- primary app: `{summary.primary_app}`',
        f'- duration: {summary.duration_seconds}s',
        f'- ended reason: {summary.ended_reason}',
        f'- average idle: {summary.average_idle_seconds}s',
    ]

    if plan is not None:
        section_lines.append(f'- reminder plan: {format_plan(plan)}')

    if summary.reminder_outcomes:
        section_lines.append('- reminder outcomes:')
        for reminder in summary.reminder_outcomes:
            stage = reminder.get('stage', 'reminder')
            outcome = reminder.get('outcome', 'unknown')
            section_lines.append(f'  - {stage}: {outcome}')

    section = '\n'.join(section_lines).rstrip() + '\n'
    if note_path.exists() and note_path.stat().st_size > 0:
        existing = note_path.read_text(encoding='utf-8')
        if not existing.endswith('\n'):
            existing += '\n'
        if not existing.endswith('\n\n'):
            existing += '\n'
        note_path.write_text(existing + section, encoding='utf-8')
    else:
        note_path.write_text(f'# {ended_local.date().isoformat()}\n\n{section}', encoding='utf-8')

    return note_path
