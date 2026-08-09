"""
Tool executor: dispatches Gemini tool calls to the right services.

Each tool name maps to a handler that calls the appropriate service and returns
a simplified dict for the model to read.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any

from app.agent.skill_manager import SkillManager
from app.services.experiments import ExperimentService
from app.services.memory import MemoryManager
from app.services.notion import NotionService
from app.services.agent_queue import AgentTaskQueue

logger = logging.getLogger(__name__)


class ToolExecutor:
    """Executes tool calls by dispatching them to the services."""

    def __init__(
        self,
        notion: NotionService,
        memory: MemoryManager,
        source: str = "text",
        experiments: ExperimentService | None = None,
        planner: Any = None,
        calendar: Any = None,
        skill_manager: SkillManager | None = None,
        agent_queue: AgentTaskQueue | None = None,
        telegram: Any = None,
    ) -> None:
        self.notion = notion
        self.memory = memory
        self.experiments = experiments
        self.planner = planner
        self.calendar = calendar
        self._source = source
        self._skills = skill_manager
        self._agent_queue = agent_queue
        self.telegram = telegram

    async def execute(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """
        Execute one tool and return its result.

        Args:
            tool_name: The tool name (must match one declared in TOOLS).
            args: The tool arguments from Gemini's function call.

        Returns:
            A dict with the execution result.
        """
        logger.info("Executing tool: %s with args: %s", tool_name, json.dumps(args, default=str))

        handler = self._handlers.get(tool_name)
        if handler is None:
            logger.error("Unknown tool: %s", tool_name)
            return {"error": f"Unknown tool: {tool_name}"}

        try:
            result = await handler(self, **args)
            # Invalidate the work-context cache after any write
            if tool_name in ("create_task", "update_task", "archive_task", "create_goal", "update_goal_progress", "update_goal", "archive_goal", "create_key_result", "update_key_result", "complete_key_result", "archive_key_result"):
                self.memory.invalidate_work_context_cache()
            return result
        except Exception as exc:
            logger.error("Tool %s failed: %s", tool_name, exc)
            return {"error": f"Error running {tool_name}: {str(exc)}"}

    # ── Handlers ─────────────────────────────────────────────────────────────

    async def _send_message_to_user(
        self, message: str, message_type: str, reason: str
    ) -> dict[str, Any]:
        """
        Record the intent to message the user. Nothing is sent here:
        ProactiveService reads this decision out of actions_taken and applies the
        hard limits (quiet hours, daily cap, anti-repetition) before sending.
        """
        logger.info("Proactive decision: type=%s reason=%s", message_type, reason)
        return {"status": "queued", "message_type": message_type, "reason": reason}

    async def _save_day_priority(self, date: str, priority: str) -> dict[str, Any]:
        self.memory.save_day_priority(date, priority)
        return {"status": "saved", "date": date}

    async def _create_task(
        self,
        title: str,
        priority: str = "p2",
        due_date: str | None = None,
        scheduled_date: str | None = None,
        time_estimate_minutes: int | None = None,
        tags: list[str] | None = None,
        goal_id: str | None = None,
    ) -> dict[str, Any]:
        source_label = "Voice" if self._source == "esp32" else "Text"
        result = await self.notion.create_task(
            title=title,
            priority=priority.upper(),
            due_date=due_date,
            scheduled_date=scheduled_date,
            time_estimate_minutes=time_estimate_minutes,
            tags=tags,
            source=source_label,
            goal_id=goal_id,
        )
        return {"status": "created", "task": result}

    async def _get_tasks(
        self,
        status: str | None = None,
        date: str | None = None,
        priority: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        tasks = await self.notion.get_tasks(
            status=status,
            date=date,
            priority=priority,
            limit=limit,
        )
        return {"tasks": tasks, "count": len(tasks)}

    async def _update_task(
        self,
        task_id: str,
        title: str | None = None,
        status: str | None = None,
        priority: str | None = None,
        due_date: str | None = None,
        scheduled_date: str | None = None,
        time_estimate_minutes: int | None = None,
        tags: list[str] | None = None,
        goal_id: str | None = None,
    ) -> dict[str, Any]:
        candidates = {
            "title": title,
            "status": status,
            "priority": priority,
            "due_date": due_date,
            "scheduled_date": scheduled_date,
            "time_estimate_minutes": time_estimate_minutes,
            "tags": tags,
            "goal_id": goal_id,
        }
        updates = {k: v for k, v in candidates.items() if v is not None}

        result = await self.notion.update_task(task_id, **updates)
        return {"status": "updated", "task": result}

    async def _archive_task(self, task_id: str, task_title: str) -> dict[str, Any]:
        result = await self.notion.archive_task(task_id)
        return {"status": "archived", "task": task_title, **result}



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

    async def _create_key_result(
        self,
        goal_id: str,
        title: str,
        notes: str = "",
    ) -> dict[str, Any]:
        result = await self.notion.create_key_result(goal_id=goal_id, title=title, notes=notes)
        return {"status": "created", "key_result": result}

    async def _get_key_results(
        self,
        goal_id: str | None = None,
        unassigned_only: bool = False,
    ) -> dict[str, Any]:
        krs = await self.notion.get_key_results(
            goal_id=goal_id, unassigned_only=unassigned_only
        )
        done = sum(1 for kr in krs if kr.get("status") == "Done")
        return {"key_results": krs, "count": len(krs), "done": done, "pending": len(krs) - done}

    async def _archive_key_result(self, kr_id: str, kr_title: str) -> dict[str, Any]:
        result = await self.notion.archive_key_result(kr_id)
        return {"status": "archived", "key_result": kr_title, **result}

    async def _update_key_result(
        self,
        kr_id: str,
        title: str | None = None,
        notes: str | None = None,
        status: str | None = None,
        goal_id: str | None = None,
    ) -> dict[str, Any]:
        result = await self.notion.update_key_result(
            kr_id=kr_id, title=title, notes=notes, status=status, goal_id=goal_id
        )
        return {"status": "updated", "key_result": result}

    async def _complete_key_result(self, kr_id: str, note: str = "") -> dict[str, Any]:
        result = await self.notion.mark_kr_done(kr_id=kr_id, note=note)
        return {"status": "completed", "key_result": result}

    async def _read_storage(
        self,
        collection: str,
        document_id: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        return self.memory.read_storage(
            collection=collection, document_id=document_id, limit=limit
        )

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
            return {"error": "ExperimentService unavailable"}
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
            return {"error": "ExperimentService unavailable"}
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
            return {"error": "ExperimentService unavailable"}
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

    # ── Calendar (direct read-only access) ──────────────────────────────────

    async def _get_calendar_events(
        self,
        start_iso: str | None = None,
        end_iso: str | None = None,
        days: int = 1,
    ) -> dict[str, Any]:
        if self.calendar is None:
            return {"error": "Calendar unavailable"}
        from datetime import datetime as _dt
        start = _dt.fromisoformat(start_iso) if start_iso else None
        end = _dt.fromisoformat(end_iso) if end_iso else None
        events = self.calendar.list_events(start=start, end=end, days=days)
        return {"events": events, "count": len(events)}

    # ── Planner delegation ──────────────────────────────────────────────────

    async def _delegate_to_planner(
        self,
        action: str,
        target_date: str | None = None,
        instruction: str | None = None,
    ) -> dict[str, Any]:
        if self.planner is None:
            return {"error": "Planner unavailable"}
        if action == "plan_day":
            return await self.planner.plan_day(target_date_iso=target_date)
        if action == "daily_review":
            return await self.planner.daily_review(day_iso=target_date, user_input=instruction or "")
        if action == "edit_request":
            if not instruction:
                return {"error": "edit_request requiere 'instruction'"}
            return await self.planner.edit_request(instruction=instruction)
        return {"error": f"Unknown action: {action}"}

    # ── Memory management ────────────────────────────────────────────────────

    async def _remember_fact(self, fact: str) -> dict[str, Any]:
        ok = await self.memory.add_memory(fact)
        if ok:
            return {"status": "saved", "fact": fact}
        return {"error": "Memory service not available"}

    async def _list_memories(self) -> dict[str, Any]:
        memories = await self.memory.list_memories()
        if not memories:
            return {"memories": [], "message": "No memories stored yet"}
        return {"memories": memories, "count": len(memories)}

    async def _forget_memory(self, memory_id: str, memory_text: str) -> dict[str, Any]:
        ok = await self.memory.delete_memory(memory_id)
        if ok:
            return {"status": "deleted", "memory": memory_text}
        return {"error": f"Could not delete memory '{memory_text}'"}

    async def _update_my_profile(
        self, entry: str, replaces: str | None = None
    ) -> dict[str, Any]:
        result = self.memory.add_profile_entry(entry, replaces=replaces)
        if result.get("error"):
            return result
        self.memory.invalidate_work_context_cache()

        # The profile goes into all three agents' prompts, so a change here alters
        # the behaviour of the whole system. It gets recorded in the log.
        if result.get("replaced"):
            await self._log_to_bitacora(
                f"✏️ perfil editado\nantes: {result['replaced']}\nahora: {entry}"
            )
        else:
            await self._log_to_bitacora(f"✏️ perfil: entrada nueva\n{entry}")
        return result

    async def _forget_about_me(self, entry: str) -> dict[str, Any]:
        result = self.memory.remove_profile_entry(entry)
        if result.get("status") == "removed":
            self.memory.invalidate_work_context_cache()
            await self._log_to_bitacora(f"🗑️ perfil: entrada borrada\n{entry}")
        return result

    async def _log_to_bitacora(self, text: str) -> None:
        """Write to the activity log if available. Never raises."""
        if self.telegram is None:
            return
        try:
            await self.telegram.send_log_message(text)
        except Exception:
            pass

    # ── Skills ───────────────────────────────────────────────────────────────

    async def _save_skill(
        self,
        name: str,
        content: str,
        reason: str,
    ) -> dict[str, Any]:
        if self._skills is None:
            return {"error": "SkillManager not available"}
        try:
            slug = self._skills.save(name, content, reason)
            return {"status": "saved", "name": slug, "reason": reason}
        except Exception as exc:
            logger.error("Failed to save skill '%s': %s", name, exc)
            return {"error": str(exc)}

    async def _delete_skill(self, name: str) -> dict[str, Any]:
        if self._skills is None:
            return {"error": "SkillManager not available"}
        try:
            deleted = self._skills.delete(name)
            if deleted:
                return {"status": "deleted", "name": name}
            return {"status": "not_found", "name": name}
        except Exception as exc:
            logger.error("Failed to delete skill '%s': %s", name, exc)
            return {"error": str(exc)}

    # ── Scheduled agent reminders ────────────────────────────────────────────

    async def _schedule_agent_task(
        self, title: str, prompt: str, run_at: str
    ) -> dict[str, Any]:
        if self._agent_queue is None:
            return {"error": "AgentTaskQueue not available"}
        try:
            task_id = self._agent_queue.create_task(title, prompt, run_at)
            return {"status": "scheduled", "task_id": task_id, "title": title, "run_at": run_at}
        except Exception as exc:
            logger.error("schedule_agent_task failed: %s", exc)
            return {"error": str(exc)}

    async def _cancel_agent_task(
        self, task_id: str, task_title: str
    ) -> dict[str, Any]:
        if self._agent_queue is None:
            return {"error": "AgentTaskQueue not available"}
        cancelled = self._agent_queue.cancel_task(task_id)
        if cancelled:
            return {"status": "cancelled", "task": task_title}
        return {"status": "not_found", "task_id": task_id}

    async def _list_agent_tasks(self) -> dict[str, Any]:
        if self._agent_queue is None:
            return {"tasks": [], "message": "AgentTaskQueue not available"}
        tasks = self._agent_queue.list_tasks(status="pending")
        if not tasks:
            return {"tasks": [], "message": "No agent tasks pending"}
        return {"tasks": tasks, "count": len(tasks)}

    # ── Dispatch map ─────────────────────────────────────────────────────────

    _handlers: dict[str, Any] = {
        "create_task": _create_task,
        "get_tasks": _get_tasks,
        "update_task": _update_task,
        "archive_task": _archive_task,

        "create_goal": _create_goal,
        "get_goals": _get_goals,
        "save_day_priority": _save_day_priority,
        "send_message_to_user": _send_message_to_user,
        "update_goal_progress": _update_goal_progress,
        "update_goal": _update_goal,
        "archive_goal": _archive_goal,
        "get_user_profile": _get_user_profile,
        "read_storage": _read_storage,
        "start_experiment": _start_experiment,
        "log_experiment_progress": _log_experiment_progress,
        "close_experiment": _close_experiment,
        "list_active_experiments": _list_active_experiments,
        "delegate_to_planner": _delegate_to_planner,
        "get_calendar_events": _get_calendar_events,
        "remember_fact": _remember_fact,
        "list_memories": _list_memories,
        "forget_memory": _forget_memory,
        "update_my_profile": _update_my_profile,
        "forget_about_me": _forget_about_me,
        "save_skill": _save_skill,
        "delete_skill": _delete_skill,
        "schedule_agent_task": _schedule_agent_task,
        "cancel_agent_task": _cancel_agent_task,
        "list_agent_tasks": _list_agent_tasks,
        "create_key_result": _create_key_result,
        "get_key_results": _get_key_results,
        "update_key_result": _update_key_result,
        "complete_key_result": _complete_key_result,
        "archive_key_result": _archive_key_result,
    }


def date_module_today() -> date:
    """Wrapper to make testing easier."""
    return date.today()
