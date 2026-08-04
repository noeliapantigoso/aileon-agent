---
name: task-management
description: Create, update, and prioritize tasks in Notion
triggers: [task, tarea, pendiente, todo, to-do, recordatorio, reminder, agregar, add, create, crear, update, actualizar, prioritize, priorizar, pending]
---

# Task Management

Use this skill when the user wants to add, update, search, or organize tasks in Notion.

## Creating tasks

Call `create_task` with:
- `title`: actionable verb + object ("Review Q3 report", "Send invoice to client")
- `priority`: use this matrix:
  - `p0` = urgent AND important (deadline today, blocking others)
  - `p1` = important, not urgent (strategic work, goals-related)
  - `p2` = useful but not critical (default for most tasks)
  - `p3` = low priority, can wait
- `due_date`: only if there's a real deadline (YYYY-MM-DD)
- `scheduled_date`: the day the user plans to do it (YYYY-MM-DD)
- `time_estimate_minutes`: ask if not obvious; helps with day planning

## Updating tasks

Use `update_task` when:
- User says a task is done → set `status="done"`
- User pushes a task to another day → update `scheduled_date`
- User changes priority
- Task is partially done → set `status="in_progress"`

## Prioritization guidance

When the user has too many tasks, help them decide by asking:
- "Does this block anyone else if it's not done today?"
- "Does this connect to one of your current goals?"
- "What's the real deadline — or is that self-imposed?"

## What NOT to do

- Don't create duplicate tasks — check context first
- Don't set every task as p0/p1 — most things are p2
- Don't add `due_date` just to fill the field; leave it empty if there's no real deadline
