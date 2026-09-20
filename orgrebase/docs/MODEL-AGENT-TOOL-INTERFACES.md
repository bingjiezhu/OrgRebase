# Model, Agent, and tool interfaces

## Design rule

Models and Agents may interpret, propose, explain, or generate candidates. They do not decide canonical authority, admission, context permissions, impact classification, approval validity, state transitions, Skill release, or evidence class. Those decisions remain strict deterministic contracts.

## Runtime profiles

| Profile | Implementation | Purpose | Evidence class | Failure behavior |
|---|---|---|---|---|
| Deterministic CI | `DeterministicModelProvider` and deterministic Domain providers | Reproducible contract, transaction, benchmark, privacy, and evidence tests | `LOCAL_DETERMINISTIC` | invalid schema returns `SCHEMA_ERROR` |
| Recorded replay | `RecordedModelProvider` | Re-run an exact request digest without a network call | `LOCAL_DETERMINISTIC` when record exists | missing record returns `ABSTAIN` / `NOT_RUN` |
| Local structured model | `LocalOllamaStructuredProvider` | Loopback-only JSON-schema review with an exact local model-manifest digest | `LOCAL_OLLAMA_MODEL`; `provider_request_id` stays null and client/response digests are separate observations | missing endpoint/model or schema drift fails closed; never upgraded to external-provider evidence |
| Live structured model | `LiveHTTPModelProvider` | OpenAI-compatible JSON-schema endpoint | `LIVE_MODEL` only when a provider request ID is returned | missing credentials is `NOT_RUN`; provider/schema error is explicit |
| Bounded repair | `BoundedSchemaRepairProvider` | Initial call plus at most two schema repair attempts | inherits the underlying provider | after three invalid attempts returns `ABSTAIN`; no unbounded retry |
| AgentTeams transport | fixed Workspace Worker pool and transport verifier | Carry exact delegation tasks and structured candidate bytes | `LIVE_AGENTTEAMS` only with complete external evidence | missing or inconsistent K8s/Matrix/artifact/Skill/provider evidence is `NOT_RUN` or rejection |

The public-transaction first run uses deterministic candidates and needs no model. The explicitly selected `./run-semifinal-demo.sh live` path uses the configured Vertex provider; its model receipts remain separate from task execution, review and business approval. See the [Demo guide](guide/demo.en.md) for the available launch modes.

## Structured model contract

`ModelRequest` binds every model call to:

```text
request_id / run_id / task_ref / actor_id / purpose
schema_name + schema_digest
context_refs + input_refs
allowed_tool_ids
provider / model_id / model_version
prompt_template_ref + prompt_template_digest
temperature / seed / max_output_tokens / attempt (0..2)
```

`ModelResponseReceipt` records:

```text
request_digest / status / schema validity / output digest
provider request ID (when available)
provider/model/version
token counts / latency / finish reason
error code / completed_at / evidence class
```

Raw prompts, raw restricted sources, and raw free-form model output are not required in public evidence. Structured values are admitted only after Pydantic validation and downstream deterministic policy checks.

## Agent contract matrix

| Agent / role | Primary input | Candidate output | Model-appropriate work | Deterministic checks after the Agent | Tool ceiling |
|---|---|---|---|---|---|
| Employee Task Agent | `TaskRequest`, template catalog summary | `TemplateCandidate`, `TaskRequirementCandidate` | interpret task intent and explain requirement rationale | exact template match, declared-slot allowlist, candidate ordering | no canonical write; no raw Domain source |
| Product steward | task, Product projection, Product source refs | Product `ClaimCandidate` | interpret product packaging/date/residency | Product authority, source digest, freshness, purpose, recipient, org/task scope | Product read only |
| Legal steward | Legal projection and restricted synthetic source | minimum-disclosure legal `ClaimCandidate` | derive notice obligation and abstain outside Legal scope | source binding, projection digest, forbidden-field/redaction policy | Legal source read; no GTM raw disclosure |
| Finance steward | Finance projection and Policy refs | price/currency Policy candidate | interpret allowed price/currency rules | Finance authority, freshness, purpose, recipient | Finance read only |
| GTM steward | customer/task projection and partner-term source | GTM requirement/Claim candidates | explain customer/task context | task scope, recipient, template slot, source digest | GTM read only |
| Change advisory Agents | locked ChangeSet, Preview, RevisionLock, delegation task | candidate-only handoff | explain a change or coverage gap | compilation/coordination/ingestion recomputation; `target_writes=0` | dependency evidence read only |
| Skill Curator | redacted trajectories, template and Skill contract | immutable declarative `SkillCandidateArtifact` | propose reusable bounded operations | exact candidate bytes/digest, no held-out access, permission/side-effect ceiling | no release authority; no arbitrary Python execution |

