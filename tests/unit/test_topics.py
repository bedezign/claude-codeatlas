"""Tests for explore_codebase.topics — topic-scope resolver."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeatlas.explore_codebase import topics


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(root: Path, rel: str, content: str = "x = 1\n") -> Path:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return target


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project root with some Python files for scope resolution."""
    _write(tmp_path, "pkg_a/alpha.py")
    _write(tmp_path, "pkg_a/beta.py")
    _write(tmp_path, "pkg_b/gamma.py")
    _write(tmp_path, "top_level.py")
    return tmp_path


# ---------------------------------------------------------------------------
# Whole-codebase topics
# ---------------------------------------------------------------------------


def test_architecture_returns_all_source_files(project: Path):
    files = topics.files_for_topic("architecture", project)
    assert sorted(files) == [
        "pkg_a/alpha.py",
        "pkg_a/beta.py",
        "pkg_b/gamma.py",
        "top_level.py",
    ]


def test_modules_returns_all_source_files(project: Path):
    files = topics.files_for_topic("modules", project)
    assert sorted(files) == [
        "pkg_a/alpha.py",
        "pkg_a/beta.py",
        "pkg_b/gamma.py",
        "top_level.py",
    ]


def test_domain_rules_returns_all_source_files(project: Path):
    files = topics.files_for_topic("domain-rules", project)
    assert sorted(files) == [
        "pkg_a/alpha.py",
        "pkg_a/beta.py",
        "pkg_b/gamma.py",
        "top_level.py",
    ]


def test_data_returns_all_source_files(project: Path):
    """data mirrors architecture for now (TODO: narrow to models)."""
    files = topics.files_for_topic("data", project)
    arch_files = topics.files_for_topic("architecture", project)
    assert sorted(files) == sorted(arch_files)


def test_api_returns_all_source_files(project: Path):
    """api mirrors architecture for now (TODO: narrow to endpoints)."""
    files = topics.files_for_topic("api", project)
    arch_files = topics.files_for_topic("architecture", project)
    assert sorted(files) == sorted(arch_files)


# ---------------------------------------------------------------------------
# context/<module> topic
# ---------------------------------------------------------------------------


def test_context_module_returns_only_matching_module_files(project: Path):
    files = topics.files_for_topic("context/pkg_a", project)
    assert sorted(files) == ["pkg_a/alpha.py", "pkg_a/beta.py"]


def test_context_module_other_module(project: Path):
    files = topics.files_for_topic("context/pkg_b", project)
    assert files == ["pkg_b/gamma.py"]


def test_context_nonexistent_module_returns_empty(project: Path):
    files = topics.files_for_topic("context/no_such_module", project)
    assert files == []


def test_context_top_level_file_not_in_any_module(project: Path):
    """top_level.py has no directory — not included in any context/<module>."""
    files = topics.files_for_topic("context/top_level", project)
    assert files == []


# ---------------------------------------------------------------------------
# project-identity topic
# ---------------------------------------------------------------------------


def test_project_identity_returns_pyproject_toml(project: Path):
    """project-identity always returns ['pyproject.toml'] regardless of disk contents."""
    files = topics.files_for_topic("project-identity", project)
    assert files == ["pyproject.toml"]


def test_project_identity_returned_even_if_file_missing(tmp_path: Path):
    """project-identity scope is fixed regardless of whether pyproject.toml exists."""
    files = topics.files_for_topic("project-identity", tmp_path)
    assert files == ["pyproject.toml"]


# ---------------------------------------------------------------------------
# Unknown / fallback topics
# ---------------------------------------------------------------------------


def test_unknown_topic_falls_back_to_all_source_files(project: Path):
    files = topics.files_for_topic("foo", project)
    arch_files = topics.files_for_topic("architecture", project)
    assert sorted(files) == sorted(arch_files)


def test_empty_string_topic_falls_back_to_all_source_files(project: Path):
    files = topics.files_for_topic("", project)
    arch_files = topics.files_for_topic("architecture", project)
    assert sorted(files) == sorted(arch_files)


def test_context_empty_module_falls_back_to_all_source_files(project: Path):
    """'context/' with empty module name is malformed — silent fallback."""
    files = topics.files_for_topic("context/", project)
    arch_files = topics.files_for_topic("architecture", project)
    assert sorted(files) == sorted(arch_files)


# ---------------------------------------------------------------------------
# Return type: sorted list
# ---------------------------------------------------------------------------


def test_result_is_sorted(project: Path):
    files = topics.files_for_topic("architecture", project)
    assert files == sorted(files)


def test_result_is_list(project: Path):
    files = topics.files_for_topic("architecture", project)
    assert isinstance(files, list)


# ---------------------------------------------------------------------------
# Edge cases / doom-path coverage
# ---------------------------------------------------------------------------


def test_empty_project_returns_empty_list(tmp_path: Path):
    """No source files on disk → empty list (architecture/all-files topics)."""
    files = topics.files_for_topic("architecture", tmp_path)
    assert files == []


def test_context_module_empty_project_returns_empty(tmp_path: Path):
    files = topics.files_for_topic("context/pkg_a", tmp_path)
    assert files == []


def test_unicode_module_name_in_context(tmp_path: Path):
    """Unicode module name — path is valid, no crashes."""
    _write(tmp_path, "café/main.py")
    files = topics.files_for_topic("context/café", tmp_path)
    assert files == ["café/main.py"]
