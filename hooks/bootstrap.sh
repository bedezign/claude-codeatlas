#!/usr/bin/env bash
set -euo pipefail
VENV="${CLAUDE_PLUGIN_DATA}/.venv"
CACHED="${CLAUDE_PLUGIN_DATA}/pyproject.toml.cached"
MANIFEST="${CLAUDE_PLUGIN_ROOT}/pyproject.toml"
if ! diff -q "$MANIFEST" "$CACHED" >/dev/null 2>&1; then
  mkdir -p "$CLAUDE_PLUGIN_DATA"
  [ -d "$VENV" ] || python3 -m venv "$VENV"
  "$VENV/bin/pip" install --quiet "$CLAUDE_PLUGIN_ROOT"
  cp "$MANIFEST" "$CACHED"
fi
