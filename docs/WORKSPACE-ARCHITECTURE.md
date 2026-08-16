# OrgRebase Workspace architecture

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
→ minimum Domain-Agent coalition
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

## Three authority planes

### 1. Proposal plane

The Employee Task Agent and Product, Legal, Finance, and GTM Domain Agents may propose typed requirements and Claims. They do not admit their own output and never write canonical Work state.

### 2. Deterministic control plane

The control plane owns:

- TaskTemplate selection and coalition minimization;
- Claim/Policy admission;
- purpose, recipient, organization and task-scope enforcement;
- Context compilation;
- reference-monitored value access;
- dependency/manifest compilation;
- graph closure and version pointers;
- impact traversal, `UNKNOWN`, certificates and VMRC;
- approval, transaction, idempotency and state transitions;
- Skill evaluation verdicts.

### 3. Evidence plane

Every trust-boundary object is a strict, frozen Pydantic model with canonical JSON and SHA-256 content addressing. The evidence index verifies file digests, evidence classes and privacy canaries independently of the demo UI.

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

A process restart is forced between the launch-date and currency changes. Quote v3 and graph v3 are reconstructed from persisted pointers/artifacts, not Python memory.

## Governed Skill Foundry

The Curator produces an immutable declarative program, not executable arbitrary Python. The evaluator loads the exact stored candidate bytes and verifies the candidate and executed-program digests match. It compares no-Skill, previous-Skill and candidate behavior on the same cases across replay, held-out, negative-transfer, permission, injection, malformed, resource and canary partitions. Safety failures veto aggregate gains; the only release outcomes are `CANARY` and `QUARANTINED`.

## AgentTeams boundary

AgentTeams is the required collaboration basis and external runtime target. OrgRebase maps the contest collaboration items onto a **fixed** Product/Legal/Finance/GTM Worker pool; AgentTeams does not choose canonical authority.

| Contest item | AgentTeams capability | OrgRebase mapping |
|---|---|---|
| Role orchestration | Team / Worker CRDs and identity cards | `agentteams/workspace/team.yaml` plus capability cards; `CoalitionPlanner` selects the subset |
| Task decomposition | Delegation tasks | `WorkspaceTransportCompiler` emits `DomainDelegationTask` from admitted slots |
| Context passing | Structured Skill / handoff bytes | Actor-specific projections and candidate bundles; no raw store handle |
| Collaborative execution | Team runtime / transport | Default `LocalDeterministicTransport`; `LiveAgentTeamsTransport` only with complete external evidence |
| State tracking | AgentTeams task / Matrix state | Transport receipt and run/nonce binding; canonical Workspace state remains SQLite `StateStore` |

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
