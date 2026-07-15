"""Enforced fullscreen stretch overlay.

When the stretch reminder has been skipped ``ESCALATION_SKIP_THRESHOLD``
times in a row, the assistant escalates from the normal interactive popup to
a fullscreen, always-on-top overlay with a 60-second countdown and a couple
of stretch suggestions.

The overlay is spawned as a detached ``pythonw`` subprocess so it never
blocks (or can kill) the assistant loop or any other background process —
it is only a window on top of the screen.

Escape hatch: a small text entry at the bottom — type ``skip`` and press
Enter to close the overlay early (logged as ``overridden``).  Completing the
countdown logs ``completed``, which resets the stretch skip counter.

Multi-monitor: the overlay covers the primary screen via ``-fullscreen``.
Secondary monitors are best-effort only (see README limitation note).

The child writes its outcome ('completed' / 'overridden') to the same JSONL
response file used by interactive popups.
"""

from __future__ import annotations

from pathlib import Path

from .interactive_popup import RESPONSE_FILE, _spawn_detached

OVERLAY_COUNTDOWN_SECONDS = 60


def build_overlay_script(
    stage: str = 'stretch',
    suggestions: list[dict[str, str]] | None = None,
    response_file: Path | str | None = None,
    countdown_seconds: int = OVERLAY_COUNTDOWN_SECONDS,
) -> str:
    """Build the source for the detached overlay child process."""
    response_path = str(response_file or RESPONSE_FILE)
    suggestion_lines: list[str] = []
    for s in suggestions or []:
        name = s.get('name', '')
        duration = s.get('duration', '')
        instruction = s.get('instruction', '')
        suggestion_lines.append(f'🧘 {name} ({duration})\n{instruction}')
    suggestions_text = '\n\n'.join(suggestion_lines)
    return f"""
import json
import tkinter as tk
from datetime import datetime, timezone
from pathlib import Path

STAGE = {stage!r}
RESPONSE_FILE = Path({response_path!r})
COUNTDOWN = {int(countdown_seconds)}
SUGGESTIONS = {suggestions_text!r}

_done = {{'written': False}}


def write_response(response):
    if _done['written']:
        return
    _done['written'] = True
    RESPONSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    record = {{
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'stage': STAGE,
        'response': response,
    }}
    with RESPONSE_FILE.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(record) + '\\n')


def finish(response):
    write_response(response)
    root.destroy()


root = tk.Tk()
root.title('Stretch break')
root.configure(bg='#0B1120')
root.attributes('-topmost', True)
try:
    root.attributes('-fullscreen', True)
except Exception:
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry('%dx%d+0+0' % (sw, sh))

frame = tk.Frame(root, bg='#0B1120')
frame.pack(expand=True)

tk.Label(
    frame, text='Stretch break — 60 seconds',
    font=('Segoe UI', 34, 'bold'), fg='white', bg='#0B1120',
).pack(pady=(0, 12))

countdown_var = tk.StringVar(value=str(COUNTDOWN))
tk.Label(
    frame, textvariable=countdown_var,
    font=('Segoe UI', 72, 'bold'), fg='#34D399', bg='#0B1120',
).pack(pady=(0, 24))

if SUGGESTIONS:
    tk.Label(
        frame, text=SUGGESTIONS, font=('Segoe UI', 14), fg='#D1D5DB',
        bg='#0B1120', justify='center', wraplength=900,
    ).pack(pady=(0, 24))

tk.Label(
    frame, text="Your background work keeps running — this is just for you.",
    font=('Segoe UI', 11), fg='#6B7280', bg='#0B1120',
).pack(pady=(0, 16))

escape_row = tk.Frame(frame, bg='#0B1120')
escape_row.pack()
tk.Label(
    escape_row, text="type 'skip' + Enter to dismiss:",
    font=('Segoe UI', 10), fg='#6B7280', bg='#0B1120',
).pack(side='left', padx=(0, 8))
escape_entry = tk.Entry(
    escape_row, font=('Segoe UI', 11), width=12,
    bg='#1F2937', fg='white', insertbackground='white', relief='flat',
)
escape_entry.pack(side='left')
escape_entry.focus_set()


def on_enter(event=None):
    if escape_entry.get().strip().lower() == 'skip':
        finish('overridden')


escape_entry.bind('<Return>', on_enter)

state = {{'remaining': COUNTDOWN}}


def tick():
    state['remaining'] -= 1
    if state['remaining'] <= 0:
        finish('completed')
        return
    countdown_var.set(str(state['remaining']))
    root.after(1000, tick)


root.after(1000, tick)
root.protocol('WM_DELETE_WINDOW', lambda: finish('overridden'))
root.mainloop()
write_response('completed')
""".strip()


def spawn_stretch_overlay(
    suggestions: list[dict[str, str]] | None = None,
    response_file: Path | str | None = None,
    countdown_seconds: int = OVERLAY_COUNTDOWN_SECONDS,
) -> bool:
    """Spawn the fullscreen stretch overlay as a detached subprocess."""
    script = build_overlay_script(
        stage='stretch',
        suggestions=suggestions,
        response_file=response_file,
        countdown_seconds=countdown_seconds,
    )
    return _spawn_detached(script)
