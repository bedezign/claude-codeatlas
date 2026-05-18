"""Tests for queries.py — pure SQL helper functions."""

from __future__ import annotations

import sqlite3

import pytest

from codeatlas.explore_codebase import db
from codeatlas.explore_codebase.queries import (
    EdgeRow,
    ImpactRow,
    SymbolRow,
    SummaryStats,
    callers_of,
    callees_of,
    find_symbols,
    impact_of,
    summary,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "codebase.db"
    c = db.init(db_path)
    yield c
    c.close()


def _insert_file(
    conn: sqlite3.Connection,
    path: str,
    sha: str = "abc",
    ts: str = "2026-05-15T10:00:00",
) -> int:
    cur = conn.execute(
        "INSERT INTO files (path, sha, language, last_parsed_at) VALUES (?, ?, ?, ?)",
        (path, sha, "python", ts),
    )
    conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


def _insert_symbol(
    conn: sqlite3.Connection,
    file_id: int,
    name: str,
    kind: str = "function",
    line: int = 1,
    line_end: int | None = None,
    loc: int | None = None,
    scope: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO symbols (file_id, kind, name, scope, line, line_end, loc) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (file_id, kind, name, scope, line, line_end, loc),
    )
    conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


def _insert_edge(
    conn: sqlite3.Connection, src_id: int, dst_id: int, kind: str = "calls"
) -> None:
    conn.execute(
        "INSERT INTO edges (src_id, dst_id, kind) VALUES (?, ?, ?)",
        (src_id, dst_id, kind),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# find_symbols
# ---------------------------------------------------------------------------


def test_find_symbols_exact_match(conn):
    fid = _insert_file(conn, "a.py")
    _insert_symbol(conn, fid, "my_func", line=5)
    _insert_symbol(conn, fid, "my_func_extra", line=10)

    rows = find_symbols(conn, "my_func")

    assert len(rows) == 1
    assert rows[0].name == "my_func"
    assert rows[0].path == "a.py"
    assert rows[0].line == 5


def test_find_symbols_returns_symrow_dataclass(conn):
    fid = _insert_file(conn, "a.py")
    _insert_symbol(conn, fid, "fn", kind="function", line=3, line_end=8, loc=6)

    rows = find_symbols(conn, "fn")

    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, SymbolRow)
    assert row.path == "a.py"
    assert row.name == "fn"
    assert row.kind == "function"
    assert row.line == 3
    assert row.line_end == 8
    assert row.loc == 6


def test_find_symbols_substring_broadens_results(conn):
    fid = _insert_file(conn, "a.py")
    _insert_symbol(conn, fid, "parse_json", line=1)
    _insert_symbol(conn, fid, "parse_csv", line=2)
    _insert_symbol(conn, fid, "unrelated", line=3)

    rows = find_symbols(conn, "parse", substring=True)

    names = {r.name for r in rows}
    assert "parse_json" in names
    assert "parse_csv" in names
    assert "unrelated" not in names


def test_find_symbols_empty_db_returns_empty(conn):
    rows = find_symbols(conn, "anything")
    assert rows == []


def test_find_symbols_multiple_matches_same_name(conn):
    """Same name in different files — all returned (method `run` in many classes)."""
    fa = _insert_file(conn, "mod_a.py")
    fb = _insert_file(conn, "mod_b.py")
    _insert_symbol(conn, fa, "run", line=10)
    _insert_symbol(conn, fb, "run", line=20)

    rows = find_symbols(conn, "run")

    assert len(rows) == 2
    paths = {r.path for r in rows}
    assert "mod_a.py" in paths
    assert "mod_b.py" in paths


def test_find_symbols_sorted_by_path_then_line(conn):
    fa = _insert_file(conn, "z_file.py")
    fb = _insert_file(conn, "a_file.py")
    _insert_symbol(conn, fa, "fn", line=5)
    _insert_symbol(conn, fb, "fn", line=99)

    rows = find_symbols(conn, "fn")

    assert len(rows) == 2
    assert rows[0].path == "a_file.py"
    assert rows[1].path == "z_file.py"


def test_find_symbols_no_match_returns_empty(conn):
    fid = _insert_file(conn, "a.py")
    _insert_symbol(conn, fid, "existing_fn")

    rows = find_symbols(conn, "nonexistent")
    assert rows == []


def test_find_symbols_substring_false_no_partial(conn):
    fid = _insert_file(conn, "a.py")
    _insert_symbol(conn, fid, "my_long_function_name")

    rows = find_symbols(conn, "my_long")

    # exact match only — must not find partial
    assert rows == []


def test_find_symbols_empty_name_returns_empty(conn):
    fid = _insert_file(conn, "a.py")
    _insert_symbol(conn, fid, "something", line=1)

    assert find_symbols(conn, "") == []
    assert find_symbols(conn, "", substring=True) == []


def test_find_symbols_substring_escapes_percent(conn):
    """Substring search for 'foo_bar' must not match 'fooXbar' via unescaped %."""
    fid = _insert_file(conn, "a.py")
    _insert_symbol(conn, fid, "foo_bar", line=1)
    _insert_symbol(conn, fid, "fooXbar", line=2)

    rows = find_symbols(conn, "foo_bar", substring=True)

    names = {r.name for r in rows}
    assert "foo_bar" in names
    assert "fooXbar" not in names


def test_find_symbols_substring_escapes_underscore(conn):
    """Substring search: underscore in query must not act as LIKE wildcard."""
    fid = _insert_file(conn, "a.py")
    _insert_symbol(conn, fid, "parse_csv", line=1)
    _insert_symbol(conn, fid, "parseXcsv", line=2)

    rows = find_symbols(conn, "parse_csv", substring=True)

    names = {r.name for r in rows}
    assert "parse_csv" in names
    assert "parseXcsv" not in names


def test_find_symbols_stable_sort_with_duplicate_path_line(conn):
    """Two symbols sharing (path, line) must return in the same order every run."""
    fid = _insert_file(conn, "a.py")
    _insert_symbol(conn, fid, "alpha", line=10)
    _insert_symbol(conn, fid, "beta", line=10)

    names_first = [r.name for r in find_symbols(conn, "a", substring=True)]

    for _ in range(4):
        names_run = [r.name for r in find_symbols(conn, "a", substring=True)]
        assert names_run == names_first, (
            "result order must be stable across repeated calls"
        )


# ---------------------------------------------------------------------------
# callers_of
# ---------------------------------------------------------------------------


def test_callers_of_returns_edge_rows(conn):
    fa = _insert_file(conn, "caller.py")
    fb = _insert_file(conn, "callee.py")
    s_caller = _insert_symbol(conn, fa, "caller_fn", line=5)
    s_callee = _insert_symbol(conn, fb, "target_fn", line=10)
    _insert_edge(conn, s_caller, s_callee, "calls")

    rows = callers_of(conn, "target_fn")

    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, EdgeRow)
    assert row.caller_name == "caller_fn"
    assert row.symbol_name == "target_fn"
    assert row.caller_path == "caller.py"
    assert row.caller_line == 5


