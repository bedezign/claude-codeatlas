"""Tests for cmd_init wiring - Phase 2: cli + db + changeset."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from codeatlas.explore_codebase.cli import _db_path_for, cmd_init


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _git_init(repo: Path) -> None:
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    _git_init(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n")
    return tmp_path


def _args(project: Path, *, full: bool = False):
    import argparse

    return argparse.Namespace(project_root=str(project), full=full)


def test_cmd_init_returns_zero(project: Path):
    rc = cmd_init(_args(project))
    assert rc == 0


def test_cmd_init_creates_db_file(project: Path):
    cmd_init(_args(project))
    db_path = project / ".claude/codeatlas" / "codebase.db"
    assert db_path.exists()


def test_cmd_init_creates_companion_dirs(project: Path):
    cmd_init(_args(project))
    base = project / ".claude/codeatlas"
    assert (base / "maps").is_dir()
    assert (base / "context").is_dir()
    assert (base / "notes").is_dir()


def test_cmd_init_prints_json_with_required_keys(project: Path, capsys):
    cmd_init(_args(project))
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert set(payload.keys()) == {"new", "changed", "deleted", "stale_narratives"}


def test_cmd_init_lists_new_files_on_first_run(project: Path, capsys):
    cmd_init(_args(project))
    payload = json.loads(capsys.readouterr().out)
    assert "a.py" in payload["new"]


def test_cmd_init_full_flag_passes_through(project: Path, capsys):
    args = _args(project, full=True)
    cmd_init(args)
    payload = json.loads(capsys.readouterr().out)
    assert "a.py" in payload["new"]


# ---------------------------------------------------------------------------
# _db_path_for
# ---------------------------------------------------------------------------


def test_db_path_for_empty_string():
    """Empty-string root does not crash; result ends with the expected suffix."""
    result = _db_path_for("")
    assert result.endswith(".claude/codeatlas/codebase.db")


def test_db_path_for_unicode_path():
    """Unicode path components are handled without error."""
    result = _db_path_for("/tmp/プロジェクト")
    assert result == "/tmp/プロジェクト/.claude/codeatlas/codebase.db"
