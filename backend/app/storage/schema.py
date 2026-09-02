"""
The real database: one SQLite file, one table per thing that exists.

This replaces the document-store adapter. That adapter kept every record as a
JSON blob in a single `documents` table, which was the right way to get off
Firestore quickly, but it means the database cannot see any field inside a
record. Asking for "low-energy messages on Tuesdays" loads every row and throws
most of them away in Python. With one user that is invisible; with a hundred it
is the whole problem.

Conventions follow Hermes, which runs this shape in production:

- ``id TEXT PRIMARY KEY``. Ids are generated, not counted, so they stay stable
  across a copy of the file.
- ``NOT NULL`` on anything the code assumes is present, and ``DEFAULT`` on
  anything with a sensible zero value, so a partial insert cannot leave a row
  the readers have to defend against.
- Enum-valued columns document their values in a comment next to the column.
  There is no other place that stays true.
- Schema changes are additive and applied on open: a missing column is added,
  never renamed or dropped in place.

One convention is deliberately not copied. Hermes stores times as integer
epochs; these stay ISO-8601 text, because every date in this codebase is
already an ISO string and ISO strings sort correctly as text — which is what
makes ``ORDER BY timestamp`` work. Converting would mean touching every
comparison for no gain the app can feel.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# SQLite builds carrying the WAL-reset corruption bug. On these, a database
# that is not already in WAL is left in DELETE mode rather than moved into it.
# See apply_journal_mode below.
_WAL_UNSAFE_BELOW = (3, 51, 3)

SCHEMA = """
-- Who the user is, as prose the model reads. One row per entry rather than a
-- JSON list, so editing the seventh thing we know about someone is one UPDATE
-- instead of rewriting all of them -- the failure being that a model which
-- stops halfway takes the whole profile with it.
CREATE TABLE IF NOT EXISTS profile_entries (
    user_id     TEXT    NOT NULL,
    position    INTEGER NOT NULL,
    content     TEXT    NOT NULL,
    created_at  TEXT,
    PRIMARY KEY (user_id, position)
);

-- The six numbers the weekly tuner computes on. Every one is read and written
-- by code, which is exactly why these are columns and the profile above is not.
CREATE TABLE IF NOT EXISTS calibration (
    user_id                       TEXT    NOT NULL PRIMARY KEY,
    preferred_focus_block_minutes INTEGER DEFAULT 90,
    estimation_calibration        REAL    DEFAULT 1.0,
    work_start                    TEXT,
    work_end                      TEXT,
    -- Comma-separated "H:MM-H:MM" ranges, e.g. "9:00-12:00,15:00-17:00"
    peak_hours                    TEXT,
    low_energy_hours              TEXT,
    updated_at                    TEXT
);

-- What the user said mattered on a given day.
CREATE TABLE IF NOT EXISTS daily_context (
    user_id   TEXT NOT NULL,
    date      TEXT NOT NULL,          -- YYYY-MM-DD, the user's local day
    priority  TEXT,
    saved_at  TEXT,
    PRIMARY KEY (user_id, date)
);

-- The conversation. The largest table and the one every other feature reads.
-- The tagger's output lives in columns because those fields exist precisely so
-- that something can count and correlate them.
CREATE TABLE IF NOT EXISTS history (
    id                   TEXT    NOT NULL PRIMARY KEY,
    user_id              TEXT    NOT NULL,
    timestamp            TEXT    NOT NULL,   -- ISO-8601, sorts as text
    user_message         TEXT,
    agent_response       TEXT,
    -- 1 when the agent spoke first and no reply was required
    proactive            INTEGER DEFAULT 0,
    word_count           INTEGER,
    -- From the tagger. Null on everything recorded before it was fixed, so a
    -- reader must treat null as "not measured", never as a zero.
    sentiment            TEXT,               -- positive | neutral | negative
    energy_level         TEXT,               -- high | medium | low
    hedging_score        REAL,               -- 0.0 confident .. 1.0 hedged
    is_action_commitment INTEGER,
    -- Genuinely list-shaped: read whole, never filtered on. json_each() is
    -- there if that ever changes.
    actions              TEXT,
    topics               TEXT,
    themes_mentioned     TEXT
);
-- Every read of this table is "this user's most recent messages".
CREATE INDEX IF NOT EXISTS idx_history_user_time
    ON history (user_id, timestamp DESC);
-- The weekly analysis correlates behaviour against the tagger's output, and
-- most rows have none, so the index covers only the rows that do.
CREATE INDEX IF NOT EXISTS idx_history_energy
    ON history (user_id, energy_level) WHERE energy_level IS NOT NULL;