Workspace Domain Worker cards live in `agentteams/workspace/identities/` (`worker_id`, `domain_id`, `candidate_only`, allowed output schemas). Workspace slot, tool, purpose, and cost ceilings live on capability cards in `src/orgrebase/workspace/templates.py`. Full Core `AgentIdentity` contracts live in `agentteams/identities/` and are compiled by `OrchestrationCompiler`. The Employee Task Agent is `TemplateBoundTaskInterpreter`; it is not a Worker in `agentteams/workspace/team.yaml`. See [AGENT-IDENTITIES](AGENT-IDENTITIES.md).

## Internal task and Domain interfaces

### Task boundary

- `TaskRequest`: organization, actor, purpose, deliverable kind, optional explicit template, task values, customer, and idempotency key.
- `TaskTemplateVersion`: requirement slots, relation/strength, authority Domain, freshness, sensitivity, purposes, recipients, output lineage, forbidden fields, renderer, and allowed tools.
- `CoalitionPlan`: exact selected capability cards, admitted slots, coverage witnesses, revisions, cost, and deterministic tie-break tuple.

### Domain-read boundary

- Typed contracts: `DomainReadRequest` and `DomainReadProjection` in `workspace/models.py` (`ports.py` is the adapter Protocol).
- Default formation path: `DeterministicDomainProvider.source_projection` / `produce`. The Protocol is the seam for a later connector or MCP wrapper; formation does not construct a public HTTP Domain-read tool today.
- `ClaimCandidate`: candidate value, authority/source/freshness/purpose/recipient/org/task bindings.
- `AdmissionDecision`: accepted/rejected state and reason codes; only accepted references may enter `TaskContextManifest`.

A Domain Agent never receives the entire Workspace store. Actor projections and slot-specific requests constrain the data surface before candidate generation.

## Execution reference monitor

The Quote renderer receives only:

```python
monitor.resolve(slot_id)
monitor.finish(output_ref=..., output_digest=..., lineage=...)
```

It does not receive a raw fixture dictionary, SQLite connection, Claim store, or full task context. Every `resolve` call creates a digest-chained `ReferenceResolvedEvent`; `finish` adds the output event and finalizes `WorkTrace`. This is the mechanism behind “execution-born dependencies.”

## Agent-facing and control-plane tools

### `orgrebase.read_dependency_evidence`

- Surface: `GET /api/tools/v1/dependency-evidence/contract` and `POST /api/tools/v1/dependency-evidence`.
- Caller binding in the controlled-local tool endpoint: `X-OrgRebase-Actor`, exact graph revision, target IDs and idempotency key. This local endpoint is not a production authentication mechanism or a production route.
- Permission: admitted dependency evidence read only.
- Output: bounded dependency evidence plus immutable `ToolInvocationReceipt` and event-chain binding.
- Degradation: tool failure can lower certainty to `UNKNOWN`; it can never authorize bounded unaffected.
- Retry ownership: `retry_policy` in the tool contract describes the caller's permitted policy (at most three attempts with exponential jitter for `TRANSPORT_UNAVAILABLE`). The tool itself does not execute that retry loop. A caller must preserve the same request identity and must not retry authorization, graph-revision, or idempotency conflicts as transport failures.

### `orgrebase.git_downstream_artifact`

