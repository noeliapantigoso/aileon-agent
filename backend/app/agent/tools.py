"""
Tool definitions for Gemini function calling.

Each tool has a name, a description and a parameter schema. Gemini uses these
declarations to decide which tool to invoke.
"""

from __future__ import annotations

TOOLS = [
    {
        "name": "create_task",
        "description": (
            "Create a new task in Notion. Use when the user wants to add a to-do, "
            "a pending item, or something they need to get done."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Clear, actionable task name",
                },
                "priority": {
                    "type": "string",
                    "enum": ["p0", "p1", "p2", "p3"],
                    "description": (
                        "p0=urgent+important, p1=important, p2=normal, p3=low"
                    ),
                },
                "due_date": {
                    "type": "string",
                    "description": "Hard deadline, YYYY-MM-DD",
                },
                "scheduled_date": {
                    "type": "string",
                    "description": "Day the user plans to do it (YYYY-MM-DD)",
                },
                "time_estimate_minutes": {
                    "type": "integer",
                    "description": "Time estimate in minutes",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Tags. Valid values in Notion: investigacion, software, desarrollo, "
                        "carrera, marketing, startup, health, well-being, leisure"
                    ),
                },
                "goal_id": {
                    "type": "string",
                    "description": "Notion goal id this task belongs to (optional)",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "save_day_priority",
        "description": (
            "Store the user's stated priority for a specific day. Call this when they "
            "answer what they want to prioritise tomorrow. The planner reads it "
            "when generating that day's plan."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Date this priority applies to, YYYY-MM-DD",
                },
                "priority": {
                    "type": "string",
                    "description": "What they want to prioritise that day (free text)",
                },
            },
            "required": ["date", "priority"],
        },
    },
    {
        "name": "get_tasks",
        "description": (
            "Query tasks from Notion. Use to see pending work, the agenda, or to find "
            "specific tasks."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "waiting", "done", "all"],
                    "description": "Filter by status",
                },
                "date": {
                    "type": "string",
                    "description": "Filter by scheduled date (YYYY-MM-DD)",
                },
                "priority": {
                    "type": "string",
                    "enum": ["p0", "p1", "p2", "p3"],
                    "description": "Filter by priority",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max tasks to return (default 20)",
                },
            },
        },
    },
    {
        "name": "update_task",
        "description": (
            "Update any field of an existing task: mark it done, change priority, "
            "reschedule, fix the title, change tags, the time estimate, or the "
            "goal it belongs to. Only the fields you pass are modified."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Notion page id",
                },
                "title": {
                    "type": "string",
                    "description": "New task title",
                },
                "status": {
                    "type": "string",
                    "enum": ["inbox", "next", "in_progress", "waiting", "done"],
                    "description": "New task status",
                },
                "priority": {
                    "type": "string",
                    "enum": ["p0", "p1", "p2", "p3"],
                    "description": "New priority",
                },
                "due_date": {
                    "type": "string",
                    "description": "New deadline (YYYY-MM-DD)",
                },
                "scheduled_date": {
                    "type": "string",
                    "description": "New scheduled date (YYYY-MM-DD)",
                },
                "time_estimate_minutes": {
                    "type": "integer",
                    "description": "New time estimate in minutes",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Replaces the current tags. Valid values: investigacion, software, "
                        "desarrollo, carrera, marketing, startup, health, "
                        "well-being, leisure"
                    ),
                },
                "goal_id": {
                    "type": "string",
                    "description": "Notion goal id to link the task to",
                },
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "archive_task",
        "description": (
            "Archive a task in Notion (soft-delete: it leaves the database but can be "
            "restored from Notion's trash). Use when the user asks to delete or "
            "remove a task. Note: if the task was completed, prefer update_task "
            "with status='done' over archiving, so the history is kept."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Notion page id",
                },
                "task_title": {
                    "type": "string",
                    "description": "Task title, to confirm back to the user what was archived",
                },
            },
            "required": ["task_id", "task_title"],
        },
    },
    {
        "name": "create_goal",
        "description": (
            "Create a new goal in Notion. NEVER call without target_date — always ask "
            "for the deadline before creating it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Clear goal title",
                },
                "goal_type": {
                    "type": "string",
                    "enum": ["short_term", "medium_term", "long_term"],
                    "description": "short=1-3 months, medium=3-12 months, long=1+ years",
                },
                "area": {
                    "type": "string",
                    "enum": ["work", "personal", "health", "finance", "learning", "relationships"],
                    "description": "Life area it belongs to",
                },
                "target_date": {
                    "type": "string",
                    "description": "Mandatory deadline, YYYY-MM-DD. No date, no goal.",
                },
            },
            "required": ["title", "goal_type", "target_date", "area"],
        },
    },
    {
        "name": "get_goals",
        "description": "Read the user's active goals with their progress.",
        "parameters": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["short_term", "medium_term", "long_term", "all"],
                    "description": "Filter by goal type",
                },
                "status": {
                    "type": "string",
                    "enum": ["active", "completed", "paused", "abandoned", "all"],
                    "description": "Filter by status",
                },
            },
        },
    },
    {
        "name": "update_goal",
        "description": (
            "Edit a goal's fields (title, type, area, target date, KRs, status). "
            "Do NOT use it to report progress — use update_goal_progress. "
            "If you don't have the goal_id, call get_goals first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal_id": {"type": "string", "description": "Notion goal id"},
                "title": {"type": "string", "description": "New title"},
                "goal_type": {
                    "type": "string",
                    "enum": ["short_term", "medium_term", "long_term"],
                },
                "area": {
                    "type": "string",
                    "enum": ["work", "personal", "health", "finance", "learning", "relationships"],
                },
                "target_date": {"type": "string", "description": "YYYY-MM-DD"},
                "key_results": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["active", "completed", "paused", "abandoned"],
                },
            },
            "required": ["goal_id"],
        },
    },
    {
        "name": "archive_goal",
        "description": (
            "Archive (soft-delete) a goal. Use when the user wants to delete or remove "
            "a goal. CONFIRM with them before archiving."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal_id": {"type": "string", "description": "Notion goal id"},
            },
            "required": ["goal_id"],
        },
    },
    {
        "name": "update_goal_progress",
        "description": (
            "Update ONLY the progress percentage and append a progress note. "
            "To change title, date or area use update_goal."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal_id": {
                    "type": "string",
                    "description": "Notion goal id",
                },
                "progress_note": {
                    "type": "string",
                    "description": "Description of the progress made",
                },
                "new_percentage": {
                    "type": "integer",
                    "description": "New progress percentage (0-100)",
                },
            },
            "required": ["goal_id", "progress_note"],
        },
    },
    {
        "name": "create_key_result",
        "description": (
            "Create a Key Result for a goal. Call when the user defines a concrete "
            "outcome that would show the goal was achieved. "
            "If you don't have the goal_id, call get_goals first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal_id": {
                    "type": "string",
                    "description": "Notion goal id",
                },
                "title": {
                    "type": "string",
                    "description": "The key result, e.g. 'Finish the ML course on Coursera'",
                },
                "notes": {
                    "type": "string",
                    "description": "Extra context or completion criteria",
                },
            },
            "required": ["goal_id", "title"],
        },
    },
    {
        "name": "get_key_results",
        "description": (
            "List Key Results with their status (Pending/Done) and the goal they belong "
            "to. With goal_id, lists that goal's KRs. With no parameters, lists "
            "all. With unassigned_only=true, lists only the orphans — the ones "
            "left without a goal — which is what to use when the user asks about "
            "loose KRs."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal_id": {
                    "type": "string",
                    "description": "Notion goal id. Omit to list all.",
                },
                "unassigned_only": {
                    "type": "boolean",
                    "description": (
                        "If true, returns only KRs with no goal assigned. Overrides goal_id."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "archive_key_result",
        "description": (
            "Archive a Key Result in Notion (soft-delete: it leaves the database but "
            "can be restored from Notion's trash). Use when the user asks to "
            "delete or remove a KR. If you don't have the kr_id, call "
            "get_key_results first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "kr_id": {
                    "type": "string",
                    "description": "Notion Key Result id",
                },
                "kr_title": {
                    "type": "string",
                    "description": "KR title, to confirm back to the user what was archived",
                },
            },
            "required": ["kr_id", "kr_title"],
        },
    },
    {
        "name": "update_key_result",
        "description": (
            "Edit an existing Key Result: change the title or notes, or reopen one "
            "that was Done (status=pending). If you don't have the kr_id, call "
            "get_key_results first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "kr_id": {
                    "type": "string",
                    "description": "Notion Key Result id",
                },
                "title": {
                    "type": "string",
                    "description": "New KR title",
                },
                "notes": {
                    "type": "string",
                    "description": "New notes or completion criteria",
                },
                "status": {
                    "type": "string",
                    "enum": ["pending", "done"],
                    "description": "Change status: 'pending' to reopen, 'done' to close",
                },
                "goal_id": {
                    "type": "string",
                    "description": (
                        "Notion goal id to link the KR to. Use it to reassign an orphan KR "
                        "to its correct goal instead of archiving it."
                    ),
                },
            },
            "required": ["kr_id"],
        },
    },
    {
        "name": "complete_key_result",
        "description": (
            "Mark a Key Result as Done and automatically recalculate the goal's "
            "progress. Use when the user confirms they reached a KR, or when the "
            "context makes it unambiguous. Don't ask for confirmation if they "
            "already said it plainly."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "kr_id": {
                    "type": "string",
                    "description": "Notion Key Result id",
                },
                "note": {
                    "type": "string",
                    "description": "Note on how or when it was completed",
                },
            },
            "required": ["kr_id"],
        },
    },
    {
        "name": "start_experiment",
        "description": (
            "Start a personal experiment: something the user wants to try and track "
            "for a few days. E.g. 'I'll meditate 5 minutes a day for a week'. "
            "Check-ins are surfaced automatically per check_in_every_days."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Short, clear experiment name",
                },
                "hypothesis": {
                    "type": "string",
                    "description": "What the user expects to achieve or find out",
                },
                "duration_days": {
                    "type": "integer",
                    "description": "Total duration in days (default 7)",
                },
                "check_in_every_days": {
                    "type": "integer",
                    "description": "How often to check in, in days (default 3)",
                },
            },
            "required": ["name", "hypothesis"],
        },
    },
    {
        "name": "log_experiment_progress",
        "description": (
            "Append an entry to an active experiment's history. "
            "Use when the user reports how it went."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "experiment_id": {
                    "type": "string",
                    "description": "Experiment id",
                },
                "note": {
                    "type": "string",
                    "description": "The user's note on how it went",
                },
                "did_it": {
                    "type": "boolean",
                    "description": "Whether they followed through this time",
                },
            },
            "required": ["experiment_id", "note"],
        },
    },
    {
        "name": "close_experiment",
        "description": (
            "Close an experiment. Use when the user considers it finished "
            "(completed, abandoned or pivoted)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "experiment_id": {
                    "type": "string",
                    "description": "Experiment id",
                },
                "outcome": {
                    "type": "string",
                    "description": "Final reflection / what they learned",
                },
                "status": {
                    "type": "string",
                    "enum": ["completed", "abandoned", "pivoted"],
                    "description": "How it ends",
                },
            },
            "required": ["experiment_id", "outcome"],
        },
    },
    {
        "name": "list_active_experiments",
        "description": "List the currently active experiments.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_calendar_events",
        "description": (
            "List the user's Google Calendar events in a range. Use to answer "
            "questions like 'what do I have tomorrow', 'what's on this week', "
            "'when is my meeting'. Read-only — modifies nothing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "start_iso": {"type": "string", "description": "ISO start datetime (optional, defaults to now)"},
                "end_iso": {"type": "string", "description": "ISO end datetime (optional)"},
                "days": {"type": "integer", "description": "Days from now if start/end are absent. Default 1."},
            },
        },
    },
    {
        "name": "delegate_to_planner",
        "description": (
            "Delegate to the PLANNER subagent, which specialises in calendar planning. "
            "Use when the user wants to organise their day or week, allocate time "
            "to goals, run a day review, OR create / move / delete individual "
            "calendar blocks (the planner validates against goals, peak hours and "
            "conflicts before applying). Do NOT use it to create a single Notion "
            "task — use create_task. The planner returns a summary of the "
            "plan / review / edit that you should present to the user."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["plan_day", "daily_review", "edit_request"],
                    "description": (
                        "plan_day=build the plan for a given date (default tomorrow). "
                        "daily_review=end of day: list the blocks and mark only what "
                        "the user confirmed doing. "
                        "edit_request=apply one calendar change (create / move / "
                        "delete a block), validated against goals, peak hours and "
                        "conflicts."
                    ),
                },
                "target_date": {
                    "type": "string",
                    "description": "For plan_day: target date YYYY-MM-DD. Defaults to tomorrow.",
                },
                "instruction": {
                    "type": "string",
                    "description": (
                        "For edit_request: the user's natural-language description of the "
                        "change. For daily_review: their reply saying which blocks "
                        "they completed. Pass their message through verbatim. "
                        "Examples: 'book 30min of gym tomorrow at 7pm', "
                        "'move my reading block from 7pm to 10pm'."
                    ),
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "read_storage",
        "description": (
            "Read raw documents from the agent's database (Firestore). Read-only. "
            "Use when the user asks what is stored about them, what was recorded, "
            "or why the agent did something: 'what did you save in my profile', "
            "'what priority did I set for today', 'why did you message me "
            "yesterday', 'what patterns did you detect'. Present the contents in "
            "plain language — never paste raw JSON at them."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "collection": {
                    "type": "string",
                    "enum": [
                        "users",
                        "history",
                        "experiments",
                        "insights",
                        "principles",
                        "proactive_log",
                        "daily_context",
                        "agent_queue",
                        "skills",
                        "profile_history",
                    ],
                    "description": (
                        "users=profile entries. history=past conversations. "
                        "experiments=experiments. insights=patterns from the weekly "
                        "analysis. principles=stored principles. proactive_log=which "
                        "proactive messages were sent and why. daily_context=the "
                        "priority stored per day. agent_queue=scheduled reminders. "
                        "skills=skills created at runtime. profile_history=profile "
                        "snapshots."
                    ),
                },
                "document_id": {
                    "type": "string",
                    "description": (
                        "Document id, when looking for a specific one. For 'users' it is "
                        "the user_id; for 'daily_context' it is the date "
                        "YYYY-MM-DD. Omit to list several."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Max documents when listing (default 10, capped at 50)",
                },
            },
            "required": ["collection"],
        },
    },
    {
        "name": "read_personality_type",
        "description": (
            "Read the reference guide for a personality type from the shared library. "
            "Call this ONCE, when the user states their type and their profile has no "
            "handling entry yet. Then condense the 'how to talk to them' part into their "
            "profile with update_my_profile — the library file is the generic starting "
            "point, their profile entry is the version that can be personalised.\n"
            "Never call it to look up a type you inferred yourself: only when they "
            "stated it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": (
                        "The file key for the type, e.g. 'enneagram_3'. Format is "
                        "framework_type. Call with no key to list what is available."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_user_profile",
        "description": (
            "Get the user's full profile and calibration. Use only when you need "
            "information that is not already in the current context."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "remember_fact",
        "description": (
            "Save a fact or preference about the user to long-term memory (Mem0). "
            "Memories here are retrieved only when semantically relevant to the current "
            "message, so this is the right place for details that matter on their topic "
            "and not otherwise: tool preferences, names of people or projects, one-off "
            "context.\n"
            "If the fact is identity-level — something that should shape how you treat "
            "them even in an unrelated conversation — use update_my_profile instead, "
            "so it is always in context. The `memory-management` skill has the rule."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fact": {
                    "type": "string",
                    "description": "The fact, written as a clear statement (e.g. 'Prefers short replies when stressed')",
                },
            },
            "required": ["fact"],
        },
    },
    {
        "name": "list_memories",
        "description": (
            "List everything stored in long-term memory about the user. "
            "Use when the user asks 'what do you know about me?', 'what do you remember?', or similar."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "forget_memory",
        "description": (
            "Delete a specific memory. Use when the user says 'forget that...', "
            "'that's no longer true', or wants to remove something you remember. "
            "First call list_memories to get the memory ID."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "string",
                    "description": "The ID of the memory to delete (from list_memories)",
                },
                "memory_text": {
                    "type": "string",
                    "description": "The text of the memory being deleted (for confirmation message to user)",
                },
            },
            "required": ["memory_id", "memory_text"],
        },
    },
    {
        "name": "update_my_profile",
        "description": (
            "Add or correct a fact in the user's profile. The profile is free prose — "
            "there are NO fields and no schema, so never worry about where something "
            "belongs. Just write the fact as a sentence.\n"
            "The profile is injected WHOLE into every prompt of every agent, so it costs "
            "tokens on every turn and must stay tight (~3000 chars total). Reserve it for "
            "identity-level facts: things that change how you treat them even when the "
            "conversation is about something unrelated — how they work, their "
            "routines, their personality, how they want to be spoken to.\n"
            "For details that only matter when a specific topic comes up, use "
            "remember_fact instead. The `memory-management` skill has the full rule.\n"
            "To correct something already there, pass `replaces` with part of the old "
            "entry's text — that avoids stacking a contradictory duplicate. If the profile "
            "is near its size limit, consolidate rather than append."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "entry": {
                    "type": "string",
                    "description": (
                        "The fact, written as one clear sentence in third person. "
                        "Example: 'Trains 6 days a week: gym 3 times, cardio 3, "
                        "1.5h per session.' Describe reality — never write it as "
                        "an order ('always leave 1.5h free' is wrong)."
                    ),
                },
                "replaces": {
                    "type": "string",
                    "description": (
                        "Part of the text of an existing entry to overwrite. Partial "
                        "match is enough. Omit when adding something new."
                    ),
                },
            },
            "required": ["entry"],
        },
    },
    {
        "name": "forget_about_me",
        "description": (
            "Remove an entry from the user's profile. Use when they say something is "
            "wrong or no longer true and there is nothing to replace it with. "
            "If it should be corrected rather than deleted, use update_my_profile "
            "with `replaces` instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "entry": {
                    "type": "string",
                    "description": "Part of the text of the entry to remove. Partial match is enough.",
                },
            },
            "required": ["entry"],
        },
    },
    {
        "name": "save_skill",
        "description": (
            "Creates or updates a SKILL.md file — a reusable guide that improves "
            "how you handle a recurring situation. Use this when you notice a pattern "
            "the user repeats, learn a new rule or preference that should stick "
            "permanently, or when the user asks you to remember a procedure. "
            "The skill will be loaded automatically in future conversations when relevant. "
            "IMPORTANT: after calling this tool, always tell the user what skill you created "
            "or updated and why, in one short line. Example: "
            "'📎 Saved skill: handle-interruptions — I'll remember to only move the last block when something comes up.'"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Short slug for the skill (e.g. 'handle-interruptions', "
                        "'meeting-notes'). Use existing skill names to update them."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": (
                        "Full SKILL.md content. Must include YAML frontmatter with "
                        "name, description, and triggers fields, followed by markdown "
                        "instructions. Example frontmatter:\n"
                        "---\n"
                        "name: handle-interruptions\n"
                        "description: How to handle unexpected interruptions during a planned day\n"
                        "triggers: [interrupted, distraction, something came up]\n"
                        "---"
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "Why you're creating or updating this skill — what pattern triggered it.",
                },
            },
            "required": ["name", "content", "reason"],
        },
    },
    {
        "name": "delete_skill",
        "description": (
            "Permanently deletes a custom skill. Use when the user explicitly asks to "
            "remove a skill, or when a skill is outdated and shouldn't guide future "
            "conversations. Built-in skills (plan-day, daily-review, etc.) cannot be "
            "deleted — use save_skill to override them instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name or slug of the skill to delete (e.g. 'handle-interruptions')",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "schedule_agent_task",
        "description": (
            "Schedule a one-time agent reminder to run at a specific future datetime. "
            "Use when the user says things like 'remind me in 3 weeks', "
            "'check on this goal next month', 'follow up on X on September 15th'. "
            "This is NOT a Notion task — it's an internal agent action that fires once at the specified time."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short label for the reminder (shown in list_agent_tasks)",
                },
                "prompt": {
                    "type": "string",
                    "description": (
                        "What to do when the reminder fires — written as a clear instruction. "
                        "Example: 'Check in on the journaling experiment: has the user kept it up for 30 days?'"
                    ),
                },
                "run_at": {
                    "type": "string",
                    "description": (
                        "ISO datetime with the user's UTC offset for when it should run. "
                        "Example: '2026-09-15T09:00:00-05:00'"
                    ),
                },
            },
            "required": ["title", "prompt", "run_at"],
        },
    },
    {
        "name": "cancel_agent_task",
        "description": (
            "Cancel a scheduled agent reminder so it won't run. "
            "Use when the user says 'cancel that reminder', 'don't check on X anymore', "
            "or when a reminder is no longer relevant. Call list_agent_tasks first to get the task_id."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The task ID from list_agent_tasks",
                },
                "task_title": {
                    "type": "string",
                    "description": "The task title (for confirmation message to user)",
                },
            },
            "required": ["task_id", "task_title"],
        },
    },
    {
        "name": "list_agent_tasks",
        "description": (
            "List all pending agent reminders. "
            "Call this when the user asks 'what reminders do I have?', "
            "'what have you scheduled?', or before cancelling a reminder. "
            "These are agent-internal actions, NOT Notion tasks."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
]


# ── Proactive-cycle-only tools ───────────────────────────────────────────────
# Not exposed in normal conversations: only when the agent wakes up on its own
# and has to decide whether reaching out is worth it.

PROACTIVE_TOOLS = [
    {
        "name": "send_message_to_user",
        "description": (
            "Send a proactive Telegram message to the user. Call this ONLY if, after "
            "looking at their current situation, you decided it is genuinely worth "
            "interrupting them right now. If there is nothing worth saying, do NOT "
            "call this tool — end your turn with a one-line note explaining why you "
            "stayed quiet. Silence is a valid and often correct choice; do not send "
            "filler just because you woke up."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": (
                        "The exact text to send, in the user's language, following the "
                        "Telegram formatting rules. Short and direct — no greetings."
                    ),
                },
                "message_type": {
                    "type": "string",
                    "description": (
                        "Category, used to avoid repeating the same kind of message: "
                        "affirmation | nudge | overdue_tasks | experiment_check_in | "
                        "experiment_proposal | priority_question | reflection | other"
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": (
                        "Why this is worth sending right now. Logged for debugging, "
                        "NEVER shown to the user."
                    ),
                },
            },
            "required": ["message", "message_type", "reason"],
        },
    },
    {
        "name": "stay_silent",
        "description": (
            "Declare that you are NOT writing to the user this wake-up.\n"
            "Finish every wake-up by calling exactly one of `send_message_to_user` "
            "or `stay_silent`. Never finish by writing plain text instead: anything "
            "you write as a final answer goes to an internal log the user never reads, "
            "so a message meant for them would be lost."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": (
                        "Why staying quiet is right, in one line, written for yourself "
                        "in the third person. e.g. 'They are in a focus block until "
                        "17:30 and nothing is urgent.'"
                    ),
                },
            },
            "required": ["reason"],
        },
    },
]


