# Usage Guide

This guide walks you through real scenarios using explore-codebase — from first-time setup through ongoing maintenance and advanced options.

## First-time setup

When you're starting with a new project, you'll run the full analysis workflow. It detects the project structure, extracts symbols and dependencies, and creates maps for future reference.

### Full workflow (recommended)

Open a Claude Code session in your project directory and invoke the command with no arguments:

```
/codeatlas:explore-codebase
```

Claude orchestrates the entire workflow automatically:

1. **Init** — hashes source files and detects what's new or changed
2. **Analyze** — pipes the changeset into static analysers (ctags, pyan3, grimp, vulture) to extract symbols, edges, and dead symbols
3. **Render** — builds seven markdown maps from the database: `architecture.md`, `modules.md`, `data.md`, `api.md`, `impact.md`, `recent-changes.md`, `index.md`. Maps only appear after this step — `init` and `analyze` alone produce no map files.
4. **Narrative loop** — Claude reviews the rendered maps, writes prose explanations for key topics like architecture, data models, and APIs, then re-renders to incorporate them
5. **Cleanup** — removes orphan map files from previous runs
6. **Activation prompt** — asks whether to write a rule so future sessions use the explore-codebase skill to query the database

The entire workflow is interactive — Claude shows you what it finds, asks clarifying questions, and writes prose narratives as you direct.

### Manual step-by-step (for scripting)

If you want to run the steps separately, use this sequence:

```bash
explore-codebase init | explore-codebase analyze
explore-codebase render
explore-codebase cleanup
explore-codebase activate
```

The `init` and `analyze` steps are piped together — `analyze` reads the JSON changeset from `init` via stdin when no explicit flags are given.

All output is stored under `.claude/codeatlas/`:

- **codebase.db** — SQLite database (the source of truth for all maps)
- **maps/** — seven markdown files (`architecture.md`, `modules.md`, `data.md`, `api.md`, `impact.md`, `recent-changes.md`, `index.md`)
- **context/** — per-file pages (≥ 15 symbols) and `_module.md` rollups for smaller files
- **notes/** — hand-written annotations (never touched by the CLI)

## Refreshing after code changes

The analysis is incremental. When you edit source files, `init` detects what changed automatically by comparing file hashes against stored values. Unchanged files skip re-analysis.

After making significant code changes, refresh the knowledge graph:

```
/codeatlas:explore-codebase
```

Same full workflow as the first run. Claude detects only the changed files, re-analyzes them, and updates the narratives as needed. Unchanged topics keep their existing prose.

Alternatively, run the steps manually:

```bash
explore-codebase init | explore-codebase analyze
explore-codebase render
explore-codebase cleanup
```

## Generating the blast-radius map

When you want to understand what a set of changes affects — the call-path impact through the codebase — use the `--base-sha` flag:

```bash
explore-codebase render --base-sha <git-sha>
```

The tool finds all files whose stored hash differs from `<git-sha>`, then performs a breadth-first search through the call graph starting from those files. The result is written to `maps/impact.md`.

### Practical examples

Compare against the previous commit:

```bash
explore-codebase render --base-sha $(git rev-parse HEAD~1)
```

Compare against a release tag:

```bash
explore-codebase render --base-sha v1.2.0
```

Compare against main branch (useful when working on a feature branch):

```bash
explore-codebase render --base-sha origin/main
```

## Generating the recent-changes map

To see which files changed in a recent time window and what they affect:

```bash
explore-codebase render --since "2 weeks ago"
explore-codebase render --since v1.2.0
explore-codebase render --since "2025-04-01"
```

The `--since` flag accepts anything that `git log --since=...` accepts — relative dates (`"2 days ago"`, `"1 month ago"`), absolute dates (`"2025-04-01"`), or tag names. The result is written to `maps/recent-changes.md`.

## Writing a narrative manually

If you want to add or refine a narrative without running the full analysis — for example, to explain a complex domain pattern that the maps alone don't capture — use the `narrative` subcommand.

Write your prose to a temporary file:

```bash
cat > /tmp/architecture.md << 'EOF'
The system has three layers: request handling, business logic, and data access.
The request layer parses HTTP input and routes to handlers. The business layer
enforces domain rules and coordinates between modules. The data layer abstracts
the persistence mechanism.
EOF
```

Store the narrative:

```bash
explore-codebase narrative --topic architecture --content-file /tmp/architecture.md
```

Valid topics are:

- `architecture` — high-level system design across all modules
- `modules` — general notes on module responsibilities
- `context/<module>` — detailed narrative for a specific module (e.g., `context/database`)
- `data` — data models, schemas, and persistence layer
- `api` — external API surface and endpoint design
- `project-identity` — project purpose, users, and entry points
- `domain-rules` — business rules and domain invariants

After storing narratives, re-render to pick them up:

```bash
explore-codebase render
```

## Activating codebase awareness

Once the database is populated, activate it so future Claude Code sessions automatically query the knowledge graph when you ask questions about codebase structure.

```
/codeatlas:explore-codebase activate
```

Claude asks: should the rule be global (visible to all your projects) or project-only (this project only)? Choose based on whether the codebase insight is portable:

- **Global** — if you expect to reference this project's structure from other projects (rare)
- **Project-only** — standard choice; the rule lives at `.claude/rules/codeatlas-index.md`

To skip the prompt and activate globally:

```
/codeatlas:explore-codebase activate --global
```

To activate project-only:

```
/codeatlas:explore-codebase activate --project
```

Once activated, any time you ask Claude about the codebase in a future session, it will invoke the explore-codebase skill to query the database instead of reading source files directly.

## Dry-running cleanup

Before letting the tool delete orphan map files, preview what would be removed:

```bash
explore-codebase cleanup --dry-run
```

This lists any files in `maps/` or `context/` that no longer have corresponding database entries, without actually deleting anything. Review the list, then run `cleanup` without the flag to perform the actual deletion.

## Using the standalone CLI

The same commands work outside Claude Code using the `explore-codebase` executable (installed via `pip install explore-codebase`). Substitute `explore-codebase <subcommand>` for `/codeatlas:explore-codebase <subcommand>`:

```bash
explore-codebase init | explore-codebase analyze
explore-codebase render
explore-codebase cleanup
explore-codebase activate --project
```

All flags and options are identical. The primary difference is that outside Claude Code, the narrative loop (writing prose to topics) is less natural — the tool provides structure and storage, but there's no AI to generate the prose. The structural analysis and rendering work identically in both contexts.

## Customizing analysis

### Force a full rebuild

Normally, `init` compares file hashes against stored values to detect changes. To force re-analysis of the entire codebase (an escape hatch when you suspect the database is stale):

```bash
explore-codebase init --full
explore-codebase analyze
explore-codebase render
explore-codebase cleanup
```

The `--full` flag ignores cached hashes and re-hashes every source file.

### Custom project root

If you're invoking `explore-codebase` from outside the project directory, or your project has a non-standard layout, specify the root:

```bash
explore-codebase init --project-root /path/to/project
explore-codebase analyze --project-root /path/to/project
explore-codebase render --project-root /path/to/project
```

The default is the current working directory.
