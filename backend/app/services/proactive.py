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
        database: Any = None,
    ) -> None:
        self.sql = database
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
        silence = next(
            (a for a in response.actions_taken if a.tool == "stay_silent"),
            None,
        )
        other_actions = [
            a.tool for a in response.actions_taken
            if a.tool not in ("send_message_to_user", "stay_silent")
        ]

        # ── Case 2: it declared silence deliberately ────────────────────────
        if decision is None and silence is not None:
            why = (silence.args or {}).get("reason", "").strip()
            logger.info("Proactive stayed silent at hour %d: %s", current_hour_local, why[:200])
            self._log_wakeup(current_hour_local, sent=False, reason=why)

            # Going quiet right after changing something in the user's world is
            # still worth flagging, even when the silence was deliberate.
            writes = [a for a in other_actions if a in _WRITE_TOOLS]
            warning = (
                f"\n\n⚠️ HEADS UP: it ran {', '.join(writes)} and chose not to tell you."
                if writes else ""
            )
            await self._log(
                f"🤐 {current_hour_local}:00 — stayed silent\n"
                f"{why[:400] or '(no reason given)'}"
                + (f"\n\nlooked at: {', '.join(other_actions)}" if other_actions else "")
                + warning
            )
            return {
                "sent": False,
                "silent_because": why[:300],
                "actions": other_actions,
            }

        # ── Case 3: it called neither tool ──────────────────────────────────
        # A protocol violation. Every wake-up must end by calling exactly one of
        # send_message_to_user or stay_silent. Ending with prose instead almost
        # always means the model wrote a message for the user into its final
        # answer — which reaches nobody. It happened three times in two days
        # before `stay_silent` existed, and prompt wording alone did not stop it.
        # Deliver the text rather than lose a real message, and flag it loudly.
        if decision is None:
            text = (response.message or "").strip()
            if not text:
                self._log_wakeup(current_hour_local, sent=False, reason="(no output)")
                await self._log(f"🤐 {current_hour_local}:00 — no output at all")
                return {"sent": False, "skipped": "empty_output", "actions": other_actions}

            logger.warning(
                "Proactive: no send/silence tool at hour %d — delivering the final "
                "text rather than losing it",
                current_hour_local,
            )
            await self.telegram.send_proactive_message(text)
            self._log_wakeup(
                current_hour_local, sent=True, msg_type=forced or "protocol_violation",
                reason="[recovered] written as final text, no tool call",
            )
            await self._log(
                f"📨 {current_hour_local}:00 — sent, RECOVERED\n"
                f"It called neither send_message_to_user nor stay_silent, and wrote "
                f"this as its final answer. Delivered so it wasn't lost — but the "
                f"agent broke protocol."
                + (f"\n\nactions: {', '.join(other_actions)}" if other_actions else "")
            )
            return {
                "sent": True,
                "type": forced or "protocol_violation",
                "recovered": True,
                "preview": text[:80],
                "actions": other_actions,
            }

        # ── Case 1: it decided to write ─────────────────────────────────────
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
                f"It is time to ask what their priority for tomorrow ({tomorrow}) is.\n\n"
                f"**You MUST call `send_message_to_user` this turn.** Do not write the "
                f"question in your final answer — that text goes to an internal log and "
                f"they will never see it. The question only exists if it goes through "
                f"the tool. This has gone wrong before: the model wrote a perfectly good "
                f"question as its final text and the user never received it.\n\n"
                f"Use message_type='priority_question' and a single short question. "
                f"When they answer, store it with save_day_priority(date='{tomorrow}'). "
                f"The 1am planner reads it.\n"
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

**Finish this wake-up by calling exactly one of two tools. Never by writing prose.**

- `send_message_to_user` — you decided to write to them
- `stay_silent` — you decided not to, with your reason in one line

**Your final text does NOT reach them. Nobody reads it.** If you write something
addressed to them there — "I scheduled", "I couldn't", "do you want me to…?" — that
message is lost. This has already happened several times: perfectly good messages
written as a final answer that the user never received. The message only exists if it
goes through `send_message_to_user`.

Silence is a valid and frequent choice — but it has to be declared with `stay_silent`,
not left as the absence of an action.

**If you did or attempted anything that affects them, you must tell them.** You moved
a block, rescheduled a task, tried to schedule something and hit conflicts: all of
that gets reported, without exception. Silence is valid when you touched nothing and
discovered nothing they need to know — never after reaching into their calendar.

The criteria for deciding — when it is worth writing, when it is not, and how to
write an affirmation — live in the `proactive` skill already loaded above. Follow it.
"""

    # ── State ───────────────────────────────────────────────────────────────

    def _day_row_id(self) -> str:
        """One row per user per day, holding that day's wake-ups."""
        return f"{self._user_id}_{date.today().isoformat()}"

    def _wakeups_today(self) -> list[dict[str, Any]]:
        """
        Every wake-up today, whether it spoke or not.

        This is the cycle's memory between one hour and the next. It lives apart
        from the conversation history on purpose: these are internal
        deliberations and have no business cluttering the user's chat.
        """
        if self.sql is None:
            return []
        try:
            row = self.sql.query_one(
                "SELECT wakeups FROM proactive_log WHERE id = ?", (self._day_row_id(),)
            )
            return (row or {}).get("wakeups") or []
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
        if self.sql is None:
            return
        try:
            entry = {
                "hour": hour,
                "sent": sent,
                "type": msg_type,
                "reason": reason[:300],
                "at": datetime.now(timezone.utc).isoformat(),
            }
            self.sql.upsert("proactive_log", {
                "id": self._day_row_id(),
                "user_id": self._user_id,
                "date": date.today().isoformat(),
                "type": "wakeups",
                "wakeups": self._wakeups_today() + [entry],
            }, keys=["id"])
        except Exception as exc:
            logger.warning("_log_wakeup failed: %s", exc)

    def _days_since_last_user_message(self) -> int:
        """Days since the user last wrote."""
        if self.sql is None:
            return 0
        try:
            row = self.sql.query_one(
                "SELECT timestamp FROM history WHERE user_id = ? "
                "ORDER BY timestamp DESC LIMIT 1",
                (self._user_id,),
            )
            ts_str = (row or {}).get("timestamp") or ""
            if ts_str:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                return (datetime.now(timezone.utc) - ts).days
            return 999
        except Exception as exc:
            logger.warning("days_since_last_message failed: %s", exc)
            return 0
