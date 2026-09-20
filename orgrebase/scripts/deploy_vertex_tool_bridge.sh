#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
kube_context=${ORGREBASE_KUBE_CONTEXT:-kind-orgrebase-agentteams}
kube_namespace=${ORGREBASE_KUBE_NAMESPACE:-orgrebase-agentteams}
kind_cluster=${ORGREBASE_KIND_CLUSTER:-orgrebase-agentteams}
image=orgrebase/gemini-tool-bridge:0.1.0
project_id=${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null)}

if [[ -z "$project_id" ]]; then
  echo "GOOGLE_CLOUD_PROJECT or the active gcloud project is required" >&2
  exit 1
fi

upstream_url="https://aiplatform.googleapis.com/v1beta1/projects/${project_id}/locations/global/endpoints/openapi"

docker build --quiet -f "$repo_root/agentteams/tool-bridge/Dockerfile" -t "$image" "$repo_root"
kind load docker-image --name "$kind_cluster" "$image"
kubectl --context "$kube_context" -n "$kube_namespace" create configmap \
  orgrebase-gemini-tool-bridge \
  --from-literal="vertex-upstream-url=$upstream_url" \
  --dry-run=client -o yaml | kubectl --context "$kube_context" -n "$kube_namespace" apply -f -
kubectl --context "$kube_context" -n "$kube_namespace" apply \
  -f "$repo_root/agentteams/tool-bridge/deployment.yaml"
kubectl --context "$kube_context" -n "$kube_namespace" rollout restart \
  deployment/orgrebase-gemini-tool-bridge
kubectl --context "$kube_context" -n "$kube_namespace" rollout status \
  deployment/orgrebase-gemini-tool-bridge --timeout=180s
