"""Tests for the 'summary' subcommand."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from codeatlas.explore_codebase import db
from codeatlas.explore_codebase.cli import cmd_summary


def _args(project: Path, *, json_out: bool = False):
    return argparse.Namespace(
        project_root=str(project),
        json=json_out,
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    db_path = tmp_path / ".claude/codeatlas/codebase.db"
    conn = db.init(db_path)
    conn.close()
    return tmp_path


def _open_db(project: Path):
    return db.init(project / ".claude/codeatlas/codebase.db")


def _insert_file(conn, path: str, ts: str = "2026-05-15T10:00:00") -> int:
    cur = conn.execute(
        "INSERT INTO files (path, sha, language, last_parsed_at) VALUES (?, ?, ?, ?)",
        (path, "abc", "python", ts),
    )
    conn.commit()
    return cur.lastrowid


def _insert_symbol(conn, file_id: int, name: str) -> int:
    cur = conn.execute(
        "INSERT INTO symbols (file_id, kind, name, scope, line) VALUES (?, ?, ?, ?, ?)",
        (file_id, "function", name, None, 1),
    )
    conn.commit()
    return cur.lastrowid


def _insert_edge(conn, src_id: int, dst_id: int, kind: str = "calls") -> None:
    conn.execute(
        "INSERT INTO edges (src_id, dst_id, kind) VALUES (?, ?, ?)",
        (src_id, dst_id, kind),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Basic summary
# ---------------------------------------------------------------------------


def test_cmd_summary_returns_zero(project: Path):
    rc = cmd_summary(_args(project))
    assert rc == 0


def test_cmd_summary_empty_db_no_crash(project: Path, capsys):
    rc = cmd_summary(_args(project))
    out = capsys.readouterr().out
    assert rc == 0
    assert "Files:" in out
    assert "Symbols:" in out
    assert "Edges:" in out


def test_cmd_summary_text_counts(project: Path, capsys):
    conn = _open_db(project)
    fa = _insert_file(conn, "a.py", ts="2026-05-15T10:00:00")
    fb = _insert_file(conn, "b.py", ts="2026-05-14T09:00:00")
    s_a = _insert_symbol(conn, fa, "fn_a")
    s_b = _insert_symbol(conn, fb, "fn_b")
    _insert_edge(conn, s_a, s_b, "calls")
    _insert_edge(conn, s_a, s_b, "imports")
    conn.execute(
        "INSERT INTO dead_symbols (file, line, kind, name, confidence) VALUES (?, ?, ?, ?, ?)",
        ("a.py", 5, "function", "dead_fn", 60),
    )
    db.upsert_narrative(
        conn,
        topic="arch",
        scope_id="",
        content="x",
        depends_on="[]",
        generated_at="2026-01-01",
    )
    conn.commit()
    conn.close()

    cmd_summary(_args(project))
    out = capsys.readouterr().out

    assert "Files: 2" in out
    assert "Symbols: 2" in out
    assert "Edges: 2" in out
    assert "calls: 1" in out
    assert "imports: 1" in out
    assert "Dead symbols: 1" in out
    assert "Narratives: 1" in out


def test_cmd_summary_last_parsed_shown(project: Path, capsys):
    conn = _open_db(project)
    _insert_file(conn, "a.py", ts="2026-05-15T12:34:56")
    conn.close()

    cmd_summary(_args(project))
    out = capsys.readouterr().out

    assert "2026-05-15T12:34:56" in out
    assert "Last parsed:" in out


def test_cmd_summary_empty_db_no_last_parsed(project: Path, capsys):
    cmd_summary(_args(project))
    out = capsys.readouterr().out

    assert (
        "Last parsed: —" in out
        or "Last parsed: N/A" in out
        or "Last parsed: none" in out.lower()
    )


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


def test_cmd_summary_json_parseable(project: Path, capsys):
    cmd_summary(_args(project, json_out=True))
    out = capsys.readouterr().out
    data = json.loads(out)

    assert isinstance(data, dict)
    assert "files" in data
    assert "symbols" in data
    assert "edges" in data
    assert "calls" in data
    assert "imports" in data
    assert "dead_symbols" in data
    assert "narratives" in data
    assert "last_parsed_at" in data


def test_cmd_summary_json_empty_db_zeros(project: Path, capsys):
    cmd_summary(_args(project, json_out=True))
    out = capsys.readouterr().out
    data = json.loads(out)

    assert data["files"] == 0
    assert data["symbols"] == 0
    assert data["edges"] == 0
    assert data["last_parsed_at"] is None


def test_cmd_summary_json_counts(project: Path, capsys):
    conn = _open_db(project)
    fa = _insert_file(conn, "a.py", ts="2026-05-15T10:00:00")
    fb = _insert_file(conn, "b.py", ts="2026-05-14T09:00:00")
    s_a = _insert_symbol(conn, fa, "fn_a")
    s_b = _insert_symbol(conn, fb, "fn_b")
    _insert_edge(conn, s_a, s_b, "calls")
    conn.close()

    cmd_summary(_args(project, json_out=True))
    out = capsys.readouterr().out
    data = json.loads(out)

    assert data["files"] == 2
    assert data["symbols"] == 2
    assert data["edges"] == 1
    assert data["calls"] == 1
    assert data["imports"] == 0
    assert data["last_parsed_at"] == "2026-05-15T10:00:00"
