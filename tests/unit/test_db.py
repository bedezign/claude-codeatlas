"""Tests for explore_codebase.db - Phase 1: schema + startup logic."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from codeatlas.explore_codebase import db


EXPECTED_TABLES = {"files", "symbols", "narratives", "edges", "dead_symbols", "meta"}
SCHEMA_VERSION = "1"


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r[0] for r in rows}


def _journal_mode(conn: sqlite3.Connection) -> str:
    return conn.execute("PRAGMA journal_mode").fetchone()[0].lower()


def test_init_creates_db_file_at_path(tmp_path: Path):
    db_path = tmp_path / ".claude/codeatlas" / "codebase.db"
    assert not db_path.exists()

    conn = db.init(db_path)
    try:
        assert db_path.exists()
        assert db_path.is_file()
    finally:
        conn.close()


def test_init_returns_connection(tmp_path: Path):
    db_path = tmp_path / "codebase.db"
    conn = db.init(db_path)
    try:
        assert isinstance(conn, sqlite3.Connection)
    finally:
        conn.close()


def test_init_applies_wal_journal_mode(tmp_path: Path):
    db_path = tmp_path / "codebase.db"
    conn = db.init(db_path)
    try:
        assert _journal_mode(conn) == "wal"
    finally:
        conn.close()


def test_init_creates_all_schema_tables(tmp_path: Path):
    db_path = tmp_path / "codebase.db"
    conn = db.init(db_path)
    try:
        tables = _table_names(conn)
        assert EXPECTED_TABLES.issubset(tables), (
            f"missing tables: {EXPECTED_TABLES - tables}"
        )
    finally:
        conn.close()


def test_init_writes_schema_version_meta_row(tmp_path: Path):
    db_path = tmp_path / "codebase.db"
    conn = db.init(db_path)
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        assert row is not None
        assert row[0] == SCHEMA_VERSION
    finally:
        conn.close()


def test_init_idempotent_on_second_call_with_matching_version(tmp_path: Path):
    db_path = tmp_path / "codebase.db"
    conn = db.init(db_path)
    try:
        conn.execute(
            "INSERT INTO files (path, sha, language, last_parsed_at) VALUES (?, ?, ?, ?)",
            ("a.py", "deadbeef", "python", "2026-01-01T00:00:00"),
        )
        conn.commit()
    finally:
        conn.close()

    conn2 = db.init(db_path)
    try:
        rows = conn2.execute("SELECT path FROM files").fetchall()
        assert [r[0] for r in rows] == ["a.py"], "data lost on idempotent re-init"
        version = conn2.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        assert version == SCHEMA_VERSION
    finally:
        conn2.close()


def test_init_drops_and_rebuilds_on_version_mismatch(tmp_path: Path):
    db_path = tmp_path / "codebase.db"
    conn = db.init(db_path)
    try:
        conn.execute(
            "INSERT INTO files (path, sha, language, last_parsed_at) VALUES (?, ?, ?, ?)",
            ("old.py", "stale", "python", "2026-01-01T00:00:00"),
        )
        conn.execute("UPDATE meta SET value = '0' WHERE key = 'schema_version'")
        conn.commit()
    finally:
        conn.close()

    conn2 = db.init(db_path)
    try:
        rows = conn2.execute("SELECT path FROM files").fetchall()
        assert rows == [], "version mismatch should drop and rebuild (data wiped)"

        tables = _table_names(conn2)
        assert EXPECTED_TABLES.issubset(tables)

        version = conn2.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        assert version == SCHEMA_VERSION
    finally:
        conn2.close()


def test_init_creates_companion_directories(tmp_path: Path):
    db_path = tmp_path / ".claude/codeatlas" / "codebase.db"
    conn = db.init(db_path)
    try:
        base = db_path.parent
        assert (base / "maps").is_dir()
        assert (base / "context").is_dir()
        assert (base / "notes").is_dir()
    finally:
        conn.close()


def test_init_does_not_wipe_companion_dirs_on_version_mismatch(tmp_path: Path):
    db_path = tmp_path / ".claude/codeatlas" / "codebase.db"
    conn = db.init(db_path)
    try:
        conn.execute("UPDATE meta SET value = '0' WHERE key = 'schema_version'")
        conn.commit()
    finally:
        conn.close()

    base = db_path.parent
    sentinel = base / "notes" / "human.md"
    sentinel.write_text("hand-written")
    map_sentinel = base / "maps" / "stale.md"
    map_sentinel.write_text("old map")

    conn2 = db.init(db_path)
    try:
        assert sentinel.read_text() == "hand-written"
        assert map_sentinel.read_text() == "old map"
    finally:
        conn2.close()


def test_init_creates_parent_dirs_when_db_path_nested(tmp_path: Path):
    db_path = tmp_path / "deep" / "nested" / "codebase.db"
    assert not db_path.parent.exists()

    conn = db.init(db_path)
    try:
        assert db_path.exists()
        assert db_path.parent.is_dir()
    finally:
        conn.close()


def test_init_accepts_string_path(tmp_path: Path):
    db_path = tmp_path / "codebase.db"
    conn = db.init(str(db_path))
    try:
        assert db_path.exists()
    finally:
        conn.close()


def test_init_sets_user_version_pragma(tmp_path: Path):
    db_path = tmp_path / "codebase.db"
    conn = db.init(db_path)
    try:
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert user_version == 1
    finally:
        conn.close()


def test_files_table_unique_path_constraint(tmp_path: Path):
    db_path = tmp_path / "codebase.db"
    conn = db.init(db_path)
    try:
        conn.execute(
            "INSERT INTO files (path, sha, language, last_parsed_at) VALUES (?, ?, ?, ?)",
            ("a.py", "sha1", "python", "2026-01-01T00:00:00"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO files (path, sha, language, last_parsed_at) VALUES (?, ?, ?, ?)",
                ("a.py", "sha2", "python", "2026-01-02T00:00:00"),
            )
    finally:
        conn.close()


def test_symbols_cascade_delete_with_files(tmp_path: Path):
    db_path = tmp_path / "codebase.db"
    conn = db.init(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        cur = conn.execute(
            "INSERT INTO files (path, sha, language, last_parsed_at) VALUES (?, ?, ?, ?)",
            ("a.py", "sha", "python", "2026-01-01T00:00:00"),
        )
        file_id = cur.lastrowid
        conn.execute(
            "INSERT INTO symbols (file_id, kind, name, scope, line) VALUES (?, ?, ?, ?, ?)",
            (file_id, "function", "foo", None, 1),
        )
        conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
        conn.commit()
        remaining = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        assert remaining == 0
    finally:
        conn.close()


def test_narratives_topic_primary_key(tmp_path: Path):
    db_path = tmp_path / "codebase.db"
    conn = db.init(db_path)
    try:
        conn.execute(
            "INSERT INTO narratives (topic, scope_id, content, depends_on, generated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("architecture", "", "v1", "[]", "2026-01-01T00:00:00"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO narratives (topic, scope_id, content, depends_on, generated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("architecture", "", "v2", "[]", "2026-01-02T00:00:00"),
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Section 1: Indexes
# ---------------------------------------------------------------------------


def test_init_creates_required_indexes(tmp_path: Path):
    """All five idx_* indexes must be created after init."""
    db_path = tmp_path / "codebase.db"
    conn = db.init(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
        ).fetchall()
        index_names = {r[0] for r in rows}
    finally:
        conn.close()

    expected = {
        "idx_symbols_name",
        "idx_symbols_file_id",
        "idx_edges_src_id",
        "idx_edges_dst_id",
        "idx_edges_kind",
    }
    assert expected.issubset(index_names), f"missing indexes: {expected - index_names}"


# ---------------------------------------------------------------------------
# Section 2: line_end and loc columns
# ---------------------------------------------------------------------------


def test_symbols_table_has_line_end_and_loc_columns(tmp_path: Path):
    """symbols table must have line_end and loc columns."""
    db_path = tmp_path / "codebase.db"
    conn = db.init(db_path)
    try:
        col_names = {
            row[1] for row in conn.execute("PRAGMA table_info(symbols)").fetchall()
        }
    finally:
        conn.close()
    assert "line_end" in col_names
    assert "loc" in col_names


# ---------------------------------------------------------------------------
# Section 3: dead_symbols table (renamed from dead_code)
# ---------------------------------------------------------------------------


def test_dead_symbols_table_exists(tmp_path: Path):
    """dead_symbols table must exist; dead_code must not."""
    db_path = tmp_path / "codebase.db"
    conn = db.init(db_path)
    try:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        conn.close()
    assert "dead_symbols" in tables
    assert "dead_code" not in tables


# ---------------------------------------------------------------------------
# Section 4: narratives composite key (topic, scope_id)
# ---------------------------------------------------------------------------


def test_narratives_has_scope_id_column(tmp_path: Path):
    """narratives table must have a scope_id column."""
    db_path = tmp_path / "codebase.db"
    conn = db.init(db_path)
    try:
        col_names = {
            row[1] for row in conn.execute("PRAGMA table_info(narratives)").fetchall()
        }
    finally:
        conn.close()
    assert "scope_id" in col_names


def test_narratives_same_topic_different_scope_coexist(tmp_path: Path):
    """(topic, scope_id) composite key: same topic + different scope must coexist."""
    db_path = tmp_path / "codebase.db"
    conn = db.init(db_path)
    try:
        conn.execute(
            "INSERT INTO narratives (topic, scope_id, content, depends_on, generated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("architecture", "", "global", "[]", "2026-01-01T00:00:00"),
        )
        conn.execute(
            "INSERT INTO narratives (topic, scope_id, content, depends_on, generated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("architecture", "src/pkg_a", "scoped", "[]", "2026-01-01T00:00:00"),
        )
        conn.commit()
        rows = conn.execute(
            "SELECT scope_id FROM narratives WHERE topic = 'architecture'"
        ).fetchall()
    finally:
        conn.close()
    scopes = {r[0] for r in rows}
    assert "" in scopes
    assert "src/pkg_a" in scopes


def test_upsert_narrative_default_scope_id(tmp_path: Path):
    """upsert_narrative with no scope_id stores sentinel empty string."""
    db_path = tmp_path / "codebase.db"
    conn = db.init(db_path)
    try:
        db.upsert_narrative(
            conn,
            topic="modules",
            content="body",
            depends_on="[]",
            generated_at="2026-01-01T00:00:00",
        )
        row = conn.execute(
            "SELECT scope_id FROM narratives WHERE topic = 'modules'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == ""


def test_upsert_narrative_with_explicit_scope_id(tmp_path: Path):
    """upsert_narrative with scope_id stores it correctly."""
    db_path = tmp_path / "codebase.db"
    conn = db.init(db_path)
    try:
        db.upsert_narrative(
            conn,
            topic="context",
            scope_id="src/mypkg",
            content="scoped body",
            depends_on="[]",
            generated_at="2026-01-01T00:00:00",
        )
        row = conn.execute(
            "SELECT scope_id, content FROM narratives WHERE topic = 'context'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == "src/mypkg"
    assert row[1] == "scoped body"
