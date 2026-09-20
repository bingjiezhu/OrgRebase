#!/usr/bin/env bash
set -euo pipefail

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv is required; see https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 127
fi
if ! command -v git >/dev/null 2>&1; then
  echo "ERROR: git is required by the focused reversible-tool checks" >&2
  exit 127
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir"

uv sync --all-extras --frozen
uv run orgrebase agentteams-verify --output-dir evidence/goai-agentteams/latest
uv run python scripts/validate_assets.py
uv run pytest -q \
  tests/test_goai_agentteams.py \
  tests/test_context_and_collaboration.py \
  tests/test_agentteams_live_evidence.py \
  tests/test_agentteams_candidate_ingest.py \
  tests/test_agentteams_evidence_publication.py
