---
name: daily-review
description: End-of-day review of what was completed — never assumes, always asks
triggers: [review, cómo me fue, qué hice, cumplí, terminé, how did, end of day, cierre, fin del día, what did i, logré]
schedule: "0 0 * * *"
job: planner.daily_review
---

# Daily Review

Use this skill when the user asks how their day went, wants to close out the day, or the evening review job fires.

## The most important rule

**Never assume tasks were completed.** If the user hasn't confirmed something was done, treat it as not done. Silence is not confirmation.

- User said nothing → task was NOT completed
- User said "yes" or confirmed → task was completed
- User said "partially" → mark as in_progress, not done

## When triggered by the user (chat)

1. Call `delegate_to_planner` with `action="daily_review"` and include the user's message as `instruction`
2. The planner lists today's calendar blocks
3. Ask: "Which of these did you actually complete?" — list them clearly
4. Wait for the user's response before marking anything
5. After confirmation: mark completed blocks as done, update Notion task status accordingly

## Steps for the automated review job

1. List today's plan blocks from the calendar
2. If user_input is empty → treat all blocks as not completed
3. Based ONLY on what the user explicitly said, determine which blocks were completed
4. Call `mark_block_completed` for EVERY block (no exceptions):
   - Confirmed by user → `"true"`
   - Not mentioned, user said no, or said partially → `"false"`

   When `completed=false` and the block has no linked task, the system automatically creates
   a Notion task so nothing gets lost. You don't need to do anything extra.
5. For experiments: only call `log_experiment_progress` if the user confirmed the experiment block was done
6. For goals: check if any completed blocks had a `goal_id`. If so, call `get_key_results(goal_id)` and
   read the pending KRs. If the work done clearly satisfies a KR (title matches what was done), call
   `mark_kr_done`. Do not mark KRs based on ambiguous evidence — only clear matches.
7. Generate a Telegram-ready review summary that includes:
   - ✅ Blocks completed (list them)
   - ❌ Blocks not done → mention that a Notion task was created for each
   - 🎯 Any KR completed: "KR completado: [title] → meta al X%"
   - One observation about the day (pattern, win, or something to watch)
   Keep it concise and scannable — no wall of text.

## Tone

- Empathetic, not judgmental about unfinished work
- If very little was done: acknowledge it, don't lecture
- If a lot was done: celebrate it genuinely, not generically
- Ask one focused follow-up if there's a clear pattern (e.g., same task moved 3 days in a row)

## What to avoid

- Don't say "Great job completing everything!" before the user confirms
- Don't list tomorrow's plan unprompted — only if the user asks
- Don't turn the review into a therapy session
