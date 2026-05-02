"""CLI entry point for explore-codebase."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from codeatlas.explore_codebase import analyze, changeset, cleanup, db, render

_PROJECT_ROOT_HELP = "Project root directory (default: cwd)."


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="explore-codebase",
        description="SQLite-backed codebase knowledge graph for Claude Code.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser(
        "init",
        help="Initialize DB, detect changed/new/deleted files, list stale narratives.",
    )
    p_init.add_argument(
        "--full",
        action="store_true",
        help="Force full rebuild (ignore cached SHAs).",
    )
    p_init.add_argument(
        "--project-root",
        default=".",
        help=_PROJECT_ROOT_HELP,
    )

    p_analyze = sub.add_parser(
        "analyze",
        help="Run static analysis tools and store results in DB.",
        description=(
            "Run static analysis tools (ctags, pyan3, grimp, vulture) and store "
            "results in the DB. When --changed/--new/--deleted are omitted, reads "
            "a JSON changeset from stdin (pipe from 'init')."
        ),
    )
    p_analyze.add_argument(
        "--project-root",
        default=".",
        help=_PROJECT_ROOT_HELP,
    )
    p_analyze.add_argument(
        "--changed",
        nargs="*",
        default=None,
        help="Files in the changed set (relative paths).",
    )
    p_analyze.add_argument(
        "--new",
        nargs="*",
        default=None,
        help="Files in the new set (relative paths).",
    )
    p_analyze.add_argument(
        "--deleted",
        nargs="*",
        default=None,
        help="Files in the deleted set (relative paths).",
    )

    p_narrative = sub.add_parser(
        "narrative",
        help="Store an AI-generated narrative for a topic.",
    )
    p_narrative.add_argument("--topic", required=True, help="Narrative topic key.")
    p_narrative.add_argument(
        "--content-file",
        required=True,
        help="Path to a file containing the narrative prose.",
    )
    p_narrative.add_argument(
        "--project-root",
        default=".",
        help=_PROJECT_ROOT_HELP,
    )

    p_render = sub.add_parser(
        "render",
        help="Render all markdown maps and context files from the DB.",
    )
    p_render.add_argument(
        "--project-root",
        default=".",
        help=_PROJECT_ROOT_HELP,
    )
    p_render.add_argument(
        "--base-sha",
        default=None,
        help=(
            "Treat files whose current sha differs from this value as 'changed' "
            "for the impact map. Without it, impact.md becomes a placeholder."
        ),
    )
    p_render.add_argument(
        "--since",
        default=None,
        help=(
            "git ref or date string passed to `git log --since=...` to populate "
            "recent-changes.md. Without it, recent-changes.md is a placeholder."
        ),
    )

    p_cleanup = sub.add_parser(
        "cleanup",
        help="Remove orphaned map files and legacy directories.",
    )
    p_cleanup.add_argument(
        "--project-root",
        default=".",
        help=_PROJECT_ROOT_HELP,
    )
    p_cleanup.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be removed without deleting anything.",
    )

    _ = p_analyze, p_render, p_cleanup
    return parser


def _db_path_for(project_root: str) -> str:
    return str(Path(project_root) / ".claude" / "codeatlas" / "codebase.db")


def cmd_init(args: argparse.Namespace) -> int:
    db_path = _db_path_for(args.project_root)
    conn = db.init(db_path)
    try:
        result = changeset.compute(conn, args)
    finally:
        conn.close()
    sys.stdout.write(json.dumps(result) + "\n")
    return 0


def _read_stdin_changeset() -> dict | None:
    """Read a changeset JSON object from stdin. Returns None on parse failure."""
    raw = sys.stdin.read()
    if not raw.strip():
        return {"new": [], "changed": [], "deleted": []}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"explore-codebase: invalid stdin JSON: {exc}\n")
        return None
    if not isinstance(payload, dict):
        sys.stderr.write("explore-codebase: stdin payload must be a JSON object\n")
        return None
    for field in ("changed", "new", "deleted"):
        value = payload.get(field, [])
        if not isinstance(value, list):
            sys.stderr.write(
                f"explore-codebase: stdin payload field '{field}' must be a list, "
                f"got {type(value).__name__}\n"
            )
            return None
    return payload


def cmd_analyze(args: argparse.Namespace) -> int:
    db_path = _db_path_for(args.project_root)

    flags_provided = (
        args.changed is not None or args.new is not None or args.deleted is not None
    )
    if flags_provided:
        changed = args.changed or []
        new = args.new or []
        deleted = args.deleted or []
    else:
        payload = _read_stdin_changeset()
        if payload is None:
            return 1
        changed = list(payload.get("changed", []))
        new = list(payload.get("new", []))
        deleted = list(payload.get("deleted", []))

    root = Path(args.project_root).resolve()
    for rel in changed + new + deleted:
        if not (root / rel).resolve().is_relative_to(root):
            sys.stderr.write(f"explore-codebase: path escapes project root: {rel}\n")
            return 1

    conn = db.init(db_path)
    try:
        return analyze.run(
            conn,
            args.project_root,
            changed=changed,
            new=new,
            deleted=deleted,
        )
    finally:
        conn.close()


def cmd_narrative(args: argparse.Namespace) -> int:
    from datetime import datetime, timezone

    from codeatlas.explore_codebase import topics

    content_path = Path(args.content_file)
    try:
        content = content_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        sys.stderr.write(f"explore-codebase: content file not found: {content_path}\n")
        return 1
    except IsADirectoryError:
        sys.stderr.write(
            f"explore-codebase: content file is a directory: {content_path}\n"
        )
        return 1
    except OSError as exc:
        sys.stderr.write(
            f"explore-codebase: cannot read content file {content_path}: {exc}\n"
        )
        return 1

    root = Path(args.project_root)
    scope = topics.files_for_topic(args.topic, root)

    entries = []
    for rel in scope:
        abs_path = root / rel
        try:
            sha = changeset.file_sha(abs_path)
        except OSError:
            # File is in scope but not on disk — skip silently.
            continue
        entries.append({"path": rel, "sha": sha})
    entries.sort(key=lambda e: e["path"])
    depends_on = json.dumps(entries, separators=(",", ":"))

    db_path = _db_path_for(args.project_root)
    conn = db.init(db_path)
    try:
        generated_at = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
        db.upsert_narrative(
            conn,
            topic=args.topic,
            content=content,
            depends_on=depends_on,
            generated_at=generated_at,
        )
    finally:
        conn.close()
    sys.stdout.write(f"Narrative '{args.topic}' saved.\n")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    db_path = _db_path_for(args.project_root)
    conn = db.init(db_path)
    try:
        return render.run(
            conn,
            args.project_root,
            base_sha=getattr(args, "base_sha", None),
            since=getattr(args, "since", None),
        )
    finally:
        conn.close()


def cmd_cleanup(args: argparse.Namespace) -> int:
    db_path = _db_path_for(args.project_root)
    conn = db.init(db_path)
    try:
        return cleanup.run(
            conn,
            args.project_root,
            dry_run=getattr(args, "dry_run", False),
        )
    finally:
        conn.close()


_DISPATCH = {
    "init": cmd_init,
    "analyze": cmd_analyze,
    "narrative": cmd_narrative,
    "render": cmd_render,
    "cleanup": cmd_cleanup,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return _DISPATCH[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
