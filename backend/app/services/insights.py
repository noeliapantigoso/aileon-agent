"""
Servicio de análisis semanal.

Llamado por Cloud Scheduler (domingo 8pm). Agrega últimos 7 días de assistant_history,
calcula estadísticas, le pide a Gemini que extraiga 2-3 patrones nuevos,
guarda los insights en Firestore y manda resumen al usuario.

Los insights se inyectan en el system prompt en cada conversación para que
el agente los use silenciosamente.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 7
MAX_INSIGHTS_PER_RUN = 3
MAX_STORED_INSIGHTS = 20  # cap en assistant_insights, FIFO

INSIGHTS_PROMPT = """Eres un analista de comportamiento. Analiza estos datos de la \
última semana del usuario y devuelve 2-3 patrones/insights ÚNICOS que el usuario \
probablemente no nota.

Datos agregados:
{stats}

Insights previos ya detectados (NO repitas):
{prior}

Reglas:
- Devuelve SOLO JSON válido sin markdown:
{{
  "insights": [
    {{
      "title": "Título corto",
      "description": "Explicación 1-2 frases, basada en datos",
      "category": "temporal" | "emocional" | "cumplimiento" | "topico" | "patron",
      "actionable": "Sugerencia concreta de acción"
    }}
  ]
}}
- Cada insight debe basarse en datos visibles arriba
- Si los datos son insuficientes, devuelve {{"insights": []}}
- No inventes números — usa los que ves
- Tono: directo, observacional

