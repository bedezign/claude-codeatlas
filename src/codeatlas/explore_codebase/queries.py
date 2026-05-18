"""Pure-SQL query helpers for the codebase knowledge graph.

All functions accept a sqlite3.Connection and return plain dataclasses.
No CLI concerns live here — the CLI layer formats and prints.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class SymbolRow:
    """A single symbol record with its file location."""

    path: str
    name: str
    kind: str
    line: int | None
    line_end: int | None
    loc: int | None


@dataclass(frozen=True)
class EdgeRow:
    """A calls-direction edge between two symbols."""

    caller_path: str
    caller_name: str
    caller_line: int | None
    symbol_name: str
    symbol_path: str


@dataclass(frozen=True)
class ImpactRow:
    """A file reached by BFS from a changed file."""

    path: str
    depth: int


@dataclass(frozen=True)
class SummaryStats:
    """Aggregate counts from the DB for health/status display."""

    files: int
    symbols: int
    edges: int
    calls: int
    imports: int
    dead_symbols: int
    narratives: int
    last_parsed_at: str | None


def find_symbols(
    conn: sqlite3.Connection,
    name: str,
    *,
    substring: bool = False,
) -> list[SymbolRow]:
    """Return symbols matching *name*, sorted by (path, line).

    When *substring* is True, matches any symbol whose name contains *name*
    (SQL LIKE %name%). When False (default), exact-match only.
    """
    if substring:
        if not name:
            return []
        escaped = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        rows = conn.execute(
            "SELECT f.path, s.name, s.kind, s.line, s.line_end, s.loc "
            "FROM symbols s JOIN files f ON f.id = s.file_id "
            "WHERE s.name LIKE ? ESCAPE '\\' "
            "ORDER BY f.path, s.line",
            (pattern,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT f.path, s.name, s.kind, s.line, s.line_end, s.loc "
            "FROM symbols s JOIN files f ON f.id = s.file_id "
            "WHERE s.name = ? "
            "ORDER BY f.path, s.line",
            (name,),
        ).fetchall()

    return [
        SymbolRow(path=r[0], name=r[1], kind=r[2], line=r[3], line_end=r[4], loc=r[5])
        for r in rows
    ]


def callers_of(conn: sqlite3.Connection, symbol_name: str) -> list[EdgeRow]:
    """Return all incoming 'calls' edges for symbols named *symbol_name*.

    When *symbol_name* matches more than one symbol (e.g. method 'run' in many
    classes), all incoming calls edges for all matching symbols are returned.
    """
    rows = conn.execute(
        "SELECT fc.path, sc.name, sc.line, sd.name, fd.path "
        "FROM edges e "
        "JOIN symbols sc ON sc.id = e.src_id "
        "JOIN files fc ON fc.id = sc.file_id "
        "JOIN symbols sd ON sd.id = e.dst_id "
        "JOIN files fd ON fd.id = sd.file_id "
        "WHERE sd.name = ? AND e.kind = ? "
        "ORDER BY fc.path, sc.line",
        (symbol_name, "calls"),
    ).fetchall()

    return [
        EdgeRow(
            caller_path=r[0],
            caller_name=r[1],
            caller_line=r[2],
            symbol_name=r[3],
            symbol_path=r[4],
        )
        for r in rows
    ]


def callees_of(conn: sqlite3.Connection, symbol_name: str) -> list[EdgeRow]:
    """Return all outgoing 'calls' edges for symbols named *symbol_name*.

    When *symbol_name* matches more than one symbol, all outgoing calls edges
    from all matching symbols are returned.
    """
    rows = conn.execute(
        "SELECT fc.path, sc.name, sc.line, sd.name, fd.path "
        "FROM edges e "
        "JOIN symbols sc ON sc.id = e.src_id "
        "JOIN files fc ON fc.id = sc.file_id "
        "JOIN symbols sd ON sd.id = e.dst_id "
        "JOIN files fd ON fd.id = sd.file_id "
        "WHERE sc.name = ? AND e.kind = ? "
        "ORDER BY fc.path, sc.line",
        (symbol_name, "calls"),
    ).fetchall()

    return [
        EdgeRow(
            caller_path=r[0],
            caller_name=r[1],
            caller_line=r[2],
            symbol_name=r[3],
            symbol_path=r[4],
        )
        for r in rows
    ]


def _seed_frontier(
    conn: sqlite3.Connection,
    file_path: str,
) -> tuple[set[int], list[ImpactRow]] | None:
    """Look up *file_path* in the DB and return its symbol IDs as the initial frontier.

    Returns None when the file is not tracked (caller should treat this as an error).
    Returns an empty set when the file has no symbols (no outgoing edges possible).
    """
    row = conn.execute("SELECT id FROM files WHERE path = ?", (file_path,)).fetchone()
    if row is None:
        return None
    file_id: int = row[0]
    sym_ids = {
        r[0]
        for r in conn.execute(
            "SELECT id FROM symbols WHERE file_id = ?", (file_id,)
        ).fetchall()
    }
    return sym_ids, []


def _neighbours_of(
    conn: sqlite3.Connection,
    src_ids: set[int],
) -> list[tuple[int, str]]:
    """Return (dst_sym_id, dst_file_path) for all outgoing edges from *src_ids*."""
    placeholders = ",".join("?" * len(src_ids))
    return conn.execute(
        f"SELECT DISTINCT s.id, f.path "  # noqa: S608
        f"FROM edges e "
        f"JOIN symbols s ON s.id = e.dst_id "
        f"JOIN files f ON f.id = s.file_id "
        f"WHERE e.src_id IN ({placeholders})",
        list(src_ids),
    ).fetchall()


def _expand_frontier(
    conn: sqlite3.Connection,
    current_sym_ids: set[int],
    visited_files: set[str],
    hop: int,
) -> tuple[set[int], list[ImpactRow]]:
    """Advance one BFS hop from *current_sym_ids*.

    Returns the next frontier's symbol IDs and the ImpactRows discovered at *hop*.
    Files already in *visited_files* are skipped; newly discovered files are added
    to *visited_files* in place.
    """
    dst_rows = _neighbours_of(conn, current_sym_ids)

    new_rows: list[ImpactRow] = []
    newly_discovered: list[str] = []
    for _sym_id, path in dst_rows:
        if path not in visited_files:
            visited_files.add(path)
            new_rows.append(ImpactRow(path=path, depth=hop))
            newly_discovered.append(path)

    # Collect symbol IDs for all newly discovered files.
    next_sym_ids: set[int] = set()
    for fpath in newly_discovered:
        frow = conn.execute("SELECT id FROM files WHERE path = ?", (fpath,)).fetchone()
        if frow:
            next_sym_ids.update(
                r[0]
                for r in conn.execute(
                    "SELECT id FROM symbols WHERE file_id = ?", (frow[0],)
                ).fetchall()
            )

    return next_sym_ids, new_rows


def impact_of(
    conn: sqlite3.Connection,
    file_path: str,
    *,
    depth: int = 2,
) -> list[ImpactRow] | None:
    """BFS blast radius from *file_path*'s symbols up to *depth* hops.

    Returns None when *file_path* is not tracked in the DB (caller should
    treat this as an error and exit 1).

    Returns an empty list when the file exists but has no outgoing edges.

    Edge filter: any kind (calls + imports) — all dependency types count.
    Dedup: a file reached at hop N is not repeated at hop N+1.
    """
    if depth < 1:
        raise ValueError(f"depth must be >= 1, got {depth}")

    seed = _seed_frontier(conn, file_path)
    if seed is None:
        return None

    frontier_sym_ids, results = seed
    if not frontier_sym_ids:
        return []

    visited_files: set[str] = {file_path}

    for hop in range(1, depth + 1):
        if not frontier_sym_ids:
            break
        frontier_sym_ids, new_rows = _expand_frontier(
            conn, frontier_sym_ids, visited_files, hop
        )
        results.extend(new_rows)
        if not new_rows:
            break

    return results


def summary(conn: sqlite3.Connection) -> SummaryStats:
    """Return aggregate counts from all tables for health/status display."""
    (files,) = conn.execute("SELECT COUNT(*) FROM files").fetchone()
    (symbols,) = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()
    (edges,) = conn.execute("SELECT COUNT(*) FROM edges").fetchone()
    (calls,) = conn.execute(
        "SELECT COUNT(*) FROM edges WHERE kind = 'calls'"
    ).fetchone()
    (imports,) = conn.execute(
        "SELECT COUNT(*) FROM edges WHERE kind = 'imports'"
    ).fetchone()
    (dead,) = conn.execute("SELECT COUNT(*) FROM dead_symbols").fetchone()
    (narratives,) = conn.execute("SELECT COUNT(*) FROM narratives").fetchone()
    row = conn.execute("SELECT MAX(last_parsed_at) FROM files").fetchone()
    last_parsed_at: str | None = row[0] if row else None

    return SummaryStats(
        files=files,
        symbols=symbols,
        edges=edges,
        calls=calls,
        imports=imports,
        dead_symbols=dead,
        narratives=narratives,
        last_parsed_at=last_parsed_at,
    )