# Which optional integration each tool needs. A tool that is absent from this
# map works with no integration at all — memory, skills, experiments and the
# agent's own queue only need the document store, which is always present.
#
# Tools whose integration is off are never offered to the model. Withholding
# them is what makes an integration genuinely optional: the model cannot call
# what it cannot see, so nothing has to fail gracefully at call time.
TOOL_REQUIREMENTS: dict[str, str] = {
    # Notion: tasks, goals and key results
    "create_task": "notion",
    "get_tasks": "notion",
    "update_task": "notion",
    "archive_task": "notion",
    "create_goal": "notion",
    "get_goals": "notion",
    "update_goal": "notion",
    "archive_goal": "notion",
    "update_goal_progress": "notion",
    "create_key_result": "notion",
    "get_key_results": "notion",
    "archive_key_result": "notion",
    "update_key_result": "notion",
    "complete_key_result": "notion",
    # Google Calendar
    "get_calendar_events": "calendar",
    # The planner needs both a calendar to write to and goals to plan towards
    "delegate_to_planner": "planner",
}


def get_tools(available: set[str] | None = None) -> list[dict]:
    """
    The tools to offer, given the integrations that are actually configured.

    `available` holds integration names ("notion", "calendar", "planner").
    Passing None keeps every tool, which is what a fully configured deployment
    gets.
    """
    if available is None:
        return list(TOOLS)
    return [
        tool for tool in TOOLS
        if TOOL_REQUIREMENTS.get(tool["name"], None) in (None, *available)
    ]


def get_gemini_tool_declarations(available: set[str] | None = None) -> list[dict]:
    """Convert the tool definitions to the shape the google-genai SDK expects."""
    declarations = []
    for tool in get_tools(available):
        declarations.append({
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["parameters"],
        })
    return declarations
