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
    AIWorkProfile,
    SEED_TOOLS,
    title_is_ai,
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


def test_unknown_tool_is_discovered_from_a_window_title():
    """Learning that helps: a new AI product becomes known the moment its name
    shows up in a window title, with no release needed."""
    p = AIWorkProfile()
    assert not p.is_ai_tool("hugging face")   # a hint, not yet a seed
    p.observe("chrome.exe", ai_evidence=False, title="Hugging Face - Spaces")
    assert "hugging face" in p.weights
    assert p.is_ai_tool("hugging face")


def test_user_can_teach_a_tool_by_name():
    p = AIWorkProfile()
    assert not p.is_ai_tool("brandnewai")
    p.correct("brandnewai", is_ai_tool=True)
    assert p.is_ai_tool("brandnewai")
    assert "brandnewai" in p.known_tools()


def test_cooccurrence_alone_never_promotes_an_app():
    """Regression: Chrome, msedge and ShellHost.exe were all promoted purely
    for being open while an agent ran."""
    p = AIWorkProfile()
    for app in ("chrome.exe", "msedge.exe", "ShellHost.exe", "explorer.exe"):
        for _ in range(500):
            p.observe(app, ai_evidence=True)
        assert not p.is_ai_tool(app), app
        assert app.replace(".exe", "") not in p.known_tools()


def test_silence_does_not_demote_a_real_tool():
    """Regression: the old detector needed the user idle, so it never fired
    during real AI work. Treating that silence as contradicting evidence
    decayed Hermes (48h of agent time) out of the profile entirely."""
    p = AIWorkProfile()
    for _ in range(200):
        p.observe("Hermes.exe", ai_evidence=False)
    assert p.is_ai_tool("Hermes.exe"), "silence must not erase a known tool"
    assert p.contradictions.get("hermes", 0) == 200


def test_explicit_correction_can_demote_a_seed():
    p = AIWorkProfile()
    assert p.is_ai_tool("gemini")
    p.correct("gemini", is_ai_tool=False)
    assert not p.is_ai_tool("gemini")


def test_explicit_correction_can_promote_an_unknown_app():
    p = AIWorkProfile()
    assert not p.is_ai_tool("someaisuite")
    p.correct("someaisuite", is_ai_tool=True)
    assert p.is_ai_tool("someaisuite")


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


# --------------------------------------------------------------- regression
# Found by running the profile over 530 real sessions: chrome.exe and
# msedge.exe were promoted to "AI tools" merely for being open while an agent
# ran (128h and 100h of the user's time mislabelled as AI work). A browser is
# where the user reads email.

def test_browser_is_never_promoted_by_process_alone():
    p = AIWorkProfile()
    for _ in range(50):
        p.observe("chrome.exe", ai_evidence=True)
    assert not p.is_ai_tool("chrome.exe")
    assert p.weight_of("chrome.exe") == 0.0
    assert "chrome" not in p.known_tools()


def test_browser_counts_as_ai_only_while_showing_ai_work():
    p = AIWorkProfile()
    assert not p.is_ai_tool("chrome.exe", "Gmail - Chrome")
    assert p.is_ai_tool("chrome.exe", "ChatGPT - Chrome")
    assert p.is_ai_tool("msedge.exe", "Copilot Chat")
    assert not p.is_ai_tool("msedge.exe", "Outlook")


def test_terminal_is_ai_only_for_agent_titles():
    p = AIWorkProfile()
    assert p.is_ai_tool("WindowsTerminal.exe", "hermes agent - run")
    assert not p.is_ai_tool("WindowsTerminal.exe", "git log")


def test_exe_suffix_is_normalised():
    p = AIWorkProfile()
    assert p.is_ai_tool("Hermes.exe")
    assert p.is_ai_tool("ChatGPT Classic.exe")
    assert p.is_ai_tool("M365Copilot.exe")
    assert p.weight_of("ChatGPT Classic.exe") == p.weight_of("chatgpt classic")


def test_generic_host_split_stays_honest():
    p = AIWorkProfile()
    # Same app, same duration: the title decides which clock it lands in.
    assert p.split("chrome.exe", 600, agent_evidence=False,
                   title="Gmail")["human"] == 600
    assert p.split("chrome.exe", 600, agent_evidence=False,
                   title="ChatGPT")["ai_assisted"] == 600


def test_known_tools_excludes_generic_hosts():
    p = AIWorkProfile()
    p.observe("chrome.exe", ai_evidence=True, title="ChatGPT")
    assert "chrome" not in p.known_tools()
