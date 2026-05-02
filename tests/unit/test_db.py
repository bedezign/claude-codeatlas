"""Tests for explore_codebase.db - Phase 1: schema + startup logic."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from codeatlas.explore_codebase import db


EXPECTED_TABLES = {"files", "symbols", "narratives", "edges", "dead_code", "meta"}
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
            "INSERT INTO narratives (topic, content, depends_on, generated_at) VALUES (?, ?, ?, ?)",
            ("architecture", "v1", "[]", "2026-01-01T00:00:00"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO narratives (topic, content, depends_on, generated_at) VALUES (?, ?, ?, ?)",
                ("architecture", "v2", "[]", "2026-01-02T00:00:00"),
            )
    finally:
        conn.close()
