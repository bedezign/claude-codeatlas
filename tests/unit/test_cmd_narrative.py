"""Tests for cmd_narrative wiring — Phase 4: JSON depends_on with path+sha."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from codeatlas.explore_codebase.cli import cmd_narrative


def _args(
    project: Path,
    *,
    topic: str | None = "architecture",
    content_file: str | None = None,
):
    import argparse

    return argparse.Namespace(
        project_root=str(project),
        topic=topic,
        content_file=content_file,
    )


def _open_db(project: Path) -> sqlite3.Connection:
    return sqlite3.connect(project / ".claude/codeatlas" / "codebase.db")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project root with the DB pre-initialised."""
    from codeatlas.explore_codebase import db

    db_path = tmp_path / ".claude/codeatlas" / "codebase.db"
    conn = db.init(db_path)
    conn.close()
    return tmp_path


def _write_content(project: Path, body: str = "narrative body\n") -> Path:
    p = project / "content.md"
    p.write_text(body)
    return p


def _write_source(project: Path, rel: str, body: str = "x = 1\n") -> Path:
    """Write a source file inside the project, creating parent dirs."""
    target = project / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)
    return target


# ---------------------------------------------------------------------------
# Happy path — basic upsert behaviour
# ---------------------------------------------------------------------------


def test_cmd_narrative_returns_zero(project: Path):
    content = _write_content(project)
    rc = cmd_narrative(_args(project, content_file=str(content)))
    assert rc == 0


def test_cmd_narrative_inserts_row(project: Path):
    content = _write_content(project, "v1 body\n")
    cmd_narrative(_args(project, topic="architecture", content_file=str(content)))

    conn = _open_db(project)
    try:
        rows = conn.execute(
            "SELECT topic, content FROM narratives WHERE topic = ?",
            ("architecture",),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0] == ("architecture", "v1 body\n")


def test_cmd_narrative_depends_on_is_json_with_path_and_sha_sorted_by_path(
    project: Path,
):
    """depends_on is compact JSON [{path, sha}, ...] sorted by path."""
    _write_source(project, "z/late.py")
    _write_source(project, "a/early.py")
    _write_source(project, "m/middle.py")

    content = _write_content(project)
    cmd_narrative(_args(project, topic="architecture", content_file=str(content)))

    conn = _open_db(project)
    try:
        raw = conn.execute(
            "SELECT depends_on FROM narratives WHERE topic = ?", ("architecture",)
        ).fetchone()[0]
    finally:
        conn.close()

    entries = json.loads(raw)
    assert isinstance(entries, list)
    assert len(entries) == 3
    paths = [e["path"] for e in entries]
    assert paths == sorted(paths), "entries must be sorted by path"
    for entry in entries:
        assert set(entry.keys()) == {"path", "sha"}
        assert isinstance(entry["sha"], str)
        assert len(entry["sha"]) == 64  # SHA-256 hex digest


def test_cmd_narrative_writes_generated_at_iso(project: Path):
    content = _write_content(project)
    cmd_narrative(_args(project, topic="t", content_file=str(content)))

    conn = _open_db(project)
    try:
        ts = conn.execute(
            "SELECT generated_at FROM narratives WHERE topic = ?", ("t",)
        ).fetchone()[0]
    finally:
        conn.close()
    assert isinstance(ts, str)
    assert len(ts) >= 10
    assert ts[4] == "-" and ts[7] == "-"


def test_cmd_narrative_prints_confirmation(project: Path, capsys):
    content = _write_content(project)
    cmd_narrative(_args(project, topic="architecture", content_file=str(content)))
    out = capsys.readouterr().out
    assert "Narrative 'architecture' saved." in out


# ---------------------------------------------------------------------------
# Upsert semantics — second call replaces, no duplicate
# ---------------------------------------------------------------------------


