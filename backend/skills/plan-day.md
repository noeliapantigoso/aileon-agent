---
name: plan-day
description: Create a structured work plan for the day with calendar blocks
triggers: [plan, organize, schedule, tomorrow, day, week, agenda, morning]
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

## Core principle: objectives, not hours

Plan around what gets ADVANCED, never around filling the calendar. Two real
priorities moved forward is a good day. Eight one-hour blocks is a bad plan even
when every hour is accounted for — slicing a goal into daily 1h fragments does not
move it, it only makes the calendar look busy.

Empty space in the day is a feature, not a gap to fill.

## Planning rules (used by both chat agent and automated planner job)

1. MAXIMUM 1-2 priorities per work day. Never more. Pick only the ones that genuinely
   move a goal or a KR forward, and leave the rest of the day unscheduled.
2. Each priority gets ONE substantial block of 2-3 contiguous hours. Never split a
   single objective into several short blocks spread across the day.
3. Think in weeks, not days. Before planning a day, look at the whole week: which
   objectives should advance this week, and which ones already got their block. Do
   NOT give the same goal a slot every day — spread objectives across the week so
   each one gets real depth on its own day.
4. NEVER distribute "remaining hours until target_date" proportionally across days.
   That is what produces fragmented, useless plans. Ask instead: what is the next
   concrete outcome for this goal, and which single day this week earns it?
5. Deep work / high focus → profile peak hours (default 9am-12pm)
6. Operational / admin / repetitive tasks → low energy hours (default 1pm-3pm), and
   batch them all into ONE block instead of scattering them through the day
7. Active experiments keep their own cadence (check_in_every_days) but do NOT count
   toward the 1-2 daily priorities — they are small and recurring, a different thing
8. NEVER overwrite existing fixed events (meetings, external commitments)
9. Lunch 1pm-2pm is NOT optional — always protect it
10. The plannable window and which days count as working days come from the USER
    PROFILE in the context above — read it, never assume a default office schedule.
    - Do NOT treat `work_start` / `work_end` in the calibration block as the end of
      the day. Those are measured activity hours, not a boundary.
    - Unless the profile says otherwise, plan every day of the week, weekends
      included. NEVER ask whether to plan a weekend: the automated job runs at 1am,
      there is nobody awake to answer, and an unanswered question means no plan at all.
    - Protect a dinner break, and leave the last stretch before bed unscheduled.
    - If the user already scheduled something on a day, rule 8 protects it.
    The 1-2 priority limit applies to every day, weekends included.
11. If the week's objectives don't fit → say so explicitly and propose what to drop
    or postpone. Never compress everything into shorter blocks to make it fit.
12. Based on completion history: DO NOT schedule deep work at hours with <40%
    historical completion rate

## Steps for the automated planner job

1. Call `list_calendar_events` for the target day AND for the rest of the week, to see
   both the fixed events and which objectives already got their block this week
2. Call `get_completion_history` to check real historical patterns before placing deep work
3. Decide the 1-2 priorities for this day. Everything else stays out of the calendar.
4. For each block, call `create_block` with its goal_id / experiment_id / task_id when applicable
5. End with a Telegram-ready summary that includes:
   - The day's priorities (1-2), each with the concrete outcome it should produce
     (e.g. "9am-12pm — Literature review → close out the state of the art section")
   - What is deliberately NOT happening today, and which day of the week it lands on
   - Anything that doesn't fit in the week at all, stated plainly
   Keep it scannable — bullet points, no wall of text. Do not report total hours planned;
   report what advances.

## Datetime format (critical)

Every datetime MUST carry the user's UTC offset (see their timezone in the
context above). Example below uses `-05:00`.

- Correct: `2026-08-03T09:00:00-05:00`
- Wrong: `2026-08-03T14:00:00Z` (means a 9am local block but sends 2pm UTC — lands at the wrong time)

Never use `Z` suffix for blocks you create.
