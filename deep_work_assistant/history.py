from __future__ import annotations

import json
import os
from pathlib import Path

from .engine import SessionSummary


class HistoryStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @classmethod
    def default(cls) -> 'HistoryStore':
        base = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local'))
        return cls(base / 'DeepWorkAssistant' / 'history.jsonl')

    def append(self, summary: SessionSummary) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                for line in reversed(self.path.read_text(encoding='utf-8').splitlines()):
                    if not line.strip():
                        continue
                    if json.loads(line).get('session_id') == summary.session_id:
                        return False
                    break
            except (OSError, json.JSONDecodeError, AttributeError):
                pass
        with self.path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(summary.to_record(), ensure_ascii=False) + '\n')
        return True

    def load_recent(self, limit: int = 12) -> list[SessionSummary]:
        if limit <= 0 or not self.path.exists():
            return []
        lines = self.path.read_text(encoding='utf-8').splitlines()
        records: list[SessionSummary] = []
        for line in lines[-limit:]:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                records.append(SessionSummary.from_record(json.loads(stripped)))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
        return records
