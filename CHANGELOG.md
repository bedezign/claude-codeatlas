# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
