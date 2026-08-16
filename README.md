<p align="right"><strong>English</strong> · <a href="README.zh-CN.md">简体中文</a></p>

# OrgRebase

**Know Change: an enterprise work evolution engine.** Let a deliverable record the organization facts it depends on. When those facts change, locate the stale parts, produce a verifiable minimum rebuild plan, and emit a new version with evidence.

**License.** Source-available dual license: [non-commercial PolyForm Noncommercial 1.0.0](LICENSE) · [commercial use needs a separate grant](COMMERCIAL-LICENSE.md). This is not OSI Open Source.

[![CI](https://github.com/bingjiezhu/OrgRebase/actions/workflows/ci.yml/badge.svg)](https://github.com/bingjiezhu/OrgRebase/actions/workflows/ci.yml)

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#scenario">Scenario</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="#verification">Verification</a> ·
  <a href="#license">License</a> ·
  <a href="CHANGELOG.md">Changelog</a>
</p>

## Scenario

When Sales or Deal Desk writes an enterprise quote, Product, Legal, Finance, and GTM each own part of the truth. The usual process is asking people and copying answers. The quote rarely records which sources and versions it used. After a launch date, currency, or policy changes, there is no reliable way to find and update the affected historical work.

OrgRebase treats a quote as a traceable build:

```text
Employee task + admitted organization premises + governed Skill
    -> versioned Quote + WorkTrace + RuntimeDependencyManifest

Organization premises change
    -> ImpactCertificate + exact VMRC
    -> selective rebuild of the Quote + successor graph + RebaseReceipt
```

This repository ships an executable synthetic enterprise slice. Users, the manual workflow, deployment, and the external validation protocol are in [USER-AND-APPLICATION-SCENARIO](docs/USER-AND-APPLICATION-SCENARIO.md).

## Architecture

### Agents

Workspace uses a fixed AgentTeams worker pool and then picks the smallest coalition for each task. One agent does not read the whole company. Agents do not obtain authority by free-form chat.

| Role | Responsibility | Authority boundary |
|---|---|---|
| Employee Task Agent | Bind the employee task to a published `TaskTemplate` and declared slots | Not an AgentTeams worker; cannot invent slots, choose authorities, or write state |
| Product Steward | Product packages, launch date, residency candidates | Cannot speak for Legal/Finance/GTM or admit its own candidates |
| Legal Steward | Derive a minimum notice obligation from restricted sources | Raw contract text cannot leave the Legal domain |
| Finance Steward | Price band and currency-policy candidates | Cannot read Legal sources or approve change |
| GTM Steward | Customer, task, and deliverable semantics | Cannot mark Work status or decide impact |

`CoalitionPlanner` chooses among the 15 non-empty subsets of four capability cards by coverage, size, cost, and lexicographic order. AgentTeams carries roles, structured task transport, and runtime status. The deterministic control plane owns authority, admission, context, impact, approval, and state.

There is a separate change-advisory collaboration plane: `change-coordinator -> product/finance -> gtm`. Impact and VMRC are locked before advisory work; advisory output is explanatory only (`target_writes=0`). The frozen Core five-agent live receipt belongs to that change-advisory slice. It does not stand in for Workspace formation live.

Identity eight-field inventory and AgentTeams capability mapping: [AGENT-IDENTITIES](docs/AGENT-IDENTITIES.md), [WORKSPACE-ARCHITECTURE](docs/WORKSPACE-ARCHITECTURE.md).

### Skills

| Skill | Role | Current status |
|---|---|---|
| `structured-domain-handoff@1.0.0` | Exact, zero-write Domain candidates between AgentTeams workers and the control plane | Static/local contract pass; Workspace live `NOT_RUN` |
| `enterprise-quote-compose@1.1` | Quote experience as three allowed declarative candidate programs | Independent evaluator, exact bytes; local evaluation `CANARY` |
| `enterprise-launch-readiness@1.3` | Map a Core ImpactResult to restricted action candidates | Core local qualification, isolation, and rollback pass |

Agents judge the task and collaboration. Skills encapsulate reusable capability. Tools perform external access. The control plane keeps publish and write rights. A Skill candidate cannot read held-out answers, modify the evaluator, widen permissions, publish itself, or execute arbitrary Python. Full fields: [SKILL-LIST](docs/SKILL-LIST.md).

### Dependencies come from execution

The quote renderer may only call `ExecutionReferenceMonitor.resolve(slot_id)`. Every read enters the digest chain. Output fields bind to already-read slots or declared task constants. `WorkTrace` and the manifest are produced from that record. Dependencies are not agent self-reports, and they are not inferred from “it appeared in the prompt.”

### Verifiable selective rebuild

If the full manifest is missing, the system cannot conclude “unaffected”; incomplete evidence returns `UNKNOWN`. VMRC checks the exact effect set in both directions: omitting a required `REBUILD` fails, and adding an unrelated `REBUILD` also fails. Approval binds ChangeSet, Preview, VMRC, authorization root, scope, and expiry. Apply re-checks every binding before the transaction.

### Skills evolve with organization experience

The curator emits only immutable declarative candidates. The evaluator compares no-Skill, previous-Skill, and candidate on the same replay, held-out, negative-transfer, permission, injection, malformed, resource, and canary partitions. A safety failure quarantines the candidate. Publish results are only `CANARY` or `QUARANTINED`.

## End-to-end loop

1. Receive a `TaskRequest`.
2. The Task Agent matches a template and proposes declared slots.
3. The planner selects the Product/Legal/Finance/GTM minimum coalition.
4. AgentTeams or local transport carries actor-specific projections and candidate bytes.
5. The control plane admits on authority, purpose, recipient, organization, task scope, and freshness.
6. The reference monitor forms Quote v1, WorkTrace, manifest, and graph v1.
7. Read-only dependency tools produce a `ToolInvocationReceipt` and a same-run `ToolCalledEvent`.
8. A launch-date change produces Preview, ImpactCertificates, VMRC, approval, and Quote v2.
9. After process restart, state is recovered from SQLite pointers; a currency change produces Quote v3 and graph v3.
10. The trajectory becomes a Skill candidate; independent evaluation yields CANARY or quarantine.
11. The Evidence Index binds task, agents, tools, Skill, approval, state, and file digests.

Failure, conflict, timeout, overreach, drift, idempotency collision, transaction rollback, and live-evidence rejection paths: [AGENT-TASK-CLOSURE](docs/AGENT-TASK-CLOSURE.md).

## Tools, MCP, context, observability

- No MCP server. `ToolContract` carries protocol, auth, schema, errors, retry, idempotency, audit, degrade, and MCP-migration fields. A later migration adds a protocol adapter only.
- No knowledge-base RAG. Of the four contest context capabilities, two are implemented: SQLite shared state, and WorkTrace / event / OTLP observability.
- Core exports OTLP/JSON traces, logs, and metrics. Workspace uses WorkTrace, tool events, SQLite digest chains, and the Evidence Index.
- Recommended tool versions, replacement reasons, interfaces, permission boundaries, and migration cost: [THIRD-PARTY-INVENTORY](docs/THIRD-PARTY-INVENTORY.md).

## Quick start

Needs Python `>=3.12,<3.15`. `uv` is recommended:

```bash
git clone https://github.com/bingjiezhu/OrgRebase.git
cd OrgRebase
uv sync --all-extras
make workspace-check
make workspace-review-readiness-check
make workspace-demo
```

Without `uv`:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
PYTHONPATH=src python -m pytest -q
PYTHONPATH=src python -m orgrebase workspace-demo --output-dir evidence/workspace/latest
```

Expected business summary:

```text
OrgRebase Workspace: PASS
Final Quote: work:quote_acme@v3 · 2026-09-15 · EUR
Final Graph: graph:workspace@v3
OWB v1.1: PASS
Skill: CANARY
Workspace AgentTeams live: NOT_RUN
```

Start the API:

```bash
make serve
```

Primary endpoints:

```text
POST /api/demo/workspace/quote-to-rebase
GET  /api/workspace/state
POST /api/workspace/form
POST /api/workspace/preview/{launch_date|currency}
POST /api/workspace/apply/{launch_date|currency}
POST /api/workspace/run
GET  /api/demo/workspace/agentteams-status
GET  /api/tools/v1/dependency-evidence/contract
POST /api/tools/v1/dependency-evidence
```

## Verification

| Material | Location |
|---|---|
| Inputs, coalition, Quote v1 | `examples/`, `evidence/workspace/latest/formation/` |
| Two changes, approval, VMRC, Quote v2/v3 | `evidence/workspace/latest/rebase/` |
| Restart recovery | `evidence/workspace/latest/repeatability/` |
| Tool receipt and event | `evidence/workspace/latest/tool/` |
| OWB, Skill, and security evaluation | `evidence/workspace/latest/evaluation/`, `skill/` |
| Content-addressed index | `evidence/workspace/latest/evidence-index.json` |
| Core ProofPack, OTLP, rollback, local Git receipt | `evidence/latest/` |
| Machine facts for this release | `evidence/release-facts.json` |
| Tests, coverage, package re-check | `RELEASE-VERIFICATION.md` |

Contest requirement mapping: [COMPETITION-TECHNICAL-COMPLIANCE](docs/COMPETITION-TECHNICAL-COMPLIANCE.md).

## Current boundary

| Item | Status |
|---|---|
| Workspace local deterministic task formation and two changes | `PASS` |
| AgentTeams static assets and transport verifier | `PASS` |
| Workspace-specific AgentTeams live | `NOT_RUN` |
| Real-user validation | `NOT_RUN` (0 participants) |
| Real enterprise connectors and production ROI | `NOT_RUN` / not claimed |

The synthetic benchmark proves conformance inside the declared boundary. It does not prove accuracy on arbitrary enterprise tasks, global completeness of a real graph, or production return. The public tree does not include raw AgentTeams sessions, credentials, prompts, raw model output, or the nonce ledger.

## Layout

```text
src/orgrebase/workspace/   Workspace formation, recovery, Skill, evaluation
src/orgrebase/             Core impact, certificates, transactions, tools, compensation
agentteams/                Team / Worker / Identity / Skill and transport assets
benchmark/orgworkbench/    12 synthetic organizations, 192 conformance cases
configs/workspace/         Model, metric, and review config
schemas/                   JSON Schema exported from runtime models
tests/                     Positive, negative, attack, fault, and regression tests
evidence/                  Content-addressed run evidence
docs/                      Architecture, compliance, tools, observability, privacy
```

## License

Project-owned code, documentation, Skills, fixtures, and the synthetic benchmark use [PolyForm Noncommercial 1.0.0](LICENSE). Commercial use requires a separate written grant: [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md). This project is source-available. It is not OSI Open Source. Third-party dependencies keep their original licenses.

Security reports use GitHub's private advisory workflow. See [SECURITY.md](SECURITY.md). Contributions follow [CONTRIBUTING.md](CONTRIBUTING.md).