JSON:"""


class InsightService:
    """Análisis semanal + storage de insights + reporte."""

    def __init__(
        self,
        firestore_client: Any,
        telegram_bot: Any,
        genai_client: Any,
        collection_prefix: str = "assistant",
        user_id: str = "noe",
        model_id: str = "gemini-2.5-flash",
    ) -> None:
        self.db = firestore_client
        self.telegram = telegram_bot
        self.genai = genai_client
        self._prefix = collection_prefix
        self._user_id = user_id
        self._model_id = model_id

    async def run_weekly_analysis(self) -> dict[str, Any]:
        """Ciclo principal del análisis."""
        stats = self._aggregate_last_week()
        if stats["total_messages"] < 3:
            return {"ok": False, "reason": "not enough data", "stats": stats}

        prior_insights = self._load_recent_insights(limit=10)
        new_insights = await self._extract_insights(stats, prior_insights)

        stored = []
        for ins in new_insights:
            ins_id = self._store_insight(ins, stats)
            if ins_id:
                stored.append({**ins, "id": ins_id})

        if stored:
            await self._send_weekly_report(stats, stored)

        return {"ok": True, "stats": stats, "insights_stored": len(stored), "details": stored}

    def get_active_insights(self, limit: int = 5) -> list[dict[str, Any]]:
        """Insights recientes para inyectar al system prompt."""
        return self._load_recent_insights(limit=limit)

    # ── Aggregation ─────────────────────────────────────────────────────────

    def _aggregate_last_week(self) -> dict[str, Any]:
        """Agrega métricas desde assistant_history últimos 7 días."""
        if self.db is None:
            return {"total_messages": 0}

        cutoff = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).isoformat()

        try:
            from google.cloud.firestore import Query
            docs = (
                self.db.collection(f"{self._prefix}_history")
                .where("user_id", "==", self._user_id)
                .where("timestamp", ">=", cutoff)
                .stream()
            )
            interactions = [d.to_dict() for d in docs]
        except Exception as exc:
            logger.error("Failed to aggregate: %s", exc)
            return {"total_messages": 0}

        if not interactions:
            return {"total_messages": 0}

        # Métricas
        sentiments = Counter()
        topics = Counter()
        themes = Counter()
        energy_levels = Counter()
        hedging_scores = []
        word_counts = []
        commits = 0
        by_hour: dict[int, int] = defaultdict(int)
        by_weekday: dict[int, int] = defaultdict(int)
        tools_used = Counter()

        for it in interactions:
            sent = it.get("sentiment")
            if sent:
                sentiments[sent] += 1
            for t in it.get("topics", []):
                topics[t] += 1
            for th in it.get("themes_mentioned", []):
                themes[th] += 1
            energy = it.get("energy_level")
            if energy:
                energy_levels[energy] += 1
            hs = it.get("hedging_score")
            if isinstance(hs, (int, float)):
                hedging_scores.append(hs)
            wc = it.get("word_count")
            if isinstance(wc, int):
                word_counts.append(wc)
            if it.get("is_action_commitment"):
                commits += 1
            ts_str = it.get("timestamp", "")
            if ts_str:
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    by_hour[ts.hour] += 1
                    by_weekday[ts.weekday()] += 1
                except Exception:
                    pass
            for a in it.get("actions", []):
                tname = a.get("tool")
                if tname:
                    tools_used[tname] += 1

        avg_hedge = sum(hedging_scores) / len(hedging_scores) if hedging_scores else None
        avg_words = sum(word_counts) / len(word_counts) if word_counts else None

        return {
            "total_messages": len(interactions),
            "lookback_days": LOOKBACK_DAYS,
            "sentiments": dict(sentiments),
            "top_topics": dict(topics.most_common(8)),
            "themes": dict(themes),
            "energy_levels": dict(energy_levels),
            "avg_hedging_score": avg_hedge,
            "avg_word_count": avg_words,
            "action_commitments": commits,
            "messages_by_hour": dict(by_hour),
            "messages_by_weekday": dict(by_weekday),
            "tools_used": dict(tools_used.most_common(10)),
        }

    # ── Insights extraction ─────────────────────────────────────────────────

    async def _extract_insights(
        self,
        stats: dict[str, Any],
        prior_insights: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        prior_text = (
            "\n".join(f"- {p.get('title', '')}: {p.get('description', '')}"
                      for p in prior_insights)
            or "(ninguno)"
        )

        prompt = INSIGHTS_PROMPT.format(
            stats=json.dumps(stats, indent=2, ensure_ascii=False),
            prior=prior_text,
        )

        try:
            response = self.genai.models.generate_content(
                model=self._model_id,
                contents=prompt,
            )
            text = response.text or ""
            parsed = _parse_json_loose(text)
            insights = parsed.get("insights", []) if isinstance(parsed, dict) else []
            return [_validate_insight(i) for i in insights if i][:MAX_INSIGHTS_PER_RUN]
        except Exception as exc:
            logger.error("Insight extraction failed: %s", exc)
            return []

    # ── Storage ─────────────────────────────────────────────────────────────

    def _store_insight(
        self,
        insight: dict[str, Any],
        stats: dict[str, Any],
    ) -> str | None:
        if self.db is None:
            return None
        try:
            doc = {
                "user_id": self._user_id,
                "title": insight.get("title", ""),
                "description": insight.get("description", ""),
                "category": insight.get("category", ""),
                "actionable": insight.get("actionable", ""),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "based_on_messages": stats.get("total_messages", 0),
            }
            _, ref = self.db.collection(f"{self._prefix}_insights").add(doc)
            return ref.id
        except Exception as exc:
            logger.error("Failed to store insight: %s", exc)
            return None

    def _load_recent_insights(self, limit: int = 10) -> list[dict[str, Any]]:
        if self.db is None:
            return []
        try:
            from google.cloud.firestore import Query
            docs = (
                self.db.collection(f"{self._prefix}_insights")
                .where("user_id", "==", self._user_id)
                .order_by("created_at", direction=Query.DESCENDING)
                .limit(limit)
                .stream()
            )
            return [d.to_dict() for d in docs]
        except Exception as exc:
            logger.warning("Failed to load insights: %s", exc)
            return []

    # ── Report ──────────────────────────────────────────────────────────────

    async def _send_weekly_report(
        self,
        stats: dict[str, Any],
        insights: list[dict[str, Any]],
    ) -> None:
        if self.telegram is None:
            return

        lines = [f"📊 *Resumen semanal* ({LOOKBACK_DAYS} días)\n"]
        lines.append(f"Mensajes: {stats['total_messages']}")

        sentiments = stats.get("sentiments", {})
        if sentiments:
            top_sent = max(sentiments, key=sentiments.get)
            lines.append(f"Tono dominante: {top_sent}")

        commits = stats.get("action_commitments", 0)
        lines.append(f"Compromisos asumidos: {commits}")

        lines.append("\n*Patrones detectados:*")
        for ins in insights:
            lines.append(f"\n• *{ins.get('title')}*\n  {ins.get('description')}\n  → {ins.get('actionable')}")

        text = "\n".join(lines)
        await self.telegram.send_proactive_message(text)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _parse_json_loose(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        return json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError:
        return {}


def _validate_insight(ins: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(ins, dict):
        return {}
    return {
        "title": str(ins.get("title", ""))[:200],
        "description": str(ins.get("description", ""))[:500],
        "category": str(ins.get("category", ""))[:50],
        "actionable": str(ins.get("actionable", ""))[:300],
    }