- Surface: control-plane-only contract at `GET /api/tools/v1/git-artifact/contract`; exercised by the local Git demo/tests.
- Permission: only `control-plane:git-executor`, only `downstream/`, clean repository, exact HEAD/content/approval/request digests.
- Output: real commit/revert IDs and receipts.
- Safety: Agent identities, network access, path escape, dirty tree, hooks/config drift, and cross-run compensation are rejected.

### Workspace Domain adapters

Workspace Domain reads are currently in-process typed ports, not public arbitrary tools. Their contract is `DomainReadRequest → DomainReadProjection`; the same schema can back a future MCP or connector adapter without changing admission or context logic.

### AgentTeams transport

`DomainTransportPort` carries precompiled delegation tasks to selected fixed Workers and returns candidate bundles plus a zero-write transport receipt. The control plane revalidates bytes, digests, worker/domain binding, run/nonce, and evidence class. AgentTeams task state is transport state, not canonical Workspace state.

## Public API surface

### Inspect the contract and caller identity

A loopback local API instance without identity configuration exposes `/docs` for
Swagger UI, `/redoc` for the API reference and `/openapi.json` for the machine-readable
schema. Local instances with controlled role sessions, OIDC or bearer-token authentication
do not expose these documentation routes, including after login or role selection.
Production also excludes them. For a protected instance, inspect the source routes
and standalone contract schemas below; do not relax authentication or expose a
local-demo server to obtain an API reference.

The route definitions in [api.py](../src/orgrebase/api.py),
[workspace/routes.py](../src/orgrebase/workspace/routes.py) and
[source_binding_routes.py](../src/orgrebase/workspace/source_binding_routes.py) are the
current HTTP surface. Typed request bodies appear in OpenAPI; the semantic checks
also enforce source bindings, current versions, authority and exact digests.
Standalone JSON Schemas are checked by `scripts/export_contract_schemas.py`.

Production API clients use verified Bearer identities and server-owned membership.
Browser sessions use the session cookie, matching `Origin` and `X-CSRF-Token` for
mutations; `GET /api/session` returns the active identity and session information.
A body `actor_id` or `X-OrgRebase-Actor` cannot replace authenticated identity.
The optional local role selector simulates roles; it is not enterprise SSO.
See [authentication and browser entry](AUTHENTICATED-DEPLOYMENT.md).

### Task, proposal and exact application

Use `GET /api/workspace/change-options` to discover the admitted fields, current
versions and digests, allowed operations, source bindings and blocked reasons.
Submit a unique `event_id` to `/change-proposals`, then carry that exact ID through
preview, review, approval, application and detail retrieval. The path parameter
`change_kind` is retained for compatibility: new clients supply the registered
`event_id`; the fixed `launch_date` and `currency` names belong to the local reference
scenario and are not the complete change API.

| Endpoint | Purpose | Changes the official Quote? |
|---|---|---|
| `POST /api/workspace/task-intake/prepare` | Prepare a task candidate from the request and admitted enterprise context | no |
| `POST /api/workspace/task-intake/admit` | Confirm the exact prepared task and execution scope | no |
| `POST /api/workspace/task-intake/run` | Execute the admitted task and form its baseline deliverable | yes, on successful formation |
| `GET /api/workspace/state` | Current deliverable, execution and graph state | no |
| `GET /api/workspace/change-options` | Admitted fields, versions, sources and action availability | no |
| `POST /api/workspace/change-proposals` | Register a typed proposal with source and base-version bindings | no |
| `GET /api/workspace/changes` | Paginated change history | no |
| `GET /api/workspace/changes/{event_id}` | Candidate, impact, owner, permitted actions, lineage and recovery detail | no |
| `POST /api/workspace/preview/{change_kind}` | Persist the exact preview, affected-task candidates and verification | no; candidate and audit records only |
| `POST /api/workspace/changes/{event_id}/review-observation` | Record client-reported review time for the exact preview | no; not an approval or a replacement for the server review gate |
| `POST /api/workspace/approve/{change_kind}` | Bind the authorized owner to `preview_digest` and, when required, `recovery_digest` | no; approval record only |
| `POST /api/workspace/apply/{change_kind}` | Apply the exact `approval_digest` through the deterministic transaction | yes, on success |
| `POST /api/workspace/changes/{event_id}/reject` | Record the authorized owner's reason and reject this proposal | no; the current Quote remains unchanged |
| `GET /api/workspace/export/quote` | Export the current Quote | no |
| `GET /api/workspace/export/evidence` | Export the current workspace evidence | no |
| `GET /api/workspace/run-archive` | Read the current run's records and completion bindings | no |
| `GET /api/health`, `GET /readyz` | Service health and configured readiness | no |

