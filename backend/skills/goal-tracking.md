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

## Connecting tasks to goals

When creating a task that relates to a goal, mention the connection:
"Added 'Review course material' — this connects to your [goal name] goal."

When reviewing the day, notice if goal-related tasks were done and highlight the progress.

## Updating progress

Use `update_goal_progress` when:
- User completes a milestone
- User explicitly says they've made progress
- A completed task clearly advances a goal

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
