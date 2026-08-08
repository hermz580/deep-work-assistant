# Deep Work Assistant — Governance & Development Framework

> Status: adopted 2026-08-08. This document is the contract for what
> DWA is, how value is judged, how features enter, and how correctness
> is proven. It exists so a multi-faceted tool stays coherent instead of
> turning into an undifferentiated blob.

## 1. Mission

DWA is a personal embodied health-and-focus agent: it senses the user's
physical and digital state, models their patterns, and acts with
adaptive self-care (hydration, stretch, posture, meals, focus).

**The mission test:** every proposed feature must answer "does this make
the user healthier or more focused?" If no, it does not enter DWA — it
becomes a separate tool (see §6).

## 2. Lane architecture

All features live in exactly one lane. A feature that touches all four
is too big; split it.

| Lane | Owns | Examples |
|---|---|---|
| **Focus** | Session engine, active-window detection, human-vs-agent classification | focus streaks, session logs |
| **Health** | Reminder engine, confirmable popups, overlays, future camera vision | hydration/stretch/eat, posture |
| **Analytics** | Pattern learning, adaptive plans, reports | laptop profile, weekly report |
| **Board** | Local kanban, command center UI, session tracking | cards, pomodoro, web UI (8791) |

## 3. Value framework (how to judge a candidate)

Six tests, in order. Any repo/library/feature is scored against these
before it is mentioned as "valuable":

1. **Mission fit** — serves health or focus directly.
2. **Reality** — real code: tests, active maintenance, non-trivial
   footprint. A 1-commit / 1KB stub fails instantly.
3. **Reuse vs reference** — integration-grade code, or only an idea?
   React scaffolds cannot drop into a Python assistant; their value is
   conceptual at most.
4. **Integration cost** — language match, dependency weight, upkeep.
5. **Privacy/trust** — local-only, no cloud, no telemetry.
6. **Redundancy** — if DWA already does it, do not add it.

**Judging repos requires inspection, not descriptions.** Always check
commit count, footprint size, last push date, README authenticity
(AI Studio scaffold templates are a red flag), and tests before ranking.

## 4. Feature pipeline

```
Idea → Spike (throwaway, validated) → Spec (Given/When/Then)
     → TDD build → Verification gates → Merge (PR on its branch)
     → Monitor → Debrief
```

- **Spike first.** Prove feasibility with throwaway code and numeric
  evidence before any production work. See `spikes/` pattern.
- **Spec as a contract.** Given/When/Then framing per behavior.
- **Verification gates.** Full test suite green + live behavioral
  harness against the running system, before merge.
- **Debrief.** After a feature has run in the wild, read the logs: did
  it do what the spec said? Refine or retire.

## 5. Correctness framework (how we know it works)

1. **Test gate** — nothing merges with the suite red (175 tests as of
   2026-08-08).
2. **Liveness, not faith** — external watchdog probes the run loop
   (30-min cadence), revives it if dead, reports restarts only.
3. **Behavior evidence** — every reminder, confirmation, skip, session,
   and event is logged (`~/.deep_work_assistant/*.jsonl`, Obsidian
   session notes). Audit what it *did* vs what it *should* have done.
4. **Live behavioral harness** — probe the running system (both watchdog
   branches, process liveness, vault path), not just unit tests.
5. **Weekly debrief** — read logs and report: sent vs confirmed vs
   skipped, sessions logged, streak accuracy, drift.
6. **QA culture** — adversarial review gates (Nazurak → Vonta) before
   deploy, same as all other HarpStar systems.

## 6. The separate-tool rule

Anything that does not pass the mission test becomes its own local tool.
Example: `~/camwatch/camwatch.py` (motion-triggered camera snapshots
with Teams delivery) — a security capability, not a health feature, so
it stays out of DWA. The camera **posture** spike
(`~/tmp/spikes/001-camera-posture/`) is the DWA-relevant piece and is
parked, not discarded.

## 7. Sensor map (sense–model–act)

| Stage | Now | Spiked / parked | Future |
|---|---|---|---|
| **Sense** | window title, input idleness, human-vs-agent | camera motion + posture (VALIDATED 2026-08-08: 14.3fps, forward-head angle) | drink/stretch verification |
| **Model** | laptop profile, adaptive intervals, streaks | — | office multi-user profiles |
| **Act** | reminders, overlays, board | posture reminder stage | wellness journal panel |

The camera is the first sensor observing the physical user, not the
digital one. It is the difference between an app guessing state from a
keyboard and a coach watching the body.

## Appendix A — Repo evaluation log (2026-08-08)

Inspected repos from `hermz580` for DWA relevance. Verdicts are
evidence-based (footprint, commit count, push date, README
authenticity, tests).

| Repo | Evidence | Verdict |
|---|---|---|
| `deep-work-assistant` | active, 175 tests, merged PR #1 (befe233) | **Canonical. This repo.** |
| `convoscope-v2` | real Python package (analyzer lanes, CI, MIT), no tests | **Architectural reference** for analytics lane |
| `bloom` | 591KB, stale Feb 2026, AI Studio scaffold README, no tests | **Concept only** (wellness UI ideas) |
| `o-sscroll-` | 786KB, AI Studio scaffold, no tests, touched Jul 2026 | **Direction only** (face-analysis interest) |
| `facial-intel` | 1KB, one commit, 2025 | **Discarded — a stub** |

Rule for future evaluations: never rank from a description alone; run
the six tests on inspected evidence.
