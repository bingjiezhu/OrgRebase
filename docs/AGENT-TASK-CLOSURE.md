# Agent task closure and failure semantics

## Closure claim

The repository contains one complete **local deterministic** Workspace task loop:

```text
TaskRequest
→ TaskTemplate interpretation
→ minimum Domain coalition
→ Domain candidate production
→ Claim/Policy admission
→ actor-scoped context projection
→ monitored Quote rendering
→ Quote v1 + WorkTrace + Manifest + Graph v1
→ read-only dependency tool + receipt + ToolCalledEvent
→ launch-date Preview / Certificates / VMRC / approval
→ Quote v2 + Graph v2
→ process restart
→ currency Preview / Certificates / VMRC / approval
→ Quote v3 + Graph v3
→ exact-candidate Skill evaluation
→ content-addressed evidence verification
```

The Workspace-specific live AgentTeams transport is implemented and statically checked, but its external run status remains `NOT_RUN` unless one run supplies correlated Kubernetes, Matrix, candidate artifact, Skill artifact, and provider-request evidence. Local completion must never be relabeled as `LIVE_AGENTTEAMS`.

## Contest eight-step loop

The 21-stage matrix below is the implementation. The contest handbook requires these eight named stages; each is covered:

| Contest step | Implementation stages | Primary evidence |
|---|---|---|
| 1. Task input | 1 | `examples/input/enterprise-quote-task.json` |
| 2. Task decomposition | 2–3, 15 | `formation/coalition-plan.json` |
| 3. Context passing | 4–6 | `formation/task-context.json` |
| 4. Tool calling | 11 | `tool/dependency-evidence-invocation.json`; matching called event |
| 5. Result verification | 9, 13–14, 20 | certificates, VMRC, OWB, Skill receipt |
| 6. Execution evidence | 8–11, 21 | WorkTrace, tool receipt/event, evidence index, Core OTLP |
| 7. Approval and rollback | 16–17; Workspace transaction rollback; Core compensation / Git revert | approval, rebase, failure and rollback evidence |
| 8. Experience precipitation | 20 | `skill/candidate.json`, evaluation receipt |

MCP is not used. Tool calling uses the equivalent `ToolContract`. Knowledge-base RAG is not used; shared state (`StateStore`) and trajectory evidence (WorkTrace / OTLP) satisfy the “at least 2 of the remaining 3” rule.

## Authority separation

| Participant | May do | Must not do |
|---|---|---|
| Employee Task Agent | Match a declared template and propose requirement candidates | Invent undeclared requirement slots; approve its own interpretation |
| Product Domain Agent | Return Product source-bound Claim candidates | Speak for Legal, Finance, or GTM; admit its own Claim |
| Legal Domain Agent | Read the restricted synthetic source and return minimum-disclosure legal candidates | Disclose raw contract text to GTM/renderer; write canonical state |
| Finance Domain Agent | Return price/currency policy candidates | Read Legal source; approve or apply changes |
| GTM Domain Agent | Interpret customer/task intent and partner terms | Read restricted Legal source; mark work stale/current |
| Coalition Planner | Select the minimum card subset that covers admitted slots | Use semantic AgentTeams matching as a canonical authority decision |
| Admission / Context control plane | Decide authority, purpose, recipient, organization, task scope, freshness, and projection | Delegate final authorization to an LLM |
| Execution Reference Monitor | Expose admitted values and record exact reads | Allow renderer access to raw fixture/store dictionaries |
| Impact / Certificate / Apply control plane | Compute classification, VMRC, approval binding, transaction, state, and receipts | Treat Agent candidates as effect plans |
| Skill Curator | Propose immutable candidate bytes/digest from trajectories | Read held-out answers; change evaluator thresholds; publish itself |
| Skill Evaluator | Execute the exact candidate and issue CANARY/QUARANTINED verdict | Substitute a repository implementation for the candidate |
| AgentTeams | Carry exact delegation tasks and candidate bytes | Choose canonical authority, write Workspace graph, or approve Apply |

## End-to-end step matrix

