"""Tests for explore_codebase.changeset - Phase 2: changeset detection."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from codeatlas.explore_codebase import changeset, db


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _git_init(repo: Path) -> None:
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")


def _commit_all(repo: Path, msg: str = "snap") -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)


def _write(repo: Path, rel: str, content: str = "x = 1\n") -> Path:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return target


@pytest.fixture
def project(tmp_path: Path) -> Path:
    _git_init(tmp_path)
    return tmp_path


@pytest.fixture
def conn(project: Path):
    db_path = project / ".claude/codeatlas" / "codebase.db"
    c = db.init(db_path)
    yield c
    c.close()


def _args(project: Path, *, full: bool = False):
    import argparse

    return argparse.Namespace(project_root=str(project), full=full)


def _seed_file_row(conn, path: str, sha: str) -> None:
    conn.execute(
        "INSERT INTO files (path, sha, language, last_parsed_at) VALUES (?, ?, ?, ?)",
        (path, sha, "python", "2026-01-01T00:00:00"),
    )
    conn.commit()


def test_source_files_detects_python(project: Path):
    _write(project, "a.py")
    _write(project, "pkg/b.py")
    files = changeset.source_files(project)
    rels = sorted(files)
    assert "a.py" in rels
    assert "pkg/b.py" in rels


def test_source_files_detects_multi_language_extensions(project: Path):
    for rel in (
        "a.py",
        "b.js",
        "c.ts",
        "d.go",
        "e.java",
        "f.c",
        "g.cpp",
        "h.h",
        "i.rs",
        "j.rb",
        "k.php",
        "l.cs",
    ):
        _write(project, rel, "x")
    files = sorted(changeset.source_files(project))
    expected = [
        "a.py",
        "b.js",
        "c.ts",
        "d.go",
        "e.java",
        "f.c",
        "g.cpp",
        "h.h",
        "i.rs",
        "j.rb",
        "k.php",
        "l.cs",
    ]
    assert files == expected


def test_source_files_excludes_blacklisted_dirs(project: Path):
    _write(project, "src/keep.py")
    _write(project, ".git/hooks/skip.py")
    _write(project, ".claude/skip.py")
    _write(project, "__pycache__/skip.py")
    _write(project, "src/foo.egg-info/skip.py")
    _write(project, "node_modules/pkg/skip.js")
    _write(project, "venv/lib/skip.py")
    _write(project, ".venv/lib/skip.py")
    _write(project, "dist/skip.py")
    _write(project, "build/skip.py")
    files = changeset.source_files(project)
    assert files == {"src/keep.py"}


def test_source_files_excludes_unknown_extensions(project: Path):
    _write(project, "a.py")
    _write(project, "README.md", "# md")
    _write(project, "config.json", "{}")
    _write(project, "image.png", "x")
    files = changeset.source_files(project)
    assert files == {"a.py"}


def test_compute_detects_new_files(project: Path, conn):
    _write(project, "new.py")
    _commit_all(project, "init commit")
    out = changeset.compute(conn, _args(project))
    assert "new.py" in out["new"]
    assert out["changed"] == []
    assert out["deleted"] == []


def test_compute_detects_deleted_files(project: Path, conn):
    _write(project, "gone.py")
    _commit_all(project, "init commit")
    _seed_file_row(conn, "gone.py", changeset.file_sha(project / "gone.py"))
    (project / "gone.py").unlink()
    _commit_all(project, "remove gone")

    out = changeset.compute(conn, _args(project))
    assert "gone.py" in out["deleted"]
    assert out["new"] == []


def test_compute_detects_changed_files_by_sha(project: Path, conn):
    target = _write(project, "mut.py", "v = 1\n")
    _commit_all(project, "init commit")
    old_sha = changeset.file_sha(target)
    _seed_file_row(conn, "mut.py", old_sha)

    target.write_text("v = 2\n")
    out = changeset.compute(conn, _args(project))
    assert "mut.py" in out["changed"]
    assert out["new"] == []
    assert out["deleted"] == []


def test_compute_full_flag_returns_all_source_files(project: Path, conn):
    _write(project, "a.py")
    _write(project, "b.py")
    _commit_all(project, "init commit")
    sha_a = changeset.file_sha(project / "a.py")
    sha_b = changeset.file_sha(project / "b.py")
    _seed_file_row(conn, "a.py", sha_a)
    _seed_file_row(conn, "b.py", sha_b)

    out = changeset.compute(conn, _args(project, full=True))
    all_changed = sorted(out["changed"])
    assert all_changed == ["a.py", "b.py"]
    assert out["new"] == []
    assert out["deleted"] == []


def test_compute_json_shape(project: Path, conn):
    _write(project, "a.py")
    _commit_all(project, "init commit")
    out = changeset.compute(conn, _args(project))

    assert set(out.keys()) == {"new", "changed", "deleted", "stale_narratives"}
    for key in ("new", "changed", "deleted", "stale_narratives"):
        assert isinstance(out[key], list)
    serialized = json.dumps(out)
    assert json.loads(serialized) == out


def test_compute_handles_fresh_repo_with_no_commits(project: Path, conn):
    _write(project, "a.py")
    out = changeset.compute(conn, _args(project))
    assert "a.py" in out["new"]
    assert out["deleted"] == []


def test_compute_unchanged_file_is_not_reported(project: Path, conn):
    target = _write(project, "stable.py", "x = 1\n")
    _commit_all(project, "init commit")
    sha = changeset.file_sha(target)
    _seed_file_row(conn, "stable.py", sha)

    out = changeset.compute(conn, _args(project))
    assert "stable.py" not in out["new"]
    assert "stable.py" not in out["changed"]
    assert "stable.py" not in out["deleted"]


def test_compute_git_diff_marks_uncommitted_change(project: Path, conn):
    target = _write(project, "g.py", "v = 1\n")
    _commit_all(project, "init commit")
    sha = changeset.file_sha(target)
    _seed_file_row(conn, "g.py", sha)

    new_text = "v = 2\n"
    target.write_text(new_text)
    _seed_file_row(
        conn.cursor().connection,
        "g.py",
        sha,
    ) if False else None
    conn.execute(
        "UPDATE files SET sha = ? WHERE path = ?", (changeset.file_sha(target), "g.py")
    )
    conn.commit()

    out = changeset.compute(conn, _args(project))
    assert "g.py" in out["changed"]


def test_file_sha_is_deterministic(project: Path):
    target = _write(project, "x.py", "abc\n")
    s1 = changeset.file_sha(target)
    s2 = changeset.file_sha(target)
    assert s1 == s2
    assert len(s1) == 64


def test_file_sha_changes_with_content(project: Path):
    target = _write(project, "x.py", "abc\n")
    s1 = changeset.file_sha(target)
    target.write_text("xyz\n")
    s2 = changeset.file_sha(target)
    assert s1 != s2


def test_compute_empty_project(project: Path, conn):
    out = changeset.compute(conn, _args(project))
    assert out == {"new": [], "changed": [], "deleted": [], "stale_narratives": []}


def test_compute_full_on_empty_disk_lists_only_deletions(project: Path, conn):
    _seed_file_row(conn, "ghost.py", "deadbeef")
    out = changeset.compute(conn, _args(project, full=True))
    assert out["deleted"] == ["ghost.py"]
    assert out["changed"] == []
    assert out["new"] == []


# ---------------------------------------------------------------------------
# stale_narratives
# ---------------------------------------------------------------------------


def _seed_narrative(conn, topic: str, depends_on: str, content: str = "body") -> None:
    conn.execute(
        "INSERT OR REPLACE INTO narratives "
        "(topic, scope_id, content, depends_on, generated_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (topic, "", content, depends_on, "2026-01-01T00:00:00+00:00"),
    )
    conn.commit()


def test_stale_narratives_empty_table(project: Path, conn):
    """No narratives → stale_narratives is []."""
    result = changeset.stale_narratives(conn, project)
    assert result == []


def test_stale_narratives_fresh_narrative_not_returned(project: Path, conn):
    """Narrative whose recorded SHA matches current disk → not stale."""
    target = _write(project, "pkg/a.py")
    sha = changeset.file_sha(target)
    import json as _json

    _seed_narrative(
        conn, "architecture", _json.dumps([{"path": "pkg/a.py", "sha": sha}])
    )

    result = changeset.stale_narratives(conn, project)
    assert result == []


def test_stale_narratives_changed_sha_marks_stale(project: Path, conn):
    """Narrative with stale SHA (file changed on disk) → returned."""
    import json as _json

    _write(project, "pkg/a.py", "v = 1\n")
    _seed_narrative(
        conn,
        "architecture",
        _json.dumps([{"path": "pkg/a.py", "sha": "old-sha-that-never-matches"}]),
    )

    result = changeset.stale_narratives(conn, project)
    assert "architecture" in result


def test_stale_narratives_deleted_file_marks_stale(project: Path, conn):
    """Narrative referencing a file that no longer exists on disk → stale."""
    import json as _json

    _seed_narrative(
        conn, "modules", _json.dumps([{"path": "gone.py", "sha": "any-sha"}])
    )

    result = changeset.stale_narratives(conn, project)
    assert "modules" in result


def test_stale_narratives_mixed_states(project: Path, conn):
    """Only stale topics returned; fresh ones excluded; result is sorted."""
    import json as _json

    # Fresh narrative
    target = _write(project, "stable.py")
    sha = changeset.file_sha(target)
    _seed_narrative(conn, "zzz-fresh", _json.dumps([{"path": "stable.py", "sha": sha}]))

    # Stale narrative (file missing)
    _seed_narrative(
        conn, "aaa-stale", _json.dumps([{"path": "missing.py", "sha": "x"}])
    )

    result = changeset.stale_narratives(conn, project)
    assert result == ["aaa-stale"]


def test_stale_narratives_result_is_sorted(project: Path, conn):
    """Returned list of stale topics is sorted."""
    import json as _json

    for topic in ["z-topic", "a-topic", "m-topic"]:
        _seed_narrative(conn, topic, _json.dumps([{"path": "missing.py", "sha": "x"}]))

    result = changeset.stale_narratives(conn, project)
    assert result == sorted(result)


def test_stale_narratives_corrupted_json_treated_as_stale(project: Path, conn, capsys):
    """A row with invalid JSON in depends_on is treated as stale; one stderr line emitted."""
    _seed_narrative(conn, "bad-topic", "NOT VALID JSON {{{")

    result = changeset.stale_narratives(conn, project)
    assert "bad-topic" in result

    err = capsys.readouterr().err
    assert err.strip(), "expected at least one stderr line for corrupted depends_on"


def test_stale_narratives_empty_json_array_is_always_fresh(project: Path, conn):
    """A narrative with depends_on='[]' has no files to check — never stale."""
    import json as _json

    _seed_narrative(conn, "no-deps", _json.dumps([]))

    result = changeset.stale_narratives(conn, project)
    assert "no-deps" not in result


def test_stale_narratives_non_list_json_treated_as_stale(project: Path, conn, capsys):
    """A JSON object (not a list) in depends_on must not crash — treat as stale."""
    _seed_narrative(conn, "shape-bad", '{"foo": "bar"}')

    result = changeset.stale_narratives(conn, project)
    assert "shape-bad" in result

    err = capsys.readouterr().err
    assert err.strip(), "expected a stderr line for shape-corrupt depends_on"


def test_stale_narratives_wrong_dict_shape_treated_as_stale(
    project: Path, conn, capsys
):
    """List items missing the expected 'path' key must not crash — treat as stale."""
    _seed_narrative(conn, "wrong-keys", '[{"wrong_key": "x"}]')

    result = changeset.stale_narratives(conn, project)
    assert "wrong-keys" in result

    err = capsys.readouterr().err
    assert err.strip(), "expected a stderr line for shape-corrupt depends_on"


def test_stale_narratives_null_json_treated_as_stale(project: Path, conn, capsys):
    """JSON null parses to None; iterating it raises TypeError — treated as stale."""
    _seed_narrative(conn, "null-topic", "null")

    result = changeset.stale_narratives(conn, project)
    assert "null-topic" in result

    err = capsys.readouterr().err
    assert err.strip(), "expected a stderr line for null depends_on"


def test_compute_includes_stale_narratives_in_output(project: Path, conn):
    """compute() result['stale_narratives'] reflects real disk state."""
    import json as _json

    _seed_narrative(conn, "stale-arch", _json.dumps([{"path": "gone.py", "sha": "x"}]))

    out = changeset.compute(conn, _args(project))
    assert "stale-arch" in out["stale_narratives"]
