"""
Weekly analysis service.

Triggered by the hourly tick on its cron schedule. Aggregates the last 7 days of
history, computes statistics, asks Gemini to extract 2-3 new patterns, stores the
insights in Firestore and sends a summary to the user.

Insights are injected into the system prompt of every conversation so the agent
can use them quietly.
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
MAX_STORED_INSIGHTS = 20  # cap on stored insights, FIFO

INSIGHTS_PROMPT = """You are a behaviour analyst. Look at this data from the user's \
last week and return 2-3 UNIQUE patterns they probably have not noticed themselves.

Aggregated data:
{stats}

Insights already detected before (do NOT repeat these):
{prior}

Rules:
- Return ONLY valid JSON, no markdown:
{{
  "insights": [
    {{
      "title": "Short title",
      "description": "1-2 sentence explanation, grounded in the data",
      "category": "temporal" | "emotional" | "completion" | "topic" | "pattern",
      "actionable": "One concrete suggested action"
    }}
  ]
}}
- Every insight must be grounded in the data shown above
- If the data is insufficient, return {{"insights": []}}
- Never invent numbers — use the ones you can see
- Tone: direct and observational

JSON:"""


CALIBRATION_PROMPT = """You are reviewing an automatic calibration of a user's \
productivity profile before it is written to their record.

# Completion by hour (last 30 days)
Only blocks the user explicitly confirmed or denied are counted. Hours with fewer
than 3 confirmed blocks were skipped by the arithmetic pass — treat those as no data.

{by_hour}

# What the arithmetic pass concluded
Peak candidates (>=70% completed): {peak_candidates}
Low-energy candidates (<=30% completed): {low_candidates}
Proposed peak_hours: {new_peak}
Proposed low_energy_hours: {new_low}

# Current stored values
peak_hours: {cur_peak}
low_energy_hours: {cur_low}

# Who the user is
{user_context}

# Their active experiments and recurring commitments
{experiments}

# Your job

The arithmetic tells you WHAT happened, never WHY. An hour with low completion can mean
several different things, and only one of them is low energy:

- They genuinely have less capacity then -> it IS low energy
- Meetings or commitments keep landing on top of those blocks -> a scheduling conflict
- They train, eat or rest at that hour -> the block should not have been there at all
- They always put the hardest work there -> difficulty, not energy

Marking a scheduling conflict as "low energy" is worse than doing nothing: the planner
then refuses to put deep work there permanently, for a reason that was never true.

Return ONLY valid JSON, no markdown:
{{
  "peak_hours": "H:MM-H:MM" or null to leave unchanged,
  "low_energy_hours": "H:MM-H:MM" or null to leave unchanged,
  "reasoning": "one or two sentences, in the user's language",
  "confidence": "high" | "medium" | "low"
}}

Rules:
- Thin sample -> return nulls for both. No data, no change.
- confidence "low" -> the change will not be applied, so say so honestly.
- Only change a value when you could state the reason out loud and have it sound right.
- Keep the same "H:MM-H:MM" format, comma-separated when there are several ranges.

