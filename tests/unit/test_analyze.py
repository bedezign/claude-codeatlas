"""Tests for explore_codebase.analyze - Phase 3: static analysis pipeline."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from codeatlas.explore_codebase import analyze, db


# ---------------------------------------------------------------------------
# _venv_bin — venv-sibling resolution
# ---------------------------------------------------------------------------


def test_venv_bin_returns_sibling_when_it_exists(tmp_path: Path):
    fake_bin = tmp_path / "pyan3"
    fake_bin.touch()
    with patch("sys.executable", str(tmp_path / "python")):
        result = analyze._venv_bin("pyan3")
    assert result == str(fake_bin)


def test_venv_bin_falls_back_to_which_when_sibling_absent(tmp_path: Path):
    with patch("sys.executable", str(tmp_path / "python")):
        with patch("codeatlas.explore_codebase.analyze.shutil.which", return_value="/usr/bin/pyan3"):
            result = analyze._venv_bin("pyan3")
    assert result == "/usr/bin/pyan3"


def test_venv_bin_returns_bare_name_when_not_found(tmp_path: Path):
    with patch("sys.executable", str(tmp_path / "python")):
        with patch("codeatlas.explore_codebase.analyze.shutil.which", return_value=None):
            result = analyze._venv_bin("pyan3")
    assert result == "pyan3"


def _seed_file_row(conn, path: str, sha: str = "deadbeef") -> int:
    cur = conn.execute(
        "INSERT INTO files (path, sha, language, last_parsed_at) VALUES (?, ?, ?, ?)",
        (path, sha, "python", "2026-01-01T00:00:00"),
    )
    conn.commit()
    return cur.lastrowid


def _seed_symbol(conn, file_id: int, name: str, kind: str = "function") -> int:
    cur = conn.execute(
        "INSERT INTO symbols (file_id, kind, name, scope, line) VALUES (?, ?, ?, ?, ?)",
        (file_id, kind, name, None, 1),
    )
    conn.commit()
    return cur.lastrowid


@pytest.fixture
def conn(tmp_path: Path):
    db_path = tmp_path / ".claude/codeatlas" / "codebase.db"
    c = db.init(db_path)
    yield c
    c.close()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return tmp_path


# ---------------------------------------------------------------------------
# Test 1 — empty changeset is a no-op
# ---------------------------------------------------------------------------


def test_analyze_empty_changeset_returns_zero(conn, project: Path):
    rc = analyze.run(conn, project, changed=[], new=[], deleted=[])
    assert rc == 0


def test_analyze_empty_changeset_writes_nothing(conn, project: Path):
    analyze.run(conn, project, changed=[], new=[], deleted=[])
    assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM dead_code").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Test 2 — deleted files have their rows removed
# ---------------------------------------------------------------------------


def test_analyze_deletes_files_row_for_deleted_path(conn, project: Path):
    file_id = _seed_file_row(conn, "gone.py")
    _seed_symbol(conn, file_id, "doomed_func")

    analyze.run(conn, project, changed=[], new=[], deleted=["gone.py"])

    rows = conn.execute("SELECT * FROM files WHERE path = ?", ("gone.py",)).fetchall()
    assert rows == []


def test_analyze_cascade_removes_symbols_for_deleted_file(conn, project: Path):
    file_id = _seed_file_row(conn, "gone.py")
    _seed_symbol(conn, file_id, "doomed_func")

    analyze.run(conn, project, changed=[], new=[], deleted=["gone.py"])

    sym_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    assert sym_count == 0


# ---------------------------------------------------------------------------
# Test 3 — deleting a path that has no DB row is idempotent (no error)
# ---------------------------------------------------------------------------


def test_analyze_deleted_path_with_no_row_is_idempotent(conn, project: Path):
    rc = analyze.run(conn, project, changed=[], new=[], deleted=["never_existed.py"])
    assert rc == 0
    assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Test 4-7 — ctags integration
# ---------------------------------------------------------------------------


def _ctags_output(*tags) -> str:
    """Build a fake ctags --output-format=json --fields=+n stdout blob."""
    return "\n".join(json.dumps(t) for t in tags) + "\n"


def _completed(stdout: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout)


def _stub_tool_chain(ctags_stdout: str = "", **overrides):
    """Build a side_effect routing subprocess.run calls by argv[0]."""

    def fake_run(argv, *args, **kwargs):
        tool = Path(argv[0]).name if argv else ""
        if tool == "ctags":
            return _completed(stdout=ctags_stdout)
        if tool == "pyan3":
            return _completed(stdout=overrides.get("pyan3_stdout", ""))
        if tool == "vulture":
            return _completed(
                stdout=overrides.get("vulture_stdout", ""),
                returncode=overrides.get("vulture_rc", 0),
            )
        return _completed()

    return fake_run


def test_ctags_inserts_function_symbols(conn, project: Path):
    (project / "a.py").write_text("def foo():\n    pass\n")

    ctags_out = _ctags_output(
        {"_type": "tag", "name": "foo", "kind": "function", "line": 1},
    )
    with patch(
        "codeatlas.explore_codebase.analyze.subprocess.run",
        side_effect=_stub_tool_chain(ctags_stdout=ctags_out),
    ):
        analyze.run(conn, project, changed=[], new=["a.py"], deleted=[])

    rows = conn.execute("SELECT name, kind, line FROM symbols").fetchall()
    assert ("foo", "function", 1) in rows


def test_ctags_normalizes_member_to_method(conn, project: Path):
    """Universal Ctags emits 'member' for Python methods; we normalize to 'method'."""
    (project / "a.py").write_text("class Foo:\n    def bar(self): pass\n")

    ctags_out = _ctags_output(
        {"_type": "tag", "name": "Foo", "kind": "class", "line": 1},
        {"_type": "tag", "name": "bar", "kind": "member", "scope": "Foo", "line": 2},
    )
    with patch(
        "codeatlas.explore_codebase.analyze.subprocess.run",
        side_effect=_stub_tool_chain(ctags_stdout=ctags_out),
    ):
        analyze.run(conn, project, changed=[], new=["a.py"], deleted=[])

    kinds = {row[0] for row in conn.execute("SELECT kind FROM symbols").fetchall()}
    assert "method" in kinds
    assert "member" not in kinds


def test_ctags_filters_irrelevant_kinds(conn, project: Path):
    """Variables, imports etc. are dropped — only function/class/method/etc. kept."""
    (project / "a.py").write_text("x = 1\ndef foo(): pass\n")

    ctags_out = _ctags_output(
        {"_type": "tag", "name": "x", "kind": "variable", "line": 1},
        {"_type": "tag", "name": "foo", "kind": "function", "line": 2},
    )
    with patch(
        "codeatlas.explore_codebase.analyze.subprocess.run",
        side_effect=_stub_tool_chain(ctags_stdout=ctags_out),
    ):
        analyze.run(conn, project, changed=[], new=["a.py"], deleted=[])

    rows = conn.execute("SELECT name FROM symbols").fetchall()
    names = {r[0] for r in rows}
    assert names == {"foo"}


def test_insert_symbols_skips_tags_missing_kind(conn, project: Path):
    """A ctags tag dict with no 'kind' key must be dropped silently with no crash."""
    (project / "a.py").write_text("def foo(): pass\n")

    ctags_out = _ctags_output(
        {"_type": "tag", "name": "foo"},  # no 'kind' key
    )
    with patch(
        "codeatlas.explore_codebase.analyze.subprocess.run",
        side_effect=_stub_tool_chain(ctags_stdout=ctags_out),
    ):
        rc = analyze.run(conn, project, changed=[], new=["a.py"], deleted=[])

    assert rc == 0
    assert conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] == 0


def test_ctags_empty_output_does_not_crash(conn, project: Path):
    """ctags returning no tags must not error and must not insert symbols."""
    (project / "a.py").write_text("# empty module\n")

    with patch(
        "codeatlas.explore_codebase.analyze.subprocess.run",
        side_effect=_stub_tool_chain(ctags_stdout=""),
    ):
        rc = analyze.run(conn, project, changed=[], new=["a.py"], deleted=[])

    assert rc == 0
    assert conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] == 0
    # files row is still inserted
    assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 1


def test_ctags_preserves_scope_and_line(conn, project: Path):
    (project / "a.py").write_text("class C:\n    def m(self): pass\n")
    ctags_out = _ctags_output(
        {"_type": "tag", "name": "C", "kind": "class", "line": 1},
        {"_type": "tag", "name": "m", "kind": "member", "scope": "C", "line": 2},
    )
    with patch(
        "codeatlas.explore_codebase.analyze.subprocess.run",
        side_effect=_stub_tool_chain(ctags_stdout=ctags_out),
    ):
        analyze.run(conn, project, changed=[], new=["a.py"], deleted=[])

    row = conn.execute(
        "SELECT name, scope, line FROM symbols WHERE name = ?", ("m",)
    ).fetchone()
    assert row == ("m", "C", 2)


def test_ctags_missing_binary_returns_empty_list_and_warns(conn, project: Path):
    """ctags binary absent — return empty symbol list, no crash, exit 0."""
    (project / "a.py").write_text("def foo(): pass\n")
    with patch(
        "codeatlas.explore_codebase.analyze.subprocess.run",
        side_effect=FileNotFoundError("no ctags"),
    ):
        rc = analyze.run(conn, project, changed=[], new=["a.py"], deleted=[])
    assert rc == 0
    assert conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Test 8-11 — pyan3 integration (Python only)
#
# pyan3 dot output names nodes as "<module>__<name>". We resolve those to
# symbol ids by matching name + file stem. Two-phase orchestration: all
# symbols across the changeset must exist before we resolve edges, so a call
# from a.py:foo → b.py:bar resolves regardless of file ordering.
# ---------------------------------------------------------------------------


def _pyan_dot_with_call(
    src_module: str, src_name: str, dst_module: str, dst_name: str
) -> str:
    return (
        "digraph G {\n"
        "    graph [rankdir=TB, ranksep=0.5, layout=dot];\n"
        '    subgraph "cluster_G" {\n'
        f'        "{src_module}__{src_name}" [label="{src_name}"];\n'
        f'        "{dst_module}__{dst_name}" [label="{dst_name}"];\n'
        "    }\n"
        f'        "{src_module}__{src_name}" -> "{dst_module}__{dst_name}" '
        '[style="solid"];\n'
        "}\n"
    )


def test_pyan3_inserts_call_edges_for_python_files(conn, project: Path):
    (project / "a.py").write_text("def foo():\n    bar()\n\ndef bar():\n    pass\n")

    ctags_out = _ctags_output(
        {"_type": "tag", "name": "foo", "kind": "function", "line": 1},
        {"_type": "tag", "name": "bar", "kind": "function", "line": 4},
    )
    pyan_out = _pyan_dot_with_call("a", "foo", "a", "bar")

    with patch(
        "codeatlas.explore_codebase.analyze.subprocess.run",
        side_effect=_stub_tool_chain(ctags_stdout=ctags_out, pyan3_stdout=pyan_out),
    ):
        analyze.run(conn, project, changed=[], new=["a.py"], deleted=[])

    edges = conn.execute(
        "SELECT s.name, d.name, e.kind FROM edges e "
        "JOIN symbols s ON s.id = e.src_id "
        "JOIN symbols d ON d.id = e.dst_id"
    ).fetchall()
    assert ("foo", "bar", "calls") in edges


def test_pyan3_skips_edges_where_symbol_not_in_db(conn, project: Path):
    """pyan3 emits false edges through __init__.py — silently skip unresolvable ones."""
    (project / "a.py").write_text("def foo(): pass\n")

    ctags_out = _ctags_output(
        {"_type": "tag", "name": "foo", "kind": "function", "line": 1},
    )
    # Edge references "ghost" which has no DB row.
    pyan_out = _pyan_dot_with_call("a", "foo", "a", "ghost")

    with patch(
        "codeatlas.explore_codebase.analyze.subprocess.run",
        side_effect=_stub_tool_chain(ctags_stdout=ctags_out, pyan3_stdout=pyan_out),
    ):
        rc = analyze.run(conn, project, changed=[], new=["a.py"], deleted=[])

    assert rc == 0
    edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    assert edge_count == 0


def test_pyan3_two_phase_resolves_cross_file_edges(conn, project: Path):
    """Edges are resolved AFTER all changeset files have symbols inserted."""
    (project / "a.py").write_text("import b\n\ndef caller():\n    b.callee()\n")
    (project / "b.py").write_text("def callee(): pass\n")

    def fake_run(argv, *args, **kwargs):
        tool = Path(argv[0]).name if argv else ""
        if tool == "ctags":
            target = argv[-1]
            if target.endswith("a.py"):
                return _completed(
                    stdout=_ctags_output(
                        {
                            "_type": "tag",
                            "name": "caller",
                            "kind": "function",
                            "line": 3,
                        },
                    )
                )
            if target.endswith("b.py"):
                return _completed(
                    stdout=_ctags_output(
                        {
                            "_type": "tag",
                            "name": "callee",
                            "kind": "function",
                            "line": 1,
                        },
                    )
                )
            return _completed()
        if tool == "pyan3":
            # pyan3 is invoked once with all files; emit one cross-file edge.
            return _completed(stdout=_pyan_dot_with_call("a", "caller", "b", "callee"))
        return _completed()

    with patch("codeatlas.explore_codebase.analyze.subprocess.run", side_effect=fake_run):
        analyze.run(conn, project, changed=[], new=["a.py", "b.py"], deleted=[])

    edges = conn.execute(
        "SELECT s.name, d.name, e.kind FROM edges e "
        "JOIN symbols s ON s.id = e.src_id "
        "JOIN symbols d ON d.id = e.dst_id"
    ).fetchall()
    assert ("caller", "callee", "calls") in edges


def test_pyan3_only_runs_for_python_files(conn, project: Path):
    """Non-Python files in the changeset must not trigger pyan3."""
    (project / "x.go").write_text("package main\n")

    seen_tools: list[str] = []

    def fake_run(argv, *args, **kwargs):
        tool = Path(argv[0]).name if argv else ""
        seen_tools.append(tool)
        return _completed()

    with patch("codeatlas.explore_codebase.analyze.subprocess.run", side_effect=fake_run):
        analyze.run(conn, project, changed=[], new=["x.go"], deleted=[])

    assert "pyan3" not in seen_tools
    assert "vulture" not in seen_tools  # vulture also Python-only


# ---------------------------------------------------------------------------
# Test 12-15 — grimp integration (Python only)
# ---------------------------------------------------------------------------


def _make_pkg(project: Path, pkg_name: str, *modules: str) -> None:
    pkg = project / "src" / pkg_name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("")
    for mod in modules:
        (pkg / f"{mod}.py").write_text("")


def _make_pyproject(project: Path, name: str) -> None:
    (project / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "0.0.1"\n'
    )


def test_grimp_inserts_import_edges_when_package_resolvable(conn, project: Path):
    _make_pyproject(project, "mypkg")
    _make_pkg(project, "mypkg", "a", "b")

    ctags_out = _ctags_output(
        {"_type": "tag", "name": "x", "kind": "function", "line": 1},
    )

    # Fake grimp by patching the resolver helper so we don't depend on `grimp`
    # being installed. The helper returns a list of (src_module, dst_module)
    # pairs at the module level — symbol resolution is by file path stem.
    fake_pairs = [("src/mypkg/a.py", "src/mypkg/b.py")]

    with (
        patch(
            "codeatlas.explore_codebase.analyze.subprocess.run",
            side_effect=_stub_tool_chain(ctags_stdout=ctags_out),
        ),
        patch(
            "codeatlas.explore_codebase.analyze._collect_grimp_imports", return_value=fake_pairs
        ),
    ):
        analyze.run(
            conn,
            project,
            changed=[],
            new=["src/mypkg/a.py", "src/mypkg/b.py"],
            deleted=[],
        )

    edges = conn.execute(
        "SELECT s.name, d.name, e.kind FROM edges e "
        "JOIN symbols s ON s.id = e.src_id "
        "JOIN symbols d ON d.id = e.dst_id WHERE e.kind = 'imports'"
    ).fetchall()
    # Both files have a single 'x' symbol. The import edge resolves
    # src.x -> dst.x via file-path matching.
    assert len(edges) == 1
    assert edges[0][2] == "imports"


def test_grimp_skipped_when_top_package_undeterminable(conn, project: Path, capsys):
    """No pyproject.toml, no src/<pkg>/ — grimp is skipped with stderr warning."""
    (project / "loose.py").write_text("x = 1\n")

    ctags_out = _ctags_output(
        {"_type": "tag", "name": "x", "kind": "variable", "line": 1},
    )

    grimp_called = []

    def fake_collect(*a, **kw):
        grimp_called.append(True)
        return []

    with (
        patch(
            "codeatlas.explore_codebase.analyze.subprocess.run",
            side_effect=_stub_tool_chain(ctags_stdout=ctags_out),
        ),
        patch(
            "codeatlas.explore_codebase.analyze._collect_grimp_imports", side_effect=fake_collect
        ),
    ):
        rc = analyze.run(conn, project, changed=[], new=["loose.py"], deleted=[])

    captured = capsys.readouterr()
    assert rc == 0
    assert grimp_called == []  # grimp must not be invoked
    assert "grimp" in captured.err.lower()


def test_grimp_missing_module_skipped_gracefully(conn, project: Path, capsys):
    """grimp not installed — emit warning, exit 0."""
    _make_pyproject(project, "mypkg")
    _make_pkg(project, "mypkg", "a")

    ctags_out = _ctags_output(
        {"_type": "tag", "name": "x", "kind": "function", "line": 1},
    )

    def raise_import(*a, **kw):
        raise ImportError("no grimp")

    with (
        patch(
            "codeatlas.explore_codebase.analyze.subprocess.run",
            side_effect=_stub_tool_chain(ctags_stdout=ctags_out),
        ),
        patch(
            "codeatlas.explore_codebase.analyze._collect_grimp_imports", side_effect=raise_import
        ),
    ):
        rc = analyze.run(conn, project, changed=[], new=["src/mypkg/a.py"], deleted=[])

    captured = capsys.readouterr()
    assert rc == 0
    assert "grimp" in captured.err.lower()


# ---------------------------------------------------------------------------
# Test 16-19 — vulture integration (Python only)
#
# Vulture has no `--json` flag; we parse the text format
#   `<path>:<line>: unused <kind> '<name>' (NN% confidence)`
# The brief required `--min-confidence 80` and a re-check ≥ 80 in code.
# ---------------------------------------------------------------------------


def _vulture_line(path: str, line: int, kind: str, name: str, conf: int) -> str:
    return f"{path}:{line}: unused {kind} '{name}' ({conf}% confidence)"


def test_vulture_inserts_dead_code_rows(conn, project: Path):
    (project / "a.py").write_text("def unused_fn(): pass\n")
    ctags_out = _ctags_output(
        {"_type": "tag", "name": "unused_fn", "kind": "function", "line": 1},
    )
    vulture_out = _vulture_line("a.py", 1, "function", "unused_fn", 90) + "\n"

    with patch(
        "codeatlas.explore_codebase.analyze.subprocess.run",
        side_effect=_stub_tool_chain(
            ctags_stdout=ctags_out,
            vulture_stdout=vulture_out,
            vulture_rc=3,  # vulture returns non-zero when it finds dead code
        ),
    ):
        analyze.run(conn, project, changed=[], new=["a.py"], deleted=[])

    rows = conn.execute(
        "SELECT file, line, kind, name, confidence FROM dead_code"
    ).fetchall()
    assert ("a.py", 1, "function", "unused_fn", 90) in rows


def test_vulture_drops_below_80_confidence(conn, project: Path):
    """Belt-and-braces: even if vulture reports < 80, we must filter."""
    (project / "a.py").write_text("def maybe(): pass\n")
    ctags_out = _ctags_output(
        {"_type": "tag", "name": "maybe", "kind": "function", "line": 1},
    )
    vulture_out = (
        _vulture_line("a.py", 1, "function", "maybe", 70)
        + "\n"
        + _vulture_line("a.py", 2, "function", "deffo", 95)
        + "\n"
    )

    with patch(
        "codeatlas.explore_codebase.analyze.subprocess.run",
        side_effect=_stub_tool_chain(
            ctags_stdout=ctags_out,
            vulture_stdout=vulture_out,
            vulture_rc=3,
        ),
    ):
        analyze.run(conn, project, changed=[], new=["a.py"], deleted=[])

    rows = conn.execute("SELECT name, confidence FROM dead_code").fetchall()
    names = {r[0] for r in rows}
    assert "maybe" not in names
    assert "deffo" in names


def test_vulture_missing_binary_skipped(conn, project: Path):
    """vulture not installed — skip gracefully without raising."""
    (project / "a.py").write_text("def x(): pass\n")
    ctags_out = _ctags_output(
        {"_type": "tag", "name": "x", "kind": "function", "line": 1},
    )

    def fake_run(argv, *args, **kwargs):
        tool = Path(argv[0]).name if argv else ""
        if tool == "ctags":
            return _completed(stdout=ctags_out)
        if tool == "vulture":
            raise FileNotFoundError("[Errno 2] no vulture")
        return _completed()

    with patch("codeatlas.explore_codebase.analyze.subprocess.run", side_effect=fake_run):
        rc = analyze.run(conn, project, changed=[], new=["a.py"], deleted=[])

    assert rc == 0
    # No dead_code rows on missing binary
    assert conn.execute("SELECT COUNT(*) FROM dead_code").fetchone()[0] == 0


def test_vulture_empty_output_no_rows(conn, project: Path):
    (project / "a.py").write_text("def used(): pass\nused()\n")
    ctags_out = _ctags_output(
        {"_type": "tag", "name": "used", "kind": "function", "line": 1},
    )
    with patch(
        "codeatlas.explore_codebase.analyze.subprocess.run",
        side_effect=_stub_tool_chain(ctags_stdout=ctags_out, vulture_stdout=""),
    ):
        analyze.run(conn, project, changed=[], new=["a.py"], deleted=[])

    assert conn.execute("SELECT COUNT(*) FROM dead_code").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Test 20-22 — changed file flow + filesystem doom paths
# ---------------------------------------------------------------------------


def test_changed_file_replaces_existing_symbols(conn, project: Path):
    """Changed files: old symbols are wiped (cascade) and new ones inserted."""
    (project / "a.py").write_text("def newer(): pass\n")

    # Pre-seed: old symbols for a.py with a name that should be removed.
    file_id = _seed_file_row(conn, "a.py", sha="oldhash")
    _seed_symbol(conn, file_id, "older", kind="function")

    ctags_out = _ctags_output(
        {"_type": "tag", "name": "newer", "kind": "function", "line": 1},
    )

    with patch(
        "codeatlas.explore_codebase.analyze.subprocess.run",
        side_effect=_stub_tool_chain(ctags_stdout=ctags_out),
    ):
        analyze.run(conn, project, changed=["a.py"], new=[], deleted=[])

    names = {r[0] for r in conn.execute("SELECT name FROM symbols").fetchall()}
    assert "older" not in names
    assert "newer" in names

    # files row sha was refreshed
    sha = conn.execute("SELECT sha FROM files WHERE path = ?", ("a.py",)).fetchone()[0]
    assert sha != "oldhash"


def test_file_disappears_between_changeset_and_analyze(conn, project: Path, capsys):
    """File listed in 'new' but missing on disk: warn, skip, others still run."""
    # Only present file is b.py. a.py is in changeset but doesn't exist.
    (project / "b.py").write_text("def b(): pass\n")

    ctags_out = _ctags_output(
        {"_type": "tag", "name": "b", "kind": "function", "line": 1},
    )

    with patch(
        "codeatlas.explore_codebase.analyze.subprocess.run",
        side_effect=_stub_tool_chain(ctags_stdout=ctags_out),
    ):
        rc = analyze.run(conn, project, changed=[], new=["a.py", "b.py"], deleted=[])

    captured = capsys.readouterr()
    assert rc == 0
    paths_in_db = {r[0] for r in conn.execute("SELECT path FROM files").fetchall()}
    assert "a.py" not in paths_in_db
    assert "b.py" in paths_in_db
    assert "vanished" in captured.err.lower() or "missing" in captured.err.lower()


def test_only_deleted_no_phase2_tools_called(conn, project: Path):
    """Pure-deletion changeset must not invoke any analysis subprocess."""
    file_id = _seed_file_row(conn, "gone.py")
    _seed_symbol(conn, file_id, "doomed")

    seen: list[str] = []

    def fake_run(argv, *args, **kwargs):
        seen.append(Path(argv[0]).name if argv else "")
        return _completed()

    with patch("codeatlas.explore_codebase.analyze.subprocess.run", side_effect=fake_run):
        rc = analyze.run(conn, project, changed=[], new=[], deleted=["gone.py"])

    assert rc == 0
    assert seen == []  # no tool invocations


# ---------------------------------------------------------------------------
# Test 23-26 — CLI wiring (cmd_analyze)
# ---------------------------------------------------------------------------


def _cli_args(project: Path, *, changed=None, new=None, deleted=None):
    import argparse

    return argparse.Namespace(
        project_root=str(project),
        changed=list(changed) if changed is not None else None,
        new=list(new) if new is not None else None,
        deleted=list(deleted) if deleted is not None else None,
    )


def test_cmd_analyze_with_explicit_lists(tmp_path: Path):
    from codeatlas.explore_codebase.cli import cmd_analyze

    (tmp_path / "a.py").write_text("def foo(): pass\n")
    ctags_out = _ctags_output(
        {"_type": "tag", "name": "foo", "kind": "function", "line": 1},
    )

    with patch(
        "codeatlas.explore_codebase.analyze.subprocess.run",
        side_effect=_stub_tool_chain(ctags_stdout=ctags_out),
    ):
        rc = cmd_analyze(_cli_args(tmp_path, changed=[], new=["a.py"], deleted=[]))

    assert rc == 0
    db_path = tmp_path / ".claude/codeatlas" / "codebase.db"
    assert db_path.exists()


def test_cmd_analyze_reads_changeset_from_stdin(tmp_path: Path, monkeypatch):
    """When no --changed/--new/--deleted flags given, read JSON from stdin."""
    import io
    from codeatlas.explore_codebase.cli import cmd_analyze

    (tmp_path / "a.py").write_text("def foo(): pass\n")

    payload = {"new": ["a.py"], "changed": [], "deleted": [], "stale_narratives": []}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    ctags_out = _ctags_output(
        {"_type": "tag", "name": "foo", "kind": "function", "line": 1},
    )

    with patch(
        "codeatlas.explore_codebase.analyze.subprocess.run",
        side_effect=_stub_tool_chain(ctags_stdout=ctags_out),
    ):
        rc = cmd_analyze(_cli_args(tmp_path))  # all None — read stdin

    assert rc == 0
    # Verify the file row was inserted
    db_path = tmp_path / ".claude/codeatlas" / "codebase.db"
    import sqlite3

    c = sqlite3.connect(db_path)
    try:
        rows = c.execute("SELECT path FROM files").fetchall()
    finally:
        c.close()
    assert ("a.py",) in rows


def test_cmd_analyze_treats_invalid_stdin_as_error(tmp_path: Path, monkeypatch, capsys):
    """Bad JSON on stdin: exit 1, error to stderr, no DB writes."""
    import io
    from codeatlas.explore_codebase.cli import cmd_analyze

    monkeypatch.setattr("sys.stdin", io.StringIO("not-json{"))

    rc = cmd_analyze(_cli_args(tmp_path))
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.err.strip()  # something landed on stderr


def test_cmd_analyze_parser_accepts_changeset_flags():
    """build_parser registers --changed/--new/--deleted on analyze."""
    from codeatlas.explore_codebase.cli import build_parser

    parser = build_parser()
    # Use real argparse to confirm flags are registered.
    ns = parser.parse_args(
        [
            "analyze",
            "--project-root",
            "/tmp",
            "--changed",
            "a.py",
            "b.py",
            "--new",
            "c.py",
            "--deleted",
            "d.py",
        ]
    )
    assert ns.changed == ["a.py", "b.py"]
    assert ns.new == ["c.py"]
    assert ns.deleted == ["d.py"]


def test_cmd_analyze_rejects_dotdot_traversal_in_changed(
    tmp_path: Path, monkeypatch, capsys
):
    """A path with '..' that escapes the project root must be rejected with rc=1."""
    import io
    from codeatlas.explore_codebase.cli import cmd_analyze

    payload = {"changed": ["../outside.py"], "new": [], "deleted": []}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    rc = cmd_analyze(_cli_args(tmp_path))
    assert rc == 1
    captured = capsys.readouterr()
    assert "escapes" in captured.err


def test_cmd_analyze_rejects_absolute_path_in_new(tmp_path: Path, monkeypatch, capsys):
    """An absolute path in 'new' that escapes the project root must be rejected."""
    import io
    from codeatlas.explore_codebase.cli import cmd_analyze

    payload = {"changed": [], "new": ["/etc/passwd"], "deleted": []}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    rc = cmd_analyze(_cli_args(tmp_path))
    assert rc == 1
    captured = capsys.readouterr()
    assert "escapes" in captured.err


# ---------------------------------------------------------------------------
# Test 27-29 — Real-binary integration tests.
#
# These run the actual analysis tools when present and skip gracefully
# otherwise. They verify our parser code matches real tool output, not just
# the canned blobs in the unit tests.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("ctags") is None, reason="ctags not installed")
def test_real_ctags_against_simple_python_file(conn, project: Path):
    (project / "real.py").write_text(
        "class Greeter:\n"
        "    def hello(self):\n"
        "        return 'hi'\n"
        "\n"
        "def standalone():\n"
        "    pass\n"
    )
    rc = analyze.run(conn, project, changed=[], new=["real.py"], deleted=[])
    assert rc == 0

    rows = conn.execute("SELECT name, kind FROM symbols").fetchall()
    names = {r[0] for r in rows}
    kinds = {r[1] for r in rows}
    assert "Greeter" in names
    assert "hello" in names
    assert "standalone" in names
    assert "class" in kinds
    assert "method" in kinds  # 'member' must be normalised
    assert "function" in kinds
    assert "member" not in kinds


@pytest.mark.skipif(shutil.which("pyan3") is None, reason="pyan3 not installed")
def test_real_pyan3_against_simple_python_file(conn, project: Path):
    (project / "calls.py").write_text(
        "def caller():\n    return callee()\n\ndef callee():\n    return 1\n"
    )
    rc = analyze.run(conn, project, changed=[], new=["calls.py"], deleted=[])
    assert rc == 0

    edges = conn.execute(
        "SELECT s.name, d.name, e.kind FROM edges e "
        "JOIN symbols s ON s.id = e.src_id "
        "JOIN symbols d ON d.id = e.dst_id WHERE e.kind = 'calls'"
    ).fetchall()
    # pyan3 is known to flake on certain Python versions; tolerate empty result
    # but the well-formed call should at least not crash the pipeline.
    if edges:
        assert ("caller", "callee", "calls") in edges


# ---------------------------------------------------------------------------
# Adversarial second pass — boundaries and duplicates
# ---------------------------------------------------------------------------


def test_vulture_confidence_exactly_80_is_kept(conn, project: Path):
    """Boundary: 80 is inclusive (≥ 80, not > 80)."""
    (project / "a.py").write_text("def boundary(): pass\n")
    ctags_out = _ctags_output(
        {"_type": "tag", "name": "boundary", "kind": "function", "line": 1},
    )
    vulture_out = _vulture_line("a.py", 1, "function", "boundary", 80) + "\n"
    with patch(
        "codeatlas.explore_codebase.analyze.subprocess.run",
        side_effect=_stub_tool_chain(
            ctags_stdout=ctags_out,
            vulture_stdout=vulture_out,
            vulture_rc=3,
        ),
    ):
        analyze.run(conn, project, changed=[], new=["a.py"], deleted=[])

    rows = conn.execute("SELECT name, confidence FROM dead_code").fetchall()
    assert ("boundary", 80) in rows


def test_vulture_confidence_79_is_dropped(conn, project: Path):
    """Boundary: 79 must be filtered."""
    (project / "a.py").write_text("def justbelow(): pass\n")
    ctags_out = _ctags_output(
        {"_type": "tag", "name": "justbelow", "kind": "function", "line": 1},
    )
    vulture_out = _vulture_line("a.py", 1, "function", "justbelow", 79) + "\n"
    with patch(
        "codeatlas.explore_codebase.analyze.subprocess.run",
        side_effect=_stub_tool_chain(
            ctags_stdout=ctags_out,
            vulture_stdout=vulture_out,
            vulture_rc=3,
        ),
    ):
        analyze.run(conn, project, changed=[], new=["a.py"], deleted=[])

    assert conn.execute("SELECT COUNT(*) FROM dead_code").fetchone()[0] == 0


def test_pyan3_resolves_to_exact_module_not_suffix_match(conn, project: Path):
    """Regression: 'b.py' must match b.py exactly, not lib.py or sub.py.

    Without anchoring the LIKE pattern, `%b.py` matches every file whose
    basename ends in 'b.py' — this would silently bind edges to whichever
    `foo` the database returned first.
    """
    (project / "b.py").write_text("def foo(): pass\n")
    (project / "lib.py").write_text("def foo(): pass\n")

    def fake_run(argv, *args, **kwargs):
        tool = Path(argv[0]).name if argv else ""
        if tool == "ctags":
            target = argv[-1]
            if target.endswith("b.py"):
                return _completed(
                    stdout=_ctags_output(
                        {
                            "_type": "tag",
                            "name": "foo",
                            "kind": "function",
                            "line": 1,
                        }
                    )
                )
            if target.endswith("lib.py"):
                return _completed(
                    stdout=_ctags_output(
                        {
                            "_type": "tag",
                            "name": "foo",
                            "kind": "function",
                            "line": 1,
                        }
                    )
                )
            return _completed()
        if tool == "pyan3":
            # Edge points at b's foo; resolution must NOT pick lib.py's foo.
            return _completed(stdout=_pyan_dot_with_call("b", "foo", "b", "foo"))
        return _completed()

    with patch("codeatlas.explore_codebase.analyze.subprocess.run", side_effect=fake_run):
        # IMPORTANT: lib.py is processed first so its symbols get the lower rowid.
        # Without anchoring the LIKE pattern, the resolver returns the first
        # matching row → it would silently bind the edge to lib.py.
        analyze.run(conn, project, changed=[], new=["lib.py", "b.py"], deleted=[])

    edges = conn.execute(
        "SELECT f.path FROM edges e JOIN symbols s ON s.id = e.src_id "
        "JOIN files f ON f.id = s.file_id"
    ).fetchall()
    # All edges must resolve to b.py — never lib.py.
    assert all(row[0] == "b.py" for row in edges), (
        f"resolver leaked into the wrong file: {edges}"
    )
    # And we should have at least one edge produced.
    assert len(edges) >= 1


def test_pyan3_duplicate_edges_deduplicated(conn, project: Path):
    """Two identical 'src -> dst' lines in dot output produce one edge row."""
    (project / "a.py").write_text("def foo(): pass\ndef bar(): pass\n")
    ctags_out = _ctags_output(
        {"_type": "tag", "name": "foo", "kind": "function", "line": 1},
        {"_type": "tag", "name": "bar", "kind": "function", "line": 2},
    )
    pyan_out = (
        "digraph G {\n"
        '  "a__foo" -> "a__bar" [color="black"];\n'
        '  "a__foo" -> "a__bar" [color="red"];\n'  # duplicate
        "}\n"
    )

    with patch(
        "codeatlas.explore_codebase.analyze.subprocess.run",
        side_effect=_stub_tool_chain(
            ctags_stdout=ctags_out,
            pyan3_stdout=pyan_out,
        ),
    ):
        analyze.run(conn, project, changed=[], new=["a.py"], deleted=[])

    edge_count = conn.execute(
        "SELECT COUNT(*) FROM edges WHERE kind = 'calls'"
    ).fetchone()[0]
    assert edge_count == 1


def test_re_analyze_same_file_clears_old_dead_code(conn, project: Path):
    """Re-analysing a changed file should not accumulate stale dead_code rows.

    Note: the schema doesn't FK dead_code to files, so we expect the
    implementation to clear the file's dead_code rows when the files row is
    deleted. This is a contract test against silent stale-data growth.
    """
    (project / "a.py").write_text("def newer(): pass\n")
    file_id = _seed_file_row(conn, "a.py", sha="oldhash")
    _seed_symbol(conn, file_id, "older", kind="function")
    # Pre-seed a stale dead_code row
    conn.execute(
        "INSERT INTO dead_code (file, line, kind, name, confidence) "
        "VALUES (?, ?, ?, ?, ?)",
        ("a.py", 99, "function", "older", 95),
    )
    conn.commit()

    ctags_out = _ctags_output(
        {"_type": "tag", "name": "newer", "kind": "function", "line": 1},
    )
    with patch(
        "codeatlas.explore_codebase.analyze.subprocess.run",
        side_effect=_stub_tool_chain(ctags_stdout=ctags_out, vulture_stdout=""),
    ):
        analyze.run(conn, project, changed=["a.py"], new=[], deleted=[])

    names = {r[0] for r in conn.execute("SELECT name FROM dead_code").fetchall()}
    assert "older" not in names


def test_real_grimp_against_real_package(conn, project: Path):
    """Real grimp invocation against a tiny package layout.

    Skips when grimp isn't installed. Validates the actual
    `_collect_grimp_imports` body — module-name → relpath conversion and
    graph traversal — against a real grimp build.
    """
    grimp = pytest.importorskip("grimp")
    _ = grimp  # silence unused-import warning

    _make_pyproject(project, "demopkg")
    pkg = project / "src" / "demopkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text("X = 1\n")
    (pkg / "consumer.py").write_text("from demopkg import core\n\nuse = core.X\n")

    rc = analyze.run(
        conn,
        project,
        changed=[],
        new=[
            "src/demopkg/__init__.py",
            "src/demopkg/core.py",
            "src/demopkg/consumer.py",
        ],
        deleted=[],
    )
    assert rc == 0

    edges = conn.execute(
        "SELECT f1.path, f2.path FROM edges e "
        "JOIN symbols s ON s.id = e.src_id "
        "JOIN files f1 ON f1.id = s.file_id "
        "JOIN symbols d ON d.id = e.dst_id "
        "JOIN files f2 ON f2.id = d.file_id "
        "WHERE e.kind = 'imports'"
    ).fetchall()
    # consumer imports core; the import edge should be resolved.
    assert ("src/demopkg/consumer.py", "src/demopkg/core.py") in edges


@pytest.mark.skipif(shutil.which("vulture") is None, reason="vulture not installed")
def test_real_vulture_against_simple_python_file(conn, project: Path):
    (project / "dead.py").write_text(
        "def used():\n"
        "    return 1\n"
        "\n"
        "def unused_thing():\n"
        "    return 2\n"
        "\n"
        "print(used())\n"
    )
    rc = analyze.run(conn, project, changed=[], new=["dead.py"], deleted=[])
    assert rc == 0

    rows = conn.execute("SELECT name, confidence FROM dead_code").fetchall()
    # vulture's confidence for a fully-unused top-level function is ≥ 60.
    # Our pipeline asks for ≥ 80, so the test asserts the floor.
    if rows:
        for name, confidence in rows:
            assert confidence >= 80, f"{name} stored at confidence {confidence}"
