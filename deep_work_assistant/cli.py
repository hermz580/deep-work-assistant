from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .engine import (
    ActivitySample,
    DeepWorkAssistant,
    EngineEvent,
    FocusStreak,
    ReminderPlan,
    advance_streak,
    analyze_laptop_use,
    build_adaptive_plan,
    format_plan,
    load_streak,
    save_streak,
    summary_from_record,
)
from .history import HistoryStore
from .kanban import COLUMNS, KanbanBoard, Card, format_board, format_card_list
from .obsidian_log import append_session_log
from .notifier import DesktopNotifier
from .runtime import WindowsActivityProbe
from .voice import ChainedNotifier, VoiceNotifier
from .pomodoro import PomodoroTimer, PomodoroConfig, PomodoroState, load_history as load_pomo_history
from .mindfulness import MindfulnessCoach, MindfulnessType
from .analytics import AnalyticsEngine, format_weekly_report, format_productivity_score, format_insights, format_trend


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
    run.add_argument('--voice', action='store_true', help='Enable voice TTS reminders (requires edge-tts)')
    run.add_argument('--voice-pre-announce', action='store_true', help='Voice precedes the desktop popup (default: after)')

    plan = sub.add_parser('plan', help='Print the current adaptive reminder plan')
    plan.add_argument('--history', type=Path, default=HistoryStore.default().path)

    sub.add_parser('simulate', help='Run a dry-run scenario to verify the engine')

    # ── Kanban board ──
    board_p = sub.add_parser('board', help='Show the Kanban board')

    # ── Card commands ──
    card_p = sub.add_parser('card', help='Manage Kanban cards')
    card_sub = card_p.add_subparsers(dest='card_command')

    card_add = card_sub.add_parser('add', help='Add a new card')
    card_add.add_argument('title', help='Card title')
    card_add.add_argument('--desc', '-d', default='', help='Card description')
    card_add.add_argument('--priority', '-p', type=int, default=0, choices=[0, 1, 2],
                          help='Priority: 0=normal, 1=high, 2=urgent')
    card_add.add_argument('--column', '-c', default='backlog',
                          choices=COLUMNS, help='Starting column')
    card_add.add_argument('--tags', '-t', nargs='*', default=[], help='Tags')
    card_add.add_argument('--app', help='Linked app pattern (e.g. code.exe)')
    card_add.add_argument('--window', help='Linked window title pattern')

    card_move = card_sub.add_parser('move', help='Move a card to another column')
    card_move.add_argument('card_id', help='Card ID to move')
    card_move.add_argument('to_column', choices=COLUMNS, help='Destination column')

    card_list = card_sub.add_parser('list', help='List cards')
    card_list.add_argument('--column', '-c', choices=COLUMNS, help='Filter by column')
    card_list.add_argument('--tag', '-t', help='Filter by tag')

    card_delete = card_sub.add_parser('delete', help='Delete a card')
    card_delete.add_argument('card_id', help='Card ID to delete')

    card_show = card_sub.add_parser('show', help='View card details')
    card_show.add_argument('card_id', help='Card ID to view')

    card_log = card_sub.add_parser('log', help='Log deep work time to a card')
    card_log.add_argument('card_id', help='Card ID')
    card_log.add_argument('--minutes', '-m', type=int, default=0,
                          help='Minutes to log (default: auto from last session)')

    card_search = card_sub.add_parser('search', help='Search cards')
    card_search.add_argument('query', help='Search query')

    # ── Pomodoro timer ──
    pomo_p = sub.add_parser('pomo', help='Pomodoro timer')
    pomo_sub = pomo_p.add_subparsers(dest='pomo_command')

    pomo_start = pomo_sub.add_parser('start', help='Start a pomodoro session')
    pomo_start.add_argument('--work', type=int, default=25, help='Work minutes')
    pomo_start.add_argument('--short-break', type=int, default=5, help='Short break minutes')
    pomo_start.add_argument('--long-break', type=int, default=15, help='Long break minutes')
    pomo_start.add_argument('--pomodoros', type=int, default=4, help='Pomodoros before long break')
    pomo_start.add_argument('--card', help='Link to a Kanban card ID')

    pomo_sub.add_parser('status', help='Show current pomodoro status')
    pomo_sub.add_parser('next', help='Transition to next phase')
    pomo_sub.add_parser('skip', help='Skip current break')
    pomo_sub.add_parser('stop', help='Stop pomodoro session')
    pomo_sub.add_parser('history', help='Show pomodoro history')

    # ── Mindfulness exercises ──
    mindful_p = sub.add_parser('mindful', help='Mindfulness exercises')
    mindful_sub = mindful_p.add_subparsers(dest='mindful_command')

    breath_p = mindful_sub.add_parser('breathe', help='Breathing exercise')
    breath_p.add_argument('--type', choices=['box', '4-7-8', 'simple'], default='box',
                          help='Breathing pattern')
    breath_p.add_argument('--minutes', type=int, default=3, help='Duration in minutes')

    countdown_p = mindful_sub.add_parser('countdown', help='Silent countdown timer')
    countdown_p.add_argument('--minutes', type=int, default=5, help='Duration in minutes')

    mindful_sub.add_parser('body-scan', help='Guided body scan relaxation')

    gratitude_p = mindful_sub.add_parser('gratitude', help='Gratitude reflection')
    gratitude_p.add_argument('--minutes', type=int, default=3, help='Duration in minutes')

    # ── Analytics ──
    analytics_p = sub.add_parser('analytics', aliases=['stats'],
                                 help='Health insights and analytics')
    analytics_sub = analytics_p.add_subparsers(dest='analytics_command')

    week_p = analytics_sub.add_parser('week', help='Weekly focus report')
    week_p.add_argument('--week', type=int, help='ISO week number (default: current)')
    week_p.add_argument('--year', type=int, help='Year (default: current)')
    week_p.add_argument('--json', action='store_true', help='Output as JSON')
    analytics_sub.add_parser('score', help='Current productivity score')
    trends = analytics_sub.add_parser('trends', help='Focus trends (sparkline)')
    trends.add_argument('--days', type=int, default=30, help='Trailing days')
    hours_p = analytics_sub.add_parser('hours', help='Best focus hours')
    hours_p.add_argument('--days', type=int, default=30, help='Trailing days')
    cats_p = analytics_sub.add_parser('categories', help='Category breakdown')
    cats_p.add_argument('--days', type=int, default=30, help='Trailing days')
    analytics_sub.add_parser('insights', help='Generate intelligence insights')

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or 'run'

    if command == 'plan':
        history = HistoryStore(args.history)
        recent = history.load_recent()
        plan = build_adaptive_plan(recent)
        profile = analyze_laptop_use(recent)
        focus_streak = load_streak()
        print(format_plan(plan))
        print(
            '[deep-work-assistant] laptop profile: '
            f'category={profile.dominant_category}, '
            f'flow={profile.flow_style}, '
            f'reminders={profile.break_response_style}, '
            f'top_apps={", ".join(profile.top_apps) or "none"}, '
            f'suggested={format_plan(profile.suggested_plan)}'
        )
        print(
            '[deep-work-assistant] focus streak: '
            f'{focus_streak.current_streak} days (longest: {focus_streak.longest_streak}, '
            f'today: {focus_streak.daily_session_count} sessions)'
        )
        return 0

    if command == 'simulate':
        return simulate_scenario()

    # ── Kanban board ──
    if command == 'board':
        board = KanbanBoard()
        print(format_board(board))
        return 0

    # ── Kanban cards ──
    if command == 'card':
        return _handle_card_command(args)

    # ── Pomodoro timer ──
    if command == 'pomo':
        return _handle_pomo_command(args)

    # ── Mindfulness exercises ──
    if command == 'mindful':
        return _handle_mindful_command(args) or 0

    # ── Analytics ──
    if command == 'analytics':
        return _handle_analytics_command(args)

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
        voice_enabled=args.voice,
        voice_pre_announce=args.voice_pre_announce,
    )