| # | Stage | Responsible component | Input contract | Output / state | Deterministic gate | Verifiable material |
|---:|---|---|---|---|---|---|
| 1 | Task intake | `TemplateBoundTaskInterpreter` | `TaskRequest`, template catalog | `TemplateCandidate`, requirement candidates | template and slot allowlist | `examples/input/enterprise-quote-task.json`; formation tests |
| 2 | Template match | `TaskTemplateMatcher` | candidate + catalog | exact active template | deliverable kind / explicit template binding | interpretation receipt |
| 3 | Coalition plan | `CoalitionPlanner` | admitted slots + capability cards | `CoalitionPlan` | enumerate all 15 non-empty subsets; coverage → size → cost → lexical | `formation/coalition-plan.json` |
| 4 | Domain read | Domain provider / optional transport | `DomainReadRequest` and actor projection | `DomainReadProjection`, Claim candidates | exact domain, actor, purpose, slot, projection | admission decisions and context tests |
| 5 | Admission | `AdmissionController` | template slot, source-bound candidate, revisions | admitted reference or rejection | authority, source, freshness, purpose, recipient, org/task scope | eight admission decisions |
| 6 | Context compile | `TaskContextCompiler` | admitted references + coalition | task manifest + actor projections | minimum sufficient set; explicit exclusions | `formation/task-context.json` |
| 7 | Value access | `ExecutionReferenceMonitor.resolve` | slot ID | typed value + digest-chained event | only admitted slot bindings; no raw store handle | `formation/work-trace.json` |
| 8 | Deliverable render | `QuoteInputAssembler`, `QuoteRenderer` | monitored values + task literals | Quote v1 + field lineage | strict Pydantic output; forbidden-field check | `formation/quote-v1.json` |
| 9 | Coverage compile | `TraceCoverageVerifier`, `RuntimeDependencyCompiler` | trace + output lineage + template | complete runtime manifest | resolved-slot/output-field closure; trusted basis | `formation/trace-coverage.json`, manifest |
| 10 | Formation commit | `WorkspaceFormationService.commit_quote` | prepared objects/artifacts | Quote v1, graph v1, pointer, receipt | one SQLite `BEGIN IMMEDIATE`; idempotency | task receipt, graph snapshot v1, event chain |
| 11 | Dependency tool | `gtm-steward` through `DependencyEvidenceTool` | Quote v1 graph revision, target, run/nonce, idempotency key | real result wrapper, `ToolInvocationReceipt`, `ToolCalledEvent` | actor allowlist, graph lock, schema, idempotency, same-run and request/result digest binding | `tool/dependency-evidence-*.json` |
| 12 | Change admission | `WorkspaceChangeSetBuilder` | current object versions + change spec | semantic `ChangeSetRevision` | exact base/proposed versions and current snapshot lock | launch/currency ChangeSet evidence |
| 13 | Impact preview | existing `ImpactEngine` | immutable `EnterpriseFixture` snapshot | impact results + certificates | admitted/trusted paths, manifest-edge bijection, scope closure, `UNKNOWN` | Preview and ImpactCertificates |
| 14 | Minimal plan | `build_minimal_rebase_certificate` | exact Preview | VMRC effect set | no missing/extra rebuild; UNKNOWN never preserve | launch/currency VMRC evidence |
| 15 | Advisory | `WorkspaceChangeAdvisoryAdapter` / AgentTeams transport | locked change/preview/task | candidate-only handoffs and receipts | exact plan/task/input binding; target writes = 0 | compilation, coordination, ingestion receipts |
| 16 | Approval | `RebaseWorkflow.approve` | ChangeSet + Preview + VMRC | scoped expiring `Approval` | exact digest, auth root, scope, time | approval evidence |
| 17 | Selective apply | base workflow + `QuoteRebuildPayloadHandler` | approved effect set | Quote v2/v3, preserved/review states | freshness, payload oracle, zero unauthorized write | base and Workspace rebase receipts |
| 18 | Successor graph | `WorkspaceGraphApplyExtension` | successor object + monitored context | successor trace, manifest, snapshot, pointer | object/graph/evidence atomicity | graph pointer v2/v3 and Workspace receipt |
| 19 | Restart | `WorkspaceService.reopen` | SQLite pointers/artifacts | reconstructed current state | digest verification on every object/artifact | repeatability tests and final artifacts |
| 20 | Skill foundry | `SkillCurator`, `GovernedSkillEvaluator` | trajectories + exact candidate bytes | CANARY or QUARANTINED | replay, held-out, negative transfer, permission, injection, malformed, resource, canary | skill candidate/evaluation receipt |
| 21 | Evidence release | `WorkspaceEvidenceBuilder`, `EvidenceIndexVerifier` | complete local run | evidence index and demo summary | file SHA-256, canary scan, evidence-class integrity | `evidence-index.json`; `make workspace-evidence-check` |

