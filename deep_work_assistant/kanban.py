"""Kanban board system — local, SQLite-backed card management.

Columns: Backlog → Ready → In Progress → Review → Done
Each card tracks its status, priority, tags, time spent, and linked app context.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4


# ── Column definitions ───────────────────────────────────────────────────────

COLUMNS = ['backlog', 'ready', 'in_progress', 'review', 'done']
COLUMN_NAMES = {
    'backlog': 'Backlog',
    'ready': 'Ready',
    'in_progress': 'In Progress',
    'review': 'Review',
    'done': 'Done',
}

VALID_TRANSITIONS = {
    'backlog': ['ready'],
    'ready': ['in_progress', 'backlog'],
    'in_progress': ['review', 'ready'],
    'review': ['done', 'in_progress'],
    'done': ['review'],
}


# ── Card model ───────────────────────────────────────────────────────────────

@dataclass
class Card:
    card_id: str
    title: str
    description: str = ''
    column: str = 'backlog'
    priority: int = 0               # 0=normal, 1=high, 2=urgent
    tags: list[str] = field(default_factory=list)
    created_at: float = 0.0         # unix timestamp
    updated_at: float = 0.0
    session_time_seconds: int = 0   # total deep work time logged against this card
    linked_app_pattern: str = ''    # e.g. 'code.exe' to auto-suggest
    linked_window_pattern: str = '' # e.g. 'my-feature' to match window titles

    def to_dict(self) -> dict[str, Any]:
        return {
            'card_id': self.card_id,
            'title': self.title,
            'description': self.description,
            'column': self.column,
            'priority': self.priority,
            'tags': self.tags,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'session_time_seconds': self.session_time_seconds,
            'linked_app_pattern': self.linked_app_pattern,
            'linked_window_pattern': self.linked_window_pattern,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'Card':
        return cls(
            card_id=str(data.get('card_id', '')),
            title=str(data.get('title', '')),
            description=str(data.get('description', '')),
            column=str(data.get('column', 'backlog')),
            priority=int(data.get('priority', 0)),
            tags=list(data.get('tags', [])),
            created_at=float(data.get('created_at', 0.0)),
            updated_at=float(data.get('updated_at', 0.0)),
            session_time_seconds=int(data.get('session_time_seconds', 0)),
            linked_app_pattern=str(data.get('linked_app_pattern', '')),
            linked_window_pattern=str(data.get('linked_window_pattern', '')),
        )

    @property
    def column_label(self) -> str:
        return COLUMN_NAMES.get(self.column, self.column.title())

    @property
    def priority_label(self) -> str:
        return {0: 'normal', 1: 'high', 2: 'urgent'}.get(self.priority, 'normal')


# ── SQLite-backed Board ──────────────────────────────────────────────────────

DEFAULT_BOARD_PATH = Path.home() / '.deep_work_assistant' / 'kanban.db'


class KanbanBoard:
    """Local Kanban board backed by SQLite."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path or DEFAULT_BOARD_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS cards (
                card_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                column_name TEXT DEFAULT 'backlog',
                priority INTEGER DEFAULT 0,
                tags TEXT DEFAULT '[]',
                created_at REAL DEFAULT 0.0,
                updated_at REAL DEFAULT 0.0,
                session_time_seconds INTEGER DEFAULT 0,
                linked_app_pattern TEXT DEFAULT '',
                linked_window_pattern TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS board_metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        self._conn.commit()

    # ── CRUD ──

    def add_card(self, card: Card) -> Card:
        """Insert a new card. Assigns a generated ID if empty."""
        if not card.card_id:
            card.card_id = f'card-{uuid4().hex[:8]}'
        now = time.time()
        card.created_at = card.created_at or now
        card.updated_at = now

        self._conn.execute(
            """INSERT OR REPLACE INTO cards
               (card_id, title, description, column_name, priority, tags,
                created_at, updated_at, session_time_seconds,
                linked_app_pattern, linked_window_pattern)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                card.card_id, card.title, card.description, card.column,
                card.priority, json.dumps(card.tags),
                card.created_at, card.updated_at, card.session_time_seconds,
                card.linked_app_pattern, card.linked_window_pattern,
            ),
        )
        self._conn.commit()
        return card

    def get_card(self, card_id: str) -> Card | None:
        """Fetch a single card by ID."""
        row = self._conn.execute(
            "SELECT * FROM cards WHERE card_id = ?", (card_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_card(row)

    def update_card(self, card: Card) -> Card:
        """Update an existing card's fields."""
        card.updated_at = time.time()
        self._conn.execute(
            """UPDATE cards SET
               title=?, description=?, column_name=?, priority=?, tags=?,
               updated_at=?, session_time_seconds=?,
               linked_app_pattern=?, linked_window_pattern=?
               WHERE card_id=?""",
            (
                card.title, card.description, card.column,
                card.priority, json.dumps(card.tags),
                card.updated_at, card.session_time_seconds,
                card.linked_app_pattern, card.linked_window_pattern,
                card.card_id,
            ),
        )
        self._conn.commit()
        return card

    def delete_card(self, card_id: str) -> bool:
        """Delete a card by ID. Returns True if deleted."""
        cursor = self._conn.execute("DELETE FROM cards WHERE card_id = ?", (card_id,))
        self._conn.commit()
        return cursor.rowcount > 0

    def move_card(self, card_id: str, to_column: str) -> Card | None:
        """Move a card to another column. Validates transition."""
        card = self.get_card(card_id)
        if card is None:
            return None
        to_column = to_column.lower().replace(' ', '_')
        if to_column not in COLUMNS:
            return None

        # Allow any backward move (done→review, review→in_progress, etc.)
        from_idx = COLUMNS.index(card.column)
        to_idx = COLUMNS.index(to_column)

        if to_idx < from_idx:
            # Moving backward is always allowed
            pass
        elif to_idx > from_idx:
            # Moving forward: must be a valid transition
            allowed = VALID_TRANSITIONS.get(card.column, [])
            if to_column not in allowed:
                return None

        card.column = to_column
        return self.update_card(card)

    def list_cards(self, column: str | None = None, tag: str | None = None) -> list[Card]:
        """List cards, optionally filtered by column and/or tag."""
        query = "SELECT * FROM cards"
        params: list[str] = []
        conditions: list[str] = []

        if column:
            conditions.append("column_name = ?")
            params.append(column.lower().replace(' ', '_'))

        if tag:
            conditions.append("tags LIKE ?")
            params.append(f'%{tag}%')

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY priority DESC, updated_at DESC"

        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_card(row) for row in rows]

    def search_cards(self, query: str) -> list[Card]:
        """Search cards by title or description (simple LIKE search)."""
        like = f'%{query}%'
        rows = self._conn.execute(
            """SELECT * FROM cards
               WHERE title LIKE ? OR description LIKE ? OR tags LIKE ?
               ORDER BY priority DESC, updated_at DESC""",
            (like, like, like),
        ).fetchall()
        return [self._row_to_card(row) for row in rows]

    def log_card_session_time(self, card_id: str, seconds: int) -> Card | None:
        """Add session time (seconds) to a card. Returns updated card or None."""
        card = self.get_card(card_id)
        if card is None:
            return None
        card.session_time_seconds += seconds
        return self.update_card(card)

    # ── Board health ──

    def column_counts(self) -> dict[str, int]:
        """Return card count per column."""
        rows = self._conn.execute(
            "SELECT column_name, COUNT(*) as count FROM cards GROUP BY column_name"
        ).fetchall()
        counts = {col: 0 for col in COLUMNS}
        for row in rows:
            counts[row['column_name']] = row['count']
        return counts

    def total_cards(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) as count FROM cards").fetchone()
        return row['count'] if row else 0

    def total_session_time(self) -> int:
        """Return total logged session time across all cards in seconds."""
        row = self._conn.execute(
            "SELECT SUM(session_time_seconds) as total FROM cards"
        ).fetchone()
        return row['total'] if row and row['total'] else 0

    # ── Auto-suggest cards from app/window context ──

    def suggest_cards_for_app(self, app_name: str, window_title: str = '') -> list[Card]:
        """Find cards that match an active app or window pattern."""
        app_name_lower = app_name.lower()
        title_lower = window_title.lower()

        all_cards = self.list_cards()
        matches: list[tuple[Card, int]] = []

        for card in all_cards:
            score = 0
            if card.linked_app_pattern and card.linked_app_pattern in app_name_lower:
                score += 2
            if card.linked_window_pattern and card.linked_window_pattern in title_lower:
                score += 3
            if score > 0 and card.column not in ('done',):
                matches.append((card, score))

        matches.sort(key=lambda x: -x[1])
        return [m[0] for m in matches]

    # ── Metadata ──

    def set_metadata(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO board_metadata (key, value) VALUES (?, ?)",
            (key, value),
        )
        self._conn.commit()

    def get_metadata(self, key: str, default: str = '') -> str:
        row = self._conn.execute(
            "SELECT value FROM board_metadata WHERE key = ?", (key,)
        ).fetchone()
        return row['value'] if row else default

    def close(self) -> None:
        self._conn.close()

    # ── Internal ──

    @staticmethod
    def _row_to_card(row: sqlite3.Row) -> Card:
        return Card(
            card_id=str(row['card_id']),
            title=str(row['title']),
            description=str(row['description']),
            column=str(row['column_name']),
            priority=int(row['priority']),
            tags=json.loads(row['tags']) if row['tags'] else [],
            created_at=float(row['created_at']),
            updated_at=float(row['updated_at']),
            session_time_seconds=int(row['session_time_seconds']),
            linked_app_pattern=str(row['linked_app_pattern']),
            linked_window_pattern=str(row['linked_window_pattern']),
        )


