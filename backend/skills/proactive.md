---
name: proactive
description: Hourly proactive check — sends affirmations, energy reminders, and context-aware nudges
triggers: [proactive, affirmation, recordatorio, nudge]
schedule: "0 * * * *"
job: proactive.run_cycle
---

# Proactive

Runs every hour. The proactive service decides what (if anything) to send based on the current hour and context.

## What it does by hour

- **9am** — Morning affirmation + day framing
- **12pm / 1pm** — Midday energy check, lunch reminder if no break detected
- **5pm** — End-of-workday nudge, wind-down signal
- **Other hours** — Check for overdue tasks, stalled experiments, or pending actions

## Rules

- If there's nothing meaningful to send → stay silent. Don't send filler messages.
- Never repeat the same message two hours in a row.
- If the user is in a focus block → don't interrupt unless it's end-of-day.

## To disable

Remove or comment out the `schedule` field in this skill's frontmatter.
To change the schedule, ask the bot: "change the proactive reminders to only morning and evening."