def test_callers_of_empty_db_returns_empty(conn):
    rows = callers_of(conn, "nonexistent")
    assert rows == []


def test_callers_of_no_callers_returns_empty(conn):
    fid = _insert_file(conn, "a.py")
    _insert_symbol(conn, fid, "lonely_fn")

    rows = callers_of(conn, "lonely_fn")
    assert rows == []


def test_callers_of_only_calls_edges(conn):
    """imports edges are NOT included in callers_of."""
    fa = _insert_file(conn, "a.py")
    fb = _insert_file(conn, "b.py")
    s_a = _insert_symbol(conn, fa, "importer", line=1)
    s_b = _insert_symbol(conn, fb, "imported_fn", line=2)
    _insert_edge(conn, s_a, s_b, "imports")

    rows = callers_of(conn, "imported_fn")
    assert rows == []


def test_callers_of_multiple_callers_all_returned(conn):
    fc = _insert_file(conn, "common.py")
    fa = _insert_file(conn, "a.py")
    fb = _insert_file(conn, "b.py")
    s_target = _insert_symbol(conn, fc, "shared_fn", line=1)
    s_a = _insert_symbol(conn, fa, "caller_a", line=5)
    s_b = _insert_symbol(conn, fb, "caller_b", line=7)
    _insert_edge(conn, s_a, s_target, "calls")
    _insert_edge(conn, s_b, s_target, "calls")

    rows = callers_of(conn, "shared_fn")

    caller_names = {r.caller_name for r in rows}
    assert "caller_a" in caller_names
    assert "caller_b" in caller_names


