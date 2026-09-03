# How It Works

This is the same operating truth presented by the first-run tutorial and permanent Help & Feedback page.

## 1. Check readiness

Run `python -m deep_work_assistant doctor` or open **Settings & Diagnostics**. Windows activity capture, storage, and packaged UI assets must be ready. Voice and vision are optional.

## 2. Choose the workflow

**Start Assistant** watches human Windows activity and owns automatic recovery reminders. **Begin Focus** owns the separate manual focus/break timer. They can run together, but version 0.5.0 does not pretend they are one session.

## 3. Work

Automatic reminder time advances only during human-active work. Idle and agent-active periods pause the clock. A gap longer than five minutes closes the old automatic session at its last sample so sleep or resume time is not credited as focus.

## 4. Respond and recover

Confirm, skip, or let a reminder time out. Explicit outcomes teach the adaptive plan. Two accepted consecutive stretch skips trigger the 60-second stretch overlay. Unmatched or stale response records do not change escalation counts.

## 5. Review evidence

Session Intelligence reports saved session duration, human/agent activity, end reason, reminder outcomes, and streak state. Fewer than three completed sessions are labeled as calibration evidence, not a learned profile.

## 6. Understand posture limits

Vision requires explicit installation, model provisioning, and `--vision` opt-in. Raw frames are discarded. Current posture alerts use generic sustained thresholds. DWA does not yet have a personal posture baseline.

## 7. Ask for help

Open **Help & Feedback → Questions, comments & concerns**. Build a local support note, optionally include sanitized diagnostics, review it, then copy it. Nothing is submitted automatically.
