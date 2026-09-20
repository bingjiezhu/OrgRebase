# OrgRebase GOAI AgentTeams executable reference package

This package has one competition entrypoint:

```bash
./run-agentteams-demo.sh
```

Equivalent Make targets are available from the extracted source root:

```bash
make goai-agentteams-demo
make goai-agentteams-verify
```

Prerequisites are macOS or Linux, Python 3.12–3.14,
[uv](https://docs.astral.sh/uv/), and Git. `uv` is the only Python bootstrap dependency;
project packages and exact resolved versions are declared by `pyproject.toml` and `uv.lock`.
Git runs one reversible local tool-evidence fixture. The command installs the locked
dependencies, executes the frozen enterprise change scenario, and writes machine-verifiable
output to `evidence/goai-agentteams/latest/`.

Verify the generated evidence and the AgentTeams assets:

```bash
./verify-agentteams-demo.sh
```

## Code-package inventory

| Official code-package item | Included artifact |
|---|---|
| Entrypoint | `run-agentteams-demo.sh`; CLI equivalent `uv run orgrebase agentteams-demo` |
| Dependencies | `pyproject.toml` declarations plus exact `uv.lock`; installed with `uv sync --all-extras --frozen` |
| Configuration | `configs/goai-agentteams-demo.json`, pinned to AgentTeams v1.2.2 and its source commit |
| Sample input | `examples/agentteams/change-request.json` |
| Sample output | `examples/agentteams/run-summary.example.json`; explicitly illustrative, never runtime evidence |
| Runtime evidence | generated `evidence/goai-agentteams/latest/run-summary.json` plus its digest-bound artifacts |
| Public AgentTeams evidence | historical allowlisted fixture under `evidence/agentteams/public/`; frozen fresh Core bundle under `evidence/agentteams/fresh-live/2026-08-25-561171ed039b/` |
| Verifier | `verify-agentteams-demo.sh` or `make goai-agentteams-verify` |

The source distribution includes both shell entrypoints, configuration, examples, AgentTeams
assets, dependency locks, and evidence. Run commands from the extracted source root.

## What runs

The Manager-facing OrgRebase control plane compiles one authority-bound task DAG for a
Team-Leader / Worker reference topology:

1. `change-coordinator` commits the task graph;
2. Product, Legal, GTM, and Skill Workers produce candidate-only structured handoffs;
3. `gtm-steward` invokes the dependency-evidence tool;
4. the control plane verifies all five run and handoff digests, then requires explicit Owner
   approval before Apply;
5. the run exports the output, conflict handling, expired-preview rejection, rollback,
   structured logs, Trace, Metrics, and content-addressed evidence.

The exact configuration is `configs/goai-agentteams-demo.json`; the accepted sample input and
illustrative output are under `examples/agentteams/`; the AgentTeams v1.2.2 CRDs are
`agentteams/team.yaml`; Agent Identity contracts are under `agentteams/identities/`.

This one-command path is deterministic: its candidate payloads come from the frozen fixture.
It verifies the orchestration contract, candidate-only handoffs, tool receipt, approval boundary,
failure handling, rollback, and evidence export. It does **not** claim that live model Workers
autonomously derived those candidates.

## Evidence classes and current status

- `LOCAL_DETERMINISTIC_REFERENCE`: the one-command executable reference. Its
  `semantic_acceptance` is `REFERENCE_FIXTURE_ONLY`; `autonomous_collaboration` is `false`.
- `TRANSPORT_ONLY`: `evidence/agentteams/public/historical-transport-v1.2.2.json` is an
  allowlisted, publishable historical transport/runtime summary. It contains no raw runtime
  sources, credentials, provider request IDs, Matrix event IDs, prompts, or outputs. It does not
  establish autonomous candidate derivation or semantic correctness.
- `LIVE_AGENTTEAMS` / `LIVE_AGENTTEAMS_CORE_PROPOSAL_PLANE`: the frozen bundle at
  `evidence/agentteams/fresh-live/2026-08-25-561171ed039b/` records one fresh Core run with one
  coordinator, four specialist identities, four independent provider executions, four candidate
  artifacts/events, and one runtime Skill. Semantic ingestion admitted four advisory candidates,
  rejected zero, and made zero target writes. Worker execution used the direct OpenClaw gateway;
  Matrix records identity, publication, and collection, not Matrix-inbound delegation.
- Workspace Quote: a separate product reference profile. Its Product/Legal/Finance/GTM
  providers are executable locally; `current_workspace_live` remains `NOT_RUN`.
- OAC Runtime Admission bridge: `NOT_USED_IN_THIS_RUN`; the separate local bridge is
  verified by `make workspace-oac-admission-check`.

The generated `run-summary.json` carries all three machine-readable boundary fields:
`semantic_acceptance`, `autonomous_collaboration`, and `current_workspace_live` under
`run_classification`. Its legacy-named historical receipt block now points only to the public
`TRANSPORT_ONLY` fixture, while `fresh_core_agentteams_evidence` indexes the frozen live Core
bundle without promoting Workspace or OAC Runtime status.

Root-level operational receipts, preflight/probe output, private sessions, live sources, nonce
ledgers, debug material, and complete dispatch logs are runtime-private and excluded from the
source distribution and release staging. They are not dependencies of the executable reference.

To repeat the external runtime instead of the credential-free reference run, follow
`agentteams/LIVE-RUNBOOK.md`. It requires Docker, Kind/Kubernetes, Matrix, the pinned
AgentTeams source, and fresh provider credentials; secrets are intentionally absent from the
package.