def test_callers_of_results_sorted_by_caller_path_then_line(conn):
    """callers_of returns rows ordered alphabetically by caller path."""
    fc = _insert_file(conn, "common.py")
    fz = _insert_file(conn, "z_caller.py")
    fa = _insert_file(conn, "a_caller.py")
    s_target = _insert_symbol(conn, fc, "shared_fn", line=1)
    s_z = _insert_symbol(conn, fz, "fn_z", line=5)
    s_a = _insert_symbol(conn, fa, "fn_a", line=7)
    _insert_edge(conn, s_z, s_target, "calls")
    _insert_edge(conn, s_a, s_target, "calls")

    rows = callers_of(conn, "shared_fn")

    assert len(rows) == 2
    assert rows[0].caller_path == "a_caller.py"
    assert rows[1].caller_path == "z_caller.py"


def test_callers_of_ambiguous_symbol_aggregates_all(conn):
    """If 'run' exists in two classes, all incoming calls edges are returned."""
    fa = _insert_file(conn, "worker.py")
    fb = _insert_file(conn, "task.py")
    fc = _insert_file(conn, "main.py")
    s_run_a = _insert_symbol(conn, fa, "run", line=10)
    s_run_b = _insert_symbol(conn, fb, "run", line=20)
    s_dispatcher = _insert_symbol(conn, fc, "dispatch", line=1)
    _insert_edge(conn, s_dispatcher, s_run_a, "calls")
    _insert_edge(conn, s_dispatcher, s_run_b, "calls")

    rows = callers_of(conn, "run")

    assert len(rows) == 2
    for r in rows:
        assert r.symbol_name == "run"
        assert r.caller_name == "dispatch"


# ---------------------------------------------------------------------------
# callees_of
# ---------------------------------------------------------------------------


def test_callees_of_returns_edge_rows(conn):
    fa = _insert_file(conn, "a.py")
    fb = _insert_file(conn, "b.py")
    s_caller = _insert_symbol(conn, fa, "do_thing", line=1)
    s_callee = _insert_symbol(conn, fb, "helper", line=5)
    _insert_edge(conn, s_caller, s_callee, "calls")

    rows = callees_of(conn, "do_thing")

    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, EdgeRow)
    assert row.caller_name == "do_thing"
    assert row.symbol_name == "helper"


def test_callees_of_empty_db_returns_empty(conn):
    rows = callees_of(conn, "nonexistent")
    assert rows == []


def test_callees_of_no_callees_returns_empty(conn):
    fid = _insert_file(conn, "a.py")
    _insert_symbol(conn, fid, "leaf_fn")

    rows = callees_of(conn, "leaf_fn")
    assert rows == []


def test_callees_of_only_calls_edges(conn):
    fa = _insert_file(conn, "a.py")
    fb = _insert_file(conn, "b.py")
    s_a = _insert_symbol(conn, fa, "importer", line=1)
    s_b = _insert_symbol(conn, fb, "imported_fn", line=2)
    _insert_edge(conn, s_a, s_b, "imports")

    rows = callees_of(conn, "importer")
    assert rows == []


