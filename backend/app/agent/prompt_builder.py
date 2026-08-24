"""
Dynamic system-prompt assembly for the agent.

The prompt is rebuilt on every request, injecting:
- The user profile (free prose, no schema)
- Temporal context (date, time, weekday)
- Work context (today's agenda and tasks)
- Relevant memories from Mem0

`build_user_context` is the single place where "who the user is" is assembled.
Chat, planner and weekly insights all pass it as their system instruction, so a
new kind of fact about the user reaches all three by construction.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

WEEKDAYS = {
    0: "Monday",
    1: "Tuesday",
    2: "Wednesday",
    3: "Thursday",
    4: "Friday",
    5: "Saturday",
    6: "Sunday",
}


def _humanize_key(key: str) -> str:
    """'work_start' -> 'Work start'. Only used for calibration fields."""
    return key.replace("_", " ").capitalize()


def build_user_context(
    user_profile: dict[str, Any],
    current_datetime: datetime,
    relevant_memories: list[str] | None = None,
    relevant_principles: list[dict[str, Any]] | None = None,
    active_insights: list[dict[str, Any]] | None = None,
) -> str:
    """
    Who the user is. Shared by EVERY prompt that reasons about their life.

    This is the only function that assembles user context. Chat, planner and
    insights all pass it as `system_instruction`, so anything added here reaches
    all of them by construction — nobody has to remember to wire it up again.

    It contains nothing task-specific: no daily agenda, no skills, no chat
    behaviour rules. Each caller adds those on top.
    """
    parts: list[str] = []

    weekday = WEEKDAYS.get(current_datetime.weekday(), "")
    parts.append(
        f"## Current time\n\n"
        f"- Date: {weekday}, {current_datetime.strftime('%B %d, %Y')}\n"
        f"- Time: {current_datetime.strftime('%H:%M')}\n"
        f"- Timezone: {current_datetime.tzname() or 'local'}"
    )

    # Profile: free prose, no fields. Injected whole, always.
    entries = user_profile.get("entries") or []
    if entries:
        total = sum(len(e) for e in entries)
        prof = [f"## User profile  ({len(entries)} entries, {total:,} chars)\n"]
        prof.extend(f"- {e}" for e in entries)
        parts.append("\n".join(prof))

    # Calibration: what the system measured, not what the user stated.
    calibration = user_profile.get("calibration") or {}
    if calibration:
        cal = ["## Calibration (measured by the weekly analysis, not self-reported)\n"]
        for key, value in calibration.items():
            if value not in (None, "", []):
                cal.append(f"- {_humanize_key(key)}: {value}")
        parts.append("\n".join(cal))

    if relevant_memories:
        mem = ["## Relevant memories\n"]
        mem.append("Facts from earlier conversations that may be useful:\n")
        mem.extend(f"{i}. {m}" for i, m in enumerate(relevant_memories, 1))
        parts.append("\n".join(mem))

    if active_insights:
        ins = ["## Detected patterns (from past analyses)\n"]
        ins.append(
            "These were detected by analysing past interactions. TAKE THEM INTO "
            "ACCOUNT — they are real data about the user, do not invent more. "
            "Only mention one if it is directly relevant.\n"
        )
        for i in active_insights:
            ins.append(
                f"- **{i.get('title', '')}** ({i.get('category', '')}): "
                f"{i.get('description', '')} -> {i.get('actionable', '')}"
            )
        parts.append("\n".join(ins))

    if relevant_principles:
        princ = ["## Knowledge base — available principles\n"]
        princ.append(
            "You have access to these distilled principles. READ THE MESSAGE AND "
            "THE CONTEXT, then pick 1-3 that GENUINELY apply. If none apply, do "
            "not force it — answer without invoking any.\n"
        )
        for i, p in enumerate(relevant_principles, 1):
            applies = ", ".join(p.get("applies_when", []))
            princ.append(
                f"### {i}. {p.get('title', '')} (id: `{p.get('id', '')}`)\n"
                f"**Principle:** {p.get('principle', '')}\n"
                f"**Applies when:** {applies}\n"
                f"**Concrete action:** {p.get('actionable', '')}\n"
                f"**Quote:** \"{p.get('quote', '')}\""
            )
        parts.append("\n\n".join(princ))

    return "\n\n---\n\n".join(parts)


def build_system_prompt(
    user_profile: dict[str, Any],
    today_context: dict[str, Any],
    relevant_memories: list[str],
    current_datetime: datetime,
    relevant_principles: list[dict[str, Any]] | None = None,
    active_insights: list[dict[str, Any]] | None = None,
    active_skills: str | None = None,
    active_experiments: list[dict[str, Any]] | None = None,
    integrations: set[str] | None = None,
) -> str:
    """
    Assemble the full system prompt for a chat turn.

    Args:
        user_profile: Profile from the document store ({entries, calibration}).
        today_context: Today's agenda, pending tasks, active goals.
        relevant_memories: Episodic memories retrieved from Mem0.
        current_datetime: Current date and time in the user's timezone.
        integrations: Which optional integrations are configured. Sections that
            depend on one are left out when it is absent, so the prompt never
            describes a capability the model was not given. None means all.

    Returns:
        The complete system prompt.
    """
    sections = []

    # ── 1. ROLE ──────────────────────────────────────────────────────────
    # No name and no communication style hardcoded here: both come from the
    # profile, which is injected right below.
    sections.append("""## Role

