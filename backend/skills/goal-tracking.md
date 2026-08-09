---
name: goal-tracking
description: Track progress on long, medium and short-term goals
triggers: [goal, objective, progress, achievement, milestone, key result, okr]
---

# Goal Tracking

Use this skill when the user mentions goals, wants to check progress, update
milestones, or connect today's work to their bigger objectives.

## Goal types

- `long_term`: 1+ years (career direction, life goals)
- `medium_term`: 3–12 months (projects, skills, habits to build)
- `short_term`: 1–3 months (specific deliverables, immediate targets)

## Creating a goal — mandatory flow

**NEVER create a goal without a deadline and without KRs.** Always follow these steps:

### Step 1 — gather the inputs (in a single question if anything is missing)

Before calling any tool, make sure you have:
- A clear goal title
- A deadline (`target_date`) — if they say "in 6 months", compute the exact date
- An area: work / personal / health / finance / learning / relationships
- At least 2 concrete, observable KRs

If something is missing, ask for all of it at once:

> "To create the goal I need: what's the deadline? Which area does it belong to (work,
> learning, health…)? And what are the 2-4 concrete results that would tell you you'd
> achieved it?"

### Step 2 — create the goal

Call `create_goal` with title, goal_type, area and target_date.

### Step 3 — create the KRs

For each one, call `create_key_result(goal_id, title)`.

KRs must be observable: "Finish course X", "Publish 3 articles", "Reach 10k followers".
Never create vague KRs like "get better at ML" — reformulate them first.

### Step 4 — show a summary

```
✅ Goal created: [title]
📅 Deadline: [date]
🎯 Key Results:
  1. [KR1]
  2. [KR2]
  3. [KR3]
```

## Reading and updating goals

- `get_goals` — current state
- `get_key_results(goal_id)` — KRs of one goal, or `unassigned_only=true` for orphans
- `complete_key_result(kr_id)` — when a KR is reached; recalculates progress automatically
- `update_key_result(kr_id, goal_id=...)` — to reassign an orphan KR to its goal
- `update_goal_progress` — fallback only, for goals with no KRs
- `update_goal` — change title, date, area or status (active → paused → completed)
- `archive_goal` / `archive_key_result` — soft-delete when they ask to remove one

**When to mark a KR done:**
- They explicitly say "I completed X" or "I finished X"
- The context makes it unambiguous ("I published my fifth article" → KR "publish 5
  articles" is done)
- During a review, if a completed block clearly satisfies a KR

## Connecting tasks to goals

When creating a task linked to a goal, pass `goal_id` to `create_task`.

## Tone around goals

- Goals are personal — treat them with weight, not as checkboxes
- If a goal hasn't been touched in a while, ask once; don't repeat it every day
- If they seem stuck, ask what's in the way instead of just moving the deadline
- Celebrate a reached KR or goal genuinely — it deserves more than "✅ Done"

## What to avoid

- Don't create goals from a single passing mention — confirm first
- Don't pressure about goals; ask, then respect the answer