Approval and execution are separate permissions. An approved proposal may remain
pending until an authorized executor applies it. Follow the returned
`allowed_actions`, review gate and current digests; stale or cross-round approval
must not be retried as a fresh authorization.

The proposal body is [ChangeProposalInput](../src/orgrebase/workspace/change_proposals.py):
`event_id`, `slot_id`, `value`, `source_ref`, `base_version` and `base_digest`, with the
optional reason, operation, predecessor and read-dependency fields defined there.
Supported slots depend on the admitted template and source binding. They include
`launch_date`, `currency`, `product_plan`, and, for priced quotes, `pricing_policy`
and `quote_basket`. A `pricing_policy` value is a complete
[PricingPolicy](../src/orgrebase/workspace/pricing.py) object: basis-point integers,
tax label, tax mode, rounding and source reference. For example, 10% discount is
`discount_bps: 1000`; the API does not accept the WebUI's percentage text as the
whole policy. Keep the admitted tax fields unless the proposal intentionally changes
them. A priced basket's currency and monetary inputs must agree; changing a currency
label alone is blocked rather than treated as an exchange-rate conversion.

### Return for evidence and resume the same change

| Endpoint | Exact request contract | Effect |
|---|---|---|
| `GET /api/workspace/changes/{event_id}/recovery` | Current event identity | Read the recovery round, eligible tasks/executors, evidence options and context digest |
| `POST /api/workspace/changes/{event_id}/return-for-evidence` | `operation_id`, `expected_context_digest`, `task_id`, `reason`, `required_evidence_refs` | The authorized owner requests evidence before approval; Quote stays unchanged |
| `POST /api/workspace/changes/{event_id}/resume` | `operation_id`, `recovery_digest`, `executor_id`, `evidence` entries containing `ref` and `digest` | An authorized proposer supplies admitted evidence and chooses a registered executor for a new round |

