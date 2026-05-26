"""
Sistema de memoria de 4 capas para el agente de productividad.

Capa 1 — Corto plazo: últimos mensajes de la conversación actual.
Capa 2 — Contexto de trabajo: agenda de hoy, tareas pendientes (cache 5 min).
Capa 3 — Perfil del usuario: documento en Firestore (largo plazo).
Capa 4 — Memoria episódica: hechos extraídos con Mem0 (búsqueda semántica).
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

SESSION_TIMEOUT_SECONDS = 30 * 60  # 30 minutos de inactividad → nueva sesión
WORK_CONTEXT_TTL_SECONDS = 5 * 60  # Cache de contexto de trabajo: 5 minutos
MAX_CONVERSATION_MESSAGES = 15
MAX_RELEVANT_MEMORIES = 5


class MemoryManager:
    """Gestiona las 4 capas de memoria del agente."""

    def __init__(
        self,
        firestore_client: Any,
        mem0_client: Any,
        notion_service: Any,
        collection_prefix: str = "assistant",
        user_id: str = "noe",
        principle_service: Any = None,
        tagging_service: Any = None,
        insight_service: Any = None,
    ) -> None:
        self.db = firestore_client
        self.mem0 = mem0_client
        self.notion = notion_service
        self.principles = principle_service
        self.tagger = tagging_service
        self.insights = insight_service
        self._prefix = collection_prefix
        self._user_id = user_id

        # Capa 1: buffer de conversación en memoria
        self.conversation_buffer: list[dict[str, str]] = []
        self._last_activity: float = time.time()

        # Cache de contexto de trabajo (capa 2)
        self._work_context_cache: Optional[dict] = None
        self._work_context_ts: float = 0.0

    # ── API Principal ────────────────────────────────────────────────────────

    async def get_full_context(self, user_message: str) -> dict[str, Any]:
        """
        Carga todo el contexto necesario para el prompt_builder.

        Returns:
            {
                "user_profile": dict,
                "today_context": dict,
                "relevant_memories": list[str],
                "conversation_history": list[dict],
            }
        """
        self._check_session_timeout()

        # Si el buffer está vacío (cold start), recuperar últimos mensajes de Firestore
        if not self.conversation_buffer:
            await self._restore_buffer_from_firestore()

        # Cargar en paralelo lo que se pueda
        profile_task = self._get_user_profile()
        context_task = self._get_work_context()
        memories_task = self._search_memories(user_message)

        user_profile, today_context, relevant_memories = await asyncio.gather(
            profile_task, context_task, memories_task
        )

        # Principios — todos. Gemini decide cuáles usar.
        relevant_principles: list[dict[str, Any]] = []
        if self.principles is not None:
            try:
                relevant_principles = self.principles.get_all()
            except Exception as exc:
                logger.warning("Failed to load principles: %s", exc)

        # Insights activos (patrones detectados en análisis semanales previos)
        active_insights: list[dict[str, Any]] = []
        if self.insights is not None:
            try:
                active_insights = self.insights.get_active_insights(limit=5)
            except Exception as exc:
                logger.warning("Failed to load insights: %s", exc)

        return {
            "user_profile": user_profile,
            "today_context": today_context,
            "relevant_memories": relevant_memories,
            "conversation_history": list(self.conversation_buffer),
            "relevant_principles": relevant_principles,
            "active_insights": active_insights,
        }

    async def _restore_buffer_from_firestore(self) -> None:
        """Carga las últimas interacciones de Firestore al buffer en memoria."""
        if self.db is None:
            return
        try:
            from google.cloud.firestore import Query
            collection = self.db.collection(f"{self._prefix}_history")
            docs = (
                collection
                .where("user_id", "==", self._user_id)
                .order_by("timestamp", direction=Query.DESCENDING)
                .limit(MAX_CONVERSATION_MESSAGES)
                .stream()
            )
            interactions = [doc.to_dict() for doc in docs]
            interactions.reverse()  # cronológico ascendente

            for it in interactions:
                ts = it.get("timestamp", "")
                self.conversation_buffer.append(
                    {"role": "user", "content": it.get("user_message", ""), "timestamp": ts}
                )
                self.conversation_buffer.append({
                    "role": "assistant",
                    "content": it.get("agent_response", ""),
                    "actions": it.get("actions", []),
                    "timestamp": ts,
                    "proactive": it.get("proactive", False),
                })
            if interactions:
                logger.info("Restored %d interactions from Firestore", len(interactions))
        except Exception as exc:
            logger.warning("Failed to restore conversation buffer: %s", exc)

    async def save_interaction(
        self,
        user_message: str,
        agent_response: str,
        actions_taken: list[dict[str, Any]] | None = None,
    ) -> None:
        """Guarda la interacción en todas las capas relevantes."""
        self._last_activity = time.time()
        actions = actions_taken or []

        # Capa 1: agregar al buffer de conversación
        now_iso = datetime.now(timezone.utc).isoformat()
        self.conversation_buffer.append({"role": "user", "content": user_message, "timestamp": now_iso})
        self.conversation_buffer.append({
            "role": "assistant",
            "content": agent_response,
            "actions": actions,
            "timestamp": now_iso,
        })

        # Mantener solo los últimos N mensajes
        if len(self.conversation_buffer) > MAX_CONVERSATION_MESSAGES * 2:
            self.conversation_buffer = self.conversation_buffer[
                -(MAX_CONVERSATION_MESSAGES * 2):
            ]

        # Guardar en Firestore (historial persistente) y Mem0 (async)
        asyncio.create_task(
            self._persist_interaction(user_message, agent_response, actions)
        )

    async def save_proactive_message(self, message: str) -> None:
        """Guarda un mensaje proactivo enviado al usuario (sin respuesta del usuario)."""
        now_iso = datetime.now(timezone.utc).isoformat()

        # Agregar al buffer como mensaje del asistente sin turno de usuario
        self.conversation_buffer.append({
            "role": "assistant",
            "content": message,
            "actions": [],
            "timestamp": now_iso,
            "proactive": True,
        })

        if len(self.conversation_buffer) > MAX_CONVERSATION_MESSAGES * 2:
            self.conversation_buffer = self.conversation_buffer[-(MAX_CONVERSATION_MESSAGES * 2):]

        # Persistir en Firestore
        if self.db is not None:
            try:
                self.db.collection(f"{self._prefix}_history").add({
                    "user_id": self._user_id,
                    "user_message": "",
                    "agent_response": message,
                    "actions": [],
                    "timestamp": now_iso,
                    "proactive": True,
                })
            except Exception as exc:
                logger.warning("Failed to persist proactive message: %s", exc)

    async def update_user_profile(self, updates: dict[str, Any]) -> None:
        """Actualiza campos del perfil del usuario en Firestore."""
        if self.db is None:
            logger.warning("Firestore not available, skipping profile update")
            return

        try:
            doc_ref = self.db.collection(f"{self._prefix}_users").document(
                self._user_id
            )
            doc_ref.set(updates, merge=True)
            logger.info("User profile updated: %s", list(updates.keys()))
        except Exception as exc:
            logger.error("Failed to update user profile: %s", exc)

    def invalidate_work_context_cache(self) -> None:
        """Invalida el cache de contexto de trabajo (tras crear/actualizar tareas)."""
        self._work_context_cache = None
        self._work_context_ts = 0.0

    # ── Capas Internas ───────────────────────────────────────────────────────

    async def _get_user_profile(self) -> dict[str, Any]:
        """Capa 3: carga el perfil del usuario desde Firestore."""
        if self.db is None:
            return _default_profile()

        try:
            doc_ref = self.db.collection(f"{self._prefix}_users").document(
                self._user_id
            )
            doc = doc_ref.get()
            if doc.exists:
                return doc.to_dict()
        except Exception as exc:
            logger.error("Failed to load user profile: %s", exc)

        return _default_profile()

    async def _get_work_context(self) -> dict[str, Any]:
        """Capa 2: contexto de trabajo del día (con cache TTL)."""
        now = time.time()
        if (
            self._work_context_cache is not None
            and (now - self._work_context_ts) < WORK_CONTEXT_TTL_SECONDS
        ):
            return self._work_context_cache

        context: dict[str, Any] = {"date": date.today().isoformat()}

        try:
            # Tareas de hoy
            today_str = date.today().isoformat()
            tasks = await self.notion.get_tasks(date=today_str)
            context["today_tasks"] = tasks

            # Tareas pendientes generales
            pending = await self.notion.get_tasks(status="pending", limit=10)
            context["pending_tasks"] = pending

            # Agenda del día
            agenda = await self.notion.get_daily_agenda(today_str)
            context["agenda"] = agenda

            # Metas activas (todos los tipos)
            try:
                active_goals = await self.notion.get_goals(status="active")
                context["active_goals"] = active_goals
            except Exception as gexc:
                logger.warning("Failed to load active goals: %s", gexc)
                context["active_goals"] = []

        except Exception as exc:
            logger.warning("Failed to load work context from Notion: %s", exc)
            context["error"] = str(exc)

        self._work_context_cache = context
        self._work_context_ts = now
        return context

    async def _search_memories(self, query: str) -> list[str]:
        """Capa 4: busca memorias episódicas relevantes en Mem0."""
        if self.mem0 is None:
            return []

        try:
            results = self.mem0.search(
                query=query,
                filters={"user_id": self._user_id},
                limit=MAX_RELEVANT_MEMORIES,
                version="v2",
            )
            memories = []
            if isinstance(results, dict) and "results" in results:
                for item in results["results"]:
                    memory_text = item.get("memory", "")
                    if memory_text:
                        memories.append(memory_text)
            elif isinstance(results, list):
                for item in results:
                    memory_text = (
                        item.get("memory", "") if isinstance(item, dict) else str(item)
                    )
                    if memory_text:
                        memories.append(memory_text)
            return memories[:MAX_RELEVANT_MEMORIES]
        except Exception as exc:
            logger.warning("Mem0 search failed: %s", exc)
            return []

    async def _persist_interaction(
        self,
        user_message: str,
        agent_response: str,
        actions: list[dict[str, Any]] | None = None,
    ) -> None:
        """Guarda interacción en Firestore y Mem0 (background)."""
        # Tagging vía Gemini Flash (no bloquea respuesta — ya estamos en background)
        tags: dict[str, Any] = {}
        if self.tagger is not None:
            try:
                tags = await self.tagger.tag(user_message, agent_response)
            except Exception as exc:
                logger.warning("Tagging failed: %s", exc)

        # Firestore: historial con tags
        if self.db is not None:
            try:
                collection = self.db.collection(f"{self._prefix}_history")
                doc = {
                    "user_id": self._user_id,
                    "user_message": user_message,
                    "agent_response": agent_response,
                    "actions": actions or [],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "word_count": len(user_message.split()),
                }
                doc.update(tags)
                collection.add(doc)
            except Exception as exc:
                logger.error("Failed to persist to Firestore: %s", exc)

        # Mem0: extracción de hechos
        if self.mem0 is not None:
            try:
                conversation = [
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": agent_response},
                ]
                self.mem0.add(
                    messages=conversation,
                    user_id=self._user_id,
                )
            except Exception as exc:
                logger.warning("Mem0 add failed: %s", exc)

    def _check_session_timeout(self) -> None:
        """Limpia el buffer de conversación si pasó mucho tiempo sin actividad."""
        now = time.time()
        if (now - self._last_activity) > SESSION_TIMEOUT_SECONDS:
            logger.info("Session timeout, clearing conversation buffer")
            self.conversation_buffer.clear()
        self._last_activity = now


def _default_profile() -> dict[str, Any]:
    """Perfil por defecto cuando Firestore no está disponible."""
    return {
        "name": "Noe",
        "occupation": "Tech Consultant / Startup Co-founder",
        "timezone": "America/Lima",
        "language": "es",
        "productivity": {
            "peak_hours": "9:00-12:00",
            "secondary_peak": "15:00-17:00",
            "low_energy_hours": "13:00-15:00",
            "work_start": "8:30",
            "work_end": "18:00",
            "preferred_focus_block_minutes": 90,
            "break_reminder_minutes": 25,
        },
        "preferences": {
            "communication_style": "Directo y conciso. Tutéame.",
            "task_defaults": {
                "priority": "p2",
                "time_estimate_minutes": 30,
            },
        },
    }
