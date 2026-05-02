"""Shared path utilities for explore-codebase."""

from __future__ import annotations

from pathlib import Path


def top_module(path: str) -> str | None:
    """Return the top-level directory segment of *path*, or None for root-level files."""
    parts = Path(path).parts
    if len(parts) < 2:
        return None
    return parts[0]
