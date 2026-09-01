"""
A document store backed by a single SQLite file.

Exposes the slice of the Firestore client API the services actually use, so
swapping backends changes one line in main.py and nothing else:

    collection(name)                 -> CollectionRef
      .document(id)                  -> DocumentRef
      .add(dict)                     -> (timestamp, DocumentRef)
      .where(field, op, value)       -> _Query      (chainable)
      .order_by(field, direction=)   -> _Query      (chainable)
      .limit(n)                      -> _Query      (chainable)
      .stream()                      -> iterator of DocumentSnapshot

    DocumentRef.get() / .set(data, merge=) / .update(data) / .delete() / .id
    DocumentSnapshot.id / .exists / .to_dict()

Documents are stored as JSON in one table, keyed by (collection, doc_id).
Filtering and sorting happen in Python after loading the collection. That is
the right trade at this scale — a personal assistant holds thousands of rows,
not millions — and it removes the composite-index problem Firestore imposes on
any query that mixes an equality with an inequality.

Storing a value Firestore would accept but JSON would not (a datetime, say)
raises here rather than being silently coerced, so the two backends cannot
drift apart unnoticed.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import uuid
from datetime import date, datetime
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    collection TEXT NOT NULL,
    doc_id     TEXT NOT NULL,
    data       TEXT NOT NULL,
    PRIMARY KEY (collection, doc_id)
);
CREATE INDEX IF NOT EXISTS idx_documents_collection ON documents(collection);
"""


class DocumentNotFound(Exception):
    """Raised by update() on a missing document, matching Firestore."""


# ── Value handling ───────────────────────────────────────────────────────────


def _encode(data: dict) -> str:
    """Serialise a document, converting the datetime types Firestore accepts."""

    def default(obj: Any) -> Any:
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        raise TypeError(
            f"{type(obj).__name__} is not storable. Convert it to a string, "
            f"number, bool, list or dict before writing."
        )

    return json.dumps(data, default=default, ensure_ascii=False)


def _field(data: dict, path: str) -> Any:
    """Read a field, following dotted paths the way Firestore does."""
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _set_field(data: dict, path: str, value: Any) -> None:
    """Write a field, creating intermediate maps for dotted paths."""
    parts = path.split(".")
    current = data
    for part in parts[:-1]:
        nxt = current.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            current[part] = nxt
        current = nxt
    current[parts[-1]] = value


