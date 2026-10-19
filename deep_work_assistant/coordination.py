"""Per-profile process ownership and atomic shared timer transactions."""
from __future__ import annotations
import json
import os
import threading
import time
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from uuid import uuid4

_guard = threading.Lock()
_locks = {}
_local = threading.local()

def profile_dir():
    return Path.home() / '.deep_work_assistant'

@contextmanager
def _thread_lock(mutex, timeout):
    if not mutex.acquire(timeout=timeout):
        raise TimeoutError('Another thread owns this operation')
    try:
        yield
    finally:
        mutex.release()

@contextmanager
def file_lock(path, timeout=10):
    path = Path(path).resolve()
    key = str(path).casefold() if os.name == 'nt' else str(path)
    with _guard:
        mutex = _locks.setdefault(key, threading.RLock())
    with _thread_lock(mutex, timeout):
        held = getattr(_local, 'held', set())
        if key in held:
            yield
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a+b') as handle:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b'0')
                handle.flush()
            deadline = time.monotonic() + timeout
            while True:
                try:
                    handle.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f'Already in use: {path.name}')
                    time.sleep(.02)
            _local.held = held | {key}
            try:
                yield
            finally:
                _local.held = held
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('w', encoding='utf-8') as handle:
            json.dump(value, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)

def timer_transaction(path):
    return file_lock(Path(path).with_suffix('.lock'))

def assistant_running():
    try:
        with file_lock(profile_dir() / 'assistant.lock', timeout=0):
            return False
    except TimeoutError:
        return True

def single_assistant(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        lock = file_lock(profile_dir() / 'assistant.lock', timeout=0)
        try:
            lock.__enter__()
        except TimeoutError:
            print('[deep-work-assistant] An assistant is already running for this profile.')
            return 3
        try:
            return function(*args, **kwargs)
        finally:
            lock.__exit__(None, None, None)
    return wrapped

def active_parent(path=None):
    path = path or profile_dir() / 'active_pomodoro.json'
    try:
        with timer_transaction(path):
            state = json.loads(Path(path).read_text(encoding='utf-8'))
            return (state.get('session') or {}).get('session_id') or None
    except (OSError, ValueError, TypeError, AttributeError):
        return None
