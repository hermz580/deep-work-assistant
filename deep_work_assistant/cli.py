from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .engine import (
    ActivitySample,
    DeepWorkAssistant,
    EngineEvent,
    ReminderPlan,
    build_adaptive_plan,
    format_plan,
    summary_from_record,
)
from .history import HistoryStore
from .obsidian_log import append_session_log
from .notifier import DesktopNotifier
from .runtime import WindowsActivityProbe


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='deep-work-assistant',
        description='Adaptive deep work reminders for Windows',
    )
    sub = parser.add_subparsers(dest='command')

    run = sub.add_parser('run', help='Run the live assistant loop')
    run.add_argument('--history', type=Path, default=HistoryStore.default().path)
    run.add_argument('--poll-interval', type=float, default=15.0)
    run.add_argument('--dry-run', action='store_true')
    run.add_argument('--start-streak', type=int, default=2)
    run.add_argument('--stop-streak', type=int, default=3)
    run.add_argument('--start-idle-threshold', type=int, default=180)
    run.add_argument('--stop-idle-threshold', type=int, default=900)
    run.add_argument('--response-window', type=int, default=10)
    run.add_argument('--obsidian-vault', type=Path)

    plan = sub.add_parser('plan', help='Print the current adaptive reminder plan')
    plan.add_argument('--history', type=Path, default=HistoryStore.default().path)

    sub.add_parser('simulate', help='Run a dry-run scenario to verify the engine')

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or 'run'

    if command == 'plan':
        history = HistoryStore(args.history)
        plan = build_adaptive_plan(history.load_recent())
        print(format_plan(plan))
        return 0

    if command == 'simulate':
        return simulate_scenario()

    history = HistoryStore(args.history)
    return run_live_loop(
        history=history,
        poll_interval=args.poll_interval,
        dry_run=args.dry_run,
        start_streak=args.start_streak,
        stop_streak=args.stop_streak,
        start_idle_threshold=args.start_idle_threshold,
        stop_idle_threshold=args.stop_idle_threshold,
        response_window=args.response_window,
        obsidian_vault=args.obsidian_vault,
    )


def run_live_loop(
    *,
    history: HistoryStore,
    poll_interval: float,
    dry_run: bool,
    start_streak: int,
    stop_streak: int,
    start_idle_threshold: int,
    stop_idle_threshold: int,
    response_window: int,
    obsidian_vault: Path | None,
) -> int:
    notifier = DesktopNotifier(dry_run=dry_run)
    probe = WindowsActivityProbe()
    plan = build_adaptive_plan(history.load_recent())
    assistant = DeepWorkAssistant(
        reminder_plan=plan,
        start_streak_required=start_streak,
        stop_streak_required=stop_streak,
        start_idle_threshold_seconds=start_idle_threshold,
        stop_idle_threshold_seconds=stop_idle_threshold,
        response_window_minutes=response_window,
    )

    print(f'[deep-work-assistant] starting with {format_plan(plan)}')
    print(f'[deep-work-assistant] history file: {history.path}')

    try:
        while True:
            sample = probe.sample()
            events = assistant.process_sample(sample)
            _handle_events(events, notifier, history, assistant, obsidian_vault)
            time.sleep(max(1.0, float(poll_interval)))
    except KeyboardInterrupt:
        summary = assistant.finalize_session(datetime.now(timezone.utc), ended_reason='manual')
        if summary:
            history.append(summary)
            _maybe_write_obsidian_log(obsidian_vault, summary, assistant.reminder_plan)
            print(f'[deep-work-assistant] saved final session {summary.session_id}')
        print('[deep-work-assistant] stopped')
        return 0


def simulate_scenario() -> int:
    notifier = DesktopNotifier(dry_run=True)
    history = HistoryStore(Path.cwd() / '.dwa-simulated-history.jsonl')
    assistant = DeepWorkAssistant(
        reminder_plan=ReminderPlan(60, 120, 180),
        start_streak_required=2,
        stop_streak_required=2,
        start_idle_threshold_seconds=180,
        stop_idle_threshold_seconds=900,
    )

    base = datetime(2026, 6, 2, 9, 0, tzinfo=timezone.utc)
    samples = [
        ActivitySample(base, 'code.exe', 'Focused work', 20),
        ActivitySample(base + timedelta(seconds=15), 'code.exe', 'Focused work', 18),
        ActivitySample(base + timedelta(minutes=59), 'code.exe', 'Focused work', 22),
        ActivitySample(base + timedelta(minutes=60), 'code.exe', 'Focused work', 16),
        ActivitySample(base + timedelta(minutes=120), 'code.exe', 'Focused work', 12),
        ActivitySample(base + timedelta(minutes=180), 'code.exe', 'Focused work', 12),
        ActivitySample(base + timedelta(minutes=190), 'explorer.exe', 'Break', 1200),
        ActivitySample(base + timedelta(minutes=191), 'explorer.exe', 'Break', 1250),
    ]

    print('[deep-work-assistant] simulate scenario start')
    for sample in samples:
        events = assistant.process_sample(sample)
        _handle_events(events, notifier, history, assistant, None)
    summary = assistant.finalize_session(base + timedelta(minutes=191), ended_reason='manual')
    if summary:
        history.append(summary)
        print(f'[deep-work-assistant] simulated session summary: {summary.to_record()}')
    print('[deep-work-assistant] simulate scenario complete')
    return 0


def _handle_events(
    events: list[EngineEvent],
    notifier: DesktopNotifier,
    history: HistoryStore,
    assistant: DeepWorkAssistant,
    obsidian_vault: Path | None,
) -> None:
    for event in events:
        if event.kind == 'session_started':
            print(f"[deep-work-assistant] session started ({event.data['primary_app']})")
        elif event.kind == 'reminder_due':
            notifier.notify(event.data['title'], event.data['message'])
            print(f"[deep-work-assistant] reminder sent: {event.data['stage']} at {event.data['sent_at']}")
        elif event.kind == 'session_ended':
            summary = event.data['summary']
            session_summary = summary_from_record(summary)
            history.append(session_summary)
            assistant.reminder_plan = build_adaptive_plan(history.load_recent())
            _maybe_write_obsidian_log(obsidian_vault, session_summary, assistant.reminder_plan)
            print(f"[deep-work-assistant] session ended; duration={summary['duration_seconds']}s")
            print(f"[deep-work-assistant] adapted plan -> {format_plan(assistant.reminder_plan)}")


def _maybe_write_obsidian_log(
    obsidian_vault: Path | None,
    summary,
    plan: ReminderPlan,
) -> None:
    if obsidian_vault is None:
        return
    try:
        note_path = append_session_log(obsidian_vault, summary, plan)
        print(f'[deep-work-assistant] wrote Obsidian log: {note_path}')
    except Exception as exc:
        print(f'[deep-work-assistant] Obsidian log skipped: {exc}')
