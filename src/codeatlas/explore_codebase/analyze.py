"""Phase 3: static analysis pipeline for explore-codebase."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _venv_bin(name: str) -> str:
    """Resolve a CLI tool co-installed in the same venv as this interpreter.

    When the CLI runs from a venv (e.g. $PLUGIN_DATA/.venv/bin/explore-codebase),
    the venv's bin/ is not on PATH, so plain subprocess("pyan3") fails even though
    pyan3 was installed there by bootstrap. Check the sibling path first.
    """
    sibling = Path(sys.executable).parent / name
    if sibling.is_file():
        return str(sibling)
    return shutil.which(name) or name


_INIT_PY = "__init__.py"

# ctags emits Python methods as 'member'. Normalise to 'method' for uniformity.
_KIND_NORMALIZE = {"member": "method"}

# Allowlist of symbol kinds the analyser stores. Other kinds (variable, import,
# typedef, anchor, etc.) are silently dropped.
_KEPT_KINDS = {
    "function",
    "class",
    "method",
    "module",
    "interface",
    "struct",
    "trait",
    "enum",
}


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

_LANG_BY_EXT = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".go": "go",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
}


def _language_for(path: str) -> str | None:
    return _LANG_BY_EXT.get(Path(path).suffix)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _delete_file_rows(conn, paths) -> None:
    """Remove files rows for paths; CASCADE wipes their symbols and edges.

    dead_code is not foreign-keyed (table holds untyped path strings), so it
    needs an explicit DELETE alongside the cascade.
    """
    for path in paths:
        conn.execute("DELETE FROM files WHERE path = ?", (path,))
        conn.execute("DELETE FROM dead_code WHERE file = ?", (path,))
    conn.commit()


def _insert_file(conn, path: str, sha: str) -> int:
    cur = conn.execute(
        "INSERT INTO files (path, sha, language, last_parsed_at) VALUES (?, ?, ?, ?)",
        (path, sha, _language_for(path), _now_iso()),
    )
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# ctags integration
# ---------------------------------------------------------------------------


def _run_ctags(abs_path: Path) -> list[dict]:
    """Run ctags on a single file and return the parsed JSON tag list."""
    try:
        proc = subprocess.run(
            ["ctags", "--output-format=json", "--fields=+n", "-f", "-", str(abs_path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except FileNotFoundError:
        logger.warning(
            "ctags not found — install universal-ctags to enable symbol extraction"
        )
        return []
    except subprocess.TimeoutExpired:
        logger.warning("ctags timed out on %s", abs_path)
        return []
    if proc.returncode != 0:
        return []
    tags: list[dict] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            tags.append(json.loads(line))
        except json.JSONDecodeError:
            # ctags can prefix non-JSON lines (rare, but defensive)
            continue
    return tags


def _insert_symbols(conn, file_id: int, tags) -> None:
    for tag in tags:
        kind = _KIND_NORMALIZE.get(tag.get("kind"), tag.get("kind"))
        if kind not in _KEPT_KINDS:
            continue
        conn.execute(
            "INSERT INTO symbols (file_id, kind, name, scope, line) "
            "VALUES (?, ?, ?, ?, ?)",
            (file_id, kind, tag.get("name", ""), tag.get("scope"), tag.get("line")),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# pyan3 integration (Python only)
# ---------------------------------------------------------------------------

# pyan3 dot output references nodes as `"<module>__<name>"`. Edge lines look
# like `"src" -> "dst" [...attrs...]`. We extract the (module, name) tuple
# (split on the LAST `__` so module names containing `_` work) and resolve
# against the symbol table.
_PYAN_EDGE_RE = re.compile(r'"([^"]+)"\s*->\s*"([^"]+)"')


def _split_pyan_node(node: str) -> tuple[str, str] | None:
    """Split 'a__foo' into ('a', 'foo'). Returns None if no '__' separator."""
    if "__" not in node:
        return None
    # Find the LAST `__` so module names containing `_` work.
    idx = node.rfind("__")
    return node[:idx], node[idx + 2 :]


def _python_paths(paths) -> list[str]:
    return [p for p in paths if Path(p).suffix == ".py"]


def _run_pyan3(abs_paths: list[Path]) -> str:
    """Run pyan3 once with all files. Returns dot output, or '' on failure."""
    if not abs_paths:
        return ""
    try:
        proc = subprocess.run(
            [_venv_bin("pyan3"), *(str(p) for p in abs_paths), "--dot", "--no-defines"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        logger.warning("pyan3 timed out")
        return ""
    except FileNotFoundError:
        sys.stderr.write(
            "explore-codebase: pyan3 not installed; skipping calls graph\n"
        )
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout


def _resolve_symbol(conn, module: str, name: str) -> int | None:
    """Map a pyan3 (module, name) reference to a symbol id.

    Matches f.path against `<module>.py` exactly (root-level file) or
    `<anything>/<module>.py` (file in a subdirectory). A naive `%<module>.py`
    LIKE matches everything ending in those bytes — `b.py` would also match
    `lib.py`, leading to silent wrong-file resolution.
    """
    rows = conn.execute(
        "SELECT s.id FROM symbols s JOIN files f ON f.id = s.file_id "
        "WHERE s.name = ? AND (f.path = ? OR f.path LIKE ?)",
        (name, f"{module}.py", f"%/{module}.py"),
    ).fetchall()
    if not rows:
        return None
    if len(rows) > 1:
        logger.debug(
            "_resolve_symbol: ambiguous match for %s.%s (%d rows)",
            module,
            name,
            len(rows),
        )
        return None
    return rows[0][0]


def _insert_call_edges(conn, project_root: Path, py_rel_paths: list[str]) -> None:
    """Run pyan3 against the Python files and insert resolvable call edges."""
    if not py_rel_paths:
        return
    abs_paths = [project_root / p for p in py_rel_paths]
    dot_out = _run_pyan3(abs_paths)
    seen: set[tuple[int, int]] = set()
    for match in _PYAN_EDGE_RE.finditer(dot_out):
        src_node, dst_node = match.group(1), match.group(2)
        src = _split_pyan_node(src_node)
        dst = _split_pyan_node(dst_node)
        if src is None or dst is None:
            continue
        src_id = _resolve_symbol(conn, *src)
        dst_id = _resolve_symbol(conn, *dst)
        if src_id is None or dst_id is None:
            continue
        if (src_id, dst_id) in seen:
            continue
        seen.add((src_id, dst_id))
        conn.execute(
            "INSERT INTO edges (src_id, dst_id, kind) VALUES (?, ?, ?)",
            (src_id, dst_id, "calls"),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# grimp integration (Python only)
# ---------------------------------------------------------------------------


def _lookup_pyproject_package(project_root: Path) -> tuple[str, Path] | None:
    """Strategy 1: resolve via pyproject.toml [project] name + src/<name>/ layout.

    Checks both ``src/`` and the project root for the named package directory.
    Returns ``(package_name, sys_path_entry)`` or ``None`` if not found.
    """
    pyproject = project_root / "pyproject.toml"
    name = _name_from_pyproject(pyproject) if pyproject.is_file() else None
    if not name:
        return None
    for candidate_root in (project_root / "src", project_root):
        if (candidate_root / name / _INIT_PY).is_file():
            return name, candidate_root
    return None


def _first_package_under(directory: Path) -> tuple[str, Path] | None:
    """Return the first sub-directory containing an ``__init__.py``, sorted by name.

    Returns ``(package_name, directory)`` or ``None`` if no package is found.
    """
    if not directory.is_dir():
        return None
    for entry in sorted(directory.iterdir()):
        if entry.is_dir() and (entry / _INIT_PY).is_file():
            return entry.name, directory
    return None


def _detect_top_package(project_root: Path) -> tuple[str, Path] | None:
    """Find the top-level Python package + the search root grimp must see.

    Returns (package_name, sys_path_entry). The caller adds sys_path_entry
    to sys.path so `import <package_name>` works during grimp.build_graph.
    """
    # 1. pyproject.toml [project] name + src/<name>/__init__.py layout.
    result = _lookup_pyproject_package(project_root)
    if result:
        return result
    # 2. fallback — first __init__.py under src/.
    result = _first_package_under(project_root / "src")
    if result:
        return result
    # 3. fallback — first __init__.py at project root.
    return _first_package_under(project_root)


def _name_from_pyproject(pyproject: Path) -> str | None:
    """Read the project name without pulling tomllib edge cases — match line-wise."""
    try:
        text = pyproject.read_text()
    except OSError:
        return None
    in_project = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_project = stripped == "[project]"
            continue
        if not in_project:
            continue
        m = re.match(r'^name\s*=\s*"([^"]+)"', stripped)
        if m:
            return m.group(1)
    return None


def _collect_grimp_imports(
    package_name: str,
    sys_path_entry: Path,
    project_root: Path,
) -> list[tuple[str, str]]:
    """Run grimp.build_graph and return (src_relpath, dst_relpath) pairs.

    Each tuple is a pair of project-relative .py paths. Modules grimp can't
    map back to a real file (built-ins, namespace packages) are dropped.
    Raises ImportError if grimp is not installed.
    """
    import grimp  # type: ignore[import-not-found]

    sys.path.insert(0, str(sys_path_entry))
    try:
        graph = grimp.build_graph(package_name)
        pairs: list[tuple[str, str]] = []
        for module in graph.modules:
            src_rel = _module_to_relpath(module, sys_path_entry, project_root)
            if src_rel is None:
                continue
            for imported in graph.find_modules_directly_imported_by(module):
                dst_rel = _module_to_relpath(imported, sys_path_entry, project_root)
                if dst_rel is None:
                    continue
                pairs.append((src_rel, dst_rel))
        return pairs
    finally:
        try:
            sys.path.remove(str(sys_path_entry))
        except ValueError:
            pass


def _module_to_relpath(
    module: str, sys_path_entry: Path, project_root: Path
) -> str | None:
    """Convert a dotted module name to a project-relative .py path."""
    parts = module.split(".")
    candidate = sys_path_entry / Path(*parts).with_suffix(".py")
    if candidate.is_file():
        try:
            return str(candidate.relative_to(project_root))
        except ValueError:
            return None
    init_candidate = sys_path_entry / Path(*parts) / _INIT_PY
    if init_candidate.is_file():
        try:
            return str(init_candidate.relative_to(project_root))
        except ValueError:
            return None
    return None


def _resolve_module_to_symbol(conn, rel_path: str) -> int | None:
    """Pick a representative symbol id for a file. Used as endpoint for import edges.

    If the file has no kept symbols (e.g. only variables, which _KEPT_KINDS drops),
    a placeholder 'module' symbol is inserted so the import edge can still be recorded.
    """
    row = conn.execute(
        "SELECT s.id FROM symbols s JOIN files f ON f.id = s.file_id "
        "WHERE f.path = ? ORDER BY s.id LIMIT 1",
        (rel_path,),
    ).fetchone()
    if row:
        return row[0]
    file_row = conn.execute(
        "SELECT id FROM files WHERE path = ?", (rel_path,)
    ).fetchone()
    if file_row is None:
        return None
    module_name = Path(rel_path).stem
    cur = conn.execute(
        "INSERT INTO symbols (file_id, kind, name, scope, line) VALUES (?, ?, ?, ?, ?)",
        (file_row[0], "module", module_name, None, 1),
    )
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# vulture integration (Python only)
# ---------------------------------------------------------------------------

# Vulture text output:  `<path>:<line>: unused <kind> '<name>' (NN% confidence)`
_VULTURE_LINE_RE = re.compile(
    r"^(?P<path>[^:]+):(?P<line>\d+):\s+unused\s+(?P<kind>\w+)\s+"
    r"'(?P<name>[^']+)'\s+\((?P<conf>\d+)%\s+confidence\)\s*$"
)

_VULTURE_MIN_CONFIDENCE = 80


def _run_vulture(abs_paths: list[Path]) -> str:
    """Run vulture on python files. Returns text output, or '' if unavailable."""
    if not abs_paths:
        return ""
    try:
        proc = subprocess.run(
            [
                _venv_bin("vulture"),
                *(str(p) for p in abs_paths),
                "--min-confidence",
                str(_VULTURE_MIN_CONFIDENCE),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        logger.warning("vulture timed out")
        return ""
    except FileNotFoundError:
        sys.stderr.write(
            "explore-codebase: vulture not installed; skipping dead code\n"
        )
        return ""
    # vulture exits non-zero when it finds dead code; that's a feature, not a fail.
    return proc.stdout


def _parse_vulture(
    output: str, project_root: Path
) -> list[tuple[str, int, str, str, int]]:
    """Yield (rel_path, line, kind, name, confidence) tuples ≥ 80% confidence."""
    rows: list[tuple[str, int, str, str, int]] = []
    for raw in output.splitlines():
        match = _VULTURE_LINE_RE.match(raw.strip())
        if not match:
            continue
        confidence = int(match.group("conf"))
        if confidence < _VULTURE_MIN_CONFIDENCE:
            continue
        path = match.group("path")
        # Convert absolute paths back to project-relative if possible.
        rel = _to_relative(path, project_root)
        rows.append(
            (
                rel,
                int(match.group("line")),
                match.group("kind"),
                match.group("name"),
                confidence,
            )
        )
    return rows


def _to_relative(path: str, project_root: Path) -> str:
    """Best-effort: relativise an absolute vulture path to the project root."""
    p = Path(path)
    if p.is_absolute():
        try:
            return str(p.relative_to(project_root))
        except ValueError:
            return path
    return path


def _insert_dead_code(conn, project_root: Path, py_rel_paths: list[str]) -> None:
    if not py_rel_paths:
        return
    abs_paths = [project_root / p for p in py_rel_paths]
    output = _run_vulture(abs_paths)
    rows = _parse_vulture(output, project_root)
    for row in rows:
        conn.execute(
            "INSERT INTO dead_code (file, line, kind, name, confidence) "
            "VALUES (?, ?, ?, ?, ?)",
            row,
        )
    conn.commit()


def _insert_import_edges(conn, project_root: Path, py_rel_paths: list[str]) -> None:
    """Run grimp and insert import edges resolved to symbol ids."""
    if not py_rel_paths:
        return
    detected = _detect_top_package(project_root)
    if detected is None:
        sys.stderr.write(
            "explore-codebase: grimp top-level package not determinable; "
            "skipping import graph\n"
        )
        return
    package_name, sys_path_entry = detected
    try:
        pairs = _collect_grimp_imports(
            package_name,
            sys_path_entry,
            project_root,
        )
    except ImportError:
        sys.stderr.write(
            "explore-codebase: grimp not installed; skipping import graph\n"
        )
        return
    except Exception as exc:  # pragma: no cover - defensive
        sys.stderr.write(f"explore-codebase: grimp failed: {exc}\n")
        return

    seen: set[tuple[int, int]] = set()
    for src_path, dst_path in pairs:
        src_id = _resolve_module_to_symbol(conn, src_path)
        dst_id = _resolve_module_to_symbol(conn, dst_path)
        if src_id is None or dst_id is None:
            continue
        if (src_id, dst_id) in seen:
            continue
        seen.add((src_id, dst_id))
        conn.execute(
            "INSERT INTO edges (src_id, dst_id, kind) VALUES (?, ?, ?)",
            (src_id, dst_id, "imports"),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


def _file_sha(abs_path: Path) -> str:
    """Compute the file SHA. Mirrors changeset.file_sha to avoid a circular import."""
    import hashlib

    h = hashlib.sha256()
    with open(abs_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _process_file_phase1(conn, project_root: Path, rel_path: str) -> bool:
    """Phase 1: insert files+symbols+dead_code rows.

    Returns True if the file was processed, False if it vanished.
    """
    abs_path = project_root / rel_path
    try:
        sha = _file_sha(abs_path)
    except FileNotFoundError:
        sys.stderr.write(
            f"explore-codebase: file vanished before analyze: {rel_path}\n"
        )
        return False

    file_id = _insert_file(conn, rel_path, sha)
    tags = _run_ctags(abs_path)
    _insert_symbols(conn, file_id, tags)
    return True


def run(conn, project_root, *, changed, new, deleted) -> int:
    """Run static analysis tools and write results to the DB.

    Two-phase orchestration: phase 1 inserts files, symbols, and dead_code
    rows for every path in changed|new. Phase 2 then runs the edge-emitting
    tools (pyan3, grimp) against the now-complete symbol table, so edges
    resolve regardless of file processing order. Unresolvable edges (e.g.
    pyan3 false positives through __init__.py) are silently dropped.
    """
    root = Path(project_root)

    _delete_file_rows(conn, deleted)
    _delete_file_rows(conn, changed)  # changed: drop existing rows before re-insert

    # Phase 1: per-file ingestion.
    processed: list[str] = []
    for rel_path in list(changed) + list(new):
        if _process_file_phase1(conn, root, rel_path):
            processed.append(rel_path)

    # Phase 2: edge resolution + dead-code analysis against the complete table.
    py_paths = _python_paths(processed)
    _insert_call_edges(conn, root, py_paths)
    _insert_import_edges(conn, root, py_paths)
    _insert_dead_code(conn, root, py_paths)

    return 0