def _handle_card_command(args: argparse.Namespace) -> int:
    """Handle card subcommands."""
    board = KanbanBoard()
    cmd = args.card_command

    if cmd == 'add':
        card = Card(
            card_id='',
            title=args.title,
            description=args.desc,
            column=args.column,
            priority=args.priority,
            tags=list(args.tags) if args.tags else [],
            linked_app_pattern=args.app or '',
            linked_window_pattern=args.window or '',
        )
        created = board.add_card(card)
        print(f'✅ Card added: {created.card_id}')
        print(f'   "{created.title}" → {created.column_label}')
        return 0

    if cmd == 'move':
        result = board.move_card(args.card_id, args.to_column)
        if result is None:
            card = board.get_card(args.card_id)
            if card is None:
                print(f'❌ Card not found: {args.card_id}')
                return 1
            print(f'❌ Cannot move from "{card.column_label}" to "{args.to_column.replace("_", " ").title()}"')
            return 1
        print(f'✅ Moved: "{result.title}" → {result.column_label}')
        return 0

    if cmd == 'list':
        cards = board.list_cards(column=args.column, tag=args.tag)
        if not cards:
            print('  (no cards found)')
        else:
            print(format_card_list(cards))
        return 0

    if cmd == 'delete':
        if board.delete_card(args.card_id):
            print(f'✅ Deleted card: {args.card_id}')
        else:
            print(f'❌ Card not found: {args.card_id}')
            return 1
        return 0

    if cmd == 'show':
        card = board.get_card(args.card_id)
        if card is None:
            print(f'❌ Card not found: {args.card_id}')
            return 1
        mins = card.session_time_seconds // 60
        print(f'📋 Card: {card.title}')
        print(f'   ID:       {card.card_id}')
        print(f'   Column:   {card.column_label}')
        print(f'   Priority: {card.priority_label}')
        print(f'   Tags:     {", ".join(card.tags) or "(none)"}')
        print(f'   Time:     {mins}m logged')
        if card.description:
            print(f'   Desc:     {card.description}')
        if card.linked_app_pattern:
            print(f'   App:      {card.linked_app_pattern}')
        if card.linked_window_pattern:
            print(f'   Window:   {card.linked_window_pattern}')
        return 0

    if cmd == 'log':
        card = board.get_card(args.card_id)
        if card is None:
            print(f'❌ Card not found: {args.card_id}')
            return 1
        minutes = args.minutes
        if minutes <= 0:
            print('❌ Use --minutes to specify time to log')
            return 1
        seconds = minutes * 60
        updated = board.log_card_session_time(args.card_id, seconds)
        if updated:
            print(f'✅ Logged {minutes}m to "{updated.title}"')
        return 0

    if cmd == 'search':
        cards = board.search_cards(args.query)
        if not cards:
            print(f'  (no cards matching "{args.query}")')
        else:
            print(f'Search results for "{args.query}":')
            print(format_card_list(cards))
        return 0

    print('❌ Unknown card command. Use: add, move, list, delete, search')
    return 0


