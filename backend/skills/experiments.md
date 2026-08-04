---
name: experiments
description: Track personal experiments and habits the user is testing
triggers: [experiment, experimento, hábito, habit, probar, testing, ensayo, hypothesis, hipótesis, check-in, tracking]
---

# Experiments

Use this skill when the user wants to run a personal experiment, track a habit, log progress on an ongoing experiment, or do a check-in.

## What is an experiment?

A personal experiment is a time-boxed test of a hypothesis about behavior, productivity, health, or any life area. Example: "If I wake up at 6am for 2 weeks, I'll feel more productive."

## Starting an experiment

Call `start_experiment` with:
- `name`: short descriptive label
- `hypothesis`: "If I do X, then Y will happen"
- `duration_days`: how long to run it (default 14 days if not specified)
- `check_in_every_days`: how often to log progress (default 3 days)
- `metrics`: what to track (optional but useful)

Ask before creating: "What are you trying to test, and how will you know if it worked?"

## Logging progress

Use `log_experiment_progress` when:
- User reports how the experiment is going
- It's check-in time (the proactive service will trigger this)
- User shares observations or results

Capture both quantitative (if any) and qualitative notes.

## Closing an experiment

Use `close_experiment` when:
- Duration ends
- User explicitly wants to stop or declare a result
- Ask: "What did you learn from this? Would you turn it into a permanent habit?"

## Tone

- Experiments are explorations, not performance reviews — curiosity over judgment
- If an experiment failed, that's data, not failure: "That tells us something useful"
- Encourage small, specific experiments over vague intentions
