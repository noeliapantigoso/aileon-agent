"""
PlannerAgent — subagente especializado en planificación de calendario.

Responsabilidades:
1. Generar plan diario auto que llena el calendar respetando metas, experimentos,
   tareas pendientes, energía, horas de productividad.
2. Verificar cumplimiento de bloques pasados.
3. Reprogramar bloques no cumplidos.
4. Daily review nocturna.
5. Priorización urgencia × importancia × probabilidad_cumplir.

Loop propio de function calling con tools especializadas.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 10
MODEL_ID_VERTEX = "gemini-2.5-flash"
MODEL_ID_AISTUDIO = "gemini-2.5-flash"

# ── Tools del planner ────────────────────────────────────────────────────────

PLANNER_TOOLS = [
    {
        "name": "list_calendar_events",
        "description": "Lista eventos del Calendar en un rango (default próximas 24h).",
        "parameters": {
            "type": "object",
            "properties": {
                "start_iso": {"type": "string", "description": "ISO datetime con tz"},
                "end_iso": {"type": "string", "description": "ISO datetime con tz"},
                "days": {"type": "integer", "description": "Si no hay start/end, días desde ahora"},
            },
        },
    },
    {
        "name": "find_free_slots",
        "description": "Encuentra huecos libres entre dos datetimes.",
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
            "Crea un bloque en Calendar. Usar para todos los bloques de planning del día. "
            "Asociar a goal_id/experiment_id/task_id cuando aplique."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Título corto del bloque"},
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
        "description": "Mueve o modifica un bloque existente.",
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
        "description": "Borra un bloque del Calendar.",
        "parameters": {
            "type": "object",
            "properties": {"event_id": {"type": "string"}},
            "required": ["event_id"],
        },
    },
    {
        "name": "mark_block_completed",
        "description": "Marca cumplimiento de un bloque pasado.",
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "completed": {
                    "type": "string",
                    "enum": ["true", "false", "partial"],
                },
                "note": {"type": "string"},
            },
            "required": ["event_id", "completed"],
        },
    },
    {
        "name": "get_completion_history",
        "description": "Estadísticas históricas de cumplimiento (por hora del día).",
        "parameters": {
            "type": "object",
            "properties": {"days": {"type": "integer", "description": "default 14"}},
        },
    },
    {
        "name": "get_active_goals",
        "description": "Lee metas activas con sus deadlines y progreso.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_active_experiments",
        "description": "Lee experimentos activos con sus cadencias.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_pending_tasks",
        "description": "Lee tareas pendientes de Notion sin agendar.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "log_experiment_progress",
        "description": "Avanza progreso de un experimento (usar tras verificar cumplimiento).",
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
        "description": "Avanza % de una meta (usar tras completar bloque asociado).",
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
]


# ── Prompts ─────────────────────────────────────────────────────────────────

PLAN_DAY_PROMPT_TEMPLATE = """You are an expert time management planner. Your role: generate a realistic \
plan for the specified day in the user's Google Calendar, respecting goals, active experiments, \
pending tasks, energy levels, and existing fixed events.

IMPORTANT: All datetimes you generate must be in Lima time (America/Lima, UTC-5).
Always use ISO format with explicit offset: 2024-05-20T09:00:00-05:00
NEVER use the Z suffix or UTC for blocks you create.

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

# Planning rules
1. Deep work (high focus) → only during profile peak hours
2. Operational / repetitive tasks → low energy hours
3. Each active experiment needs its daily slot or per check_in_every_days
4. For each short-term goal: calculate remaining hours until target_date and distribute proportionally
5. 10-15min buffer between blocks
6. DO NOT overwrite existing fixed events (meetings, etc.)
7. Blocks 30-90min — no more than 2h continuous
8. Lunch / breaks NOT optional
9. If required hours > available hours → flag at end of plan
10. Based on completion history: DO NOT schedule deep work at hours with <40% completion rate

# Your job
1. Call `get_completion_history` to check real patterns if needed
2. Call `list_calendar_events` to confirm free slots for the day
3. For each block call `create_block` with its goal_id/experiment_id/task_id association
4. At the end reply with a brief plan summary + warnings if over-allocated

Action: plan the day. Start now."""


VERIFY_PROMPT_TEMPLATE = """You are a planner. Time to verify plan blocks that have already passed.

# Current time
{now_iso}

# Plan blocks that passed in the last 3h
{recent_blocks}

# Rules
1. For each UNMARKED block:
   - If associated with an experiment_id, call `log_experiment_progress` with did_it=true
     assuming completed (user can correct later if false)
   - Mark the block with `mark_block_completed` status "true"
2. For each block marked "false":
   - Find a free slot in the next 24h and create an equivalent new block
3. If 2+ "false" for the same experiment in the last 3 days, flag it

