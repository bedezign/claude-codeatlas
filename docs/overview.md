# Overview

When Claude Code starts exploring an unfamiliar codebase, it faces a knowledge problem. Without context, every question requires grepping source files, reading code cold, and building understanding from first principles. This is slow and token-expensive, and it repeats every session.

explore-codebase solves this by computing a knowledge graph from static analysis and storing it in a local SQLite database. Instead of reading source files cold, Claude queries this database directly via the explore-codebase skill. The `render` step produces markdown maps on demand for human review.

## The knowledge graph

The tool's foundation is a SQLite database stored at `.claude/codeatlas/codebase.db`. This database is project-local, never shared, and contains five tables of structural information:

**files** — every source file with its SHA-256 hash and detected language. The hash enables incremental analysis: on each run, the tool compares current source-tree hashes against stored values and re-analyzes only changed, new, or deleted files.

**symbols** — functions, classes, variables, and other named entities extracted via static analysis. Each symbol record includes the file it lives in, its kind (function, class, etc.), and its line number.

**edges** — directed relationships between symbols. Call-graph edges represent which functions invoke which; import-graph edges represent which modules import which. Edges are the foundation for understanding control flow and dependencies.

**dead_code** — symbols that are defined but never used, detected by static analysis. Useful for cleanup and understanding which APIs are actually exercised.

**narratives** — prose explanations written by Claude during analysis. These are stored alongside the structural data and rendered into the maps. Narratives persist across re-runs and are only invalidated when the files they cover change.

## Incremental analysis

The tool's efficiency comes from its SHA-based incremental model. On the first run, it hashes all source files and stores the hashes in the database. On every subsequent run, it re-hashes the source tree and compares against stored values. Only changed, new, or deleted files are re-analyzed. On large codebases, this keeps re-analysis fast even after small edits.

The `init` step detects changes and emits a JSON changeset (`new`, `changed`, `deleted`) to stdout. This changeset is piped directly into `analyze`, which uses it to decide which files to feed through the static-analysis pipeline.

## The analysis pipeline

Four optional tools contribute to the knowledge graph. All are optional in practice — if one is missing, the analysis continues with reduced coverage.

**universal-ctags** performs symbol extraction across many languages. It scans source files and emits the names, kinds, and locations of functions, classes, variables, and other entities.

**pyan3** builds a Python-specific call graph. It traces which functions invoke which and emits directed edges representing the call relationships.

**grimp** builds a Python-specific import graph. It traces which modules import which and emits directed edges representing import relationships.

**vulture** performs dead-code detection. It identifies symbols that are defined but never referenced, which is useful for understanding which APIs are actually used and which may be safe to remove.

The tool runs these tools independently; if ctags is missing, symbols won't be extracted but the call graph and import graph will still build.

## AI narratives

After the structural analysis populates the database, Claude writes prose explanations for topics like `architecture`, `modules`, `data`, `api`, and `context/<module>`. These narratives are human-readable explanations of what the structure means, stored in the `narratives` table and rendered into the maps.

The narrative loop is reactive. The tool renders the maps from structural data, Claude reviews them to identify knowledge gaps, writes narratives to fill those gaps, and then renders again. Narratives persist — they're only invalidated when the files they cover change. A narrative is worth writing when reading three small maps still leaves a Claude reader without context.

## The maps

The `render` step produces markdown files from the database on demand, useful for human review and quick reference. Each map answers a specific question about the codebase:

**index.md** — Inventory of all maps and entry point.

**architecture.md** — High-level system overview and design rationale.

**modules.md** — Per-module roles, responsibilities, and boundaries.

**symbols.md** — All extracted symbols (functions, classes, variables) with file and line numbers.

**callgraph.md** — Function-call edges from static call-graph analysis.

**imports.md** — Module import graph showing dependencies.

**dead-code.md** — Unused symbols detected by static analysis.

**data.md** — Data models and persistence layer narrative.

**api.md** — External API surface narrative.

**impact.md** — Blast radius from changed files (computed when a `--base-sha` is provided to render).

**recent-changes.md** — Files changed since a reference or date (computed when a `--since` flag is provided to render).

All maps are generated from the database and live under `.claude/codeatlas/maps/`. Per-module context pages are generated under `.claude/codeatlas/context/`. These are all overwritten on every render — hand edits belong in `.claude/codeatlas/notes/`, not in the maps themselves.

## Activation

After the knowledge graph is built, the tool can write a rule file that tells future Claude Code sessions to invoke the explore-codebase skill when you ask about codebase structure. The `activate` step writes `codeatlas-index.md`, which can be scoped globally or per-project:

**--project** — writes to `.claude/rules/codeatlas-index.md`. Only this project uses the skill.

**--global** — writes to `~/.claude/rules/codeatlas-index.md`. All projects use the skill.

Without activation, the knowledge graph exists but Claude won't automatically know to use it. Activation bridges the gap: future sessions see the rule and know to invoke the explore-codebase skill rather than reading source files directly.
