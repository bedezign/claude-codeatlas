#!/usr/bin/env bash
set -euo pipefail
if ! command -v uv >/dev/null 2>&1; then
  echo "codeatlas bootstrap: 'uv' not found." >&2
  echo "Install it from https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi
VENV="${CLAUDE_PLUGIN_DATA}/.venv"
CACHED="${CLAUDE_PLUGIN_DATA}/pyproject.toml.cached"
MANIFEST="${CLAUDE_PLUGIN_ROOT}/pyproject.toml"
if ! diff -q "$MANIFEST" "$CACHED" >/dev/null 2>&1; then
  mkdir -p "$CLAUDE_PLUGIN_DATA"
  [ -d "$VENV" ] || uv venv "$VENV"
  VIRTUAL_ENV="$VENV" uv pip install --no-config "$CLAUDE_PLUGIN_ROOT"
  cp "$MANIFEST" "$CACHED"
fi