# ── Analytics command handler ────────────────────────────────────────────

def _handle_analytics_command(args: argparse.Namespace) -> int:
    """Handle analytics subcommands."""
    cmd = args.analytics_command
    engine = AnalyticsEngine()

    if cmd == 'week':
        report = engine.weekly_report(
            year=getattr(args, 'year', None),
            week=getattr(args, 'week', None),
        )
        if getattr(args, 'json', False):
            import json as _json
            import dataclasses
            print(_json.dumps(dataclasses.asdict(report), indent=2))
        else:
            # Enrich the report with full category breakdown for the bar chart
            categories = engine.category_breakdown(days=7)
            print(format_weekly_report(report, categories))
        return 0

    if cmd == 'score':
        score = engine.productivity_score(days=30)
        print(format_productivity_score(score))
        return 0

    if cmd == 'trends':
        trend = engine.focus_trend(days=getattr(args, 'days', 30))
        print(format_trend(trend))
        return 0

    if cmd == 'hours':
        hours = engine.best_hours(days=getattr(args, 'days', 30))
        if not hours:
            print('  (no session data)')
            return 0
        print('  Best focus hours:')
        for hour, minutes in hours:
            print(f'    {hour:02d}:00-{hour + 1:02d}:00  —  {minutes} min')
        return 0

    if cmd == 'categories':
        breakdown = engine.category_breakdown(days=getattr(args, 'days', 30))
        if not breakdown:
            print('  (no session data)')
            return 0
        print('  Category breakdown:')
        total = sum(breakdown.values())
        for cat, minutes in sorted(breakdown.items(), key=lambda x: -x[1]):
            pct = minutes / total * 100 if total else 0
            print(f'    {cat:20s} {_minutes_to_hours_short(minutes):>8s}  ({pct:.0f}%)')
        return 0

    if cmd == 'insights':
        insights = engine.generate_insights(days=30)
        print(format_insights(insights))
        return 0

    print('❌ Unknown analytics command. Use: week, score, trends, hours, categories, insights')
    return 1


def _minutes_to_hours_short(m: int) -> str:
    hours = m // 60
    mins = m % 60
    if hours:
        return f'{hours}h {mins:02d}m'
    return f'{mins}m'


# ── Pomodoro command handler ────────────────────────────────────────────────

ACTIVE_POMO_PATH = Path.home() / '.deep_work_assistant' / 'active_pomodoro.json'


