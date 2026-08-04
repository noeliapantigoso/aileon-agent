---
name: memory-management
description: How to manage what the assistant remembers about the user
triggers: [remember, recuerda, olvida, forget, memoria, memory, sé de mí, sabes de mi, qué sabes, what do you know, perfil, profile, actualiza, update my, cambió, changed, ya no, no longer]
---

# Memory Management

Use this skill when the user wants to manage what you know about them.

## Saving a new memory

Call `remember_fact` when the user says:
- "remember that..."
- "keep in mind that..."
- "from now on..."
- "I want you to always know that..."

Write the fact as a clear, third-person statement:
- ✅ "Noe prefers not to be given advice when she says she's overwhelmed — just ask what she needs"
- ❌ "user said she doesn't like advice" (too vague)

## Listing memories

Call `list_memories` when the user asks:
- "what do you know about me?"
- "what do you remember?"
- "show me my memories"

Present the list cleanly — one line per memory. If the list is long (10+), summarize by theme.

## Forgetting a memory

1. Call `list_memories` first to find the memory and its ID
2. Confirm with the user which one to delete
3. Call `forget_memory` with the ID and text

Never delete without showing the user what will be removed.

## Updating the profile

Call `update_my_profile` when the user says their info changed:
- "my schedule changed, now I work 9 to 6"
- "I changed jobs, I'm now at..."
- "I prefer more detailed responses"

Common fields:
- `name` — display name
- `occupation` — job title
- `company` — where they work
- `productivity.work_start` — e.g. "09:00"
- `productivity.work_end` — e.g. "18:00"
- `preferences.communication_style` — e.g. "Brief and direct"

After updating, confirm: "Got it — updated your [field] to [value]."
