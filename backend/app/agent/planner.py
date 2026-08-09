"""
PlannerAgent — subagent specialised in calendar planning.

Responsibilities:
1. Build the daily plan, filling the calendar while respecting goals,
   experiments, pending tasks, energy levels and productivity hours.
2. Apply targeted calendar edits (create / move / delete blocks).
3. Run the end-of-day review and mark block completion.

It has its own function-calling loop with a specialised toolset.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 10

# Fallback only; the real value arrives from settings.gemini_model.
DEFAULT_MODEL_ID = "gemini-2.5-pro"

# ── Tools del planner ────────────────────────────────────────────────────────

PLANNER_TOOLS = [
    {
        "name": "list_calendar_events",
        "description": "List calendar events in a range (defaults to the next 24h).",
        "parameters": {
            "type": "object",
            "properties": {
                "start_iso": {"type": "string", "description": "ISO datetime con tz"},
                "end_iso": {"type": "string", "description": "ISO datetime con tz"},
                "days": {"type": "integer", "description": "Days from now, when start/end are absent"},
            },
        },
    },
    {
        "name": "find_free_slots",
        "description": "Find free slots between two datetimes.",
        "parameters": {
            "type": "object",
            "properties": {
                "start_iso": {"type": "string"},
                "end_iso": {"type": "string"},
                "min_duration_minutes": {"type": "integer", "description": "default 30"},
            },
            "required": ["start_iso", "end_iso"],
        },
    },
    {
        "name": "create_block",
        "description": (
            "Create a calendar block. Use for every planned block of the day. "
            "Attach goal_id / experiment_id / task_id when applicable."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Short block title"},
                "start_iso": {"type": "string"},
                "end_iso": {"type": "string"},
                "description": {"type": "string"},
                "experiment_id": {"type": "string"},
                "goal_id": {"type": "string"},
                "task_id": {"type": "string"},
            },
            "required": ["summary", "start_iso", "end_iso"],
        },
    },
    {
        "name": "update_block",
        "description": "Move or modify an existing block.",
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "summary": {"type": "string"},
                "start_iso": {"type": "string"},
                "end_iso": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "delete_block",
        "description": "Delete a calendar block.",
        "parameters": {
            "type": "object",
            "properties": {"event_id": {"type": "string"}},
            "required": ["event_id"],
        },
    },
    {
        "name": "mark_block_completed",
        "description": "Mark whether a past block was completed.",
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "completed": {
                    "type": "string",
                    "enum": ["true", "false"],
                },
                "note": {"type": "string"},
            },
            "required": ["event_id", "completed"],
        },
    },
    {
        "name": "get_completion_history",
        "description": "Historical completion statistics, broken down by hour of day.",
        "parameters": {
            "type": "object",
            "properties": {"days": {"type": "integer", "description": "default 14"}},
        },
    },
    {
        "name": "get_active_goals",
        "description": "Read active goals with their deadlines and progress.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_active_experiments",
        "description": "Read active experiments with their check-in cadence.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_pending_tasks",
        "description": "Read pending Notion tasks that are not scheduled yet.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "log_experiment_progress",
        "description": "Log experiment progress (use after verifying completion).",
        "parameters": {
            "type": "object",
            "properties": {
                "experiment_id": {"type": "string"},
                "note": {"type": "string"},
                "did_it": {"type": "boolean"},
            },
            "required": ["experiment_id", "note"],
        },
    },
    {
        "name": "update_goal_progress",
        "description": "Advance a goal's percentage (use after completing a linked block).",
        "parameters": {
            "type": "object",
            "properties": {
                "goal_id": {"type": "string"},
                "progress_note": {"type": "string"},
                "new_percentage": {"type": "integer"},
            },
            "required": ["goal_id", "progress_note"],
        },
    },
    {
        "name": "get_key_results",
        "description": (
            "Read a goal's Key Results to inform what to schedule. "
            "Use while planning a day to see which KRs are still pending."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal_id": {"type": "string"},
            },
            "required": ["goal_id"],
        },
    },
    {
        "name": "mark_kr_done",
        "description": (
            "Mark a KR as completed and recalculate the goal's progress. "
            "Use during the daily review when the evidence clearly shows a KR was met."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "kr_id": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["kr_id"],
        },
    },
]


# ── Prompts ─────────────────────────────────────────────────────────────────

PLAN_DAY_PROMPT_TEMPLATE = """You are an expert time management planner. Your role: generate a realistic \
plan for the specified day in the user's Google Calendar, respecting goals, active experiments, \
pending tasks, energy levels, and existing fixed events.

