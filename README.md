# Deep Work Assistant

Deep Work Assistant (DWA) is a local-first Windows focus and recovery companion. It detects human-active work, pauses recovery clocks during idle or agent-active time, and records session, reminder, and work-board evidence locally.

Version 0.5.0 is an integration alpha. The engine, Command Center, board, analytics, reminder responses, stretch escalation, and privacy-gated vision sampling exist. The current milestone is making them install, launch, report, and recover through one truthful path.

## Start on Windows

1. Clone or download this repository.
2. Double-click `Launch Deep Work Assistant.vbs`.
3. On the first run, DWA creates `.venv`, installs the core package, runs its readiness check, and opens the local Command Center.

The daily launcher is silent: it does not keep a terminal window open. If setup fails, it shows the error and points to `%LOCALAPPDATA%\DeepWorkAssistant\bootstrap.log`.

For a visible troubleshooting launch, run:

```bat
run_focus_command_center.bat
```

The Command Center binds to `http://127.0.0.1:8791`. A second launch reuses a healthy server on that address instead of starting a second UI server.

## Install from a terminal

Python 3.11 or newer is required.

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m deep_work_assistant doctor
.venv\Scripts\python -m deep_work_assistant.web_ui_v2
```

Optional capabilities are installed explicitly:

```bash
.venv\Scripts\python -m pip install -e ".[voice]"
.venv\Scripts\python -m pip install -e ".[vision]"
```

Voice is not local-only: when enabled, `edge-tts` sends reminder text to an online text-to-speech service. Core tracking and reminders work without voice.

## Two controls, two jobs

- **Start Assistant** launches automatic Windows activity detection and the hydration, stretch, meal, and optional posture reminder loop.
- **Begin Focus** launches the separate manual Pomodoro-style work and break timer.

You can use either or both. They do not yet share one session identity; connecting them through one runtime coordinator is the State Truth milestone. The UI and tutorial state this boundary instead of implying that they are already unified.

## What the automatic assistant does

- Classifies samples as human-active, agent-active, or idle.
- Starts a session after stable active samples.
- Advances recovery timers only during human-active work.
- Sends confirmable hydration, stretch, and meal reminders.
- Escalates after two accepted consecutive stretch skips to a 60-second primary-monitor overlay.
- Stores completed sessions and reminder outcomes locally.
- Learns app category, session-length, flow-style, and reminder-response patterns from session history.
- Counts a streak day only after ten human-active minutes in a non-agent-dominated session.
- Ends a session at the last valid sample after a long sleep/resume gap instead of crediting the entire gap.

Explicit `confirmed`, `completed`, `skipped`, `timeout`, and `overridden` outcomes now participate in adaptation and adherence metrics.

## Optional vision

Vision is off by default and remains a command-line opt-in:

```bash
python -m deep_work_assistant vision status
python -m deep_work_assistant vision provision
python -m deep_work_assistant run --vision
```

Raw frames are processed in memory and discarded. Stored vision events contain sanitized metrics only.

Personal posture calibration is consent-gated:

```bash
python -m deep_work_assistant vision consent grant
python -m deep_work_assistant vision calibrate        # sit upright; 8 local samples
python -m deep_work_assistant vision baseline         # show | clear | inaccurate
```

Calibration stores a median neutral angle, its spread, and a confidence label in `~/.deep_work_assistant/body_profile.json`. Alerts then fire at your neutral plus a margin, not at the generic 25°. "That alert was wrong" widens the threshold. Revoking consent deletes the baseline. Without consent, the generic threshold is unchanged.

## Who did the work: human vs AI attribution

DWA credits only **your** hands-on time. If the [Hermes Companion](https://github.com/hermz580/hermes-companion) plugin is installed, `run` also reads its signed, content-free agent log (`<HERMES_HOME>/companion/events.jsonl`). It checks every record's signature and never writes to the log. Every stretch of a session is attributed to one of three buckets:

- **You alone**: real keyboard/mouse input, no agent working.
- **Both at once**: you worked while Hermes worked too. This counts as yours.
- **AI alone**: Hermes (or another agent changing windows) worked with no human input.

Only the first two count toward focus, streaks and reminder clocks. Sessions also record what the agent did (`agent_work`, for example `hermes:terminal`). DWA OS → Evidence shows the split, and Obsidian notes include it. Without the plugin, DWA falls back to window-change detection. `run --no-hermes` turns the bridge off.

## DWA OS (preview)

`deep-work-ui --os` (or `http://127.0.0.1:8791/os`) opens DWA OS, a calmer shell built around the mission loop: **Sense → Model → Act**. Apps: Today (one next right action), Focus, Body, Board, Recover, Evidence, System. It uses the same local APIs and loads nothing from the network. The classic Command Center stays at `/` until DWA OS passes Windows acceptance.

**Body** is the real camera helper: consent, a framing preview that never leaves the browser, calibration, a live posture trend against your threshold, an estimated head-load figure (Hansraj curve, labeled as an estimate), and height-scaled workstation starting points. It carries over the useful ideas from the KinePost concept (`hermz580/dwa-cam-helper-`) without its mock analyses or cloud image upload.

## Command Center

The canonical interface is `deep_work_assistant.web_ui_v2` / `deep-work-ui`. It includes:

- Advanced Board and manual focus timer
- Local SQLite Kanban board
- Session Intelligence and JSON exports
- Recovery Chamber
- Settings, logs, and shared diagnostics
- First-run How It Works tutorial
- Permanent Help & Feedback page
- Questions, comments & concerns FAQ
- Local support-note composer with sanitized diagnostics

Nothing in the support composer is sent automatically. The user reviews and copies the note before opening GitHub.

## Verify a checkout

```bash
python -m deep_work_assistant doctor --json
python -m deep_work_assistant simulate
python -m pytest -q
python -m build
```

The CI workflow runs unit/integration checks, simulation, diagnostics, and wheel builds on Linux and Windows. A clean Windows 11 launcher smoke test is still required before this project can honestly claim “works every time.”

## Local data

- Session history: `%LOCALAPPDATA%\DeepWorkAssistant\history.jsonl`
- Board: `~/.deep_work_assistant/kanban.db`
- Streak: `~/.deep_work_assistant/deep_work_streak.json`
- UI settings: `~/.deep_work_assistant/ui_settings.json`
- UI-managed assistant log: `~/.deep_work_assistant/assistant-ui.log`
- Bootstrap log: `%LOCALAPPDATA%\DeepWorkAssistant\bootstrap.log`

## Current boundaries

- DWA is a localhost browser application, not a native Windows shell.
- The UI-managed assistant and a separately started CLI assistant do not yet share a cross-process singleton lock.
- Manual timer sessions and automatic activity sessions are separate ledgers.
- Vision install and model provisioning are command-line only; calibration works from the CLI or DWA OS.
- The fullscreen stretch overlay covers the primary monitor only.
- Windows notifications, foreground capture, silent bootstrap, and restart persistence still need a clean Windows 11 acceptance run.

## Project guidance

- [How It Works](docs/HOW_IT_WORKS.md)
- [Milestones and outcomes](docs/MILESTONES.md)
- [Focus Command Center](FOCUS_COMMAND_CENTER.md)

Questions, comments, concerns, accessibility problems, privacy concerns, and bugs can be prepared from **Help & Feedback** inside the Command Center or opened directly in [GitHub Issues](https://github.com/hermz580/deep-work-assistant/issues).