def test_cmd_narrative_upserts_on_same_topic(project: Path):
    first = project / "first.md"
    first.write_text("first version\n")
    cmd_narrative(_args(project, topic="t", content_file=str(first)))

    second = project / "second.md"
    second.write_text("second version\n")
    cmd_narrative(_args(project, topic="t", content_file=str(second)))

    conn = _open_db(project)
    try:
        rows = conn.execute(
            "SELECT topic, content FROM narratives WHERE topic = ?", ("t",)
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0] == ("t", "second version\n")


def test_cmd_narrative_upsert_refreshes_depends_on(project: Path):
    """A later call with more files on disk → depends_on must reflect the new scope."""
    content = _write_content(project)

    # First call: only one source file on disk.
    _write_source(project, "first.py")
    cmd_narrative(_args(project, topic="architecture", content_file=str(content)))

    # Second call: second source file appears on disk.
    _write_source(project, "second.py")
    cmd_narrative(_args(project, topic="architecture", content_file=str(content)))

    conn = _open_db(project)
    try:
        raw = conn.execute(
            "SELECT depends_on FROM narratives WHERE topic = ?", ("architecture",)
        ).fetchone()[0]
    finally:
        conn.close()

    entries = json.loads(raw)
    paths = [e["path"] for e in entries]
    assert "first.py" in paths
    assert "second.py" in paths


# ---------------------------------------------------------------------------
# Doom paths
# ---------------------------------------------------------------------------


def test_cmd_narrative_empty_scope_writes_json_empty_array(project: Path):
    """No source files on disk → depends_on is '[]' (JSON empty array, NOT '')."""
    content = _write_content(project)
    rc = cmd_narrative(_args(project, topic="architecture", content_file=str(content)))
    assert rc == 0

    conn = _open_db(project)
    try:
        depends_on = conn.execute(
            "SELECT depends_on FROM narratives WHERE topic = ?", ("architecture",)
        ).fetchone()[0]
    finally:
        conn.close()
    assert depends_on == "[]"


def test_cmd_narrative_missing_content_file_returns_non_zero(project: Path, capsys):
    """--content-file pointing at a path that doesn't exist must NOT traceback."""
    rc = cmd_narrative(
        _args(
            project,
            topic="t",
            content_file=str(project / "does-not-exist.md"),
        )
    )
    captured = capsys.readouterr()
    assert rc != 0
    assert captured.err.strip(), "expected an error message on stderr"


def test_cmd_narrative_directory_as_content_file_returns_non_zero(
    project: Path, capsys
):
    """A directory passed as --content-file must error cleanly, not traceback."""
    a_dir = project / "some-dir"
    a_dir.mkdir()
    rc = cmd_narrative(_args(project, topic="t", content_file=str(a_dir)))
    captured = capsys.readouterr()
    assert rc != 0
    assert captured.err.strip()


def test_cmd_narrative_permission_denied_on_content_file_returns_non_zero(
    project: Path, capsys
):
    """A chmod 000 content file must error cleanly, not traceback."""
    if os.getuid() == 0:
        pytest.skip("chmod 000 has no effect as root")
    restricted = project / "restricted.md"
    restricted.write_text("narrative body\n")
    restricted.chmod(0o000)
    try:
        rc = cmd_narrative(_args(project, topic="t", content_file=str(restricted)))
        captured = capsys.readouterr()
        assert rc == 1
        assert "cannot read content file" in captured.err
    finally:
        restricted.chmod(0o644)


def test_cmd_narrative_empty_content_file_is_accepted(project: Path):
    """An empty content file is valid — content stored as empty string."""
    empty = project / "empty.md"
    empty.write_text("")
    rc = cmd_narrative(_args(project, topic="t", content_file=str(empty)))
    assert rc == 0

    conn = _open_db(project)
    try:
        content = conn.execute(
            "SELECT content FROM narratives WHERE topic = ?", ("t",)
        ).fetchone()[0]
    finally:
        conn.close()
    assert content == ""


