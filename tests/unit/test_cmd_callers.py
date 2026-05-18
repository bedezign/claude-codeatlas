"""Tests for the 'callers' subcommand."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from codeatlas.explore_codebase import db
from codeatlas.explore_codebase.cli import cmd_callers


def _args(project: Path, symbol: str, *, json_out: bool = False):
    return argparse.Namespace(
        project_root=str(project),
        symbol=symbol,
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


def _insert_symbol(
    conn, file_id: int, name: str, kind: str = "function", line: int = 1
) -> int:
    cur = conn.execute(
        "INSERT INTO symbols (file_id, kind, name, scope, line) VALUES (?, ?, ?, ?, ?)",
        (file_id, kind, name, None, line),
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
# Basic callers
# ---------------------------------------------------------------------------


def test_cmd_callers_returns_zero(project: Path):
    conn = _open_db(project)
    fa = _insert_file(conn, "caller.py")
    fb = _insert_file(conn, "callee.py")
    s_caller = _insert_symbol(conn, fa, "caller_fn", line=5)
    s_callee = _insert_symbol(conn, fb, "target_fn", line=10)
    _insert_edge(conn, s_caller, s_callee, "calls")
    conn.close()

    rc = cmd_callers(_args(project, "target_fn"))
    assert rc == 0


def test_cmd_callers_text_format(project: Path, capsys):
    conn = _open_db(project)
    fa = _insert_file(conn, "caller.py")
    fb = _insert_file(conn, "callee.py")
    s_caller = _insert_symbol(conn, fa, "caller_fn", line=7)
    s_callee = _insert_symbol(conn, fb, "target_fn", line=10)
    _insert_edge(conn, s_caller, s_callee, "calls")
    conn.close()

    cmd_callers(_args(project, "target_fn"))
    out = capsys.readouterr().out

    assert "caller.py:7" in out
    assert "caller_fn" in out
    assert "target_fn" in out
    assert "→" in out


def test_cmd_callers_no_callers_empty_output(project: Path, capsys):
    conn = _open_db(project)
    fid = _insert_file(conn, "a.py")
    _insert_symbol(conn, fid, "leaf_fn")
    conn.close()

    rc = cmd_callers(_args(project, "leaf_fn"))
    out = capsys.readouterr().out

    assert rc == 0
    assert out.strip() == ""


def test_cmd_callers_empty_db_returns_zero_empty(project: Path, capsys):
    rc = cmd_callers(_args(project, "anything"))
    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip() == ""


def test_cmd_callers_imports_edge_excluded(project: Path, capsys):
    conn = _open_db(project)
    fa = _insert_file(conn, "a.py")
    fb = _insert_file(conn, "b.py")
    s_a = _insert_symbol(conn, fa, "importer")
    s_b = _insert_symbol(conn, fb, "imported_fn")
    _insert_edge(conn, s_a, s_b, "imports")
    conn.close()

    cmd_callers(_args(project, "imported_fn"))
    out = capsys.readouterr().out
    assert out.strip() == ""


def test_cmd_callers_ambiguous_symbol_aggregates(project: Path, capsys):
    """'run' in two modules — all incoming calls shown."""
    fa = _insert_file(conn := _open_db(project), "worker.py")
    fb = _insert_file(conn, "task.py")
    fc = _insert_file(conn, "main.py")
    s_run_a = _insert_symbol(conn, fa, "run", line=10)
    s_run_b = _insert_symbol(conn, fb, "run", line=20)
    s_dispatch = _insert_symbol(conn, fc, "dispatch", line=1)
    _insert_edge(conn, s_dispatch, s_run_a, "calls")
    _insert_edge(conn, s_dispatch, s_run_b, "calls")
    conn.close()

    cmd_callers(_args(project, "run"))
    out = capsys.readouterr().out

    assert out.count("dispatch") == 2
    assert out.count("→ run") == 2


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


def test_cmd_callers_json_parseable(project: Path, capsys):
    conn = _open_db(project)
    fa = _insert_file(conn, "a.py")
    fb = _insert_file(conn, "b.py")
    s_a = _insert_symbol(conn, fa, "caller_fn", line=3)
    s_b = _insert_symbol(conn, fb, "target_fn", line=8)
    _insert_edge(conn, s_a, s_b, "calls")
    conn.close()

    cmd_callers(_args(project, "target_fn", json_out=True))
    out = capsys.readouterr().out
    data = json.loads(out)

    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["caller_name"] == "caller_fn"
    assert data[0]["symbol_name"] == "target_fn"
    assert data[0]["caller_path"] == "a.py"
    assert data[0]["caller_line"] == 3


def test_cmd_callers_json_empty_result(project: Path, capsys):
    cmd_callers(_args(project, "nothing", json_out=True))
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data == []
