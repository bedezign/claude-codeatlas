"""Tests for the 'find' subcommand."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from codeatlas.explore_codebase import db
from codeatlas.explore_codebase.cli import cmd_find


def _args(project: Path, name: str, *, substring: bool = False, json_out: bool = False):
    return argparse.Namespace(
        project_root=str(project),
        name=name,
        substring=substring,
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


def _insert_file(conn, path: str, sha: str = "abc") -> int:
    cur = conn.execute(
        "INSERT INTO files (path, sha, language, last_parsed_at) VALUES (?, ?, ?, ?)",
        (path, sha, "python", "2026-05-15T10:00:00"),
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


# ---------------------------------------------------------------------------
# Basic find
# ---------------------------------------------------------------------------


def test_cmd_find_returns_zero_on_match(project: Path):
    conn = _open_db(project)
    fid = _insert_file(conn, "a.py")
    _insert_symbol(conn, fid, "my_fn")
    conn.close()

    rc = cmd_find(_args(project, "my_fn"))
    assert rc == 0


def test_cmd_find_returns_zero_no_match(project: Path):
    rc = cmd_find(_args(project, "nonexistent"))
    assert rc == 0


def test_cmd_find_text_output_format(project: Path, capsys):
    conn = _open_db(project)
    fid = _insert_file(conn, "pkg/module.py")
    _insert_symbol(conn, fid, "my_func", "function", line=42)
    conn.close()

    cmd_find(_args(project, "my_func"))
    out = capsys.readouterr().out

    assert "pkg/module.py:42" in out
    assert "function" in out
    assert "my_func" in out


def test_cmd_find_text_sorted_by_path_then_line(project: Path, capsys):
    conn = _open_db(project)
    fz = _insert_file(conn, "z.py")
    fa = _insert_file(conn, "a.py")
    _insert_symbol(conn, fz, "fn", line=1)
    _insert_symbol(conn, fa, "fn", line=99)
    conn.close()

    cmd_find(_args(project, "fn"))
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]

    assert lines[0].startswith("a.py")
    assert lines[1].startswith("z.py")


def test_cmd_find_empty_db_no_output(project: Path, capsys):
    cmd_find(_args(project, "anything"))
    out = capsys.readouterr().out
    assert out.strip() == ""


def test_cmd_find_substring_flag(project: Path, capsys):
    conn = _open_db(project)
    fid = _insert_file(conn, "a.py")
    _insert_symbol(conn, fid, "parse_json", line=1)
    _insert_symbol(conn, fid, "parse_csv", line=2)
    _insert_symbol(conn, fid, "unrelated", line=3)
    conn.close()

    cmd_find(_args(project, "parse", substring=True))
    out = capsys.readouterr().out

    assert "parse_json" in out
    assert "parse_csv" in out
    assert "unrelated" not in out


def test_cmd_find_without_substring_no_partial(project: Path, capsys):
    conn = _open_db(project)
    fid = _insert_file(conn, "a.py")
    _insert_symbol(conn, fid, "my_long_name")
    conn.close()

    cmd_find(_args(project, "my_long"))
    out = capsys.readouterr().out
    assert "my_long_name" not in out


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


def test_cmd_find_json_output_parseable(project: Path, capsys):
    conn = _open_db(project)
    fid = _insert_file(conn, "a.py")
    _insert_symbol(conn, fid, "my_fn", "function", line=5)
    conn.close()

    cmd_find(_args(project, "my_fn", json_out=True))
    out = capsys.readouterr().out
    data = json.loads(out)

    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["name"] == "my_fn"
    assert data[0]["path"] == "a.py"
    assert data[0]["kind"] == "function"
    assert data[0]["line"] == 5


def test_cmd_find_json_empty_db_returns_empty_list(project: Path, capsys):
    cmd_find(_args(project, "anything", json_out=True))
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data == []


def test_cmd_find_json_substring_flag(project: Path, capsys):
    conn = _open_db(project)
    fid = _insert_file(conn, "a.py")
    _insert_symbol(conn, fid, "parse_json", line=1)
    _insert_symbol(conn, fid, "parse_csv", line=2)
    conn.close()

    cmd_find(_args(project, "parse", substring=True, json_out=True))
    out = capsys.readouterr().out
    data = json.loads(out)

    names = {r["name"] for r in data}
    assert "parse_json" in names
    assert "parse_csv" in names