Reply with a summary of what you did."""


EDIT_REQUEST_PROMPT_TEMPLATE = """You are a planner. The user is requesting a specific change to their Calendar. \
Your job is to validate coherence with their goals, peak hours, and patterns — then apply (or propose an alternative).

IMPORTANT: All datetimes you generate must be in Lima time (America/Lima, UTC-5).
Always use ISO format with explicit offset: 2024-05-20T09:00:00-05:00
NEVER use the Z suffix or UTC for blocks you create or move.

# User request (natural language)
"{instruction}"

# Current time (Lima, UTC-5)
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
{upcoming_events}

# Completion history (last 14 days)
{completion_summary}

# Decision rules
1. Parse the intent: create / move / delete / query?
2. Identify entities: affected block(s), date/time, duration.
3. If event_id is ambiguous, use `list_calendar_events` to find it by title/date.
4. VALIDATE before applying:
   - Does the proposed time fall in low_energy_hours and is it deep work? → warn and suggest peak hours
   - Does it conflict with an existing fixed event? → suggest nearby free slot with `find_free_slots`
   - Does it remove the only block dedicated to an active goal? → warn before deleting
   - Is that time repeatedly low in completion history? → mention and suggest alternative
5. If validation OK, execute with `create_block`/`update_block`/`delete_block`.
6. If there's a conflict, DO NOT apply — return an alternative proposal for the user to decide.

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

# Your job
1. Based ONLY on the user's response above, determine which blocks were completed.
   — NEVER mark a block as completed if the user didn't explicitly mention it.
   — If the user didn't mention a block, mark it with completed="false".
2. Call `mark_block_completed` for each block based on what the user said.
3. For experiments/goals: only advance progress if the block was confirmed by the user.
4. Generate a brief review: what they completed (per user), what's pending, day pattern if applicable.

