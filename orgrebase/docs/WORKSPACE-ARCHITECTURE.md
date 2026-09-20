# OrgRebase Workspace architecture

> Start with the [L0 product truth](SYSTEM-MAP.md). This document is an architecture detail;
> it does not define a second product, current evidence maturity, or current user journey.

## Product identity

OrgRebase Workspace is an **Enterprise Work Build & Recovery System**. It treats an enterprise deliverable like a build artifact:

```text
Task + admitted organizational premises + governed Skill
        → versioned Work artifact + depfile-like WorkTrace

premise changes
        → certified impact preview + exact minimal rebuild plan
        → successor Work artifact + successor dependency graph
```


For the concrete users/current workflow, see [USER-AND-APPLICATION-SCENARIO](USER-AND-APPLICATION-SCENARIO.md). For the step-by-step closure and exceptions, see [AGENT-TASK-CLOSURE](AGENT-TASK-CLOSURE.md). For exact model/Agent/tool contracts, see [MODEL-AGENT-TOOL-INTERFACES](MODEL-AGENT-TOOL-INTERFACES.md).

The product loop is:

```text
Employee task
→ deterministic TaskTemplate match
→ minimum domain-role coalition from the predefined Template/Card set
→ authority/purpose/freshness admission
→ least-sufficient task context
→ reference-monitored rendering
→ WorkTrace + RuntimeDependencyManifest + WorkspaceGraphSnapshot
→ same-run dependency ToolInvocationReceipt + ToolCalledEvent
→ ChangeSet / ImpactCertificate / VMRC
→ selective rebase
→ atomic successor object + successor graph promotion
→ governed Skill candidate and requalification
```

## Four authority boundaries

These are the product's decision boundaries, implemented in one modular application. OAC's
SOURCE / PLAN / EVIDENCE / BENCHMARK classes describe contract authority; they are not deployment layers.

### 1. Task lifecycle and proposals

The Employee Task Agent and Product, Legal, Finance, and GTM domain roles may propose typed requirements and Claims. The default enterprise path uses local deterministic reference providers. Separate controlled AgentTeams formation evidence does not qualify every subsequent change as a live AgentTeams run. Candidates do not admit themselves and never write canonical Work state.

### 2. Deterministic admission and orchestration

The control plane owns:

- TaskTemplate selection and coalition minimization;
- Claim/Policy admission;
- purpose, recipient, organization and task-scope enforcement;
- Context compilation;
- reference-monitored value access;
- dependency/manifest compilation;
- Formation proof-graph replay, graph closure and version pointers;
- impact traversal, `UNKNOWN`, certificates and VMRC;
- exact approval binding and transition eligibility;
- Skill evaluation verdicts.

### 3. Exact Human Owner approval

Each Owner approves only the reviewed source scope and exact preview. Multi-source recovery collects each
original Owner's approval. A delegate acts under an event-bound, expiring grant; this does not change the
organization's admitted ownership. Model output, task completion and evidence cannot create approval.

### 4. Canonical state and effect execution

`StateStore` and `RebaseWorkflow` are the single canonical write kernel. Source promotion, successor work,
graph, receipt and pointer commit together. Production uses PostgreSQL with a dedicated tenant database,
workspace isolation and a restricted runtime role; SQLite supports local use and historical replay.
Credentialed effect workers use the Commit Gateway for external writes. An unknown external outcome requires
reconciliation of the same effect identity; internal transaction success is not proof of external success.

### Evidence across the boundaries

Every trust-boundary object is a strict, frozen Pydantic model with canonical JSON and SHA-256 content addressing. The evidence index verifies file digests, evidence classes and privacy canaries independently of the demo UI.

Content addressing validates each node, not the relations between nodes. Before the first canonical write,
`formation_integrity.py` deterministically replays the exact Profile request through Template/Coalition, local or
live candidate evidence, Admission/Context, renderer/Trace, Coverage/Manifest and trusted-Universe Snapshot/Pointer.
It then compares the full TaskReceipt, Enterprise Seed binding and fixed Event facts. A digest-valid reconstructed
node that disagrees with any other node fails with zero formation writes. This is the Quote Reference Runtime's
`FormationProofGraph` verifier, not an OAC-generic verifier or external authenticity proof.

## Why multiple Agents are necessary

The coalition follows organizational authority rather than arbitrary parallelism:

| Domain Worker | Necessary authority | Forbidden authority |
|---|---|---|
| Product | product plan, launch date, residency capability | Legal/Finance decisions |
| Legal | restricted legal source interpretation and minimum-disclosure Claim | raw contract disclosure to GTM; canonical admission |
| Finance | price band and currency Policy candidates | Legal source access; canonical admission |
| GTM | customer/task intent and partner terms | restricted legal source; state transition |

The `CoalitionPlanner` enumerates all 15 non-empty subsets of the four fixed cards and selects by exact coverage, then card count, declared cost and lexical order. A Quote selects four Domains; a public launch summary selects Product + GTM only.