# ── Display helpers ──────────────────────────────────────────────────────────

PRIORITY_ICONS = {0: '•', 1: '↑', 2: '🔥'}


def format_card_list(cards: list[Card]) -> str:
    """Pretty-print a list of cards."""
    if not cards:
        return '  (no cards)'

    lines = []
    for card in cards:
        icon = PRIORITY_ICONS.get(card.priority, '•')
        tag_str = f' [{", ".join(card.tags[:3])}]' if card.tags else ''
        time_str = ''
        if card.session_time_seconds:
            mins = card.session_time_seconds // 60
            time_str = f' ({mins}m logged)'
        lines.append(
            f'  {icon} [{card.column_label:12s}] {card.title}{tag_str}{time_str}'
            f'\n         id: {card.card_id}'
        )
    return '\n'.join(lines)


def format_board(board: KanbanBoard) -> str:
    """Pretty-print the full board."""
    counts = board.column_counts()
    lines = ['📋 Deep Work Kanban Board', '─' * 48]
    for col in COLUMNS:
        label = COLUMN_NAMES[col]
        count = counts.get(col, 0)
        cards = board.list_cards(column=col)
        lines.append(f'\n{label} ({count})')
        if cards:
            lines.append(format_card_list(cards))
        else:
            lines.append('  (empty)')

    total = board.total_session_time()
    if total:
        hours = total // 3600
        minutes = (total % 3600) // 60
        lines.append('\n─' * 48)
        lines.append(f'⏱ Total deep work logged: {hours}h {minutes}m')
    return '\n'.join(lines)