You are the personal assistant of the user described below. You help \
them to live a fulfilling life by acting as someone who is always rethinking about
their behaviors, organising their day, managing tasks, capturing ideas and staying focused on their goals.

Communication rules:
- Reply in the SAME language the user writes to you in
- Tone: direct, efficient, empathetic, with subtle humour when it fits
- Follow the communication style stated in their profile
- For a simple action (saving a note, creating a task), a short confirmation is enough
- For a question, answer concisely
- For planning, propose a structured plan
- Do not repeat back what the user said — act on it""")

    # ── 2. USER CONTEXT (shared with planner and insights) ───────────────
    sections.append(build_user_context(
        user_profile=user_profile,
        current_datetime=current_datetime,
        relevant_memories=relevant_memories,
        relevant_principles=relevant_principles,
        active_insights=active_insights,
    ))

    # ── 3. TODAY ─────────────────────────────────────────────────────────
    agenda_lines = ["## Today"]

    today_tasks = today_context.get("today_tasks", [])
    if today_tasks:
        agenda_lines.append("\n### Tasks scheduled for today:")
        for task in today_tasks[:10]:
            status = task.get("status", "?")
            priority = task.get("priority", "")
            title = task.get("title", "Untitled")
            estimate = task.get("time_estimate", "")
            line = f"- [{status}] {title}"
            if priority:
                line += f" ({priority})"
            if estimate:
                line += f" ~{estimate}min"
            agenda_lines.append(line)
    else:
        agenda_lines.append("\nNothing specifically scheduled for today.")

    pending = today_context.get("pending_tasks", [])
    if pending:
        agenda_lines.append("\n### Pending tasks (no date):")
        for task in pending[:8]:
            priority = task.get("priority", "")
            title = task.get("title", "Untitled")
            line = f"- {title}"
            if priority:
                line += f" ({priority})"
            agenda_lines.append(line)

    active_goals = today_context.get("active_goals", [])
    if active_goals:
        agenda_lines.append("\n### Active goals (all horizons):")
        by_type: dict[str, list[dict[str, Any]]] = {
            "long_term": [], "medium_term": [], "short_term": [], "other": [],
        }
        type_map_inv = {
            "Long Term": "long_term",
            "Medium Term": "medium_term",
            "Short Term": "short_term",
        }
        for g in active_goals:
            t = type_map_inv.get(g.get("type", ""), "other")
            by_type[t].append(g)

        labels = {
            "long_term": "Long term (1+ years)",
            "medium_term": "Medium term (3-12 months)",
            "short_term": "Short term (1-3 months)",
            "other": "Other",
        }
        for key in ["long_term", "medium_term", "short_term", "other"]:
            goals_in_type = by_type[key]
            if not goals_in_type:
                continue
            agenda_lines.append(f"\n**{labels[key]}:**")
            for g in goals_in_type[:6]:
                title = g.get("title", "Untitled")
                progress = g.get("progress")
                area = g.get("area", "")
                target = g.get("target_date", "")
                progress_pct = (
                    f"{int(progress * 100)}%"
                    if isinstance(progress, float) and progress <= 1
                    else (f"{progress}%" if progress else "0%")
                )
                line = f"- {title} [{progress_pct}]"
                if area:
                    line += f" ({area})"
                if target:
                    line += f" -> {target}"
                agenda_lines.append(line)

    sections.append("\n".join(agenda_lines))

    # ── 4. ACTIVE EXPERIMENTS ────────────────────────────────────────────
    if active_experiments:
        exp_lines = ["## Active experiments\n"]
        exp_lines.append(
            "Personal experiments the user is running right now. Take them into "
            "account when relevant — progress, adherence, upcoming check-ins.\n"
        )
        for e in active_experiments:
            name = e.get("name", "")
            hypothesis = e.get("hypothesis", "")
            start = e.get("start_date", "")
            duration = e.get("duration_days", "")
            check_in = e.get("check_in_every_days", 1)
            next_check = e.get("next_check_in", "")
            progress = e.get("progress_log", [])
            adherence = ""
            if progress:
                done = sum(1 for p in progress if p.get("did_it"))
                adherence = f"{done}/{len(progress)} days ({int(done / len(progress) * 100)}%)"
            line = f"- **{name}**"
            if hypothesis:
                line += f": {hypothesis}"
            if start and duration:
                line += f" | Started: {start}, {duration} days"
            if check_in:
                line += f" | Check-in every {check_in}d"
            if next_check:
                line += f" | Next: {next_check}"
            if adherence:
                line += f" | Adherence: {adherence}"
            exp_lines.append(line)
        sections.append("\n".join(exp_lines))

    # ── 5. ACTIVE SKILLS ─────────────────────────────────────────────────
    if active_skills:
        sections.append(active_skills)

    # ── 6. BEHAVIOUR ─────────────────────────────────────────────────────
    sections.append("""## Behaviour

