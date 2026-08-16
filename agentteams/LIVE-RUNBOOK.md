# AgentTeams live evidence runbook

This runbook creates one auditable `LIVE_AGENTTEAMS` receipt. A deployment screenshot,
natural-language transcript, unit-test fixture, or replay can never be promoted to live
evidence.

## 1. Pin and preflight

The audited upstream is pinned in `agentteams/source-lock.json`: AgentTeams `v1.2.2` at
commit `849182af8e017168a5a200a87b1062142caf462d`.

```bash
make agentteams-preflight
```

The repository's isolated local Kind profile maps the AgentTeams gateway to
`http://127.0.0.1:28080`, avoiding the upstream demo default `18080`:

```bash
kind create cluster --name orgrebase-agentteams --config agentteams/kind-config.yaml
```

Preflight checks Docker, `kubectl`, static assets, and either an explicit
`AGENTTEAMS_LLM_API_KEY` or usable Vertex Application Default Credentials (ADC); it never
prints a secret. `READY` proves host readiness, not a run.

The tested Vertex route is:

```text
model: google/gemini-3.1-flash-lite
base URL: https://aiplatform.googleapis.com/v1beta1/projects/$PROJECT/locations/global/endpoints/openapi
```

Validate one real but deliberately non-AgentTeams provider call first:

```bash
make vertex-probe
```

For the Helm install, materialize a fresh ADC access token immediately before the command and
pass it through protected process input/secret management, together with
`credentials.llmProvider=openai-compat`, the base URL above, and the tested model. Never commit
the token, the GCP project ID, or a values file that would reconstruct the Vertex URL.
Vertex access tokens are short-lived, so renew
the Kubernetes Secret before expiration for a long run. A provider probe is not Worker evidence.

## 2. Deploy and freeze a run envelope

Install the pinned official runtime and apply `agentteams/team.yaml`. Wait until all Worker
resources have `status.phase=Running`, `status.observedGeneration=metadata.generation`, and
the Team has `status.phase=Active`. Record the exact CR UIDs, generations, Matrix identities,
Team room, model/runtime and controller Pod UIDs in a new run envelope conforming to
`schemas/agentteams-live-run.schema.json`. `Worker.spec.image` is nullable because v1.2.2 may
resolve a Helm-configured runtime default; the envelope must separately bind the resulting
Pod's exact `worker` container image and immutable `status.containerStatuses[].imageID`.

Generate a fresh cryptographically random 32-byte nonce for every attempt. The envelope must
use a collection window of at most two hours:

```bash
openssl rand -hex 32
```

The envelope binds AgentTeams source, one Team, all five Workers, the controller-created Pods,
and the `enterprise-launch-readiness` runtime Skill digest. Do not reuse a nonce for a retry
with different inputs.

## 3. Execute through Matrix

The Team Leader must emit a structured `LEADER_DELEGATION` payload at
`content.orgrebase.run` that commits the compiled `orchestration_plan_digest` before any
candidate event. At least three distinct, previously and unambiguously joined Worker Matrix
identities must then emit `WORKER_CANDIDATE` payloads in the same Team room. Every candidate
event and exact artifact bytes must carry the same plan digest plus its own
`delegation_task_digest` and exact `input_refs`. The Skill Worker must emit
`SKILL_LOADED` with the digest of the actual runtime `SKILL.md` bytes. Payloads follow
`schemas/agentteams-matrix-run-event.schema.json`.

Every output remains a candidate. Agents have no canonical-write credentials; only the
OrgRebase control plane may validate, approve and apply.

## 4. Export authoritative sources

Retain these inputs before the run envelope expires:

- Matrix homeserver JSONL events, including membership state events, server-assigned
  `event_id`, `room_id`, `sender`, `type`, `state_key` where applicable, timestamp and content.
- A candidate artifact manifest plus the exact referenced files. File digests are SHA-256 over
  raw bytes, not copied claims in the manifest.
- The runtime Skill file copied from the Worker filesystem/object store, not the repository
  authoring copy.
- A redacted gateway/provider JSONL export with one successful, unique provider request ID
  for every candidate Worker. Bind each record to the candidate artifact ref and digest.
  Keep request IDs and model metadata; omit prompts, completions, credentials and headers.

Schemas are in `schemas/agentteams-artifact-manifest.schema.json` and
`schemas/agentteams-model-call.schema.json`.

## 5. Collect

While the cluster is reachable and before the envelope expires:

```bash
uv run python scripts/collect_agentteams_evidence.py \
  --run-envelope /secure/run-envelope.json \
  --matrix-events /secure/matrix-events.jsonl \
  --artifact-manifest /secure/artifact-manifest.json \
  --artifact-root /secure/run-artifacts \
  --model-calls /secure/model-calls.jsonl \
  --nonce-ledger evidence/agentteams/nonce-ledger.json \
  --output evidence/agentteams/live-receipt.json
```

The collector queries Kubernetes directly and verifies:

- exact Worker/Team names, UIDs, generations, reconciled status, `specHash`, roles and Matrix IDs;
- controller Worker labels, Running/Ready Pods, exact worker-container images and immutable
  runtime `imageID` digests;
- unambiguous prior Matrix membership for every structured event, leader-before-worker ordering,
  exact sender-to-Worker identity, and
  one run ID/nonce across structured events;
- candidate file bytes, Matrix references, producer identity, candidate-only authority, and
  Leader-committed plan/task/input bindings;
- assigned Skill plus runtime Skill bytes and its structured load event;
- one successful model call per candidate Worker, bound to that candidate's artifact bytes;
- nonce reuse only for a byte-identical source bundle, serialized by a locked atomic ledger;
- that the run envelope is still fresh after collection, not only when collection starts.

Any missing or mismatched predicate produces `NOT_RUN` with a stable error code. There is no
partial-live state.

## Evidence boundary

`LIVE_AGENTTEAMS` proves one observed runtime execution and the integrity of these evidence
bindings. It does not prove production accuracy, ROI, long-term reliability, or a real
enterprise connector.