Use the values from the current recovery response. Resuming retains the original
change, prior plans, candidates and receipts. It creates a new preview requiring
review and exact approval; it does not apply the business result or automatically
choose an arbitrary replacement Agent. An unknown outcome is not permission to
redispatch. See [same-change recovery](guide/agentteams.en.md#evidence-recovery) and
the [request models](../src/orgrebase/workspace/change_recovery.py).

### Reference and compatibility boundaries

`POST /api/workspace/form` remains available only where direct formation is allowed.
A runtime requiring task intake returns `410 WORKSPACE_TASK_INTAKE_REQUIRED` and
identifies the prepare/admit/run sequence. The retired local
`POST /api/workspace/run` returns `410 WORKSPACE_STAGED_COMMANDS_REQUIRED`; production
omits it. Neither path is a shortcut around intake, approval or Apply.

The local reference also exposes certificate/receipt verification and read-only tool
contracts. Production uses its own route allowlist, including exclusion of the local
Skill qualification, experience and OAC-adaptation fixtures. Consult the deployment
guide before treating a local route as a customer integration endpoint.

Legacy `/api/demo/*` routes are excluded by default except a read-only AgentTeams
status and a one-shot tombstone that returns `410`. The CLI `workspace-loop` and
retained-evidence builders use explicitly scripted local owner identities; the CLI
requires `--allow-scripted-approval`. Those runs exercise persisted commands but do
not claim an external human decision.

## Error and abstention semantics

The interfaces use explicit states rather than accepting malformed model output:

- `VALID`: schema-valid candidate, still subject to policy/admission;
- `ABSTAIN`: bounded repair failed or the role lacks enough information;
- `SCHEMA_ERROR`: response did not satisfy the exact schema;
- `PROVIDER_ERROR`: provider call failed;
- `NOT_RUN`: credentials/external evidence were not supplied;
- admission/context/authorization errors: candidate cannot cross the control boundary;
- `UNKNOWN`: dependency coverage or traversal evidence is insufficient; never equivalent to unaffected.

This distinction is tested in `tests/workspace/test_model_provider.py`, `test_model_and_transport.py`, `test_planner_and_governance.py`, `test_privacy.py`, and `test_agentteams_live_evidence.py`.

## DeepSeek structured Reviewer adapter

`workspace/deepseek_provider.py` implements the existing `ModelRequest` / `ModelResponseReceipt`
boundary against `https://api.deepseek.com/chat/completions`. The configured API name is
`deepseek-flash`. This is an unpinned provider alias, not an immutable model digest. Requested and observed model
identities remain separate. An unqualified response alias or another model family fails closed.

The adapter uses JSON Object mode, an explicit JSON Schema instruction, local Pydantic
validation and disabled thinking for the bounded structured advisory request. Empty content,
truncation, schema mismatch, redirects and oversized responses do not become valid candidates.
Only `DEEPSEEK_API_KEY` is passed to a selected DeepSeek Reviewer child; it is not passed to
domain workers or to Vertex. Receipts retain bindings and usage observations, never credentials,
raw response text or reasoning content.

This journey requires two providers: Vertex for OAC mapping, and DeepSeek for the
native Reviewer. Configure the Vertex project and credentials described in the
[Vertex guide](guide/models-vertex.en.md), plus `DEEPSEEK_API_KEY`, outside the repository.
From the product source directory, after the normal locked installation:

```sh
export ORGREBASE_OAC_ROOT="$(pwd)/../oac-spec"
export ORGREBASE_VERTEX_MODEL_ID=gemini-3.8-flash
export ORGREBASE_OAC_EXECUTION_MODE=LIVE_VERTEX
./run-enterprise-pilot.sh start --pack examples/enterprise-quote-pilot/evergreen \
  --work-dir /path/to/new-pilot --competition-mode golden --model-provider deepseek \
  --vertex-project YOUR_GCP_PROJECT
```

Replace `YOUR_GCP_PROJECT` with the authorized Vertex project. The launcher requires
explicit `LIVE_VERTEX` for DeepSeek and rejects `OFFLINE_LOCAL`, which permits only
the local provider. Vertex mapping candidates still require deterministic validation
and owner admission; domain workers retain their existing scoped deterministic implementation.
It does not switch the separate `ModelRequestV2` change-advisory adapter or confer approval
or Apply authority. `python -m orgrebase.workspace.deepseek_provider --probe` makes one
small structured API probe, not a business workflow. Without a key it reports `NOT_RUN`.
Protocol and native Reviewer integration have fixture-based tests; those results are not
DeepSeek live-service qualification.

The Vertex Reviewer resolves its dedicated project/model and credential in the parent
process before starting the private-HOME child. Only the selected access token or API key
is forwarded; no ADC directory or unrelated provider credential is copied. This preserves
`gemini-3.8-flash` across the process boundary without granting ordinary domain workers
access to cloud credentials.

Official API references: [model names and compatible endpoint](https://api-docs.deepseek.com/zh-cn/),
[JSON Output](https://api-docs.deepseek.com/zh-cn/guides/json_mode/),
[Chat Completions parameters](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/).

### Live-test opt-in

The local inference fixture in `tests/workspace/test_competition_run.py` requires explicit
`ORGREBASE_TEST_LIVE_OLLAMA=1` before inspecting the local model or executing inference.
Without it, dependent live tests skip; their presence is not a passed model qualification.
The default test suite must not start inference merely because a developer has a model installed.
CI does not currently set that opt-in. Protocol fixtures and frozen-evidence checks remain
available without live model calls. A requested cloud acceptance run uses an explicitly selected
cloud provider and its own evidence, not this optional local fixture.
