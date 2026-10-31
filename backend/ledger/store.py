"""SQLite storage for the ledger: one append-only table of hash-chained records.

The API server and the MCP server can share one database file. Writes take an IMMEDIATE
transaction, so reading the chain head and appending after it can't interleave across
processes. Triggers refuse UPDATE and DELETE; anyone who edits the file directly anyway
breaks the hash chain, and verify_chain() reports where.

Postgres (for Render) is Day 11 work; only sqlite:/// URLs are accepted until then.
"""
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from backend.config import get_settings

_ROOT = Path(__file__).resolve().parents[2]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    seq INTEGER PRIMARY KEY,        -- position in the chain, from 1
    receipt_id TEXT NOT NULL,
    kind TEXT NOT NULL,             -- 'intent', or the event kind
    order_id TEXT,                  -- PayPal order id, for find_by_order
    body TEXT NOT NULL,             -- canonical JSON of the hashed record (includes prev_hash)
    hash TEXT NOT NULL UNIQUE
);
CREATE UNIQUE INDEX IF NOT EXISTS one_intent_per_receipt ON records (receipt_id) WHERE kind = 'intent';
CREATE INDEX IF NOT EXISTS records_by_receipt ON records (receipt_id);
CREATE INDEX IF NOT EXISTS records_by_order ON records (order_id);
CREATE TRIGGER IF NOT EXISTS records_no_update BEFORE UPDATE ON records
    BEGIN SELECT RAISE(ABORT, 'ledger records are append-only'); END;
CREATE TRIGGER IF NOT EXISTS records_no_delete BEFORE DELETE ON records
    BEGIN SELECT RAISE(ABORT, 'ledger records are append-only'); END;
"""

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
_url: str | None = None


def sqlite_path(url: str) -> str:
    """sqlite:///./x.db -> <repo root>/x.db, so the API and MCP server agree whatever their cwd."""
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise ValueError(f"Ledger supports sqlite:/// URLs only for now, got {url.split(':', 1)[0]}:")
    path = url[len(prefix):]
    if path == ":memory:":
        return path
    p = Path(path)
    return str(p if p.is_absolute() else (_ROOT / p).resolve())


def configure(url: str | None) -> None:
    """Point the ledger at a database (tests use sqlite:///:memory:). None = DATABASE_URL."""
    global _conn, _url
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn, _url = None, url


def connection() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            path = sqlite_path(_url or get_settings().database_url)
            # Autocommit mode; writes open their own IMMEDIATE transaction.
            _conn = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            if path != ":memory:":
                _conn.execute("PRAGMA journal_mode=WAL")
            _conn.executescript(_SCHEMA)
        return _conn


@contextmanager
def write() -> Iterator[sqlite3.Connection]:
    with _lock:
        conn = connection()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")


def read(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    with _lock:
        return connection().execute(sql, params).fetchall()


def clear() -> None:
    """For tests: drop everything and start an empty chain."""
    with _lock:
        conn = connection()
        conn.executescript("DROP TABLE IF EXISTS records;")
        conn.executescript(_SCHEMA)