def _save_active_pomo(timer: PomodoroTimer) -> None:
    """Persist the active pomodoro timer state so subcommands can find it."""
    ACTIVE_POMO_PATH.parent.mkdir(parents=True, exist_ok=True)
    state = timer.save_state()
    ACTIVE_POMO_PATH.write_text(
        __import__('json').dumps(state, indent=2), encoding='utf-8'
    )


def _load_active_pomo() -> PomodoroTimer | None:
    """Restore the active pomodoro timer from saved state, or None."""
    if not ACTIVE_POMO_PATH.exists():
        return None
    try:
        import json
        data = json.loads(ACTIVE_POMO_PATH.read_text(encoding='utf-8'))
        if data.get('session') is None:
            return None
        return PomodoroTimer.restore_state(data)
    except Exception:
        return None


def _clear_active_pomo() -> None:
    ACTIVE_POMO_PATH.unlink(missing_ok=True)


def _handle_pomo_command(args: argparse.Namespace) -> int:
    """Handle pomodoro subcommands."""
    cmd = args.pomo_command

    if cmd == 'start':
        config = PomodoroConfig(
            work_minutes=args.work,
            short_break_minutes=args.short_break,
            long_break_minutes=args.long_break,
            pomodoros_before_long=args.pomodoros,
        )
        timer = PomodoroTimer(config)
        timer.start(card_id=args.card)
        _save_active_pomo(timer)

        print(f'🍅 Pomodoro session started (work={args.work}m, break={args.short_break}m)')
        session_id = timer.session.session_id if timer.session else '?'
        print(f'   Session: {session_id}')
        if args.card:
            print(f'   Card:    {args.card}')

        try:
            while timer.session is not None:
                time.sleep(1)
                now = datetime.now(timezone.utc)
                events = timer.tick(now)
                for event in events:
                    _handle_pomo_event(event, timer)
                _save_active_pomo(timer)
        except KeyboardInterrupt:
            summary = timer.stop()
            _clear_active_pomo()
            pomos = summary.get('pomodoros_completed', 0)
            total_m = summary.get('total_work_minutes', 0)
            print(f'\n🍅 Pomodoro session ended — {pomos} pomodoros completed ({total_m}m total)')
        return 0

    if cmd == 'status':
        timer = _load_active_pomo()
        if timer is None:
            print('🍅 No active pomodoro session.')
            print('   Start one with: deep-work-assistant pomo start')
            return 0
        s = timer.status()
        state = s['state']
        remaining = s['remaining_minutes']
        pomo_num = s['current_pomodoro']
        completed = s['pomodoros_completed']
        print(f'🍅 Pomodoro: {state} (pomodoro #{pomo_num}, {completed} completed)')
        if state != 'idle':
            print(f'   Remaining: {remaining:.1f}m')
        return 0

    if cmd == 'next':
        timer = _load_active_pomo()
        if timer is None:
            print('❌ No active pomodoro session.')
            return 1
        events = timer.transition()
        for event in events:
            _handle_pomo_event(event, timer)
        if timer.session:
            _save_active_pomo(timer)
        else:
            _clear_active_pomo()
        return 0

    if cmd == 'skip':
        timer = _load_active_pomo()
        if timer is None:
            print('❌ No active pomodoro session.')
            return 1
        result = timer.skip_break()
        if result is None:
            print('❌ Not in a break phase. Use "next" to transition.')
            return 1
        _handle_pomo_event(result, timer)
        _save_active_pomo(timer)
        return 0

    if cmd == 'stop':
        timer = _load_active_pomo()
        if timer is None:
            print('❌ No active pomodoro session.')
            return 1
        summary = timer.stop()
        _clear_active_pomo()
        pomos = summary.get('pomodoros_completed', 0)
        total_m = summary.get('total_work_minutes', 0)
        print(f'🍅 Pomodoro stopped — {pomos} pomodoros completed ({total_m}m total)')
        return 0

    if cmd == 'history':
        records = load_pomo_history(limit=20)
        if not records:
            print('  (no pomodoro history found)')
            return 0
        print('🍅 Recent pomodoros:')
        for r in reversed(records[-10:]):
            started = r.get('started_at', '?')[:16]
            mins = r.get('work_minutes', '?')
            card = f' [card: {r["card_id"]}]' if r.get('card_id') else ''
            print(f'   #{r.get("pomodoro_number", "?")} — {started} — {mins}m{card}')
        return 0

    print('❌ Unknown pomo command. Use: start, status, next, skip, stop, history')
    return 1


