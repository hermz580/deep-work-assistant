"""DWA Mind — the assistant's private memory bank.

DWA Mind is DWA's own second brain: a local, append-mostly store that DWA
writes to whenever it finishes a session or resolves a reminder, and reads
from when it adapts its plan. Raw experience is recorded verbatim; a
consolidation layer distills recurring patterns into learned insights.

Boundaries (by design):
- Only DWA writes here. External agents and processes are readers at most;
  nothing in this module accepts writes from outside the assistant loop.
- All data stays under the assistant's own data directory.
- No network. No telemetry. No secrets.
"""

from __future__ import annotations

import json
import math
import os
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable

MIND_VERSION = 1


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


class DWAMind:
    """Private long-term memory for the Deep Work Assistant.

    Layout under ``base`` (default ``~/.deep_work_assistant/mind``):

    - ``episodes.jsonl``    raw experience records (sessions, reminders, plan changes)
    - ``insights.jsonl``    distilled patterns with confidence and evidence counts
    - ``mind.json``         meta state (version, consolidation cursor)

    Writes happen only through :meth:`record_episode` and
    :meth:`consolidate` — both are called by the assistant itself.
    """

    def __init__(self, base: str | Path | None = None) -> None:
        if base is None:
            base = Path.home() / '.deep_work_assistant' / 'mind'
        self.base = Path(base)
        self.episodes_path = self.base / 'episodes.jsonl'
        self.insights_path = self.base / 'insights.jsonl'
        self.meta_path = self.base / 'mind.json'
        self._lock = threading.Lock()
        self.base.mkdir(parents=True, exist_ok=True)

    # ── meta ─────────────────────────────────────────────────────────────

    def _load_meta(self) -> dict[str, Any]:
        try:
            return json.loads(self.meta_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return {'version': MIND_VERSION, 'consolidated_through': None}

    def _save_meta(self, meta: dict[str, Any]) -> None:
        self.meta_path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False) + '\n', encoding='utf-8'
        )

    # ── raw experience ───────────────────────────────────────────────────

    def record_episode(self, kind: str, data: dict[str, Any]) -> dict[str, Any]:
        """Append one raw experience record. Called by the assistant only."""
        episode = {
            'recorded_at': _utc_iso(datetime.now(timezone.utc)),
            'kind': str(kind),
            'data': dict(data),
        }
        with self._lock:
            with self.episodes_path.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(episode, ensure_ascii=False) + '\n')
        return episode

    def record_session(self, summary_record: dict[str, Any]) -> dict[str, Any]:
        """Mirror one finalized session into memory as a session episode."""
        return self.record_episode('session', summary_record)

    def record_reminder_outcome(
        self, stage: str, response: str, session_id: str | None = None
    ) -> dict[str, Any]:
        return self.record_episode(
            'reminder_outcome',
            {'stage': stage, 'response': response, 'session_id': session_id},
        )

    def record_plan_change(self, plan: dict[str, int], reason: str) -> dict[str, Any]:
        return self.record_episode(
            'plan_change', {'plan': dict(plan), 'reason': reason}
        )

    def load_episodes(self, limit: int | None = 500) -> list[dict[str, Any]]:
        if not self.episodes_path.exists():
            return []
        lines = self.episodes_path.read_text(encoding='utf-8').splitlines()
        if limit is not None and limit > 0:
            lines = lines[-limit:]
        episodes: list[dict[str, Any]] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                episodes.append(json.loads(stripped))
            except json.JSONDecodeError:
                continue
        return episodes

    # ── consolidation: distill raw experience into insights ──────────────

    def consolidate(self, minimum_episodes: int = 3) -> list[dict[str, Any]]:
        """Re-derive insights from raw episodes. Idempotent per episode.

        Runs on the assistant's own schedule (after sessions); each run
        rewrites insights.jsonl from the full episode log so stale
        patterns are replaced, never stacked.
        """
        episodes = self.load_episodes(limit=0)
        new_episodes = [
            e for e in episodes
            if e.get('kind') in ('session', 'reminder_outcome')
        ]
        if len(new_episodes) < minimum_episodes:
            return self.load_insights()

        insights: list[dict[str, Any]] = []
        now = _utc_iso(datetime.now(timezone.utc))

        sessions = [e['data'] for e in episodes if e.get('kind') == 'session']
        reminder_events = [
            e['data'] for e in episodes if e.get('kind') == 'reminder_outcome'
        ]

        # 1. Best focus hours — when do long, human-active sessions happen?
        hour_scores: Counter[int] = Counter()
        hour_counts: Counter[int] = Counter()
        for session in sessions:
            started = str(session.get('started_at', ''))
            try:
                hour = int(started[11:13])
            except (ValueError, IndexError):
                continue
            minutes = float(session.get('human_active_seconds', 0)) / 60.0
            if minutes <= 0:
                continue
            hour_scores[hour] += minutes
            hour_counts[hour] += 1
        if hour_scores:
            best_hour, score = hour_scores.most_common(1)[0]
            total = sum(hour_counts.values())
            insights.append({
                'id': 'best_focus_hour',
                'kind': 'pattern',
                'statement': f'Sessions started around {best_hour:02d}:00 UTC show the most human-active focus time.',
                'best_hour_utc': best_hour,
                'evidence_sessions': total,
                'confidence': min(0.9, 0.3 + total / 30.0),
                'updated_at': now,
            })

        # 2. Reminder adherence by stage — which nudges actually land?
        stage_outcomes: dict[str, Counter[str]] = {}
        for event in reminder_events:
            stage = str(event.get('stage', ''))
            response = str(event.get('response', '')).lower()
            if not stage:
                continue
            stage_outcomes.setdefault(stage, Counter())[response] += 1
        for stage, outcomes in sorted(stage_outcomes.items()):
            total = sum(outcomes.values())
            if total < 2:
                continue
            accepted = outcomes.get('confirmed', 0) + outcomes.get('completed', 0)
            insights.append({
                'id': f'adherence_{stage}',
                'kind': 'pattern',
                'statement': f'{stage.title()} reminders are accepted {accepted}/{total} times ({accepted / total:.0%}).',
                'stage': stage,
                'accepted': accepted,
                'total': total,
                'confidence': min(0.9, 0.4 + total / 20.0),
                'updated_at': now,
            })

        # 3. Flow style — median session length shifts over time.
        durations = [
            float(s.get('duration_seconds', 0)) / 60.0
            for s in sessions
            if float(s.get('duration_seconds', 0)) > 0
        ]
        if len(durations) >= 3:
            recent = durations[-10:]
            insights.append({
                'id': 'flow_style',
                'kind': 'trend',
                'statement': (
                    f'Recent sessions median {median(recent):.0f} minutes '
                    f'across {len(recent)} sessions.'
                ),
                'median_minutes': median(recent),
                'samples': len(recent),
                'confidence': min(0.85, 0.35 + len(recent) / 25.0),
                'updated_at': now,
            })

        # 4. Agent-dominance awareness — how much of tracked time is agent work?
        agent_sessions = sum(1 for s in sessions if s.get('agent_dominated'))
        if sessions:
            share = agent_sessions / len(sessions)
            insights.append({
                'id': 'agent_dominance',
                'kind': 'pattern',
                'statement': (
                    f'{agent_sessions}/{len(sessions)} tracked sessions were agent-dominated '
                    f'({share:.0%}); recovery clocks pause during that time.'
                ),
                'agent_dominated': agent_sessions,
                'total_sessions': len(sessions),
                'confidence': min(0.8, 0.3 + len(sessions) / 40.0),
                'updated_at': now,
            })

        with self._lock:
            payload = ''.join(
                json.dumps(insight, ensure_ascii=False) + '\n' for insight in insights
            )
            self.insights_path.write_text(payload, encoding='utf-8')
            meta = self._load_meta()
            meta['version'] = MIND_VERSION
            meta['consolidated_at'] = now
            meta['episode_count'] = len(episodes)
            self._save_meta(meta)
        return insights

    # ── reading memory ───────────────────────────────────────────────────

    def load_insights(self, limit: int | None = 50) -> list[dict[str, Any]]:
        if not self.insights_path.exists():
            return []
        lines = self.insights_path.read_text(encoding='utf-8').splitlines()
        if limit is not None and limit > 0:
            lines = lines[-limit:]
        insights: list[dict[str, Any]] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                insights.append(json.loads(stripped))
            except json.JSONDecodeError:
                continue
        return insights

    def recall(self, query: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Simple keyword recall over insights, falling back to episodes."""
        if not query:
            return self.load_insights(limit=limit)
        needle = query.strip().lower()
        matches: list[dict[str, Any]] = []
        for insight in self.load_insights(limit=0):
            if needle in json.dumps(insight).lower():
                matches.append(insight)
        if matches:
            return matches[:limit]
        for episode in reversed(self.load_episodes(limit=1000)):
            if needle in json.dumps(episode).lower():
                matches.append(episode)
                if len(matches) >= limit:
                    break
        return matches

    def status(self) -> dict[str, Any]:
        meta = self._load_meta()
        episode_count = len(self.load_episodes(limit=0))
        return {
            'base': str(self.base),
            'version': meta.get('version'),
            'episode_count': episode_count,
            'insight_count': len(self.load_insights(limit=0)),
            'consolidated_at': meta.get('consolidated_at'),
        }
