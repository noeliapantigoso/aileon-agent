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
        memory_manager: Any = None,
        calendar_service: Any = None,
        experiment_service: Any = None,
        notion_service: Any = None,
    ) -> None:
        self.db = firestore_client
        self.telegram = telegram_bot
        self.genai = genai_client
        self._prefix = collection_prefix
        self._user_id = user_id
        self._model_id = model_id
        self.memory = memory_manager
        self.calendar = calendar_service
        self.experiments = experiment_service
        self.notion = notion_service

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

        # Auto-tune del perfil productivity en base a completion_history
        productivity_change = await self._tune_productivity_profile()
        focus_change = await self._tune_focus_block_minutes()
        work_hours_change = await self._tune_work_hours()
        estimation_change = await self._tune_estimation_calibration()
        experiments_change = await self._auto_close_stale_experiments()

        return {
            "ok": True,
            "stats": stats,
            "insights_stored": len(stored),
            "details": stored,
            "productivity_tuning": productivity_change,
            "focus_block_tuning": focus_change,
            "work_hours_tuning": work_hours_change,
            "estimation_tuning": estimation_change,
            "experiments_cleanup": experiments_change,
        }

    # ── Productivity auto-tuning ────────────────────────────────────────────

    async def _tune_productivity_profile(self) -> dict[str, Any]:
        """Ajusta peak_hours/low_energy_hours según cumplimiento real."""
        if self.calendar is None or self.memory is None:
            return {"applied": False, "reason": "calendar/memory no disponible"}

        try:
            history = self.calendar.get_completion_history(days=30)
        except Exception as exc:
            logger.warning("Tuner: failed get_completion_history: %s", exc)
            return {"applied": False, "reason": "no history"}

        by_hour = history.get("by_hour") or {}
        if not by_hour:
            return {"applied": False, "reason": "sin datos by_hour"}

        peak_candidates = []
        low_candidates = []
        for hour, slot in by_hour.items():
            try:
                h = int(hour)
            except Exception:
                continue
            total = slot.get("total", 0)
            done = slot.get("completed", 0)
            if total < 3:
                continue
            rate = done / total if total else 0
            if rate >= 0.70:
                peak_candidates.append((h, rate, total))
            elif rate <= 0.30:
                low_candidates.append((h, rate, total))

        if not peak_candidates and not low_candidates:
            return {"applied": False, "reason": "sin patrones claros"}

        peak_candidates.sort()
        low_candidates.sort()

        new_peak = _build_hour_range(peak_candidates) if peak_candidates else None
        new_low = _build_hour_range(low_candidates) if low_candidates else None

        try:
            profile = await self.memory._get_user_profile()
        except Exception:
            profile = {}
        productivity = (profile or {}).get("productivity", {}) or {}
        cur_peak = productivity.get("peak_hours", "")
        cur_low = productivity.get("low_energy_hours", "")

        updates: dict[str, Any] = {}
        if new_peak and new_peak != cur_peak:
            updates["peak_hours"] = new_peak
        if new_low and new_low != cur_low:
            updates["low_energy_hours"] = new_low

        if not updates:
            return {"applied": False, "reason": "valores iguales a perfil actual"}

        merged = {**productivity, **updates}
        await self.memory.update_user_profile({"productivity": merged})

        # Guardar entrada en historial
        if self.db is not None:
            try:
                self.db.collection(f"{self._prefix}_profile_history").add({
                    "user_id": self._user_id,
                    "changed_at": datetime.now(timezone.utc).isoformat(),
                    "before": {"peak_hours": cur_peak, "low_energy_hours": cur_low},
                    "after": updates,
                    "source": "weekly_auto_tune",
                    "sample_size_days": 30,
                })
            except Exception as exc:
                logger.warning("Failed history write: %s", exc)

        # Notif Telegram
        if self.telegram is not None:
            msg_parts = ["🔧 *Ajusté tu perfil de productividad*"]
            if "peak_hours" in updates:
                msg_parts.append(f"• Peak hours: `{cur_peak or 'sin valor'}` → `{updates['peak_hours']}`")
            if "low_energy_hours" in updates:
                msg_parts.append(f"• Low energy: `{cur_low or 'sin valor'}` → `{updates['low_energy_hours']}`")
            msg_parts.append("_Basado en tu cumplimiento real de los últimos 30 días. Avísame si no concuerda._")
            try:
                await self.telegram.send_proactive_message("\n".join(msg_parts))
            except Exception as exc:
                logger.warning("Tuner notif failed: %s", exc)

        logger.info("Productivity tuned: %s", updates)
        return {"applied": True, "updates": updates, "before": {"peak_hours": cur_peak, "low_energy_hours": cur_low}}

    # ── Focus block tuning ──────────────────────────────────────────────────

    async def _tune_focus_block_minutes(self) -> dict[str, Any]:
        """Ajusta preferred_focus_block_minutes según duración óptima de bloques cumplidos."""
        if self.calendar is None or self.memory is None:
            return {"applied": False, "reason": "calendar/memory no disponible"}

        events = self._fetch_plan_events(days=30)
        if not events:
            return {"applied": False, "reason": "sin eventos"}

        # Buckets de duración (min)
        buckets = {"30": {"t": 0, "d": 0}, "45": {"t": 0, "d": 0},
                   "60": {"t": 0, "d": 0}, "90": {"t": 0, "d": 0}, "120": {"t": 0, "d": 0}}

        for e in events:
            dur = _event_duration_min(e)
            if dur is None:
                continue
            if dur <= 30: key = "30"
            elif dur <= 45: key = "45"
            elif dur <= 60: key = "60"
            elif dur <= 90: key = "90"
            else: key = "120"
            buckets[key]["t"] += 1
            if e.get("completed") == "true":
                buckets[key]["d"] += 1

        # Mejor bucket: total >=3 y mejor rate
        best = None
        for k, v in buckets.items():
            if v["t"] < 3:
                continue
            rate = v["d"] / v["t"]
            if best is None or rate > best[1]:
                best = (int(k), rate, v["t"])

        if best is None:
            return {"applied": False, "reason": "sin muestra suficiente por bucket"}

        new_value = best[0]
        profile = await self.memory._get_user_profile()
        productivity = (profile or {}).get("productivity", {}) or {}
        cur = productivity.get("preferred_focus_block_minutes")

        # Solo cambiar si diferencia >= 15 min
        if cur is not None and abs(new_value - cur) < 15:
            return {"applied": False, "reason": "valor cercano al actual"}

        merged = {**productivity, "preferred_focus_block_minutes": new_value}
        await self.memory.update_user_profile({"productivity": merged})
        await self._notify(
            f"🔧 *Ajusté duración ideal de bloque*\n"
            f"• `{cur or 'sin valor'}min` → `{new_value}min`\n"
            f"_Bloques de {new_value}min cumplen {best[1]*100:.0f}% (n={best[2]})._"
        )
        return {"applied": True, "value": new_value, "rate": best[1], "samples": best[2]}

    # ── Work hours tuning ──────────────────────────────────────────────────

    async def _tune_work_hours(self) -> dict[str, Any]:
        """Ajusta work_start/work_end según horas reales con actividad."""
        if self.calendar is None or self.memory is None:
            return {"applied": False, "reason": "calendar/memory no disponible"}

        events = self._fetch_plan_events(days=30)
        if not events:
            return {"applied": False, "reason": "sin eventos"}

        starts: list[float] = []
        ends: list[float] = []
        for e in events:
            if e.get("completed") != "true":
                continue
            s = _parse_iso_dt(e.get("start"))
            en = _parse_iso_dt(e.get("end"))
            if s is None or en is None:
                continue
            starts.append(s.hour + s.minute / 60)
            ends.append(en.hour + en.minute / 60)

        if len(starts) < 5:
            return {"applied": False, "reason": "sin muestras suficientes"}

        starts.sort()
        ends.sort()
        p10_start = starts[max(0, int(len(starts) * 0.10))]
        p90_end = ends[min(len(ends) - 1, int(len(ends) * 0.90))]
        new_start = f"{int(p10_start)}:{int((p10_start % 1) * 60):02d}"
        new_end = f"{int(p90_end)}:{int((p90_end % 1) * 60):02d}"

        profile = await self.memory._get_user_profile()
        productivity = (profile or {}).get("productivity", {}) or {}
        cur_start = productivity.get("work_start", "")
        cur_end = productivity.get("work_end", "")

        updates = {}
        if new_start != cur_start and abs(_hour_str_to_float(new_start) - _hour_str_to_float(cur_start)) >= 0.5:
            updates["work_start"] = new_start
        if new_end != cur_end and abs(_hour_str_to_float(new_end) - _hour_str_to_float(cur_end)) >= 0.5:
            updates["work_end"] = new_end

        if not updates:
            return {"applied": False, "reason": "valores cercanos al perfil"}

        merged = {**productivity, **updates}
        await self.memory.update_user_profile({"productivity": merged})
        parts = ["🔧 *Ajusté tus horas laborales*"]
        if "work_start" in updates:
            parts.append(f"• Inicio: `{cur_start or 'sin valor'}` → `{updates['work_start']}`")
        if "work_end" in updates:
            parts.append(f"• Fin: `{cur_end or 'sin valor'}` → `{updates['work_end']}`")
        parts.append(f"_Basado en {len(starts)} bloques cumplidos últimos 30 días._")
        await self._notify("\n".join(parts))
        return {"applied": True, "updates": updates, "samples": len(starts)}

    # ── Estimation calibration ─────────────────────────────────────────────

    async def _tune_estimation_calibration(self) -> dict[str, Any]:
        """Aprende cuánto te tomas vs estimas. Guarda factor en profile."""
        if self.calendar is None or self.memory is None or self.notion is None:
            return {"applied": False, "reason": "deps no disponibles"}

        events = self._fetch_plan_events(days=30)
        ratios: list[float] = []
        details: list[dict[str, Any]] = []

        for e in events:
            if e.get("completed") != "true":
                continue
            task_id = e.get("task_id")
            if not task_id:
                continue
            actual = _event_duration_min(e)
            if actual is None or actual <= 0:
                continue
            task = await self.notion.get_task(task_id)
            if not task:
                continue
            est = task.get("time_estimate")
            if not est or est <= 0:
                continue
            ratio = actual / est
            ratios.append(ratio)
            details.append({"task": task.get("title", "")[:40], "est": est, "actual": round(actual)})

        if len(ratios) < 5:
            return {"applied": False, "reason": f"muestras insuficientes ({len(ratios)}/5)"}

        ratios.sort()
        median_ratio = ratios[len(ratios) // 2]

        profile = await self.memory._get_user_profile()
        productivity = (profile or {}).get("productivity", {}) or {}
        cur_cal = productivity.get("estimation_calibration", 1.0)

        # Solo aplicar si diff >= 0.15
        if abs(median_ratio - cur_cal) < 0.15:
            return {"applied": False, "reason": "calibración cercana", "median": median_ratio}

        new_cal = round(median_ratio, 2)
        merged = {**productivity, "estimation_calibration": new_cal}
        await self.memory.update_user_profile({"productivity": merged})

        if new_cal > 1.0:
            direction = f"subestimas {int((new_cal - 1) * 100)}%"
        else:
            direction = f"sobreestimas {int((1 - new_cal) * 100)}%"

        await self._notify(
            f"⏱️ *Aprendí cómo estimas tiempos*\n"
            f"En promedio {direction} cuánto te toman las cosas.\n"
            f"Factor: `{cur_cal}` → `{new_cal}` (n={len(ratios)} tareas)\n"
            f"_Voy a ajustar mis sugerencias de tiempo automáticamente._"
        )
        return {"applied": True, "value": new_cal, "samples": len(ratios), "details": details[:5]}

    # ── Experiments auto-close ─────────────────────────────────────────────

    async def _auto_close_stale_experiments(self) -> dict[str, Any]:
        """Cierra experimentos vencidos o abandonados."""
        if self.experiments is None:
            return {"applied": False, "reason": "experiments no disponible"}

        try:
            active = self.experiments.list_active()
        except Exception as exc:
            logger.warning("list_active failed: %s", exc)
            return {"applied": False, "reason": "list_active failed"}

        today = date.today()
        closed = []

        for exp in active:
            exp_id = exp.get("id")
            name = exp.get("name", "")
            ends_str = exp.get("ends", "")
            history = exp.get("history", []) or []

            try:
                ends = date.fromisoformat(ends_str) if ends_str else today
            except Exception:
                ends = today

            last_log_str = max((h.get("date", "") for h in history), default=exp.get("started", ""))
            try:
                last_log = date.fromisoformat(last_log_str) if last_log_str else today
                days_since_log = (today - last_log).days
            except Exception:
                days_since_log = 0

            stale = today > ends + timedelta(days=7) and days_since_log >= 14

            if not stale:
                continue

            did = sum(1 for h in history if h.get("did_it"))
            total = max(len(history), 1)
            rate = did / total

            if rate >= 0.70:
                outcome = f"Auto-cerrado: alta tasa de cumplimiento ({int(rate * 100)}%)"
                status = "completed"
                emoji = "✅"
            elif rate <= 0.30:
                outcome = f"Auto-cerrado: baja actividad ({int(rate * 100)}% + {days_since_log}d sin logs)"
                status = "abandoned"
                emoji = "⚠️"
            else:
                outcome = f"Auto-cerrado: sin actividad reciente (cumplimiento {int(rate * 100)}%)"
                status = "abandoned"
                emoji = "🛑"

            try:
                self.experiments.close_experiment(exp_id, outcome=outcome, status=status)
                closed.append({"id": exp_id, "name": name, "status": status, "rate": rate})
                await self._notify(
                    f"{emoji} *Cerré experimento '{name}'*\n"
                    f"Estado: `{status}`\n"
                    f"{outcome}"
                )
            except Exception as exc:
                logger.warning("Failed to auto-close exp %s: %s", exp_id, exc)

        return {"applied": len(closed) > 0, "closed": closed}

    # ── Helpers internos ────────────────────────────────────────────────────

    def _fetch_plan_events(self, days: int = 30) -> list[dict[str, Any]]:
        """Devuelve eventos [plan] últimos N días (cumplidos o no)."""
        from datetime import timezone as _tz
        now = datetime.now(_tz.utc)
        try:
            events = self.calendar.list_events(
                start=now - timedelta(days=days),
                end=now,
                max_results=500,
            )
            return [e for e in events if "[plan]" in (e.get("summary") or "")]
        except Exception as exc:
            logger.warning("fetch_plan_events failed: %s", exc)
            return []

    async def _notify(self, text: str) -> None:
        if self.telegram is None:
            return
        try:
            await self.telegram.send_proactive_message(text)
        except Exception as exc:
            logger.warning("Tuner notify failed: %s", exc)

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


def _parse_iso_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _event_duration_min(event: dict[str, Any]) -> float | None:
    s = _parse_iso_dt(event.get("start"))
    e = _parse_iso_dt(event.get("end"))
    if s is None or e is None:
        return None
    return (e - s).total_seconds() / 60.0


def _hour_str_to_float(value: str) -> float:
    if not value or ":" not in value:
        return 0.0
    try:
        h, m = value.split(":", 1)
        return int(h) + int(m) / 60.0
    except Exception:
        return 0.0


def _build_hour_range(candidates: list[tuple[int, float, int]]) -> str | None:
    """Convierte lista [(hour, rate, total)] ordenada por hora en string 'H:00-H:00'.

    Si las horas son contiguas, devuelve un rango único. Si hay gaps, los segmenta
    con coma. Ej: [(6,0.8,5),(7,0.75,4),(15,0.7,3)] → '6:00-8:00,15:00-16:00'.
    """
    if not candidates:
        return None
    hours = sorted({h for h, _, _ in candidates})
    segments: list[tuple[int, int]] = []
    seg_start = hours[0]
    prev = hours[0]
    for h in hours[1:]:
        if h == prev + 1:
            prev = h
            continue
        segments.append((seg_start, prev + 1))
        seg_start = h
        prev = h
    segments.append((seg_start, prev + 1))
    return ",".join(f"{s}:00-{e}:00" for s, e in segments)