def _handle_pomo_event(event, timer: PomodoroTimer) -> None:
    """Print a message for a pomodoro event."""
    kind = event.kind
    if kind == 'pomodoro_completed':
        s = timer.status()
        print(f'✅ Pomodoro #{s["current_pomodoro"] - 1} completed!')
    elif kind == 'short_break':
        s = timer.status()
        print(f'☕ Short break ({s["remaining_minutes"]:.0f}m) — stretch, hydrate, rest your eyes')
    elif kind == 'long_break':
        s = timer.status()
        print(f'🌟 Long break ({s["remaining_minutes"]:.0f}m) — well earned! Step away from the screen')
    elif kind == 'started':
        s = timer.status()
        print(f'▶️  Work phase #{s["current_pomodoro"]} ({s["remaining_minutes"]:.0f}m remaining)')
    elif kind == 'work_elapsed':
        print('⏰ Work time is up! Press "next" to start your break, or continue working.')
    elif kind == 'break_elapsed':
        print('⏰ Break is over! Press "next" to start the next work phase.')


# ── Mindfulness command handler ─────────────────────────────────────────────

def _handle_mindful_command(args: argparse.Namespace) -> int | None:
    """Handle mindfulness subcommands."""
    cmd = args.mindful_command
    coach = MindfulnessCoach()

    if cmd == 'breathe':
        coach.start(MindfulnessType.BREATHING, duration_minutes=args.minutes)
        pattern = args.type
        coach._pattern_name = pattern  # Set breathing pattern
        print(f'🧘 Breathing exercise: {pattern} ({args.minutes}m)')
        return _run_mindful_loop(coach)

    if cmd == 'countdown':
        coach.start(MindfulnessType.COUNTDOWN, duration_minutes=args.minutes)
        print(f'⏱️  Countdown timer: {args.minutes}m')
        return _run_mindful_loop(coach)

    if cmd == 'body-scan':
        coach.start(MindfulnessType.BODY_SCAN, duration_minutes=5)
        print('🧘 Body scan — 5 minute progressive relaxation')
        return _run_mindful_loop(coach)

    if cmd == 'gratitude':
        coach.start(MindfulnessType.GRATITUDE, duration_minutes=args.minutes)
        print(f'🙏 Gratitude reflection ({args.minutes}m)')
        return _run_mindful_loop(coach)

    print('❌ Unknown mindful command. Use: breathe, countdown, body-scan, gratitude')
    return 1


def _run_mindful_loop(coach: MindfulnessCoach) -> int:
    """Run a mindfulness session loop, printing guidance and handling events."""
    try:
        while coach.status().get('active', False):
            guidance = coach.get_guidance()
            if guidance:
                # Use \r to overwrite the same line for countdown/breathing
                print(f'\r{guidance}', end='', flush=True)
            time.sleep(1)
            now = datetime.now(timezone.utc)
            events = coach.tick(now)
            for event in events:
                if event.kind == 'session_completed':
                    print()
                    print('✅ Session complete. 🙏')
                elif event.kind == 'interrupted':
                    print()
                    print('⏹️  Session interrupted.')
    except KeyboardInterrupt:
        coach.interrupt()
        print('\n⏹️  Session interrupted.')
    return 0


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
    voice_enabled: bool = False,
    voice_pre_announce: bool = False,
) -> int:
    desktop_notifier = DesktopNotifier(dry_run=dry_run)
    voice_notifier = VoiceNotifier(enabled=voice_enabled, dry_run=dry_run)
    notifier = ChainedNotifier(desktop_notifier, voice_notifier, pre_announce=voice_pre_announce)
    probe = WindowsActivityProbe()
    recent = history.load_recent()
    plan = build_adaptive_plan(recent)
    laptop_profile = analyze_laptop_use(recent)
    focus_streak = load_streak()
    board = KanbanBoard()
    active_card_id: str | None = None

    # Track the last active app for card suggestions
    last_app_name = ''
    last_window_title = ''

    assistant = DeepWorkAssistant(
        reminder_plan=plan,
        laptop_use_profile=laptop_profile,
        focus_streak=focus_streak,
        voice_enabled=voice_enabled,
        start_streak_required=start_streak,
        stop_streak_required=stop_streak,
        start_idle_threshold_seconds=start_idle_threshold,
        stop_idle_threshold_seconds=stop_idle_threshold,
        response_window_minutes=response_window,
    )

    print(f'[deep-work-assistant] starting with {format_plan(plan)}')
    print(f'[deep-work-assistant] history file: {history.path}')
    print(f'[deep-work-assistant] focus streak: {focus_streak.current_streak} days (voice={"on" if voice_enabled else "off"})')
    print(f'[deep-work-assistant] kanban board: {board.db_path} ({board.total_cards()} cards)')

    try:
        while True:
            sample = probe.sample()
            events = assistant.process_sample(sample)

            # Track app context for card suggestions
            if sample.process_name:
                last_app_name = sample.process_name
                last_window_title = sample.window_title or ''

            _handle_events(events, notifier, history, assistant, obsidian_vault, board, active_card_id)
            time.sleep(max(1.0, float(poll_interval)))
    except KeyboardInterrupt:
        summary = assistant.finalize_session(datetime.now(timezone.utc), ended_reason='manual')
        if summary:
            history.append(summary)
            save_streak(assistant.focus_streak)
            _maybe_write_obsidian_log(obsidian_vault, summary, assistant.reminder_plan)
            print(f'[deep-work-assistant] saved final session {summary.session_id}')
            print(f'[deep-work-assistant] focus streak: {assistant.focus_streak.current_streak} days')
        print('[deep-work-assistant] stopped')
        return 0


