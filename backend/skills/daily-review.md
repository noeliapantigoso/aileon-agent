---
name: daily-review
description: End-of-day review of what was completed — never assumes, always asks
triggers: [review, cómo me fue, qué hice, cumplí, terminé, how did, end of day, cierre, fin del día, what did i, logré]
---

# Daily Review

Use this skill when the user asks how their day went, wants to close out the day, or the evening review cron fires.

## The most important rule

**Never assume tasks were completed.** If the user hasn't confirmed something was done, treat it as not done. Silence is not confirmation.

- User said nothing → task was NOT completed
- User said "yes" or confirmed → task was completed
- User said "partially" → mark as in_progress, not done

## Procedure

1. Call `delegate_to_planner` with `action="daily_review"` and include the user's message as `instruction`
2. The planner lists today's calendar blocks
3. Ask: "Which of these did you actually complete?" — list them clearly
4. Wait for the user's response before marking anything
5. After confirmation: mark completed blocks as done, update Notion task status accordingly

## Tone

- Empathetic, not judgmental about unfinished work
- If very little was done: acknowledge it, don't lecture
- If a lot was done: celebrate it genuinely, not generically
- Ask one focused follow-up if there's a clear pattern (e.g., same task moved 3 days in a row)

## What to avoid

- Don't say "Great job completing everything!" before the user confirms
- Don't list tomorrow's plan unprompted — only if the user asks
- Don't turn the review into a therapy session
