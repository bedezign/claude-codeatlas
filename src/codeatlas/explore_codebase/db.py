"""Phase 1: SQLite schema + startup logic for explore-codebase."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

SCHEMA_VERSION = "1"

logger = logging.getLogger(__name__)

_TABLES = {
    "meta": (
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)"
    ),
    "files": (
        "CREATE TABLE IF NOT EXISTS files ("
        "id INTEGER PRIMARY KEY,"
        "path TEXT UNIQUE NOT NULL,"
        "sha TEXT NOT NULL,"
        "language TEXT,"
        "last_parsed_at TEXT NOT NULL"
        ")"
    ),
    "symbols": (
        "CREATE TABLE IF NOT EXISTS symbols ("
        "id INTEGER PRIMARY KEY,"
        "file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,"
        "kind TEXT NOT NULL,"
        "name TEXT NOT NULL,"
        "scope TEXT,"
        "line INTEGER"
        ")"
    ),
    "edges": (
        "CREATE TABLE IF NOT EXISTS edges ("
        "src_id INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,"
        "dst_id INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,"
        "kind TEXT NOT NULL"
        ")"
    ),
    "dead_code": (
        "CREATE TABLE IF NOT EXISTS dead_code ("
        "file TEXT NOT NULL,"
        "line INTEGER,"
        "kind TEXT NOT NULL,"
        "name TEXT NOT NULL,"
        "confidence INTEGER NOT NULL"
        ")"
    ),
    "narratives": (
        "CREATE TABLE IF NOT EXISTS narratives ("
        "topic TEXT PRIMARY KEY,"
        "content TEXT NOT NULL,"
        "depends_on TEXT NOT NULL,"
        "generated_at TEXT NOT NULL"
        ")"
    ),
}


def _ensure_companion_dirs(base: Path) -> None:
    for sub in ("maps", "context", "notes"):
        (base / sub).mkdir(parents=True, exist_ok=True)


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 3000")
    conn.execute("PRAGMA foreign_keys = ON")


def _read_schema_version(conn: sqlite3.Connection) -> str | None:
    has_meta = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
    ).fetchone()
    if not has_meta:
        return None
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    return row[0] if row else None


def _drop_all_tables(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    for (name,) in rows:
        if name.startswith("sqlite_"):
            continue
        # `name` comes from sqlite_master, not user input — f-string is safe here.
        conn.execute(f"DROP TABLE IF EXISTS {name}")  # noqa: S608
    conn.commit()


def _create_schema(conn: sqlite3.Connection) -> None:
    for ddl in _TABLES.values():
        conn.execute(ddl)
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("schema_version", SCHEMA_VERSION),
    )
    conn.commit()


def init(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_companion_dirs(path.parent)

    conn = sqlite3.connect(str(path))
    _apply_pragmas(conn)

    current = _read_schema_version(conn)
    if current is None:
        _create_schema(conn)
    elif current != SCHEMA_VERSION:
        logger.warning(
            "Schema version mismatch (%s → %s): dropping all tables including narratives. "
            "Re-run the full workflow to rebuild.",
            current,
            SCHEMA_VERSION,
        )
        _drop_all_tables(conn)
        _create_schema(conn)
    return conn


def upsert_narrative(
    conn: sqlite3.Connection,
    *,
    topic: str,
    content: str,
    depends_on: str,
    generated_at: str,
) -> None:
    """Insert or replace a narrative row keyed by topic."""
    conn.execute(
        "INSERT OR REPLACE INTO narratives "
        "(topic, content, depends_on, generated_at) VALUES (?, ?, ?, ?)",
        (topic, content, depends_on, generated_at),
    )
    conn.commit()
