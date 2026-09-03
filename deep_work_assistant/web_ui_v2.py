"""Expanded local Focus Command Center.

This module layers persistent settings, diagnostics, exports, assistant logs,
and richer dashboard data over the dependency-free server in ``web_ui``.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import platform
import signal
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .history import HistoryStore
from .kanban import KanbanBoard
from .web_ui import (
    ACTIVE_POMO_PATH,
    UI_DIR,
    AppState,
    CommandCenterHandler,
    _to_dict,
)

SETTINGS_PATH = Path.home() / ".deep_work_assistant" / "ui_settings.json"
ASSISTANT_LOG_PATH = Path.home() / ".deep_work_assistant" / "assistant-ui.log"
VISION_EVENTS_PATH = Path.home() / ".deep_work_assistant" / "vision_events.jsonl"

DEFAULT_SETTINGS: dict[str, Any] = {
    "theme": "cinematic",
    "motion": "full",
    "auto_refresh_seconds": 15,
    "default_focus_minutes": 50,
    "short_break_minutes": 5,
    "long_break_minutes": 15,
    "pomodoros_before_long": 4,
    "voice": False,
    "voice_pre_announce": False,
    "obsidian_vault": "",
    "poll_interval": 15.0,
    "start_streak": 2,
    "stop_streak": 3,
    "start_idle_threshold": 180,
    "stop_idle_threshold": 900,
    "response_window": 10,
    "auto_start_assistant": False,
    "hide_done_cards": False,
    "vision_enabled": False,
    "vision_sample_interval": 60.0,
    "vision_frame_interval": 0.4,
}


class SettingsStore:
    """Atomic JSON settings storage with conservative validation."""

    def __init__(self, path: str | Path = SETTINGS_PATH) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def load(self) -> dict[str, Any]:
        with self._lock:
            stored: dict[str, Any] = {}
            if self.path.exists():
                try:
                    value = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(value, dict):
                        stored = value
                except (OSError, json.JSONDecodeError):
                    stored = {}
            return self.validate({**DEFAULT_SETTINGS, **stored})

    def save(self, updates: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            merged = {**self.load(), **updates}
            clean = self.validate(merged)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8")
            temp.replace(self.path)
            return clean

    @staticmethod
    def validate(value: dict[str, Any]) -> dict[str, Any]:
        clean = dict(DEFAULT_SETTINGS)
        clean["theme"] = value.get("theme") if value.get("theme") in {"cinematic", "cosmic", "liquid", "core"} else "cosmic"
        clean["motion"] = value.get("motion") if value.get("motion") in {"full", "reduced"} else "full"
        clean["auto_refresh_seconds"] = _clamp_int(value.get("auto_refresh_seconds"), 5, 300, 15)
        clean["default_focus_minutes"] = _clamp_int(value.get("default_focus_minutes"), 5, 240, 50)
        clean["short_break_minutes"] = _clamp_int(value.get("short_break_minutes"), 1, 60, 5)
        clean["long_break_minutes"] = _clamp_int(value.get("long_break_minutes"), 1, 120, 15)
        clean["pomodoros_before_long"] = _clamp_int(value.get("pomodoros_before_long"), 1, 12, 4)
        clean["voice"] = bool(value.get("voice"))
        clean["voice_pre_announce"] = bool(value.get("voice_pre_announce"))
        clean["obsidian_vault"] = str(value.get("obsidian_vault") or "").strip()
        clean["poll_interval"] = _clamp_float(value.get("poll_interval"), 1.0, 300.0, 15.0)
        clean["start_streak"] = _clamp_int(value.get("start_streak"), 1, 20, 2)
        clean["stop_streak"] = _clamp_int(value.get("stop_streak"), 1, 20, 3)
        clean["start_idle_threshold"] = _clamp_int(value.get("start_idle_threshold"), 0, 3600, 180)
        clean["stop_idle_threshold"] = _clamp_int(value.get("stop_idle_threshold"), 60, 14400, 900)
        clean["response_window"] = _clamp_int(value.get("response_window"), 1, 180, 10)
        clean["auto_start_assistant"] = bool(value.get("auto_start_assistant"))
        clean["hide_done_cards"] = bool(value.get("hide_done_cards"))
        clean["vision_enabled"] = value.get("vision_enabled") is True
        clean["vision_sample_interval"] = _clamp_float(value.get("vision_sample_interval"), 30.0, 60.0, 60.0)
        clean["vision_frame_interval"] = _clamp_float(value.get("vision_frame_interval"), 0.1, 5.0, 0.4)
        return clean


def _clamp_int(value: Any, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _clamp_float(value: Any, low: float, high: float, default: float) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return default


def _finite_metric(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _tail_lines(path: Path, *, max_lines: int = 200, max_bytes: int = 262_144) -> list[str]:
    """Read a bounded tail without loading an ever-growing JSONL file."""
    if max_lines <= 0 or max_bytes <= 0 or not path.exists():
        return []
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            data = handle.read(max_bytes)
    except OSError:
        return []
    if size > max_bytes:
        first_break = data.find(b"\n")
        data = data[first_break + 1 :] if first_break >= 0 else b""
    return data.decode("utf-8", errors="replace").splitlines()[-max_lines:]


def _same_origin_mutation_allowed(
    *,
    content_type: str,
    origin: str,
    host: str,
    requires_json: bool,
) -> bool:
    if requires_json and content_type.split(";", 1)[0].strip().lower() != "application/json":
        return False
    if not origin:
        return True
    parsed = urlparse(origin)
    return parsed.scheme == "http" and parsed.netloc == host


def _vision_payload(
    path: Path = VISION_EVENTS_PATH,
    *,
    enabled: bool,
    assistant_running: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return an allow-listed, metrics-only view of recent vision events."""
    base = {
        "enabled": enabled,
        "privacy": "Metrics only · no images stored",
        "sample_count": 0,
        "latest": None,
        "recent": [],
        "fresh": False,
    }
    if not enabled:
        return {**base, "status": "disabled"}

    records: list[dict[str, Any]] = []
    for line in _tail_lines(path):
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        posture_raw = raw.get("posture")
        posture = None
        if isinstance(posture_raw, dict):
            posture = {
                "fwd_head_deg": _finite_metric(posture_raw.get("fwd_head_deg")),
                "shoulder_tilt_deg": _finite_metric(posture_raw.get("shoulder_tilt_deg")),
                "nose_y_frac": _finite_metric(posture_raw.get("nose_y_frac")),
                "visible": posture_raw.get("visible") if isinstance(posture_raw.get("visible"), bool) else False,
            }
        activity = raw.get("activity")
        error = raw.get("error")
        records.append(
            {
                "captured_at": raw.get("captured_at") if isinstance(raw.get("captured_at"), str) else None,
                "activity": activity if isinstance(activity, str) else "unavailable",
                "motion": _finite_metric(raw.get("motion")),
                "posture_alert": raw.get("posture_alert") is True,
                "posture": posture,
                "error": error[:500] if isinstance(error, str) else None,
            }
        )
    latest = records[-1] if records else None
    current = now or datetime.now(timezone.utc)
    fresh = False
    if latest and latest.get("captured_at"):
        try:
            captured = datetime.fromisoformat(latest["captured_at"])
            if captured.tzinfo is None:
                captured = captured.replace(tzinfo=timezone.utc)
            age = (current.astimezone(timezone.utc) - captured.astimezone(timezone.utc)).total_seconds()
            fresh = 0 <= age <= 180
        except (TypeError, ValueError):
            fresh = False
    if not assistant_running:
        status = "stopped"
    elif latest is None or not fresh:
        status = "waiting"
    elif latest["posture_alert"]:
        status = "attention"
    elif latest["activity"] == "unavailable" or latest["error"]:
        status = "unavailable"
    else:
        status = "active"
    return {
        **base,
        "status": status,
        "sample_count": len(records),
        "latest": latest,
        "recent": records[-12:],
        "fresh": fresh,
    }


