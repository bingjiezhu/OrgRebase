#!/usr/bin/env bash
set -euo pipefail

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv is required; see https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 127
fi
if ! command -v git >/dev/null 2>&1; then
  echo "ERROR: git is required for the reversible local tool-evidence fixture" >&2
  exit 127
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir"

uv sync --all-extras --frozen
uv run orgrebase agentteams-demo \
  --config configs/goai-agentteams-demo.json \
  --input examples/agentteams/change-request.json \
  --output-dir evidence/goai-agentteams/latest
