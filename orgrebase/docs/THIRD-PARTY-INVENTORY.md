# Third-party inventory and toolchain decisions

## Direct dependencies

`pyproject.toml` defines compatibility ranges; `uv.lock` records the resolved release used for verification. Dependencies are installed at deploy time and are not vendored into the source tree.

| Component | Verified release / compatibility | License | Purpose and relationship |
|---|---|---|---|
| Python | Verified 3.12.13; installation range `>=3.12,<3.15` | PSF | Native runtime for the control plane, adapters, tests, and CLI; `.python-version` pins the validation interpreter; 3.13/3.14 lack equivalent qualification |
| FastAPI | 0.141.1; `>=0.116,<1` | MIT | HTTP/OpenAPI surface for Workspace, certificates, receipts, and `ToolContract` |
| Authlib | 1.8.0; `>=1.8,<2` | BSD-3-Clause | OIDC Authorization Code client with PKCE for browser sessions |
| HTTPX2 | 2.12.0; `>=2.12,<3` | BSD-3-Clause | Verified HTTPS for the OIDC client, source reader and target adapter |
| Pydantic | 2.13.4; `>=2.11,<3` | MIT | Frozen Agent, Skill, tool, state, and evidence contracts; JSON Schema export |
| rfc8785.py | 0.1.4; `>=0.1.4,<0.2` | Apache-2.0 | RFC 8785 canonicalization for the versioned JSON wire protocol; retained content artifacts keep their original digest codec |
| Uvicorn | 0.52.3; `>=0.35,<1` | BSD-3-Clause | Local ASGI server; it has no canonical write authority by itself |
| SQLite | Python standard library | public domain upstream | Append-only object versions, pointers, events, artifacts, and idempotency in the local profile |
| SQLAlchemy | 2.0.52; `>=2.0,<3` | MIT | One Core statement implementation for SQLite and production PostgreSQL transactions |
| Psycopg / psycopg-binary | 3.3.5; `>=3.2,<4` | LGPL-3.0-only | PostgreSQL driver; installed distributions retain their own license files |
| PyJWT | 2.13.0; `>=2.10,<3` | MIT | JWT signature, issuer, audience and validity verification |
| cryptography | 50.0.1; `>=50,<51` | Apache-2.0 OR BSD-3-Clause | JWT asymmetric primitives and independent Ed25519 audit checkpoints |
| PostgreSQL | Local validation: 17.9; CI: supported 17.x packages | PostgreSQL License | Production state, per-record concurrency and isolated backup/restore qualification |
| AgentTeams | v1.2.3, commit `223ddc2b8073e4c8b93bcbb15e1d717f196c04d9` | Apache-2.0 upstream | Required multi-Agent design and transport target; exact complete Git bundle vendored for offline reconstruction and digest-checked before use. Historical distributed deployment qualification retains its own v1.2.2 identity. |

The development extra directly requests `pytest==9.1.1` (MIT), `pytest-cov==7.1.0` (MIT), `PyYAML==6.0.3` (MIT), and `ruff==0.16.3` (MIT). PyYAML also belongs to the resolved runtime through Uvicorn's standard extra. Complete transitive versions are in `uv.lock`; `requirements.txt` is the frozen runtime export and `requirements-dev.txt` is its frozen additive development delta. `NOTICE.md` records the release-level license boundary.

Project-owned code, documentation, Skills, fixtures, and synthetic benchmark use `PolyForm-Noncommercial-1.0.0`. Commercial use requires a separate license. This does not relicense AgentTeams or any Python dependency.

## Local reference model

The pinned `qwen2.5:3b` local model is subject to the [Qwen RESEARCH LICENSE AGREEMENT](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct/blob/main/LICENSE), whose non-commercial grant is for research or evaluation; commercial use requires a license from its upstream licensor. This was checked against the upstream text and `ollama show --license qwen2.5:3b`. The Ollama application license is separate and does not grant model rights. Model weights are not redistributed in this source package. The reference run is a controlled evaluation; commercial deployment needs upstream permission or a separately qualified model/service with appropriate terms. No model or digest was changed for this disclosure.

