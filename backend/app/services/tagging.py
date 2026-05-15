"""
Tagging service: anota cada interacción con metadata útil para análisis posterior.

Usa Gemini Flash (rápido y barato) para extraer:
- sentiment: tono emocional del mensaje del usuario
- topics: temas tratados
- energy_level: nivel de energía/compromiso
- hedging_score: cuánto duda/se compromete (0=firme, 1=muy dudoso)
- is_action_commitment: si comprometió a hacer algo concreto

Estos tags se guardan en assistant_history para detectar patrones después.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

TAGGING_PROMPT = """Analiza este mensaje del usuario y la respuesta del asistente. \
Devuelve SOLO un JSON válido sin markdown, sin explicaciones, con esta estructura exacta:

{
  "sentiment": "negative" | "neutral" | "positive" | "anxious" | "motivated" | "frustrated" | "reflective",
  "topics": ["array", "de", "topics", "principales"],
  "energy_level": "high" | "medium" | "low",
  "hedging_score": 0.0,
  "is_action_commitment": false,
  "themes_mentioned": ["trabajo", "salud", "relaciones", "metas", "habitos", "emocional", "otro"]
}

Reglas:
- sentiment: tono dominante del MENSAJE DEL USUARIO
- topics: 1-4 temas concretos en español, lowercase, ej ["estudiar aws", "procrastinacion"]
- energy_level: nivel energético percibido del usuario
- hedging_score: 0.0 = firme/decidido, 1.0 = muy dudoso. Cuenta "tal vez", "quizás", "intentaré"
- is_action_commitment: true si usuario se compromete a hacer algo concreto
- themes_mentioned: subset del array dado, los que apliquen

Mensaje usuario: {user_msg}
Respuesta asistente: {agent_msg}

JSON:"""


class TaggingService:
    """Tag de cada interacción con metadata vía Gemini Flash."""

    def __init__(self, genai_client: Any, model_id: str = "gemini-2.5-flash") -> None:
        self._client = genai_client
        self._model_id = model_id

    async def tag(self, user_message: str, agent_response: str) -> dict[str, Any]:
        """Tag una interacción. Devuelve dict con metadata."""
        if self._client is None:
            return {}

        prompt = TAGGING_PROMPT.format(
            user_msg=user_message[:1500],
            agent_msg=agent_response[:1000],
        )

        try:
            response = self._client.models.generate_content(
                model=self._model_id,
                contents=prompt,
            )
            text = response.text or ""
            tags = _parse_json_loose(text)
            return _validate_tags(tags)
        except Exception as exc:
            logger.warning("Tagging failed: %s", exc)
            return {}


def _parse_json_loose(text: str) -> dict[str, Any]:
    """Parsea JSON aunque venga con markdown code fences."""
    cleaned = text.strip()
    # Quitar markdown fences si están
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    # Encontrar primer { y último }
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        return json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError:
        return {}


def _validate_tags(tags: dict[str, Any]) -> dict[str, Any]:
    """Valida y limpia tags. Devuelve dict seguro para Firestore."""
    if not isinstance(tags, dict):
        return {}

    out: dict[str, Any] = {}

    sentiment = tags.get("sentiment", "")
    if sentiment in {"negative", "neutral", "positive", "anxious", "motivated", "frustrated", "reflective"}:
        out["sentiment"] = sentiment

    topics = tags.get("topics", [])
    if isinstance(topics, list):
        out["topics"] = [str(t)[:50] for t in topics[:5] if t]

    energy = tags.get("energy_level", "")
    if energy in {"high", "medium", "low"}:
        out["energy_level"] = energy

    hedging = tags.get("hedging_score", None)
    if isinstance(hedging, (int, float)):
        out["hedging_score"] = max(0.0, min(1.0, float(hedging)))

    commit = tags.get("is_action_commitment", None)
    if isinstance(commit, bool):
        out["is_action_commitment"] = commit

    themes = tags.get("themes_mentioned", [])
    if isinstance(themes, list):
        allowed = {"trabajo", "salud", "relaciones", "metas", "habitos", "emocional", "otro"}
        out["themes_mentioned"] = [t for t in themes if t in allowed]

    return out