class EnhancedAppState(AppState):
    """UI-managed assistant process with persisted launch options and log tail."""

    def __init__(
        self,
        settings: SettingsStore | None = None,
        vision_events_path: Path = VISION_EVENTS_PATH,
    ) -> None:
        super().__init__()
        self.settings_store = settings or SettingsStore()
        self.vision_events_path = vision_events_path
        self.log_path = ASSISTANT_LOG_PATH
        self._log_handle: Any = None
        self.last_command: list[str] = []

    def assistant_status(self) -> dict[str, Any]:
        status = super().assistant_status()
        if not status["running"]:
            self._close_log()
        status.update(
            {
                "log_path": str(self.log_path),
                "command": list(self.last_command),
                "recent_log": self.tail_log(30),
            }
        )
        return status

    def build_assistant_command(self, settings: dict[str, Any]) -> list[str]:
        command = [
            sys.executable,
            "-m",
            "deep_work_assistant",
            "run",
            "--poll-interval",
            str(settings["poll_interval"]),
            "--start-streak",
            str(settings["start_streak"]),
            "--stop-streak",
            str(settings["stop_streak"]),
            "--start-idle-threshold",
            str(settings["start_idle_threshold"]),
            "--stop-idle-threshold",
            str(settings["stop_idle_threshold"]),
            "--response-window",
            str(settings["response_window"]),
        ]
        if settings["voice"] or settings["voice_pre_announce"]:
            command.append("--voice")
        if settings["voice_pre_announce"]:
            command.append("--voice-pre-announce")
        if settings["obsidian_vault"]:
            command.extend(["--obsidian-vault", settings["obsidian_vault"]])
        if settings["vision_enabled"]:
            command.extend(
                [
                    "--vision",
                    "--vision-sample-interval",
                    str(settings["vision_sample_interval"]),
                    "--vision-frame-interval",
                    str(settings["vision_frame_interval"]),
                ]
            )
        return command

    def update_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        before = self.settings_store.load()
        saved = self.settings_store.save(updates)
        runtime_vision_keys = {
            "vision_enabled",
            "vision_sample_interval",
            "vision_frame_interval",
        }
        vision_changed = any(before[key] != saved[key] for key in runtime_vision_keys)
        if vision_changed and self.assistant_status()["running"]:
            self.stop_assistant()
        return saved

    def start_assistant(self, _options: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if super().assistant_status()["running"]:
                return self.assistant_status()
            settings = self.settings_store.load()
            command = self.build_assistant_command(settings)

            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_handle = self.log_path.open("a", encoding="utf-8", buffering=1)
            self._log_handle.write(
                f"\n[{datetime.now(timezone.utc).isoformat()}] UI launch: {' '.join(command)}\n"
            )
            creationflags = 0
            if os.name == "nt":
                creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            env = dict(os.environ)
            env["PYTHONUNBUFFERED"] = "1"
            self.assistant_process = subprocess.Popen(
                command,
                cwd=str(Path.cwd()),
                text=True,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
                env=env,
            )
            self.assistant_started_at = datetime.now(timezone.utc).isoformat()
            self.last_command = command
            return self.assistant_status()

    def stop_assistant(self) -> dict[str, Any]:
        with self.lock:
            process = self.assistant_process
            if process is not None and process.poll() is None:
                if os.name == "nt" and hasattr(signal, "CTRL_BREAK_EVENT"):
                    try:
                        process.send_signal(signal.CTRL_BREAK_EVENT)
                        process.wait(timeout=3)
                    except (OSError, subprocess.TimeoutExpired):
                        process.terminate()
                else:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
            self.assistant_process = None
            self.assistant_started_at = None
            self._close_log()
            return self.assistant_status()

    def tail_log(self, limit: int = 80) -> list[str]:
        if not self.log_path.exists():
            return []
        try:
            lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            return lines[-max(1, limit):]
        except OSError:
            return []

    def _close_log(self) -> None:
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except OSError:
                pass
            self._log_handle = None


class EnhancedHandler(CommandCenterHandler):
    server_version = "DeepWorkCommandCenter/2.0"

    @property
    def enhanced_state(self) -> EnhancedAppState:
        return self.server.app_state  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/settings":
            self._send_json(self.enhanced_state.settings_store.load())
            return
        if path == "/api/diagnostics":
            self._send_json(self._diagnostics_payload())
            return
        if path == "/api/assistant/logs":
            self._send_json({"lines": self.enhanced_state.tail_log(120)})
            return
        if path == "/api/vision":
            settings = self.enhanced_state.settings_store.load()
            assistant = self.enhanced_state.assistant_status()
            self._send_json(
                _vision_payload(
                    self.enhanced_state.vision_events_path,
                    enabled=settings["vision_enabled"],
                    assistant_running=assistant["running"],
                )
            )
            return
        if path == "/api/export/sessions":
            self._send_download("deep-work-sessions.json", self._all_sessions())
            return
        if path == "/api/export/cards":
            board = KanbanBoard()
            try:
                cards = [card.to_dict() for card in board.list_cards()]
            finally:
                board.close()
            self._send_download("deep-work-cards.json", cards)
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if not self._mutation_permitted(requires_json=True):
            return
        super().do_POST()

    def do_PATCH(self) -> None:  # noqa: N802
        if not self._mutation_permitted(requires_json=True):
            return
        path = urlparse(self.path).path
        if path == "/api/settings":
            payload = self._read_json()
            if payload is None:
                return
            self._send_json(self.enhanced_state.update_settings(payload))
            return
        super().do_PATCH()

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._mutation_permitted(requires_json=False):
            return
        super().do_DELETE()

    def _mutation_permitted(self, *, requires_json: bool) -> bool:
        content_type = self.headers.get("Content-Type", "")
        origin = self.headers.get("Origin", "")
        host = self.headers.get("Host", "")
        if requires_json and content_type.split(";", 1)[0].strip().lower() != "application/json":
            self._send_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "JSON Content-Type required")
            return False
        if not _same_origin_mutation_allowed(
            content_type=content_type,
            origin=origin,
            host=host,
            requires_json=requires_json,
        ):
            self._send_error(HTTPStatus.FORBIDDEN, "Cross-origin mutation rejected")
            return False
        return True

    def _dashboard_payload(self) -> dict[str, Any]:
        payload = super()._dashboard_payload()
        sessions = payload.get("recent_sessions", [])
        total_seconds = sum(int(session.get("duration_seconds", 0)) for session in sessions)
        settings = self.enhanced_state.settings_store.load()
        assistant = self.enhanced_state.assistant_status()
        payload.update(
            {
                "settings": settings,
                "vision": _vision_payload(
                    self.enhanced_state.vision_events_path,
                    enabled=settings["vision_enabled"],
                    assistant_running=assistant["running"],
                ),
                "diagnostics": self._diagnostics_payload(),
                "assistant": assistant,
                "session_summary": {
                    "visible_count": len(sessions),
                    "visible_seconds": total_seconds,
                    "average_seconds": round(total_seconds / len(sessions)) if sessions else 0,
                },
            }
        )
        return payload

    def _serve_static(self, request_path: str) -> None:
        relative = "index.html" if request_path in ("", "/") else request_path.lstrip("/")
        if relative in {"app.v2.css", "app.v2.js"}:
            file_path = UI_DIR / relative
            if not file_path.exists():
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, f"UI asset missing: {relative}")
                return
            content = file_path.read_bytes()
            content_type = "text/css" if relative.endswith(".css") else "text/javascript"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)
            return
        super()._serve_static(request_path)

    def _diagnostics_payload(self) -> dict[str, Any]:
        history = HistoryStore.default()
        board_path = Path.home() / ".deep_work_assistant" / "kanban.db"
        paths = {
            "history": _path_status(history.path),
            "kanban": _path_status(board_path),
            "pomodoro": _path_status(ACTIVE_POMO_PATH),
            "settings": _path_status(self.enhanced_state.settings_store.path),
            "assistant_log": _path_status(self.enhanced_state.log_path),
        }
        board = KanbanBoard()
        try:
            card_count = board.total_cards()
        finally:
            board.close()
        session_count = len(self._all_sessions())
        windows_native = platform.system().lower() == "windows"
        psutil_ready = importlib.util.find_spec("psutil") is not None
        edge_tts_ready = importlib.util.find_spec("edge_tts") is not None
        return {
            "status": "ready" if windows_native and psutil_ready else "limited",
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "windows_activity_capture": windows_native and psutil_ready,
            "voice_available": edge_tts_ready,
            "dependencies": {"psutil": psutil_ready, "edge_tts": edge_tts_ready},
            "paths": paths,
            "counts": {"sessions": session_count, "cards": card_count},
            "assistant": self.enhanced_state.assistant_status(),
        }

    @staticmethod
    def _all_sessions() -> list[dict[str, Any]]:
        path = HistoryStore.default().path
        if not path.exists():
            return []
        records: list[dict[str, Any]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    records.append(value)
        except OSError:
            return []
        return records

    def _send_download(self, filename: str, payload: Any) -> None:
        content = json.dumps(
            _to_dict(payload), ensure_ascii=False, indent=2, allow_nan=False
        ).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        try:
            content = json.dumps(
                _to_dict(payload), ensure_ascii=False, allow_nan=False
            ).encode("utf-8")
        except (TypeError, ValueError):
            status = HTTPStatus.INTERNAL_SERVER_ERROR
            content = b'{"error":"Response contained invalid numeric data"}'
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)


class EnhancedServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], state: EnhancedAppState) -> None:
        super().__init__(server_address, EnhancedHandler)
        self.app_state = state


def _path_status(path: Path) -> dict[str, Any]:
    parent = path.parent
    exists = path.exists()
    writable = os.access(path if exists else parent, os.W_OK)
    try:
        size = path.stat().st_size if exists else 0
    except OSError:
        size = 0
    return {"path": str(path), "exists": exists, "writable": writable, "size_bytes": size}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deep Work Assistant Focus Command Center")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address; localhost by default")
    parser.add_argument("--port", type=int, default=8791, help="Local web port")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser automatically")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = SettingsStore()
    state = EnhancedAppState(settings)
    server = EnhancedServer((args.host, args.port), state)
    url = f"http://{args.host}:{args.port}"
    print(f"[focus-command-center] running at {url}")
    print("[focus-command-center] local data remains on this machine")
    if settings.load()["auto_start_assistant"]:
        try:
            state.start_assistant({})
        except OSError as exc:
            print(f"[focus-command-center] assistant auto-start failed: {exc}")
    if not args.no_browser:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        state.stop_assistant()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
