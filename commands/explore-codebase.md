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
- Source files have changed since the last run.
- The user explicitly asks to refresh the maps.


## Workflow

Run from the project root in this order:

1. **`explore-codebase refresh`** — the standard path. Runs `init` and `analyze` in sequence, then renders all maps. Use `init` and `analyze` separately only when you need fine-grained control (e.g. passing `--full` to force a full rebuild, or piping a custom changeset).

2. **`explore-codebase render`** — re-render maps from the current DB without re-parsing. **Maps are only produced by this step — they do not appear from `init` or `analyze` alone.** Writes every map under `.claude/codeatlas/maps/` and per-file context pages under `.claude/codeatlas/context/`. Pass `--base-sha <sha>` to populate `impact.md` (BFS from changed files); pass `--since <ref-or-date>` to populate `recent-changes.md`. Without those flags both files become placeholders.

3. **AI narrative loop** — see next section. After an initial render shows the structural data, you write prose for the documented topics, then call `render` again to incorporate them.

4. **`explore-codebase cleanup`** — removes orphan map files (anything in `maps/` or `context/` that wasn't produced by the run). `--dry-run` lists what would go without deleting.

5. **Activation check** — after cleanup, check whether a `codeatlas-index.md` rule already exists at `.claude/rules/codeatlas-index.md` (project-level) or `~/.claude/rules/codeatlas-index.md` (global). If neither is present, offer to activate — see *Activate mode* below.

## Querying the DB

Agents with DB access have two paths for ad-hoc queries beyond what the maps show.

### CLI subcommands

All subcommands accept `--json` for machine-readable output.

| Subcommand | Purpose |
|------------|---------|
| `find <name>` | Locate a symbol (file:line, kind) — supports `--substring` for partial match |
| `callers <symbol>` | Incoming `calls` edges — who calls this symbol |
| `callees <symbol>` | Outgoing `calls` edges — what this symbol calls |
| `impact <file> [--depth N]` | BFS blast radius from a file (default depth 2) |
| `summary` | DB stats + last parsed timestamp |

### SQL recipes

When the CLI doesn't expose what you need, query the DB directly:

```sql
-- Symbol lookup with line range
SELECT f.path, s.line, s.line_end, s.loc, s.kind, s.name
FROM symbols s JOIN files f ON s.file_id = f.id
WHERE s.name = ?;

-- Functions over 50 lines
SELECT f.path, s.name, s.loc FROM symbols s
JOIN files f ON s.file_id = f.id
WHERE s.kind = 'function' AND s.loc > 50
ORDER BY s.loc DESC;

-- Callers of X (raw)
SELECT f.path, src.name FROM edges e
JOIN symbols dst ON e.dst_id = dst.id
JOIN symbols src ON e.src_id = src.id
JOIN files f ON src.file_id = f.id
WHERE dst.name = ? AND e.kind = 'calls';
```

## AI narrative loop

`analyze` fills the structural tables. `render` (step 2) writes the initial maps from structural data only — no prose yet. Prose explanation per topic is your job. Review the rendered maps, identify gaps, write narratives, then call `render` again to incorporate them.

For each topic below, decide whether prose is warranted given the codebase, write the narrative to a temp file, then call `narrative` to store it:

```
explore-codebase narrative --topic <topic> --scope <scope> --content-file /tmp/<topic>.md
```

Per-file context narratives use `--topic context --scope <rel-path>`:

```
explore-codebase narrative --topic context --scope src/pkg/module.py --content-file /tmp/module.md
```

Repeat the `render` step after the narratives are stored.

| Topic | Scope |
|-------|-------|
| `architecture` | High-level system overview across all source files |
| `modules` | Per-module roles and boundaries |
| `context` | One narrative per source file (scope = rel-path, e.g. `src/pkg/module.py`) or per parent directory (scope = dir path) for rollup pages |
| `data` | Data models, schemas, persistence layer |
| `api` | External API surface, endpoints |
| `project-identity` | What this project is, who uses it, entry points |
| `domain-rules` | Domain invariants and business rules |

A narrative is worth writing when reading three small maps still leaves a Claude reader without context. Skip topics that don't apply (e.g. `api` for a library with no HTTP surface).

## Output locations

| Path | Contents |
|------|----------|
| `.claude/codeatlas/codebase.db` | SQLite store (WAL mode) |
| `.claude/codeatlas/maps/` | `architecture.md`, `modules.md`, `data.md`, `api.md`, `impact.md`, `recent-changes.md`, `index.md` |
| `.claude/codeatlas/context/<rel-path>.md` | Per-file page for files with >= 15 symbols (path mirrored, `.py` → `.md`) |
| `.claude/codeatlas/context/<dir>/_module.md` | Rollup page aggregating small files (< 15 symbols) under `<dir>` |
| `.claude/codeatlas/notes/` | Hand-written notes — never touched by the CLI |

Every generated file starts with a banner warning hand edits will be overwritten. Hand-written annotations belong in `.claude/codeatlas/notes/`, not in the maps.

The threshold of 15 symbols is controlled by `CONTEXT_SYMBOL_THRESHOLD` in `render.py`.

## Flag reference

| Flag | Subcommand | Purpose |
|------|------------|---------|
| `--project-root <path>` | all | Project root (default: cwd) |
| `--base-sha <sha>` | `render` | Files whose stored sha differs feed `impact.md`'s BFS |
| `--since <ref-or-date>` | `render` | Passed to `git log --since=...` for `recent-changes.md` |
| `--content-file <path>` | `narrative` | File holding the prose body |
| `--topic <key>` | `narrative` | Narrative key (see topic table) |
| `--scope <value>` | `narrative` | Narrative scope — rel-path for `context` topic, dir path for rollup |
| `--depends-on <dep>` | `narrative` | Record a dependency for this narrative |
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
