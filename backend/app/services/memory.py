"""
Four-layer memory system for the productivity agent.

Layer 1 — Short term: the most recent messages of the current conversation.
Layer 2 — Work context: today's agenda and pending tasks (5 min cache).
Layer 3 — User profile: a Firestore document of free prose (long term).
Layer 4 — Episodic memory: facts stored in Mem0 (semantic search).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# The collection names the agent knows, mapped onto tables. Only `users`
# differs: the profile became one row per entry, so what used to be a single
# document is now a small table of its own.
_TABLE_FOR_COLLECTION = {
    "users": "profile_entries",
}

# Columns in `history` the tagger is allowed to fill. Anything else it returns
# is ignored: a new tag needs a column before it can be stored.
_HISTORY_TAG_COLUMNS = frozenset({
    "sentiment", "energy_level", "hedging_score",
    "is_action_commitment", "topics", "themes_mentioned",
})

SESSION_TIMEOUT_SECONDS = 30 * 60  # 30 min idle -> new session
WORK_CONTEXT_TTL_SECONDS = 5 * 60  # Work-context cache lifetime
MAX_CONVERSATION_MESSAGES = 15
# How many Mem0 memories are retrieved per message.
# Mem0 returns exactly N ranked by similarity, with NO relevance floor: if only
# 3 memories relate to the message, the other N-3 are weak filler the model
# still reads as pertinent. Raising it trades precision for coverage, not just
# tokens. Anything that must never fall below the cut does not belong here —
# it belongs in the profile, which is always injected whole.
MAX_RELEVANT_MEMORIES = 12


class MemoryManager:
    """Manages the agent's four memory layers."""

    def __init__(
        self,
        mem0_client: Any,
        notion_service: Any,
        database: Any = None,
        collection_prefix: str = "assistant",
        user_id: str = "user",
        principle_service: Any = None,
        tagging_service: Any = None,
        insight_service: Any = None,
    ) -> None:
        # `db` is the document-store adapter, still handed to SkillManager.
        # `sql` is the real database; everything in this class uses it.
        self.sql = database
        self.mem0 = mem0_client
        self.notion = notion_service
        self.principles = principle_service
        self.tagger = tagging_service
        self.insights = insight_service
        self._prefix = collection_prefix
        self._user_id = user_id

        # Layer 1: in-memory conversation buffer
        self.conversation_buffer: list[dict[str, str]] = []
        self._last_activity: float = time.time()

        # Work-context cache (layer 2)
        self._work_context_cache: Optional[dict] = None
        self._work_context_ts: float = 0.0

    # ── Public API ───────────────────────────────────────────────────────────

    async def get_full_context(self, user_message: str) -> dict[str, Any]:
        """
        Load everything prompt_builder needs.

        Returns:
            {
                "user_profile": dict,
                "today_context": dict,
                "relevant_memories": list[str],
                "conversation_history": list[dict],
            }
        """
        self._check_session_timeout()

        # On a cold start the buffer is empty: restore recent messages from Firestore
        if not self.conversation_buffer:
            await self._restore_buffer_from_firestore()

        # Load in parallel what can be loaded in parallel
        profile_task = self._get_user_profile()
        context_task = self._get_work_context()
        memories_task = self._search_memories(user_message)

        user_profile, today_context, relevant_memories = await asyncio.gather(
            profile_task, context_task, memories_task
        )

        # Principles — all of them. Gemini picks which ones apply.
        relevant_principles: list[dict[str, Any]] = []
        if self.principles is not None:
            try:
                relevant_principles = self.principles.get_all()
            except Exception as exc:
                logger.warning("Failed to load principles: %s", exc)

        # Active insights (patterns detected by previous weekly analyses)
        active_insights: list[dict[str, Any]] = []
        if self.insights is not None:
            try:
                active_insights = self.insights.get_active_insights(limit=5)
            except Exception as exc:
                logger.warning("Failed to load insights: %s", exc)

        return {
            "user_profile": user_profile,
            "today_context": today_context,
            "relevant_memories": relevant_memories,
            "conversation_history": list(self.conversation_buffer),
            "relevant_principles": relevant_principles,
            "active_insights": active_insights,
        }

    async def _restore_buffer_from_firestore(self) -> None:
        """Load the most recent interactions into the in-memory buffer."""
        if self.sql is None:
            return
        try:
            interactions = self.sql.query(
                "SELECT * FROM history WHERE user_id = ? "
                "ORDER BY timestamp DESC LIMIT ?",
                (self._user_id, MAX_CONVERSATION_MESSAGES),
            )
            interactions.reverse()  # oldest first

            # A column that was never filled is NULL, not absent, so the second
            # argument to .get() never fires and every optional field is read
            # with `or` instead.
            for it in interactions:
                ts = it.get("timestamp") or ""
                self.conversation_buffer.append(
                    {"role": "user", "content": it.get("user_message") or "", "timestamp": ts}
                )
                self.conversation_buffer.append({
                    "role": "assistant",
                    "content": it.get("agent_response") or "",
                    "actions": it.get("actions") or [],
                    "timestamp": ts,
                    "proactive": bool(it.get("proactive")),
                })
            if interactions:
                logger.info("Restored %d interactions", len(interactions))
        except Exception as exc:
            logger.warning("Failed to restore conversation buffer: %s", exc)

    async def save_interaction(
        self,
        user_message: str,
        agent_response: str,
        actions_taken: list[dict[str, Any]] | None = None,
    ) -> None:
        """Save the interaction across every relevant layer."""
        self._last_activity = time.time()
        actions = actions_taken or []

        # Layer 1: append to the conversation buffer
        now_iso = datetime.now(timezone.utc).isoformat()
        self.conversation_buffer.append({"role": "user", "content": user_message, "timestamp": now_iso})
        self.conversation_buffer.append({
            "role": "assistant",
            "content": agent_response,
            "actions": actions,
            "timestamp": now_iso,
        })

        # Keep only the most recent N messages
        if len(self.conversation_buffer) > MAX_CONVERSATION_MESSAGES * 2:
            self.conversation_buffer = self.conversation_buffer[
                -(MAX_CONVERSATION_MESSAGES * 2):
            ]

        # Persist to Firestore (durable history) and Mem0, in the background
        asyncio.create_task(
            self._persist_interaction(user_message, agent_response, actions)
        )

    def save_day_priority(self, date_iso: str, priority: str) -> None:
        """Store the user's priority for a specific date."""
        if self.sql is None:
            return
        try:
            self.sql.upsert("daily_context", {
                "user_id": self._user_id,
                "date": date_iso,
                "priority": priority,
                "saved_at": datetime.now(timezone.utc).isoformat(),
            }, keys=["user_id", "date"])
        except Exception as exc:
            logger.warning("Failed to save day priority: %s", exc)

    def get_day_priority(self, date_iso: str) -> str:
        """Read the stored priority for a date. Returns '' when there is none."""
        if self.sql is None:
            return ""
        try:
            row = self.sql.query_one(
                "SELECT priority FROM daily_context WHERE user_id = ? AND date = ?",
                (self._user_id, date_iso),
            )
            if row:
                return row.get("priority") or ""
        except Exception as exc:
            logger.warning("Failed to get day priority: %s", exc)
        return ""

    # Collections the agent may inspect. The prefix is added here, so there is no
    # way to read anything outside the user's namespace.
    READABLE_COLLECTIONS = (
        "users",
        "calibration",
        "history",
        "experiments",
        "insights",
        "principles",
        "proactive_log",
        "daily_context",
        "agent_queue",
        "skills",
        "profile_history",
    )

    def read_storage(
        self,
        collection: str,
        document_id: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """
        Read raw Firestore documents so the agent can inspect them.

        Read-only. Lets it answer questions like "what did you save in my profile"
        or "why did you decide to message me yesterday" without the user having to
        open the Google Cloud console.
        """
        if self.sql is None:
            return {"error": "database unavailable"}
        if collection not in self.READABLE_COLLECTIONS:
            return {
                "error": f"Unknown collection: {collection}",
                "valid": list(self.READABLE_COLLECTIONS),
            }

        if self.sql is None:
            return {"error": "database unavailable", "collection": collection}

        table = _TABLE_FOR_COLLECTION.get(collection, collection)
        limit = max(1, min(limit, 50))

        # Rows carry a user column except for `principles`, which is shared
        # knowledge rather than anything about a person.
        scoped = table != "principles"
        where = "WHERE user_id = ?" if scoped else ""
        params: tuple = (self._user_id,) if scoped else ()

        try:
            if document_id:
                key = "date" if table == "daily_context" else "id"
                rows = self.sql.query(
                    f"SELECT * FROM {table} {where} "
                    f"{'AND' if scoped else 'WHERE'} {key} = ? LIMIT 1",
                    params + (document_id,),
                )
                if not rows:
                    return {
                        "collection": collection,
                        "document_id": document_id,
                        "found": False,
                    }
                return {
                    "collection": collection,
                    "document_id": document_id,
                    "found": True,
                    "data": _truncate_doc(rows[0]),
                }

            rows = self.sql.query(
                f"SELECT * FROM {table} {where} LIMIT ?", params + (limit,)
            )
            return {
                "collection": collection,
                "count": len(rows),
                "documents": [_truncate_doc(r) for r in rows],
            }
        except Exception as exc:
            logger.warning("read_storage failed for %s: %s", collection, exc)
            return {"error": str(exc), "collection": collection}

    async def save_proactive_message(self, message: str) -> None:
        """Record a proactive message sent to the user (no user turn precedes it)."""
        now_iso = datetime.now(timezone.utc).isoformat()

        # Append as an assistant message with no matching user turn
        self.conversation_buffer.append({
            "role": "assistant",
            "content": message,
            "actions": [],
            "timestamp": now_iso,
            "proactive": True,
        })

        if len(self.conversation_buffer) > MAX_CONVERSATION_MESSAGES * 2:
            self.conversation_buffer = self.conversation_buffer[-(MAX_CONVERSATION_MESSAGES * 2):]

        if self.sql is not None:
            try:
                self.sql.insert("history", {
                    "id": uuid.uuid4().hex[:20],
                    "user_id": self._user_id,
                    "user_message": "",
                    "agent_response": message,
                    "actions": [],
                    "timestamp": now_iso,
                    "proactive": True,
                })
            except Exception as exc:
                logger.warning("Failed to persist proactive message: %s", exc)

    # ── Memory management (user-facing) ──────────────────────────────────────

    async def add_memory(self, text: str) -> bool:
        """Explicitly save a fact to Mem0. Returns True on success."""
        if self.mem0 is None:
            return False
        try:
            self.mem0.add(
                messages=[{"role": "user", "content": text}],
                user_id=self._user_id,
            )
            return True
        except Exception as exc:
            logger.warning("add_memory failed: %s", exc)
            return False

    async def list_memories(self) -> list[dict[str, Any]]:
        """Return all stored memories for the user."""
        if self.mem0 is None:
            return []
        try:
            results = self.mem0.get_all(filters={"user_id": self._user_id})
            memories = []
            items = results.get("results", results) if isinstance(results, dict) else results
            for item in (items or []):
                if isinstance(item, dict) and item.get("memory"):
                    memories.append({"id": item.get("id", ""), "text": item["memory"]})
            return memories
        except Exception as exc:
            logger.warning("list_memories failed: %s", exc)
            return []

    async def delete_memory(self, memory_id: str) -> bool:
        """Delete a specific memory by its Mem0 ID. Returns True on success."""
        if self.mem0 is None:
            return False
        try:
            self.mem0.delete(memory_id=memory_id)
            return True
        except Exception as exc:
            logger.warning("delete_memory failed: %s", exc)
            return False

    def invalidate_work_context_cache(self) -> None:
        """Invalida el cache de contexto de trabajo (tras crear/actualizar tareas)."""
        self._work_context_cache = None
        self._work_context_ts = 0.0

    # ── Internal layers ──────────────────────────────────────────────────────

    async def _get_user_profile(self) -> dict[str, Any]:
        """
        Layer 3: the user profile.

        Returns two deliberately separate things:
          - entries:     free prose, no schema. What the user said about themselves.
          - calibration: the few fields insights.py computes on (peak_hours,
                         work_start, ...). These are the machine's measurements,
                         not statements the user made.

        Nothing the user says has to pick a field: it all goes to entries.
        """
        return {
            "entries": self.get_profile_entries(),
            "calibration": self.get_calibration(),
        }

    # ── Profile: prose entries (no schema) ───────────────────────────────────

    def get_profile_entries(self) -> list[str]:
        """Prose entries about the user. Always injected in full."""
        if self.sql is None:
            return list(DEFAULT_PROFILE_ENTRIES)
        try:
            rows = self.sql.query(
                "SELECT content FROM profile_entries WHERE user_id = ? "
                "ORDER BY position",
                (self._user_id,),
            )
            if rows:
                return [r["content"] for r in rows if r.get("content")]
        except Exception as exc:
            logger.error("Failed to load profile entries: %s", exc)
        return list(DEFAULT_PROFILE_ENTRIES)

    def _write_profile_entries(self, entries: list[str]) -> None:
        """
        Replace the stored entries with this list.

        Done in one transaction: a half-written profile is worse than an
        unchanged one, and this is the only place the whole set is rewritten.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self.sql.transaction() as conn:
            conn.execute(
                "DELETE FROM profile_entries WHERE user_id = ?", (self._user_id,)
            )
            conn.executemany(
                "INSERT INTO profile_entries (user_id, position, content, created_at) "
                "VALUES (?, ?, ?, ?)",
                [(self._user_id, i, e, now) for i, e in enumerate(entries)],
            )

    def add_profile_entry(self, entry: str, replaces: str | None = None) -> dict[str, Any]:
        """
        Add an entry to the profile, or replace an existing one.

        `replaces` accepts a partial match so the model does not have to reproduce
        the previous text character by character.
        """
        entry = (entry or "").strip()
        if not entry:
            return {"error": "empty entry"}
        if self.sql is None:
            return {"error": "database unavailable"}

        entries = self.get_profile_entries()
        replaced = None

        if replaces:
            needle = replaces.strip().lower()
            for i, existing in enumerate(entries):
                if needle in existing.lower() or existing.lower() in needle:
                    replaced = existing
                    entries[i] = entry
                    break
            if replaced is None:
                entries.append(entry)
        else:
            entries.append(entry)

        try:
            self._write_profile_entries(entries)
        except Exception as exc:
            logger.error("Failed to save profile entry: %s", exc)
            return {"error": str(exc)}

        return {
            "status": "replaced" if replaced else "added",
            "entry": entry,
            "replaced": replaced,
            "total_entries": len(entries),
            "total_chars": sum(len(e) for e in entries),
        }

    def remove_profile_entry(self, entry: str) -> dict[str, Any]:
        """Remove a profile entry by partial match."""
        if self.sql is None:
            return {"error": "database unavailable"}
        entries = self.get_profile_entries()
        needle = (entry or "").strip().lower()
        if not needle:
            return {"error": "empty entry"}

        kept = [e for e in entries if needle not in e.lower()]
        if len(kept) == len(entries):
            return {"status": "not_found", "entry": entry}
        try:
            self._write_profile_entries(kept)
        except Exception as exc:
            logger.error("Failed to remove profile entry: %s", exc)
            return {"error": str(exc)}
        return {"status": "removed", "removed_count": len(entries) - len(kept)}

    # ── Calibration: what the machine measures (this one does have fields) ───

    # Calibration has real columns because every one of these is a number the
    # weekly tuner reads and writes.

    def get_calibration(self) -> dict[str, Any]:
        """
        Fields derived from real behaviour, tuned weekly by insights.py.

        They have structure because code reads them, recomputes them and writes
        them back. That is the only reason a field exists here at all.
        """
        if self.sql is None:
            return dict(DEFAULT_CALIBRATION)
        try:
            row = self.sql.query_one(
                "SELECT * FROM calibration WHERE user_id = ?", (self._user_id,)
            )
            if row:
                # A column the tuner has never written comes back as NULL, and
                # the caller expects the default rather than a None it would
                # have to handle at every use.
                stored = {k: v for k, v in row.items()
                          if v is not None and k not in ("user_id", "updated_at")}
                return {**DEFAULT_CALIBRATION, **stored}
        except Exception as exc:
            logger.warning("Failed to load calibration: %s", exc)
        return dict(DEFAULT_CALIBRATION)

    async def update_calibration(self, updates: dict[str, Any]) -> None:
        """Write calibration fields. Only insights.py uses this."""
        if self.sql is None or not updates:
            return
        try:
            self.sql.upsert("calibration", {
                "user_id": self._user_id,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                **updates,
            }, keys=["user_id"])
            logger.info("Calibration updated: %s", list(updates.keys()))
        except Exception as exc:
            logger.error("Failed to update calibration: %s", exc)

    async def _get_work_context(self) -> dict[str, Any]:
        """Layer 2: today's work context (TTL-cached)."""
        now = time.time()
        if (
            self._work_context_cache is not None
            and (now - self._work_context_ts) < WORK_CONTEXT_TTL_SECONDS
        ):
            return self._work_context_cache

        context: dict[str, Any] = {"date": date.today().isoformat()}

        try:
            # Tareas de hoy
            today_str = date.today().isoformat()
            tasks = await self.notion.get_tasks(date=today_str)
            context["today_tasks"] = tasks

            # Tareas pendientes generales
            pending = await self.notion.get_tasks(status="pending", limit=10)
            context["pending_tasks"] = pending


            # Metas activas (todos los tipos)
            try:
                active_goals = await self.notion.get_goals(status="active")
                context["active_goals"] = active_goals
            except Exception as gexc:
                logger.warning("Failed to load active goals: %s", gexc)
                context["active_goals"] = []

        except Exception as exc:
            logger.warning("Failed to load work context from Notion: %s", exc)
            context["error"] = str(exc)

        self._work_context_cache = context
        self._work_context_ts = now
        return context

    async def _search_memories(self, query: str) -> list[str]:
        """Layer 4: search Mem0 for relevant episodic memories."""
        if self.mem0 is None:
            return []

        try:
            results = self.mem0.search(
                query=query,
                filters={"user_id": self._user_id},
                limit=MAX_RELEVANT_MEMORIES,
                version="v2",
            )
            memories = []
            if isinstance(results, dict) and "results" in results:
                for item in results["results"]:
                    memory_text = item.get("memory", "")
                    if memory_text:
                        memories.append(memory_text)
            elif isinstance(results, list):
                for item in results:
                    memory_text = (
                        item.get("memory", "") if isinstance(item, dict) else str(item)
                    )
                    if memory_text:
                        memories.append(memory_text)
            return memories[:MAX_RELEVANT_MEMORIES]
        except Exception as exc:
            logger.warning("Mem0 search failed: %s", exc)
            return []

    async def _persist_interaction(
        self,
        user_message: str,
        agent_response: str,
        actions: list[dict[str, Any]] | None = None,
    ) -> None:
        """Persist the interaction to Firestore and Mem0 (background task)."""
        # Tagging vía Gemini Flash (no bloquea respuesta — ya estamos en background)
        tags: dict[str, Any] = {}
        if self.tagger is not None:
            try:
                tags = await self.tagger.tag(user_message, agent_response)
            except Exception as exc:
                logger.warning("Tagging failed: %s", exc)

        if self.sql is not None:
            try:
                row = {
                    "id": uuid.uuid4().hex[:20],
                    "user_id": self._user_id,
                    "user_message": user_message,
                    "agent_response": agent_response,
                    "actions": actions or [],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "word_count": len(user_message.split()),
                }
                # The tagger returns only the columns it managed to fill, and
                # anything it does not know about would be a column that does
                # not exist, so unknown keys are dropped rather than crashing
                # the write of a message that is otherwise fine.
                row.update({k: v for k, v in tags.items() if k in _HISTORY_TAG_COLUMNS})
                self.sql.insert("history", row)
            except Exception as exc:
                logger.error("Failed to persist interaction: %s", exc)

        # Mem0: fact extraction
        if self.mem0 is not None:
            try:
                conversation = [
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": agent_response},
                ]
                self.mem0.add(
                    messages=conversation,
                    user_id=self._user_id,
                )
            except Exception as exc:
                logger.warning("Mem0 add failed: %s", exc)

    def _check_session_timeout(self) -> None:
        """Clear the conversation buffer after a long enough idle gap."""
        now = time.time()
        if (now - self._last_activity) > SESSION_TIMEOUT_SECONDS:
            logger.info("Session timeout, clearing conversation buffer")
            self.conversation_buffer.clear()
        self._last_activity = now


def _truncate_doc(data: dict[str, Any], max_chars: int = 600) -> dict[str, Any]:
    """
    Truncate long values in a document before handing it to the model.

    Without this, a single `history` document holding a long conversation can
    eat a large share of the context window.
    """
    out: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, str) and len(value) > max_chars:
            out[key] = value[:max_chars] + f"... [+{len(value) - max_chars} chars]"
        elif isinstance(value, dict):
            out[key] = _truncate_doc(value, max_chars)
        elif isinstance(value, list) and len(value) > 20:
            out[key] = value[:20] + [f"... [+{len(value) - 20} items]"]
        else:
            out[key] = value
    return out


# Seed profile used when Firestore is unavailable or the profile is empty.
# Free prose: there are no fields to respect and no order to maintain.
# Deliberately near-empty — the real profile is built from what the user tells
# the agent, never from assumptions baked into the code.
DEFAULT_PROFILE_ENTRIES: tuple[str, ...] = (
    "No profile information has been recorded yet. Ask about their work, their "
    "routines and how they want to be spoken to as it comes up naturally, and "
    "store it with update_my_profile.",
)

# Los únicos campos con estructura de todo el sistema. Existen porque
# insights.py los lee, los recalcula desde el comportamiento real y los
# reescribe cada semana. Si algún día nada computa sobre uno, sobra.
DEFAULT_CALIBRATION: dict[str, Any] = {
    "peak_hours": "9:00-12:00",
    "low_energy_hours": "13:00-15:00",
    "work_start": "8:00",
    "work_end": "23:00",
    "preferred_focus_block_minutes": 90,
    "estimation_calibration": 1.0,
}
