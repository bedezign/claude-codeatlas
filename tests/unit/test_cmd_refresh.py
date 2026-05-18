"""Tests for cmd_refresh — in-process init + analyze shortcut."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from codeatlas.explore_codebase.cli import build_parser


# ---------------------------------------------------------------------------
# Helpers shared with test_analyze.py conventions
# ---------------------------------------------------------------------------


def _completed(stdout: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout)


def _ctags_output(*tags) -> str:
    return "\n".join(json.dumps(t) for t in tags) + "\n"


def _stub_tool_chain(ctags_stdout: str = "", **overrides):
    def fake_run(argv, *_args, **_kwargs):
        tool = Path(argv[0]).name if argv else ""
        if tool == "ctags":
            return _completed(stdout=ctags_stdout)
        if tool == "pyan3":
            return _completed(stdout=overrides.get("pyan3_stdout", ""))
        if tool == "vulture":
            return _completed(
                stdout=overrides.get("vulture_stdout", ""),
                returncode=overrides.get("vulture_rc", 0),
            )
        return _completed()

    return fake_run


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _git_init(repo: Path) -> None:
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")


def _args(project: Path, *, full: bool = False):
    import argparse

    return argparse.Namespace(project_root=str(project), full=full)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_cmd_refresh_returns_zero_on_empty_project(tmp_path: Path):
    """refresh on a project with no source files returns 0."""
    from codeatlas.explore_codebase.cli import cmd_refresh

    _git_init(tmp_path)
    rc = cmd_refresh(_args(tmp_path))
    assert rc == 0


def test_cmd_refresh_creates_db(tmp_path: Path):
    """refresh creates the DB file when it doesn't exist yet."""
    from codeatlas.explore_codebase.cli import cmd_refresh

    _git_init(tmp_path)
    cmd_refresh(_args(tmp_path))
    db_path = tmp_path / ".claude/codeatlas" / "codebase.db"
    assert db_path.exists()


def test_cmd_refresh_inserts_file_rows(tmp_path: Path):
    """refresh inserts file rows for source files found in the project."""
    from codeatlas.explore_codebase.cli import cmd_refresh
    import sqlite3

    _git_init(tmp_path)
    (tmp_path / "a.py").write_text("def foo(): pass\n")

    ctags_out = _ctags_output(
        {"_type": "tag", "name": "foo", "kind": "function", "line": 1},
    )
    with patch(
        "codeatlas.explore_codebase.analyze.subprocess.run",
        side_effect=_stub_tool_chain(ctags_stdout=ctags_out),
    ):
        rc = cmd_refresh(_args(tmp_path))

    assert rc == 0
    db_path = tmp_path / ".claude/codeatlas" / "codebase.db"
    conn = sqlite3.connect(str(db_path))
    try:
        paths = {r[0] for r in conn.execute("SELECT path FROM files").fetchall()}
    finally:
        conn.close()
    assert "a.py" in paths


def test_cmd_refresh_full_flag_accepted(tmp_path: Path):
    """refresh accepts --full without error."""
    from codeatlas.explore_codebase.cli import cmd_refresh

    _git_init(tmp_path)
    rc = cmd_refresh(_args(tmp_path, full=True))
    assert rc == 0


def test_build_parser_registers_refresh_subcommand():
    """build_parser must register a 'refresh' subcommand."""
    parser = build_parser()
    ns = parser.parse_args(["refresh", "--project-root", "/tmp"])
    assert ns.command == "refresh"
    assert ns.project_root == "/tmp"


def test_build_parser_refresh_full_flag():
    """build_parser refresh subparser exposes --full."""
    parser = build_parser()
    ns = parser.parse_args(["refresh", "--project-root", "/tmp", "--full"])
    assert ns.full is True


def test_build_parser_refresh_default_project_root():
    """build_parser refresh subparser defaults project_root to '.'."""
    parser = build_parser()
    ns = parser.parse_args(["refresh"])
    assert ns.project_root == "."
    assert ns.full is False


def test_cmd_refresh_empty_changeset_idempotent(tmp_path: Path):
    """Two refresh calls on a project with no source files both succeed; no duplicates."""
    import sqlite3

    from codeatlas.explore_codebase.cli import cmd_refresh

    _git_init(tmp_path)

    rc_first = cmd_refresh(_args(tmp_path))
    assert rc_first == 0

    rc_second = cmd_refresh(_args(tmp_path))
    assert rc_second == 0

    db_path = tmp_path / ".claude/codeatlas" / "codebase.db"
    conn = sqlite3.connect(str(db_path))
    try:
        (file_count,) = conn.execute("SELECT COUNT(*) FROM files").fetchone()
    finally:
        conn.close()
    # No source files → no file rows; repeated refresh must not insert phantom rows.
    assert file_count == 0
