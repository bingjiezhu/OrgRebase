# Model, Agent, and tool interfaces

## Design rule

Models and Agents may interpret, propose, explain, or generate candidates. They do not decide canonical authority, admission, context permissions, impact classification, approval validity, state transitions, Skill release, or evidence class. Those decisions remain strict deterministic contracts.

## Runtime profiles

| Profile | Implementation | Purpose | Evidence class | Failure behavior |
|---|---|---|---|---|
| Deterministic CI | `DeterministicModelProvider` and deterministic Domain providers | Reproducible contract, transaction, benchmark, privacy, and evidence tests | `LOCAL_DETERMINISTIC` | invalid schema returns `SCHEMA_ERROR` |
| Recorded replay | `RecordedModelProvider` | Re-run an exact request digest without a network call | `LOCAL_DETERMINISTIC` when record exists | missing record returns `ABSTAIN` / `NOT_RUN` |
| Live structured model | `LiveHTTPModelProvider` | OpenAI-compatible JSON-schema endpoint | `LIVE_MODEL` only when a provider request ID is returned | missing credentials is `NOT_RUN`; provider/schema error is explicit |
| Bounded repair | `BoundedSchemaRepairProvider` | Initial call plus at most two schema repair attempts | inherits the underlying provider | after three invalid attempts returns `ABSTAIN`; no unbounded retry |
| AgentTeams transport | fixed Workspace Worker pool and transport verifier | Carry exact delegation tasks and structured candidate bytes | `LIVE_AGENTTEAMS` only with complete external evidence | missing or inconsistent K8s/Matrix/artifact/Skill/provider evidence is `NOT_RUN` or rejection |

The default one-command demo uses the deterministic profile. It does not claim an LLM produced the correct benchmark answers.

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
- Caller binding: `X-OrgRebase-Actor`, exact graph revision, target IDs, idempotency key.
- Permission: admitted dependency evidence read only.
- Output: bounded dependency evidence plus immutable `ToolInvocationReceipt` and event-chain binding.
- Degradation: tool failure can lower certainty to `UNKNOWN`; it can never authorize bounded unaffected.

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

| Endpoint | Purpose | Canonical write? |
|---|---|---:|
| `POST /api/workspace/form` | Form Quote v1 from a `TaskRequest` | yes, through atomic Workspace formation transaction |
| `GET /api/workspace/state` | Read current Quote and graph pointers | no |
| `POST /api/workspace/preview/{launch_date|currency}` | Build locked change/preview/VMRC/advisory bundle | audit only; no target write |
| `POST /api/workspace/apply/{launch_date|currency}` | Apply an approved selective rebase | yes, through the deterministic transaction |
| `POST /api/workspace/run` | Execute the local v1 → v2 → restart → v3 loop | yes, local demo store |
| `POST /api/demo/workspace/quote-to-rebase` | Ephemeral one-shot demo | ephemeral local store only |
| `GET /api/demo/workspace/agentteams-status` | Report static/live prerequisites and explicit evidence class | no |
| `/api/*certificates*/verify`, `/api/receipts/verify` | Recompute/validate evidence | no |
| `/api/tools/v1/dependency-evidence*` | Read-only dependency tool contract/invocation | no target write |

FastAPI generates OpenAPI 3.x schemas from the same Pydantic models used by the runtime. Standalone JSON Schemas are regenerated by `scripts/export_contract_schemas.py` and checked for drift.

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
