#!/usr/bin/env python3
"""
Move the JSON blobs in `documents` into the real tables.

The document store keeps everything as JSON in one table. This reads those rows
and writes them into the typed tables in schema.py, where the fields become
columns the database can index and query.

    python migrate_to_tables.py --dry-run   # report, write nothing
    python migrate_to_tables.py

Non-destructive: the `documents` table is left exactly as it is, so services
that have not been converted yet keep reading what they always read, and a
second run overwrites rather than duplicating.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from typing import Any, Optional


def _pick(doc: dict, *names: str) -> Any:
    for name in names:
        if doc.get(name) is not None:
            return doc[name]
    return None


def migrate(db, raw: sqlite3.Connection, default_user: str, dry: bool) -> dict[str, int]:
    """Copy every collection into its table. Returns rows written per table."""
    counts: dict[str, int] = {}

    def docs(collection: str) -> list[tuple[str, dict]]:
        rows = raw.execute(
            "SELECT doc_id, data FROM documents WHERE collection = ?", (collection,)
        ).fetchall()
        return [(r[0], json.loads(r[1])) for r in rows]

    # -- profile: one row per entry, so a later edit touches one line ---------
    n = 0
    for user_id, doc in docs("assistant_users"):
        for position, entry in enumerate(doc.get("entries") or []):
            if not dry:
                db.upsert(
                    "profile_entries",
                    {"user_id": user_id, "position": position, "content": entry},
                    keys=["user_id", "position"],
                )
            n += 1
    counts["profile_entries"] = n

    # -- calibration ----------------------------------------------------------
    n = 0
    for user_id, doc in docs("assistant_calibration"):
        if not dry:
            db.upsert("calibration", {"user_id": user_id, **doc}, keys=["user_id"])
        n += 1
    counts["calibration"] = n

    # -- daily context: the document id was the date, and no user was stored --
    n = 0
    for date_iso, doc in docs("assistant_daily_context"):
        if not dry:
            db.upsert(
                "daily_context",
                {
                    "user_id": doc.get("user_id") or default_user,
                    "date": date_iso,
                    "priority": doc.get("priority"),
                    "saved_at": doc.get("saved_at"),
                },
                keys=["user_id", "date"],
            )
        n += 1
    counts["daily_context"] = n

    # -- history --------------------------------------------------------------
    n = 0
    for doc_id, doc in docs("assistant_history"):
        if not dry:
            db.upsert("history", {
                "id": doc_id,
                "user_id": doc.get("user_id") or default_user,
                "timestamp": doc.get("timestamp") or "",
                "user_message": doc.get("user_message"),
                "agent_response": doc.get("agent_response"),
                "proactive": bool(doc.get("proactive")),
                "word_count": doc.get("word_count"),
                "sentiment": doc.get("sentiment"),
                "energy_level": doc.get("energy_level"),
                "hedging_score": doc.get("hedging_score"),
                "is_action_commitment": (
                    None if doc.get("is_action_commitment") is None
                    else bool(doc["is_action_commitment"])
                ),
                "actions": doc.get("actions"),
                "topics": doc.get("topics"),
                "themes_mentioned": doc.get("themes_mentioned"),
            }, keys=["id"])
        n += 1
    counts["history"] = n

    # -- experiments ----------------------------------------------------------
    n = 0
    for doc_id, doc in docs("assistant_experiments"):
        if not dry:
            db.upsert("experiments", {
                "id": doc_id,
                "user_id": doc.get("user_id") or default_user,
                "name": doc.get("name") or doc_id,
                "hypothesis": doc.get("hypothesis"),
                "status": doc.get("status") or "active",
                "started": doc.get("started"),
                "ends": doc.get("ends"),
                "duration_days": doc.get("duration_days"),
                "check_in_every_days": doc.get("check_in_every_days"),
                "next_check_in": doc.get("next_check_in"),
                "closed_at": doc.get("closed_at"),
                "outcome": doc.get("outcome"),
                "created_at": doc.get("created_at"),
                "history": doc.get("history"),
            }, keys=["id"])
        n += 1
    counts["experiments"] = n

    # -- insights -------------------------------------------------------------
    n = 0
    for doc_id, doc in docs("assistant_insights"):
        if not dry:
            db.upsert("insights", {
                "id": doc_id,
                "user_id": doc.get("user_id") or default_user,
                "title": doc.get("title") or "(untitled)",
                "description": doc.get("description"),
                "category": doc.get("category"),
                "actionable": doc.get("actionable"),
                "based_on_messages": doc.get("based_on_messages") or 0,
                "created_at": doc.get("created_at"),
            }, keys=["id"])
        n += 1
    counts["insights"] = n

    # -- principles: shared, no user ------------------------------------------
    n = 0
    for doc_id, doc in docs("assistant_principles"):
        if not dry:
            db.upsert("principles", {
                "id": doc.get("id") or doc_id,
                "title": doc.get("title") or doc_id,
                "principle": doc.get("principle"),
                "quote": doc.get("quote"),
                "source": doc.get("source"),
                "actionable": doc.get("actionable"),
                "applies_when": doc.get("applies_when"),
                "personality_relevance": doc.get("personality_relevance"),
            }, keys=["id"])
        n += 1
    counts["principles"] = n

    # -- proactive log: two shapes shared one collection, split by which
    #    fields are present rather than by the id, which is not reliable ------
    n = 0
    for doc_id, doc in docs("assistant_proactive_log"):
        kind = "wakeups" if doc.get("wakeups") is not None else (
            doc.get("type") or "message"
        )
        if not dry:
            db.upsert("proactive_log", {
                "id": doc_id,
                "user_id": doc.get("user_id") or default_user,
                "date": doc.get("date") or doc_id.rsplit("_", 1)[-1],
                "type": kind,
                "sent_at": doc.get("sent_at"),
                "wakeups": doc.get("wakeups"),
                "messages": doc.get("messages"),
            }, keys=["id"])
        n += 1
    counts["proactive_log"] = n

    # -- profile history ------------------------------------------------------
    n = 0
    for doc_id, doc in docs("assistant_profile_history"):
        if not dry:
            db.upsert("profile_history", {
                "id": doc_id,
                "user_id": doc.get("user_id") or default_user,
                "changed_at": doc.get("changed_at"),
                "source": doc.get("source"),
                "sample_size_days": doc.get("sample_size_days"),
                "before": doc.get("before"),
                "after": doc.get("after"),
            }, keys=["id"])
        n += 1
    counts["profile_history"] = n

    # -- agent queue ----------------------------------------------------------
    n = 0
    for doc_id, doc in docs("assistant_agent_queue"):
        if not dry:
            db.upsert("agent_queue", {
                "id": doc_id,
                "user_id": doc.get("user_id") or default_user,
                "title": doc.get("title") or "(untitled)",
                "prompt": doc.get("prompt"),
                "status": doc.get("status") or "pending",
                "run_at": doc.get("run_at"),
                "created_at": doc.get("created_at"),
                "completed_at": doc.get("completed_at"),
            }, keys=["id"])
        n += 1
    counts["agent_queue"] = n

    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", default="", help="SQLite file (default: from settings)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sys.path.insert(0, ".")
    from app.config import get_settings
    from app.storage.schema import Database

    settings = get_settings()
    path = args.path or settings.sqlite_path

    db = Database(path)
    raw = sqlite3.connect(path)

    have = raw.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='documents'"
    ).fetchone()
    if not have:
        print("  No `documents` table — nothing to migrate.")
        return 0

    print(f"  file     {path}")
    print(f"  journal  {db.journal_mode}")
    print(f"  user     {settings.user_id}" + ("   (dry run)" if args.dry_run else ""))
    print()

    counts = migrate(db, raw, settings.user_id, args.dry_run)
    for table, n in counts.items():
        print(f"  {table:20s} {n:6d}")
    print(f"\n  {'total':20s} {sum(counts.values()):6d}")

    if args.dry_run:
        return 0

    # Read the tables back rather than trusting the writes above.
    print("\n  verifying...")
    problems = []
    for table, expected in counts.items():
        actual = db.count(f"SELECT COUNT(*) FROM {table}")
        if actual < expected:
            problems.append(f"    {table}: wrote {expected}, found {actual}")
    if problems:
        print("  MISMATCH:")
        print("\n".join(problems))
        return 1
    print("  ok — every row is readable from its table")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