JSON:"""


class InsightService:
    """Weekly analysis: compute, store insights, report."""

    def __init__(
        self,
        firestore_client: Any,
        telegram_bot: Any,
        genai_client: Any,
        collection_prefix: str = "assistant",
        user_id: str = "user",
        model_id: str = "gemini-2.5-pro",
        memory_manager: Any = None,
        calendar_service: Any = None,
        experiment_service: Any = None,
        notion_service: Any = None,
        database: Any = None,
    ) -> None:
        self.db = firestore_client
        self.sql = database
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
        """Main analysis cycle."""
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

        # Auto-tune the calibration fields from real completion history
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

    # ── Calibration auto-tuning ─────────────────────────────────────────────

    async def _tune_productivity_profile(self) -> dict[str, Any]:
        """Tune peak_hours / low_energy_hours from real completion rates."""
        if self.calendar is None or self.memory is None:
            return {"applied": False, "reason": "calendar/memory unavailable"}

        try:
            history = self.calendar.get_completion_history(days=30)
        except Exception as exc:
            logger.warning("Tuner: failed get_completion_history: %s", exc)
            return {"applied": False, "reason": "no history"}

        by_hour = history.get("by_hour") or {}
        if not by_hour:
            return {"applied": False, "reason": "no by_hour data"}

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
            return {"applied": False, "reason": "no clear patterns in verified data"}

        peak_candidates.sort()
        low_candidates.sort()

        new_peak = _build_hour_range(peak_candidates) if peak_candidates else None
        new_low = _build_hour_range(low_candidates) if low_candidates else None

        calibration = self.memory.get_calibration()
        cur_peak = calibration.get("peak_hours", "")
        cur_low = calibration.get("low_energy_hours", "")

        # The arithmetic above says WHAT happened. Before writing it down, the model
        # reviews it with the context the numbers cannot see — the user's routines,
        # their experiments, their commitments — and decides whether a low completion
        # rate really means low energy or something else entirely.
        review = await self._review_calibration(
            by_hour=by_hour,
            peak_candidates=peak_candidates,
            low_candidates=low_candidates,
            new_peak=new_peak,
            new_low=new_low,
            cur_peak=cur_peak,
            cur_low=cur_low,
        )

        if review.get("confidence") == "low":
            logger.info("Calibration review returned low confidence; nothing applied")
            return {
                "applied": False,
                "reason": "review not confident",
                "reasoning": review.get("reasoning", ""),
                "arithmetic_proposed": {"peak_hours": new_peak, "low_energy_hours": new_low},
            }

        # The reviewed values win over the raw arithmetic. None means "leave it".
        reviewed_peak = review.get("peak_hours") or None
        reviewed_low = review.get("low_energy_hours") or None

        updates: dict[str, Any] = {}
        if reviewed_peak and reviewed_peak != cur_peak:
            updates["peak_hours"] = reviewed_peak
        if reviewed_low and reviewed_low != cur_low:
            updates["low_energy_hours"] = reviewed_low

        if not updates:
            return {
                "applied": False,
                "reason": "review left values unchanged",
                "reasoning": review.get("reasoning", ""),
            }

        await self.memory.update_calibration(updates)

        # Record the change in the profile history
        if self.db is not None:
            try:
                self.db.collection(f"{self._prefix}_profile_history").add({
                    "user_id": self._user_id,
                    "changed_at": datetime.now(timezone.utc).isoformat(),
                    "before": {"peak_hours": cur_peak, "low_energy_hours": cur_low},
                    "after": updates,
                    "source": "weekly_auto_tune_reviewed",
                    "sample_size_days": 30,
                    "reasoning": review.get("reasoning", ""),
                    "confidence": review.get("confidence", ""),
                    "arithmetic_proposed": {
                        "peak_hours": new_peak, "low_energy_hours": new_low,
                    },
                })
            except Exception as exc:
                logger.warning("Failed history write: %s", exc)

        # Telegram notification, including WHY — the reasoning is the part worth
        # reading, and the part the user can push back on.
        if self.telegram is not None:
            msg_parts = ["🔧 *Adjusted your productivity profile*"]
            if "peak_hours" in updates:
                msg_parts.append(f"• Peak hours: `{cur_peak or 'unset'}` → `{updates['peak_hours']}`")
            if "low_energy_hours" in updates:
                msg_parts.append(f"• Low energy: `{cur_low or 'unset'}` → `{updates['low_energy_hours']}`")
            if review.get("reasoning"):
                msg_parts.append(f"\n{review['reasoning']}")
            msg_parts.append("_Based on blocks you actually confirmed. Tell me if it does not match._")
            try:
                await self.telegram.send_proactive_message("\n".join(msg_parts))
            except Exception as exc:
                logger.warning("Tuner notif failed: %s", exc)

        logger.info("Productivity tuned: %s", updates)
        return {"applied": True, "updates": updates, "before": {"peak_hours": cur_peak, "low_energy_hours": cur_low}}

    # ── Focus block tuning ──────────────────────────────────────────────────

    async def _review_calibration(
        self,
        by_hour: dict,
        peak_candidates: list,
        low_candidates: list,
        new_peak: str | None,
        new_low: str | None,
        cur_peak: str,
        cur_low: str,
    ) -> dict[str, Any]:
        """
        Have the model review what the arithmetic concluded.

        Counting completion per hour is arithmetic and belongs in code. Deciding
        whether a low rate means low energy — rather than a scheduling clash, a
        training slot, or simply where the hardest work always lands — needs the
        context the numbers do not carry. So the numbers propose and the model
        rules on them.

        Returns the raw review dict; a failure returns low confidence, which the
        caller treats as "change nothing".
        """
        if self.genai is None:
            return {"confidence": "low", "reasoning": "no model available to review"}

        rows = []
        for hour in sorted(by_hour, key=lambda h: int(h)):
            slot = by_hour[hour]
            total = slot.get("total", 0)
            done = slot.get("completed", 0)
            unverified = slot.get("unverified", 0)
            rate = f"{done / total * 100:.0f}%" if total else "no data"
            rows.append(
                f"  {int(hour):02d}:00 — {rate} ({done}/{total} confirmed, "
                f"{unverified} never verified)"
            )

        experiments = "(none)"
        if self.experiments is not None:
            try:
                active = self.experiments.list_active()
                experiments = "\n".join(
                    f"- {e.get('name')}: {e.get('hypothesis', '')}" for e in active
                ) or "(none)"
            except Exception:
                pass

        prompt = CALIBRATION_PROMPT.format(
            by_hour="\n".join(rows) or "  (no hours with confirmed blocks)",
            peak_candidates=peak_candidates or "none",
            low_candidates=low_candidates or "none",
            new_peak=new_peak or "no change proposed",
            new_low=new_low or "no change proposed",
            cur_peak=cur_peak or "unset",
            cur_low=cur_low or "unset",
            user_context=self._user_context() or "(no profile available)",
            experiments=experiments,
        )

        try:
            response = self.genai.models.generate_content(
                model=self._model_id,
                contents=prompt,
            )
            parsed = _parse_json_loose(response.text or "")
            if not isinstance(parsed, dict):
                return {"confidence": "low", "reasoning": "unparseable review"}
            logger.info(
                "Calibration review: confidence=%s peak=%s low=%s",
                parsed.get("confidence"), parsed.get("peak_hours"),
                parsed.get("low_energy_hours"),
            )
            return parsed
        except Exception as exc:
            logger.error("Calibration review failed: %s", exc)
            return {"confidence": "low", "reasoning": f"review failed: {exc}"}

    async def _tune_focus_block_minutes(self) -> dict[str, Any]:
        """Tune preferred_focus_block_minutes from the best-performing block length."""
        if self.calendar is None or self.memory is None:
            return {"applied": False, "reason": "calendar/memory unavailable"}

        events = self._fetch_plan_events(days=30)
        if not events:
            return {"applied": False, "reason": "sin eventos"}

        # Duration buckets, in minutes
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
            return {"applied": False, "reason": "not enough samples per bucket"}

        new_value = best[0]
        calibration = self.memory.get_calibration()
        cur = calibration.get("preferred_focus_block_minutes")

        # Solo cambiar si diferencia >= 15 min
        if cur is not None and abs(new_value - cur) < 15:
            return {"applied": False, "reason": "value close to current"}

        await self.memory.update_calibration({"preferred_focus_block_minutes": new_value})
        await self._notify(
            f"🔧 *Adjusted your ideal block length*\n"
            f"• `{cur or 'unset'}min` → `{new_value}min`\n"
            f"_Blocks of {new_value}min get completed {best[1]*100:.0f}% of the time (n={best[2]})._"
        )
        return {"applied": True, "value": new_value, "rate": best[1], "samples": best[2]}

    # ── Work hours tuning ──────────────────────────────────────────────────

    async def _tune_work_hours(self) -> dict[str, Any]:
        """Tune work_start / work_end from the hours with real activity."""
        if self.calendar is None or self.memory is None:
            return {"applied": False, "reason": "calendar/memory unavailable"}

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

        calibration = self.memory.get_calibration()
        cur_start = calibration.get("work_start", "")
        cur_end = calibration.get("work_end", "")

        updates = {}
        if new_start != cur_start and abs(_hour_str_to_float(new_start) - _hour_str_to_float(cur_start)) >= 0.5:
            updates["work_start"] = new_start
        if new_end != cur_end and abs(_hour_str_to_float(new_end) - _hour_str_to_float(cur_end)) >= 0.5:
            updates["work_end"] = new_end

        if not updates:
            return {"applied": False, "reason": "values close to current"}

        await self.memory.update_calibration(updates)
        parts = ["🔧 *Adjusted your working hours*"]
        if "work_start" in updates:
            parts.append(f"• Start: `{cur_start or 'unset'}` → `{updates['work_start']}`")
        if "work_end" in updates:
            parts.append(f"• End: `{cur_end or 'unset'}` → `{updates['work_end']}`")
        parts.append(f"_Based on {len(starts)} confirmed blocks over the last 30 days._")
        await self._notify("\n".join(parts))
        return {"applied": True, "updates": updates, "samples": len(starts)}

    # ── Estimation calibration ──────────────────────────────────────────────

    async def _tune_estimation_calibration(self) -> dict[str, Any]:
        """Learn how long things really take versus the estimate. Stores the factor."""
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

        calibration = self.memory.get_calibration()
        cur_cal = calibration.get("estimation_calibration", 1.0)

        # Solo aplicar si diff >= 0.15
        if abs(median_ratio - cur_cal) < 0.15:
            return {"applied": False, "reason": "calibration already close", "median": median_ratio}

        new_cal = round(median_ratio, 2)
        await self.memory.update_calibration({"estimation_calibration": new_cal})

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

    # ── Experiments auto-close ──────────────────────────────────────────────

    async def _auto_close_stale_experiments(self) -> dict[str, Any]:
        """Close experiments that are past their end date or clearly abandoned."""
        if self.experiments is None:
            return {"applied": False, "reason": "experiments unavailable"}

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
                    f"{emoji} *Closed experiment '{name}'*\n"
                    f"Estado: `{status}`\n"
                    f"{outcome}"
                )
            except Exception as exc:
                logger.warning("Failed to auto-close exp %s: %s", exp_id, exc)

        return {"applied": len(closed) > 0, "closed": closed}

    # ── Helpers internos ────────────────────────────────────────────────────

    def _fetch_plan_events(self, days: int = 30) -> list[dict[str, Any]]:
        """Return [plan] events from the last N days, completed or not."""
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

    def _user_context(self) -> str:
        """
        User context shared with the chat agent and the planner.

        The weekly analyst draws conclusions about the user's life, so it needs
        to know who they are. It used to see anonymous statistics only.
        """
        if self.memory is None:
            return ""
        try:
            from datetime import datetime as _dt
            from zoneinfo import ZoneInfo

            from app.agent.prompt_builder import build_user_context

            return build_user_context(
                user_profile={
                    "entries": self.memory.get_profile_entries(),
                    "calibration": self.memory.get_calibration(),
                },
                current_datetime=_dt.now(ZoneInfo("America/Lima")),
            )
        except Exception as exc:
            logger.warning("Insights: could not build user context: %s", exc)
            return ""

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
        """Aggregate metrics from the last 7 days of history."""
        if self.sql is None:
            return {"total_messages": 0}

        cutoff = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).isoformat()

        try:
            # An equality plus an inequality, which is the shape Firestore
            # demanded a composite index for and SQLite answers off one index.
            interactions = self.sql.query(
                "SELECT * FROM history WHERE user_id = ? AND timestamp >= ?",
                (self._user_id, cutoff),
            )
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

        # Rows come from a table now, so an unmeasured field is NULL rather than
        # absent — `.get(name, default)` returns None and the default never
        # fires. Every optional field below is read with `or` for that reason.
        for it in interactions:
            sent = it.get("sentiment")
            if sent:
                sentiments[sent] += 1
            for t in it.get("topics") or []:
                topics[t] += 1
            for th in it.get("themes_mentioned") or []:
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
            ts_str = it.get("timestamp") or ""
            if ts_str:
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    by_hour[ts.hour] += 1
                    by_weekday[ts.weekday()] += 1
                except Exception:
                    pass
            for a in it.get("actions") or []:
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
            from google.genai import types as _types

            response = self.genai.models.generate_content(
                model=self._model_id,
                contents=prompt,
                config=_types.GenerateContentConfig(
                    system_instruction=self._user_context() or None,
                ),
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
            from app.storage import Query
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
    """Turn an hour-sorted [(hour, rate, total)] list into an 'H:00-H:00' string.

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
