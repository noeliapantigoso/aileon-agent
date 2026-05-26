"""
Servicio de mensajes proactivos.

Llamado por Cloud Scheduler cada hora. Revisa triggers y manda mensajes vía Telegram.

Triggers implementados:
- experiment_check_in: experimento con next_check_in <= hoy
- silent_period: sin mensajes del usuario en >3 días
- daily_morning: chequeo matutino (configurable)
- daily_evening: reflexión nocturna (configurable)
- overdue_tasks: tareas con Due Date pasada sin marcar Done

Generación de mensajes: Gemini Flash con prompt contextual.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Umbrales
SILENT_DAYS_THRESHOLD = 3
DAILY_MORNING_HOUR = 8     # local time
DAILY_EVENING_HOUR = 21


class ProactiveService:
    """Detecta triggers y genera/envía mensajes proactivos."""

    def __init__(
        self,
        firestore_client: Any,
        experiment_service: Any,
        notion_service: Any,
        telegram_bot: Any,
        genai_client: Any,
        collection_prefix: str = "assistant",
        user_id: str = "noe",
        model_id: str = "gemini-2.5-flash",
    ) -> None:
        self.db = firestore_client
        self.experiments = experiment_service
        self.notion = notion_service
        self.telegram = telegram_bot
        self.genai = genai_client
        self._prefix = collection_prefix
        self._user_id = user_id
        self._model_id = model_id

    async def run_cycle(self, current_hour_local: int) -> dict[str, Any]:
        """
        Ciclo principal. Llamado por endpoint cron.

        Args:
            current_hour_local: hora actual en timezone del usuario (0-23).

        Returns:
            Dict con triggers detectados y mensajes enviados.
        """
        triggers = await self._collect_triggers(current_hour_local)
        sent: list[dict[str, Any]] = []

        for trigger in triggers:
            try:
                message = await self._generate_message(trigger)
                if message:
                    await self.telegram.send_proactive_message(message)
                    sent.append({"type": trigger["type"], "preview": message[:80]})
                    await self._mark_trigger_handled(trigger)
            except Exception as exc:
                logger.error("Failed proactive for %s: %s", trigger.get("type"), exc)

        return {"triggers_found": len(triggers), "messages_sent": len(sent), "details": sent}

    # ── Trigger detection ───────────────────────────────────────────────────

    async def _collect_triggers(self, hour_local: int) -> list[dict[str, Any]]:
        triggers: list[dict[str, Any]] = []

        # 1. Experiment check-ins
        if self.experiments is not None:
            try:
                pending = self.experiments.get_pending_check_ins()
                for exp in pending:
                    triggers.append({"type": "experiment_check_in", "experiment": exp})
            except Exception as exc:
                logger.warning("Exp check-in detection failed: %s", exc)

        # 2. Silent period (solo una vez al día — chequeo 10am)
        if hour_local == 10:
            silent_days = self._days_since_last_user_message()
            if silent_days >= SILENT_DAYS_THRESHOLD:
                triggers.append({"type": "silent_period", "days": silent_days})

        # 3. Daily morning (8am)
        if hour_local == DAILY_MORNING_HOUR and self._is_config_enabled("daily_morning"):
            if not self._already_sent_today("daily_morning"):
                triggers.append({"type": "daily_morning"})

        # 4. Daily evening (9pm)
        if hour_local == DAILY_EVENING_HOUR and self._is_config_enabled("daily_evening"):
            if not self._already_sent_today("daily_evening"):
                triggers.append({"type": "daily_evening"})

        # 5. Overdue tasks (chequeo 9am)
        if hour_local == 9:
            try:
                overdue = await self._get_overdue_tasks()
                if overdue:
                    triggers.append({"type": "overdue_tasks", "tasks": overdue})
            except Exception as exc:
                logger.warning("Overdue check failed: %s", exc)

        return triggers

    def _days_since_last_user_message(self) -> int:
        """Días desde el último mensaje del usuario."""
        if self.db is None:
            return 0
        try:
            from google.cloud.firestore import Query
            docs = (
                self.db.collection(f"{self._prefix}_history")
                .where("user_id", "==", self._user_id)
                .order_by("timestamp", direction=Query.DESCENDING)
                .limit(1)
                .stream()
            )
            for doc in docs:
                ts_str = doc.to_dict().get("timestamp", "")
                if ts_str:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    delta = datetime.now(timezone.utc) - ts
                    return delta.days
            return 999
        except Exception as exc:
            logger.warning("days_since_last_message failed: %s", exc)
            return 0

    def _is_config_enabled(self, key: str) -> bool:
        """Lee config del perfil del usuario. Default false."""
        if self.db is None:
            return False
        try:
            doc = self.db.collection(f"{self._prefix}_users").document(self._user_id).get()
            if not doc.exists:
                return False
            data = doc.to_dict() or {}
            config = data.get("proactive_messages", {})
            return bool(config.get(key, False))
        except Exception:
            return False

    def _already_sent_today(self, trigger_type: str) -> bool:
        """Evita duplicados en el mismo día."""
        if self.db is None:
            return False
        try:
            today = date.today().isoformat()
            doc_id = f"{self._user_id}_{trigger_type}_{today}"
            doc = self.db.collection(f"{self._prefix}_proactive_log").document(doc_id).get()
            return doc.exists
        except Exception:
            return False

    async def _get_overdue_tasks(self) -> list[dict[str, Any]]:
        """Tareas con Due Date <= ayer y status no Done."""
        try:
            tasks = await self.notion.get_tasks(status="pending", limit=20)
            yesterday = (date.today() - timedelta(days=1)).isoformat()
            overdue = [
                t for t in tasks
                if t.get("due_date") and t.get("due_date") <= yesterday
            ]
            return overdue[:5]
        except Exception as exc:
            logger.warning("get_overdue_tasks failed: %s", exc)
            return []

    # ── Message generation ──────────────────────────────────────────────────

    async def _generate_message(self, trigger: dict[str, Any]) -> str:
        """Genera el texto del mensaje vía Gemini."""
        ttype = trigger["type"]
        context = self._build_trigger_context(trigger)

        prompt = f"""Generate a short Telegram message (1-3 lines) for the user \