def test_callees_of_multiple_callees_all_returned(conn):
    fa = _insert_file(conn, "orchestrator.py")
    fb = _insert_file(conn, "helpers.py")
    s_orch = _insert_symbol(conn, fa, "orchestrate", line=1)
    s_h1 = _insert_symbol(conn, fb, "step_one", line=10)
    s_h2 = _insert_symbol(conn, fb, "step_two", line=20)
    _insert_edge(conn, s_orch, s_h1, "calls")
    _insert_edge(conn, s_orch, s_h2, "calls")

    rows = callees_of(conn, "orchestrate")

    callee_names = {r.symbol_name for r in rows}
    assert "step_one" in callee_names
    assert "step_two" in callee_names


def test_callees_of_results_sorted_by_caller_path_then_line(conn):
    """callees_of returns rows ordered alphabetically by caller path."""
    fz = _insert_file(conn, "z_module.py")
    fa = _insert_file(conn, "a_module.py")
    fc = _insert_file(conn, "common.py")
    s_helper = _insert_symbol(conn, fc, "helper", line=1)
    s_z = _insert_symbol(conn, fz, "fn_z", line=3)
    s_a = _insert_symbol(conn, fa, "fn_a", line=3)
    _insert_edge(conn, s_z, s_helper, "calls")
    _insert_edge(conn, s_a, s_helper, "calls")

    rows_z = callees_of(conn, "fn_z")
    rows_a = callees_of(conn, "fn_a")

    assert len(rows_z) == 1
    assert rows_z[0].caller_path == "z_module.py"
    assert len(rows_a) == 1
    assert rows_a[0].caller_path == "a_module.py"


def test_callees_of_ambiguous_caller_aggregates_all(conn):
    """'run' in two modules — callees from both are returned."""
    fa = _insert_file(conn, "worker.py")
    fb = _insert_file(conn, "task.py")
    fc = _insert_file(conn, "shared.py")
    s_run_a = _insert_symbol(conn, fa, "run", line=10)
    s_run_b = _insert_symbol(conn, fb, "run", line=20)
    s_helper = _insert_symbol(conn, fc, "common_helper", line=1)
    _insert_edge(conn, s_run_a, s_helper, "calls")
    _insert_edge(conn, s_run_b, s_helper, "calls")

    rows = callees_of(conn, "run")

    assert len(rows) == 2
    for r in rows:
        assert r.caller_name == "run"
        assert r.symbol_name == "common_helper"


# ---------------------------------------------------------------------------
# impact_of
# ---------------------------------------------------------------------------


def test_impact_of_returns_impact_rows(conn):
    fa = _insert_file(conn, "src.py")
    fb = _insert_file(conn, "dep.py")
    s_a = _insert_symbol(conn, fa, "fn_a", line=1)
    s_b = _insert_symbol(conn, fb, "fn_b", line=1)
    _insert_edge(conn, s_a, s_b, "calls")

    rows = impact_of(conn, "src.py")

    assert rows is not None
    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, ImpactRow)
    assert row.path == "dep.py"
    assert row.depth == 1


def test_impact_of_depth_2_bfs(conn):
    fa = _insert_file(conn, "a.py")
    fb = _insert_file(conn, "b.py")
    fc = _insert_file(conn, "c.py")
    s_a = _insert_symbol(conn, fa, "fn_a", line=1)
    s_b = _insert_symbol(conn, fb, "fn_b", line=1)
    s_c = _insert_symbol(conn, fc, "fn_c", line=1)
    _insert_edge(conn, s_a, s_b, "calls")
    _insert_edge(conn, s_b, s_c, "calls")

    rows = impact_of(conn, "a.py", depth=2)

    assert rows is not None
    paths_by_depth = {r.path: r.depth for r in rows}
    assert paths_by_depth.get("b.py") == 1
    assert paths_by_depth.get("c.py") == 2


