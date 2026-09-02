"""
Storage.

One SQLite file with real tables, defined in schema.py. Everything the agent
knows lives there: the conversation, the profile, the calibration, the skills
it wrote for itself.

There was a Firestore-shaped adapter here for a while, which is how the project
got off Google Cloud without touching a single service. Once every service had
moved onto real tables it had no callers left, so it is gone; migrate_to_sqlite.py
still copies a Firestore database into this one for anyone making that move.
"""

from app.storage.schema import Database

__all__ = ["Database"]
