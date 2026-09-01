"""One truthful readiness snapshot shared by the CLI and local web UI."""
from __future__ import annotations

import importlib.util
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .history import HistoryStore
from .kanban import KanbanBoard


DATA_DIR = Path.home() / ".deep_work_assistant"
UI_DIR = Path(__file__).with_name("ui")
REQUIRED_UI_ASSETS = ("index.html", "app.css", "app.v2.css", "app.v2.js")


def _path_status(path: Path) -> dict[str, Any]:
    parent = path.parent
    exists = path.exists()
    writable_target = path if exists else parent
    try:
        writable = writable_target.exists() and os.access(writable_target, os.W_OK)
        size = path.stat().st_size if exists else 0
    except OSError:
        writable = False
        size = 0
    return {
        "path": str(path),
        "exists": exists,
        "writable": writable,
        "size_bytes": size,
    }


def _session_count(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                count += 1
    except OSError:
        return 0
    return count


def collect_diagnostics(
    *,
    settings_path: Path | None = None,
    assistant_log_path: Path | None = None,
    assistant: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a stable, local-only readiness report without opening the camera."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    history = HistoryStore.default()
    board_path = DATA_DIR / "kanban.db"
    pomodoro_path = DATA_DIR / "active_pomodoro.json"
    try:
        history.path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    paths = {
        "history": _path_status(history.path),
        "kanban": _path_status(board_path),
        "pomodoro": _path_status(pomodoro_path),
    }
    if settings_path is not None:
        paths["settings"] = _path_status(settings_path)
    if assistant_log_path is not None:
        paths["assistant_log"] = _path_status(assistant_log_path)

    try:
        board = KanbanBoard()
        try:
            card_count = board.total_cards()
        finally:
            board.close()
    except OSError:
        card_count = 0

    windows_native = platform.system().lower() == "windows"
    psutil_ready = importlib.util.find_spec("psutil") is not None
    edge_tts_ready = importlib.util.find_spec("edge_tts") is not None
    mediapipe_ready = importlib.util.find_spec("mediapipe") is not None
    opencv_ready = importlib.util.find_spec("cv2") is not None
    ui_assets = {
        name: (UI_DIR / name).is_file()
        for name in REQUIRED_UI_ASSETS
    }
    storage_ready = all(item["writable"] for item in paths.values())
    ui_ready = all(ui_assets.values())
    live_ready = windows_native and psutil_ready and storage_ready and ui_ready

    return {
        "schema_version": 1,
        "version": __version__,
        "status": "ready" if live_ready else "limited",
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "windows_activity_capture": windows_native and psutil_ready,
        "voice_available": edge_tts_ready,
        "dependencies": {
            "psutil": psutil_ready,
            "edge_tts": edge_tts_ready,
            "mediapipe": mediapipe_ready,
            "opencv": opencv_ready,
        },
        "checks": {
            "storage_writable": storage_ready,
            "ui_assets": ui_ready,
        },
        "ui_assets": ui_assets,
        "paths": paths,
        "counts": {
            "sessions": _session_count(history.path),
            "cards": card_count,
        },
        "assistant": assistant or {"running": False},
        "learning": {
            "work_patterns": "session-history based",
            "posture_personalization": "not implemented",
        },
        "vision": {
            "installed": mediapipe_ready and opencv_ready,
            "opt_in_required": True,
            "raw_frames_persisted": False,
            "personal_posture_baseline": False,
        },
    }
