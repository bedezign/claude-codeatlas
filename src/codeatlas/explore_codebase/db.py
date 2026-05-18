"""Phase 1: SQLite schema + startup logic for explore-codebase."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

SCHEMA_VERSION = "1"

logger = logging.getLogger(__name__)

_TABLES = {
    # meta — generic key/value store.
    # Known keys: schema_version (str — current schema generation).
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
        "line INTEGER,"
        "line_end INTEGER,"
        "loc INTEGER"
        ")"
    ),
    "edges": (
        "CREATE TABLE IF NOT EXISTS edges ("
        "src_id INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,"
        "dst_id INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,"
        "kind TEXT NOT NULL"
        ")"
    ),
    # Renamed from dead_code. Stores vulture findings (unused symbols).
    "dead_symbols": (
        "CREATE TABLE IF NOT EXISTS dead_symbols ("
        "file TEXT NOT NULL,"
        "line INTEGER,"
        "kind TEXT NOT NULL,"
        "name TEXT NOT NULL,"
        "confidence INTEGER NOT NULL"
        ")"
    ),
    # Composite primary key (topic, scope_id) allows per-file scoped narratives.
    # scope_id uses '' (empty string sentinel) for single-scope topics like
    # 'architecture', 'modules', 'data', etc. Never store NULL — SQLite treats
    # each NULL row as unique in a PRIMARY KEY, making duplicate-prevention impossible.
    "narratives": (
        "CREATE TABLE IF NOT EXISTS narratives ("
        "topic TEXT NOT NULL,"
        "scope_id TEXT NOT NULL,"
        "content TEXT NOT NULL,"
        "depends_on TEXT NOT NULL,"
        "generated_at TEXT NOT NULL,"
        "PRIMARY KEY (topic, scope_id)"
        ")"
    ),
}

# Non-unique indexes created after tables. Keys are index names (used for
# idempotent IF NOT EXISTS creation), values are CREATE INDEX DDL.
_INDEXES = {
    "idx_symbols_name": (
        "CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name)"
    ),
    "idx_symbols_file_id": (
        "CREATE INDEX IF NOT EXISTS idx_symbols_file_id ON symbols(file_id)"
    ),
    "idx_edges_src_id": (
        "CREATE INDEX IF NOT EXISTS idx_edges_src_id ON edges(src_id)"
    ),
    "idx_edges_dst_id": (
        "CREATE INDEX IF NOT EXISTS idx_edges_dst_id ON edges(dst_id)"
    ),
    "idx_edges_kind": ("CREATE INDEX IF NOT EXISTS idx_edges_kind ON edges(kind)"),
}


def _ensure_companion_dirs(base: Path) -> None:
    for sub in ("maps", "context", "notes"):
        (base / sub).mkdir(parents=True, exist_ok=True)


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 3000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")  # noqa: S608


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
    for ddl in _INDEXES.values():
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
    scope_id: str = "",
    content: str,
    depends_on: str,
    generated_at: str,
) -> None:
    """Insert or replace a narrative row keyed by (topic, scope_id).

    scope_id defaults to '' (empty-string sentinel) for single-scope topics
    such as 'architecture', 'modules', 'data', etc. Pass an explicit
    scope_id (e.g. 'src/pkg_a') for per-module narratives.
    """
    conn.execute(
        "INSERT OR REPLACE INTO narratives "
        "(topic, scope_id, content, depends_on, generated_at) VALUES (?, ?, ?, ?, ?)",
        (topic, scope_id, content, depends_on, generated_at),
    )
    conn.commit()