def test_impact_of_depth_1_does_not_walk_further(conn):
    fa = _insert_file(conn, "a.py")
    fb = _insert_file(conn, "b.py")
    fc = _insert_file(conn, "c.py")
    s_a = _insert_symbol(conn, fa, "fn_a", line=1)
    s_b = _insert_symbol(conn, fb, "fn_b", line=1)
    s_c = _insert_symbol(conn, fc, "fn_c", line=1)
    _insert_edge(conn, s_a, s_b, "calls")
    _insert_edge(conn, s_b, s_c, "calls")

    rows = impact_of(conn, "a.py", depth=1)

    assert rows is not None
    paths = {r.path for r in rows}
    assert "b.py" in paths
    assert "c.py" not in paths


def test_impact_of_any_edge_kind(conn):
    """BFS walks both calls and imports edges."""
    fa = _insert_file(conn, "src.py")
    fb = _insert_file(conn, "imported.py")
    s_a = _insert_symbol(conn, fa, "fn_a", line=1)
    s_b = _insert_symbol(conn, fb, "fn_b", line=1)
    _insert_edge(conn, s_a, s_b, "imports")

    rows = impact_of(conn, "src.py", depth=1)

    assert rows is not None
    paths = {r.path for r in rows}
    assert "imported.py" in paths


def test_impact_of_dedup_at_min_depth(conn):
    """Diamond a→c (hop1) and a→b→c (hop2): c listed at depth 1 only."""
    fa = _insert_file(conn, "a.py")
    fb = _insert_file(conn, "b.py")
    fc = _insert_file(conn, "c.py")
    s_a = _insert_symbol(conn, fa, "fn_a", line=1)
    s_b = _insert_symbol(conn, fb, "fn_b", line=1)
    s_c = _insert_symbol(conn, fc, "fn_c", line=1)
    _insert_edge(conn, s_a, s_c, "calls")
    _insert_edge(conn, s_a, s_b, "calls")
    _insert_edge(conn, s_b, s_c, "calls")

    rows = impact_of(conn, "a.py", depth=2)

    assert rows is not None
    c_rows = [r for r in rows if r.path == "c.py"]
    assert len(c_rows) == 1
    assert c_rows[0].depth == 1


def test_impact_of_file_not_in_db_returns_none(conn):
    """File not in DB returns None (caller checks and emits error)."""
    result = impact_of(conn, "missing.py")
    assert result is None


def test_impact_of_empty_db_file_not_found(conn):
    result = impact_of(conn, "any.py")
    assert result is None


def test_impact_of_no_edges_returns_empty_list(conn):
    _insert_file(conn, "isolated.py")

    rows = impact_of(conn, "isolated.py")

    assert rows == []


def test_impact_of_depth_zero_raises(conn):
    _insert_file(conn, "a.py")
    with pytest.raises(ValueError):
        impact_of(conn, "a.py", depth=0)


def test_impact_of_negative_depth_raises(conn):
    _insert_file(conn, "a.py")
    with pytest.raises(ValueError):
        impact_of(conn, "a.py", depth=-1)


def test_impact_of_depth_one_returns_only_direct_neighbours(conn):
    """3-hop chain a→b→c→d: depth=1 returns only b, not c or d."""
    fa = _insert_file(conn, "a.py")
    fb = _insert_file(conn, "b.py")
    fc = _insert_file(conn, "c.py")
    fd = _insert_file(conn, "d.py")
    s_a = _insert_symbol(conn, fa, "fn_a", line=1)
    s_b = _insert_symbol(conn, fb, "fn_b", line=1)
    s_c = _insert_symbol(conn, fc, "fn_c", line=1)
    s_d = _insert_symbol(conn, fd, "fn_d", line=1)
    _insert_edge(conn, s_a, s_b, "calls")
    _insert_edge(conn, s_b, s_c, "calls")
    _insert_edge(conn, s_c, s_d, "calls")

    rows = impact_of(conn, "a.py", depth=1)

    assert rows is not None
    paths = {r.path for r in rows}
    assert "b.py" in paths
    assert "c.py" not in paths
    assert "d.py" not in paths


