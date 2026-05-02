"""Tests for cmd_cleanup wiring - Phase 7: orphan sweep + legacy dir detection."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from codeatlas.explore_codebase import db
from codeatlas.explore_codebase.cli import cmd_cleanup


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


# Files that ``render`` currently writes. Cleanup must keep these.
CANONICAL_MAP_FILES = (
    "architecture.md",
    "modules.md",
    "symbols.md",
    "callgraph.md",
    "imports.md",
    "dead-code.md",
    "data.md",
    "api.md",
    "impact.md",
    "recent-changes.md",
    "index.md",
)


def _args(project: Path, *, dry_run: bool = False):
    import argparse

    return argparse.Namespace(project_root=str(project), dry_run=dry_run)


def _open_db(project: Path) -> sqlite3.Connection:
    return sqlite3.connect(project / ".claude/codeatlas" / "codebase.db")


def _maps_dir(project: Path) -> Path:
    return project / ".claude/codeatlas" / "maps"


def _context_dir(project: Path) -> Path:
    return project / ".claude/codeatlas" / "context"


def _insert_file(conn: sqlite3.Connection, path: str) -> None:
    conn.execute(
        "INSERT INTO files (path, sha, language, last_parsed_at) VALUES (?, ?, ?, ?)",
        (path, "sha_x", "python", "2026-01-01T00:00:00"),
    )
    conn.commit()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project root with the DB pre-initialised (creates maps/, context/, notes/)."""
    db_path = tmp_path / ".claude/codeatlas" / "codebase.db"
    conn = db.init(db_path)
    conn.close()
    return tmp_path


# ---------------------------------------------------------------------------
# maps/ orphan sweep
# ---------------------------------------------------------------------------


def test_orphan_md_in_maps_is_removed(project: Path):
    """An .md file under maps/ that's not in the canonical set is deleted."""
    orphan = _maps_dir(project) / "old-legacy-map.md"
    orphan.write_text("# old\n")

    rc = cmd_cleanup(_args(project))
    assert rc == 0
    assert not orphan.exists(), "orphan map file must be deleted"


def test_canonical_map_files_are_not_removed(project: Path):
    """All 11 canonical maps written by render must survive cleanup."""
    for fname in CANONICAL_MAP_FILES:
        (_maps_dir(project) / fname).write_text("# canonical\n")

    cmd_cleanup(_args(project))

    for fname in CANONICAL_MAP_FILES:
        assert (_maps_dir(project) / fname).exists(), (
            f"canonical map {fname} was wrongly removed"
        )


def test_orphan_only_targets_md_files_in_maps(project: Path):
    """Non-.md files in maps/ are left alone (cleanup only touches .md)."""
    other = _maps_dir(project) / "leftover.txt"
    other.write_text("not markdown\n")

    cmd_cleanup(_args(project))
    assert other.exists(), "cleanup must only target .md files in maps/"


# ---------------------------------------------------------------------------
# context/ orphan sweep
# ---------------------------------------------------------------------------


def test_orphan_context_md_removed_when_module_not_in_db(project: Path):
    """A context/<module>.md whose stem is not a top-level module in DB → delete."""
    # No files in DB yet → every context/<x>.md is orphan.
    orphan = _context_dir(project) / "gone_module.md"
    orphan.write_text("# gone\n")

    cmd_cleanup(_args(project))
    assert not orphan.exists()


def test_context_md_kept_when_module_still_in_db(project: Path):
    """context/<module>.md is preserved when module has tracked files in DB."""
    conn = _open_db(project)
    try:
        _insert_file(conn, "pkg_alive/file.py")
    finally:
        conn.close()

    keep = _context_dir(project) / "pkg_alive.md"
    keep.write_text("# alive\n")

    cmd_cleanup(_args(project))
    assert keep.exists(), "module still tracked in DB → context file must be kept"


def test_context_only_targets_md_files(project: Path):
    """Non-.md files under context/ are not touched."""
    other = _context_dir(project) / "README.txt"
    other.write_text("hi\n")

    cmd_cleanup(_args(project))
    assert other.exists()


def test_context_md_with_top_level_files_still_orphan(project: Path):
    """A file with no directory prefix has no top-level module — context entry is orphan."""
    conn = _open_db(project)
    try:
        # Top-level file: no directory → not a module.
        _insert_file(conn, "top.py")
    finally:
        conn.close()

    orphan = _context_dir(project) / "top.md"
    orphan.write_text("# bogus\n")

    cmd_cleanup(_args(project))
    assert not orphan.exists(), (
        "context/top.md is orphan when top.py is a root-level file (no module)"
    )


# ---------------------------------------------------------------------------
# --dry-run
# ---------------------------------------------------------------------------


def test_dry_run_prints_would_remove_lines(project: Path, capsys):
    """--dry-run announces what would be removed via 'Would remove:' prefix."""
    orphan = _maps_dir(project) / "orphan.md"
    orphan.write_text("# x\n")

    cmd_cleanup(_args(project, dry_run=True))
    out = capsys.readouterr().out
    assert "Would remove:" in out
    assert ".claude/codeatlas/maps/orphan.md" in out


def test_dry_run_does_not_actually_delete(project: Path):
    """In --dry-run mode the orphan file must still exist after the call."""
    orphan = _maps_dir(project) / "orphan.md"
    orphan.write_text("# x\n")

    cmd_cleanup(_args(project, dry_run=True))
    assert orphan.exists(), "dry-run must not delete files"


