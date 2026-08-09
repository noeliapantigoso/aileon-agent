"""
Proactive cycle — the agent wakes up every hour and decides what to do.

Unlike the earlier version (fixed rules such as `if hour == 9`), here the agent
wakes up with its full toolset, looks at the user's real situation, acts if
needed, and only then decides whether it is worth writing to them. Staying
silent is a valid outcome.

Split of responsibilities:
  - Code enforces the hard limits: quiet hours, daily message cap, minimum gap
    between messages, and the nightly priority question (which feeds the 1am
    planner and is therefore not left to the model's judgement).
  - The agent decides everything else: whether to talk, when, about what, and
    which actions to take beforehand.

All limits are configured in the frontmatter of skills/proactive.md.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


# Ceilings the agent cannot escape by editing the skill.
# Config may be STRICTER than these, never more permissive.
ABSOLUTE_MAX_MESSAGES_PER_DAY = 6
ABSOLUTE_QUIET_HOURS = {0, 1, 2, 3, 4, 5, 6}

# Tools that change something in the user's world. When a wake-up runs one of
# these and then does NOT write to the user, it is almost always because the
# model put the notice in its final text — which reaches nobody — instead of
# calling send_message_to_user.
_WRITE_TOOLS = {
    "delegate_to_planner",
    "create_task", "update_task", "archive_task",
    "create_goal", "update_goal", "update_goal_progress", "archive_goal",
    "create_key_result", "update_key_result", "complete_key_result",
    "archive_key_result",
    "start_experiment", "log_experiment_progress", "close_experiment",
    "schedule_agent_task", "cancel_agent_task",
    "save_skill", "delete_skill",
    "forget_memory", "forget_about_me",
}


class ProactiveService:
    """Wakes the agent every hour and enforces the hard contact limits."""

    def __init__(
        self,
        firestore_client: Any,
        experiment_service: Any,
        notion_service: Any,
        telegram_bot: Any,
        genai_client: Any,
        agent: Any = None,
        skill_manager: Any = None,
        collection_prefix: str = "assistant",
        user_id: str = "user",
        model_id: str = "gemini-2.5-pro",
        config: dict | None = None,
    ) -> None:
        self.db = firestore_client
        self.experiments = experiment_service
        self.notion = notion_service
        self.telegram = telegram_bot
        self.genai = genai_client
        self.agent = agent
        self._skills = skill_manager
        self._prefix = collection_prefix
        self._user_id = user_id
        self._model_id = model_id
        self._apply_config(config or {})

    # ── Config (re-read on every wake-up) ───────────────────────────────────

    def _apply_config(self, cfg: dict) -> None:
        """
        Apply the skill config, clamped by the absolute ceilings.

        Quiet hours can only widen and the daily cap can only go down: if the
        agent edits the skill to talk more, the clamp stops it.
        """
        requested_max = int(cfg.get("max_messages_per_day", 4))
        requested_quiet = set(cfg.get("quiet_hours", [0, 1, 2, 3, 4, 5, 6, 7]))

        self._max_per_day: int = min(requested_max, ABSOLUTE_MAX_MESSAGES_PER_DAY)
        self._quiet_hours: set[int] = requested_quiet | ABSOLUTE_QUIET_HOURS
        self._min_gap_hours: int = int(cfg.get("min_hours_between_messages", 2))
        self._priority_question_hour: int = int(cfg.get("priority_question_hour", 22))
        self._silent_threshold: int = int(cfg.get("silent_threshold_days", 3))

        if requested_max > self._max_per_day or requested_quiet != self._quiet_hours:
            logger.warning(
                "Proactive config clamped: max_per_day %d->%d, quiet_hours %s->%s",
                requested_max, self._max_per_day,
                sorted(requested_quiet), sorted(self._quiet_hours),
            )

    def _refresh_config(self) -> None:
        """
        Re-read the frontmatter of skills/proactive.md before each cycle.

        Without this, a change the user (or the agent itself via save_skill)
        makes to the skill would not take effect until Cloud Run restarts the
        container.
        """
        if self._skills is None:
            return
        try:
            for skill in self._skills.load_all():
                if skill.get("name") == "proactive":
                    self._apply_config(skill.get("config") or {})
                    return
        except Exception as exc:
            logger.warning("Could not refresh proactive config: %s", exc)

    # ── Entry point ─────────────────────────────────────────────────────────

    async def run_cycle(self, current_hour_local: int) -> dict[str, Any]:
        """
        One wake-up. Called by the hourly tick.

        Args:
            current_hour_local: the user's local hour (0-23).
        """
        if self.agent is None:
            return {"sent": False, "skipped": "agent_not_available"}

        self._refresh_config()
        sent_today = self._sent_today()

        blocked = self._check_hard_limits(current_hour_local, sent_today)
        forced = self._pending_obligation(current_hour_local, sent_today)

        # A pending obligation overrides the daily cap and the minimum gap,
        # but never the quiet hours.
        if blocked == "quiet_hours" or (blocked and not forced):
            logger.info("Proactive skipped at hour %d: %s", current_hour_local, blocked)
            # quiet_hours is not logged: it would be 8 lines of noise every night.
            if blocked != "quiet_hours":
                await self._log(f"⏸️ {current_hour_local}:00 — did not wake ({blocked})")
            return {"sent": False, "skipped": blocked}

        prompt = self._build_wake_prompt(current_hour_local, sent_today, forced)

        try:
            from app.agent.tools import PROACTIVE_TOOLS
            response = await self.agent.process(
                prompt,
                source="proactive",
                save_history=False,
                extra_tools=PROACTIVE_TOOLS,
            )
        except Exception as exc:
            logger.error("Proactive wake-up failed: %s", exc)
            await self._log(f"❌ {current_hour_local}:00 — wake-up failed: {exc}")
            return {"sent": False, "error": str(exc)}

        decision = next(
            (a for a in response.actions_taken if a.tool == "send_message_to_user"),
            None,
        )
        other_actions = [
            a.tool for a in response.actions_taken if a.tool != "send_message_to_user"
        ]

        if decision is None:
            why = (response.message or "").strip()
            logger.info(
                "Proactive stayed silent at hour %d. Reasoning: %s",
                current_hour_local,
                why[:200],
            )
            self._log_wakeup(current_hour_local, sent=False, reason=why)

            # Going silent after touching the user's world is almost always a
            # model error: it wrote the message into the final text, which
            # reaches nobody.
            writes = [a for a in other_actions if a in _WRITE_TOOLS]
            warning = (
                f"\n\n⚠️ HEADS UP: it ran {', '.join(writes)} and did NOT tell you. "
                f"The text above was meant for you and was lost."
                if writes else ""
            )
            await self._log(
                f"🤐 {current_hour_local}:00 — stayed silent\n"
                f"{why[:400] or '(no explanation)'}"
                + (f"\n\nlooked at: {', '.join(other_actions)}" if other_actions else "")
                + warning
            )
            return {
                "sent": False,
                "silent_because": why[:300],
                "actions": other_actions,
            }

        message = (decision.args or {}).get("message", "").strip()
        msg_type = (decision.args or {}).get("message_type", "other")
        reason = (decision.args or {}).get("reason", "")

        if not message:
            return {"sent": False, "skipped": "empty_message", "actions": other_actions}

        await self.telegram.send_proactive_message(message)
        self._log_wakeup(current_hour_local, sent=True, msg_type=msg_type, reason=reason)
        logger.info("Proactive sent (%s): %s | reason: %s", msg_type, message[:80], reason)
        await self._log(
            f"📨 {current_hour_local}:00 — wrote to you ({msg_type})\n"
            f"reason: {reason}"
            + (f"\nactions: {', '.join(other_actions)}" if other_actions else "")
        )

        return {
            "sent": True,
            "type": msg_type,
            "reason": reason,
            "preview": message[:80],
            "actions": other_actions,
        }

    def _experiment_facts(self) -> str:
        """
        State of the experiments, rendered as facts for the wake-up.

        Code only computes what is happening; whether that is worth a message
        is the agent's call, guided by the `experiments` skill.
        """
        if self.experiments is None:
            return ""
        try:
            active = self.experiments.list_active()
            overdue = self.experiments.get_pending_check_ins()
        except Exception as exc:
            logger.warning("Could not read experiments: %s", exc)
            return ""

        if not active:
            base = (
                "- There is NO active experiment. The user wants one running at all "
                "times, and wants you to propose it when they haven't started one. "
                "See the `experiments` skill for how to pick one worth running."
            )
            # Urgency grows with the days: without this the agent can conclude
            # "not now" every hour and leave them for weeks with no experiment.
            try:
                days = self.experiments.days_without_experiment()
            except Exception:
                days = None
            if days is None:
                return base
            if days >= 7:
                return (
                    f"{base}\n"
                    f"  It has been {days} DAYS with none. That is too long: unless today "
                    f"is a bad day for them, propose one in this wake-up."
                )
            if days >= 3:
                return f"{base}\n  It has been {days} days with none — time to propose one."
            return f"{base}\n  The last one closed {days} day(s) ago; give it a little room."

        lines = [f"- Active experiments: {len(active)}."]
        for exp in overdue:
            lines.append(
                f"  - '{exp.get('name')}' has an OVERDUE check-in "
                f"(due {exp.get('next_check_in')}, "
                f"{len(exp.get('history') or [])} entries so far)."
            )
        if not overdue:
            lines.append("  None has a check-in due today.")
        return "\n".join(lines)

    async def _log(self, text: str) -> None:
        """Write to the log channel, if configured."""
        try:
            await self.telegram.send_log_message(text)
        except Exception:
            pass

    # ── Hard limits (code, not the model) ───────────────────────────────────

    def _check_hard_limits(
        self, hour: int, sent_today: list[dict[str, Any]]
    ) -> str | None:
        """Return the reason for blocking, or None if it may proceed."""
        if hour in self._quiet_hours:
            return "quiet_hours"
        if len(sent_today) >= self._max_per_day:
            return "daily_cap"
        last_hour = max((s.get("hour", -99) for s in sent_today), default=None)
        if last_hour is not None and (hour - last_hour) < self._min_gap_hours:
            return "too_soon"
        return None

    def _pending_obligation(
        self, hour: int, sent_today: list[dict[str, Any]]
    ) -> str | None:
        """Messages that must go out at a given hour no matter what."""
        types_today = {s.get("type") for s in sent_today}
        if hour == self._priority_question_hour and "priority_question" not in types_today:
            return "priority_question"
        return None

    # ── Wake-up prompt ──────────────────────────────────────────────────────

    def _build_wake_prompt(
        self,
        hour: int,
        sent_today: list[dict[str, Any]],
        forced: str | None,
    ) -> str:
        today = date.today()
        tomorrow = (today + timedelta(days=1)).isoformat()
        silent_days = self._days_since_last_user_message()
        remaining = max(0, self._max_per_day - len(sent_today))

        # Memory of the cycle: what you decided in each wake-up today, whether
        # you spoke or not. Without it you re-deliberate the same thing from
        # scratch sixteen times a day.
        wakeups = sorted(self._wakeups_today(), key=lambda w: w.get("hour", 0))
        if wakeups:
            lines = []
            for w in wakeups:
                reason = (w.get("reason") or "")[:110]
                if w.get("sent"):
                    lines.append(
                        f"  - {w.get('hour')}:00 — YOU WROTE ({w.get('type')}): {reason}"
                    )
                else:
                    lines.append(f"  - {w.get('hour')}:00 — you stayed silent: {reason}")
            already = "\n".join(lines)
        else:
            already = "  (this is your first wake-up today)"

        if silent_days >= self._silent_threshold:
            silence_note = (
                f"They have not written to you in {silent_days} days. That is past the "
                f"{self._silent_threshold}-day threshold — consider a genuine check-in."
            )
        elif silent_days >= 1:
            silence_note = f"They have not written to you in {silent_days} day(s)."
        else:
            silence_note = "They were active today."

        obligation = ""
        if forced == "priority_question":
            obligation = (
                f"\n## This one is mandatory right now\n"
                f"It is time to ask what their priority for tomorrow ({tomorrow}) is. "
                f"Call send_message_to_user with message_type='priority_question' and a "
                f"single short question. When they answer, store it with "
                f"save_day_priority(date='{tomorrow}'). The 1am planner uses it.\n"
            )

        return f"""\