## Competition toolchain mapping

The default profile favors a deterministic, offline-verifiable control plane. Every recommended product has a stable interface boundary, so deployment substitutions do not change Claim admission, VMRC, approval, or evidence semantics.

| Competition item | Current implementation and call path | Why this release uses it | Permission boundary | Migration work |
|---|---|---|---|---|
| AgentTeams (required) | v1.2.3; exact source lock; pinned native `projectflow/taskflow` `call_tool` path; fixed Product/Legal/Finance/GTM roles | It is the required collaboration basis and carries exact task/delegation/candidate bytes | AgentTeams owns operational Task state only; candidates have `target_writes=0`; SQLite control plane remains the only canonical writer | Controlled-local native lifecycle is verified. A separate Core proposal-plane live run binds K8s pods, Matrix publication and four provider executions; Workspace/Golden autonomous Matrix-inbound Worker handoff and same-run OAC Resume remain `NOT_RUN` |
| Aliyun cloud Skills | Current project heads: `structured-domain-handoff@1.1.2`, `enterprise-quote-compose@1.3.1`, `enterprise-launch-readiness@1.4.2`; no official cloud Skill is claimed. Manifest v2 bundles exact input/output Schema bytes and the controlled lifecycle runs from an isolated installed wheel | Three exact packages expose governed domain capability contracts, local-only Schema resolution, uniform evaluation partitions and fresh dependency requalification. Each has one canonical bilingual-discoverable `SKILL.md`, Chinese/English references, and Chinese/English/mixed-language evaluation cases | Skill output cannot admit facts, approve Apply, write canonical state, or publish itself; release authority remains process-local and constructed cases do not prove external security/generalization | Spec 052 proves controlled-local restore and restricted invocation of Quote Compose's exact direct predecessor `1.3.0`, including idempotent replay, state rehydration and tamper probes. Persistent signed trust, production rollout rollback and cross-enterprise qualification remain future work |
| Nacos | Frozen JSON model/metric/identity configuration plus source-lock digest | Offline runs need immutable reviewed configuration and deterministic replay | Config changes cannot bypass admission, context, approval, or source-lock verification | Map the existing config/card schemas to Nacos records and add identity/auth plus revision receipts; medium effort |
| Higress | Direct FastAPI endpoints, `ModelProvider`, and HTTP `ToolContract` | A local demo does not need an ingress control plane | Gateway may authenticate, route, rate-limit, and observe; it cannot obtain canonical write authority | Route existing HTTP/model endpoints through Higress and preserve request, schema, idempotency, and audit headers; low-to-medium effort |
| PolarDB for PostgreSQL | Single `StateStore` now supports PostgreSQL through SQLAlchemy Core/Psycopg; local SQLite remains available | Real PostgreSQL transactions, tenant binding, claims and isolated restore are locally verified | Database credentials stay with the control plane and are excluded from candidate subprocesses | Qualify the specific managed database version, network/IAM, backup service and capacity; no PolarDB production qualification is claimed |
| UnifiedModel | Pydantic entities and exported JSON Schemas | Exact immutable schemas are sufficient for the bundled closed-world dataset | Model conversion cannot broaden domain authority or context visibility | Add a schema/entity mapping layer and preserve IDs, versions, digests, and relation semantics; medium effort |
| RocketMQ | Synchronous transaction plus digest-chained events | The reference loop is local and deterministic; asynchronous fan-out is not required | Consumers may receive post-commit notifications, never perform unapproved canonical writes | Add a transactional outbox, event-schema adapter, consumer idempotency, and replay checks; medium effort |
| LoongSuite / AgentScope Studio / AgentLoop | Core OTLP/JSON Trace, Log, Metrics; Workspace `WorkTrace`, SQLite event chain, and evidence index | File evidence is reproducible and reviewable without an external backend | Public telemetry excludes credentials, raw prompts/output, restricted sources, and project-scoped endpoints | Send the existing OTLP records to a collector and index Workspace event/receipt refs; low effort for ingestion, medium for production retention and access control |

## MCP compatibility

