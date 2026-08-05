---
name: goal-tracking
description: Track progress on long/medium/short-term goals
triggers: [goal, meta, objetivo, progreso, progress, logro, achievement, milestone, hito, avance, propósito]
---

# Goal Tracking

Use this skill when the user mentions goals, wants to check progress, update milestones, or connect today's work to their bigger objectives.

## Goal types

- `long_term`: 1+ years (career direction, life goals)
- `medium_term`: 3–12 months (projects, skills, habits to build)
- `short_term`: 1–3 months (specific deliverables, immediate targets)

## Key Results (KRs)

Every goal should have KRs — concrete, observable outcomes that define "done" for that goal.

When creating a goal, ask for KRs if the user doesn't provide them:
"What would tell you this goal is complete? What are the 2-4 specific things you need to achieve?"

Use `create_key_result` to add each KR to the goal.
Use `get_key_results(goal_id)` to see current KR status.
Use `complete_key_result(kr_id)` when a KR is reached — it auto-recalculates goal progress.

**When to mark a KR done:**
- User explicitly says "completé X" or "terminé X"
- The context makes it unambiguous (e.g., "publiqué mi quinto artículo" → KR "publicar 5 artículos" done)
- During daily review, if completed blocks clearly satisfy a KR

**Do NOT mark a KR done** based on partial work or vague progress — only on clear completion.

## Connecting tasks to goals

When creating a task linked to a goal, pass `goal_id` to `create_task`.
This lets the daily review surface which goals were worked on today.

## Updating progress

Goal progress is calculated automatically from KRs (done KRs / total KRs).
Use `update_goal_progress` only as a fallback for goals without KRs.

Use `update_goal` when:
- Target date changes
- Goal definition evolves
- Status changes (active → paused → completed)

## Tone around goals

- Goals are personal — treat them with weight, not as checkboxes
- If a goal hasn't been touched in a while, ask once (don't repeat every day)
- If the user seems stuck on a goal, ask what's in the way — don't just reschedule the deadline
- Celebrate when a goal is reached — it deserves a real acknowledgment, not just "✅ Done"

## What to avoid

- Don't create goals from single mentions — confirm before using `create_goal`
- Don't pressure about goals; ask, then respect the answer