of a productivity assistant. Tone: direct, empathetic, minimal emojis, in English.

Trigger type: {ttype}
Context: {context}

Rules:
- NO generic greetings like "Hey!" or "Hello!"
- Go straight to the point of the trigger
- End with an actionable question when applicable
- Markdown ok but minimal

Message:"""

        try:
            response = self.genai.models.generate_content(
                model=self._model_id,
                contents=prompt,
            )
            return (response.text or "").strip()
        except Exception as exc:
            logger.error("Message generation failed: %s", exc)
            return self._fallback_message(trigger)

    def _build_trigger_context(self, trigger: dict[str, Any]) -> str:
        ttype = trigger["type"]
        if ttype == "experiment_check_in":
            exp = trigger["experiment"]
            return (
                f"Experiment '{exp.get('name')}' (hypothesis: {exp.get('hypothesis')}). "
                f"Has {len(exp.get('history', []))} check-ins so far. Time to ask how it's going."
            )
        if ttype == "silent_period":
            return f"User hasn't messaged in {trigger['days']} days. Friendly check-in."
        if ttype == "daily_morning":
            return "Start of day. Ask what they're prioritizing today."
        if ttype == "daily_evening":
            return "End of day. Ask for reflection: what did they accomplish, what's still pending."
        if ttype == "overdue_tasks":
            titles = [t.get("title", "?") for t in trigger["tasks"]]
            return f"Overdue tasks not marked done: {', '.join(titles[:3])}"
        return ""

    def _fallback_message(self, trigger: dict[str, Any]) -> str:
        ttype = trigger["type"]
        if ttype == "experiment_check_in":
            exp = trigger["experiment"]
            return f"Time for a check-in on your *{exp.get('name')}* experiment. How's it going?"
        if ttype == "silent_period":
            return f"We haven't talked in {trigger['days']} days. Everything okay?"
        if ttype == "daily_morning":
            return "Morning. What are you prioritizing today?"
        if ttype == "daily_evening":
            return "How did today go? Let's close out the day."
        if ttype == "overdue_tasks":
            count = len(trigger["tasks"])
            return f"You have {count} overdue tasks. Want to reschedule or close them?"
        return ""

    # ── Post-send bookkeeping ───────────────────────────────────────────────

    async def _mark_trigger_handled(self, trigger: dict[str, Any]) -> None:
        """Registra que el trigger se manejó para evitar duplicados."""
        if self.db is None:
            return
        ttype = trigger["type"]
        try:
            if ttype in {"daily_morning", "daily_evening", "silent_period", "overdue_tasks"}:
                today = date.today().isoformat()
                doc_id = f"{self._user_id}_{ttype}_{today}"
                self.db.collection(f"{self._prefix}_proactive_log").document(doc_id).set({
                    "user_id": self._user_id,
                    "type": ttype,
                    "date": today,
                    "sent_at": datetime.now(timezone.utc).isoformat(),
                })
            elif ttype == "experiment_check_in":
                # Avanzar next_check_in del experimento
                exp = trigger["experiment"]
                next_ci = (
                    date.today() + timedelta(days=exp.get("check_in_every_days", 3))
                ).isoformat()
                self.db.collection(f"{self._prefix}_experiments").document(
                    exp["id"]
                ).update({"next_check_in": next_ci})
        except Exception as exc:
            logger.warning("mark_trigger_handled failed: %s", exc)
