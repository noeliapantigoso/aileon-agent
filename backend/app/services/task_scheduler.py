"""
Cola de tareas agendadas para una sola vez (one-time scheduled tasks).

Almacena en Firestore para sobrevivir reinicios de Cloud Run.
El tick las levanta cuando run_at <= ahora.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class TaskScheduler:
    """Firestore-backed queue for one-time scheduled tasks."""

    def __init__(self, db: Any, collection_prefix: str = "assistant") -> None:
        self._db = db
        self._prefix = collection_prefix

    def _collection(self):
        return self._db.collection(f"{self._prefix}_scheduled_tasks")

    # ── Write ────────────────────────────────────────────────────────────────

    def create_task(self, title: str, prompt: str, run_at: str) -> str:
        """
        Schedule a one-time task.

        Args:
            title:   Short human-readable label (shown in list_scheduled_tasks).
            prompt:  What to do when the task fires — sent to the agent as a message.
            run_at:  ISO datetime with tz offset (e.g. 2026-09-01T09:00:00-05:00).

        Returns:
            Firestore document ID (task_id).
        """
        if self._db is None:
            raise RuntimeError("Firestore not available")
        doc_ref = self._collection().document()
        now = datetime.now(timezone.utc).isoformat()
        doc_ref.set({
            "title": title,
            "prompt": prompt,
            "run_at": run_at,
            "status": "pending",
            "created_at": now,
        })
        logger.info("Scheduled task created: '%s' at %s (id=%s)", title, run_at, doc_ref.id)
        return doc_ref.id

    def cancel_task(self, task_id: str) -> bool:
        """Mark a task as cancelled. Returns True if found."""
        if self._db is None:
            return False
        doc_ref = self._collection().document(task_id)
        if not doc_ref.get().exists:
            return False
        doc_ref.update({"status": "cancelled"})
        logger.info("Scheduled task cancelled: %s", task_id)
        return True

    def mark_done(self, task_id: str) -> None:
        """Mark a task as done after execution."""
        if self._db is None:
            return
        self._collection().document(task_id).update({
            "status": "done",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })

    # ── Read ─────────────────────────────────────────────────────────────────

    def list_tasks(self, status: str = "pending") -> list[dict[str, Any]]:
        """Return tasks filtered by status."""
        if self._db is None:
            return []
        try:
            docs = self._collection().where("status", "==", status).stream()
            return [{"id": doc.id, **doc.to_dict()} for doc in docs]
        except Exception as exc:
            logger.warning("list_tasks failed: %s", exc)
            return []

    def get_due_tasks(self, now: datetime) -> list[dict[str, Any]]:
        """Return pending tasks whose run_at is <= now."""
        if self._db is None:
            return []
        try:
            now_iso = now.isoformat()
            docs = (
                self._collection()
                .where("status", "==", "pending")
                .where("run_at", "<=", now_iso)
                .stream()
            )
            return [{"id": doc.id, **doc.to_dict()} for doc in docs]
        except Exception as exc:
            logger.warning("get_due_tasks failed: %s", exc)
            return []
