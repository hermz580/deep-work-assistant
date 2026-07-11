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
| **24 new tests** | 33 tests total, all passing |

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