The semifinal controlled-local slice invokes AgentTeams TeamHarness through its pinned MCP `call_tool` boundary.
OrgRebase business tools still use the project-owned `ToolContract`, which fixes protocol, authentication,
input/output Schema, errors, permission ceiling, retries, idempotency, audit, degradation, and migration notes.
An enterprise MCP server can wrap the same domain service, but Tool exposure never grants OAC plan admission,
human approval, or canonical-write authority. See `TOOL-CONTRACT.md`.

## Model-provider evidence lanes

### Hosted services and deployment dependencies

These adapters are project code; access to a hosted service is configured separately from installing the Python dependencies above. A compatible protocol or a passing local test does not establish that a customer deployment has been qualified.

| Service | Implemented integration | Deployment requirement and evidence boundary |
|---|---|---|
| Google Vertex AI | `workspace/model_provider.py` and `vertex_tool_bridge.py`; `aiplatform.googleapis.com` | Selected runs need a configured project, location, model and service credentials. Retained Golden calls have their own run IDs and provider receipts; a new deployment needs its own verification. |
| DeepSeek | `workspace/deepseek_provider.py`; `api.deepseek.com/chat/completions` | Explicit Reviewer selection and a service key are required. Local protocol and native integration tests do not prove a live customer run; see [model interfaces](MODEL-AGENT-TOOL-INTERFACES.md#deepseek-structured-reviewer-adapter). |
| OpenAI / other compatible endpoints | `workspace/openai_http_worker.py` uses `api.openai.com/v1/responses`; `LiveHTTPModelProvider` accepts a configured compatible endpoint | The endpoint, credentials and returned model identity belong to the selected provider. Protocol compatibility alone does not identify OpenAI as the service operator or prove a live run. |
| Microsoft Dataverse / Dynamics | `workspace/dataverse.py`, `dataverse_target.py`, `dataverse_qualification.py` | Needs an authorized tenant environment, source mappings and access permissions. Target writes also need a qualified receipt table and concurrency behavior; see [target qualification](DATAVERSE-TARGET-OPERATIONS.md). Local protocol tests do not establish customer production readiness. |
| Matrix / Element | Configured homeserver and room used by the Matrix observation/transport adapters | Publishing needs a reachable homeserver and an authorized account. A local Synapse rehearsal does not qualify a customer's messaging deployment; see [Matrix integration](MATRIX-OBSERVATION.md). |

Service accounts, quotas, data handling settings and commercial permissions must be supplied by the deployment. They are not granted by the license of OrgRebase or of an HTTP client. The retained evidence lanes below describe specific runs, not blanket qualification of these services.

- The current cloud reference explicitly uses `./run-semifinal-demo.sh live`, with fresh OAC admission and Vertex `gemini-3.8-flash` as the default model for that mode. It requires external project configuration and credentials; each run must retain its own actual provider observations. The launcher still selects the older `interactive` mode when no mode argument is given; use explicit `live` for this cloud path. `interactive` remains a local Ollama evaluation path with the exact pinned model and its original license restrictions. Neither mode imports historical mapping receipts to authorize a new workspace.
- The separately retained Golden records two schema-valid Vertex `gemini-3.8-flash` advisories with distinct provider response IDs; the sanitized pack contains no cloud credential or GCP project identifier. Its standalone verifier makes no new model call.
- A fresh independent run may explicitly select the native structured Vertex adapter with bounded `gemini-3.7-flash` or `gemini-3.8-flash`. It receives a new run ID and its own provider response IDs; both Reviewer results stay advisory-only and deterministic review remains authoritative.
- The OpenAI-compatible `google/gemini-3.1-flash-lite` coordinate belongs to a retained Core AgentTeams compatibility profile; it is `RETAINED_NON_CURRENT`, not the frozen Golden provider.
- Another Ollama or Vertex execution cannot inherit or retrofit the retained Golden's evidence; replacing the current baseline requires a separately verified new pack and refreshed release bindings.

Credentials, GCP project identifiers, project-scoped URLs, raw prompts, and raw output are excluded from public evidence. Provider choice never changes deterministic Claim, VMRC, approval, canonical-write or Skill-governance authority.
