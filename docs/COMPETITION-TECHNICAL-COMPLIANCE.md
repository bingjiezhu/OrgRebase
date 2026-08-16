# Competition technical compliance

This document maps the cross-stage technical requirements to executable code and evidence. `PASS_LOCAL` means the deterministic reference profile runs and is verified locally. It does not imply a production deployment or external AgentTeams run.

## Requirement status

| Requirement | Status | Implementation and evidence | Boundary |
|---|---|---|---|
| 1.1 At least three Agents; AgentTeams as design basis | `PASS_LOCAL` / live `NOT_RUN` | Four fixed Domain Workers in `agentteams/workspace/team.yaml`; minimum coalition in `workspace/planner.py`; AgentTeams mapping and transport in `WORKSPACE-ARCHITECTURE.md` and `workspace/transport.py` | Workspace-specific K8s/Matrix/provider execution needs external evidence |
| 1.2 Agent Identity inventory | `PASS` | Eight Appendix A fields for Employee Task Agent and four Domain Workers in `AGENT-IDENTITIES.md`; transport cards in `agentteams/workspace/identities/`; Core identities in `agentteams/identities/` | Compact Workspace JSON cards and human Appendix A serve different contracts |
| 1.3 Multi-Agent task closure | `PASS_LOCAL` | Twenty-stage matrix in `AGENT-TASK-CLOSURE.md`; formation, tool, two rebases, restart, Skill evaluation, evidence index, transaction rollback tests | Workspace has transaction-level rollback; Core has separate business-compensation receipts |
| 2.1 Skill | `PASS` | Three custom Skills with purpose, I/O, trigger, dependencies, failure, safety, reuse, and Agent relation in `SKILL-LIST.md` | No Aliyun official cloud Skill is claimed |
| 2.2 MCP or equivalent contract | `PASS_EQUIVALENT` | `ToolContract` and `TOOL-CONTRACT.md` cover protocol, auth, schemas, errors, permissions, retry, idempotency, audit, degradation, and MCP migration | No MCP Server in this release |
| 2.3 Observability | `PASS` | Core OTLP/JSON Trace, Log, Metrics; Workspace WorkTrace, tool event, SQLite event chain, and evidence index; `OBSERVABILITY.md` | Online backend, alerts, and retention are deployment work |
| 2.4 RAG/context enhancement | `PASS_2_OF_4` | Shared state: SQLite `StateStore`; trajectory observability: WorkTrace, events, OTLP | No knowledge-base RAG; no claim that RAG is implemented |
| 3 Toolchain and resource explanation | `PASS` | Versions, call paths, Agent/Skill/tool relationship, replacement reasons, permissions, and migration work in `THIRD-PARTY-INVENTORY.md` | Recommended products are not counted as features when unused |

## Required eight-step closure

| Required stage | OrgRebase implementation | Verifiable material |
|---|---|---|
| Task input | `TaskRequest` accepts the enterprise Quote task | `examples/input/enterprise-quote-task.json`; task receipt |
| Task decomposition | Template-bound requirements and deterministic minimum coalition | interpretation receipt; `formation/coalition-plan.json` |
| Context passing | Actor projection, admitted Claims, exact delegation bytes, least-sufficient `TaskContextManifest` | `formation/task-context.json`; transport receipts |
| Tool call | Read-only dependency evidence tool with real receipt and matching `ToolCalledEvent`; Domain access stays behind typed ports | `tool/dependency-evidence-invocation.json`; `tool/dependency-evidence-called-event.json`; `TOOL-CONTRACT.md` |
| Result verification | Output lineage, trace coverage, graph closure, ImpactCertificates, VMRC, OWB, Skill partitions | formation/rebase/evaluation/skill evidence |
| Evidence deposition | Canonical JSON, SHA-256, run binding, event chain, Evidence Index | `evidence/workspace/latest/evidence-index.json` |
| Approval and rollback | Approval binds ChangeSet/Preview/VMRC/scope/expiry; fault injection proves Workspace zero-write rollback; Core exposes compensation/Git revert receipts | approval/rebase receipts; rollback tests; `evidence/latest/rollback-*.json` |
| Experience deposition | Exact declarative Skill candidate enters independent evaluation and can only become CANARY or QUARANTINED | `skill/candidate.json`; `skill/evaluation-receipt.json` |

## AgentTeams capability mapping

| AgentTeams capability | Concrete mapping |
|---|---|
| Role orchestration | Fixed Team/Worker CRDs, source-locked identity/capability cards, and a deterministic coalition selector |
| Task decomposition | `TaskTemplate` slots become exact `DomainDelegationTask` objects; change advisory uses a bounded DAG |
| Context transfer | Task and actor projections, source refs, schemas, run/nonce/deadline, and candidate digests |
| Collaborative execution | Same delegation contracts over local deterministic or live AgentTeams transport |
| State tracking | AgentTeams run/task receipts for transport; SQLite object versions, pointers, and events for canonical state |

## Reproduction

```bash
make workspace-check
make workspace-review-readiness-check
make workspace-release-check
```

The code release reports Workspace AgentTeams live, real user validation, and real enterprise connectors as `NOT_RUN`. The frozen Core change-advisory live receipt is retained as a separate evidence class and cannot be used to upgrade those Workspace milestones.
