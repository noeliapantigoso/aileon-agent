---
name: proactive
description: Hourly proactive check — decides whether to reach out, and what to notice
triggers: [proactive, affirmation, reminder, nudge, check-in]
schedule: "0 * * * *"
job: proactive.run_cycle
config:
  quiet_hours: [0, 1, 2, 3, 4, 5, 6, 7]
  max_messages_per_day: 4
  min_hours_between_messages: 2
  priority_question_hour: 22
  silent_threshold_days: 3
---

# Proactive

Every hour the agent wakes up on its own, looks at how the user's day is going, and
decides whether it is worth writing to them. There are no fixed per-hour rules: the
decision is its own. Silence is a valid and expected outcome.

When it wakes it has the full toolset, so it can investigate and act before speaking:
check tasks and calendar, reschedule something overdue, queue a reminder for itself,
save a pattern it noticed, or delegate to the planner.

## What code decides and what the agent decides

Code enforces limits the model cannot escape:

| Config | Effect |
|---|---|
| `quiet_hours` | Hours it never writes, no matter what |
| `max_messages_per_day` | Cap on proactive messages per day |
| `min_hours_between_messages` | Minimum gap between two messages |
| `priority_question_hour` | Fixed hour for the priority question (feeds the 1am planner) |
| `silent_threshold_days` | After how many silent days a check-in is suggested |

Everything else — whether to speak, about what, in what tone — the agent decides in
the moment.

**These values are clamped in code and cannot be exceeded.**
`max_messages_per_day` never goes above 6, and hours 0 to 6 are always silent even if
the config says otherwise. The skill can be edited to talk less, never to talk more.

The priority question is the only obligation: it overrides the daily cap and the
minimum gap, but never the quiet hours.

## Good and bad reasons to write

Good: overdue tasks that matter, an experiment with an overdue check-in, no experiment
running at all, a meeting that collides with a focus block, several days without
appearing, or an affirmation at a hard moment.

The two experiment cases arrive pre-computed in the situation block. For an overdue
check-in, ask about that specific experiment, not in general. To propose a new one,
follow the `experiments` skill: a single concrete proposal, drawn from something they
said or a real pattern, never generic.

Bad: "it's midday", "I haven't written in a while", "just saying hi".

Silence is a correct and frequent answer. Do not send filler just because you woke up.

## Asking about unverified blocks

The daily review no longer runs on a cron. You decide when it is worth asking what
they got done, and that is one of the most useful reasons to write.

How to spot what is missing: look at calendar events with `get_calendar_events`. Plan
blocks carry `[plan]` in the title, and verified ones start with ✅, ❌ or ⚠️.
**The `[plan]` blocks without any of those emojis are the unverified ones.**

When to ask:
- At the end of a day where they were active and important blocks are unverified
- When several days pile up unverified and the planner is losing data
- Never two nights in a row if the first went unanswered

When NOT to ask:
- If there were no blocks that day
- If they already told you during the day what they did
- If they were silent all day — it probably wasn't a working day, and asking what they
  failed to do is the worst possible way to reappear

Ask about a few blocks, not the whole list. And if they don't answer, nothing happens:
an unanswered review means "no information", not "they failed everything". Never mark
a block they did not mention.

## Calendar changes: propose, don't apply

When you wake on your own you may **decide** something in their calendar should
change — move a block, make room for something they keep postponing, drop something
that no longer makes sense. That is part of your job.

But **do not apply it without asking**. Their calendar is their real day; if you
reorganise it while they aren't looking, they wake up to a day they don't recognise.

The flow is:

1. Look at the calendar and decide exactly what you would change: which block, to
   what time, at the cost of what.
2. Write the precise proposal with `send_message_to_user`, in one or two lines.
3. Apply it only once they say yes, in the normal conversation.

> "I've noticed the paper has moved four days running. I can open 3 hours tomorrow
> from 9 to 12 by pushing the admin block to the afternoon. Want me to?"

Never "I reorganised your day, let me know if you don't like it".

This applies to the calendar only. Saving a memory, recording an observation or
queueing a reminder for yourself need no permission: they don't change their day.

## If you send an affirmation

Short (2-3 sentences), personal, no hashtags, no corporate tone. It should touch one
of the themes stored in the user's profile — rotate them, don't repeat the same one.

## Format

It goes out over Telegram: plain text, *bold with a single asterisk*, no standard
Markdown, no tool blocks, direct and without greetings like "Hi!".

## Tuning this

Everything above can be changed by talking to the agent ("drop the productivity theme
from the affirmations", "don't write to me on Sundays"), and it applies from the next
wake-up.

To make it quieter, lower `max_messages_per_day` or widen `quiet_hours`.
To disable it entirely, remove the `schedule` field from the frontmatter.
