---
name: verify-blocks
description: Track experiment block progress during the day — completion marking is handled by daily-review
triggers: [verify, verificar, bloques, cumplimiento, reschedule]
schedule: "0 8,10,12,14,16,18,20,22 * * *"
job: planner.verify_recent
---

# Verify Blocks

Runs every 2 hours during the day. Its only job is to log experiment progress for blocks that have passed — NOT to mark general block completion (that's daily-review's job at 10pm).

## When triggered by the automated verify job

### Steps

1. For each plan block that has passed and has an `experiment_id` and is **not yet logged**:
   - Call `log_experiment_progress` with `did_it=true`
   - Do NOT call `mark_block_completed` — leave that for daily-review

2. For blocks with no `experiment_id` → skip entirely

3. If the same experiment has 2+ unlogged blocks in the last 3 days → flag it in the summary

4. Reply with a compact summary: how many experiment blocks logged, any flags

### What NOT to do

- Never call `mark_block_completed` — daily-review owns that at 10pm
- Don't touch blocks that are in the future
- Don't reschedule anything — daily-review handles that too
