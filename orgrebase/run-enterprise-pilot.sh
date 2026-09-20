#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
DEFAULT_PACK="${SCRIPT_DIR}/examples/enterprise-quote-pilot/evergreen"

usage() {
  cat <<'EOF'
Usage: ./run-enterprise-pilot.sh [check|preflight|start|init|seal] [options]

Default command: start (launches the Golden UI; two owner clicks remain human)

`check` is the retained Spec 054 deterministic Pack health check. It does not
execute the Spec 060 Golden AgentTeams path.

Common options:
  --pack PATH       Enterprise Quote Pack (default: Evergreen example)
  --work-dir PATH   Fresh output/state directory (default: retained temp directory)

Start options:
  --host HOST       127.0.0.1, localhost, or ::1 (default: 127.0.0.1)
  --port PORT       TCP port 1-65535 (default: 8000)
  --review-seconds N
                    server-enforced owner review window (default: 4)
  --identity-mode MODE
                    approval identity transport (default: CONTROLLED_LOCAL_HEADER_IDENTITY)
  --competition-mode MODE
                    golden (default) or off for the legacy deterministic formation path
  --competition-evidence-dir PATH
                    writable evidence root for fresh Golden runs
  --model-provider PROVIDER
                    ollama-local (offline default), vertex-ai (Gemini Flash), or deepseek (deepseek-flash)
  --vertex-project PROJECT
                    optional Vertex project ID; credentials remain outside the repository

Start environment:
  ORGREBASE_VERTEX_MODEL_ID
                    Vertex model selected by the process (3.7 or 3.8 Flash)
  ORGREBASE_OAC_EXECUTION_MODE
                    DeepSeek requires explicit LIVE_VERTEX: the OAC mapper uses
                    Vertex credentials/project and the Reviewer uses DEEPSEEK_API_KEY.
                    Vertex defaults to LIVE_VERTEX; Ollama defaults to OFFLINE_LOCAL.
  ORGREBASE_OAC_ADAPTATION_MODE
                    required by default. optional/off are explicit diagnostic
                    compatibility overrides and are not the public-demo path.

Authoring options:
  --output PATH     New draft directory for init; it must not exist
  --draft PATH      Edited draft Pack directory
  --output PATH     New sealed Pack directory for seal; it must not exist

Examples:
  ./run-enterprise-pilot.sh
  ./run-enterprise-pilot.sh check --pack /path/to/pack
  ./run-enterprise-pilot.sh preflight --pack /path/to/pack
  ./run-enterprise-pilot.sh start --pack /path/to/pack --work-dir /path/to/state
  ./run-enterprise-pilot.sh init --output /path/to/draft
  ./run-enterprise-pilot.sh seal --draft /path/to/draft --output /path/to/sealed
EOF
}

die() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 2
}

run_orgrebase() {
  if command -v uv >/dev/null 2>&1; then
    uv run --project "$SCRIPT_DIR" orgrebase "$@"
  elif command -v orgrebase >/dev/null 2>&1; then
    orgrebase "$@"
  else
    die 'uv or an installed orgrebase command is required'
  fi
}

command_name="start"
if (($# > 0)) && [[ "$1" != --* ]]; then
  command_name="$1"
  shift
fi

pack="$DEFAULT_PACK"
work_dir=""
host="127.0.0.1"
port="8000"
review_seconds="4"
identity_mode="CONTROLLED_LOCAL_HEADER_IDENTITY"
competition_mode="golden"
competition_evidence_dir=""
model_provider="ollama-local"
vertex_project=""
draft=""
sealed_output=""

while (($# > 0)); do
  case "$1" in
    --pack|--work-dir|--host|--port|--review-seconds|--identity-mode|--competition-mode|--competition-evidence-dir|--model-provider|--vertex-project|--draft|--output)
      (($# >= 2)) || die "missing value for $1"
      case "$1" in
        --pack) pack="$2" ;;
        --work-dir) work_dir="$2" ;;
        --host) host="$2" ;;
        --port) port="$2" ;;
        --review-seconds) review_seconds="$2" ;;
        --identity-mode) identity_mode="$2" ;;
        --competition-mode) competition_mode="$2" ;;
        --competition-evidence-dir) competition_evidence_dir="$2" ;;
        --model-provider) model_provider="$2" ;;
        --vertex-project) vertex_project="$2" ;;
        --draft) draft="$2" ;;
        --output) sealed_output="$2" ;;
      esac
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $1"
      ;;
  esac
done

case "$command_name" in
  check|preflight|start)
    [[ -d "$pack" ]] || die "Pack directory not found: $pack"
    [[ ! -L "$pack" ]] || die "Pack root cannot be a symlink: $pack"
    pack="$(CDPATH= cd -- "$pack" && pwd -P)"
    if [[ -z "$work_dir" ]]; then
      temp_base="${TMPDIR:-/tmp}"
      work_dir="$(mktemp -d "${temp_base%/}/orgrebase-enterprise-pilot.XXXXXX")"
    else
      [[ ! -L "$work_dir" ]] || die "work directory cannot be a symlink: $work_dir"
      mkdir -p -- "$work_dir"
      work_dir="$(CDPATH= cd -- "$work_dir" && pwd -P)"
    fi
    ;;
  seal)
    [[ -n "$draft" ]] || die 'seal requires --draft PATH'
    [[ -n "$sealed_output" ]] || die 'seal requires --output PATH'
    [[ -d "$draft" && ! -L "$draft" ]] || die "draft Pack must be a real directory: $draft"
    [[ ! -e "$sealed_output" && ! -L "$sealed_output" ]] || \
      die "sealed output already exists: $sealed_output"
    ;;
  init)
    [[ -n "$sealed_output" ]] || die 'init requires --output PATH'
    [[ ! -e "$sealed_output" && ! -L "$sealed_output" ]] || \
      die "draft output already exists: $sealed_output"
    ;;
  *)
    die "unknown command: $command_name"
    ;;