def simulate_scenario() -> int:
    desktop_notifier = DesktopNotifier(dry_run=True)
    voice_notifier = VoiceNotifier(enabled=False, dry_run=True)
    notifier = ChainedNotifier(desktop_notifier, voice_notifier)
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
        _handle_events(events, notifier, history, assistant, None, None, None)
    summary = assistant.finalize_session(base + timedelta(minutes=191), ended_reason='manual')
    if summary:
        history.append(summary)
        print(f'[deep-work-assistant] simulated session summary: {summary.to_record()}')
    print('[deep-work-assistant] simulate scenario complete')
    return 0


def _handle_events(
    events: list[EngineEvent],
    notifier: ChainedNotifier,
    history: HistoryStore,
    assistant: DeepWorkAssistant,
    obsidian_vault: Path | None,
    board: KanbanBoard | None = None,
    active_card_id: str | None = None,
) -> None:
    for event in events:
        if event.kind == 'session_started':
            message = event.data.get('message', f"session started ({event.data['primary_app']})")
            print(f"[deep-work-assistant] session started ({event.data['primary_app']})")
            # Speak the motivational session-start message
            if event.data.get('voice_enabled'):
                notifier.speak_session_start(message)
            # Show card suggestions for this app
            if board and event.data.get('primary_app'):
                app_name = event.data['primary_app']
                suggestions = board.suggest_cards_for_app(app_name)
                if suggestions:
                    print(f"[deep-work-assistant] cards matching '{app_name}':")
                    for s in suggestions[:3]:
                        print(f"   • {s.title} ({s.card_id})")
        elif event.kind == 'reminder_due':
            notifier.notify(event.data['title'], event.data['message'])
            print(f"[deep-work-assistant] reminder sent: {event.data['stage']} at {event.data['sent_at']}")
        elif event.kind == 'session_ended':
            summary = event.data['summary']
            session_summary = summary_from_record(summary)
            history.append(session_summary)
            recent = history.load_recent()
            assistant.reminder_plan = build_adaptive_plan(recent)
            assistant.laptop_use_profile = analyze_laptop_use(recent)
            # Advance streak if session was meaningful
            if session_summary.duration_seconds >= 600:
                assistant.focus_streak = advance_streak(assistant.focus_streak)
                save_streak(assistant.focus_streak)
            # Log session time to Kanban board if active card
            if board and active_card_id and session_summary.duration_seconds >= 60:
                board.log_card_session_time(active_card_id, session_summary.duration_seconds)
            _maybe_write_obsidian_log(obsidian_vault, session_summary, assistant.reminder_plan)
            print(f"[deep-work-assistant] session ended; duration={summary['duration_seconds']}s")
            print(f"[deep-work-assistant] adapted plan -> {format_plan(assistant.reminder_plan)}")
            print(f"[deep-work-assistant] focus streak: {assistant.focus_streak.current_streak} days (longest: {assistant.focus_streak.longest_streak})")
            if board and active_card_id:
                print(f"[deep-work-assistant] time logged to card: {active_card_id}")
            print(
                '[deep-work-assistant] laptop profile -> '
                f'{assistant.laptop_use_profile.dominant_category}, '
                f'{assistant.laptop_use_profile.flow_style}, '
                f'{assistant.laptop_use_profile.break_response_style}'
            )


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
