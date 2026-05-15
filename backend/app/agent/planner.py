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

PLAN_DAY_PROMPT_TEMPLATE = """Eres un planificador experto en gestión del tiempo. Tu rol: generar un plan \
realista para el día especificado en el Google Calendar del usuario, respetando \
metas, experimentos activos, tareas pendientes, energía y eventos fijos ya existentes.

# Día a planificar
{target_date} ({day_name})

# Perfil del usuario
{user_profile_summary}

# Personalidad
{personality_summary}

# Insights/patrones detectados
{insights_summary}

# Cumplimiento histórico
{completion_summary}

# Metas activas
{goals_summary}

# Experimentos activos
{experiments_summary}

# Tareas pendientes Notion
{pending_tasks_summary}

# Eventos fijos ya en Calendar
{fixed_events_summary}

# Reglas de planificación
1. Deep work (alto enfoque) → solo en peak hours del perfil
2. Tareas operativas / repetitivas → low energy hours
3. Cada experimento activo necesita su slot diario o según check_in_every_days
4. Para cada meta corto plazo: calcular horas restantes hasta target_date
   y distribuir proporcionalmente
5. Buffer 10-15min entre bloques
6. NO sobreescribir eventos fijos existentes (meetings, etc.)
7. Bloques 30-90min — no más de 2h continuas
8. Almuerzo / breaks NO opcionales
9. Si suma horas requeridas > horas disponibles → flag al final del plan
10. Para tu cumplimiento histórico: NO agendes deep work en horas con <40% cumplimiento

# Tu trabajo
1. Llama `get_completion_history` para ver patrones reales si necesario
2. Llama `list_calendar_events` para confirmar slots libres del día
3. Para cada bloque generá `create_block` con su asociación goal_id/experiment_id/task_id
4. Al final responde con resumen breve del plan + advertencias si hay sobre-asignación

Acción: planifica el día. Empieza ahora."""


VERIFY_PROMPT_TEMPLATE = """Eres planificador. Toca verificar bloques de plan que ya pasaron.

# Hora actual
{now_iso}

# Bloques de plan que pasaron en las últimas 3h
{recent_blocks}

# Reglas
1. Para cada bloque NO marcado:
   - Si está asociado a un experiment_id, llamar `log_experiment_progress`
     con did_it=true asumiendo cumplido (luego usuario corrige si fue falso)
   - Marcar el bloque con `mark_block_completed` status "true"
2. Para cada bloque marcado "false":
   - Buscar slot libre en próximas 24h y crear nuevo bloque equivalente
3. Si hay 2+ "false" del mismo experimento en últimos 3 días, flaggear

Responde con resumen de qué hiciste."""


DAILY_REVIEW_PROMPT_TEMPLATE = """Eres planificador. Cierre del día — review.

# Bloques del día
{today_blocks}

# Estadísticas
{stats}

# Tu trabajo
1. Para bloques sin verificar todavía, llamar `mark_block_completed`
   (en su mayoría asumir cumplidos a menos que el usuario haya dicho lo contrario en chat)
2. Para experiments asociados a bloques cumplidos, avanzar progreso
3. Para goals asociadas a bloques cumplidos, calcular nuevo % y `update_goal_progress`
4. Generar review breve para el usuario: qué cumplió, qué quedó pendiente,
   patrón observado del día (si aplica)

Responde con el review final que ve el usuario."""


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

    async def verify_recent(self) -> dict[str, Any]:
        """Verifica bloques pasados en últimas ~3h."""
        from datetime import timezone
        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=3)
        events = self._calendar.list_events(start=start, end=now)
        plan_events = [
            e for e in events
            if "[plan]" in (e.get("summary") or "")
        ]
        if not plan_events:
            return {"ok": True, "verified": 0, "reason": "no plan events in window"}

        prompt = VERIFY_PROMPT_TEMPLATE.format(
            now_iso=now.isoformat(),
            recent_blocks=json.dumps(plan_events, ensure_ascii=False, indent=2),
        )
        return await self._run_loop(prompt, mode="verify")

    async def daily_review(self, day_iso: str | None = None) -> dict[str, Any]:
        """Review del día. Default: hoy."""
        from datetime import timezone
        day = date.fromisoformat(day_iso) if day_iso else date.today()
        start = datetime.combine(day, datetime.min.time()).replace(tzinfo=timezone.utc)
        end = start + timedelta(days=1)

        events = self._calendar.list_events(start=start, end=end)
        plan_events = [e for e in events if "[plan]" in (e.get("summary") or "")]
        stats = self._calendar.get_completion_history(days=1)

        prompt = DAILY_REVIEW_PROMPT_TEMPLATE.format(
            today_blocks=json.dumps(plan_events, ensure_ascii=False, indent=2),
            stats=json.dumps(stats, ensure_ascii=False, indent=2),
        )
        return await self._run_loop(prompt, mode="review")

    # ── Context loading ────────────────────────────────────────────────────

    async def _build_planning_context(self, target_date) -> dict[str, str]:
        from datetime import timezone

        # Perfil
        profile = await self._memory._get_user_profile() if self._memory else {}
        productivity = profile.get("productivity", {}) if isinstance(profile, dict) else {}
        profile_summary = (
            f"Nombre: {profile.get('name', 'usuario')}\n"
            f"Ocupación: {profile.get('occupation', '')}\n"
            f"Peak hours: {productivity.get('peak_hours', '')}\n"
            f"Low energy: {productivity.get('low_energy_hours', '')}\n"
            f"Work start/end: {productivity.get('work_start', '')} - {productivity.get('work_end', '')}\n"
            f"Focus block preferido: {productivity.get('preferred_focus_block_minutes', 90)}min"
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
                ev = self._calendar.mark_event_completed(
                    event_id=args["event_id"],
                    completed=args.get("completed", "true"),
                    note=args.get("note"),
                )
                return {"marked": ev}

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