## Execution-born dependencies

The renderer never receives a raw fixture dictionary. It receives an `ExecutionReferenceMonitor` exposing only `resolve(slot_id)` and `finish(...)`. Each resolution creates a digest-chained event bound to the exact admitted projection. Field lineage maps every output field to a task literal, deterministic computation or one or more resolved slots.

Dependency is recorded at the actual read boundary. Prompt presence alone does not create a dependency.

After Quote v1 commits, `gtm-steward` invokes the read-only dependency-evidence tool against the locked graph revision. Its result wrapper, `ToolInvocationReceipt`, and `ToolCalledEvent` share `run:workspace:complete@v1` and bind the same tool, request, result, and receipt references. The Evidence Index rejects file or binding drift.

## Workspace Snapshot bridge

The existing `ImpactEngine` remains unchanged. `WorkspaceSnapshotBuilder` materializes an immutable `EnterpriseFixture` view from the current graph pointer, snapshot, current object versions and verified manifests. It verifies:

- object, edge, manifest and target closure;
- exact manifest target versions;
- no trusted edge outside the declared universe;
- no reachable Work/Skill outside evaluation scope;
- content digests of every artifact.

This bridge is intentionally a compatibility layer, not a second canonical store.

## Certified change recovery

For each semantic change:

1. current graph snapshot and change object versions are locked;
2. `ImpactEngine` produces `AFFECTED_*`, bounded unaffected, `UNKNOWN`, or Skill requalification;
3. each result receives an independently recomputable `ImpactCertificate`;
4. VMRC maps the exact target set to `REBUILD`, `PRESERVE_WITHIN_BOUNDARY`, `HOLD_FOR_REVIEW`, or `REQUALIFY`;
5. approval binds ChangeSet, Preview, VMRC, authorization root, scope and expiry;
6. Apply rechecks all bindings before opening the transaction;
7. a payload-only handler updates the actual Quote field without gaining state/version authority;
8. successor WorkTrace, manifest, snapshot, graph pointer, receipts, event and idempotency record commit atomically.

Change explanations use each frozen source object's domain and the same admitted capability-card set used
by Formation. One domain task covers all its changed objects; the downstream impact task depends on every
domain task. This is deterministic, zero-write advisory evidence, not a second impact engine or proof of live
model reasoning. Unknown or ambiguous domain capability mapping fails closed.

Paused work is bound to the execution implementation, including templates, advisory, source interpretation
and authorization. An implementation change requires a new review; previously applied archives remain
readable without recomputing or replacing their evidence. The successor pipeline resolves its task template
once and uses that same object for context, monitored execution, coverage and dependency compilation.

A process restart is forced between the launch-date and currency changes. Quote v3 and graph v3 are reconstructed from persisted pointers/artifacts, not Python memory.

## Governed Skill Foundry

The current Curator records task/trace provenance refs but does not read or induce behavior from trace content. It emits an immutable declarative program from a fixed action table, not executable arbitrary Python. The evaluator loads the exact stored candidate bytes and checks 16 constructed cases across replay, held-out, negative-transfer, permission, injection, malformed, resource and canary partitions; expected actions come from that same fixed mapping. Safety failures veto the candidate; the only outcomes are `CANARY` and `QUARANTINED`. This validates a governance scaffold, not trajectory learning, generalization, or production gain.

## AgentTeams boundary

AgentTeams is the required collaboration basis and external runtime target. OrgRebase maps the contest collaboration items onto a **fixed** Product/Legal/Finance/GTM Worker pool; AgentTeams does not choose canonical authority.

| Contest item | AgentTeams capability | OrgRebase mapping |
|---|---|---|
| Role orchestration | Team / Worker CRDs and identity cards | `agentteams/workspace/team.yaml` plus capability cards; `CoalitionPlanner` selects the subset |
| Task decomposition | Delegation tasks | `WorkspaceTransportCompiler` emits `DomainDelegationTask` from admitted slots |
| Context passing | Structured Skill / handoff bytes | Actor-specific projections and candidate bundles; no raw store handle |
| Collaborative execution | Team runtime / transport | Default `LocalDeterministicTransport`; `LiveAgentTeamsTransport` only with complete external evidence |
| State tracking | AgentTeams task / Matrix state | Transport receipt and run/nonce binding; canonical Workspace state remains in `StateStore` (production PostgreSQL, local SQLite) |

The Workspace planner, not AgentTeams semantic matching, chooses the coalition. Live evidence must correlate:

- pinned upstream commit and immutable Worker image;
- Team/Worker UID + generation;
- exact Worker identity/domain/Skill/workspace digest;
- Matrix join membership, sender, room and event IDs;
- run ID, nonce and exact delegation digest;
- candidate artifact bytes/digest;
- provider request ID;
- zero canonical target writes.

Without such a Workspace-specific run the status remains `NOT_RUN`.
