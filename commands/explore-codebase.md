---
description: SQLite-backed codebase exploration — build maps, write narratives, activate session awareness
argument-hint: "[activate [--global|--project]]"
allowed-tools: Bash(${CLAUDE_PLUGIN_DATA}/.venv/bin/explore-codebase:*), Bash(git:*), Bash(find:*), Read, Write, Edit
---

# explore-codebase

Always invoked as **`/codeatlas:explore-codebase`**.

The `explore-codebase` CLI does all mechanical work: schema management, file SHA hashing, static analysis, DB inserts, markdown rendering, orphan sweep. Your job is to orchestrate the CLI and write prose narratives in between.

## Invocation modes

| Argument | Behaviour |
|----------|-----------|
| *(none)* | Full analysis workflow — build or refresh all maps |
| `activate` | Write `codeatlas-index.md` only, without re-running the analysis |
| `activate --global` | Write to `~/.claude/rules/codeatlas-index.md` without asking where |
| `activate --project` | Write to `.claude/rules/codeatlas-index.md` without asking where |

## When to run the full workflow

Run the workflow when:

- The user asks about codebase structure, module relationships, dependencies, blast radius, or where a symbol lives, AND `.claude/codeatlas/codebase.db` is missing or stale.
- A new project: no `.claude/codeatlas/codebase.db` exists yet.
- Source files have changed since the last run (the `init` step will detect this).
- The user explicitly asks to refresh the maps.


## Workflow

Run from the project root in this order:

1. **`explore-codebase init | explore-codebase analyze`** — hashes source files, reconciles against `git diff`, pipes the JSON changeset into `analyze`. Static analysers (ctags, pyan3, grimp, vulture) populate the `files`, `symbols`, `edges`, and `dead_code` tables. `analyze` reads the JSON from stdin when no `--changed/--new/--deleted` flags are passed.

2. **`explore-codebase render`** — DB → markdown. **Maps are only produced by this step — they do not appear from `init` or `analyze` alone.** Writes every map under `.claude/codeatlas/maps/` and per-module pages under `.claude/codeatlas/context/`. Pass `--base-sha <sha>` to populate `impact.md` (BFS from changed files); pass `--since <ref-or-date>` to populate `recent-changes.md`. Without those flags both files become placeholders.

3. **AI narrative loop** — see next section. After an initial render shows the structural data, you write prose for the documented topics, then call `render` again to incorporate them.

4. **`explore-codebase cleanup`** — removes orphan map files (anything in `maps/` or `context/` that wasn't produced by the run). `--dry-run` lists what would go without deleting.

5. **Activation check** — after cleanup, check whether a `codeatlas-index.md` rule already exists at `.claude/rules/codeatlas-index.md` (project-level) or `~/.claude/rules/codeatlas-index.md` (global). If neither is present, offer to activate — see *Activate mode* below.

## AI narrative loop

`analyze` fills the structural tables. `render` (step 2) writes the initial maps from structural data only — no prose yet. Prose explanation per topic is your job. Review the rendered maps, identify gaps, write narratives, then call `render` again to incorporate them.

For each topic below, decide whether prose is warranted given the codebase, write the narrative to a temp file, then call `narrative` to store it:

```
explore-codebase narrative --topic <topic> --content-file /tmp/<topic>.md
```

Repeat the `render` step after the narratives are stored.

| Topic | Scope |
|-------|-------|
| `architecture` | High-level system overview across all source files |
| `modules` | Per-module roles and boundaries |
| `context/<module>` | One topic per top-level module — its purpose, public API, internal callees |
| `data` | Data models, schemas, persistence layer |
| `api` | External API surface, endpoints |
| `project-identity` | What this project is, who uses it, entry points |
| `domain-rules` | Domain invariants and business rules |

A narrative is worth writing when reading three small maps still leaves a Claude reader without context. Skip topics that don't apply (e.g. `api` for a library with no HTTP surface).

## Output locations

| Path | Contents |
|------|----------|
| `.claude/codeatlas/codebase.db` | SQLite store (WAL mode) |
| `.claude/codeatlas/maps/` | `architecture.md`, `modules.md`, `symbols.md`, `callgraph.md`, `imports.md`, `dead-code.md`, `data.md`, `api.md`, `impact.md`, `recent-changes.md`, `index.md` |
| `.claude/codeatlas/context/<module>.md` | Per-module page generated from `symbols`/`edges` plus `context/<module>` narrative |
| `.claude/codeatlas/notes/` | Hand-written notes — never touched by the CLI |

Every generated file starts with a banner warning hand edits will be overwritten. Hand-written annotations belong in `.claude/codeatlas/notes/`, not in the maps.

## Flag reference

| Flag | Subcommand | Purpose |
|------|------------|---------|
| `--project-root <path>` | all | Project root (default: cwd) |
| `--base-sha <sha>` | `render` | Files whose stored sha differs feed `impact.md`'s BFS |
| `--since <ref-or-date>` | `render` | Passed to `git log --since=...` for `recent-changes.md` |
| `--content-file <path>` | `narrative` | File holding the prose body |
| `--topic <key>` | `narrative` | Narrative key (see topic table) |
| `--dry-run` | `cleanup` | List orphans without deleting |
| `--full` | `init` | Force full rebuild (ignore cached SHAs) — escape hatch only |

## Activate mode

Writes a `codeatlas-index.md` rule so that future sessions invoke this skill to query the database when the user asks about codebase structure, rather than reading source files directly.

**Triggered automatically** at the end of the full workflow when no `codeatlas-index.md` is found at either `.claude/rules/codeatlas-index.md` or `~/.claude/rules/codeatlas-index.md`. Ask:

> "Analysis is ready. Add codebase awareness for future sessions? I can write a rule so future sessions query the database via this skill. (global — all your projects / project-only / skip)"

**Invoked directly as `activate`** (no location flag): ask where to write:

> "Write to `~/.claude/rules/` (global — all your projects) or `.claude/rules/` (this project only)?"

**Invoked as `activate --global`** or **`activate --project`**: skip the location question and write immediately.

**Before writing**, check whether the target file already exists. If it does, show the current content and ask:

> "`codeatlas-index.md` already exists at `<path>`. Replace it, update it, or leave it as-is?"

### `codeatlas-index.md` template

```markdown
# Codebase Index

This project has a codebase index at `.claude/codeatlas/codebase.db`.

For any question about codebase structure, module relationships, symbol lookup,
blast radius, or recent changes — invoke the `codeatlas:explore-codebase` skill
rather than grepping or reading source files directly.

If `.claude/codeatlas/maps/index.md` exists, see it for the map inventory.
```
