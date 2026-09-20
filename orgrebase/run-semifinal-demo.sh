#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$SCRIPT_DIR"

usage() {
  cat <<'EOF'
Usage: ./run-semifinal-demo.sh [interactive|live]

Default mode: interactive (fresh, clickable OAC-to-business journey)
Both modes require OAC adaptation before the Golden AgentTeams journey.
Requires the installed locked Python environment and the sibling oac-spec.
interactive additionally needs a running local Ollama with the exact pinned
qwen2.5:3b model; see README.md for installation and the full model digest.

  interactive  Start a fresh or caller-retained clickable local Pilot.
               OAC execution mode is OFFLINE_LOCAL; no Vertex call is allowed.
  live         Start a fresh or caller-retained clickable Pilot with Vertex.
               OAC execution mode is LIVE_VERTEX; credentials stay external.

The former guided mode is retired. Use interactive for a fresh local journey;
archived mapping receipts cannot authorize a new workspace.

Optional environment:
  ORGREBASE_DEMO_WORK_DIR  New or existing Pilot state directory.
  ORGREBASE_DEMO_PORT      Local console port (default: 8000).
  ORGREBASE_VERTEX_PROJECT Vertex project for live mode (optional when ADC
                           already supplies project configuration).
  ORGREBASE_VERTEX_MODEL_ID Vertex model for live mode. Defaults to
                           gemini-3.8-flash; gemini-3.7-flash remains accepted
                           for replay-compatible verification.
  ORGREBASE_NO_OPEN=1      Do not open the browser automatically.
  ORGREBASE_OLLAMA_ENDPOINT Local HTTP loopback endpoint for interactive mode
                           (default: http://127.0.0.1:11434).

Without a model service, independently verify the archived completed run:
  python3 scripts/verify_golden_pilot_evidence.py \
    --root evidence/golden-competition/latest/pilot
This reads historical evidence; it does not create a new business run.
EOF
}

die() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 2
}

mode="interactive"
if (($# > 0)); then
  case "$1" in
    interactive|live)
      mode="$1"
      shift
      ;;
    guided)
      die 'GUIDED_MODE_RETIRED: use ./run-semifinal-demo.sh interactive; archived mapping receipts cannot authorize a new workspace.'
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      die "unknown mode: $1 (expected interactive or live)"
      ;;
  esac
fi
(($# == 0)) || die "unexpected argument: $1"

DEMO_PORT="${ORGREBASE_DEMO_PORT:-8000}"
DEMO_URL="http://127.0.0.1:${DEMO_PORT}/"
PACK_ROOT="${SCRIPT_DIR}/examples/enterprise-quote-pilot/evergreen"
PILOT_ENTRYPOINT="${SCRIPT_DIR}/run-enterprise-pilot.sh"
OAC_ROOT="${ORGREBASE_OAC_ROOT:-${SCRIPT_DIR}/../oac-spec}"

[[ -x "$PILOT_ENTRYPOINT" ]] || die "Enterprise Pilot entrypoint is missing: $PILOT_ENTRYPOINT"
[[ -d "$PACK_ROOT" ]] || die "Enterprise Quote Pack is missing: $PACK_ROOT"
[[ -f "$OAC_ROOT/pyproject.toml" && -d "$OAC_ROOT/src/oac" ]] || {
  printf '%s\n' "OAC reference implementation is missing: $OAC_ROOT" >&2
  printf '%s\n' 'Set ORGREBASE_OAC_ROOT to the submitted sibling oac-spec directory.' >&2
  exit 2
}

port_python="${SCRIPT_DIR}/.venv/bin/python"
if [[ ! -x "$port_python" ]]; then
  port_python="$(command -v python3)" || die 'Python 3 is required; run uv sync --locked --all-extras first.'
fi
"$port_python" - "$DEMO_PORT" <<'PY'
import socket
import sys

value = sys.argv[1]
if not value.isascii() or not value.isdecimal() or len(value) > 5 or not 1 <= int(value) <= 65535:
    print("ERROR: ORGREBASE_DEMO_PORT must be an integer from 1 to 65535", file=sys.stderr)
    raise SystemExit(2)
try:
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", int(value)))
except OSError:
    print(
        f"ERROR: DEMO_PORT_UNAVAILABLE: 127.0.0.1:{value}; "
        "choose a free ORGREBASE_DEMO_PORT. The existing service was not changed.",
        file=sys.stderr,
    )
    raise SystemExit(2)
PY

open_console_when_ready() {
  if command -v open >/dev/null 2>&1 && [[ "${ORGREBASE_NO_OPEN:-0}" != "1" ]]; then
    (
      for _attempt in {1..80}; do
        if curl --fail --silent --show-error "${DEMO_URL}api/health" >/dev/null 2>&1; then
          open "$DEMO_URL"
          exit 0
        fi
        sleep 0.1
      done
      printf '%s\n' "Demo server did not become ready at ${DEMO_URL}" >&2
    ) &
  fi
}

export ORGREBASE_OAC_ROOT="$OAC_ROOT"
export ORGREBASE_OAC_ADAPTATION_MODE=required
export ORGREBASE_OAC_ADAPTATION_REVIEW_SECONDS=4

if [[ "$mode" == "interactive" || "$mode" == "live" ]]; then
  export ORGREBASE_WORKSPACE_TASK_INTAKE_REQUIRED=1
  provider="ollama-local"
  export ORGREBASE_OAC_EXECUTION_MODE=OFFLINE_LOCAL
  if [[ "$mode" == "live" ]]; then
    provider="vertex-ai"
    export ORGREBASE_OAC_EXECUTION_MODE=LIVE_VERTEX
    export ORGREBASE_VERTEX_MODEL_ID="${ORGREBASE_VERTEX_MODEL_ID:-gemini-3.8-flash}"
    [[ "$ORGREBASE_VERTEX_MODEL_ID" == "gemini-3.8-flash" || \
       "$ORGREBASE_VERTEX_MODEL_ID" == "gemini-3.7-flash" ]] || \
      die "unsupported Vertex model: $ORGREBASE_VERTEX_MODEL_ID"
  fi

  start_args=(start
    --pack "$PACK_ROOT"
    --host 127.0.0.1
    --port "$DEMO_PORT"
    --review-seconds 4
    --identity-mode CONTROLLED_LOCAL_HEADER_IDENTITY
    --competition-mode golden
    --model-provider "$provider")
  if [[ -n "${ORGREBASE_DEMO_WORK_DIR:-}" ]]; then
    start_args+=(--work-dir "$ORGREBASE_DEMO_WORK_DIR")
  fi
  if [[ "$mode" == "live" && -n "${ORGREBASE_VERTEX_PROJECT:-}" ]]; then
    start_args+=(--vertex-project "$ORGREBASE_VERTEX_PROJECT")
  fi

  printf '%s\n' "Starting semifinal demo mode: ${mode} (${ORGREBASE_OAC_EXECUTION_MODE})"
  if [[ "$mode" == "interactive" ]]; then
    printf '%s\n' 'This local clickable Pilot cannot make a new Vertex call.'
    printf '%s\n' 'New Reviewer work requires local Ollama and the exact qwen2.5:3b model; README.md documents setup and model errors. Retained history remains readable without inference.'
  else
    printf '%s\n' "This live Pilot explicitly requests Vertex ${ORGREBASE_VERTEX_MODEL_ID}; only provider receipts prove success."
  fi
  open_console_when_ready
  exec "$PILOT_ENTRYPOINT" "${start_args[@]}"
fi

die "unreachable mode dispatch: $mode"