def _merge(base: dict, incoming: dict) -> dict:
    """
    Firestore's `merge=True`: maps merge recursively, everything else is
    replaced outright — including lists.
    """
    result = dict(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def _compare(left: Any, op: str, right: Any) -> bool:
    """Apply a where() operator, treating incomparable types as no match."""
    try:
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        if op == "in":
            return left in (right or [])
        if op == "not-in":
            return left not in (right or [])
        if op == "array_contains":
            return isinstance(left, list) and right in left
        if op == "array_contains_any":
            return isinstance(left, list) and any(v in left for v in (right or []))
        # Ordering comparisons: a missing field never matches, which is how
        # Firestore behaves too — documents without the field are not returned.
        if left is None or right is None:
            return False
        if op == "<":
            return left < right
        if op == "<=":
            return left <= right
        if op == ">":
            return left > right
        if op == ">=":
            return left >= right
    except TypeError:
        return False
    raise ValueError(f"Unsupported where() operator: {op!r}")


def _sort_key(value: Any) -> tuple:
    """
    Order mixed types without raising.

    Values are grouped by type first — Firestore does the same — so a None
    never has to be compared against a string.
    """
    if value is None:
        return (0, "")
    if isinstance(value, bool):
        return (1, int(value))
    if isinstance(value, (int, float)):
        return (2, value)
    if isinstance(value, str):
        return (3, value)
    return (4, str(value))


# ── Snapshots and references ─────────────────────────────────────────────────


class DocumentSnapshot:
    """The result of a read. Mirrors Firestore's snapshot."""

    __slots__ = ("id", "_data")

    def __init__(self, doc_id: str, data: Optional[dict]) -> None:
        self.id = doc_id
        self._data = data

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> Optional[dict]:
        return dict(self._data) if self._data is not None else None

    def get(self, path: str, default: Any = None) -> Any:
        if self._data is None:
            return default
        value = _field(self._data, path)
        return default if value is None else value

    def __repr__(self) -> str:
        return f"<DocumentSnapshot {self.id} exists={self.exists}>"


class DocumentRef:
    """A pointer to one document."""

    __slots__ = ("_store", "_collection", "id")

    def __init__(self, store: "SqliteStore", collection: str, doc_id: str) -> None:
        self._store = store
        self._collection = collection
        self.id = doc_id

    def get(self) -> DocumentSnapshot:
        return DocumentSnapshot(self.id, self._store._read(self._collection, self.id))

    def set(self, data: dict, merge: bool = False) -> None:
        if merge:
            existing = self._store._read(self._collection, self.id) or {}
            data = _merge(existing, data)
        self._store._write(self._collection, self.id, data)

    def update(self, data: dict) -> None:
        """Patch fields. Raises when the document does not exist, like Firestore."""
        existing = self._store._read(self._collection, self.id)
        if existing is None:
            raise DocumentNotFound(
                f"No document to update at {self._collection}/{self.id}"
            )
        for path, value in data.items():
            _set_field(existing, path, value)
        self._store._write(self._collection, self.id, existing)

    def delete(self) -> None:
        self._store._delete(self._collection, self.id)

    def __repr__(self) -> str:
        return f"<DocumentRef {self._collection}/{self.id}>"


class _Query:
    """A chainable, lazily-evaluated query. Nothing runs until stream()."""

    __slots__ = ("_store", "_collection", "_filters", "_order", "_limit")

    def __init__(
        self,
        store: "SqliteStore",
        collection: str,
        filters: Optional[list] = None,
        order: Optional[list] = None,
        limit: Optional[int] = None,
    ) -> None:
        self._store = store
        self._collection = collection
        self._filters = filters or []
        self._order = order or []
        self._limit = limit

    def _clone(self, **changes: Any) -> "_Query":
        return _Query(
            self._store,
            self._collection,
            changes.get("filters", list(self._filters)),
            changes.get("order", list(self._order)),
            changes.get("limit", self._limit),
        )

    def where(self, field: str, op: str, value: Any) -> "_Query":
        return self._clone(filters=self._filters + [(field, op, value)])

    def order_by(self, field: str, direction: str = "ASCENDING") -> "_Query":
        descending = str(direction).upper().startswith("DESC")
        return self._clone(order=self._order + [(field, descending)])

    def limit(self, count: int) -> "_Query":
        return self._clone(limit=count)

    def stream(self) -> Iterator[DocumentSnapshot]:
        rows = self._store._read_collection(self._collection)

        for field, op, value in self._filters:
            rows = [r for r in rows if _compare(_field(r[1], field), op, value)]

        # Applied in reverse so the first order_by is the primary sort.
        for field, descending in reversed(self._order):
            rows.sort(key=lambda r: _sort_key(_field(r[1], field)), reverse=descending)

        if self._limit is not None:
            rows = rows[: self._limit]

        return iter([DocumentSnapshot(doc_id, data) for doc_id, data in rows])

    def get(self) -> list:
        return list(self.stream())


class CollectionRef(_Query):
    """A collection. Queryable like any other query, and also writable."""

    __slots__ = ()

    def __init__(self, store: "SqliteStore", collection: str) -> None:
        super().__init__(store, collection)

    def document(self, doc_id: Optional[str] = None) -> DocumentRef:
        return DocumentRef(self._store, self._collection, doc_id or _new_id())

    def add(self, data: dict) -> tuple:
        """Create a document with a generated id. Returns (timestamp, ref)."""
        ref = DocumentRef(self._store, self._collection, _new_id())
        ref.set(data)
        return (datetime.now(), ref)

    def __repr__(self) -> str:
        return f"<CollectionRef {self._collection}>"


def _new_id() -> str:
    """A 20-character id, the same length Firestore generates."""
    return uuid.uuid4().hex[:20]


# ── The store ────────────────────────────────────────────────────────────────


class SqliteStore:
    """
    Firestore-shaped façade over a SQLite file.

    One connection guarded by a lock. FastAPI runs sync handlers in a thread
    pool, so the connection is shared across threads deliberately and every
    access is serialised; at this volume the lock is never the bottleneck.
    """

    def __init__(self, path: str = "data/aileon.db") -> None:
        self._path = path
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)

        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=5)
        self._conn.executescript(_SCHEMA)
        # Wait rather than fail when another writer holds the file.
        self._conn.execute("PRAGMA busy_timeout=5000")
        # WAL keeps reads from blocking on a write, but it is not
        # unconditionally safe: network filesystems cannot take its locks, and
        # SQLite builds before 3.51.3 carry a WAL-reset corruption bug. The
        # shared helper decides, and leaves a database that is already in WAL
        # alone rather than downgrading it under other open connections.
        from app.storage.schema import apply_journal_mode
        self.journal_mode = apply_journal_mode(self._conn, os.path.basename(path))
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.commit()

    # -- public API -----------------------------------------------------------

    def collection(self, name: str) -> CollectionRef:
        return CollectionRef(self, name)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def collections(self) -> list[str]:
        """Every collection that currently holds at least one document."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT collection FROM documents ORDER BY collection"
            ).fetchall()
        return [r[0] for r in rows]

    # -- internals ------------------------------------------------------------

    def _read(self, collection: str, doc_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM documents WHERE collection = ? AND doc_id = ?",
                (collection, doc_id),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def _read_collection(self, collection: str) -> list[tuple[str, dict]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT doc_id, data FROM documents WHERE collection = ?",
                (collection,),
            ).fetchall()
        return [(doc_id, json.loads(data)) for doc_id, data in rows]

    def _write(self, collection: str, doc_id: str, data: dict) -> None:
        payload = _encode(data)
        with self._lock:
            self._conn.execute(
                "INSERT INTO documents (collection, doc_id, data) VALUES (?, ?, ?) "
                "ON CONFLICT(collection, doc_id) DO UPDATE SET data = excluded.data",
                (collection, doc_id, payload),
            )
            self._conn.commit()

    def _delete(self, collection: str, doc_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM documents WHERE collection = ? AND doc_id = ?",
                (collection, doc_id),
            )
            self._conn.commit()

    def __repr__(self) -> str:
        return f"<SqliteStore {self._path}>"
