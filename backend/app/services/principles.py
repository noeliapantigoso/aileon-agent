"""
Selector de principios destilados (Jordan Peterson knowledge base).

Carga principios desde Firestore y selecciona los más relevantes para el
mensaje del usuario y su perfil de personalidad usando matching de keywords.
"""

from __future__ import annotations

import logging
import time
import unicodedata
from typing import Any

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 60 * 60  # 1 hora — principios casi nunca cambian


def _normalize(text: str) -> str:
    """Lowercase + sin tildes para matching robusto."""
    text = text.lower()
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


class PrincipleService:
    """Gestiona la carga y selección de principios desde Firestore."""

    def __init__(self, firestore_client: Any, collection_prefix: str = "assistant") -> None:
        self.db = firestore_client
        self._prefix = collection_prefix
        self._cache: list[dict[str, Any]] = []
        self._cache_ts: float = 0.0

    def _load_principles(self) -> list[dict[str, Any]]:
        """Carga todos los principios (cacheado)."""
        now = time.time()
        if self._cache and (now - self._cache_ts) < CACHE_TTL_SECONDS:
            return self._cache

        if self.db is None:
            return []

        try:
            docs = self.db.collection(f"{self._prefix}_principles").stream()
            self._cache = [doc.to_dict() for doc in docs]
            self._cache_ts = now
            logger.info("Loaded %d principles from Firestore", len(self._cache))
        except Exception as exc:
            logger.error("Failed to load principles: %s", exc)
            return []

        return self._cache

    def get_all(self) -> list[dict[str, Any]]:
        """Devuelve todos los principios. Gemini decide cuáles usar."""
        return self._load_principles()

    def _extract_profile_traits(self, profile: dict[str, Any]) -> set[str]:
        """Extrae rasgos del perfil para matching contra personality_relevance."""
        traits: set[str] = set()

        struggles = profile.get("current_struggles", [])
        if isinstance(struggles, list):
            traits.update(struggles)

        # Big 5 — convertir scores a labels si están presentes
        big5 = profile.get("personality", {}).get("big5", {})
        if isinstance(big5, dict):
            for trait_name, score in big5.items():
                if isinstance(score, (int, float)):
                    if score >= 70:
                        traits.add(f"high_{trait_name.lower()}")
                    elif score <= 30:
                        traits.add(f"low_{trait_name.lower()}")

        return traits

    def invalidate_cache(self) -> None:
        """Limpia cache. Útil tras reseed."""
        self._cache = []
        self._cache_ts = 0.0
