# Third-party inventory and toolchain decisions

## Direct dependencies

`pyproject.toml` defines compatibility ranges; `uv.lock` records the resolved release used for verification. Dependencies are installed at deploy time and are not vendored into the source tree.

| Component | Verified release / compatibility | License | Purpose and relationship |
|---|---|---|---|
| Python | 3.12; supported `>=3.12,<3.15` | PSF | Runtime for the control plane, adapters, tests, and CLI |
| FastAPI | 0.141.1; `>=0.116,<1` | MIT | HTTP/OpenAPI surface for Workspace, certificates, receipts, and `ToolContract` |
| Pydantic | 2.13.4; `>=2.11,<3` | MIT | Frozen Agent, Skill, tool, state, and evidence contracts; JSON Schema export |
| Uvicorn | 0.52.3; `>=0.35,<1` | BSD-3-Clause | Local ASGI server; it has no canonical write authority by itself |
| SQLite | Python standard library | public domain upstream | Append-only object versions, pointers, events, artifacts, and idempotency in the local profile |
| AgentTeams | v1.2.2, commit `849182af8e017168a5a200a87b1062142caf462d` | Apache-2.0 upstream | Required multi-Agent design and transport target; source is not vendored |

Development-only direct dependencies are `httpx2==2.10.0` (BSD), `pytest==9.1.1` (MIT), `pytest-cov==7.1.0` (MIT), `PyYAML==6.0.3` (MIT), and `ruff==0.16.3` (MIT). Complete transitive versions are in `uv.lock`; `requirements.txt` and `requirements-dev.txt` are frozen pip exports. `NOTICE.md` records the release-level license boundary.

Project-owned code, documentation, Skills, fixtures, and synthetic benchmark use `PolyForm-Noncommercial-1.0.0`. Commercial use requires a separate license. This does not relicense AgentTeams or any Python dependency.

## Competition toolchain mapping

The default profile favors a deterministic, offline-verifiable control plane. Every recommended product has a stable interface boundary, so deployment substitutions do not change Claim admission, VMRC, approval, or evidence semantics.

| Competition item | Current implementation and call path | Why this release uses it | Permission boundary | Migration work |
|---|---|---|---|---|
| AgentTeams (required) | v1.2.2; fixed Product/Legal/Finance/GTM Worker pool; Team/Worker CRDs; `WorkspaceTransportCompiler` and source lock | It is the required collaboration basis and carries exact delegation/candidate bytes | Workers are `candidate_only`; `target_writes=0`; SQLite control plane remains the only canonical writer | Static assets and verifier are complete. A live deployment needs K8s/Matrix/provider setup and correlated evidence; Workspace live is currently `NOT_RUN` |
| Aliyun cloud Skills | Project Skills: `structured-domain-handoff@1.0.0`, `enterprise-quote-compose@1.1`, `enterprise-launch-readiness@1.3`; no official cloud Skill is claimed | The demo is enterprise-work formation and recovery, not a cloud-resource operation; custom Skills expose the required domain capability contracts | Skill output cannot admit facts, approve Apply, write canonical state, or publish itself | Add an official Skill as another adapter behind the same input/output, evidence-class, and candidate-only gates; medium integration effort, no control-plane redesign |
| Nacos | Frozen JSON model/metric/identity configuration plus source-lock digest | Offline runs need immutable reviewed configuration and deterministic replay | Config changes cannot bypass admission, context, approval, or source-lock verification | Map the existing config/card schemas to Nacos records and add identity/auth plus revision receipts; medium effort |
| Higress | Direct FastAPI endpoints, `ModelProvider`, and HTTP `ToolContract` | A local demo does not need an ingress control plane | Gateway may authenticate, route, rate-limit, and observe; it cannot obtain canonical write authority | Route existing HTTP/model endpoints through Higress and preserve request, schema, idempotency, and audit headers; low-to-medium effort |
| PolarDB for PostgreSQL | SQLite `StateStore` behind object/event/idempotency contracts | SQLite provides a single-process atomic reference implementation with no external service | Database credentials stay with the control plane; Agents never receive a connection or raw store handle | Port DDL/transactions, add row-level access and concurrency tests, and migrate artifacts/events; medium-to-high effort, models remain unchanged |
| UnifiedModel | Pydantic entities and exported JSON Schemas | Exact immutable schemas are sufficient for the bundled closed-world dataset | Model conversion cannot broaden domain authority or context visibility | Add a schema/entity mapping layer and preserve IDs, versions, digests, and relation semantics; medium effort |
| RocketMQ | Synchronous transaction plus digest-chained events | The reference loop is local and deterministic; asynchronous fan-out is not required | Consumers may receive post-commit notifications, never perform unapproved canonical writes | Add a transactional outbox, event-schema adapter, consumer idempotency, and replay checks; medium effort |
| LoongSuite / AgentScope Studio / AgentLoop | Core OTLP/JSON Trace, Log, Metrics; Workspace `WorkTrace`, SQLite event chain, and evidence index | File evidence is reproducible and reviewable without an external backend | Public telemetry excludes credentials, raw prompts/output, restricted sources, and project-scoped endpoints | Send the existing OTLP records to a collector and index Workspace event/receipt refs; low effort for ingestion, medium for production retention and access control |

## MCP compatibility

MCP is not used in the current release. `ToolContract` is the equivalent integration contract and fixes protocol, authentication, input/output Schema, errors, permission ceiling, retries, idempotency, audit, degradation, and migration notes. An MCP server would wrap the same domain services and schemas; authorization and business logic remain in the control plane. See `TOOL-CONTRACT.md`.

## Optional closed model

`make workspace-demo` calls no commercial API. A separately enabled AgentTeams profile may use Vertex AI through an OpenAI-compatible endpoint with `google/gemini-3.1-flash-lite`. Credentials, GCP project identifiers, project-scoped URLs, raw prompts, and raw output are excluded from public evidence. Disabling the live provider does not change deterministic Claim, VMRC, or Skill evaluation behavior.
