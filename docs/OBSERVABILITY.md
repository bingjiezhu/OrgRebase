# Observability and evidence correlation

OrgRebase records two complementary telemetry channels. Both use stable IDs, canonical JSON, SHA-256 digests, and explicit evidence classes.

| Channel | Collected data | Storage and lookup | Current scope |
|---|---|---|---|
| Core change-recovery exporter | OTLP/JSON Trace, Log, and Metrics; five Agent runs; audited tool call; Apply and receipt links | `evidence/latest/{traces,logs,metrics}.otlp.json` and `observability.json`; lookup by `workflow_run_id`, span, task, tool, receipt, or digest | Local deterministic Core demo; one separately frozen Core AgentTeams receipt |
| Workspace evidence channel | `WorkTrace`, `ReferenceResolvedEvent`, `ToolCalledEvent`, transport/rebase receipts, SQLite event chain, evidence index | `evidence/workspace/latest/`; lookup by run/task/object/artifact ID, graph revision, event head, or file digest | Quote formation, two changes, restart, Skill evaluation, and local transport |

## Collection and semantics

`GET /api/demo/observability` and `make demo` build the Core OTLP/JSON export. The control-plane span is the trace root; coordinator, Worker, tool, and Apply spans retain causal parents. Agent spans use `invoke_agent`; tool spans use `execute_tool`. One `workflow_run_id` and nonce bind spans, logs, metrics, receipts, and the tool invocation.

The exporter pins `opentelemetry-semantic-conventions@v1.43.0`, source commit `89aae43`. Stable GenAI attributes follow that developing namespace; project-specific facts use `orgrebase.*`. The project does not claim complete conformance to a semantic convention still marked `DEVELOPMENT`.

The Workspace loop writes execution evidence at the control boundaries:

- every monitored input resolution becomes a digest-chained `ReferenceResolvedEvent`;
- renderer completion records field lineage and closes the `WorkTrace`;
- the read-only dependency tool produces a `ToolInvocationReceipt` and matching `ToolCalledEvent`;
- formation, approval, rebase, graph promotion, Skill evaluation, and idempotency produce typed receipts or events;
- `EvidenceIndexVerifier` checks file hashes, evidence classes, run binding, and privacy canaries.

Core OTLP and Workspace evidence remain separate files because their evidence classes differ. A production collector can ingest both channels without changing the underlying contracts.

## Evaluation and operations

The recorded data supports:

- reconstruction of Agent, Skill, tool, approval, and state-transition order;
- offline verification of task/input/output binding and zero-write candidate limits;
- replay and drift checks using request, schema, policy, graph, and artifact digests;
- latency/token fields for live model profiles when provider receipts exist;
- security review of rejected authority, context, injection, idempotency, and freshness paths;
- linkage from benchmark or Skill outcomes to exact artifacts and versions.

The bundled profile stores JSON files and SQLite events rather than operating an online telemetry backend. Online alerting, long-term retention, and production dashboards remain deployment work. Workspace-specific AgentTeams live telemetry remains `NOT_RUN` until K8s, Matrix, candidate-artifact, Skill, and provider evidence can be correlated.

## Public telemetry policy

Public telemetry may contain stable identifiers, versions, evidence classes, timestamps, token/latency counts, request IDs, and SHA-256 digests. It excludes credentials, authorization headers, project-scoped endpoints, raw prompts, raw model output, restricted Legal text, evaluator gold, and unredacted connector values. Raw AgentTeams session transcripts and nonce ledgers are not release artifacts.
