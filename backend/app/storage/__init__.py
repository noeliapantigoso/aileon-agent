"""
Storage backends.

The services talk to a document store through a small, Firestore-shaped API:

    db.collection("things").document("id").get()
    db.collection("things").where("user_id", "==", uid).stream()

Two backends implement it. SQLite is the default and needs no account, no
project and no network — a single file on disk. Firestore is opt-in for
deployments that already live on Google Cloud.

Nothing above this package knows which one is in use.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class Query:
    """
    Sort-direction constants.

    Deliberately the same strings google-cloud-firestore uses, so a service can
    import this one name and pass `direction=Query.DESCENDING` to either backend.
    """

    ASCENDING = "ASCENDING"
    DESCENDING = "DESCENDING"


def get_store(settings: Any) -> Optional[Any]:
    """
    Build the document store described by the settings.

    `storage_backend` picks the implementation:
      - "sqlite"    -> a local file, the default
      - "firestore" -> Google Cloud Firestore
      - "auto"      -> Firestore when a GCP project is configured, else SQLite

    Returns None only when the chosen backend cannot be reached, which the
    callers already treat as "run without persistence".
    """
    backend = (getattr(settings, "storage_backend", "") or "auto").lower()

    if backend == "auto":
        backend = "firestore" if getattr(settings, "gcp_project_id", "") else "sqlite"

    if backend == "firestore":
        return _build_firestore(settings)

    if backend == "sqlite":
        return _build_sqlite(settings)

    logger.error("Unknown storage_backend %r, falling back to sqlite", backend)
    return _build_sqlite(settings)


def _build_sqlite(settings: Any):
    from app.storage.sqlite_store import SqliteStore

    path = getattr(settings, "sqlite_path", "") or "data/aileon.db"
    store = SqliteStore(path)
    logger.info("Storage backend: sqlite (%s)", path)
    return store


def _build_firestore(settings: Any):
    project = getattr(settings, "gcp_project_id", "")
    if not project:
        logger.warning(
            "storage_backend=firestore but no gcp_project_id set — using sqlite"
        )
        return _build_sqlite(settings)
    try:
        from google.cloud import firestore

        client = firestore.Client(project=project)
        logger.info("Storage backend: firestore (project=%s)", project)
        return client
    except Exception as exc:
        logger.error("Firestore unavailable (%s) — falling back to sqlite", exc)
        return _build_sqlite(settings)


__all__ = ["Query", "get_store"]
