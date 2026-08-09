---
name: daily-review
description: End-of-day review of what was completed — never assumes, always asks
triggers: [review, how did it go, what did i do, finished, completed, end of day, wrap up]
job: planner.daily_review
---

> No `schedule`: this no longer runs on a midnight cron. The proactive cycle decides
> when to ask about unverified blocks, and the user also reports progress ad hoc during
> the day. To restore the nightly cron, add `schedule: "0 0 * * *"` to the frontmatter.

# Daily Review

Use this skill when the user asks how their day went, wants to close out the day, or
the review job fires.

## The most important rule

**Never invent data in either direction.** Only what the user explicitly says gets
recorded.

- They confirmed it → mark completed
- They said "partially" → mark as in_progress, not done
- They said no → mark not completed
- **They said nothing at all → leave the block UNVERIFIED. Do not mark it, do not
  create a Notion task for it, do not log it as failed.**

That last case is normal and expected — the user often will not answer, and that is
fine. An unanswered review means "no information", NOT "they failed everything".
Recording silence as failure poisons the completion history just as badly as recording
it as success would.

## When triggered by the user (chat)

1. Call `delegate_to_planner` with `action="daily_review"` and pass their message as
   `instruction`
2. The planner lists today's calendar blocks
3. Ask: "Which of these did you actually complete?" — list them clearly
4. Wait for their response before marking anything
5. After confirmation: mark completed blocks as done and update Notion task status

## Ad-hoc reports during the day

The user reports progress whenever it suits them, not only at night. If at any point
they say something like "I finished the literature review", "I didn't get to X", or "I
moved Y to tomorrow", mark that block right then with `mark_block_completed` — do not
wait for a nightly review. This is the primary path, not the exception.

## Steps for the automated review job

1. List today's plan blocks from the calendar
2. If user_input is empty → STOP. Mark nothing, create nothing, log nothing.
   An unanswered review is not evidence of anything.
3. Based ONLY on what they explicitly said, determine which blocks they addressed
4. Call `mark_block_completed` ONLY for the blocks they actually mentioned:
   - Confirmed done → `"true"`
   - They said they didn't do it → `"false"`
   - Not mentioned at all → skip the block entirely, leave it unverified

   When `completed=false` and the block has no linked task, the system automatically
   creates a Notion task so nothing gets lost. You don't need to do anything extra.
5. For experiments: only call `log_experiment_progress` if they confirmed the
   experiment block was done
6. For goals: check whether any completed block had a `goal_id`. If so, call
   `get_key_results(goal_id)` and read the pending KRs. If the work clearly satisfies a
   KR (title matches what was done), call `mark_kr_done`. Never mark a KR on ambiguous
   evidence — only clear matches.
7. Generate a Telegram-ready summary that includes:
   - ✅ Blocks completed (list them)
   - ❌ Blocks not done → mention that a Notion task was created for each
   - 🎯 Any KR completed: "KR completed: [title] → goal now at X%"
   - One observation about the day (a pattern, a win, or something to watch)
   Keep it concise and scannable — no wall of text.

## Tone

- Empathetic, never judgmental about unfinished work
- If very little was done: acknowledge it, don't lecture
- If a lot was done: celebrate it genuinely, not generically
- Ask one focused follow-up if there is a clear pattern (e.g. the same task moved three
  days running)

## What to avoid

- Don't say "Great job completing everything!" before they confirm
- Don't list tomorrow's plan unprompted — only if they ask
- Don't turn the review into a therapy session