def test_cmd_narrative_unicode_content_preserved(project: Path):
    """Unicode in narrative body must round-trip intact."""
    body = "façade — naïve café 🐍\n"
    p = project / "u.md"
    p.write_text(body, encoding="utf-8")
    rc = cmd_narrative(_args(project, topic="t", content_file=str(p)))
    assert rc == 0

    conn = _open_db(project)
    try:
        content = conn.execute(
            "SELECT content FROM narratives WHERE topic = ?", ("t",)
        ).fetchone()[0]
    finally:
        conn.close()
    assert content == body


def test_cmd_narrative_missing_file_in_scope_is_skipped_silently(project: Path):
    """A file in topic scope that doesn't exist on disk is skipped — no error."""
    # project-identity always returns ["pyproject.toml"] — but we don't create it.
    # The scope has one entry that is missing from disk → should be skipped.
    content = _write_content(project)
    rc = cmd_narrative(
        _args(project, topic="project-identity", content_file=str(content))
    )
    assert rc == 0

    conn = _open_db(project)
    try:
        raw = conn.execute(
            "SELECT depends_on FROM narratives WHERE topic = ?", ("project-identity",)
        ).fetchone()[0]
    finally:
        conn.close()
    entries = json.loads(raw)
    # pyproject.toml was not created — entry skipped → empty array
    assert entries == []


# ---------------------------------------------------------------------------
# Scope-respect tests
# ---------------------------------------------------------------------------


def test_cmd_narrative_context_topic_scopes_to_module(project: Path):
    """--topic context/pkg_a writes only pkg_a paths."""
    _write_source(project, "pkg_a/alpha.py")
    _write_source(project, "pkg_a/beta.py")
    _write_source(project, "pkg_b/gamma.py")

    content = _write_content(project)
    cmd_narrative(_args(project, topic="context/pkg_a", content_file=str(content)))

    conn = _open_db(project)
    try:
        raw = conn.execute(
            "SELECT depends_on FROM narratives WHERE topic = ?", ("context/pkg_a",)
        ).fetchone()[0]
    finally:
        conn.close()

    entries = json.loads(raw)
    paths = [e["path"] for e in entries]
    assert sorted(paths) == ["pkg_a/alpha.py", "pkg_a/beta.py"]


def test_cmd_narrative_project_identity_scope(project: Path):
    """--topic project-identity writes only pyproject.toml (if it exists)."""
    # Create the file so it's picked up on disk.
    (project / "pyproject.toml").write_text("[project]\nname = 'test'\n")

    content = _write_content(project)
    cmd_narrative(_args(project, topic="project-identity", content_file=str(content)))

    conn = _open_db(project)
    try:
        raw = conn.execute(
            "SELECT depends_on FROM narratives WHERE topic = ?", ("project-identity",)
        ).fetchone()[0]
    finally:
        conn.close()

    entries = json.loads(raw)
    paths = [e["path"] for e in entries]
    assert paths == ["pyproject.toml"]


# ---------------------------------------------------------------------------
# Argparse-level: missing required flags exit non-zero via SystemExit(2)
# ---------------------------------------------------------------------------


def test_parser_rejects_missing_topic():
    """argparse: --topic is required → SystemExit(2)."""
    from codeatlas.explore_codebase.cli import build_parser

    parser = build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["narrative", "--content-file", "x.md"])
    assert exc_info.value.code == 2


def test_parser_rejects_missing_content_file():
    """argparse: --content-file is required → SystemExit(2)."""
    from codeatlas.explore_codebase.cli import build_parser

    parser = build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["narrative", "--topic", "t"])
    assert exc_info.value.code == 2


def test_parser_accepts_full_narrative_invocation():
    """Sanity: build_parser registers --topic and --content-file."""
    from codeatlas.explore_codebase.cli import build_parser

    parser = build_parser()
    ns = parser.parse_args(
        [
            "narrative",
            "--topic",
            "architecture",
            "--content-file",
            "doc.md",
            "--project-root",
            "/tmp/x",
        ]
    )
    assert ns.topic == "architecture"
    assert ns.content_file == "doc.md"
    assert ns.project_root == "/tmp/x"
