---
name: plan-day
description: Create a structured work plan for the day with calendar blocks
triggers: [plan, organize, schedule, organiza, planea, mañana, tomorrow, day, día, semana, week, agenda, morning]
---

# Plan Day

Use this skill when the user wants to plan their day, organize their schedule, prepare for tomorrow, or set up their week.

## Procedure

1. Check `get_tasks` with today's date to see what's pending
2. Check `get_calendar_events` to see existing commitments
3. Call `delegate_to_planner` with `action="plan_day"` — this creates the actual calendar blocks

Never write a plan in text. The plan only exists if it's in the calendar.

## Schedule rules

- Work hours: 8am–5pm, Monday–Friday only
- Always protect 1pm–2pm as lunch (don't schedule work blocks there)
- No blocks shorter than 30 minutes
- Group similar tasks together (deep work / meetings / admin)
- Schedule high-focus work in the morning (peak energy hours)
- Leave buffer between consecutive blocks — don't pack the day solid
- If today is a weekend, ask before planning anything

## Block creation rules

- Every calendar event MUST use `-05:00` timezone offset (Lima), never `Z`
- Correct: `2026-08-03T09:00:00-05:00`
- Wrong: `2026-08-03T14:00:00Z` (this creates a 9am Lima block but sends it as 2pm UTC — it will appear at the wrong time)

## What to ask if unclear

- "Do you want to plan today or tomorrow?"
- "Do you have any fixed meetings or commitments I should work around?"
- "Anything specific you need to get done today that's not in your task list?"
