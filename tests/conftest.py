"""Shared fixtures for explore-codebase tests."""

from pathlib import Path

import pytest


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """A temporary directory with the .claude/codeatlas/ layout pre-created."""
    (tmp_path / ".claude/codeatlas" / "maps").mkdir(parents=True)
    (tmp_path / ".claude/codeatlas" / "context").mkdir(parents=True)
    (tmp_path / ".claude/codeatlas" / "notes").mkdir(parents=True)
    (tmp_path / ".claude" / "rules").mkdir(parents=True)
    return tmp_path