- Voice input may contain transcription errors — interpret with context
- Not every reply needs to be long. A short confirmation is fine for simple actions
- If the user mentions something personal (a win, a frustration), acknowledge it
  briefly before acting
- If you detect schedule conflicts or task overload, say so proactively
- When prioritising, use the Eisenhower matrix (urgent / important)
- Suggest focus blocks during the user's peak productivity hours
- When organising a day, group similar tasks and respect energy cycles
- If something is unclear or context is missing, ask before assuming
- Dates always in YYYY-MM-DD format when calling tools

## Response format — Telegram

Your replies are delivered through Telegram. Follow these formatting rules:
- Plain text with emojis for structure. Do NOT use standard Markdown.
- For bold use *text* (one asterisk), NOT **text** (two asterisks).
- For lists use • or — with indentation, never `*` or `-` at the start of a line.
- NEVER include `[tools_used: ...]` blocks or tool-call details in your reply.
  That belongs to the internal history, not to the chat.
- Short and direct. No large headings or formal sections unless the content
  really calls for it.
""")

    # Stated only when a planner exists. Ordering the model to always route
    # planning through a tool this deployment does not have would be an
    # instruction it cannot follow, and it would explain the gap by inventing one.
    if integrations is None or "planner" in integrations:
        sections.append("""## Critical rule — Calendar and planning

NEVER reply with a schedule or plan as text. If the user asks to organise their day,
plan blocks, add something to the calendar or run a review, ALWAYS call
`delegate_to_planner`. A plan written as text but never executed through a tool does
not exist in their real calendar. No exceptions.
""")

    sections.append("""## Critical rule — Always say what you saved and where

Every time you write something persistent — `remember_fact`, `update_my_profile`,
`forget_about_me`, `forget_memory`, `save_skill`, `delete_skill` — you must say so in
your reply, with the exact text and the destination. One line at the end is enough:

  📌 Saved to your profile: "Trains 6 days a week, 1.5h per session"
  🧠 Saved to memory: "The paper they are writing is about computer vision"
  📝 Updated the `plan-day` skill: the window now runs until 11pm

NEVER summarise it as "updated your preferences" or "I'll keep that in mind". The user
cannot see what you stored, so if you do not quote it back they are blind to something
that will change your behaviour in every conversation from now on.

If you replaced an entry, show what was there before as well.
If you decided NOT to save something, say nothing — this rule is only for writes.

## Critical rule — Memory

NEVER say "I have no memories" or "I don't know anything about you" without calling
`list_memories` first. If the user asks what you know about them, what you remember,
or to see their information, ALWAYS call `list_memories` and base your answer on the
real result. Do not invent or assume — read what is actually there.

## Skills — when to create or update one

Call `save_skill` when you notice any of these:
- The user corrects you about how to handle something ("no, when I say X do Y")
- You had to figure out a procedure that wasn't obvious and will likely repeat
- The user asks you to "remember" a way of doing things
- You realise an existing skill guide is wrong or incomplete

When saving, generate the full SKILL.md content with proper frontmatter, including
relevant `triggers` keywords so the skill loads automatically next time.""")

    return "\n\n".join(sections)