[PROACTIVE WAKE-UP — this is not a message from the user]

You woke up on your own. It is {hour}:00 on {today.isoformat()}. Nobody wrote to you.

Your job now is to look at how their day is going and decide whether it is worth
interrupting them. You have your full toolset: you can check tasks, calendar, goals
and experiments, and you can also act — reschedule an overdue task, queue a reminder
for yourself, save something you noticed, or delegate to the planner if their day
fell apart.

## Situation
- {silence_note}
{self._experiment_facts()}
- What you already decided in today's wake-ups:
{already}
- You have at most {remaining} messages left today.
- The contact limits are enforced by code, not by you. You may edit the `proactive`
  skill to talk LESS, never to talk more: the hard ceiling is
  {ABSOLUTE_MAX_MESSAGES_PER_DAY} messages a day and hours
  {sorted(ABSOLUTE_QUIET_HOURS)} are always silent. Asking for more is ignored.
{obligation}
## Whatever you discover, save it

This is your most important job here — more important than deciding whether to write.

Looking at their day you will notice things they cannot see: that they have been
postponing everything from one project for two weeks, that afternoon blocks never
get done, that every time a certain meeting appears the rest of the day falls apart.

Those observations are lost unless you save them. The next wake-up starts from
scratch, and when they write to you in three hours you will have no idea what you
noticed today.

