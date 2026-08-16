# Generated evidence

Checked-in files are local deterministic receipts for review, not a production audit archive.

## Workspace

`make workspace-demo` writes `evidence/workspace/latest/`.

| Path | What it shows |
|---|---|
| `workspace-demo.json` | Final Quote v3, OWB score, Skill CANARY, AgentTeams status |
| `formation/` | Task receipt, coalition, admissions, Quote v1, WorkTrace, manifest, graph snapshot |
| `rebase/` | Launch-date and currency ChangeSet, Preview, VMRC, receipts, successor Quotes |
| `repeatability/` | Quote v3 and graph pointer after process restart |
| `evaluation/` | OrgWorkBench v1.1 results |
| `skill/` | Exact candidate bytes and evaluation receipt |
| `user-validation.json` | `NOT_RUN` until consented walkthroughs exist |
| `evidence-index.json` | Content-addressed index; `make workspace-evidence-check` verifies it |
| `review-readiness.json` | Five-criterion machine report |

Workspace-specific live AgentTeams remains `NOT_RUN` unless correlated K8s / Matrix / provider evidence is supplied separately.

## Core

`make demo` replaces files under `evidence/latest/` with deterministic local evidence.
The manifest records a digest for every exported artifact and keeps `LIVE_AGENTTEAMS`
separate from local/static proof.

`release-facts.json` combines the Workspace summary, verified Core manifest, the
Core change-advisory AgentTeams receipt, candidate-ingestion receipt, and current
code-release checks. It keeps Workspace live status separate from the Core receipt.

Raw AgentTeams session transcripts and nonce ledgers are private runtime material.
They are excluded from this public release and rejected by the source-manifest and
offline-package checks.
