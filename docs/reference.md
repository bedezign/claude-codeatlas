# CLI Reference

## Synopsis

```
explore-codebase <subcommand> [options]
```

Available subcommands: `init`, `analyze`, `narrative`, `render`, `cleanup`, `activate`

Global option: `--project-root <path>` — project root directory (default: current working directory). Accepted by all subcommands.

## Subcommands

### init

Initializes the SQLite database at `.claude/codeatlas/codebase.db`, computes SHA hashes of all source files in the project, reconciles them against `git diff` to detect changes, and emits a JSON changeset on stdout. The changeset has three keys: `changed`, `new`, `deleted` — each a list of relative paths from the project root.

Designed to pipe into `analyze` to trigger static analysis on changed files only.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--full` | boolean | `false` | Force full rebuild, ignore cached SHAs. Use only as an escape hatch if changeset detection becomes unreliable. |
| `--project-root` | path | `.` (cwd) | Project root directory. |

**Stdout:** JSON changeset object with keys `changed`, `new`, `deleted`, each containing a list of relative paths.

**Behavior:**
- First run: creates all DB tables (schema versioned).
- Subsequent runs: reconciles stored SHAs against live files and `git diff`.
- Always exits with code 0 on success, writes error to stderr on failure.
- Output is valid JSON even on partial failure — client must validate fields.

**Example:**

```bash
explore-codebase init | explore-codebase analyze
```

### analyze

Runs static analysis tools (`ctags`, `pyan3`, `grimp`, `vulture`) on changed, new, and deleted files. Populates the `files`, `symbols`, `edges`, and `dead_code` tables. Removes DB rows for deleted files. When `--changed`, `--new`, and `--deleted` flags are omitted, reads a JSON changeset from stdin (pipe from `init`).

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--changed` | path list | `[]` (read from stdin) | Relative paths in the changed set. |
| `--new` | path list | `[]` (read from stdin) | Relative paths in the new set. |
| `--deleted` | path list | `[]` (read from stdin) | Relative paths in the deleted set. |
| `--project-root` | path | `.` (cwd) | Project root directory. |

**Stdin:** JSON changeset object (when `--changed`, `--new`, `--deleted` are all omitted). Expected shape:

```json
{
  "changed": ["src/module/file.py"],
  "new": ["src/new_module/init.py"],
  "deleted": ["src/old_module/legacy.py"]
}
```

**Behavior:**
- Paths are validated against the project root boundary (no path traversal).
- All four analysers run; failures in one do not stop others.
- Returns exit code 0 on success, 1 on validation failure or stdin parse failure.
- Error messages written to stderr with context.

**Example:**

```bash
# Pipe from init
explore-codebase init | explore-codebase analyze

# Pass files directly
explore-codebase analyze \
  --changed src/module.py \
  --new src/new_module.py
```

### narrative

Stores a prose narrative for a topic in the `narratives` DB table. The narrative is keyed by topic and records a SHA-based dependency list of scope files. Overwrites any existing narrative for that topic. Designed to be called interactively after reviewing gaps in the rendered maps.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--topic` | string | *(required)* | Narrative topic key (see topic table below). |
| `--content-file` | path | *(required)* | Path to a file containing the narrative prose (plain text or markdown). |
| `--project-root` | path | `.` (cwd) | Project root directory. |

**Topic keys:**

| Topic | Scope |
|-------|-------|
| `architecture` | High-level system overview across all source files. |
| `modules` | Per-module roles, responsibilities, and boundaries. |
| `context/<module>` | One topic per top-level module — purpose, public API, internal dependencies. |
| `data` | Data models, schemas, persistence layer. |
| `api` | External API surface, endpoints, HTTP routes. |
| `project-identity` | Project purpose, primary users, entry points. |
| `domain-rules` | Domain invariants, business rules, validation logic. |

**Behavior:**
- Reads the narrative file as UTF-8 text.
- Computes a `depends_on` list by walking the scope files for the topic (via `topics.files_for_topic`) and recording their relative path + SHA.
- Records `generated_at` as the current UTC ISO timestamp.
- Writes success to stdout, exits with code 0.
- Returns exit code 1 if the file is not found, is a directory, or cannot be read.

**Example:**

```bash
# Write narrative for a specific module context
cat > /tmp/mymodule.md << 'EOF'
The mymodule package handles request routing and validation.

Public API:
- Router class for registration
- middleware decorator for request hooks
EOF

explore-codebase narrative \
  --topic context/mymodule \
  --content-file /tmp/mymodule.md
```

### render

Reads the DB and writes all map files under `.claude/codeatlas/maps/` and per-module context pages under `.claude/codeatlas/context/`. Every file starts with a banner warning that hand edits will be overwritten.

Produces the following map files: `architecture.md`, `modules.md`, `symbols.md`, `callgraph.md`, `imports.md`, `dead-code.md`, `data.md`, `api.md`, `impact.md`, `recent-changes.md`, `index.md`.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--base-sha` | git SHA | none | Files whose stored SHA differs from `<base-sha>` seed the BFS for `impact.md`. Omit to write `impact.md` as a placeholder. |
| `--since` | git ref or date | none | Passed to `git log --since=...` to populate `recent-changes.md`. Omit to write `recent-changes.md` as a placeholder. |
| `--project-root` | path | `.` (cwd) | Project root directory. |

