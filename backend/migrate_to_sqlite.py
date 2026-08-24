#!/usr/bin/env python3
"""
Copy everything out of Firestore into a local SQLite file.

Read-only on the Firestore side: it never writes, updates or deletes anything
up there. Re-running is safe — documents are keyed by (collection, id), so a
second run overwrites with the same values rather than duplicating.

    python migrate_to_sqlite.py --dry-run          # count first, touch nothing
    python migrate_to_sqlite.py                    # write data/aileon.db
    python migrate_to_sqlite.py --dest /tmp/x.db   # somewhere else

The resulting file is the whole agent's memory. Point SQLITE_PATH at it and set
STORAGE_BACKEND=sqlite, and the agent carries on where it left off.
"""

from __future__ import annotations

import argparse
import base64
import sys
from datetime import date, datetime
from typing import Any


def _clean(value: Any, path: str = "") -> Any:
    """
    Convert Firestore's own types into something JSON can hold.

    Anything unrecognised is reported rather than silently coerced: a value
    that arrives as a string when it used to be a reference would be a bug that
    only shows up weeks later, in behaviour, with no trace of where it started.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    # DatetimeWithNanoseconds subclasses datetime, so this catches it too
    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")

    if isinstance(value, dict):
        return {k: _clean(v, f"{path}.{k}") for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [_clean(v, f"{path}[{i}]") for i, v in enumerate(value)]

    # Firestore references and geo points, identified by shape so this file
    # does not have to import the SDK's private classes.
    if hasattr(value, "path") and hasattr(value, "id"):
        return {"_type": "reference", "path": value.path}
    if hasattr(value, "latitude") and hasattr(value, "longitude"):
        return {"_type": "geopoint", "lat": value.latitude, "lng": value.longitude}

    raise TypeError(f"Unconvertible value at {path or '<root>'}: {type(value).__name__}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="", help="GCP project (default: from settings)")
    parser.add_argument("--dest", default="", help="SQLite file to write (default: from settings)")
    parser.add_argument("--prefix", default="", help="Only collections starting with this")
    parser.add_argument("--dry-run", action="store_true", help="Count, write nothing")
    args = parser.parse_args()

    sys.path.insert(0, ".")
    from app.config import get_settings

    settings = get_settings()
    project = args.project or settings.gcp_project_id
    dest = args.dest or settings.sqlite_path
    prefix = args.prefix or settings.firestore_collection_prefix

    if not project:
        print("No GCP project. Pass --project or set GCP_PROJECT_ID.", flush=True)
        return 1

    from google.cloud import firestore

    print(f"  source   firestore://{project}", flush=True)
    print(f"  dest     {dest}{'  (dry run, nothing written)' if args.dry_run else ''}", flush=True)
    print(f"  prefix   {prefix}_*\n", flush=True)

    source = firestore.Client(project=project)

    dest_store = None
    if not args.dry_run:
        from app.storage.sqlite_store import SqliteStore
        dest_store = SqliteStore(dest)

    names = sorted(c.id for c in source.collections() if c.id.startswith(prefix))
    if not names:
        print(f"No collections start with {prefix!r}. Nothing to do.", flush=True)
        return 1

    total = 0
    counts: dict[str, int] = {}
    skipped: list[tuple[str, str, str]] = []
    subcollections: list[str] = []

    # Listing a document's subcollections costs a round trip each, so probing
    # every document would add one network call per row. Subcollections are a
    # structural choice, not a per-document one, so a sample per collection
    # finds them just as reliably at a fraction of the cost.
    SUBCOLLECTION_PROBE = 5

    for name in names:
        count = 0
        for doc in source.collection(name).stream():
            data = doc.to_dict() or {}
            try:
                cleaned = _clean(data, name)
            except TypeError as exc:
                skipped.append((name, doc.id, str(exc)))
                continue

            if count < SUBCOLLECTION_PROBE:
                for sub in doc.reference.collections():
                    subcollections.append(f"{name}/*/{sub.id}")

            if dest_store is not None:
                dest_store.collection(name).document(doc.id).set(cleaned)
            count += 1

        counts[name] = count
        total += count
        print(f"  {name:44s} {count:6d}", flush=True)

    print(f"\n  {'total':44s} {total:6d}", flush=True)

    if skipped:
        print(f"\n  {len(skipped)} document(s) skipped:", flush=True)
        for coll, doc_id, why in skipped[:10]:
            print(f"    {coll}/{doc_id}: {why}", flush=True)

    if subcollections:
        found = sorted(set(subcollections))
        print(f"\n  {len(found)} subcollection(s) NOT copied:", flush=True)
        for s in found[:10]:
            print(f"    {s}", flush=True)
        print("  This script only copies top-level collections.", flush=True)

    # Read the destination back to confirm what is actually retrievable, which
    # is not the same as what the loop above believes it wrote. Compared against
    # the counts from that pass rather than re-streaming Firestore, which would
    # download everything a second time.
    if dest_store is not None:
        print("\n  verifying...", flush=True)
        bad = []
        for name in names:
            written = len(list(dest_store.collection(name).stream()))
            if written != counts[name]:
                bad.append(f"    {name}: copied {counts[name]}, readable {written}")
        if bad:
            print("  MISMATCH:", flush=True)
            print("\n".join(bad), flush=True)
            return 1
        print(f"  ok — {total} documents readable from {dest}", flush=True)
        print("\n  To use it:", flush=True)
        print("    STORAGE_BACKEND=sqlite", flush=True)
        print(f"    SQLITE_PATH={dest}", flush=True)

    if skipped:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