When you discover something durable about them, call `remember_fact` — or
`update_my_profile` if it is identity-level. The `memory-management` skill has the
rule for choosing between the two. Write it as an observed fact, not a vague hunch:
"Tasks from project X have been postponed since July 20th, rescheduled four times"
works; "seems to be avoiding something" does not.

Save it even if you decide to stay silent. Noticing something and writing to them
are independent decisions: you can record the pattern today and only bring it up
when they raise the subject.

## How to decide

First investigate with your tools if you are missing information. Act if there is
something you can resolve yourself. Only then decide whether to speak.

**Your final text does NOT reach them. Nobody reads it.** It is stored as the reason
for your silence in an internal log. The ONLY channel to them is the
`send_message_to_user` tool. If you write something addressed to them in the final
text — "I scheduled", "I couldn't", "do you want me to…?" — that message is lost and
they never find out.

So: if you have something to tell them, call the tool. If not, do not call it and
finish with ONE line, written for yourself in the third person, explaining why you
stayed quiet.

**If you did or attempted anything that affects them, you must tell them.** You moved
a block, rescheduled a task, tried to schedule something and hit conflicts: all of
that gets reported, without exception. Silence is valid when you touched nothing and
discovered nothing they need to know — never after reaching into their calendar.

The criteria for deciding — when it is worth writing, when it is not, and how to
write an affirmation — live in the `proactive` skill already loaded above. Follow it.
"""

    # ── State in Firestore ──────────────────────────────────────────────────

    def _day_doc(self):
        """One document per day: avoids composite queries (and their index)."""
        doc_id = f"{self._user_id}_{date.today().isoformat()}"
        return self.db.collection(f"{self._prefix}_proactive_log").document(doc_id)

    def _wakeups_today(self) -> list[dict[str, Any]]:
        """
        Every wake-up today, whether it spoke or not.

        This is the cycle's memory between one hour and the next. It lives apart
        from the conversation history on purpose: these are internal
        deliberations and have no business cluttering the user's chat.
        """
        if self.db is None:
            return []
        try:
            doc = self._day_doc().get()
            if not doc.exists:
                return []
            return (doc.to_dict() or {}).get("wakeups", [])
        except Exception as exc:
            logger.warning("_wakeups_today failed: %s", exc)
            return []

    def _sent_today(self) -> list[dict[str, Any]]:
        """Only the wake-ups that actually sent a message."""
        return [w for w in self._wakeups_today() if w.get("sent")]

    def _log_wakeup(
        self, hour: int, sent: bool, msg_type: str = "", reason: str = ""
    ) -> None:
        """Record this wake-up so the next one knows what was already considered."""
        if self.db is None:
            return
        try:
            entry = {
                "hour": hour,
                "sent": sent,
                "type": msg_type,
                "reason": reason[:300],
                "at": datetime.now(timezone.utc).isoformat(),
            }
            self._day_doc().set(
                {
                    "user_id": self._user_id,
                    "date": date.today().isoformat(),
                    "wakeups": self._wakeups_today() + [entry],
                },
                merge=True,
            )
        except Exception as exc:
            logger.warning("_log_wakeup failed: %s", exc)

    def _days_since_last_user_message(self) -> int:
        """Days since the user last wrote."""
        if self.db is None:
            return 0
        try:
            from google.cloud.firestore import Query
            docs = (
                self.db.collection(f"{self._prefix}_history")
                .where("user_id", "==", self._user_id)
                .order_by("timestamp", direction=Query.DESCENDING)
                .limit(1)
                .stream()
            )
            for doc in docs:
                ts_str = doc.to_dict().get("timestamp", "")
                if ts_str:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    return (datetime.now(timezone.utc) - ts).days
            return 999
        except Exception as exc:
            logger.warning("days_since_last_message failed: %s", exc)
            return 0
