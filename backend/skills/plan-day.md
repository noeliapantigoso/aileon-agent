---
name: plan-day
description: Create a structured work plan for the day with calendar blocks
triggers: [plan, organize, schedule, organiza, planea, mañana, tomorrow, day, día, semana, week, agenda, morning]
schedule: "0 1 * * *"
job: planner.plan_day
---

# Plan Day

Use this skill when the user wants to plan their day, organize their schedule, prepare for tomorrow, or set up their week.

## When triggered by the user (chat)

1. Check `get_tasks` with today's date to see what's pending
2. Check `get_calendar_events` to see existing commitments
3. Call `delegate_to_planner` with `action="plan_day"` — this creates the actual calendar blocks

Never write a plan in text. The plan only exists if it's in the calendar.

## What to ask if unclear

- "Do you want to plan today or tomorrow?"
- "Do you have any fixed meetings or commitments I should work around?"
- "Anything specific you need to get done today that's not in your task list?"

## Planning rules (used by both chat agent and automated planner job)

1. Deep work / high focus tasks → only during profile peak hours (default 9am-12pm)
2. Operational / repetitive / admin tasks → low energy hours (default 1pm-3pm)
3. Each active experiment needs its daily slot, or at its check_in_every_days cadence
4. For each short-term goal: calculate remaining hours until target_date and distribute proportionally across days
5. 10-15 min buffer between consecutive blocks — don't pack the day solid
6. NEVER overwrite existing fixed events (meetings, external commitments)
7. Blocks: 30-90 min each — no single block longer than 2h without a break
8. Lunch 1pm-2pm is NOT optional — always protect it
9. Work hours: 8am-5pm, Monday-Friday only. Weekend → skip or ask
10. If required hours > available hours → flag it at the end of the plan, don't silently omit tasks
11. Based on completion history: DO NOT schedule deep work at hours with <40% historical completion rate

## Steps for the automated planner job

1. Call `list_calendar_events` to see fixed events already in the calendar for the target day
2. Call `get_completion_history` to check real historical patterns before placing deep work blocks
3. For each block, call `create_block` with its goal_id / experiment_id / task_id when applicable
4. End with a Telegram-ready summary that includes:
   - Each block created: time, title, and ONE sentence explaining why it was placed there
     (e.g. "9-10:30am — Deep work ML → peak hour, KR 'Completar curso' pendiente")
   - Any tasks that didn't fit and why
   - Total hours planned
   Keep it scannable — bullet points, no wall of text.

## Datetime format (critical)

Every datetime MUST use the Lima offset: `-05:00`

- Correct: `2026-08-03T09:00:00-05:00`
- Wrong: `2026-08-03T14:00:00Z` (creates a 9am Lima block but sends it as 2pm UTC — wrong time in calendar)

Never use `Z` suffix for blocks you create.
