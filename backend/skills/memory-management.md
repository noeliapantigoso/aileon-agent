---
name: memory-management
description: What to remember about the user, where to store it, and what to tell them
triggers: [remember, memory, forget, what do you know, profile, my info]
---

# Memory Management

Use this skill when deciding what to store about the user, where it belongs, or when
they ask to see, change or delete what you know.

## Two stores, one decision

There are two places to save something about the user, and picking the wrong one is
the most common mistake. They are not interchangeable:

| | `update_my_profile` (profile) | `remember_fact` (Mem0) |
|---|---|---|
| When it's read | **Always**, whole, in every prompt of chat, planner and weekly analysis | Only when semantically relevant to the current message |
| Cost | Tokens on **every single turn** | Only when retrieved |
| Size | Must stay tight (~3,000 chars total) | Can grow freely |

**The test:** *does this change how you should treat them even when the conversation
is about something completely unrelated?*

- **Yes → profile.** "Works freelance, available all seven days." "Trains 6 days a
  week, 1.5h per session." "Tends toward workaholism." "Hates meetings before 10am."
  These shape every plan and every reply, whatever the topic.
- **No → `remember_fact`.** "Prefers Notion over Todoist." "Their sister is called
  Ana." "The paper they're writing is about computer vision." These only matter when
  that specific subject comes up.

If the profile is already near its size limit, do not append: consolidate two entries
into one with `replaces`, or demote the less central one to `remember_fact`.

## Always say what you saved

The user cannot see the contents of either store. If you save something and don't
quote it back, they are blind to a fact that will change your behaviour in every
conversation from now on.

After each write, one line with the exact text and the destination:

  📌 Saved to your profile: "..."
  🧠 Saved to memory: "..."
  🗑️ Removed from your profile: "..."

If you replaced an entry, show what was there before and what replaced it. Never
summarise it as "updated your preferences": that tells them nothing.

This applies in the proactive cycle too. If you save an observation and also write to
them, mention it. If you save and choose to stay silent, don't write just to announce
it — it is already in the log.

## What you observe counts too

Not everything you know came from them. During proactive wake-ups you will notice
patterns by looking at their calendar and tasks — things they cannot see.

Those observations are worth as much as what they tell you, and are lost just as
easily. Store them with the same rule above, and mark them as observed so you never
confuse what you saw with what they claim:

- ✅ "Observed: tasks from project X postponed since July 20th, rescheduled four times."
- ❌ "Seems to struggle with that project." (vague, no evidence, not actionable)

The distinction matters: if they later tell you they love that project, you need to be
able to notice the contradiction between what they say and what they do, instead of
holding two loose statements that quietly conflict.

## Saving a new memory

Call `remember_fact` when they explicitly ask — "remember that", "keep in mind",
"from now on" — **and also when they simply tell you something durable without asking
you to save it.**

They will not phrase it as a request. Waiting for magic words means losing most of
what matters. Run the test above first to decide which store it belongs in.

Write the fact as a clear, third-person statement:
- ✅ "Prefers not to be given advice when overwhelmed — just ask what they need"
- ❌ "user said they don't like advice" (too vague)

### Facts, not instructions

Write memories as declarative statements about reality, never as orders to yourself.

- ✅ "Trains 6 days a week, 1.5h per session"
- ❌ "Always leave 1.5h free for the gym"

An imperative gets re-read as a directive in a later session and can override what
they are actually asking for at that moment. Facts describe; they don't command.

### What does NOT belong in memory

- **Anything that will be stale in a week.** Task progress, "finished X", "moved Y to
  Friday", completed work. That lives in Notion and the calendar, not here.
- **Procedures and workflows.** If you worked out a way of doing something that should
  be repeated, that is a skill — use `save_skill`, not `remember_fact`.

Memory is for durable facts about who they are and how they live. Skills are for how
to do things. Keep them separate.

### Keep it tight

Memory is injected into every prompt, so it costs context on every turn. Before adding,
check with `list_memories` whether a similar fact already exists — update it instead of
stacking a near-duplicate. Prioritise what stops them from having to correct or remind
you again.

## Listing memories

Call `list_memories` when they ask "what do you know about me?", "what do you
remember?", or "show me my memories".

Present the list cleanly — one line each. If it is long (10+), summarise by theme.

## Forgetting

1. Call `list_memories` first to find the memory and its ID
2. Confirm with them which one to delete
3. Call `forget_memory` with the ID and text

For profile entries, use `forget_about_me` — or `update_my_profile` with `replaces`
if it should be corrected rather than removed.
