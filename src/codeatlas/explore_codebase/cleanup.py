"""Phase 7: orphan sweep + legacy dir detection for explore-codebase.

Removes ``.md`` files under ``<project-root>/.claude/codeatlas/maps/`` that are no
longer in the canonical set produced by :mod:`render`, and ``.md`` files
under ``<project-root>/.claude/codeatlas/context/`` that would not be produced by
the current DB state.

Context files can be either per-file pages (mirroring the source path) or
``_module.md`` rollup pages for directories with only small files.  Orphan
detection reconstructs the set of paths that ``render.run`` would produce
and flags everything else.

A legacy ``.codeatlas/`` directory under the project root triggers a
warning only — never auto-deletion. The user must remove it manually.

The canonical map set is sourced from :mod:`render` so that adding a new
map file there does not silently leave it as an "orphan" here. The
``test_canonical_set_matches_render_output`` test in
``tests/unit/test_cmd_cleanup.py`` is the drift guard.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from codeatlas.explore_codebase.render import (
    CANONICAL_MAP_FILES,
    CONTEXT_SYMBOL_THRESHOLD,
    MODULE_ROLLUP_FILENAME,
)

# Files that ``render`` writes into ``maps/``. Cleanup must keep these.
_CANONICAL_MAPS: frozenset[str] = frozenset(CANONICAL_MAP_FILES)

# Layout under the project root.
_CODEBASE_REL = Path(".claude") / "codeatlas"
_MAPS_REL = _CODEBASE_REL / "maps"
_CONTEXT_REL = _CODEBASE_REL / "context"
_LEGACY_REL = Path(".codeatlas")


def _expected_context_rel_paths(conn: sqlite3.Connection) -> set[str]:
    """Reconstruct the set of context-relative paths that render would produce.

    Mirrors the partitioning logic in ``render.run``:
    - Files with >= CONTEXT_SYMBOL_THRESHOLD symbols → ``<mirrored-path>.md``
    - Files below threshold → ``<parent-dir>/_module.md``
    - Root-level files (no directory component) → skipped entirely
    """
    rows = conn.execute(
        "SELECT f.path, COUNT(s.id) "
        "FROM files f LEFT JOIN symbols s ON s.file_id = f.id "
        "GROUP BY f.id"
    ).fetchall()

    expected: set[str] = set()
    rollup_dirs: set[str] = set()

    for rel_path, count in rows:
        parts = Path(rel_path).parts
        if len(parts) < 2:
            # Root-level file — no context page produced.
            continue
        parent = str(Path(*parts[:-1]))
        if count >= CONTEXT_SYMBOL_THRESHOLD:
            p = Path(rel_path)
            expected.add(str(p.with_suffix(".md")))
        else:
            rollup_dirs.add(parent)

    for parent_dir in rollup_dirs:
        expected.add(f"{parent_dir}/{MODULE_ROLLUP_FILENAME}")

    return expected


def _orphan_map_files(maps_dir: Path) -> list[Path]:
    """Return ``.md`` files under ``maps_dir`` that are not in the canonical set."""
    if not maps_dir.is_dir():
        return []
    return [
        p
        for p in sorted(maps_dir.iterdir())
        if p.is_file() and p.suffix == ".md" and p.name not in _CANONICAL_MAPS
    ]


def _orphan_context_files(
    context_dir: Path, expected_rel_paths: set[str]
) -> list[Path]:
    """Return ``.md`` files under ``context_dir`` that render would not produce."""
    if not context_dir.is_dir():
        return []
    orphans: list[Path] = []
    for p in sorted(context_dir.rglob("*.md")):
        if not p.is_file():
            continue
        rel = p.relative_to(context_dir).as_posix()
        if rel not in expected_rel_paths:
            orphans.append(p)
    return orphans


def _rel_display(project_root: Path, path: Path) -> str:
    """Render an absolute path as a project-relative POSIX string for stdout."""
    return path.relative_to(project_root).as_posix()


def run(conn: sqlite3.Connection, project_root: str | Path, *, dry_run: bool) -> int:
    """Sweep orphan maps + context files. Warn on legacy dir. Return exit code."""
    root = Path(project_root)

    expected_context = _expected_context_rel_paths(conn)
    orphans: list[Path] = []
    orphans.extend(_orphan_map_files(root / _MAPS_REL))
    orphans.extend(_orphan_context_files(root / _CONTEXT_REL, expected_context))

    verb_action = "Would remove" if dry_run else "Removed"
    for path in orphans:
        if not dry_run:
            try:
                path.unlink(missing_ok=True)
            except PermissionError as err:
                sys.stderr.write(
                    f"Warning: could not remove {_rel_display(root, path)}: {err}\n"
                )
                continue
        sys.stdout.write(f"{verb_action}: {_rel_display(root, path)}\n")

    legacy = root / _LEGACY_REL
    if legacy.exists():
        sys.stdout.write(
            "Warning: legacy directory .codeatlas/ detected. "
            "Your previous DB, narratives, and notes remain there — "
            "move .codeatlas/ contents to .claude/codeatlas/ manually. "
            "This tool will not read from the old location.\n"
        )

    summary_verb = "would be removed" if dry_run else "removed"
    sys.stdout.write(f"Cleanup complete. {len(orphans)} file(s) {summary_verb}.\n")
    return 0
