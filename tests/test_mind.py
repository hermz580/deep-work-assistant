"""Tests for DWA Mind — the assistant's private memory bank."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deep_work_assistant.mind import DWAMind


@pytest.fixture()
def mind(tmp_path: Path) -> DWAMind:
    return DWAMind(tmp_path / 'mind')


def _session_record(started='2026-08-30T14:00:00+00:00', **overrides):
    record = {
        'session_id': 's-test',
        'started_at': started,
        'ended_at': '2026-08-30T15:30:00+00:00',
        'primary_app': 'code.exe',
        'duration_seconds': 5400,
        'focus_sample_count': 100,
        'average_idle_seconds': 12,
        'ended_reason': 'manual',
        'human_active_seconds': 4800,
        'agent_active_seconds': 600,
        'agent_dominated': False,
        'reminder_outcomes': [],
    }
    record.update(overrides)
    return record


def test_mind_creates_private_directory(mind: DWAMind):
    assert mind.base.exists()
    status = mind.status()
    assert status['episode_count'] == 0
    assert status['insight_count'] == 0
    assert status['consolidated_at'] is None


def test_record_session_writes_episode(mind: DWAMind):
    episode = mind.record_session(_session_record())
    assert episode['kind'] == 'session'
    assert episode['data']['primary_app'] == 'code.exe'
    assert mind.status()['episode_count'] == 1
    loaded = mind.load_episodes()
    assert loaded[0]['data']['session_id'] == 's-test'


def test_record_reminder_outcome(mind: DWAMind):
    mind.record_reminder_outcome('hydration', 'confirmed', session_id='s-1')
    mind.record_reminder_outcome('hydration', 'confirmed', session_id='s-2')
    episodes = mind.load_episodes()
    assert all(e['kind'] == 'reminder_outcome' for e in episodes)
    assert len(episodes) == 2


def test_consolidate_requires_minimum_evidence(mind: DWAMind):
    mind.record_session(_session_record())
    insights = mind.consolidate(minimum_episodes=3)
    assert insights == []  # too little experience to conclude anything


def test_consolidate_extracts_patterns(mind: DWAMind):
    for i in range(5):
        mind.record_session(
            _session_record(
                session_id=f's-{i}',
                started_at=f'2026-08-{20 + i:02d}T14:00:00+00:00',
                human_active_seconds=3600,
                duration_seconds=4000,
            )
        )
    insights = mind.consolidate()
    by_id = {i['id']: i for i in insights}
    assert 'best_focus_hour' in by_id
    assert by_id['best_focus_hour']['best_hour_utc'] == 14
    assert 'flow_style' in by_id
    assert 'agent_dominance' in by_id
    # persisted
    assert mind.status()['insight_count'] == len(insights)


def test_consolidate_tracks_reminder_adherence(mind: DWAMind):
    for i in range(4):
        mind.record_session(_session_record(session_id=f's-{i}'))
    mind.record_reminder_outcome('hydration', 'confirmed')
    mind.record_reminder_outcome('hydration', 'confirmed')
    mind.record_reminder_outcome('hydration', 'skipped')
    insights = mind.consolidate()
    adherence = [i for i in insights if i['id'] == 'adherence_hydration']
    assert adherence, 'expected hydration adherence insight'
    assert adherence[0]['accepted'] == 2
    assert adherence[0]['total'] == 3


def test_consolidate_is_idempotent_per_state(mind: DWAMind):
    for i in range(4):
        mind.record_session(_session_record(session_id=f's-{i}'))
    first = mind.consolidate()
    second = mind.consolidate()
    strip_time = lambda insights: [
        {k: v for k, v in i.items() if k != 'updated_at'} for i in insights
    ]
    assert strip_time(first) == strip_time(second)  # reconsolidation replaces, never stacks


def test_recall_finds_insights_then_episodes(mind: DWAMind):
    for i in range(4):
        mind.record_session(_session_record(session_id=f's-{i}', primary_app='code.exe'))
    mind.consolidate()
    hits = mind.recall('code.exe')
    assert hits  # falls back to episode search


def test_mind_survives_reload(mind: DWAMind):
    mind.record_session(_session_record())
    mind.record_reminder_outcome('stretch', 'skipped')
    mind.consolidate(minimum_episodes=1)
    reloaded = DWAMind(mind.base)
    assert reloaded.status()['episode_count'] == 2
    assert reloaded.status()['insight_count'] > 0
