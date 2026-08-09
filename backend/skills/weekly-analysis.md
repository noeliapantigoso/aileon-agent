---
name: weekly-analysis
description: Sunday evening weekly analysis — patterns, insights, and next-week prep
triggers: [weekly, week review, weekly analysis, patterns]
schedule: "0 20 * * 0"
job: insights.weekly_analysis
---

# Weekly Analysis

Runs every Sunday at 8pm in the user's timezone (see the `schedule` field).

## What it does

1. Analyzes the last 7 days of interactions and calendar data
2. Detects patterns: energy cycles, completion rates, recurring blockers
3. Saves new insights to Firestore (surfaced in future conversations)
4. Generates a brief weekly summary and sends it via Telegram

## Rules

- Only surfaces patterns seen 3+ times — no single-data-point insights
- Insights decay: marks old ones as inactive if the pattern hasn't appeared in 3 weeks
- Never lectures — frames patterns as observations, not judgments

## To change the schedule

Ask the bot: "run the weekly analysis on Friday evenings instead", or edit the
`schedule` field in this frontmatter.
