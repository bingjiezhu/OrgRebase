#!/usr/bin/env bash
set -euo pipefail

# Refresh the short-lived Vertex ADC token used by the local AgentTeams Higress
# provider.  Secret values stay in pipes/variables and are never printed.

context="${AGENTTEAMS_CONTEXT:-kind-orgrebase-agentteams}"
namespace="${AGENTTEAMS_NAMESPACE:-orgrebase-agentteams}"
provider="openai-compat"
console_url="${HIGRESS_CONSOLE_URL:-http://127.0.0.1:18081}"

# This helper sends local administrator credentials and a short-lived model
# token to the Higress console.  Do not allow an environment override to turn
# that local control-plane call into credential exfiltration.
if [[ "$console_url" =~ ^http://(127[.]0[.]0[.]1|localhost|\[::1\])(:([0-9]{1,5}))?$ ]]; then
  console_port="${BASH_REMATCH[3]:-80}"
else
  printf 'HIGRESS_CONSOLE_URL must be loopback HTTP without a path\n' >&2
  exit 1
fi
if (( 10#$console_port < 1 || 10#$console_port > 65535 )); then
  printf 'HIGRESS_CONSOLE_URL has an invalid port\n' >&2
  exit 1
fi

for command_name in curl gcloud jq kubectl; do
  command -v "$command_name" >/dev/null || {
    printf 'missing required command: %s\n' "$command_name" >&2
    exit 1
  }
done

# Use a process-unique cookie jar so parallel refreshes cannot share an
# authenticated session.  Always remove it when the script exits.
cookie_file=$(mktemp "${TMPDIR:-/tmp}/orgrebase-higress-session-cookie.XXXXXX")
trap 'rm -f -- "$cookie_file"' EXIT
chmod 600 "$cookie_file"

vertex_token=$(gcloud auth application-default print-access-token 2>/dev/null)
test -n "$vertex_token"

admin_user=$(kubectl --context "$context" -n "$namespace" \
  get secret orgrebase-agentteams-runtime-env \
  -o jsonpath='{.data.AGENTTEAMS_ADMIN_USER}' | base64 -d)
admin_password=$(kubectl --context "$context" -n "$namespace" \
  get secret orgrebase-agentteams-runtime-env \
  -o jsonpath='{.data.AGENTTEAMS_ADMIN_PASSWORD}' | base64 -d)

login_payload=$(
  jq -nc \
    --rawfile username /dev/fd/3 \
    --rawfile password /dev/fd/4 \
    '{username:$username,password:$password}' \
    3< <(printf '%s' "$admin_user") \
    4< <(printf '%s' "$admin_password")
)
login_code=$(printf '%s' "$login_payload" | curl -sS -o /dev/null -w '%{http_code}' \
  -c "$cookie_file" -X POST "$console_url/session/login" \
  -H 'Content-Type: application/json' --data-binary @-)
if [[ "$login_code" != "200" && "$login_code" != "201" ]]; then
  printf 'Higress login failed: HTTP %s\n' "$login_code" >&2
  exit 1
fi
chmod 600 "$cookie_file"

provider_response=$(curl -fsS "$console_url/v1/ai/providers/$provider" -b "$cookie_file")
provider_payload=$(
  printf '%s' "$provider_response" \
    | jq -ce --rawfile token /dev/fd/3 '
        .data
        | .tokens = [$token]
        | .rawConfigs.apiTokens = [$token]
      ' 3< <(printf '%s' "$vertex_token")
)
update_code=$(printf '%s' "$provider_payload" | curl -sS -o /dev/null -w '%{http_code}' \
  -b "$cookie_file" -X PUT "$console_url/v1/ai/providers/$provider" \
  -H 'Content-Type: application/json' --data-binary @-)
if [[ "$update_code" != "200" && "$update_code" != "201" ]]; then
  printf 'Higress provider refresh failed: HTTP %s\n' "$update_code" >&2
  exit 1
fi

# Keep the cluster runtime Secret consistent so controller-driven reconciliation
# does not restore the expired token.  The patch is supplied over stdin so the
# token is absent from command arguments and logs.
encoded_token=$(printf '%s' "$vertex_token" | base64 | tr -d '\n')
jq -nc --rawfile token /dev/fd/3 '{data:{AGENTTEAMS_LLM_API_KEY:$token}}' \
  3< <(printf '%s' "$encoded_token") \
  | kubectl --context "$context" -n "$namespace" patch \
      secret orgrebase-agentteams-runtime-env --type merge --patch-file /dev/stdin \
      >/dev/null

verified_response=$(curl -fsS "$console_url/v1/ai/providers/$provider" -b "$cookie_file")
printf '%s' "$verified_response" \
  | jq -e --rawfile token /dev/fd/3 '
      .data.tokens == [$token] and .data.rawConfigs.apiTokens == [$token]
    ' 3< <(printf '%s' "$vertex_token") >/dev/null

probe_payload=$(jq -nc '{
  model:"google/gemini-3.1-flash-lite",
  messages:[{role:"user",content:"Reply with OK only."}],
  max_tokens:8,
  stream:false
}')
probe_code=$(printf '%s' "$probe_payload" \
  | kubectl --context "$context" -n "$namespace" exec -i \
      agentteams-worker-orgrebase-change-coordinator -- sh -lc '
        curl -sS -o /dev/null -w "%{http_code}" \
          "$AGENTTEAMS_AI_GATEWAY_URL/v1/chat/completions" \
          -H "Authorization: Bearer $AGENTTEAMS_WORKER_GATEWAY_KEY" \
          -H "Content-Type: application/json" --data-binary @-
      ')
if [[ "$probe_code" != "200" ]]; then
  printf 'AgentTeams gateway preflight failed: HTTP %s\n' "$probe_code" >&2
  exit 1
fi

printf '{"provider":"%s","secret_synced":true,"gateway_preflight":%s}\n' \
  "$provider" "$probe_code"
