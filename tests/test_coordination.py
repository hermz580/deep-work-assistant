import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

from deep_work_assistant import coordination as c
from deep_work_assistant.engine import DeepWorkAssistant, ActivitySample, SessionSummary
from deep_work_assistant.pomodoro import PomodoroTimer, PomodoroConfig


def test_lock_released_after_process_killed(tmp_path):
    lock = tmp_path / 'owner.lock'
    code = "from deep_work_assistant.coordination import file_lock; import time; from pathlib import Path; " + \
        f"lock=file_lock(Path({str(lock)!r})); lock.__enter__(); print('ready', flush=True); time.sleep(60)"
    child = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        assert child.stdout.readline().strip() == 'ready'
        try:
            with c.file_lock(lock, timeout=0):
                raise AssertionError('duplicate owner admitted')
        except TimeoutError:
            pass
    finally:
        child.kill()
        child.wait(timeout=5)
    with c.file_lock(lock, timeout=0):
        pass


def test_atomic_timer_parent_and_reentrant_transaction(tmp_path):
    path = tmp_path / 'active.json'
    timer = PomodoroTimer()
    timer.start()
    with c.timer_transaction(path):
        c.atomic_json(path, timer.save_state())
        assert c.active_parent(path) == timer.session.session_id
    assert not list(tmp_path.glob('*.tmp'))


def test_segments_share_parent_without_sharing_record_identity():
    assistant = DeepWorkAssistant(start_streak_required=1)
    assistant.parent_session_id = 'pomo-parent'
    now = datetime.now(timezone.utc)
    ids = []
    for offset in (0, 30):
        assistant.process_sample(ActivitySample(now + timedelta(seconds=offset), 'code', '', 0))
        summary = assistant.finalize_session()
        assert summary.parent_session_id == 'pomo-parent'
        assert summary.human_active_seconds == 0
        restored = SessionSummary.from_record(summary.to_record())
        assert restored.parent_session_id == 'pomo-parent'
        ids.append(summary.session_id)
    assert len(set(ids)) == 2


def test_elapsed_event_survives_restart():
    timer = PomodoroTimer(PomodoroConfig(work_minutes=1, auto_start_breaks=False))
    now = datetime.now(timezone.utc)
    timer.start(now=now)
    assert len(timer.tick(now + timedelta(minutes=2))) == 1
    restored = PomodoroTimer.restore_state(timer.save_state())
    assert restored.tick(now + timedelta(minutes=3)) == []


def test_cli_polling_respects_external_stop(tmp_path):
    env = dict(os.environ, USERPROFILE=str(tmp_path), HOME=str(tmp_path), LOCALAPPDATA=str(tmp_path))
    child = subprocess.Popen([sys.executable, '-m', 'deep_work_assistant', 'pomo', 'start'], env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    path = tmp_path / '.deep_work_assistant' / 'active_pomodoro.json'
    try:
        deadline = time.monotonic() + 10
        while not path.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(.05)
        assert path.exists()
        stopped = subprocess.run([sys.executable, '-m', 'deep_work_assistant', 'pomo', 'stop'],
                                 env=env, capture_output=True, text=True, encoding="utf-8", timeout=10)
        assert stopped.returncode == 0, stopped.stderr
        assert child.wait(timeout=5) == 0
        assert not path.exists()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
