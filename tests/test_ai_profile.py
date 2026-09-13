"""Tests for the learned AI-work profile.

Key properties under test:
  - the three clocks never overlap and never double count
  - a tool NOT in the seed list can be learned from evidence
  - a seeded tool can decay if evidence contradicts it
  - a plain tool (penpot) is explicitly not an AI tool
  - the profile round-trips through disk
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deep_work_assistant.ai_profile import (
    AI_THRESHOLD,
    AIWorkProfile,
    SEED_TOOLS,
)


def test_seeded_tools_are_known():
    p = AIWorkProfile()
    for tool in ("hermes", "claude", "cursor", "ollama"):
        assert p.is_ai_tool(tool), tool


def test_plain_tool_is_not_an_ai_tool():
    p = AIWorkProfile()
    assert not p.is_ai_tool("penpot")
    assert not p.is_ai_tool("excel.exe")
    assert not p.is_ai_tool("")


def test_unknown_tool_is_learned_from_evidence():
    p = AIWorkProfile()
    assert not p.is_ai_tool("brandnewai")
    needed = int((AI_THRESHOLD - 0.0) / 0.5) + 1
    for _ in range(needed):
        p.observe("brandnewai", ai_evidence=True)
    assert p.is_ai_tool("brandnewai")
    assert "brandnewai" in p.known_tools()


def test_contradicting_evidence_decays_a_weak_tool():
    p = AIWorkProfile()
    p.observe("gemini", ai_evidence=True)          # 2.0 -> 2.5
    for _ in range(20):
        p.observe("gemini", ai_evidence=False)     # decays below threshold
    assert not p.is_ai_tool("gemini")


def test_a_strong_seed_survives_noise():
    p = AIWorkProfile()
    for _ in range(3):
        p.observe("hermes", ai_evidence=False)
    assert p.is_ai_tool("hermes"), "one noisy patch must not unmade a real tool"


def test_split_attributes_to_exactly_one_clock():
    p = AIWorkProfile()
    assert p.split("hermes", 600, agent_evidence=False) == {
        "human": 0, "ai_assisted": 600, "agent": 0}
    assert p.split("excel.exe", 600, agent_evidence=False) == {
        "human": 600, "ai_assisted": 0, "agent": 0}
    assert p.split("excel.exe", 600, agent_evidence=True) == {
        "human": 0, "ai_assisted": 0, "agent": 600}
    assert p.split("hermes", 600, agent_evidence=True) == {
        "human": 0, "ai_assisted": 0, "agent": 600}


def test_split_total_is_preserved():
    p = AIWorkProfile()
    for app, agent in (("hermes", False), ("excel.exe", False), ("excel.exe", True)):
        parts = p.split(app, 300, agent_evidence=agent)
        assert sum(parts.values()) == 300


def test_round_trips_through_disk(tmp_path: Path):
    p = AIWorkProfile()
    p.observe("someide", ai_evidence=True)
    p.observe("hermes", ai_evidence=True)
    path = p.save(tmp_path / "ai_profile.json")
    assert path.exists()

    reloaded = AIWorkProfile.load(path)
    assert reloaded.observations == p.observations
    assert reloaded.is_ai_tool("hermes")
    assert reloaded.weight_of("hermes") == p.weight_of("hermes")

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert "known_tools" in on_disk
    assert "hermes" in on_disk["known_tools"]


def test_corrupt_profile_file_falls_back_to_seeds(tmp_path: Path):
    bad = tmp_path / "ai_profile.json"
    bad.write_text("{not json at all", encoding="utf-8")
    p = AIWorkProfile.load(bad)
    assert p.is_ai_tool("hermes"), "a corrupt file must not blind the profile"


def test_explain_is_human_readable():
    p = AIWorkProfile()
    assert "AI tool" in p.explain("hermes")
    assert "not an AI tool" in p.explain("excel.exe")


def test_seed_table_has_no_accidental_ai_tools():
    # Guards the curated exception: penpot is a design tool, weight 0.
    assert SEED_TOOLS["penpot"] == 0.0
