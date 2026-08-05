"""
TickHandler — ejecutor del ciclo horario.

Reemplaza múltiples jobs de Cloud Scheduler con un solo endpoint /api/v1/tick
que se despierta cada hora y decide qué ejecutar basándose en:

  1. Skills con campo `schedule` (cron expression) en su frontmatter
     → corre el job mapeado en el campo `job` cuando el cron coincide con la hora actual

  2. Cola Firestore de tareas one-time (assistant_scheduled_tasks)
     → ejecuta tareas cuyo run_at <= ahora

Agregar un nuevo job agendado = crear/editar un skill con `schedule` y `job`.
No hace falta tocar Cloud Scheduler.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


# ── Cron matching ─────────────────────────────────────────────────────────────

def _cron_field_matches(expr: str, value: int) -> bool:
    """Check if a single cron field expression matches a value.

    Supports: * | n | n,m,... | n-m | */step | n/step
    """
    if expr == "*":
        return True
    for part in expr.split(","):
        if "/" in part:
            base, step = part.split("/", 1)
            start = 0 if base == "*" else int(base)
            if value >= start and (value - start) % int(step) == 0:
                return True
        elif "-" in part:
            lo, hi = part.split("-", 1)
            if int(lo) <= value <= int(hi):
                return True
        else:
            try:
                if int(part) == value:
                    return True
            except ValueError:
                pass
    return False


def cron_matches_now(schedule: str, now: datetime) -> bool:
    """Return True if cron expression fires at the given datetime (hour granularity).

    Standard cron DOW: 0=Sunday, 1=Monday ... 6=Saturday
    Python weekday:    0=Monday, 1=Tuesday ... 6=Sunday
    """
    try:
        parts = schedule.strip().split()
        if len(parts) != 5:
            return False
        _minute, hour_expr, dom_expr, month_expr, dow_expr = parts
        cron_dow = (now.weekday() + 1) % 7  # Mon=0 → 1, Sun=6 → 0
        return (
            _cron_field_matches(hour_expr, now.hour)
            and _cron_field_matches(dom_expr, now.day)
            and _cron_field_matches(month_expr, now.month)
            and _cron_field_matches(dow_expr, cron_dow)
        )
    except Exception as exc:
        logger.warning("cron_matches_now failed for '%s': %s", schedule, exc)
        return False


# ── TickHandler ───────────────────────────────────────────────────────────────

class TickHandler:
    """
    Orchestrates the hourly tick.

    Built-in job mapping (skill.job field → method):
      planner.plan_day       → planner.plan_day()
      planner.daily_review   → planner daily review flow (asks user via Telegram)
      planner.verify_recent  → planner.verify_recent()
      proactive.run_cycle    → proactive.run_cycle(current_hour_local=hour)
      insights.weekly_analysis → insights.run_weekly_analysis()

    Any other job value → runs the skill body as a generic agent prompt.
    """

    def __init__(
        self,
        skill_manager: Any,
        planner: Any = None,
        proactive: Any = None,
        insights: Any = None,
        agent: Any = None,
        task_scheduler: Any = None,
        telegram_bot: Any = None,
        calendar_service: Any = None,
        user_timezone: str = "America/Lima",
    ) -> None:
        self._skills = skill_manager
        self._planner = planner
        self._proactive = proactive
        self._insights = insights
        self._agent = agent
        self._task_scheduler = task_scheduler
        self._telegram = telegram_bot
        self._calendar = calendar_service
        self._timezone = user_timezone

    def _now_lima(self) -> datetime:
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo(self._timezone))
        except Exception:
            from datetime import timezone as _tz, timedelta as _td
            return datetime.now(_tz(_td(hours=-5)))

    # ── Main entry point ──────────────────────────────────────────────────────

    async def run(self) -> dict[str, Any]:
        now = self._now_lima()
        logger.info("Tick running at %s (hour=%d)", now.isoformat(), now.hour)
        results = []

        # 1. Cron skills
        skills = self._skills.load_all()
        for skill in skills:
            schedule = skill.get("schedule")
            if not schedule:
                continue
            if cron_matches_now(schedule, now):
                logger.info("Cron match: skill '%s' (schedule=%s)", skill["name"], schedule)
                result = await self._execute_skill_job(skill, now)
                results.append({"source": "cron", "skill": skill["name"], "result": result})

        # 2. One-time queue
        if self._task_scheduler is not None:
            due = self._task_scheduler.get_due_tasks(now)
            for task in due:
                logger.info("Due task: '%s' (id=%s)", task.get("title"), task.get("id"))
                result = await self._execute_queued_task(task)
                self._task_scheduler.mark_done(task["id"])
                results.append({"source": "queue", "task": task.get("title"), "result": result})

        return {"ok": True, "hour": now.hour, "ran": len(results), "results": results}

    # ── Job execution ─────────────────────────────────────────────────────────

    async def _execute_skill_job(self, skill: dict[str, Any], now: datetime) -> dict[str, Any]:
        job = skill.get("job", "")
        try:
            if job == "planner.plan_day":
                return await self._job_plan_day()
            if job == "planner.daily_review":
                return await self._job_daily_review()
            if job == "planner.verify_recent":
                if self._planner is None:
                    return {"error": "planner not initialized"}
                return await self._planner.verify_recent()
            if job == "proactive.run_cycle":
                if self._proactive is None:
                    return {"error": "proactive not initialized"}
                return await self._proactive.run_cycle(current_hour_local=now.hour)
            if job == "insights.weekly_analysis":
                if self._insights is None:
                    return {"error": "insights not initialized"}
                return await self._insights.run_weekly_analysis()
            # Generic: run the skill body as an agent prompt
            return await self._run_generic_skill(skill)
        except Exception as exc:
            logger.error("Skill job '%s' failed: %s", job, exc)
            return {"error": str(exc)}

    async def _job_plan_day(self) -> dict[str, Any]:
        if self._planner is None:
            return {"error": "planner not initialized"}
        result = await self._planner.plan_day()
        if self._telegram is not None:
            summary = result.get("summary", "")[:1500]
            if summary:
                await self._telegram.send_proactive_message(
                    f"📅 *Plan de mañana listo*\n\n{summary}"
                )
        return result

    async def _job_daily_review(self) -> dict[str, Any]:
        """Send the daily review question to the user via Telegram."""
        if self._telegram is None:
            return {"error": "telegram not initialized"}
        blocks_text = ""
        if self._calendar is not None:
            try:
                from datetime import timezone as _tz
                from zoneinfo import ZoneInfo
                tz = ZoneInfo(self._timezone)
                today_local = self._now_lima().date()
                day_start = datetime.combine(today_local, datetime.min.time()).replace(tzinfo=tz)
                day_end = day_start + __import__("datetime").timedelta(days=1)
                events = self._calendar.list_events(start=day_start, end=day_end)
                plan_events = [e for e in events if "[plan]" in (e.get("summary") or "")]
                if plan_events:
                    lines = [
                        f"- {e.get('summary', '?').replace('[plan] ', '')}"
                        for e in plan_events
                    ]
                    blocks_text = "\n" + "\n".join(lines)
            except Exception as exc:
                logger.warning("daily_review: could not fetch blocks: %s", exc)
        question = (
            f"🌙 *Review del día*\n\n"
            f"¿Cuáles de estos bloques completaste hoy?{blocks_text}\n\n"
            f"Respondeme y genero el resumen."
        )
        await self._telegram.send_proactive_message(question)
        return {"ok": True, "asked": True}

    async def _run_generic_skill(self, skill: dict[str, Any]) -> dict[str, Any]:
        """Run a skill body as a prompt through the main agent."""
        if self._agent is None:
            return {"error": "agent not initialized"}
        prompt = skill.get("body", skill.get("content", ""))
        if not prompt:
            return {"error": "skill has no body"}
        response = await self._agent.process(prompt, source="scheduled")
        if self._telegram is not None and response.message:
            await self._telegram.send_proactive_message(response.message)
        return {"summary": response.message}

    async def _execute_queued_task(self, task: dict[str, Any]) -> dict[str, Any]:
        """Run a one-time queued task through the main agent."""
        if self._agent is None:
            return {"error": "agent not initialized"}
        prompt = task.get("prompt", "")
        if not prompt:
            return {"error": "task has no prompt"}
        try:
            response = await self._agent.process(prompt, source="scheduled")
            if self._telegram is not None and response.message:
                await self._telegram.send_proactive_message(response.message)
            return {"summary": response.message}
        except Exception as exc:
            logger.error("Queued task '%s' failed: %s", task.get("title"), exc)
            return {"error": str(exc)}
