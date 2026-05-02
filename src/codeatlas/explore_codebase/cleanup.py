"""Phase 7: orphan sweep + legacy dir detection for explore-codebase.

Removes ``.md`` files under ``<project-root>/.claude/codeatlas/maps/`` that are no
longer in the canonical set produced by :mod:`render`, and ``.md`` files
under ``<project-root>/.claude/codeatlas/context/`` whose stem does not correspond
to a top-level module currently tracked in the ``files`` table.

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

from codeatlas.explore_codebase import render
from codeatlas.explore_codebase._paths import top_module as _top_module

# Files that ``render`` writes into ``maps/``. Cleanup must keep these.
_CANONICAL_MAPS: frozenset[str] = frozenset(render.CANONICAL_MAP_FILES)

# Layout under the project root.
_CODEBASE_REL = Path(".claude") / "codeatlas"
_MAPS_REL = _CODEBASE_REL / "maps"
_CONTEXT_REL = _CODEBASE_REL / "context"
_LEGACY_REL = Path(".codeatlas")


def _tracked_top_modules(conn: sqlite3.Connection) -> set[str]:
    """Return the set of top-level module names currently in the ``files`` table.

    A "top-level module" is the first path component of a tracked file's
    ``path``. Files at the project root (no directory segment) contribute
    nothing — they are not bucketed into a module.
    """
    rows = conn.execute("SELECT path FROM files").fetchall()
    modules: set[str] = set()
    for (path,) in rows:
        top = _top_module(path)
        if top is not None:
            modules.add(top)
    return modules


def _orphan_map_files(maps_dir: Path) -> list[Path]:
    """Return ``.md`` files under ``maps_dir`` that are not in the canonical set."""
    if not maps_dir.is_dir():
        return []
    return [
        p
        for p in sorted(maps_dir.iterdir())
        if p.is_file() and p.suffix == ".md" and p.name not in _CANONICAL_MAPS
    ]


def _orphan_context_files(context_dir: Path, tracked_modules: set[str]) -> list[Path]:
    """Return ``.md`` files under ``context_dir`` whose stem is not a tracked module."""
    if not context_dir.is_dir():
        return []
    return [
        p
        for p in sorted(context_dir.iterdir())
        if p.is_file() and p.suffix == ".md" and p.stem not in tracked_modules
    ]


def _rel_display(project_root: Path, path: Path) -> str:
    """Render an absolute path as a project-relative POSIX string for stdout."""
    return path.relative_to(project_root).as_posix()


def run(conn: sqlite3.Connection, project_root: str | Path, *, dry_run: bool) -> int:
    """Sweep orphan maps + context files. Warn on legacy dir. Return exit code."""
    root = Path(project_root)

    tracked_modules = _tracked_top_modules(conn)
    orphans: list[Path] = []
    orphans.extend(_orphan_map_files(root / _MAPS_REL))
    orphans.extend(_orphan_context_files(root / _CONTEXT_REL, tracked_modules))

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