**Behavior:**
- Creates `.claude/codeatlas/maps/` and `.claude/codeatlas/context/` if missing.
- Per-module pages are seeded from `symbols` and `edges` tables, then merged with `context/<module>` narratives if present.
- Returns exit code 0 on success, 1 on write failure or DB error.
- All generated files are overwritten; hand-written notes belong in `.claude/codeatlas/notes/`.

**Example:**

```bash
# Basic render (no impact/recent-changes)
explore-codebase render

# With impact map (changed files seeded from a base commit)
explore-codebase render --base-sha abc1234

# With recent changes (all commits in the last month)
explore-codebase render --since "1 month ago"

# Both
explore-codebase render --base-sha abc1234 --since "1 month ago"
```

### cleanup

Removes orphan map files — any files in `.claude/codeatlas/maps/` or `.claude/codeatlas/context/` that were not produced by the most recent `render` call. Also warns about legacy `.codeatlas/` directories without auto-deleting them.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--dry-run` | boolean | `false` | List orphans and legacy directories without deleting anything. |
| `--project-root` | path | `.` (cwd) | Project root directory. |

**Behavior:**
- Reads the manifest of files written by the last render from the DB.
- Compares against actual files on disk and prints orphans to stdout.
- With `--dry-run`, stops before deleting.
- Warns about `.codeatlas/` directories (old naming convention) but does not remove them.
- Returns exit code 0 on success, 1 on write/delete failure.

**Example:**

```bash
# Preview what would be removed
explore-codebase cleanup --dry-run

# Remove orphans
explore-codebase cleanup
```

### activate

Writes a `codeatlas-index.md` rule file to either `~/.claude/rules/` (global) or `.claude/rules/` (project-local). The rule instructs future Claude Code sessions to invoke the explore-codebase skill when asked about codebase structure, rather than reading source files directly.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--global` | boolean | `false` | Write to `~/.claude/rules/codeatlas-index.md` without prompting. |
| `--project` | boolean | `false` | Write to `.claude/rules/codeatlas-index.md` without prompting. |

**Behavior:**
- If neither `--global` nor `--project` is set, prompts for the target location.
- If the target file exists, shows current content and asks whether to replace, update, or leave as-is.
- On success, writes the rule file and exits with code 0.
- Returns exit code 1 on write failure or permission error.

**Rule template:**

```markdown
# Codebase Index

This project has a codebase index at `.claude/codeatlas/codebase.db`.

For any question about codebase structure, module relationships, symbol lookup,
blast radius, or recent changes — invoke the `codeatlas:explore-codebase` skill
rather than grepping or reading source files directly.

If `.claude/codeatlas/maps/index.md` exists, see it for the map inventory.
```

**Example:**

```bash
# Prompt for location
explore-codebase activate

# Write globally without prompting
explore-codebase activate --global

# Write to project-local rules only
explore-codebase activate --project
```

## Output Locations

| Path | Contents |
|------|----------|
| `.claude/codeatlas/codebase.db` | SQLite database (WAL mode). Schema versioned; tables for files, symbols, edges, dead_code, narratives, render manifest. |
| `.claude/codeatlas/maps/` | All rendered map files: `architecture.md`, `modules.md`, `symbols.md`, `callgraph.md`, `imports.md`, `dead-code.md`, `data.md`, `api.md`, `impact.md`, `recent-changes.md`, `index.md`. |
| `.claude/codeatlas/context/<module>.md` | Per-module context page generated from symbols, edges, and `context/<module>` narrative. One file per top-level module. |
| `.claude/codeatlas/notes/` | Hand-written annotations (never touched by CLI). Use for persistent observations or corrections. |

All generated files start with a banner warning that hand edits will be overwritten on next `render`.

## Exit Codes

- `0` — success
- `1` — error (detailed message written to stderr)

## Common Workflows

### Full analysis workflow

Run from project root when the user asks about codebase structure or when the database is missing or stale.

```bash
explore-codebase init | explore-codebase analyze
# Write narratives for topics that apply...
explore-codebase narrative --topic architecture --content-file /tmp/arch.md
explore-codebase render --base-sha HEAD~10
explore-codebase cleanup
explore-codebase activate
```

### Incremental updates

After source files have changed:

```bash
explore-codebase init | explore-codebase analyze
explore-codebase render --base-sha HEAD~10
explore-codebase cleanup
```

No narratives need to be rewritten unless their scope has changed (new module, renamed class, etc.).

### Dry-run cleanup

Preview orphans before committing to deletion:

```bash
explore-codebase cleanup --dry-run
```
