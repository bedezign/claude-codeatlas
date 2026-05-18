# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-05-18

### Added
- `refresh` subcommand — runs `init` + `analyze` in one step (no shell piping)
- Query subcommands: `find`, `callers`, `callees`, `impact`, `summary` — agent-friendly DB interrogation with `--json` output for machine consumption
- `queries.py` helper module exposing `find_symbols`, `callers_of`, `callees_of`, `impact_of`, `summary` (used by the CLI subcommands; importable for custom tooling)
- `analyze` emits a one-line summary at end of run: files / symbols / edges / dead-symbols / elapsed time
- `symbols.line_end` and `symbols.loc` columns, sourced from `ctags --fields=+ne`
- Schema indexes on `symbols(name)`, `symbols(file_id)`, `edges(src_id)`, `edges(dst_id)`, `edges(kind)`
- `narratives` composite primary key `(topic, scope_id)` — enables per-file context narratives
- `narrative --scope <id>` flag to write per-file or per-rollup narratives
- `narrative --depends-on path1,path2` escape hatch for explicit dependency declaration
- `PRAGMA user_version` aligned with the in-database `schema_version` row
- Skill text: "Querying the DB" section with CLI subcommand reference and SQL recipes

### Changed
- Per-file `context/` granularity: files with ≥15 symbols get their own `context/<rel-path>.md`; smaller files fold into `context/<parent>/_module.md` rollups
- `narratives.depends_on` derives from symbols actually referenced in the narrative body (regex word match), with full-scope fallback when no symbol mentions are found — replaces the previous all-files-in-scope behaviour that marked every narrative stale on almost any commit
- `dead_code` table renamed to `dead_symbols` for plural-naming consistency
- `index.md` navigation block now points at the new CLI subcommands
- Workflow in `commands/explore-codebase.md` now recommends `refresh` over the `init | analyze` pipe; `init`/`analyze` remain for advanced use
- `architecture.md` index description now accurately reflects its content (top-level module roll-up)

### Removed
- Flat-data map files (`symbols.md`, `callgraph.md`, `imports.md`, `dead-code.md`) — superseded by the new CLI query subcommands and direct DB access. The remaining map files are prose-bearing (`architecture.md`, `modules.md`, `data.md`, `api.md`) plus the three change-aware maps (`impact.md`, `recent-changes.md`, `index.md`).

### Breaking
- Schema changed without a migration path. Existing `.claude/codeatlas/codebase.db` files must be re-initialised with `explore-codebase init --full` (or simply deleted and rebuilt). Pre-1.0 release — no backward-compatibility guarantee.
- `dead_code` → `dead_symbols` table rename affects any direct SQL consumer.
- `narratives` primary key change: rows must be re-inserted with a `scope_id` value (`""` for single-scope topics like `architecture`).
- Four map files removed from `render` output; consumers should switch to the CLI subcommands or query the DB directly.

## [0.2.0] - 2026-05-02

### Changed
- Source layout moved to `src/codeatlas/explore_codebase/` to accommodate future sibling skills
- Plugin bootstrap switched from regular install

## [0.1.2] - 2026-05-02

### Added
- `_venv_bin` helper in `analyze.py`: resolves pyan3/vulture from the plugin venv without requiring the venv `bin/` on `PATH`
- Bootstrap guard: `hooks/bootstrap.sh` now fails fast with a clear message if `uv` is not installed
- Path traversal check in `cmd_analyze`: changeset paths that escape the project root are rejected with exit code 1

### Fixed
- `ctags` not installed on the host no longer raises an unhandled `FileNotFoundError`; degrades gracefully with a warning
- Schema-version mismatch in `db.init` now emits a warning before dropping all tables and narratives
- Plugin and marketplace manifests aligned to single email address
- `pyproject.toml` now includes `readme`, `project.urls`, and author email for correct PyPI metadata

## [0.1.1] - 2026-05-02

### Added

- Initial public release as Claude Code plugin `codeatlas@bedezign`
- SQLite-backed codebase knowledge graph with incremental analysis
- Static analysis pipeline: ctags (symbols), pyan3 (call graph), grimp (import graph), vulture (dead code)
- 11 rendered markdown map files documenting repository structure and dependencies
- AI narrative loop for prose explanations per topic
- Activate mode to write `codeatlas-index.md` session rules from codebase analysis
- Bootstrap hook using uv for zero-config plugin install
