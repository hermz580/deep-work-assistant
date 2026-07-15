from __future__ import annotations

from pathlib import Path
from typing import Any

from .engine import ReminderPlan, SessionSummary, format_plan


def _build_focus_streak_badge(streak: int) -> str:
    """Return a callout for the current focus streak."""
    if streak >= 7:
        return f'> [!success]- 🔥 Focus streak: {streak} days (🔥🔥🔥)'
    elif streak >= 3:
        return f'> [!success]- 🔥 Focus streak: {streak} days'
    elif streak > 0:
        s = 's' if streak > 1 else ''
        return f'> [!info]- Focus streak: {streak} day{s}'
    return ''


def _format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds >= 3600:
        return f'{seconds // 3600}h {(seconds % 3600) // 60}m'
    if seconds >= 60:
        return f'{seconds // 60}m {seconds % 60}s'
    return f'{seconds}s'


def append_session_log(
    vault_path: Path,
    summary: SessionSummary,
    plan: ReminderPlan | None = None,
) -> Path:
    vault = Path(vault_path)
    vault.mkdir(parents=True, exist_ok=True)

    ended_local = summary.ended_at.astimezone()
    note_path = vault / f'{ended_local.date().isoformat()}.md'

    # --- Duration formatting ---
    dur = summary.duration_seconds
    if dur >= 3600:
        dur_str = f'{dur // 3600}h {(dur % 3600) // 60}m'
    elif dur >= 60:
        dur_str = f'{dur // 60}m {dur % 60}s'
    else:
        dur_str = f'{dur}s'

    # --- Session header (colored callout based on duration) ---
    agent_dominated = bool(getattr(summary, 'agent_dominated', False))
    if agent_dominated:
        header = f'> [!note]+ 🤖 Agent Work - {dur_str}'
    elif dur >= 7200:        # 2h+ deep flow
        header = f'> [!success]+ 🌊 Deep Flow - {dur_str}'
    elif dur >= 3600:      # 1-2h good session
        header = f'> [!check]+ ✅ Focus Block - {dur_str}'
    elif dur >= 1800:      # 30-60m
        header = f'> [!info]+ Session - {dur_str}'
    elif dur > 0:
        header = f'> [!warning]+ Short Session - {dur_str}'
    else:
        header = f'> [!warning]+ Interrupted - {dur_str}'

    section_lines = [
            '',
            header,
            f'> **session:** `{summary.session_id}`',
            f'> **primary app:** `{summary.primary_app}`',
            f'> **ended:** {summary.ended_reason}',
    ]

    # --- Human vs agent activity split ---
    human_s = int(getattr(summary, 'human_active_seconds', 0) or 0)
    agent_s = int(getattr(summary, 'agent_active_seconds', 0) or 0)
    if agent_s > 0:
        section_lines.append(f'> **human active:** {_format_duration(human_s)} 🧑')
        section_lines.append(f'> **agent active:** {_format_duration(agent_s)} 🤖')

    # --- Idle indicator ---
    idle = summary.average_idle_seconds
    if idle < 30:
        section_lines.append('> **idle:** minimal 🟢')
    elif idle < 120:
        section_lines.append('> **idle:** moderate 🟡')
    else:
        section_lines.append(f'> **idle:** high ({int(idle)}s avg) 🔴')

    # --- Streak badge ---
    if hasattr(summary, 'focus_streak') and summary.focus_streak:
        badge = _build_focus_streak_badge(summary.focus_streak)
        if badge:
            section_lines.append(f'>\n{badge}')

    # --- Reminder plan ---
    if plan is not None:
        plan_str = format_plan(plan)
        section_lines.append('>\n> [!tip]- Reminder plan')
        for line in plan_str.strip().split('\n'):
            section_lines.append(f'>   {line}')

    # --- Reminder outcomes ---
    if summary.reminder_outcomes:
        outcomes = summary.reminder_outcomes
        section_lines.append(f'>\n> [!note]- Reminder outcomes ({len(outcomes)})')
        for reminder in outcomes:
            stage = reminder.get('stage', 'reminder')
            outcome = reminder.get('outcome', 'unknown')
            # Color-code by outcome type
            icons: dict[str, str] = {
                'completed': '✅',
                'confirmed': '✅',
                'skipped': '⏭️',
                'dismissed': '⏭️',
                'overridden': '⏭️',
                'timeout': '⌛',
                'missed': '❌',
                'snoozed': '⏰',
            }
            icon = icons.get(outcome, '•')
            section_lines.append(f'>   {icon} **{stage}:** {outcome}')

    section_lines.append('')
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