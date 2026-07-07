# Deep Work Assistant

A local Windows assistant that detects focused work from active-window stability and input idleness, then sends adaptive desktop reminders for hydration, stretching, and meals.

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