-- Behaviour changes the user is deliberately trying.
CREATE TABLE IF NOT EXISTS experiments (
    id                  TEXT NOT NULL PRIMARY KEY,
    user_id             TEXT NOT NULL,
    name                TEXT NOT NULL,
    hypothesis          TEXT,
    status              TEXT NOT NULL DEFAULT 'active',  -- active | closed
    started             TEXT,
    ends                TEXT,
    duration_days       INTEGER,
    check_in_every_days INTEGER,
    next_check_in       TEXT,
    closed_at           TEXT,
    outcome             TEXT,   -- confirmed | refuted | inconclusive | abandoned
    created_at          TEXT,
    history             TEXT    -- [{date, note, did_it}]
);
CREATE INDEX IF NOT EXISTS idx_experiments_active
    ON experiments (user_id, status);

-- What the agent concluded about the user, with the evidence behind it.
CREATE TABLE IF NOT EXISTS insights (
    id                TEXT    NOT NULL PRIMARY KEY,
    user_id           TEXT    NOT NULL,
    title             TEXT    NOT NULL,
    description       TEXT,
    category          TEXT,
    actionable        TEXT,
    based_on_messages INTEGER DEFAULT 0,
    created_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_insights_user_created
    ON insights (user_id, created_at DESC);

-- Shared across everyone: general knowledge, not personal data, hence no
-- user_id. Ships with the image and is the same for every deployment.
CREATE TABLE IF NOT EXISTS principles (
    id                    TEXT NOT NULL PRIMARY KEY,
    title                 TEXT NOT NULL,
    principle             TEXT,
    quote                 TEXT,
    source                TEXT,
    actionable            TEXT,
    applies_when          TEXT,   -- list of situations
    personality_relevance TEXT    -- list of enneagram type keys
);

-- One row per logged event. The daily caps in the proactive cycle are counted
-- straight off this table.
--
-- Keyed by id and not by (user, date, type): the agent sends several messages
-- of the same kind on the same day — four daily_affirmation rows on one date
-- in the real data — and a composite key silently collapsed 125 of them.
CREATE TABLE IF NOT EXISTS proactive_log (
    id        TEXT NOT NULL PRIMARY KEY,
    user_id   TEXT NOT NULL,
    date      TEXT NOT NULL,          -- YYYY-MM-DD, the user's local day
    type      TEXT NOT NULL DEFAULT 'message',  -- the kind of message, or 'wakeups'
    sent_at   TEXT,
    wakeups   TEXT,   -- [{hour, decision, reason}]
    messages  TEXT
);
-- "how many did I already send today", asked on every wake-up.
CREATE INDEX IF NOT EXISTS idx_proactive_log_day
    ON proactive_log (user_id, date);

-- Every change the agent made to its own understanding of the user, so a bad
-- one can be found and undone.
CREATE TABLE IF NOT EXISTS profile_history (
    id               TEXT NOT NULL PRIMARY KEY,
    user_id          TEXT NOT NULL,
    changed_at       TEXT,
    source           TEXT,    -- weekly_auto_tune | agent_edit | user_request
    sample_size_days INTEGER,
    before           TEXT,
    after            TEXT,
    -- The tuner explains itself: what it concluded, how sure it was, and what
    -- the arithmetic had proposed before the model reviewed it.
    reasoning        TEXT,
    confidence       TEXT,   -- high | medium | low
    proposed         TEXT
);
CREATE INDEX IF NOT EXISTS idx_profile_history_user
    ON profile_history (user_id, changed_at DESC);

-- Things the agent promised to do later.
CREATE TABLE IF NOT EXISTS agent_queue (
    id           TEXT NOT NULL PRIMARY KEY,
    user_id      TEXT,
    title        TEXT NOT NULL,
    prompt       TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending | done | cancelled
    run_at       TEXT,
    created_at   TEXT,
    completed_at TEXT
);
-- The hourly tick asks for pending work that is due.
CREATE INDEX IF NOT EXISTS idx_agent_queue_due
    ON agent_queue (status, run_at);

-- Skills the agent wrote for itself. The ones shipped in the image live as
-- files under skills/ and are not stored here; on a name collision the stored
-- one wins, which is what lets the agent revise a built-in without editing it.
CREATE TABLE IF NOT EXISTS skills (
    name       TEXT NOT NULL PRIMARY KEY,   -- slug
    content    TEXT NOT NULL,
    reason     TEXT,
    created_at TEXT,
    updated_at TEXT
);

-- OAuth state for MCP servers, one row per user and server. Holds refresh
-- tokens, so it is the one table whose loss means going through consent again.
CREATE TABLE IF NOT EXISTS mcp_auth (
    user_id     TEXT NOT NULL,
    server      TEXT NOT NULL,
    tokens      TEXT,
    client_info TEXT,
    updated_at  TEXT,
    PRIMARY KEY (user_id, server)
);

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT NOT NULL PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Columns holding JSON. Encoded on write and decoded on read, so a caller
# passes and receives real lists and dicts and never sees the serialisation.
JSON_COLUMNS: dict[str, set[str]] = {
    "history": {"actions", "topics", "themes_mentioned"},
    "experiments": {"history"},
    "principles": {"applies_when", "personality_relevance"},
    "proactive_log": {"wakeups", "messages"},
    "profile_history": {"before", "after", "proposed"},
    "mcp_auth": {"tokens", "client_info"},
}

_journal_warned: set[str] = set()


def apply_journal_mode(conn: sqlite3.Connection, db_label: str = "aileon.db") -> str:
    """
    Choose a journal mode that is safe on this build and this filesystem.

    WAL is the better mode when it works, but it is not unconditionally safe:

    - On network and some FUSE filesystems SQLite cannot take the shared-memory
      locks WAL needs, and the PRAGMA raises rather than degrading.
    - SQLite builds before 3.51.3 carry a WAL-reset corruption bug. A database
      already in WAL is left there, because downgrading under another open
      connection is worse than the risk; a fresh one is kept in DELETE.

    Returns the mode actually in force.
    """
    try:
        current = conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
    except sqlite3.Error:
        return "unknown"

    version = tuple(int(p) for p in sqlite3.sqlite_version.split(".")[:3])
    if version < _WAL_UNSAFE_BELOW and current != "wal":
        if db_label not in _journal_warned:
            _journal_warned.add(db_label)
            logger.info(
                "%s: staying on %s — SQLite %s predates the WAL-reset fix",
                db_label, current, sqlite3.sqlite_version,
            )
        return current

    try:
        mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower()
        if mode != "wal" and db_label not in _journal_warned:
            _journal_warned.add(db_label)
            logger.warning("%s: WAL refused, running in %s", db_label, mode)
        return mode
    except sqlite3.Error as exc:
        if db_label not in _journal_warned:
            _journal_warned.add(db_label)
            logger.warning(
                "%s: WAL unavailable on this filesystem (%s), using DELETE",
                db_label, exc,
            )
        return current


class Database:
    """
    The SQLite file, its schema, and the connection everything shares.

    One connection behind a lock. The app is a single process, so there is
    nothing to contend with; the lock exists because FastAPI runs sync handlers
    in a thread pool.
    """

    def __init__(self, path: str = "data/aileon.db") -> None:
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=5)
        self._conn.row_factory = sqlite3.Row
        # Wait rather than fail when another writer holds the file, which
        # happens whenever the hourly tick overlaps an incoming message.
        self._conn.execute("PRAGMA busy_timeout=5000")
        self.journal_mode = apply_journal_mode(self._conn, os.path.basename(path))
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()
        logger.info(
            "Database ready at %s (schema v%d, journal=%s)",
            path, SCHEMA_VERSION, self.journal_mode,
        )

    def _migrate(self) -> None:
        """
        Bring an older file up to the current schema, additively.

        Every column named in SCHEMA that a table is missing gets added. Nothing
        is renamed or dropped: a column that is no longer read costs a few bytes,
        while a rename that half-applies costs the data in it.
        """
        wanted = _columns_from_schema(SCHEMA)
        for table, columns in wanted.items():
            try:
                have = {
                    r["name"] for r in
                    self._conn.execute(f"PRAGMA table_info({table})").fetchall()
                }
            except sqlite3.Error:
                continue
            if not have:
                continue
            for name, decl in columns.items():
                if name in have:
                    continue
                # A DEFAULT that is not constant cannot be added to an existing
                # table, and NOT NULL needs one, so both are dropped here; the
                # column is simply nullable on rows that predate it.
                simple = decl.split("DEFAULT")[0].replace("NOT NULL", "").strip()
                simple = simple.replace("PRIMARY KEY", "").strip() or "TEXT"
                logger.info("Adding column %s.%s (%s)", table, name, simple)
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {simple}")

        self._conn.execute(
            "INSERT INTO schema_meta (key, value) VALUES ('version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )

    # -- reading --------------------------------------------------------------

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        """Run a SELECT and return rows as dicts, JSON columns already decoded."""
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        table = _table_of(sql)
        return [_decode(dict(r), table) for r in rows]

    def query_one(self, sql: str, params: tuple = ()) -> Optional[dict]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def count(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return int(row[0]) if row else 0

    # -- writing --------------------------------------------------------------

    def execute(self, sql: str, params: tuple = ()) -> int:
        """Run one statement and commit. Returns rows affected."""
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.rowcount

    def update(self, table: str, data: dict, where: dict) -> int:
        """
        Patch an existing row. Returns the number of rows changed.

        Distinct from upsert on purpose. upsert attempts the INSERT first, and
        SQLite checks NOT NULL before it ever reaches the conflict clause, so
        patching two fields of an existing row fails on every required column
        the patch does not mention. Use upsert for a whole row and this for a
        change to one that is already there.
        """
        payload = _encode(data, table)
        sets = ", ".join(f"{c} = ?" for c in payload)
        conds = " AND ".join(f"{c} = ?" for c in where)
        return self.execute(
            f"UPDATE {table} SET {sets} WHERE {conds}",
            tuple(payload.values()) + tuple(where.values()),
        )

    def upsert(self, table: str, data: dict, keys: list[str]) -> None:
        """Insert a row, or update the one already holding those key columns."""
        payload = _encode(data, table)
        cols = list(payload)
        updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in keys)
        sql = (
            f"INSERT INTO {table} ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' for _ in cols)}) "
            + (
                f"ON CONFLICT({', '.join(keys)}) DO UPDATE SET {updates}"
                if updates else
                f"ON CONFLICT({', '.join(keys)}) DO NOTHING"
            )
        )
        with self._lock:
            self._conn.execute(sql, tuple(payload[c] for c in cols))
            self._conn.commit()

    def insert(self, table: str, data: dict) -> None:
        payload = _encode(data, table)
        cols = list(payload)
        with self._lock:
            self._conn.execute(
                f"INSERT INTO {table} ({', '.join(cols)}) "
                f"VALUES ({', '.join('?' for _ in cols)})",
                tuple(payload[c] for c in cols),
            )
            self._conn.commit()

    def transaction(self) -> "_Transaction":
        """Several writes that must all land or none."""
        return _Transaction(self._conn, self._lock)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __repr__(self) -> str:
        return f"<Database {self.path} journal={self.journal_mode}>"


class _Transaction:
    def __init__(self, conn: sqlite3.Connection, lock: "threading.RLock") -> None:
        self._conn = conn
        self._lock = lock

    def __enter__(self) -> sqlite3.Connection:
        self._lock.acquire()
        self._conn.execute("BEGIN")
        return self._conn

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self._lock.release()
        return False


def _columns_from_schema(sql: str) -> dict[str, dict[str, str]]:
    """Read the table and column declarations back out of SCHEMA."""
    import re

    tables: dict[str, dict[str, str]] = {}
    for match in re.finditer(
        r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\n\);", sql, re.S
    ):
        table, body = match.group(1), match.group(2)
        columns: dict[str, str] = {}
        for line in body.splitlines():
            line = line.split("--")[0].strip().rstrip(",")
            if not line or line.upper().startswith(("PRIMARY KEY", "FOREIGN KEY",
                                                    "UNIQUE", "CHECK")):
                continue
            parts = line.split(None, 1)
            if len(parts) == 2:
                columns[parts[0]] = parts[1]
        tables[table] = columns
    return tables


def _table_of(sql: str) -> str:
    """The table a SELECT reads from, so its JSON columns can be decoded."""
    lowered = sql.lower()
    if " from " not in lowered:
        return ""
    after = lowered.split(" from ", 1)[1].strip()
    return after.split()[0].strip("(),;") if after else ""


def _encode(data: dict, table: str) -> dict:
    json_cols = JSON_COLUMNS.get(table, set())
    out: dict[str, Any] = {}
    for key, value in data.items():
        if key in json_cols and not isinstance(value, (str, type(None))):
            out[key] = json.dumps(value, ensure_ascii=False)
        elif isinstance(value, bool):
            out[key] = int(value)
        else:
            out[key] = value
    return out


def _decode(row: dict, table: str) -> dict:
    for col in JSON_COLUMNS.get(table, set()):
        raw = row.get(col)
        if isinstance(raw, str):
            try:
                row[col] = json.loads(raw)
            except ValueError:
                pass
    return row
