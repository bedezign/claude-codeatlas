"""Phase 0: verify CLI entry point and subcommand registration."""

import subprocess
import sys


def test_package_imports():
    import codeatlas.explore_codebase  # noqa: F401


def test_main_is_callable():
    from codeatlas.explore_codebase.cli import main

    assert callable(main)


def test_build_parser_exposes_all_subcommands():
    from codeatlas.explore_codebase.cli import build_parser

    parser = build_parser()
    subparsers_action = next(
        a for a in parser._actions if isinstance(a.choices, dict)
    )
    assert set(subparsers_action.choices.keys()) == {
        "init",
        "analyze",
        "narrative",
        "render",
        "cleanup",
    }


def test_help_exits_zero():
    result = subprocess.run(
        [sys.executable, "-m", "codeatlas.explore_codebase.cli", "--help"],
        capture_output=True,
    )
    assert result.returncode == 0


def test_each_subcommand_has_help():
    for cmd in ("init", "analyze", "narrative", "render", "cleanup"):
        result = subprocess.run(
            [sys.executable, "-m", "codeatlas.explore_codebase.cli", cmd, "--help"],
            capture_output=True,
        )
        assert result.returncode == 0, f"{cmd} --help failed"