Reply with the final review the user sees."""


class PlannerAgent:
    """Subagente de planificación con loop function calling propio."""

    def __init__(
        self,
        calendar_service: Any,
        notion_service: Any,
        experiment_service: Any,
        memory_manager: Any,
        gemini_api_key: str = "",
        gcp_project_id: str = "",
        user_timezone: str = "America/Lima",
    ) -> None:
        if gcp_project_id:
            self._client = genai.Client(
                vertexai=True,
                project=gcp_project_id,
                location="us-central1",
            )
            self._model_id = MODEL_ID_VERTEX
        else:
            self._client = genai.Client(api_key=gemini_api_key)
            self._model_id = MODEL_ID_AISTUDIO

        self._calendar = calendar_service
        self._notion = notion_service
        self._experiments = experiment_service
        self._memory = memory_manager
        self._timezone = user_timezone

    # ── API pública (llamada desde main agent / endpoints) ─────────────────

    async def plan_day(self, target_date_iso: str | None = None) -> dict[str, Any]:
        """Genera plan para un día (default mañana)."""
        if target_date_iso:
            target = date.fromisoformat(target_date_iso)
        else:
            target = date.today() + timedelta(days=1)

        ctx = await self._build_planning_context(target)
        prompt = PLAN_DAY_PROMPT_TEMPLATE.format(
            target_date=target.isoformat(),
            day_name=_day_name_es(target),
            user_profile_summary=ctx["profile_summary"],
            personality_summary=ctx["personality_summary"],
            insights_summary=ctx["insights_summary"],
            completion_summary=ctx["completion_summary"],
            goals_summary=ctx["goals_summary"],
            experiments_summary=ctx["experiments_summary"],
            pending_tasks_summary=ctx["pending_tasks_summary"],
            fixed_events_summary=ctx["fixed_events_summary"],
        )

        return await self._run_loop(prompt, mode="plan")

    def _now_lima(self) -> datetime:
        """Hora actual en Lima (UTC-5)."""
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo(self._timezone))
        except Exception:
            from datetime import timezone as _tz, timedelta as _td
            return datetime.now(_tz(_td(hours=-5)))

    async def verify_recent(self) -> dict[str, Any]:
        """Verifica bloques pasados en últimas ~3h."""
        from datetime import timezone
        now_utc = datetime.now(timezone.utc)
        start = now_utc - timedelta(hours=3)
        events = self._calendar.list_events(start=start, end=now_utc)
        plan_events = [
            e for e in events
            if "[plan]" in (e.get("summary") or "")
        ]
        if not plan_events:
            return {"ok": True, "verified": 0, "reason": "no plan events in window"}

        now_lima = self._now_lima()
        prompt = VERIFY_PROMPT_TEMPLATE.format(
            now_iso=now_lima.isoformat(),
            recent_blocks=json.dumps(plan_events, ensure_ascii=False, indent=2),
        )
        return await self._run_loop(prompt, mode="verify")

    async def edit_request(self, instruction: str) -> dict[str, Any]:
        """Aplica cambio puntual al Calendar validando coherencia."""
        from datetime import timezone
        now_utc = datetime.now(timezone.utc)
        now_lima = self._now_lima()
        today = now_lima.date()

        ctx = await self._build_planning_context(today + timedelta(days=1))

        upcoming = self._calendar.list_events(
            start=now_utc,
            end=now_utc + timedelta(days=7),
            max_results=50,
        )
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
        """Review del día. Default: hoy."""
        from datetime import timezone
        day = date.fromisoformat(day_iso) if day_iso else date.today()
        start = datetime.combine(day, datetime.min.time()).replace(tzinfo=timezone.utc)
        end = start + timedelta(days=1)

        events = self._calendar.list_events(start=start, end=end)
        plan_events = [e for e in events if "[plan]" in (e.get("summary") or "")]
        stats = self._calendar.get_completion_history(days=1)

        user_input_text = user_input.strip() if user_input.strip() else "(el usuario no respondió — asumir todo no cumplido)"

        prompt = DAILY_REVIEW_PROMPT_TEMPLATE.format(
            today_blocks=json.dumps(plan_events, ensure_ascii=False, indent=2),
            stats=json.dumps(stats, ensure_ascii=False, indent=2),
            user_input=user_input_text,
        )
        return await self._run_loop(prompt, mode="review")

    # ── Context loading ────────────────────────────────────────────────────

    async def _build_planning_context(self, target_date) -> dict[str, str]:
        from datetime import timezone

        # Perfil
        profile = await self._memory._get_user_profile() if self._memory else {}
        productivity = profile.get("productivity", {}) if isinstance(profile, dict) else {}
        cal = productivity.get("estimation_calibration", 1.0)
        if cal > 1.05:
            cal_note = f"⚠️ Calibración temporal: usuario subestima {int((cal - 1) * 100)}% — multiplica time_estimate × {cal}"
        elif cal < 0.95:
            cal_note = f"ℹ️ Calibración temporal: usuario sobreestima {int((1 - cal) * 100)}% — multiplica time_estimate × {cal}"
        else:
            cal_note = "Calibración temporal: estimaciones precisas (factor 1.0)"

        profile_summary = (
            f"Nombre: {profile.get('name', 'usuario')}\n"
            f"Ocupación: {profile.get('occupation', '')}\n"
            f"Peak hours: {productivity.get('peak_hours', '')}\n"
            f"Low energy: {productivity.get('low_energy_hours', '')}\n"
            f"Work start/end: {productivity.get('work_start', '')} - {productivity.get('work_end', '')}\n"
            f"Focus block preferido: {productivity.get('preferred_focus_block_minutes', 90)}min\n"
            f"{cal_note}"
        )

        # Personalidad
        enneagram = (profile.get("personality") or {}).get("enneagram") if isinstance(profile, dict) else None
        if enneagram:
            personality_summary = (
                f"Eneagrama Tipo {enneagram.get('type')} ({enneagram.get('name')}). "
                f"Trampas comunes: {', '.join(enneagram.get('common_traps', []))}"
            )
        else:
            personality_summary = "No disponible"

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
        day_start = datetime.combine(target_date, datetime.min.time()).replace(tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)
        events = self._calendar.list_events(start=day_start, end=day_end)
        fixed_events = [e for e in events if "[plan]" not in (e.get("summary") or "")]
        fixed_events_summary = "\n".join(
            f"- {e.get('summary')}: {e.get('start')} → {e.get('end')}"
            for e in fixed_events
        ) or "(ninguno)"

        return {
            "profile_summary": profile_summary,
            "personality_summary": personality_summary,
            "insights_summary": insights_summary,
            "completion_summary": completion_summary,
            "goals_summary": goals_summary,
            "experiments_summary": experiments_summary,
            "pending_tasks_summary": pending_tasks_summary,
            "fixed_events_summary": fixed_events_summary,
        }

    # ── Loop ────────────────────────────────────────────────────────────────

    async def _run_loop(self, user_prompt: str, mode: str = "plan") -> dict[str, Any]:
        """Loop function calling. Devuelve dict con summary y acciones."""
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
            response = self._client.models.generate_content(
                model=self._model_id,
                contents=contents,
                config=types.GenerateContentConfig(
                    tools=gemini_tools,
                    temperature=0.4,
                    max_output_tokens=4096,
                ),
            )

            candidate = response.candidates[0]
            parts = candidate.content.parts
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
                notion_update = None
                if task_id:
                    if completed_val == "true":
                        notion_update = await self._notion.update_task(task_id, status="done")
                    elif completed_val == "partial":
                        notion_update = await self._notion.update_task(task_id, status="in progress")
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

            return {"error": f"Tool desconocida: {name}"}
        except Exception as exc:
            logger.error("Planner tool %s failed: %s", name, exc)
            return {"error": str(exc)}


# ── Helpers ─────────────────────────────────────────────────────────────────


def _day_name_es(d) -> str:
    return ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"][d.weekday()]
