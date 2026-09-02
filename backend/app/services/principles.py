"""
Distilled principles knowledge base.

Loads principles from Firestore and hands them all to the model, which picks the
ones that genuinely apply to the current message. The `applies_when` field is a
hint for the model, not a filter.
"""

from __future__ import annotations

import logging
import time
import unicodedata
from typing import Any

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 60 * 60  # 1 hour — principles almost never change


def _normalize(text: str) -> str:
    """Lowercase and strip accents, for robust matching."""
    text = text.lower()
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


class PrincipleService:
    """Loads and caches the principles stored in Firestore."""

    def __init__(
        self,
        collection_prefix: str = "assistant",
        database: Any = None,
    ) -> None:
        self.sql = database
        self._prefix = collection_prefix
        self._cache: list[dict[str, Any]] = []
        self._cache_ts: float = 0.0

    def _load_principles(self) -> list[dict[str, Any]]:
        """Load every principle (cached)."""
        now = time.time()
        if self._cache and (now - self._cache_ts) < CACHE_TTL_SECONDS:
            return self._cache

        if self.sql is None:
            return []

        try:
            # Shared knowledge, the same for every deployment, so there is no
            # user to scope by.
            self._cache = self.sql.query("SELECT * FROM principles")
            self._cache_ts = now
            logger.info("Loaded %d principles", len(self._cache))
        except Exception as exc:
            logger.error("Failed to load principles: %s", exc)
            return []

        return self._cache

    def get_all(self) -> list[dict[str, Any]]:
        """Return every principle. The model decides which ones to use."""
        return self._load_principles()

    def invalidate_cache(self) -> None:
        """Clear the cache. Useful after a reseed."""
        self._cache = []
        self._cache_ts = 0.0
