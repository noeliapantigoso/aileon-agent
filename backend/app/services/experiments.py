"""
Servicio de experimentos: trackea cosas que el usuario está probando.

Cada experimento tiene cadencia de check-in, duración, historial de progreso.
El bot luego (via proactive scheduler) revisa cuáles vencieron y manda mensaje.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ExperimentService:
    """CRUD + checks de experimentos en Firestore."""

    def __init__(
        self,
        firestore_client: Any,
        collection_prefix: str = "assistant",
        user_id: str = "noe",
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
        """Crea un experimento nuevo."""
        col = self._collection()
        if col is None:
            return {"error": "Firestore no disponible"}

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
        """Agrega entrada al historial del experimento."""
        col = self._collection()
        if col is None:
            return {"error": "Firestore no disponible"}

        doc_ref = col.document(experiment_id)
        doc = doc_ref.get()
        if not doc.exists:
            return {"error": f"Experimento {experiment_id} no existe"}

        exp = doc.to_dict()
        history = exp.get("history", [])
        history.append({
            "date": date.today().isoformat(),
            "note": note,
            "did_it": did_it,
        })

        # Avanzar next_check_in
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
        """Cierra un experimento con un outcome final."""
        col = self._collection()
        if col is None:
            return {"error": "Firestore no disponible"}

        doc_ref = col.document(experiment_id)
        doc = doc_ref.get()
        if not doc.exists:
            return {"error": f"Experimento {experiment_id} no existe"}

        doc_ref.update({
            "status": status,
            "outcome": outcome,
            "closed_at": datetime.now(timezone.utc).isoformat(),
        })

        return {"id": experiment_id, "status": status, "outcome": outcome}

    def list_active(self) -> list[dict[str, Any]]:
        """Devuelve experimentos activos del usuario."""
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

    def get_pending_check_ins(self, on_date: Optional[date] = None) -> list[dict[str, Any]]:
        """Experimentos activos cuyo next_check_in <= on_date."""
        col = self._collection()
        if col is None:
            return []

        today = (on_date or date.today()).isoformat()

        try:
            docs = (
                col
                .where("user_id", "==", self._user_id)
                .where("status", "==", "active")
                .where("next_check_in", "<=", today)
                .stream()
            )
            results = []
            for doc in docs:
                d = doc.to_dict()
                d["id"] = doc.id
                results.append(d)
            return results
        except Exception as exc:
            logger.error("get_pending_check_ins failed: %s", exc)
            return []
