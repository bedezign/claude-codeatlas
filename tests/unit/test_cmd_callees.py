"""Tests for the 'callees' subcommand."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from codeatlas.explore_codebase import db
from codeatlas.explore_codebase.cli import cmd_callees


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
# Basic callees
# ---------------------------------------------------------------------------


def test_cmd_callees_returns_zero(project: Path):
    conn = _open_db(project)
    fa = _insert_file(conn, "a.py")
    fb = _insert_file(conn, "b.py")
    s_a = _insert_symbol(conn, fa, "do_thing", line=1)
    s_b = _insert_symbol(conn, fb, "helper", line=5)
    _insert_edge(conn, s_a, s_b, "calls")
    conn.close()

    rc = cmd_callees(_args(project, "do_thing"))
    assert rc == 0


def test_cmd_callees_text_format(project: Path, capsys):
    conn = _open_db(project)
    fa = _insert_file(conn, "a.py")
    fb = _insert_file(conn, "b.py")
    s_a = _insert_symbol(conn, fa, "do_thing", line=3)
    s_b = _insert_symbol(conn, fb, "helper", line=8)
    _insert_edge(conn, s_a, s_b, "calls")
    conn.close()

    cmd_callees(_args(project, "do_thing"))
    out = capsys.readouterr().out

    assert "a.py:3" in out
    assert "do_thing" in out
    assert "helper" in out
    assert "→" in out


def test_cmd_callees_no_callees_empty_output(project: Path, capsys):
    conn = _open_db(project)
    fid = _insert_file(conn, "a.py")
    _insert_symbol(conn, fid, "leaf_fn")
    conn.close()

    rc = cmd_callees(_args(project, "leaf_fn"))
    out = capsys.readouterr().out

    assert rc == 0
    assert out.strip() == ""


def test_cmd_callees_empty_db_returns_zero_empty(project: Path, capsys):
    rc = cmd_callees(_args(project, "anything"))
    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip() == ""


def test_cmd_callees_imports_edge_excluded(project: Path, capsys):
    conn = _open_db(project)
    fa = _insert_file(conn, "a.py")
    fb = _insert_file(conn, "b.py")
    s_a = _insert_symbol(conn, fa, "importer")
    s_b = _insert_symbol(conn, fb, "imported_fn")
    _insert_edge(conn, s_a, s_b, "imports")
    conn.close()

    cmd_callees(_args(project, "importer"))
    out = capsys.readouterr().out
    assert out.strip() == ""


def test_cmd_callees_multiple_callees_all_shown(project: Path, capsys):
    conn = _open_db(project)
    fa = _insert_file(conn, "orch.py")
    fb = _insert_file(conn, "helpers.py")
    s_orch = _insert_symbol(conn, fa, "orchestrate", line=1)
    s_h1 = _insert_symbol(conn, fb, "step_one", line=10)
    s_h2 = _insert_symbol(conn, fb, "step_two", line=20)
    _insert_edge(conn, s_orch, s_h1, "calls")
    _insert_edge(conn, s_orch, s_h2, "calls")
    conn.close()

    cmd_callees(_args(project, "orchestrate"))
    out = capsys.readouterr().out

    assert "step_one" in out
    assert "step_two" in out


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


def test_cmd_callees_json_parseable(project: Path, capsys):
    conn = _open_db(project)
    fa = _insert_file(conn, "a.py")
    fb = _insert_file(conn, "b.py")
    s_a = _insert_symbol(conn, fa, "caller_fn", line=1)
    s_b = _insert_symbol(conn, fb, "callee_fn", line=5)
    _insert_edge(conn, s_a, s_b, "calls")
    conn.close()

    cmd_callees(_args(project, "caller_fn", json_out=True))
    out = capsys.readouterr().out
    data = json.loads(out)

    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["caller_name"] == "caller_fn"
    assert data[0]["symbol_name"] == "callee_fn"


def test_cmd_callees_json_empty_result(project: Path, capsys):
    cmd_callees(_args(project, "nothing", json_out=True))
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data == []
