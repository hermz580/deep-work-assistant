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
    # Names observed in Herman's real history, not guesses.
    "chatgpt classic": 3.0, "m365copilot": 3.0, "copilot classic": 3.0,
    "penpot": 0.0,  # design tool: NOT an AI tool, explicitly zeroed
}

# An entry must reach this weight before it counts.
AI_THRESHOLD = 1.5

# Apps that HOST AI work but are not AI tools themselves. Chrome being open
# while an agent ran is not evidence that Chrome is an AI tool - it is where
# the user reads email. These can only be classified by WINDOW TITLE, never by
# process name, because process name carries no information about them.
GENERIC_HOSTS = {
    "chrome", "msedge", "firefox", "brave", "opera", "vivaldi", "arc",
    "windowsterminal", "powershell", "cmd", "conhost", "wt", "putty",
    "explorer", "shellexperiencehost", "applicationframehost",
    "code", "notepad", "notepad++", "wordpad",
}

# Window-title hints for AI work inside a generic host (browser / terminal).
# This is what makes "ChatGPT in Chrome" countable without calling Chrome an
# AI tool.
TITLE_HINTS = {
    "chatgpt": 3.0, "chat.openai": 3.0, "claude": 3.0, "gemini": 2.5,
    "copilot": 2.5, "perplexity": 2.5, "grok": 2.5, "deepseek": 2.5,
    "mistral": 2.5, "hermes agent": 3.0, "hermes": 2.5,
    "cursor": 3.0, "windsurf": 2.5, "aider": 2.5, "ollama": 3.0,
    "lm studio": 2.5, "hugging face": 2.5, "kaggle": 1.5,
    "copilot chat": 2.5, "m365 copilot": 2.5,
}


def _norm(app: str | None) -> str:
    """Normalise a process name: 'ChatGPT Classic.exe' -> 'chatgpt classic'."""
    name = (app or "").strip().lower()
    for suffix in (".exe", ".com", ".bat", ".cmd"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return " ".join(name.split())


def title_is_ai(title: str | None) -> bool:
    low = (title or "").lower()
    return any(hint in low for hint in TITLE_HINTS)


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
    def observe(self, app: str | None, *, ai_evidence: bool,
                title: str | None = None) -> None:
        """Nudge the profile.

        ai_evidence=True means the pattern looked like AI-mediated work.
        Generic hosts (browsers, terminals) are NEVER promoted by process name
        alone - a browser being open while an agent ran says nothing about the
        browser. They only gain weight when the window TITLE shows AI work.

        ai_evidence=False records a contradiction but does NOT lower the
        weight. Absence of AI evidence is not evidence of absence: the old
        detector needed the user to be idle, so it stayed silent through real
        AI work, and decaying on that silence erased Hermes itself from the
        profile (48h of agent time read as "plain"). Only explicit user
        correction lowers a tool (see `correct`).
        """
        key = _norm(app)
        if not key:
            return
        self.observations += 1

        # Title discovery runs for EVERY app, host or not, so a new AI product
        # is learned the moment its name appears anywhere.
        titled = self._register_title(title)

        if key in GENERIC_HOSTS:
            # A host is classified by its title, never by weight: Chrome open
            # during agent work is still Chrome.
            if titled:
                self.evidence[key] = self.evidence.get(key, 0) + 1
            else:
                self.contradictions[key] = self.contradictions.get(key, 0) + 1
            self.weights[key] = 0.0
            return

        if titled:
            self.evidence[key] = self.evidence.get(key, 0) + 1
            return

        if ai_evidence:
            # Statistic only. Co-occurrence with the agent is NOT proof an app
            # is an AI tool: Chrome, msedge and ShellHost.exe all got promoted
            # that way and mislabelled 230h+ of the user's time as AI work.
            self.evidence[key] = self.evidence.get(key, 0) + 1
        else:
            self.contradictions[key] = self.contradictions.get(key, 0) + 1

    def _register_title(self, title: str | None) -> bool:
        """Learn any AI product named in a window title. Returns True if the
        title showed AI work."""
        if not title:
            return False
        low = title.lower()
        matched = [h for h in TITLE_HINTS if h in low]
        if not matched:
            return False
        for hint in matched:
            if hint not in self.weights:
                self.weights[hint] = TITLE_HINTS[hint]  # discovered by use
        return True

    def correct(self, app: str | None, *, is_ai_tool: bool) -> None:
        """Explicit user correction - the only thing that can demote a tool."""
        key = _norm(app)
        if not key:
            return
        if is_ai_tool:
            self.weights[key] = max(AI_THRESHOLD, self.weights.get(key, 0.0))
            self.evidence[key] = self.evidence.get(key, 0) + 1
        else:
            self.weights[key] = 0.0
            self.contradictions[key] = self.contradictions.get(key, 0) + 1

    # -------------------------------------------------------------- reading
    def is_ai_tool(self, app: str | None, title: str | None = None) -> bool:
        key = _norm(app)
        if key in GENERIC_HOSTS:
            # A browser is only an AI tool while it is showing AI work.
            return bool(title) and title_is_ai(title)
        return self.weights.get(key, 0.0) >= AI_THRESHOLD

    def weight_of(self, app: str | None) -> float:
        return round(self.weights.get(_norm(app), 0.0), 2)

    def known_tools(self) -> list[str]:
        """Curated AI tools. Generic hosts are reported separately because
        they are only AI tools while a specific window is open."""
        return sorted(k for k, v in self.weights.items()
                      if v >= AI_THRESHOLD and k not in GENERIC_HOSTS)

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

    def split(self, app: str | None, seconds: int, *, agent_evidence: bool,
              title: str | None = None) -> dict[str, int]:
        """Attribute `seconds` to one of the three clocks."""
        out = {"human": 0, "ai_assisted": 0, "agent": 0}
        if agent_evidence:
            out["agent"] = max(0, int(seconds))
        elif self.is_ai_tool(app, title):
            out["ai_assisted"] = max(0, int(seconds))
        else:
            out["human"] = max(0, int(seconds))
        return out
