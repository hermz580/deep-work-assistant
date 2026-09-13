"""Learned AI-work profile.

DWA already learns app category, session length and flow style. This extends
the same idea to the question the audit actually asks: *how much of my work
was AI-mediated, and with which tool?*

Three clocks (previously only two, and the third was invisible):

  human         - you, in a non-AI app
  ai_assisted   - you, working INSIDE an AI tool (you typing, AI producing)
  agent         - the AI driving while you were away from the keyboard

`agent` was the only one the old detector produced, and it needed the user to
be idle, so it read ~15 seconds in a 2h17m session and made an audit useless.

Design rules:
  - Seeded with known tools, never hardcoded as the final word: every
    observation nudges a weight, so the profile converges on what Herman
    actually uses. New AI tools get discovered by evidence, not by a release.
  - Inspectable and correctable: the learned map is a plain JSON file, and
    `explain()` reports why a tool is considered an AI tool.
  - Honest by construction: ai_assisted is "you in an AI tool", never claimed
    to be "AI did this alone". The two stay separate in every total.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# Hypothesis, not gospel: these only seed the weights. Anything that keeps
# showing up alongside AI-tool behaviour earns its own place in the profile.
SEED_TOOLS = {
    "hermes": 3.0, "hermes-agent": 3.0,
    "claude": 3.0, "claude code": 3.0, "claude.exe": 3.0,
    "codex": 3.0, "openai codex": 3.0,
    "cursor": 3.0, "windsurf": 3.0, "aider": 3.0, "cline": 3.0,
    "ollama": 3.0, "lm studio": 3.0, "lmstudio": 3.0,
    "chatgpt": 2.5, "copilot": 2.5, "gemini": 2.0, "perplexity": 2.0,
    "antigravity": 2.0, "codeium": 2.0, "continue": 2.0,
    "stable diffusion": 1.5, "comfyui": 1.5, "whisper": 1.5,
    "penpot": 0.0,  # design tool: NOT an AI tool, explicitly zeroed
}

# An entry must reach this weight, on evidence, before it counts.
AI_THRESHOLD = 1.5
# Each corroborating observation adds this much.
EVIDENCE_STEP = 0.5
# Each contradicting observation (user typing with no AI output pattern) costs
# less than evidence gains, so a real tool is not unmade by one noisy session.
DECAY_STEP = 0.15


def _norm(app: str | None) -> str:
    return (app or "").strip().lower()


@dataclass
class AIWorkProfile:
    """Learned view of which apps are AI tools, and how sure we are."""

    weights: dict[str, float] = field(default_factory=dict)
    evidence: dict[str, int] = field(default_factory=dict)
    contradictions: dict[str, int] = field(default_factory=dict)
    observations: int = 0
    version: int = 1

    def __post_init__(self) -> None:
        for name, weight in SEED_TOOLS.items():
            self.weights.setdefault(name, float(weight))

    # ------------------------------------------------------------ learning
    def observe(self, app: str | None, *, ai_evidence: bool) -> None:
        """Nudge the profile. ai_evidence=True means the pattern looked like
        AI-mediated work (tool window active while a generating/streaming
        pattern held); False means it looked like plain use."""
        key = _norm(app)
        if not key:
            return
        self.observations += 1
        if ai_evidence:
            self.evidence[key] = self.evidence.get(key, 0) + 1
            self.weights[key] = self.weights.get(key, 0.0) + EVIDENCE_STEP
        else:
            self.contradictions[key] = self.contradictions.get(key, 0) + 1
            self.weights[key] = max(0.0, self.weights.get(key, 0.0) - DECAY_STEP)

    # -------------------------------------------------------------- reading
    def is_ai_tool(self, app: str | None) -> bool:
        return self.weights.get(_norm(app), 0.0) >= AI_THRESHOLD

    def weight_of(self, app: str | None) -> float:
        return round(self.weights.get(_norm(app), 0.0), 2)

    def known_tools(self) -> list[str]:
        return sorted(k for k, v in self.weights.items() if v >= AI_THRESHOLD)

    def explain(self, app: str | None) -> str:
        key = _norm(app)
        w = self.weight_of(key)
        if w >= AI_THRESHOLD:
            why = ("seeded as a known AI tool" if key in SEED_TOOLS
                   else "learned from use")
            return f"{key}: AI tool (weight {w}, {why}, {self.evidence.get(key, 0)} observations)"
        return f"{key}: not an AI tool (weight {w}, {self.evidence.get(key, 0)} observations)"

    # ------------------------------------------------------------ lifecycle
    @classmethod
    def default_path(cls) -> Path:
        return Path.home() / ".deep_work_assistant" / "ai_profile.json"

    @classmethod
    def load(cls, path: Path | None = None) -> "AIWorkProfile":
        path = path or cls.default_path()
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return cls()
        return cls(
            weights={k: float(v) for k, v in (raw.get("weights") or {}).items()},
            evidence={k: int(v) for k, v in (raw.get("evidence") or {}).items()},
            contradictions={k: int(v) for k, v in (raw.get("contradictions") or {}).items()},
            observations=int(raw.get("observations") or 0),
            version=int(raw.get("version") or 1),
        )

    def save(self, path: Path | None = None) -> Path:
        path = path or self.default_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "version": self.version,
            "observations": self.observations,
            "weights": self.weights,
            "evidence": self.evidence,
            "contradictions": self.contradictions,
            "known_tools": self.known_tools(),
        }, indent=2), encoding="utf-8")
        return path

    def split(self, app: str | None, seconds: int, *, agent_evidence: bool) -> dict[str, int]:
        """Attribute `seconds` to one of the three clocks."""
        out = {"human": 0, "ai_assisted": 0, "agent": 0}
        if agent_evidence:
            out["agent"] = max(0, int(seconds))
        elif self.is_ai_tool(app):
            out["ai_assisted"] = max(0, int(seconds))
        else:
            out["human"] = max(0, int(seconds))
        return out
