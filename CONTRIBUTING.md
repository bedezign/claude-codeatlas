# Contributing

Thanks for your interest in contributing! Here's how to get started.

## Development Setup

**System prerequisites:**
- Python 3.12 or later
- uv package manager
- universal-ctags

**Install the development environment:**

```
git clone <your-fork>
cd repository
uv pip install --no-config -e ".[dev]"
```

## Running Tests

Run the full test suite:

```
uv run pytest
```

With coverage report:

```
uv run pytest --cov
```

## Code Style

Format and lint with ruff:

```
uv run ruff format src tests
uv run ruff check --fix src tests
```

Type check with pyright:

```
uv tool run pyright src
```

## Project Layout

| Path | Purpose |
|------|---------|
| src/explore_codebase/ | Package source |
| tests/ | Test suite |
| commands/ | Claude Code slash command spec |
| hooks/ | Claude Code plugin hooks |

## Submitting Changes

1. Fork the repository
2. Create a feature branch
3. Make your changes and add tests for new behaviour
4. Run tests and linters
5. Open a pull request

## License

Contributions are made under the Apache-2.0 license.
