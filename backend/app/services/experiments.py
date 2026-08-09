"""
Experiment service: tracks things the user is testing.

Each experiment has a check-in cadence, a duration and a progress history. The
proactive cycle surfaces the ones whose check-in is overdue.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ExperimentService:
    """CRUD and due-date checks for experiments in Firestore."""

    def __init__(
        self,
        firestore_client: Any,
        collection_prefix: str = "assistant",
        user_id: str = "user",
    ) -> None:
        self.db = firestore_client
        self._prefix = collection_prefix
        self._user_id = user_id

    def _collection(self):
        if self.db is None:
            return None
        return self.db.collection(f"{self._prefix}_experiments")

    # ── CRUD ────────────────────────────────────────────────────────────────

    def start_experiment(
        self,
        name: str,
        hypothesis: str,
        duration_days: int = 7,
        check_in_every_days: int = 3,
    ) -> dict[str, Any]:
        """Create a new experiment."""
        col = self._collection()
        if col is None:
            return {"error": "Firestore unavailable"}

        today = date.today()
        next_ci = today + timedelta(days=check_in_every_days)
        ends = today + timedelta(days=duration_days)

        data = {
            "user_id": self._user_id,
            "name": name,
            "hypothesis": hypothesis,
            "duration_days": duration_days,
            "check_in_every_days": check_in_every_days,
            "started": today.isoformat(),
            "ends": ends.isoformat(),
            "next_check_in": next_ci.isoformat(),
            "status": "active",
            "history": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        _, doc_ref = col.add(data)
        data["id"] = doc_ref.id
        logger.info("Experiment created: %s (id=%s)", name, doc_ref.id)
        return data

    def log_progress(
        self,
        experiment_id: str,
        note: str,
        did_it: bool = True,
    ) -> dict[str, Any]:
        """Append an entry to the experiment history."""
        col = self._collection()
        if col is None:
            return {"error": "Firestore unavailable"}

        doc_ref = col.document(experiment_id)
        doc = doc_ref.get()
        if not doc.exists:
            return {"error": f"Experiment {experiment_id} does not exist"}

        exp = doc.to_dict()
        history = exp.get("history", [])
        history.append({
            "date": date.today().isoformat(),
            "note": note,
            "did_it": did_it,
        })

        # Push next_check_in forward
        next_ci = (
            date.today() + timedelta(days=exp.get("check_in_every_days", 3))
        ).isoformat()

        doc_ref.update({
            "history": history,
            "next_check_in": next_ci,
        })

        return {
            "id": experiment_id,
            "name": exp.get("name"),
            "history_entries": len(history),
            "next_check_in": next_ci,
        }

    def close_experiment(
        self,
        experiment_id: str,
        outcome: str,
        status: str = "completed",
    ) -> dict[str, Any]:
        """Close an experiment with a final outcome."""
        col = self._collection()
        if col is None:
            return {"error": "Firestore unavailable"}

        doc_ref = col.document(experiment_id)
        doc = doc_ref.get()
        if not doc.exists:
            return {"error": f"Experiment {experiment_id} does not exist"}

        doc_ref.update({
            "status": status,
            "outcome": outcome,
            "closed_at": datetime.now(timezone.utc).isoformat(),
        })

        return {"id": experiment_id, "status": status, "outcome": outcome}

    def list_active(self) -> list[dict[str, Any]]:
        """Return the user's active experiments."""
        col = self._collection()
        if col is None:
            return []

        try:
            docs = (
                col
                .where("user_id", "==", self._user_id)
                .where("status", "==", "active")
                .stream()
            )
            results = []
            for doc in docs:
                d = doc.to_dict()
                d["id"] = doc.id
                results.append(d)
            return results
        except Exception as exc:
            logger.error("list_active failed: %s", exc)
            return []

    def days_without_experiment(self) -> Optional[int]:
        """
        Days since the last experiment was closed.

        None when there has never been one, or when one is active right now. Lets
        the pressure to propose a new one grow with time instead of depending on
        the agent remembering.
        """
        col = self._collection()
        if col is None:
            return None
        try:
            docs = col.where("user_id", "==", self._user_id).stream()
            closes = []
            for doc in docs:
                d = doc.to_dict() or {}
                if d.get("status") == "active":
                    return None  # one is running
                if d.get("closed_at"):
                    closes.append(d["closed_at"][:10])
            if not closes:
                return None
            last = date.fromisoformat(max(closes))
            return (date.today() - last).days
        except Exception as exc:
            logger.warning("days_without_experiment failed: %s", exc)
            return None

    def get_pending_check_ins(self, on_date: Optional[date] = None) -> list[dict[str, Any]]:
        """
        Active experiments whose next_check_in is already due.

        Filters in Python rather than adding `.where("next_check_in", "<=", ...)`:
        that inequality on top of two equalities requires a composite Firestore
        index, which has to be created by hand and fails at runtime when missing.
        With a single user the active list is only a few documents.
        """
        today = (on_date or date.today()).isoformat()
        return [
            exp for exp in self.list_active()
            if (exp.get("next_check_in") or "9999-12-31") <= today
        ]
