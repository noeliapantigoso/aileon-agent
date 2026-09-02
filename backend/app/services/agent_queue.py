"""
Queue of one-time agent reminders.

Distinct from the user's Notion tasks: this stores actions the AGENT must run at
a specific moment (e.g. "remind me about X at 6pm"). The hourly tick picks up
anything whose run_at is already due.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class AgentTaskQueue:
    """One-time agent-scheduled reminders, stored in the agent's database."""

    def __init__(
        self,
        db: Any = None,
        collection_prefix: str = "assistant",
        database: Any = None,
        user_id: str = "user",
    ) -> None:
        self.sql = database
        self._user_id = user_id

    # ── Write ────────────────────────────────────────────────────────────────

    def create_task(self, title: str, prompt: str, run_at: str) -> str:
        """
        Schedule a one-time agent reminder.

        Args:
            title:   Short human-readable label (shown in list_agent_tasks).
            prompt:  What to do when the task fires — sent to the agent as a message.
            run_at:  ISO datetime with tz offset (e.g. 2026-09-01T09:00:00-05:00).

        Returns:
            The task id.
        """
        if self.sql is None:
            raise RuntimeError("Database not available")
        task_id = uuid.uuid4().hex[:20]
        self.sql.insert("agent_queue", {
            "id": task_id,
            "user_id": self._user_id,
            "title": title,
            "prompt": prompt,
            "run_at": run_at,
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.info("Agent task queued: '%s' at %s (id=%s)", title, run_at, task_id)
        return task_id

    def cancel_task(self, task_id: str) -> bool:
        """Mark a task as cancelled. Returns True if one was found."""
        if self.sql is None:
            return False
        changed = self.sql.execute(
            "UPDATE agent_queue SET status = 'cancelled' WHERE id = ? AND user_id = ?",
            (task_id, self._user_id),
        )
        if changed:
            logger.info("Agent task cancelled: %s", task_id)
        return bool(changed)

    def mark_done(self, task_id: str) -> None:
        """Mark a task as done after execution."""
        if self.sql is None:
            return
        self.sql.execute(
            "UPDATE agent_queue SET status = 'done', completed_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), task_id),
        )

    # ── Read ─────────────────────────────────────────────────────────────────

    def list_tasks(self, status: str = "pending") -> list[dict[str, Any]]:
        """Return tasks filtered by status."""
        if self.sql is None:
            return []
        try:
            return self.sql.query(
                "SELECT * FROM agent_queue WHERE user_id = ? AND status = ? "
                "ORDER BY run_at",
                (self._user_id, status),
            )
        except Exception as exc:
            logger.warning("list_tasks failed: %s", exc)
            return []

    def get_due_tasks(self, now: datetime) -> list[dict[str, Any]]:
        """
        Pending reminders whose run_at has already passed.

        This asks for the status equality and the run_at inequality in one
        query, which Firestore refused without a composite index created by
        hand — the missing index raised "400 The query requires an index" on
        every tick and the queue never ran at all. SQLite answers it off
        idx_agent_queue_due.
        """
        if self.sql is None:
            return []
        try:
            return self.sql.query(
                "SELECT * FROM agent_queue "
                "WHERE user_id = ? AND status = 'pending' AND run_at <= ? "
                "ORDER BY run_at",
                (self._user_id, now.isoformat()),
            )
        except Exception as exc:
            logger.warning("get_due_tasks failed: %s", exc)
            return []
