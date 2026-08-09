---
name: experiments
description: Track personal experiments and habits the user is testing
triggers: [experiment, habit, try, testing, hypothesis, check-in, tracking]
---

# Experiments

Use this skill when the user wants to run a personal experiment, track a habit, log
progress, or do a check-in.

## What is an experiment?

A time-boxed test of a hypothesis about behaviour, productivity, health or any area of
life. Example: "If I wake up at 6am for two weeks, I'll feel more productive."

What distinguishes it from a goal is the **hypothesis** and the **end date**. It isn't
something to achieve, it's something to find out.

## There should always be one running

The user wants an active experiment at all times. **If there is none, it is your job to
propose one** — don't wait to be asked.

The proactive cycle tells you when the list is empty. When that happens, don't fire off
a generic suggestion: pick well and wait for a reasonable moment.

### Where to get the idea

In this order of preference:

1. **Something they keep complaining about.** If they've mentioned twice that mornings
   are hard to start, there's an experiment there.
2. **A pattern from the insights.** If the weekly analysis noticed that afternoon blocks
   are completed half as often as morning ones, propose testing the change.
3. **An active goal that has stalled.** If a KR hasn't moved in weeks, maybe the method
   is the problem — and that can be tested.
4. **Something from their profile.** Experiments about rest, boundaries or disconnecting
   are as valid as productivity ones.

### How to propose it

One concrete proposal, with hypothesis and duration, in two or three lines. Don't offer
a menu of five options, and don't ask "do you want to run an experiment?" in the
abstract.

> "I've noticed blocks after 4pm get completed half as often as morning ones. Want to
> try two weeks with deep work before noon and see if completion goes up? Check-in every
> 3 days."

Wait for confirmation before calling `start_experiment`. If they say no, don't insist
that day — note that the topic doesn't interest them and try another angle later.

### When NOT to propose

- If they just closed one today: give it room.
- If they're in the middle of something else: don't interrupt, this is the least urgent
  thing you have.
- If they're coming off a bad stretch: proposing an experiment when they already feel
  behind reads as pressure. Support first.

## Starting an experiment

Call `start_experiment` with:
- `name`: short descriptive label
- `hypothesis`: "If I do X, then Y will happen"
- `duration_days`: how long to run it (default 14 if unspecified)
- `check_in_every_days`: how often to log progress (default 3)

Ask before creating: "What are you trying to test, and how will you know if it worked?"

## Logging progress

Use `log_experiment_progress` when:
- They report how the experiment is going
- It is check-in time (the proactive cycle surfaces overdue ones)
- They share observations or results

Capture both the quantitative side (if any) and the qualitative notes.

## Closing an experiment

Use `close_experiment` when the duration ends, or when they explicitly want to stop or
declare a result. Ask: "What did you learn? Would you turn it into a permanent habit?"

The weekly analysis also auto-closes stale ones: completed when the duration is up,
abandoned when there has been no activity or completion is under 30%.

## Tone

- Experiments are explorations, not performance reviews — curiosity over judgement
- A failed experiment is data, not failure: "that tells us something useful"
- Encourage small, specific experiments over vague intentions
