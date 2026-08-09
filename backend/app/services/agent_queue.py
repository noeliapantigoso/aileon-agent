"""
Queue of one-time agent reminders.

Distinct from the user's Notion tasks: this stores actions the AGENT must run at
a specific moment (e.g. "remind me about X at 6pm").

Stored in Firestore so it survives Cloud Run restarts. The hourly tick picks up
anything whose run_at is already due.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class AgentTaskQueue:
    """Firestore-backed queue for one-time agent-scheduled reminders."""

    def __init__(self, db: Any, collection_prefix: str = "assistant") -> None:
        self._db = db
        self._prefix = collection_prefix

    def _collection(self):
        return self._db.collection(f"{self._prefix}_agent_queue")

    # ── Write ────────────────────────────────────────────────────────────────

    def create_task(self, title: str, prompt: str, run_at: str) -> str:
        """
        Schedule a one-time agent reminder.

        Args:
            title:   Short human-readable label (shown in list_agent_tasks).
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
        logger.info("Agent task queued: '%s' at %s (id=%s)", title, run_at, doc_ref.id)
        return doc_ref.id

    def cancel_task(self, task_id: str) -> bool:
        """Mark a task as cancelled. Returns True if found."""
        if self._db is None:
            return False
        doc_ref = self._collection().document(task_id)
        if not doc_ref.get().exists:
            return False
        doc_ref.update({"status": "cancelled"})
        logger.info("Agent task cancelled: %s", task_id)
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
        """
        Pending reminders whose run_at has already passed.

        Filters by date in Python rather than adding `.where("run_at", "<=", ...)`.
        That inequality alongside the `status` equality requires a composite
        Firestore index created by hand; without it the query raised
        "400 The query requires an index" on every tick and the queue never ran.
        """
        if self._db is None:
            return []
        now_iso = now.isoformat()
        return [
            t for t in self.list_tasks(status="pending")
            if (t.get("run_at") or "9999") <= now_iso
        ]
