"""
Tagging service: annotates each interaction with metadata for later analysis.

Uses Gemini Flash (fast and cheap) to extract:
- sentiment: emotional tone of the user's message
- topics: what was discussed
- energy_level: perceived energy / engagement
- hedging_score: how much they hedge (0 = firm, 1 = very tentative)
- is_action_commitment: whether they committed to something concrete

These tags are stored alongside the history so the weekly analysis can find
patterns in HOW the user talks, not just what they ask for.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# NOTE: the braces in the JSON example are doubled ({{ }}) because this template
# is built with .format(). With single braces Python reads them as fields and
# raises KeyError before ever reaching Gemini — which killed tagging entirely.
TAGGING_PROMPT = """Analyse this user message and the assistant's reply. Return \
ONLY valid JSON, no markdown and no explanation, with exactly this shape:

{{
  "sentiment": "negative" | "neutral" | "positive" | "anxious" | "motivated" | "frustrated" | "reflective",
  "topics": ["array", "de", "topics", "principales"],
  "energy_level": "high" | "medium" | "low",
  "hedging_score": 0.0,
  "is_action_commitment": false,
  "themes_mentioned": ["trabajo", "salud", "relaciones", "metas", "habitos", "emocional", "otro"]
}}

Rules:
- sentiment: the dominant tone of the USER MESSAGE
- topics: 1-4 concrete topics, lowercase, in the user's own language
- energy_level: the user's perceived energy
- hedging_score: 0.0 = firm and decided, 1.0 = very tentative. Count hedges like
  "maybe", "I guess", "I'll try"
- is_action_commitment: true when they commit to something concrete
- themes_mentioned: a subset of the array given above

User message: {user_msg}
Assistant reply: {agent_msg}

JSON:"""


class TaggingService:
    """Tags each interaction with metadata via Gemini Flash."""

    def __init__(self, genai_client: Any, model_id: str = "gemini-2.5-flash-lite") -> None:
        self._client = genai_client
        self._model_id = model_id

    async def tag(self, user_message: str, agent_response: str) -> dict[str, Any]:
        """Tag one interaction. Returns a metadata dict."""
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
    """Parse JSON even when it arrives wrapped in markdown code fences."""
    cleaned = text.strip()
    # Strip markdown fences if present
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    # Find the first { and the last }
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        return json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError:
        return {}


def _validate_tags(tags: dict[str, Any]) -> dict[str, Any]:
    """Validate and clean the tags. Returns a dict safe to store in Firestore."""
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
