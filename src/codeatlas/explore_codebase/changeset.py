"""Phase 2: changeset detection for explore-codebase."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".go",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".rs",
    ".rb",
    ".php",
    ".cs",
}

EXCLUDED_DIR_NAMES = {
    ".git",
    ".claude",
    "__pycache__",
    "node_modules",
    "venv",
    ".venv",
    "dist",
    "build",
}


def _is_excluded_dir(name: str) -> bool:
    if name in EXCLUDED_DIR_NAMES:
        return True
    return name.endswith(".egg-info")


def source_files(root: str | Path) -> set[str]:
    base = Path(root)
    out: set[str] = set()
    for path in _iter_source(base):
        out.add(str(path.relative_to(base)))
    return out


def _iter_source(base: Path):
    stack = [base]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError as exc:
            logger.warning("skipping %s: %s", current, exc)
            continue
        for entry in entries:
            if entry.is_dir():
                if _is_excluded_dir(entry.name):
                    continue
                stack.append(entry)
            elif entry.is_file() and entry.suffix in SOURCE_EXTENSIONS:
                yield entry


def file_sha(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_changed(root: Path) -> set[str]:
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        logger.warning("git diff timed out in %s", root)
        return set()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return set()
    return {line for line in result.stdout.splitlines() if line}


def _disk_state(root: Path) -> dict[str, str]:
    return {rel: file_sha(root / rel) for rel in source_files(root)}


def _db_state(conn) -> dict[str, str]:
    rows = conn.execute("SELECT path, sha FROM files").fetchall()
    return {row[0]: row[1] for row in rows}


def stale_narratives(conn: sqlite3.Connection, root: Path) -> list[str]:
    """Return topics whose recorded depends_on no longer matches disk state.

    Algorithm:
    1. Read all narrative rows from the DB, ordered by topic.
    2. For each row, parse depends_on as JSON. If parsing fails, treat the
       topic as stale and emit a single stderr line.
    3. For each {path, sha} entry: compute the current file_sha at root/path.
       If the file is missing or the SHA differs, the topic is stale.
    4. Return sorted list of stale topics.
    """
    rows = conn.execute(
        "SELECT topic, depends_on FROM narratives ORDER BY topic"
    ).fetchall()

    stale: list[str] = []
    for topic, depends_on in rows:
        # Pre-migration rows stored CSV paths without SHAs; they will fail JSON parse
        # and be correctly treated as stale until re-written by `narrative` subcommand.
        try:
            entries = json.loads(depends_on)
        except json.JSONDecodeError:
            sys.stderr.write(
                f"explore-codebase: corrupted depends_on for topic '{topic}' — treating as stale\n"
            )
            stale.append(topic)
            continue

        topic_is_stale = False
        try:
            for entry in entries:
                path = entry["path"]
                recorded_sha = entry["sha"]
                try:
                    current_sha = file_sha(root / path)
                except OSError:
                    topic_is_stale = True
                    break
                if current_sha != recorded_sha:
                    topic_is_stale = True
                    break
        except (TypeError, KeyError, AttributeError):
            sys.stderr.write(
                f"explore-codebase: corrupted depends_on for topic '{topic}' — treating as stale\n"
            )
            stale.append(topic)
            continue
        if topic_is_stale:
            stale.append(topic)

    return sorted(stale)


def compute(conn, args) -> dict:
    root = Path(getattr(args, "project_root", "."))
    full = bool(getattr(args, "full", False))

    disk = _disk_state(root)
    seen = _db_state(conn)

    new_set = set(disk) - set(seen)
    deleted_set = set(seen) - set(disk)

    if full:
        changed_set = set(disk) - new_set
    else:
        sha_changed = {p for p in set(disk) & set(seen) if disk[p] != seen[p]}
        git_changed = _git_changed(root) & set(disk)
        git_changed -= new_set
        changed_set = sha_changed | git_changed

    return {
        "new": sorted(new_set),
        "changed": sorted(changed_set),
        "deleted": sorted(deleted_set),
        "stale_narratives": stale_narratives(conn, root),
    }