def test_impact_of_dedups_files_in_cycle(conn):
    """Cycle a→b→a: each file appears at most once; b at depth 1 only."""
    fa = _insert_file(conn, "a.py")
    fb = _insert_file(conn, "b.py")
    s_a = _insert_symbol(conn, fa, "fn_a", line=1)
    s_b = _insert_symbol(conn, fb, "fn_b", line=1)
    _insert_edge(conn, s_a, s_b, "calls")
    _insert_edge(conn, s_b, s_a, "calls")

    rows = impact_of(conn, "a.py", depth=2)

    assert rows is not None
    paths = [r.path for r in rows]
    # a.py is the root — must not appear in the results
    assert "a.py" not in paths
    # b.py must appear exactly once
    assert paths.count("b.py") == 1


def test_impact_of_default_depth_is_2(conn):
    """Default depth=2 should walk two hops."""
    fa = _insert_file(conn, "a.py")
    fb = _insert_file(conn, "b.py")
    fc = _insert_file(conn, "c.py")
    s_a = _insert_symbol(conn, fa, "fn_a", line=1)
    s_b = _insert_symbol(conn, fb, "fn_b", line=1)
    s_c = _insert_symbol(conn, fc, "fn_c", line=1)
    _insert_edge(conn, s_a, s_b, "calls")
    _insert_edge(conn, s_b, s_c, "calls")

    rows = impact_of(conn, "a.py")  # no depth= kwarg

    assert rows is not None
    paths = {r.path for r in rows}
    assert "c.py" in paths


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------


def test_summary_returns_summary_stats(conn):
    result = summary(conn)
    assert isinstance(result, SummaryStats)


def test_summary_empty_db(conn):
    result = summary(conn)

    assert result.files == 0
    assert result.symbols == 0
    assert result.edges == 0
    assert result.calls == 0
    assert result.imports == 0
    assert result.dead_symbols == 0
    assert result.narratives == 0
    assert result.last_parsed_at is None


def test_summary_counts_correctly(conn):
    fa = _insert_file(conn, "a.py", ts="2026-05-10T08:00:00")
    fb = _insert_file(conn, "b.py", ts="2026-05-15T10:00:00")
    s_a1 = _insert_symbol(conn, fa, "fn_a1", line=1)
    s_a2 = _insert_symbol(conn, fa, "fn_a2", line=5)
    s_b = _insert_symbol(conn, fb, "fn_b", line=1)
    _insert_edge(conn, s_a1, s_b, "calls")
    _insert_edge(conn, s_a2, s_b, "imports")
    conn.execute(
        "INSERT INTO dead_symbols (file, line, kind, name, confidence) VALUES (?, ?, ?, ?, ?)",
        ("a.py", 3, "function", "dead_fn", 60),
    )
    db.upsert_narrative(
        conn,
        topic="arch",
        scope_id="",
        content="x",
        depends_on="[]",
        generated_at="2026-01-01",
    )
    conn.commit()

    result = summary(conn)

    assert result.files == 2
    assert result.symbols == 3
    assert result.edges == 2
    assert result.calls == 1
    assert result.imports == 1
    assert result.dead_symbols == 1
    assert result.narratives == 1
    assert result.last_parsed_at == "2026-05-15T10:00:00"


def test_summary_last_parsed_at_is_max(conn):
    _insert_file(conn, "old.py", ts="2020-01-01T00:00:00")
    _insert_file(conn, "new.py", ts="2026-05-15T12:34:56")
    _insert_file(conn, "mid.py", ts="2023-06-15T00:00:00")

    result = summary(conn)

    assert result.last_parsed_at == "2026-05-15T12:34:56"
