# Deep Work Assistant

A local Windows assistant that detects focused work from active-window stability and input idleness, then sends adaptive desktop reminders for hydration, stretching, and meals.

## Features

- 👁️ Watches the foreground app and idle time on Windows
- 🧠 Starts a focus session after stable active samples
- 💧 Sends reminders at adaptive intervals with **context-aware motivational messages**
- 🧘 **Activity-specific stretch suggestions** — coding, writing, research, communication, or creative
- ⏰ **Time-of-day aware** — messages change for morning, afternoon, evening, and night
- 🔥 **Focus streak tracking** — celebrates consecutive days of deep work (persisted across restarts)
- 🗣️ **Optional voice TTS reminders** — natural speech via edge-tts (`--voice` flag)
- 📊 Learns a local laptop-use profile from completed sessions: dominant app category, flow style, reminder response style, and top apps
- 🎯 Personalizes stretch/hydration wording when your pattern shows coding, writing/admin, research, communication, or creative work
- 📝 Logs completed sessions locally in JSONL
- 🔄 Adjusts future intervals based on recent session behavior

## New in v0.2.0

| Feature | Description |
|---------|-------------|
| **Smart messages** | Time-of-day-aware motivation that never repeats the same way |
| **Stretch suggestions** | 15 specific exercises across 6 activity categories |
| **Focus streaks** | Tracks consecutive days, celebrates milestones |
| **Voice TTS** | Natural-sounding voice reminders (`pip install deep-work-assistant[voice]`) |
| **Kanban board** | Local SQLite-backed project management — plan → do → review |
| **31 new tests** | 64 tests total, all passing |

## New in v0.4.0 — Interactive Reminders, Stretch Enforcement & Agent Detection

### 💬 Interactive confirmable reminders

Reminders are no longer passive. Each reminder (hydration / stretch / eat) spawns a topmost popup asking a direct question — e.g. *"💧 Hydration time — did you drink water?"* — with two big buttons: **✅ Yes, done** and **⏭️ Skip**.

- The popup runs as a detached `pythonw` subprocess, so it never blocks the assistant loop.
- The choice is appended to `~/.deep_work_assistant/reminder_responses.jsonl` (`confirmed` / `skipped` / `timeout`) and picked up asynchronously by the run loop.
- The popup auto-times-out after 120 seconds (logged as `timeout`).
- Consecutive skips per stage are tracked in `~/.deep_work_assistant/reminder_skip_state.json`; confirming resets the counter.
- Responses show up in the Obsidian session log as ✅ confirmed / ⏭️ skipped.

### 🧘 Enforced stretch overlay

Skip the stretch reminder **2 times in a row** and the assistant escalates: instead of a small popup, a fullscreen, always-on-top overlay appears — dark background, big *"Stretch break — 60 seconds"* text, a live countdown, and two stretch suggestions matched to your work category.

- It's just a window: **no background process is blocked or killed** — builds, agents, and downloads keep running underneath.
- **Escape hatch:** type `skip` in the small entry at the bottom and press Enter to dismiss it (logged as `overridden`, still counts as a skip).
- Letting the countdown finish logs `completed` and resets the stretch skip counter.
- *Limitation:* the overlay covers the **primary monitor only**. On multi-monitor setups, secondary screens are not covered.

### 🤖 Human vs agent activity detection

If you run AI agents that drive windows/terminals while you're away, `GetLastInputInfo` still only reflects *real* human keyboard/mouse input. The engine now classifies each sample:

- **human-active** — recent physical input (idle ≤ ~120s)
- **agent-active** — window title / foreground app changing while human idle keeps rising
- **idle** — nothing changing, no input

Consequences:

- Session summaries carry `human_active_seconds` and `agent_active_seconds`.
- **Reminder timers count human-active time only** — hydration/stretch/eat countdowns pause while agents run or you're idle, so you won't get told to hydrate after 3 hours of Hermes doing the work.
- Sessions that are >80% agent-active are tagged `ended_reason: agent-session` and logged in Obsidian as `> [!note]+ 🤖 Agent Work - <duration>` instead of counting as personal deep work. Mixed sessions show human 🧑 / agent 🤖 time-split lines.

## New in v0.3.0 — Kanban Board

The Deep Work Assistant now includes a **local Kanban board** that lives alongside your focus tracker. Plan tasks, track deep work time against them, and watch the board auto-suggest cards based on what you're working on.

### Board commands

```bash
# Show the full board
python -m deep_work_assistant board

# Add a card
python -m deep_work_assistant card add "Build API Gateway" \
  --desc "REST endpoints for user auth" \
  --priority 1 \
  --tags backend api \
  --app code.exe \
  --window gateway

# Move a card through columns: backlog → ready → in_progress → review → done
python -m deep_work_assistant card move <card-id> in_progress
python -m deep_work_assistant card move <card-id> review
python -m deep_work_assistant card move <card-id> done

# List cards (filter by column or tag)
python -m deep_work_assistant card list
python -m deep_work_assistant card list --column in_progress
python -m deep_work_assistant card list --tag backend

# View card details
python -m deep_work_assistant card show <card-id>

# Log deep work time to a card
python -m deep_work_assistant card log <card-id> --minutes 120

# Search cards
python -m deep_work_assistant card search "gateway"

# Delete a card
python -m deep_work_assistant card delete <card-id>
```

### Card columns

| Column | Description |
|--------|-------------|
| **Backlog** | Ideas and tasks not yet started |
| **Ready** | Prioritized and ready to work on |
| **In Progress** | Currently being worked on |
| **Review** | Done but needs verification |
| **Done** | Completed |

### Deep work integration

When you start a focus session, the assistant checks your active app and
suggests matching cards from the board. When a session ends, time is
logged against the active card if one is set.

The board is stored in `~/.deep_work_assistant/kanban.db` (SQLite) — your
data stays local and private.

## What it does

- Watches the foreground app and idle time on Windows
- Starts a focus session after stable active samples
- Sends reminders at adaptive intervals
- Learns a local laptop-use profile from completed sessions: dominant app category, flow style, reminder response style, and top apps
- Personalizes stretch/hydration wording when your pattern shows coding, writing/admin, research, communication, or creative work
- Logs completed sessions locally in JSONL
- Adjusts future intervals based on recent session behavior

## Run it

From this folder:

```bash
python -m deep_work_assistant run
```

With voice reminders (requires `pip install deep-work-assistant[voice]`):

```bash
python -m deep_work_assistant run --voice
python -m deep_work_assistant run --voice --voice-pre-announce  # voice before popup
```

The launcher batch also supports the same modes. In normal `run` mode it now auto-targets the local Obsidian vault when that path exists, so session summaries can be written into the daily note layer:

```bash
run_deep_work_assistant.bat
run_deep_work_assistant.bat simulate
run_deep_work_assistant.bat plan
```

For a no-toast verification pass:

```bash
python -m deep_work_assistant simulate
```

To print the current adaptive plan and the local laptop-use profile it has learned:

```bash
python -m deep_work_assistant plan
```

The profile is built only from the local history file. It uses app names, session duration, idle time, and reminder outcomes to infer whether you tend to do long coding flow, shorter admin/writing sprints, research, communication, or creative sessions.

## Notes

- Default history path: `%LOCALAPPDATA%\DeepWorkAssistant\history.jsonl`
- The assistant is local-only and does not require a UI.
- If Windows toast notifications fail, the notifier falls back to console output.
