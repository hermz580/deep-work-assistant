"""Local advanced work board for Deep Work Assistant.

Runs on localhost only, uses the existing JSONL/SQLite stores, and adds no
third-party web framework dependency.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import mimetypes
import os
import signal
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .analytics import AnalyticsEngine
from .engine import analyze_laptop_use, build_adaptive_plan, load_streak
from .history import HistoryStore
from .kanban import COLUMNS, Card, KanbanBoard
from .pomodoro import PomodoroConfig, PomodoroTimer

UI_DIR = Path(__file__).with_name("ui")
ACTIVE_POMO_PATH = Path.home() / ".deep_work_assistant" / "active_pomodoro.json"


def _to_dict(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, dict):
        return {str(k): _to_dict(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_dict(item) for item in value]
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Path):
        return str(value)
    return value


def _load_timer() -> PomodoroTimer:
    if not ACTIVE_POMO_PATH.exists():
        return PomodoroTimer()
    try:
        state = json.loads(ACTIVE_POMO_PATH.read_text(encoding="utf-8"))
        timer = PomodoroTimer.restore_state(state)
        timer.tick()
        _save_timer(timer)
        return timer
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return PomodoroTimer()


def _save_timer(timer: PomodoroTimer) -> None:
    ACTIVE_POMO_PATH.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE_POMO_PATH.write_text(
        json.dumps(timer.save_state(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _clear_timer() -> None:
    ACTIVE_POMO_PATH.unlink(missing_ok=True)


class AppState:
    """Shared server state for processes and serialized mutations."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.assistant_process: subprocess.Popen[str] | None = None
        self.assistant_started_at: str | None = None

    def assistant_status(self) -> dict[str, Any]:
        process = self.assistant_process
        running = process is not None and process.poll() is None
        if process is not None and not running:
            self.assistant_process = None
        return {
            "running": running,
            "pid": process.pid if running and process else None,
            "started_at": self.assistant_started_at if running else None,
            "managed_by_ui": running,
        }

    def start_assistant(self, options: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if self.assistant_status()["running"]:
                return self.assistant_status()
            command = [sys.executable, "-m", "deep_work_assistant", "run"]
            if bool(options.get("voice")):
                command.append("--voice")
            if bool(options.get("voice_pre_announce")):
                command.append("--voice-pre-announce")
            obsidian_vault = str(options.get("obsidian_vault") or "").strip()
            if obsidian_vault:
                command.extend(["--obsidian-vault", obsidian_vault])
            creationflags = 0
            if os.name == "nt":
                creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            self.assistant_process = subprocess.Popen(
                command,
                cwd=str(Path.cwd()),
                text=True,
                creationflags=creationflags,
            )
            self.assistant_started_at = datetime.now(timezone.utc).isoformat()
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
            return self.assistant_status()


class CommandCenterHandler(BaseHTTPRequestHandler):
    server_version = "DeepWorkBoard/1.0"

    @property
    def app_state(self) -> AppState:
        return self.server.app_state  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[focus-command-center] {self.address_string()} - {fmt % args}")

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/health":
            self._send_json({"ok": True, "service": "focus-command-center"})
            return
        if path == "/api/dashboard":
            self._send_json(self._dashboard_payload())
            return
        if path == "/api/cards":
            self._send_json({"cards": self._cards_payload()})
            return
        if path == "/api/pomodoro":
            with self.app_state.lock:
                self._send_json(_load_timer().status())
            return
        self._serve_static(path)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        payload = self._read_json()
        if payload is None:
            return
        try:
            if path == "/api/cards":
                self._create_card(payload)
                return
            if path.startswith("/api/cards/") and path.endswith("/move"):
                self._move_card(path.split("/")[3], payload)
                return
            if path.startswith("/api/cards/") and path.endswith("/log"):
                self._log_card_time(path.split("/")[3], payload)
                return
            if path == "/api/pomodoro/start":
                self._pomodoro_start(payload)
                return
            if path == "/api/pomodoro/next":
                self._pomodoro_action(lambda timer: timer.transition())
                return
            if path == "/api/pomodoro/skip":
                self._pomodoro_action(lambda timer: timer.skip_break())
                return
            if path == "/api/pomodoro/pause":
                self._pomodoro_action(lambda timer: timer.pause())
                return
            if path == "/api/pomodoro/resume":
                self._pomodoro_action(lambda timer: timer.resume(datetime.now(timezone.utc)))
                return
            if path == "/api/pomodoro/stop":
                self._pomodoro_stop()
                return
            if path == "/api/assistant/start":
                self._send_json(self.app_state.start_assistant(payload))
                return
            if path == "/api/assistant/stop":
                self._send_json(self.app_state.stop_assistant())
                return
        except (ValueError, TypeError) as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self._send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint")

    def do_PATCH(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        payload = self._read_json()
        if payload is None:
            return
        if path.startswith("/api/cards/"):
            self._update_card(path.split("/")[3], payload)
            return
        self._send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint")

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path.startswith("/api/cards/"):
            card_id = path.split("/")[3]
            board = KanbanBoard()
            try:
                deleted = board.delete_card(card_id)
            finally:
                board.close()
            if not deleted:
                self._send_error(HTTPStatus.NOT_FOUND, "Card not found")
                return
            self._send_json({"deleted": True, "card_id": card_id})
            return
        self._send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint")

    def _dashboard_payload(self) -> dict[str, Any]:
        history = HistoryStore.default()
        recent = history.load_recent(100)
        engine = AnalyticsEngine(history)
        board = KanbanBoard()
        try:
            weekly = engine.weekly_report()
            score = engine.productivity_score(days=30)
            trend = engine.focus_trend(days=14)
            categories = engine.category_breakdown(days=30)
            hours = [
                {"hour": hour, "minutes": minutes}
                for hour, minutes in engine.best_hours(days=30)
            ]
            insights = engine.generate_insights(days=30)
            cards = [card.to_dict() for card in board.list_cards()]
            counts = board.column_counts()
            total_card_seconds = board.total_session_time()
        finally:
            board.close()
        plan = build_adaptive_plan(recent)
        profile = analyze_laptop_use(recent)
        streak = load_streak()
        human_seconds = sum(int(getattr(s, "human_active_seconds", 0) or 0) for s in recent)
        agent_seconds = sum(int(getattr(s, "agent_active_seconds", 0) or 0) for s in recent)
        total_activity = human_seconds + agent_seconds
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "weekly": _to_dict(weekly),
            "score": _to_dict(score),
            "trend": trend,
            "categories": categories,
            "best_hours": hours,
            "insights": insights,
            "recent_sessions": [s.to_record() for s in reversed(recent[-20:])],
            "plan": _to_dict(plan),
            "profile": _to_dict(profile),
            "streak": _to_dict(streak),
            "activity_mix": {
                "human_seconds": human_seconds,
                "agent_seconds": agent_seconds,
                "human_ratio": round(human_seconds / total_activity, 3) if total_activity else 0,
                "agent_ratio": round(agent_seconds / total_activity, 3) if total_activity else 0,
            },
            "board": {
                "cards": cards,
                "counts": counts,
                "total_session_seconds": total_card_seconds,
                "columns": COLUMNS,
            },
            "pomodoro": _load_timer().status(),
            "assistant": self.app_state.assistant_status(),
            "paths": {
                "history": str(history.path),
                "kanban": str(Path.home() / ".deep_work_assistant" / "kanban.db"),
                "pomodoro": str(ACTIVE_POMO_PATH),
            },
        }

    def _cards_payload(self) -> list[dict[str, Any]]:
        board = KanbanBoard()
        try:
            return [card.to_dict() for card in board.list_cards()]
        finally:
            board.close()

    def _create_card(self, payload: dict[str, Any]) -> None:
        title = str(payload.get("title") or "").strip()
        if not title:
            raise ValueError("Card title is required")
        column = str(payload.get("column") or "backlog")
        if column not in COLUMNS:
            raise ValueError("Invalid board column")
        card = Card(
            card_id="",
            title=title,
            description=str(payload.get("description") or "").strip(),
            column=column,
            priority=max(0, min(2, int(payload.get("priority", 0)))),
            tags=self._normalise_tags(payload.get("tags")),
            linked_app_pattern=str(payload.get("linked_app_pattern") or "").strip().lower(),
            linked_window_pattern=str(payload.get("linked_window_pattern") or "").strip().lower(),
        )
        board = KanbanBoard()
        try:
            created = board.add_card(card)
        finally:
            board.close()
        self._send_json(created.to_dict(), status=HTTPStatus.CREATED)

    def _update_card(self, card_id: str, payload: dict[str, Any]) -> None:
        board = KanbanBoard()
        try:
            card = board.get_card(card_id)
            if card is None:
                self._send_error(HTTPStatus.NOT_FOUND, "Card not found")
                return
            if "title" in payload:
                title = str(payload["title"] or "").strip()
                if not title:
                    raise ValueError("Card title is required")
                card.title = title
            if "description" in payload:
                card.description = str(payload["description"] or "").strip()
            if "priority" in payload:
                card.priority = max(0, min(2, int(payload["priority"])))
            if "tags" in payload:
                card.tags = self._normalise_tags(payload["tags"])
            if "linked_app_pattern" in payload:
                card.linked_app_pattern = str(payload["linked_app_pattern"] or "").strip().lower()
            if "linked_window_pattern" in payload:
                card.linked_window_pattern = str(payload["linked_window_pattern"] or "").strip().lower()
            updated = board.update_card(card)
        finally:
            board.close()
        self._send_json(updated.to_dict())

    def _move_card(self, card_id: str, payload: dict[str, Any]) -> None:
        destination = str(payload.get("column") or "")
        board = KanbanBoard()
        try:
            current = board.get_card(card_id)
            if current is None:
                self._send_error(HTTPStatus.NOT_FOUND, "Card not found")
                return
            moved = board.move_card(card_id, destination)
        finally:
            board.close()
        if moved is None:
            self._send_error(
                HTTPStatus.CONFLICT,
                f"Move from {current.column} to {destination} is not allowed",
            )
            return
        self._send_json(moved.to_dict())

    def _log_card_time(self, card_id: str, payload: dict[str, Any]) -> None:
        minutes = int(payload.get("minutes", 0))
        if minutes <= 0:
            raise ValueError("Minutes must be greater than zero")
        board = KanbanBoard()
        try:
            card = board.log_card_session_time(card_id, minutes * 60)
        finally:
            board.close()
        if card is None:
            self._send_error(HTTPStatus.NOT_FOUND, "Card not found")
            return
        self._send_json(card.to_dict())

    def _pomodoro_start(self, payload: dict[str, Any]) -> None:
        config = PomodoroConfig(
            work_minutes=int(payload.get("work_minutes", 25)),
            short_break_minutes=int(payload.get("short_break_minutes", 5)),
            long_break_minutes=int(payload.get("long_break_minutes", 15)),
            pomodoros_before_long=int(payload.get("pomodoros_before_long", 4)),
            auto_start_breaks=True,
            auto_start_work=False,
        )
        timer = PomodoroTimer(config)
        timer.start(card_id=str(payload.get("card_id") or "") or None)
        _save_timer(timer)
        self._send_json(timer.status(), status=HTTPStatus.CREATED)

    def _pomodoro_action(self, action: Callable[[PomodoroTimer], Any]) -> None:
        with self.app_state.lock:
            timer = _load_timer()
            action(timer)
            _save_timer(timer)
            self._send_json(timer.status())

    def _pomodoro_stop(self) -> None:
        with self.app_state.lock:
            timer = _load_timer()
            summary = timer.stop()
            card_id = str(summary.get("active_card_id") or "")
            work_minutes = int(summary.get("total_work_minutes") or 0)
            if card_id and work_minutes > 0:
                board = KanbanBoard()
                try:
                    board.log_card_session_time(card_id, work_minutes * 60)
                finally:
                    board.close()
            _clear_timer()
            self._send_json(summary)

    @staticmethod
    def _normalise_tags(value: Any) -> list[str]:
        if isinstance(value, str):
            values = value.split(",")
        elif isinstance(value, list):
            values = value
        else:
            values = []
        return [str(tag).strip() for tag in values if str(tag).strip()][:12]

    def _serve_static(self, request_path: str) -> None:
        relative = "index.html" if request_path in ("", "/") else request_path.lstrip("/")
        if relative not in {"index.html", "app.css", "app.js"}:
            self._send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        file_path = UI_DIR / relative
        if not file_path.exists():
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, f"UI asset missing: {relative}")
            return
        content = file_path.read_bytes()
        mime, _ = mimetypes.guess_type(str(file_path))
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{mime or 'application/octet-stream'}; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def _read_json(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("JSON body must be an object")
            return value
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, f"Invalid JSON: {exc}")
            return None

    def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        content = json.dumps(_to_dict(payload), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self._send_json({"error": message}, status=status)


class CommandCenterServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], state: AppState) -> None:
        super().__init__(server_address, CommandCenterHandler)
        self.app_state = state


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deep Work Assistant Advanced Work Board")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address; localhost by default")
    parser.add_argument("--port", type=int, default=8765, help="Local web port")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser automatically")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    state = AppState()
    server = CommandCenterServer((args.host, args.port), state)
    url = f"http://{args.host}:{args.port}"
    print(f"[focus-command-center] running at {url}")
    print("[focus-command-center] local data remains on this machine")
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
