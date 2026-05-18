"""CLI entry point for explore-codebase."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from codeatlas.explore_codebase import analyze, changeset, cleanup, db, queries, render

_PROJECT_ROOT_HELP = "Project root directory (default: cwd)."
_JSON_HELP = "Emit JSON instead of text."


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
    p_narrative.add_argument(
        "--scope",
        default="",
        help=(
            "Scope identifier for per-file or per-module narratives "
            "(e.g. 'src/mypkg'). Defaults to '' for single-scope topics."
        ),
    )
    p_narrative.add_argument(
        "--depends-on",
        default=None,
        help=(
            "Comma-separated project-relative paths to use as depends_on, "
            "overriding auto-detection from symbol mentions."
        ),
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

    p_refresh = sub.add_parser(
        "refresh",
        help="Run init + analyze in-process (no piping needed).",
        description=(
            "Compute the changeset from disk state and immediately run static "
            "analysis. Equivalent to piping 'init' output into 'analyze', but "
            "faster and simpler — no subprocess or stdin/stdout dance required."
        ),
    )
    p_refresh.add_argument(
        "--full",
        action="store_true",
        help="Force full rebuild (ignore cached SHAs).",
    )
    p_refresh.add_argument(
        "--project-root",
        default=".",
        help=_PROJECT_ROOT_HELP,
    )

    p_find = sub.add_parser(
        "find",
        help="Look up symbols by name.",
    )
    p_find.add_argument("name", help="Symbol name to search for.")
    p_find.add_argument(
        "--substring",
        action="store_true",
        help="Broaden to substring match (SQL LIKE %%name%%).",
    )
    p_find.add_argument("--project-root", default=".", help=_PROJECT_ROOT_HELP)
    p_find.add_argument("--json", action="store_true", help=_JSON_HELP)

    p_callers = sub.add_parser(
        "callers",
        help="Show who calls a given symbol.",
    )
    p_callers.add_argument("symbol", help="Symbol name to find callers of.")
    p_callers.add_argument("--project-root", default=".", help=_PROJECT_ROOT_HELP)
    p_callers.add_argument("--json", action="store_true", help=_JSON_HELP)

    p_callees = sub.add_parser(
        "callees",
        help="Show what a given symbol calls.",
    )
    p_callees.add_argument("symbol", help="Symbol name to find callees of.")
    p_callees.add_argument("--project-root", default=".", help=_PROJECT_ROOT_HELP)
    p_callees.add_argument("--json", action="store_true", help=_JSON_HELP)

    p_impact = sub.add_parser(
        "impact",
        help="BFS blast radius from a file's symbols.",
    )
    p_impact.add_argument("file", help="Project-relative path of the file.")
    p_impact.add_argument(
        "--depth",
        type=int,
        default=2,
        help="BFS depth (default: 2).",
    )
    p_impact.add_argument("--project-root", default=".", help=_PROJECT_ROOT_HELP)
    p_impact.add_argument("--json", action="store_true", help=_JSON_HELP)

    p_summary = sub.add_parser(
        "summary",
        help="DB health and statistics.",
    )
    p_summary.add_argument("--project-root", default=".", help=_PROJECT_ROOT_HELP)
    p_summary.add_argument("--json", action="store_true", help=_JSON_HELP)

    _ = (
        p_analyze,
        p_render,
        p_cleanup,
        p_refresh,
        p_find,
        p_callers,
        p_callees,
        p_impact,
        p_summary,
    )
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


def _depends_on_from_symbols(
    conn,
    content: str,
    topic_scope: list[str],
    root: Path,
) -> str:
    """Build depends_on JSON by matching symbol names mentioned in content.

    Performs case-sensitive whole-word regex match for each symbol name found
    in the DB. Collects the owning files for all matched symbols. Falls back to
    the full topic scope when no symbol mentions are found.

    Returns compact JSON: [{path, sha}, ...] sorted by path.
    """
    symbol_rows = conn.execute(
        "SELECT DISTINCT s.name, f.path FROM symbols s JOIN files f ON f.id = s.file_id"
    ).fetchall()

    matched_paths: set[str] = set()
    for sym_name, file_path in symbol_rows:
        if not sym_name:
            continue
        pattern = re.compile(rf"\b{re.escape(sym_name)}\b")
        if pattern.search(content):
            matched_paths.add(file_path)

    # Fall back to full topic scope when no symbol mentions found.
    scope_paths = matched_paths if matched_paths else set(topic_scope)

    entries = []
    for rel in sorted(scope_paths):
        abs_path = root / rel
        try:
            sha = changeset.file_sha(abs_path)
        except OSError:
            continue
        entries.append({"path": rel, "sha": sha})
    entries.sort(key=lambda e: e["path"])
    return json.dumps(entries, separators=(",", ":"))


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
    scope_id = getattr(args, "scope", "")
    depends_on_override = getattr(args, "depends_on", None)

    db_path = _db_path_for(args.project_root)
    conn = db.init(db_path)
    try:
        if depends_on_override is not None:
            # --depends-on overrides auto-detection entirely.
            rel_paths = [p.strip() for p in depends_on_override.split(",") if p.strip()]
            resolved_root = root.resolve()
            for rel in rel_paths:
                if not (root / rel).resolve().is_relative_to(resolved_root):
                    sys.stderr.write(
                        f"explore-codebase: path escapes project root: {rel}\n"
                    )
                    return 1
            entries = []
            for rel in sorted(rel_paths):
                abs_path = root / rel
                try:
                    sha = changeset.file_sha(abs_path)
                except OSError:
                    continue
                entries.append({"path": rel, "sha": sha})
            entries.sort(key=lambda e: e["path"])
            depends_on = json.dumps(entries, separators=(",", ":"))
        else:
            depends_on = _depends_on_from_symbols(conn, content, scope, root)

        generated_at = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
        db.upsert_narrative(
            conn,
            topic=args.topic,
            scope_id=scope_id,
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


def cmd_refresh(args: argparse.Namespace) -> int:
    db_path = _db_path_for(args.project_root)
    conn = db.init(db_path)
    try:
        cs = changeset.compute(conn, args)
        return analyze.run(
            conn,
            args.project_root,
            changed=list(cs.get("changed", [])),
            new=list(cs.get("new", [])),
            deleted=list(cs.get("deleted", [])),
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


def cmd_find(args: argparse.Namespace) -> int:
    db_path = _db_path_for(args.project_root)
    conn = db.init(db_path)
    try:
        rows = queries.find_symbols(conn, args.name, substring=args.substring)
    finally:
        conn.close()

    if args.json:
        import dataclasses

        sys.stdout.write(json.dumps([dataclasses.asdict(r) for r in rows]) + "\n")
    else:
        for r in rows:
            loc = f"{r.path}:{r.line}" if r.line is not None else r.path
            sys.stdout.write(f"{loc}  {r.kind}  {r.name}\n")
    return 0


def cmd_callers(args: argparse.Namespace) -> int:
    db_path = _db_path_for(args.project_root)
    conn = db.init(db_path)
    try:
        rows = queries.callers_of(conn, args.symbol)
    finally:
        conn.close()

    if args.json:
        import dataclasses

        sys.stdout.write(json.dumps([dataclasses.asdict(r) for r in rows]) + "\n")
    else:
        for r in rows:
            loc = (
                f"{r.caller_path}:{r.caller_line}"
                if r.caller_line is not None
                else r.caller_path
            )
            sys.stdout.write(f"{loc}  {r.caller_name} → {r.symbol_name}\n")
    return 0


def cmd_callees(args: argparse.Namespace) -> int:
    db_path = _db_path_for(args.project_root)
    conn = db.init(db_path)
    try:
        rows = queries.callees_of(conn, args.symbol)
    finally:
        conn.close()

    if args.json:
        import dataclasses

        sys.stdout.write(json.dumps([dataclasses.asdict(r) for r in rows]) + "\n")
    else:
        for r in rows:
            loc = (
                f"{r.caller_path}:{r.caller_line}"
                if r.caller_line is not None
                else r.caller_path
            )
            sys.stdout.write(f"{loc}  {r.caller_name} → {r.symbol_name}\n")
    return 0


def cmd_impact(args: argparse.Namespace) -> int:
    if args.depth < 1:
        sys.stderr.write(f"explore-codebase: depth must be >= 1, got {args.depth}\n")
        return 1
    db_path = _db_path_for(args.project_root)
    conn = db.init(db_path)
    try:
        result = queries.impact_of(conn, args.file, depth=args.depth)
    finally:
        conn.close()

    if result is None:
        sys.stderr.write(f"explore-codebase: file not found in DB: {args.file}\n")
        return 1

    if args.json:
        import dataclasses

        sys.stdout.write(json.dumps([dataclasses.asdict(r) for r in result]) + "\n")
    else:
        by_depth: dict[int, list[str]] = {}
        for r in result:
            by_depth.setdefault(r.depth, []).append(r.path)
        for d in sorted(by_depth):
            sys.stdout.write(f"Depth {d}:\n")
            for path in sorted(by_depth[d]):
                sys.stdout.write(f"  {path}\n")
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    db_path = _db_path_for(args.project_root)
    conn = db.init(db_path)
    try:
        stats = queries.summary(conn)
    finally:
        conn.close()

    if args.json:
        import dataclasses

        sys.stdout.write(json.dumps(dataclasses.asdict(stats)) + "\n")
    else:
        last = stats.last_parsed_at if stats.last_parsed_at is not None else "—"
        sys.stdout.write(f"Files: {stats.files}\n")
        sys.stdout.write(f"Symbols: {stats.symbols}\n")
        sys.stdout.write(
            f"Edges: {stats.edges} (calls: {stats.calls}, imports: {stats.imports})\n"
        )
        sys.stdout.write(f"Dead symbols: {stats.dead_symbols}\n")
        sys.stdout.write(f"Narratives: {stats.narratives}\n")
        sys.stdout.write(f"Last parsed: {last}\n")
    return 0


_DISPATCH = {
    "init": cmd_init,
    "analyze": cmd_analyze,
    "refresh": cmd_refresh,
    "narrative": cmd_narrative,
    "render": cmd_render,
    "cleanup": cmd_cleanup,
    "find": cmd_find,
    "callers": cmd_callers,
    "callees": cmd_callees,
    "impact": cmd_impact,
    "summary": cmd_summary,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return _DISPATCH[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