IMPORTANT: All datetimes you generate must be in the user's local timezone
(shown in the context above). Always use ISO format with an explicit offset, e.g.
2024-05-20T09:00:00-05:00. NEVER use the Z suffix or UTC for blocks you create.

# Day to plan
{target_date} ({day_name})

# User profile
{user_profile_summary}

# Personality
{personality_summary}

# Detected insights/patterns
{insights_summary}

# Completion history
{completion_summary}

# Active goals
{goals_summary}

# Active experiments
{experiments_summary}

# Pending Notion tasks
{pending_tasks_summary}

# Fixed events already in Calendar
{fixed_events_summary}

# User's stated priority for this day
{day_priority}

{skill_rules}

Action: plan the day. Start now."""


EDIT_REQUEST_PROMPT_TEMPLATE = """You are a planner. The user is requesting a specific change to their Calendar. \
Your job is to validate coherence with their goals, peak hours, and patterns — then apply (or propose an alternative).

IMPORTANT: All datetimes you generate must be in the user's local timezone
(shown in the context above). Always use ISO format with an explicit offset, e.g.
2024-05-20T09:00:00-05:00. NEVER use the Z suffix or UTC.

# User request (natural language)
"{instruction}"

# Current time (user's local timezone)
{now_iso}

# Productivity profile
{profile_summary}

# Detected insights/patterns
{insights_summary}

# Active goals
{goals_summary}

# Active experiments
{experiments_summary}

# Calendar events next 7 days
Two lists. `bloques_del_plan_MOVIBLES` are blocks YOU created (they carry `[plan]`
in the title): they are your own suggestions, not commitments — move, shorten or
delete them freely to make room. `compromisos_fijos_NO_TOCAR` are her real
commitments: never overwrite those.

{upcoming_events}

# Completion history (last 14 days)
{completion_summary}

# Decision rules
1. Parse the intent: create / move / delete / query?
2. Identify entities: affected block(s), date/time, duration.
3. If event_id is ambiguous, use `list_calendar_events` to find it by title/date.
4. VALIDATE before applying:
   - Does the proposed time fall in low_energy_hours and is it deep work? → warn and suggest peak hours
   - Does it overlap a `[plan]` block? → that is NOT a conflict. Those are yours:
     move them, shorten them or delete them to make room, and say what you moved.
   - Does it overlap a fixed commitment (no `[plan]` in the title)? → real conflict:
     suggest a nearby free slot with `find_free_slots` and do not overwrite it.
   - Does it remove the only block dedicated to an active goal? → warn before deleting
   - Is that time repeatedly low in completion history? → mention and suggest alternative
5. If validation OK, execute with `create_block`/`update_block`/`delete_block`.
6. Only a clash with a FIXED commitment stops you. Never refuse a change because
   your own plan blocks are in the way — reorganising them is exactly your job.

# Expected response
Short text (2-4 sentences) explaining what you did or propose. \
If applied, confirm the action. If not, explain the conflict and the alternative.
Start now."""


DAILY_REVIEW_PROMPT_TEMPLATE = """You are a planner. End of day — daily review.

# Today's blocks
{today_blocks}

# Stats
{stats}

# User's response about what they completed
{user_input}