def test_normal_mode_prints_removed_lines(project: Path, capsys):
    """Normal mode prints 'Removed:' (not 'Would remove:') for each file."""
    orphan = _maps_dir(project) / "gone.md"
    orphan.write_text("# x\n")

    cmd_cleanup(_args(project))
    out = capsys.readouterr().out
    assert "Removed:" in out
    assert "Would remove:" not in out
    assert ".claude/codeatlas/maps/gone.md" in out


# ---------------------------------------------------------------------------
# Combined orphan + legacy
# ---------------------------------------------------------------------------


def test_orphan_removal_and_legacy_warning_together(project: Path, capsys):
    """Orphan map removal and legacy dir warning both appear in the same run."""
    orphan = _maps_dir(project) / "old-map.md"
    orphan.write_text("# old\n")

    legacy = project / ".codeatlas"
    legacy.mkdir()
    (legacy / "notes.md").write_text("legacy\n")

    cmd_cleanup(_args(project))

    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "Removed:" in output, "orphan removal line must appear"
    assert ".claude/codeatlas/maps/old-map.md" in output, "orphan path must be named"
    assert "legacy directory .codeatlas/" in output, "legacy warning must appear"


# ---------------------------------------------------------------------------
# Legacy dir detection
# ---------------------------------------------------------------------------


def test_legacy_dir_warning_when_present(project: Path, capsys):
    """If .codeatlas/ exists, a warning is printed with migration destination named."""
    legacy = project / ".codeatlas"
    legacy.mkdir()
    (legacy / "old-file.md").write_text("legacy data\n")

    cmd_cleanup(_args(project))

    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "legacy directory .codeatlas/" in output, (
        "must warn about legacy dir presence"
    )
    assert ".claude/codeatlas/" in output, (
        "warning must name the migration destination"
    )
    # Warning only — never auto-delete.
    assert legacy.exists(), "legacy dir must NOT be auto-deleted"
    assert (legacy / "old-file.md").exists()


def test_no_legacy_warning_when_absent(project: Path, capsys):
    """Without .codeatlas/ no warning is emitted."""
    cmd_cleanup(_args(project))
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert ".codeatlas/" not in output


# ---------------------------------------------------------------------------
# Doom-path: empty / missing dirs
# ---------------------------------------------------------------------------


def test_empty_maps_and_context_dirs_no_crash(project: Path, capsys):
    """maps/ and context/ exist but are empty — clean exit with 0 removed."""
    rc = cmd_cleanup(_args(project))
    assert rc == 0
    out = capsys.readouterr().out
    assert "0 file" in out


def test_missing_maps_dir_no_crash(project: Path):
    """maps/ does not exist — cleanup must not crash."""
    import shutil

    shutil.rmtree(_maps_dir(project))
    assert not _maps_dir(project).exists()

    rc = cmd_cleanup(_args(project))
    assert rc == 0


def test_missing_context_dir_no_crash(project: Path):
    """context/ does not exist — cleanup must not crash."""
    import shutil

    shutil.rmtree(_context_dir(project))
    assert not _context_dir(project).exists()

    rc = cmd_cleanup(_args(project))
    assert rc == 0


# ---------------------------------------------------------------------------
# Summary line
# ---------------------------------------------------------------------------


def test_summary_line_normal_mode(project: Path, capsys):
    """Normal mode prints summary line with file count + 'removed'."""
    (_maps_dir(project) / "a.md").write_text("# a\n")
    (_maps_dir(project) / "b.md").write_text("# b\n")

    cmd_cleanup(_args(project))
    out = capsys.readouterr().out
    # Two orphan map files were removed; lenient substring match.
    assert "2 file" in out
    assert "removed" in out


def test_summary_line_dry_run(project: Path, capsys):
    """Dry-run mode prints summary line with 'would be removed'."""
    (_maps_dir(project) / "a.md").write_text("# a\n")

    cmd_cleanup(_args(project, dry_run=True))
    out = capsys.readouterr().out
    assert "1 file" in out
    assert "would be removed" in out


def test_summary_line_zero_files(project: Path, capsys):
    """Empty tree → summary reports 0 files removed."""
    cmd_cleanup(_args(project))
    out = capsys.readouterr().out
    assert "0 file" in out
    assert "removed" in out


# ---------------------------------------------------------------------------
# Idempotency + drift guards
# ---------------------------------------------------------------------------


def test_running_cleanup_twice_is_idempotent(project: Path, capsys):
    """Running cleanup twice on a clean tree → second run removes 0 files."""
    (_maps_dir(project) / "orphan.md").write_text("# x\n")

    cmd_cleanup(_args(project))
    capsys.readouterr()  # discard first run output

    cmd_cleanup(_args(project))
    out = capsys.readouterr().out
    assert "0 file" in out, "second cleanup on clean tree must report 0 removed"


def test_canonical_set_matches_render_output(project: Path):
    """Drift guard: cleanup's canonical set must equal what render actually writes.

    If render gains a 12th map file and cleanup is not updated, this test fires.
    """
    import argparse

    from codeatlas.explore_codebase.cli import cmd_render

    cmd_render(argparse.Namespace(project_root=str(project), base_sha=None, since=None))

    rendered = {p.name for p in _maps_dir(project).iterdir() if p.is_file()}
    assert rendered == set(CANONICAL_MAP_FILES), (
        "render output and cleanup canonical set are out of sync: "
        f"rendered={rendered}, canonical={set(CANONICAL_MAP_FILES)}"
    )
