# Milestones and Outcomes

The release rule is simple: status follows acceptance evidence, not aspiration.

| Milestone | Status | User outcome | Exit evidence |
|---|---|---|---|
| M0 — Boot Truth | In progress | A clean Windows checkout installs and opens one canonical v2 interface without a persistent terminal. | Clone → silent bootstrap → doctor → health → every referenced asset returns 200 → relaunch reuses server. |
| M1 — State Truth | In progress | One focus identity produces accurate sessions, streaks, outcomes, and card time exactly once. | One runtime coordinator; duplicate assistant lock; automatic/manual focus identity joined; correlated reminder events; restart tests. |
| M2 — Embodied Truth | Planned | Posture guidance begins from consent, calibration, confidence, and local metrics—not assumptions. | Vision provisioned; neutral baseline calibrated; inaccurate action supported; no raw-frame persistence; confidence visible. |
| M3 — Guided Recovery | Planned | Confirm, snooze, skip, inaccurate, escalation, and return-to-task form one adaptive recovery loop. | Response ladder and exact return state tested through multiple sessions. |
| M4 — Release Truth | In progress | A contributor can pull, verify, understand, and report a problem without reverse-engineering the project. | Linux + Windows CI, packaged wheel smoke, version consistency, tutorial, FAQ, sanitized support-note flow. |

## What this integration branch establishes

- PR #3 vision work is used as the branch foundation rather than reimplemented.
- `web_ui_v2` is the canonical interface and launcher target.
- One diagnostics service powers both `doctor` and the UI.
- The package imports and tests away from Windows while live capture still fails clearly off Windows.
- Automatic finalization owns streak advancement; handlers only persist it.
- Streak eligibility uses human-active time and agent dominance.
- Missed-day streaks and “today” counts are projected honestly.
- Explicit reminder outcomes participate in adaptation and adherence.
- Long sample gaps cannot create hours of false focus or stacked reminders.
- First-run tutorial and Questions, comments & concerns are built into the product.

## What remains before M0 can be called complete

1. Run the clean Windows 11 acceptance path.
2. Prove the hidden bootstrap with and without Python already installed.
3. Verify notification, popup, overlay, and shutdown behavior on Windows.
4. Add a cross-process singleton for the assistant, not only the UI server.
5. Record the acceptance results in the pull request.

## Decision owners

- **Conductor:** goal, state, priority, and release decision
- **Reliability:** installation, lifecycle, CI, and recovery
- **Integrity:** sessions, streaks, outcomes, and persistence truth
- **Recovery:** reminder behavior, escalation, and return-to-task
- **Experience:** tutorial, accessibility, ergonomics, and feedback
- **Vision:** consent, calibration, confidence, and privacy
- **Relay:** shared decision/evidence ledger across lanes
- **Breaker:** evidence-backed failure mode, alternative, and cost before release