{skill_rules}"""


class PlannerAgent:
    """Planning subagent with its own function-calling loop."""

    def __init__(
        self,
        calendar_service: Any,
        notion_service: Any,
        experiment_service: Any,
        memory_manager: Any,
        gemini_api_key: str = "",
        gcp_project_id: str = "",
        user_timezone: str = "America/Lima",
        model_id: str = DEFAULT_MODEL_ID,
    ) -> None:
        if gcp_project_id:
            self._client = genai.Client(
                vertexai=True,
                project=gcp_project_id,
                location="us-central1",
            )
        else:
            self._client = genai.Client(api_key=gemini_api_key)
        self._model_id = model_id

        self._calendar = calendar_service
        self._notion = notion_service
        self._experiments = experiment_service
        self._memory = memory_manager
        self._timezone = user_timezone

    # ── Skill loader ────────────────────────────────────────────────────────

    @staticmethod
    def _load_skill_rules(skill_name: str) -> str:
        """Read a skill .md file and return its content (frontmatter stripped)."""
        skills_dir = Path(__file__).parent.parent.parent / "skills"
        skill_path = skills_dir / f"{skill_name}.md"
        if not skill_path.exists():
            logger.warning("Skill file not found: %s", skill_path)
            return ""
        content = skill_path.read_text(encoding="utf-8")
        if content.startswith("---"):
            end = content.find("---", 3)
            if end != -1:
                content = content[end + 3:].strip()
        return content

    # ── API pública (llamada desde main agent / endpoints) ─────────────────

    async def plan_day(self, target_date_iso: str | None = None) -> dict[str, Any]:
        """Generate the plan for a day (defaults to tomorrow)."""
        if target_date_iso:
            target = date.fromisoformat(target_date_iso)
        else:
            target = date.today() + timedelta(days=1)

        ctx = await self._build_planning_context(target)
        prompt = PLAN_DAY_PROMPT_TEMPLATE.format(
            target_date=target.isoformat(),
            day_name=target.strftime('%A'),
            user_profile_summary=ctx["profile_summary"],
            personality_summary=ctx["personality_summary"],
            insights_summary=ctx["insights_summary"],
            completion_summary=ctx["completion_summary"],
            goals_summary=ctx["goals_summary"],
            experiments_summary=ctx["experiments_summary"],
            pending_tasks_summary=ctx["pending_tasks_summary"],
            fixed_events_summary=ctx["fixed_events_summary"],
            day_priority=ctx["day_priority"],
            skill_rules=self._load_skill_rules("plan-day"),
        )

        return await self._run_loop(prompt, mode="plan")

    def _now_lima(self) -> datetime:
        """Current time in the user's timezone."""
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo(self._timezone))
        except Exception:
            from datetime import timezone as _tz, timedelta as _td
            return datetime.now(_tz(_td(hours=-5)))

    async def edit_request(self, instruction: str) -> dict[str, Any]:
        """Aplica cambio puntual al Calendar validando coherencia."""
        from datetime import timezone
        now_utc = datetime.now(timezone.utc)
        now_lima = self._now_lima()
        today = now_lima.date()

        ctx = await self._build_planning_context(today + timedelta(days=1))

        # Separate the blocks the planner created from the user's real
        # commitments. Without this distinction, edit_request treated its own
        # `[plan]` blocks as immovable conflicts and blocked itself: it could
        # not reorganise a day it had built in the first place.
        todos = self._calendar.list_events(
            start=now_utc,
            end=now_utc + timedelta(days=7),
            max_results=50,
        )
        upcoming = {
            "bloques_del_plan_MOVIBLES": [
                e for e in todos if "[plan]" in (e.get("summary") or "")
            ],
            "compromisos_fijos_NO_TOCAR": [
                e for e in todos if "[plan]" not in (e.get("summary") or "")
            ],
        }
        completion = self._calendar.get_completion_history(days=14)

        prompt = EDIT_REQUEST_PROMPT_TEMPLATE.format(
            instruction=instruction,
            now_iso=now_lima.isoformat(),
            profile_summary=ctx["profile_summary"],
            insights_summary=ctx["insights_summary"],
            goals_summary=ctx["goals_summary"],
            experiments_summary=ctx["experiments_summary"],
            upcoming_events=json.dumps(upcoming, ensure_ascii=False, indent=2)[:4000],
            completion_summary=json.dumps(completion, ensure_ascii=False, indent=2)[:2000],
        )

        return await self._run_loop(prompt, mode="edit")

    async def daily_review(self, day_iso: str | None = None, user_input: str = "") -> dict[str, Any]:
        """End-of-day review. Defaults to today."""
        from zoneinfo import ZoneInfo
        day = date.fromisoformat(day_iso) if day_iso else date.today()
        start = datetime.combine(day, datetime.min.time()).replace(tzinfo=ZoneInfo(self._timezone))
        end = start + timedelta(days=1)

        events = self._calendar.list_events(start=start, end=end)
        plan_events = [e for e in events if "[plan]" in (e.get("summary") or "")]
        stats = self._calendar.get_completion_history(days=1)

        user_input_text = user_input.strip() if user_input.strip() else "(the user did not answer — leave every block unverified)"

        prompt = DAILY_REVIEW_PROMPT_TEMPLATE.format(
            today_blocks=json.dumps(plan_events, ensure_ascii=False, indent=2),
            stats=json.dumps(stats, ensure_ascii=False, indent=2),
            user_input=user_input_text,
            skill_rules=self._load_skill_rules("daily-review"),
        )
        return await self._run_loop(prompt, mode="review")

    # ── Context loading ────────────────────────────────────────────────────

    async def _build_planning_context(self, target_date) -> dict[str, str]:
        from datetime import timezone
        from zoneinfo import ZoneInfo

        # Who the user is no longer gets assembled here: it arrives through the
        # shared system_instruction (build_user_context). Only planning-specific
        # context is built below.
        calibration = self._memory.get_calibration() if self._memory else {}
        cal = calibration.get("estimation_calibration", 1.0)
        if cal > 1.05:
            cal_note = f"⚠️ Time calibration: the user underestimates by {int((cal - 1) * 100)}% — multiply time_estimate by {cal}"
        elif cal < 0.95:
            cal_note = f"ℹ️ Time calibration: the user overestimates by {int((1 - cal) * 100)}% — multiply time_estimate by {cal}"
        else:
            cal_note = "Time calibration: estimates are accurate (factor 1.0)"

        profile_summary = cal_note
        personality_summary = "(see the profile in the system prompt)"

        # Insights
        insights = self._memory.insights.get_active_insights(limit=5) if self._memory and self._memory.insights else []
        insights_summary = "\n".join(
            f"- {i.get('title')}: {i.get('description')}" for i in insights
        ) or "(ninguno)"

        # Completion history
        completion = self._calendar.get_completion_history(days=14)
        completion_summary = (
            f"Total bloques planificados últimos 14d: {completion.get('total_plan_events', 0)}\n"
            f"Tasa cumplimiento: {completion.get('completion_rate')}\n"
            f"By hour: {json.dumps(completion.get('by_hour', {}))}"
        )

        # Goals
        goals = await self._notion.get_goals(status="active")
        goals_summary = "\n".join(
            f"- [{g.get('type')}] {g.get('title')} (área:{g.get('area')}, target:{g.get('target_date')}, progreso:{g.get('progress')})"
            for g in goals
        ) or "(ninguna activa)"

        # Experiments
        experiments = self._experiments.list_active() if self._experiments else []
        experiments_summary = "\n".join(
            f"- {e.get('name')} (hipótesis: {e.get('hypothesis')}, check_in_every_days: {e.get('check_in_every_days')}, próximo:{e.get('next_check_in')})"
            for e in experiments
        ) or "(ninguno)"

        # Pending tasks
        tasks = await self._notion.get_tasks(status="pending", limit=20)
        pending_tasks_summary = "\n".join(
            f"- {t.get('title')} ({t.get('priority', 'P2')}) est:{t.get('time_estimate', '?')}min"
            for t in tasks
        ) or "(ninguna)"

        # Fixed events del target_date
        day_start = datetime.combine(target_date, datetime.min.time()).replace(tzinfo=ZoneInfo(self._timezone))
        day_end = day_start + timedelta(days=1)
        events = self._calendar.list_events(start=day_start, end=day_end)
        fixed_events = [e for e in events if "[plan]" not in (e.get("summary") or "")]
        fixed_events_summary = "\n".join(
            f"- {e.get('summary')}: {e.get('start')} → {e.get('end')}"
            for e in fixed_events
        ) or "(ninguno)"

        # Priority saved by user the day before
        day_priority = ""
        if self._memory:
            raw = self._memory.get_day_priority(target_date.isoformat())
            if raw:
                day_priority = f"The user said their priority for this day is: {raw}"
            else:
                day_priority = "(no priority set by user for this day)"

        return {
            "profile_summary": profile_summary,
            "personality_summary": personality_summary,
            "insights_summary": insights_summary,
            "completion_summary": completion_summary,
            "goals_summary": goals_summary,
            "experiments_summary": experiments_summary,
            "pending_tasks_summary": pending_tasks_summary,
            "fixed_events_summary": fixed_events_summary,
            "day_priority": day_priority,
        }

    # ── Loop ────────────────────────────────────────────────────────────────

    async def _user_context(self) -> str:
        """
        User context shared with the chat agent.

        Passed as system_instruction on every planner call, so any new kind of
        fact about the user reaches it without being wired into each template.
        """
        if self._memory is None:
            return ""
        try:
            from app.agent.prompt_builder import build_user_context

            memories = [m.get("text", "") for m in await self._memory.list_memories()]
            insights = (
                self._memory.insights.get_active_insights(limit=5)
                if self._memory.insights
                else []
            )
            return build_user_context(
                user_profile={
                    "entries": self._memory.get_profile_entries(),
                    "calibration": self._memory.get_calibration(),
                },
                current_datetime=self._now_lima(),
                relevant_memories=[m for m in memories if m],
                active_insights=insights,
            )
        except Exception as exc:
            logger.warning("Planner: could not build user context: %s", exc)
            return ""

    async def _run_loop(self, user_prompt: str, mode: str = "plan") -> dict[str, Any]:
        """Function-calling loop. Returns a dict with the summary and the actions."""
        user_context = await self._user_context()
        gemini_tools = [types.Tool(function_declarations=PLANNER_TOOLS)]
        contents = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=user_prompt)],
            )
        ]
        actions: list[dict[str, Any]] = []
        final_text = ""

        for iteration in range(MAX_TOOL_ITERATIONS):
            logger.info("Planner iter %d/%d (mode=%s)", iteration + 1, MAX_TOOL_ITERATIONS, mode)
            from app.agent.core import generate_with_retry

            response = await generate_with_retry(
                self._client,
                model=self._model_id,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=user_context or None,
                    tools=gemini_tools,
                    temperature=0.4,
                    max_output_tokens=4096,
                ),
            )

            # Gemini can return a candidate with no content (safety filter, token
            # cutoff, empty turn): .parts comes back as None and cannot be iterated.
            candidates = response.candidates or []
            candidate = candidates[0] if candidates else None
            parts = (
                (candidate.content.parts if candidate and candidate.content else None)
                or []
            )

            if not parts:
                logger.warning(
                    "Planner: empty response from Gemini (iteration %d, finish_reason=%s)",
                    iteration + 1,
                    getattr(candidate, "finish_reason", None) if candidate else None,
                )
                final_text = (
                    "The planner got no response from the model on this attempt. "
                    "No blocks were created."
                )
                break

            has_calls = any(p.function_call is not None for p in parts)

            if not has_calls:
                final_text = "".join(p.text for p in parts if p.text)
                break

            function_responses = []
            for part in parts:
                if part.function_call is None:
                    continue
                fc = part.function_call
                tool_name = fc.name
                tool_args = dict(fc.args) if fc.args else {}
                logger.info("Planner tool call: %s(%s)", tool_name, list(tool_args.keys()))
                result = await self._execute_tool(tool_name, tool_args)
                actions.append({"tool": tool_name, "args": tool_args, "result": result})
                function_responses.append(
                    types.Part.from_function_response(name=tool_name, response=result)
                )

            contents.append(candidate.content)
            contents.append(types.Content(role="user", parts=function_responses))
        else:
            final_text = "Planner alcanzó el máximo de iteraciones."

        return {"mode": mode, "summary": final_text, "actions": actions}

    # ── Tool dispatch ───────────────────────────────────────────────────────

    async def _execute_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            if name == "list_calendar_events":
                start_iso = args.get("start_iso")
                end_iso = args.get("end_iso")
                days = args.get("days", 1)
                start = datetime.fromisoformat(start_iso) if start_iso else None
                end = datetime.fromisoformat(end_iso) if end_iso else None
                events = self._calendar.list_events(start=start, end=end, days=days)
                return {"events": events, "count": len(events)}

            if name == "find_free_slots":
                start = datetime.fromisoformat(args["start_iso"])
                end = datetime.fromisoformat(args["end_iso"])
                min_dur = args.get("min_duration_minutes", 30)
                slots = self._calendar.find_free_slots(start, end, min_dur)
                return {"slots": [(s.isoformat(), e.isoformat()) for s, e in slots]}

            if name == "create_block":
                ev = self._calendar.create_event(
                    summary=args["summary"],
                    start_iso=args["start_iso"],
                    end_iso=args["end_iso"],
                    description=args.get("description"),
                    is_plan=True,
                    experiment_id=args.get("experiment_id"),
                    goal_id=args.get("goal_id"),
                    task_id=args.get("task_id"),
                )
                return {"created": ev}

            if name == "update_block":
                ev = self._calendar.update_event(
                    event_id=args["event_id"],
                    summary=args.get("summary"),
                    start_iso=args.get("start_iso"),
                    end_iso=args.get("end_iso"),
                    description=args.get("description"),
                )
                return {"updated": ev}

            if name == "delete_block":
                return self._calendar.delete_event(args["event_id"])

            if name == "mark_block_completed":
                completed_val = args.get("completed", "true")
                ev = self._calendar.mark_event_completed(
                    event_id=args["event_id"],
                    completed=completed_val,
                    note=args.get("note"),
                )
                task_id = (ev or {}).get("task_id")
                goal_id = (ev or {}).get("goal_id")
                raw_summary = (ev or {}).get("summary", "")
                block_title = _clean_block_title(raw_summary)
                notion_update = None

                if completed_val == "true":
                    if task_id:
                        notion_update = await self._notion.update_task(task_id, status="done")
                else:  # false — not completed
                    if task_id:
                        notion_update = await self._notion.update_task(task_id, status="next")
                    elif block_title:
                        notion_update = await self._notion.create_task(
                            title=block_title, priority="P2", goal_id=goal_id, source="Text"
                        )

                return {"marked": ev, "notion_updated": notion_update}

            if name == "get_completion_history":
                return self._calendar.get_completion_history(days=args.get("days", 14))

            if name == "get_active_goals":
                goals = await self._notion.get_goals(status="active")
                return {"goals": goals, "count": len(goals)}

            if name == "get_active_experiments":
                exps = self._experiments.list_active() if self._experiments else []
                return {"experiments": exps, "count": len(exps)}

            if name == "get_pending_tasks":
                tasks = await self._notion.get_tasks(status="pending", limit=20)
                return {"tasks": tasks, "count": len(tasks)}

            if name == "log_experiment_progress":
                result = self._experiments.log_progress(
                    experiment_id=args["experiment_id"],
                    note=args.get("note", ""),
                    did_it=args.get("did_it", True),
                )
                return {"logged": result}

            if name == "update_goal_progress":
                result = await self._notion.update_goal_progress(
                    goal_id=args["goal_id"],
                    progress_note=args.get("progress_note", ""),
                    new_percentage=args.get("new_percentage"),
                )
                return {"updated": result}

            if name == "get_key_results":
                krs = await self._notion.get_key_results(args["goal_id"])
                done = sum(1 for kr in krs if kr.get("status") == "Done")
                return {"key_results": krs, "count": len(krs), "done": done, "pending": len(krs) - done}

            if name == "mark_kr_done":
                result = await self._notion.mark_kr_done(
                    kr_id=args["kr_id"],
                    note=args.get("note", ""),
                )
                return {"completed": result}

            return {"error": f"Tool desconocida: {name}"}
        except Exception as exc:
            logger.error("Planner tool %s failed: %s", name, exc)
            return {"error": str(exc)}


# ── Helpers ─────────────────────────────────────────────────────────────────




def _clean_block_title(summary: str) -> str:
    """Strips calendar emoji prefixes and [plan] tag from a block summary."""
    for prefix in ("✅ ", "❌ ", "⚠️ "):
        summary = summary.replace(prefix, "")
    if summary.startswith("[plan] "):
        summary = summary[7:]
    return summary.strip()
