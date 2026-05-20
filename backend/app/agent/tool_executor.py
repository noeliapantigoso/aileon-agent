"""
Ejecutor de tools: despacha llamadas de Gemini a los servicios correspondientes.

Cada tool name se mapea a un método del servicio apropiado.
Retorna resultados simplificados como dict para que Gemini los procese.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any

from app.services.experiments import ExperimentService
from app.services.memory import MemoryManager
from app.services.notion import NotionService

logger = logging.getLogger(__name__)


class ToolExecutor:
    """Ejecuta tool calls despachándolas a los servicios."""

    def __init__(
        self,
        notion: NotionService,
        memory: MemoryManager,
        source: str = "text",
        experiments: ExperimentService | None = None,
        planner: Any = None,
        calendar: Any = None,
    ) -> None:
        self.notion = notion
        self.memory = memory
        self.experiments = experiments
        self.planner = planner
        self.calendar = calendar
        self._source = source

    async def execute(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """
        Ejecuta una tool y retorna el resultado.

        Args:
            tool_name: Nombre de la tool (debe coincidir con TOOLS).
            args: Argumentos de la tool (del function call de Gemini).

        Returns:
            Diccionario con el resultado de la ejecución.
        """
        logger.info("Executing tool: %s with args: %s", tool_name, json.dumps(args, default=str))

        handler = self._handlers.get(tool_name)
        if handler is None:
            logger.error("Unknown tool: %s", tool_name)
            return {"error": f"Tool desconocida: {tool_name}"}

        try:
            result = await handler(self, **args)
            # Invalidar cache de trabajo después de operaciones de escritura
            if tool_name in ("create_task", "update_task", "save_daily_plan", "create_goal", "update_goal_progress", "update_goal", "archive_goal"):
                self.memory.invalidate_work_context_cache()
            return result
        except Exception as exc:
            logger.error("Tool %s failed: %s", tool_name, exc)
            return {"error": f"Error ejecutando {tool_name}: {str(exc)}"}

    # ── Handlers ─────────────────────────────────────────────────────────────

    async def _create_task(
        self,
        title: str,
        priority: str = "p2",
        due_date: str | None = None,
        scheduled_date: str | None = None,
        time_estimate_minutes: int | None = None,
        project: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        source_label = "Voice" if self._source == "esp32" else "Text"
        result = await self.notion.create_task(
            title=title,
            priority=priority.upper(),
            due_date=due_date,
            scheduled_date=scheduled_date,
            time_estimate_minutes=time_estimate_minutes,
            project=project,
            tags=tags,
            source=source_label,
        )
        return {"status": "created", "task": result}

    async def _get_tasks(
        self,
        status: str | None = None,
        date: str | None = None,
        priority: str | None = None,
        project: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        tasks = await self.notion.get_tasks(
            status=status,
            date=date,
            priority=priority,
            project=project,
            limit=limit,
        )
        return {"tasks": tasks, "count": len(tasks)}

    async def _update_task(
        self,
        task_id: str,
        status: str | None = None,
        priority: str | None = None,
        due_date: str | None = None,
        scheduled_date: str | None = None,
    ) -> dict[str, Any]:
        updates = {}
        if status is not None:
            updates["status"] = status
        if priority is not None:
            updates["priority"] = priority
        if due_date is not None:
            updates["due_date"] = due_date
        if scheduled_date is not None:
            updates["scheduled_date"] = scheduled_date

        result = await self.notion.update_task(task_id, **updates)
        return {"status": "updated", "task": result}

    async def _save_note(
        self,
        content: str,
        title: str | None = None,
        tags: list[str] | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        if source is None:
            source = "Voice" if self._source == "esp32" else "Text"
        result = await self.notion.save_note(
            content=content,
            title=title,
            tags=tags,
            source=source.title(),
        )
        return {"status": "saved", "note": result}

    async def _search_notes(self, query: str) -> dict[str, Any]:
        notes = await self.notion.search_notes(query)
        return {"notes": notes, "count": len(notes)}

    async def _get_daily_agenda(
        self,
        date: str | None = None,
    ) -> dict[str, Any]:
        agenda = await self.notion.get_daily_agenda(date)
        return {"agenda": agenda}

    async def _organize_day(
        self,
        date: str | None = None,
        focus_areas: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Recopila info necesaria para organizar un día.
        El LLM usará estos datos para generar el plan y luego
        llamará a save_daily_plan.
        """
        if not date:
            tomorrow = date_module_today() + timedelta(days=1)
            date = tomorrow.isoformat()

        # Recopilar datos
        tasks = await self.notion.get_tasks(status="pending", limit=30)
        goals = await self.notion.get_goals(status="active")
        existing_agenda = await self.notion.get_daily_agenda(date)

        return {
            "date": date,
            "pending_tasks": tasks,
            "active_goals": goals,
            "existing_agenda": existing_agenda,
            "focus_areas": focus_areas or [],
        }

    async def _create_goal(
        self,
        title: str,
        goal_type: str = "long_term",
        area: str | None = None,
        target_date: str | None = None,
        key_results: str | None = None,
        initial_progress: int = 0,
    ) -> dict[str, Any]:
        result = await self.notion.create_goal(
            title=title,
            goal_type=goal_type,
            area=area,
            target_date=target_date,
            key_results=key_results,
            initial_progress=initial_progress,
        )
        return {"status": "created", "goal": result}

    async def _get_goals(
        self,
        type: str | None = None,
        status: str = "active",
    ) -> dict[str, Any]:
        goals = await self.notion.get_goals(goal_type=type, status=status)
        return {"goals": goals, "count": len(goals)}

    async def _update_goal_progress(
        self,
        goal_id: str,
        progress_note: str,
        new_percentage: int | None = None,
    ) -> dict[str, Any]:
        result = await self.notion.update_goal_progress(
            goal_id=goal_id,
            progress_note=progress_note,
            new_percentage=new_percentage,
        )
        return {"status": "updated", "goal": result}

    async def _update_goal(
        self,
        goal_id: str,
        title: str | None = None,
        goal_type: str | None = None,
        area: str | None = None,
        target_date: str | None = None,
        key_results: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        result = await self.notion.update_goal(
            goal_id=goal_id,
            title=title,
            goal_type=goal_type,
            area=area,
            target_date=target_date,
            key_results=key_results,
            status=status,
        )
        return {"status": "updated", "goal": result}

    async def _archive_goal(self, goal_id: str) -> dict[str, Any]:
        result = await self.notion.archive_goal(goal_id)
        return {"status": "archived", "goal": result}

    async def _get_user_profile(self) -> dict[str, Any]:
        context = await self.memory.get_full_context("")
        return {"profile": context.get("user_profile", {})}

    # ── Experiments ─────────────────────────────────────────────────────────

    async def _start_experiment(
        self,
        name: str,
        hypothesis: str,
        duration_days: int = 7,
        check_in_every_days: int = 3,
    ) -> dict[str, Any]:
        if self.experiments is None:
            return {"error": "ExperimentService no disponible"}
        result = self.experiments.start_experiment(
            name=name,
            hypothesis=hypothesis,
            duration_days=duration_days,
            check_in_every_days=check_in_every_days,
        )
        return {"status": "started", "experiment": result}

    async def _log_experiment_progress(
        self,
        experiment_id: str,
        note: str,
        did_it: bool = True,
    ) -> dict[str, Any]:
        if self.experiments is None:
            return {"error": "ExperimentService no disponible"}
        result = self.experiments.log_progress(
            experiment_id=experiment_id,
            note=note,
            did_it=did_it,
        )
        return {"status": "logged", "experiment": result}

    async def _close_experiment(
        self,
        experiment_id: str,
        outcome: str,
        status: str = "completed",
    ) -> dict[str, Any]:
        if self.experiments is None:
            return {"error": "ExperimentService no disponible"}
        result = self.experiments.close_experiment(
            experiment_id=experiment_id,
            outcome=outcome,
            status=status,
        )
        return {"status": "closed", "experiment": result}

    async def _list_active_experiments(self) -> dict[str, Any]:
        if self.experiments is None:
            return {"experiments": [], "count": 0}
        active = self.experiments.list_active()
        return {"experiments": active, "count": len(active)}

    # ── Calendar (read-only directo) ────────────────────────────────────────

    async def _get_calendar_events(
        self,
        start_iso: str | None = None,
        end_iso: str | None = None,
        days: int = 1,
    ) -> dict[str, Any]:
        if self.calendar is None:
            return {"error": "Calendar no disponible"}
        from datetime import datetime as _dt
        start = _dt.fromisoformat(start_iso) if start_iso else None
        end = _dt.fromisoformat(end_iso) if end_iso else None
        events = self.calendar.list_events(start=start, end=end, days=days)
        return {"events": events, "count": len(events)}

    # ── Planner delegation ─────────────────────────────────────────────────

    async def _delegate_to_planner(
        self,
        action: str,
        target_date: str | None = None,
        instruction: str | None = None,
    ) -> dict[str, Any]:
        if self.planner is None:
            return {"error": "Planner no disponible"}
        if action == "plan_day":
            return await self.planner.plan_day(target_date_iso=target_date)
        if action == "verify_recent":
            return await self.planner.verify_recent()
        if action == "daily_review":
            return await self.planner.daily_review(day_iso=target_date, user_input=instruction or "")
        if action == "edit_request":
            if not instruction:
                return {"error": "edit_request requiere 'instruction'"}
            return await self.planner.edit_request(instruction=instruction)
        return {"error": f"Acción desconocida: {action}"}

    # ── Dispatch map ─────────────────────────────────────────────────────────

    _handlers: dict[str, Any] = {
        "create_task": _create_task,
        "get_tasks": _get_tasks,
        "update_task": _update_task,
        "save_note": _save_note,
        "search_notes": _search_notes,
        "get_daily_agenda": _get_daily_agenda,
        "organize_day": _organize_day,
        "create_goal": _create_goal,
        "get_goals": _get_goals,
        "update_goal_progress": _update_goal_progress,
        "update_goal": _update_goal,
        "archive_goal": _archive_goal,
        "get_user_profile": _get_user_profile,
        "start_experiment": _start_experiment,
        "log_experiment_progress": _log_experiment_progress,
        "close_experiment": _close_experiment,
        "list_active_experiments": _list_active_experiments,
        "delegate_to_planner": _delegate_to_planner,
        "get_calendar_events": _get_calendar_events,
    }


def date_module_today() -> date:
    """Wrapper para facilitar testing."""
    return date.today()
