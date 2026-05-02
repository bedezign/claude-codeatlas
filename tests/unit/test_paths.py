"""Tests for explore_codebase._paths — top_module boundary cases."""

from __future__ import annotations

from codeatlas.explore_codebase._paths import top_module


def test_top_module_multi_segment_returns_first_part() -> None:
    assert top_module('pkg/module.py') == 'pkg'


def test_top_module_single_segment_returns_none() -> None:
    assert top_module('foo.py') is None


def test_top_module_root_slash_returns_none() -> None:
    assert top_module('/') is None


def test_top_module_nested_returns_first_part() -> None:
    assert top_module('a/b/c.py') == 'a'


def test_top_module_empty_string_returns_none() -> None:
    assert top_module('') is None
