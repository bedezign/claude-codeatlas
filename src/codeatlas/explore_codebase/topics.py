"""Phase 4: topic-scope resolver for explore-codebase narratives.

Pure function — no DB access, no SHA computation.
Given a topic string and the project root, returns the list of relative file
paths that fall in scope for that topic.
"""

from __future__ import annotations

from pathlib import Path

from codeatlas.explore_codebase import changeset
from codeatlas.explore_codebase._paths import top_module as _top_module


# Topics that cover all source files.
_ALL_FILES_TOPICS = frozenset(
    {"architecture", "modules", "domain-rules", "data", "api"}
)


def files_for_topic(topic: str, root: Path) -> list[str]:
    """Return sorted relative paths in scope for *topic* under *root*.

    Mapping rules:
    - ``architecture``, ``modules``, ``domain-rules`` → all source files.
    - ``context/<module>`` → source files where the top directory equals
      *<module>*. A malformed ``context/`` (empty module name) falls back to
      all source files.
    - ``project-identity`` → ``["pyproject.toml"]`` only.
    - ``data``, ``api`` → all source files (same as architecture for now).
    - Anything else → all source files (silent fallback).

    Returns an empty list when no files match (valid result).
    """
    if topic == "project-identity":
        return ["pyproject.toml"]

    if topic in _ALL_FILES_TOPICS:
        return sorted(changeset.source_files(root))

    if topic.startswith("context/"):
        module = topic[len("context/") :]
        if not module:
            # Malformed — silent fallback to all source files.
            return sorted(changeset.source_files(root))
        return sorted(
            p for p in changeset.source_files(root) if _top_module(p) == module
        )

    # Unknown topic — silent fallback to all source files.
    return sorted(changeset.source_files(root))
