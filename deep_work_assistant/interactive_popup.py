"""Interactive confirmable reminder popups.

Instead of the passive dismiss-only popup, reminders spawn an interactive
tkinter window (as a detached ``pythonw`` subprocess) with two big buttons:
'✅ Yes, done' and '⏭️ Skip'.  The child process writes the choice to a JSONL
response file that the parent process reads asynchronously on its poll loop.

Pure logic (response parsing, skip-counter state, escalation decisions) lives
in module-level functions so it can be unit-tested without any GUI.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RESPONSE_FILE = Path.home() / '.deep_work_assistant' / 'reminder_responses.jsonl'
SKIP_STATE_FILE = Path.home() / '.deep_work_assistant' / 'reminder_skip_state.json'

POPUP_TIMEOUT_SECONDS = 120
ESCALATION_SKIP_THRESHOLD = 2

# Responses that count as "positive" (the human actually did the thing).
POSITIVE_RESPONSES = frozenset({'confirmed', 'completed'})
# Responses that count as a skip for the consecutive-skip counter.
SKIP_RESPONSES = frozenset({'skipped', 'timeout'})


# ── Response file (JSONL) ────────────────────────────────────────────────────

def append_response(
    stage: str,
    response: str,
    path: Path | None = None,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    """Append a single reminder response record to the JSONL response file."""
    path = Path(path or RESPONSE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        'timestamp': (timestamp or datetime.now(timezone.utc)).isoformat(),
        'stage': stage,
        'response': response,
    }
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(record) + '\n')
    return record


def parse_responses(path: Path | None = None) -> list[dict[str, Any]]:
    """Parse the whole response file, silently skipping malformed lines."""
    path = Path(path or RESPONSE_FILE)
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and 'stage' in record and 'response' in record:
            records.append(record)
    return records


class ReminderResponseWatcher:
    """Incrementally reads new responses appended to the response file.

    The parent run-loop calls :meth:`poll` each iteration; only records added
    since the previous poll are returned, so the file can keep growing while
    the assistant runs for days.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or RESPONSE_FILE)
        self._offset = self.path.stat().st_size if self.path.exists() else 0

    def poll(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            self._offset = 0
            return []
        size = self.path.stat().st_size
        if size < self._offset:
            # File was truncated/rotated — start over.
            self._offset = 0
        if size == self._offset:
            return []
        with self.path.open('r', encoding='utf-8') as handle:
            handle.seek(self._offset)
            chunk = handle.read()
            self._offset = handle.tell()
        records: list[dict[str, Any]] = []
        for line in chunk.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and 'stage' in record and 'response' in record:
                records.append(record)
        return records


# ── Consecutive-skip state ───────────────────────────────────────────────────

def load_skip_state(path: Path | None = None) -> dict[str, int]:
    """Load per-stage consecutive-skip counters from the state file."""
    path = Path(path or SKIP_STATE_FILE)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                return {str(k): int(v) for k, v in data.items()}
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
    return {}


def save_skip_state(state: dict[str, int], path: Path | None = None) -> None:
    path = Path(path or SKIP_STATE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding='utf-8')


def record_response(state: dict[str, int], stage: str, response: str) -> dict[str, int]:
    """Return a new skip-state dict updated with *response* for *stage*.

    Skips and timeouts increment the stage's consecutive-skip counter;
    positive responses (confirmed / completed) reset it to zero.
    'overridden' (overlay escape hatch) counts as a skip.
    """
    new_state = dict(state)
    if response in POSITIVE_RESPONSES:
        new_state[stage] = 0
    elif response in SKIP_RESPONSES or response == 'overridden':
        new_state[stage] = new_state.get(stage, 0) + 1
    return new_state


def consecutive_skips(state: dict[str, int], stage: str) -> int:
    return int(state.get(stage, 0))


def should_escalate(
    state: dict[str, int],
    stage: str,
    threshold: int = ESCALATION_SKIP_THRESHOLD,
) -> bool:
    """True when *stage* has been skipped enough times in a row to escalate.

    Currently only the stretch stage escalates (to the fullscreen overlay).
    """
    return stage == 'stretch' and consecutive_skips(state, stage) >= threshold


# ── Popup spawning (GUI — not covered by unit tests) ─────────────────────────

def _stage_question(stage: str) -> str:
    questions = {
        'hydration': '💧 Hydration time — did you drink water?',
        'stretch': '🧘 Stretch time — did you stretch?',
        'eat': '🍎 Meal time — did you eat something?',
    }
    return questions.get(stage, f'⏰ Reminder — did you take care of it? ({stage})')


def build_popup_script(
    stage: str,
    message: str,
    response_file: Path | str | None = None,
    timeout_seconds: int = POPUP_TIMEOUT_SECONDS,
) -> str:
    """Build the source of the detached child process showing the popup.

    The child owns the tkinter window and writes exactly one JSONL record
    (confirmed / skipped / timeout) to the response file before exiting.
    """
    response_path = str(response_file or RESPONSE_FILE)
    question = _stage_question(stage)
    return f"""
import json
import tkinter as tk
from datetime import datetime, timezone
from pathlib import Path

STAGE = {stage!r}
QUESTION = {question!r}
MESSAGE = {message!r}
RESPONSE_FILE = Path({response_path!r})
TIMEOUT_MS = {int(timeout_seconds * 1000)}

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


def respond(response):
    write_response(response)
    root.destroy()


root = tk.Tk()
root.title('Deep Work Assistant')
root.attributes('-topmost', True)
root.resizable(False, False)
root.configure(bg='#111827')

width, height = 420, 210
root.update_idletasks()
try:
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
except Exception:
    screen_width, screen_height = 1280, 720
x = max(20, screen_width - width - 24)
y = max(20, screen_height - height - 80)
root.geometry('%dx%d+%d+%d' % (width, height, x, y))

frame = tk.Frame(root, bg='#111827', padx=18, pady=16)
frame.pack(fill='both', expand=True)

tk.Label(frame, text=QUESTION, font=('Segoe UI', 12, 'bold'), fg='white',
         bg='#111827', wraplength=380, justify='left').pack(anchor='w')
tk.Label(frame, text=MESSAGE, font=('Segoe UI', 9), fg='#9CA3AF',
         bg='#111827', wraplength=380, justify='left').pack(anchor='w', pady=(8, 12))

buttons = tk.Frame(frame, bg='#111827')
buttons.pack(fill='x', pady=(4, 0))
tk.Button(
    buttons, text='✅ Yes, done', font=('Segoe UI', 11, 'bold'),
    bg='#065F46', fg='white', activebackground='#047857', activeforeground='white',
    relief='flat', padx=16, pady=8, command=lambda: respond('confirmed'),
).pack(side='left', expand=True, fill='x', padx=(0, 8))
tk.Button(
    buttons, text='⏭️ Skip', font=('Segoe UI', 11),
    bg='#374151', fg='white', activebackground='#4B5563', activeforeground='white',
    relief='flat', padx=16, pady=8, command=lambda: respond('skipped'),
).pack(side='left', expand=True, fill='x')

root.protocol('WM_DELETE_WINDOW', lambda: respond('skipped'))
root.after(TIMEOUT_MS, lambda: respond('timeout'))
root.mainloop()
write_response('timeout')
""".strip()


def spawn_interactive_popup(
    stage: str,
    message: str,
    response_file: Path | str | None = None,
    timeout_seconds: int = POPUP_TIMEOUT_SECONDS,
) -> bool:
    """Spawn the interactive popup as a detached pythonw subprocess."""
    script = build_popup_script(stage, message, response_file, timeout_seconds)
    return _spawn_detached(script)


def _spawn_detached(script: str) -> bool:
    """Run *script* in a detached, windowless python child process."""
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    interpreter = str(pythonw if pythonw.exists() else Path(sys.executable))
    creationflags = 0
    for flag_name in ('CREATE_NO_WINDOW', 'DETACHED_PROCESS', 'CREATE_NEW_PROCESS_GROUP'):
        creationflags |= int(getattr(subprocess, flag_name, 0))
    kwargs: dict[str, Any] = {
        'stdout': subprocess.DEVNULL,
        'stderr': subprocess.DEVNULL,
    }
    if creationflags:
        kwargs['creationflags'] = creationflags
    try:
        subprocess.Popen([interpreter, '-c', script], **kwargs)
        return True
    except Exception as exc:  # pragma: no cover - environment dependent
        print(f'[interactive-popup-fallback] {exc}')
        return False