esac

case "$command_name" in
  preflight)
    receipt="$work_dir/preflight.json"
    [[ ! -e "$receipt" && ! -L "$receipt" ]] || die "refusing to overwrite: $receipt"
    run_orgrebase enterprise-pilot-preflight --pack "$pack" --output "$receipt"
    printf 'Preflight receipt: %s\n' "$receipt"
    ;;
  check)
    [[ -z "$(find "$work_dir" -mindepth 1 -maxdepth 1 -print -quit)" ]] || \
      die "check work directory must be empty: $work_dir"
    run_orgrebase enterprise-pilot-check --pack "$pack" --work-dir "$work_dir"
    printf 'Pilot work directory (retained): %s\n' "$work_dir"
    printf 'Verification receipt: %s\n' "$work_dir/verification.json"
    ;;
  start)
    [[ "$host" == "127.0.0.1" || "$host" == "localhost" || "$host" == "::1" ]] || \
      die 'start host must be loopback'
    [[ "$port" =~ ^[0-9]+$ ]] || die 'port must be an integer'
    ((10#$port >= 1 && 10#$port <= 65535)) || die 'port must be between 1 and 65535'
    [[ "$review_seconds" =~ ^[0-9]+([.][0-9]+)?$ ]] || \
      die 'review seconds must be a non-negative number'
    [[ "$identity_mode" == "BODY_ACTOR_COMPATIBILITY" || \
       "$identity_mode" == "CONTROLLED_LOCAL_HEADER_IDENTITY" ]] || \
      die 'identity mode must be BODY_ACTOR_COMPATIBILITY or CONTROLLED_LOCAL_HEADER_IDENTITY'
    [[ "$competition_mode" == "golden" || "$competition_mode" == "off" ]] || \
      die 'competition mode must be golden or off'
    [[ "$model_provider" == "ollama-local" || "$model_provider" == "vertex-ai" || "$model_provider" == "deepseek" ]] || \
      die 'model provider must be ollama-local, vertex-ai, or deepseek'
    selected_oac_adaptation_mode="${ORGREBASE_OAC_ADAPTATION_MODE:-required}"
    case "$selected_oac_adaptation_mode" in
      required)
        ;;
      optional|off)
        printf 'DIAGNOSTIC OVERRIDE: OAC adaptation mode is %s; public demos require required.\n' \
          "$selected_oac_adaptation_mode" >&2
        ;;
      *)
        die 'OAC adaptation mode must be required, optional, or off'
        ;;
    esac
    export ORGREBASE_OAC_ADAPTATION_MODE="$selected_oac_adaptation_mode"
    provider_oac_mode="OFFLINE_LOCAL"
    if [[ "$model_provider" == "vertex-ai" ]]; then
      provider_oac_mode="LIVE_VERTEX"
    elif [[ "$model_provider" == "deepseek" ]]; then
      [[ "${ORGREBASE_OAC_EXECUTION_MODE:-}" == "LIVE_VERTEX" ]] || \
        die 'DeepSeek requires explicit ORGREBASE_OAC_EXECUTION_MODE=LIVE_VERTEX and both Vertex mapping credentials/project and DEEPSEEK_API_KEY for the Reviewer'
      provider_oac_mode="LIVE_VERTEX"
    fi
    selected_oac_mode="${ORGREBASE_OAC_EXECUTION_MODE:-$provider_oac_mode}"
    case "$selected_oac_mode" in
      FROZEN_REPLAY)
        [[ "$model_provider" == "ollama-local" ]] || \
          die 'FROZEN_REPLAY cannot use a cloud reviewer provider'
        ;;
      OFFLINE_LOCAL|LIVE_VERTEX)
        [[ "$selected_oac_mode" == "$provider_oac_mode" ]] || \
          die "OAC execution mode $selected_oac_mode conflicts with provider $model_provider"
        ;;
      *)
        die 'OAC execution mode must be FROZEN_REPLAY, OFFLINE_LOCAL, or LIVE_VERTEX'
        ;;
    esac
    export ORGREBASE_OAC_EXECUTION_MODE="$selected_oac_mode"
    printf 'Pilot state directory: %s\n' "$work_dir"
    printf 'OAC adaptation mode: %s\n' "$ORGREBASE_OAC_ADAPTATION_MODE"
    printf 'OAC execution mode: %s\n' "$ORGREBASE_OAC_EXECUTION_MODE"
    start_args=(enterprise-pilot-start \
      --pack "$pack" --store "$work_dir/workspace.sqlite3" --host "$host" --port "$port" \
      --review-seconds "$review_seconds" --identity-mode "$identity_mode" \
      --competition-mode "$competition_mode" --competition-model-provider "$model_provider")
    if [[ -n "$competition_evidence_dir" ]]; then
      [[ ! -L "$competition_evidence_dir" ]] || \
        die 'competition evidence directory cannot be a symlink'
      mkdir -p -- "$competition_evidence_dir"
      competition_evidence_dir="$(CDPATH= cd -- "$competition_evidence_dir" && pwd -P)"
      start_args+=(--competition-evidence-dir "$competition_evidence_dir")
    fi
    if [[ -n "$vertex_project" ]]; then
      start_args+=(--competition-vertex-project "$vertex_project")
    fi
    run_orgrebase "${start_args[@]}"
    ;;
  seal)
    run_orgrebase enterprise-pilot-seal --draft "$draft" --output "$sealed_output"
    ;;
  init)
    run_orgrebase enterprise-pilot-init --output "$sealed_output"
    ;;
esac
