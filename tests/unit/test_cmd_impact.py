"""Tests for the 'impact' subcommand."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from codeatlas.explore_codebase import db
from codeatlas.explore_codebase.cli import cmd_impact


def _args(project: Path, file: str, *, depth: int = 2, json_out: bool = False):
    return argparse.Namespace(
        project_root=str(project),
        file=file,
        depth=depth,
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


def _insert_file(conn, path: str) -> int:
    cur = conn.execute(
        "INSERT INTO files (path, sha, language, last_parsed_at) VALUES (?, ?, ?, ?)",
        (path, "abc", "python", "2026-05-15T10:00:00"),
    )
    conn.commit()
    return cur.lastrowid


def _insert_symbol(conn, file_id: int, name: str, line: int = 1) -> int:
    cur = conn.execute(
        "INSERT INTO symbols (file_id, kind, name, scope, line) VALUES (?, ?, ?, ?, ?)",
        (file_id, "function", name, None, line),
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
# Basic impact
# ---------------------------------------------------------------------------


def test_cmd_impact_returns_zero(project: Path):
    conn = _open_db(project)
    _insert_file(conn, "a.py")
    conn.close()

    rc = cmd_impact(_args(project, "a.py"))
    assert rc == 0


def test_cmd_impact_file_not_in_db_returns_exit_1(project: Path, capsys):
    rc = cmd_impact(_args(project, "missing.py"))
    err = capsys.readouterr().err
    assert rc == 1
    assert "file not found in DB" in err
    assert "missing.py" in err


def test_cmd_impact_text_output_grouped_by_depth(project: Path, capsys):
    conn = _open_db(project)
    fa = _insert_file(conn, "a.py")
    fb = _insert_file(conn, "b.py")
    fc = _insert_file(conn, "c.py")
    s_a = _insert_symbol(conn, fa, "fn_a")
    s_b = _insert_symbol(conn, fb, "fn_b")
    s_c = _insert_symbol(conn, fc, "fn_c")
    _insert_edge(conn, s_a, s_b)
    _insert_edge(conn, s_b, s_c)
    conn.close()

    cmd_impact(_args(project, "a.py", depth=2))
    out = capsys.readouterr().out

    assert "Depth 1" in out
    assert "Depth 2" in out
    assert "b.py" in out
    assert "c.py" in out


def test_cmd_impact_empty_db_file_not_found(project: Path, capsys):
    rc = cmd_impact(_args(project, "any.py"))
    err = capsys.readouterr().err
    assert rc == 1
    assert "file not found in DB" in err


def test_cmd_impact_no_edges_empty_output(project: Path, capsys):
    conn = _open_db(project)
    _insert_file(conn, "isolated.py")
    conn.close()

    rc = cmd_impact(_args(project, "isolated.py"))
    out = capsys.readouterr().out

    assert rc == 0
    # No depth sections when no edges
    assert "Depth" not in out or out.strip() == ""


def test_cmd_impact_depth_flag(project: Path, capsys):
    conn = _open_db(project)
    fa = _insert_file(conn, "a.py")
    fb = _insert_file(conn, "b.py")
    fc = _insert_file(conn, "c.py")
    s_a = _insert_symbol(conn, fa, "fn_a")
    s_b = _insert_symbol(conn, fb, "fn_b")
    s_c = _insert_symbol(conn, fc, "fn_c")
    _insert_edge(conn, s_a, s_b)
    _insert_edge(conn, s_b, s_c)
    conn.close()

    cmd_impact(_args(project, "a.py", depth=1))
    out = capsys.readouterr().out

    assert "b.py" in out
    assert "c.py" not in out


def test_cmd_impact_error_message_to_stderr(project: Path, capsys):
    rc = cmd_impact(_args(project, "ghost.py"))
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.err.strip() != ""
    assert captured.out.strip() == ""


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


def test_cmd_impact_json_parseable(project: Path, capsys):
    conn = _open_db(project)
    fa = _insert_file(conn, "src.py")
    fb = _insert_file(conn, "dep.py")
    s_a = _insert_symbol(conn, fa, "fn_a")
    s_b = _insert_symbol(conn, fb, "fn_b")
    _insert_edge(conn, s_a, s_b)
    conn.close()

    rc = cmd_impact(_args(project, "src.py", json_out=True))
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)

    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["path"] == "dep.py"
    assert data[0]["depth"] == 1


def test_cmd_impact_json_file_not_found_exit_1(project: Path, capsys):
    rc = cmd_impact(_args(project, "missing.py", json_out=True))
    err = capsys.readouterr().err
    assert rc == 1
    assert "file not found in DB" in err


def test_cmd_impact_json_empty_list_when_no_edges(project: Path, capsys):
    conn = _open_db(project)
    _insert_file(conn, "lone.py")
    conn.close()

    rc = cmd_impact(_args(project, "lone.py", json_out=True))
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert data == []