## Failure and exception branches

A Quote output alone does not close the task. The following branches are part of the closure contract:

| Failure | Required outcome | Representative verifier |
|---|---|---|
| no matching/active TaskTemplate | abstain/reject before Domain work | task-agent tests |
| requirement outside template | reject candidate; do not broaden coalition | planner/governance tests |
| missing capability coverage | no coalition plan | planner exhaustive-subset tests |
| wrong Domain authority or source | admission rejection | governance tests |
| stale, wrong-purpose, wrong-recipient, cross-org, or cross-task candidate | admission/context rejection | privacy and governance tests |
| restricted Legal source requested by GTM/renderer | deny; only derived legal Claim may cross boundary | privacy tests |
| renderer attempts unmediated value access | failure; no complete trace/manifest | formation and failure tests |
| output field lacks lineage | coverage failure; no `COMPLETE` manifest | formation tests |
| tool caller, graph revision, target, run or idempotency binding is invalid | reject; no success receipt may enter the index | tool and evidence tests |
| tool receipt/event request or result digest differs | evidence verification failure | `test_tool_trace.py`, evidence tamper tests |
| manifest does not exactly match trusted inbound edges | bounded unaffected prohibited; result becomes `UNKNOWN` or fails | benchmark / impact tests |
| path search budget exhausted without a hard witness | `UNKNOWN`, never preserve | impact tests |
| no semantic delta | zero writes | legacy/core and Workspace change tests |
| Preview/graph/policy/runtime drift | reject before transaction, zero writes | failure tests |
| VMRC target/disposition mutation | reject | certificate tests |
| payload handler changes a preservation or forbidden field | transaction rollback | rebase tests |
| successor object written but graph evidence fails | whole transaction rollback | fault-injection tests |
| idempotency key reused with different request digest | conflict rejection | repeatability tests |
| exact Skill candidate changed/substituted | evaluator rejection | Skill foundry tests |
| live model unavailable or schema repair exhausted | `NOT_RUN`/`ABSTAIN`; never fabricate live evidence | model-provider tests |
| Matrix sender/nonce/run/artifact/Skill/provider mismatch | live evidence rejection | AgentTeams live-evidence tests |
| no consented external participants | user validation remains `NOT_RUN` | user-validation tests |

## Completion states

| Surface | Current release state | Meaning |
|---|---|---|
| Workspace local deterministic loop | `PASS` | Quote v1 → tool receipt/event → v2 → restart → v3 and evidence verification are executable locally |
| OrgWorkBench open core | `PASS` on bundled reference implementation | 192 synthetic conformance cases; not real-enterprise accuracy |
| Governed Skill Foundry | `CANARY` in local deterministic evaluation | exact candidate passes the bundled safety/quality gates |
| Workspace AgentTeams static assets / verifier | `PASS_STATIC` | identities, workers, Skill, source lock, schemas, and negative verifier tests exist |
| Workspace-specific live AgentTeams run | `NOT_RUN` unless external evidence is supplied | no claim of K8s/Matrix/provider execution from local replay |
| Real user validation | `NOT_RUN` | protocol exists; no consented participant records are bundled |
| Real enterprise connectors / production ROI | `NOT_RUN` / not claimed | adapters and production validation remain future work |

Run `make workspace-review-readiness-check` for a machine-readable cross-check of these boundaries.
