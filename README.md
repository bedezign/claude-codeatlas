codeatlas

*SQLite-backed codebase knowledge graph for Claude Code.*

## What it does

codeatlas runs incremental static analysis over your source tree — hashing files, extracting symbols and call edges, detecting dead code — and stores the results in a local SQLite database. Between runs, Claude writes prose narratives that explain what the structure means. Claude queries this database directly via the explore-codebase skill to answer questions about structure, dependencies, and blast radius — without reading source files cold. The `render` step produces markdown maps on demand for human review.

## System prerequisites

Before installing the plugin, ensure you have:

- **universal-ctags** — required for symbol extraction
  - Linux (apt): `apt install universal-ctags`
  - macOS (brew): `brew install universal-ctags`
  - Other systems: see [ctags.io](https://ctags.io/)
- **Python 3.12 or later**
- **uv** — required for plugin bootstrap. Install from [docs.astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/)

Note: pyan3, grimp, and vulture are installed automatically by the plugin bootstrap; you do not need to install them separately.

## Install — Claude Code plugin (recommended)

From inside a Claude Code session:

```
/plugin marketplace add bedezign/codeatlas
/plugin install codeatlas@bedezign
```

The first new session auto-bootstraps the Python environment under the plugin's data directory.

## Install — standalone CLI

To use codeatlas outside Claude Code:

```
pip install codeatlas
```

This provides the `explore-codebase` command-line tool. Requires the system prerequisites above.

## Quick start

Invoke from inside a Claude Code session with no arguments:

```
/codeatlas:explore-codebase
```

Claude orchestrates the full pipeline automatically:

- **init** — Hash source files, detect changes since the last run, emit a JSON changeset
- **analyze** — Run static analysers (ctags, pyan3, grimp, vulture) and populate the database
- **narrative loop** — Claude writes prose explanations for architecture, modules, and other topics
- **render** — Write all maps under .claude/codeatlas/maps/ and per-module pages under .claude/codeatlas/context/
- **cleanup** — Remove orphan map files from previous runs

All output is stored under .claude/codeatlas/.

## Maps

After running render, you get eleven markdown maps:

| File | Contents |
|------|----------|
| maps/index.md | Map inventory and entry point; start here |
| maps/architecture.md | High-level system overview and design rationale |
| maps/modules.md | Per-module roles, responsibilities, and boundaries |
| maps/symbols.md | Extracted symbols (functions, classes) with file and line numbers |
| maps/callgraph.md | Function-call edges from static call-graph analysis |
| maps/imports.md | Module import graph showing dependencies |
| maps/dead-code.md | Unused symbols detected by vulture |
| maps/data.md | Data models and persistence layer narrative |
| maps/api.md | External API surface narrative |
| maps/impact.md | BFS blast-radius from changed files (requires --base-sha) |
| maps/recent-changes.md | Files changed since a reference or date (requires --since) |

All maps live under .claude/codeatlas/maps/. Per-module context pages are stored at .claude/codeatlas/context/<module>.md.

## Activate mode

After running the analysis, run:

```
/codeatlas:explore-codebase activate
```

This writes a codeatlas-index.md rule file so future Claude Code sessions invoke the explore-codebase skill when you ask about codebase structure, rather than reading source files directly. You can scope the rule globally or per-project:

- --global — all projects use the skill (global rule)
- --project — only this project uses the skill (local rule)
- No flag — you will be prompted to choose

## Troubleshooting

ctags not found

Static analysis runs but no symbols are extracted. Install universal-ctags via your system package manager (see System prerequisites above).

uv not found (plugin bootstrap fails)

Install uv from [docs.astral.sh/uv/getting-started/installation](https://docs.astral.sh/uv/getting-started/installation/).

Maps are empty or very sparse

Ensure the project contains Python source files. Non-Python files are hashed (to detect changes) but are not analysed for symbols or call edges. The plugin only extracts knowledge from Python code.

## Development

To install locally for development (editable install into the project venv):

```
uv pip install --no-config -e ".[dev]"
uv run pytest
```

For local development, a .claude-plugin/marketplace.json (not tracked in git) in this repo provides a local marketplace entry pointing at this directory. The public entry lives in the separate claude-marketplace repo, which points to the GitHub release.

## License

Apache-2.0