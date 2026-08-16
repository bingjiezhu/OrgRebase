# Data, licensing, and privacy boundary

## Canonical data inventory

The executable reference profile is self-contained and uses fictional organizations, users, customers, policies, clauses, and changes. No real customer, employee, contract, credential, production telemetry, or user-research record is bundled.

| Asset | Location | Origin | License / redistribution | Data class | Use in score |
|---|---|---|---|---|---|
| OWB v1.1 open-core organizations and cases | `benchmark/orgworkbench/sources`, `public` | project-generated | PolyForm-Noncommercial-1.0.0; bundled | synthetic, no PII | canonical |
| OWB evaluator gold | `benchmark/orgworkbench/evaluator` | project-generated | PolyForm-Noncommercial-1.0.0; bundled but evaluator-isolated | synthetic answer key | canonical evaluator only |
| Private synthetic Domain source pack | `benchmark/orgworkbench/sources/private-synthetic-sources.jsonl` and local providers | project-generated | PolyForm-Noncommercial-1.0.0; bundled for local test | synthetic restricted values | canonical, never public Agent input |
| Northstar core fixture | `fixtures/canonical-enterprise.json` | project-generated | PolyForm-Noncommercial-1.0.0 | synthetic business objects | legacy/core reference |
| Microsoft sample-schema adapter | optional, not bundled as canonical rows | upstream sample repository | MIT; pin version/commit before use | public sample schema | non-scoring realism only |
| CUAD / ContractNLI adapters | optional link/download adapters | upstream datasets | CC-BY-4.0 attribution; separate/download-only | public contract text, review required | non-scoring Legal stress only |
| Consented walkthrough records | supplied separately by the operator | external participants | consent terms, not redistributed by default | redacted qualitative findings | external validation only |
| Live K8s/Matrix/model evidence | supplied separately by the operator | external runtime | deployment-specific | IDs, digests, timestamps; may be sensitive | live milestone only |

The machine-readable canonical inventory is `benchmark/orgworkbench/license-manifest.json`; the benchmark manifest binds cases, gold, organizations, private source pack, seed, splits, and digests. Optional external assets are never downloaded automatically and cannot replace OWB dependency, impact, or privacy gold.

### License boundary

Project-owned source, documentation, fixtures, synthetic benchmark assets, and Skill contracts use `PolyForm-Noncommercial-1.0.0`; commercial use requires a separate written license. The `enterprise-launch-readiness@1.3` contract records the same distribution license and its digest-bound receipts match that contract. FastAPI, Pydantic, Uvicorn, AgentTeams, and optional datasets retain their upstream licenses. See `LICENSE`, `COMMERCIAL-LICENSE.md`, `NOTICE.md`, and `THIRD-PARTY-INVENTORY.md`.

Publisher pages identify Microsoft `sql-server-samples` as MIT, and CUAD / ContractNLI as CC-BY-4.0. These external assets remain disabled, unbundled, non-scoring, and unusable until an exact version/commit and checksum are recorded.

## Why the benchmark is synthetic

The evaluation requires ground truth that normal open enterprise documents do not provide together:

- Domain authority ownership;
- purpose, recipient, organization, and task scope;
- allowed and forbidden context fields;
- exact TaskTemplate slots and minimum coalition;
- source-bound admitted premises;
- output-field lineage;
- complete dependency manifests and trusted edge sets;
- impact classifications and exact VMRC effects;
- successor graph state;
- privacy violations and evaluator-gold isolation.

Real company data would be difficult to redistribute and would still lack complete counterfactual dependency labels. Synthetic data therefore supports reproducible conformance, but it does not establish production accuracy or ROI.

## Data flow and minimization

```text
Synthetic/connected Domain source
→ Domain-specific read adapter
→ source-bound candidate
→ authority/purpose/recipient/org/task/freshness admission
→ actor-specific projection
→ TaskContextManifest
→ ExecutionReferenceMonitor.resolve(slot)
→ Quote field + WorkTrace event + field lineage
→ RuntimeDependencyManifest + WorkspaceGraphSnapshot
→ content-addressed public evidence
```

Controls:

1. a Domain Agent receives only its actor projection and requested slots, not the full store;
2. only Legal may inspect the restricted Legal source;
3. downstream components receive the minimum derived Legal Claim, not raw text;
4. the Quote renderer receives typed resolved values, not raw candidates or a database handle;
5. public evidence records refs, schemas, statuses, evidence classes, and digests rather than raw prompts/sources;
6. evaluator gold is protected by an unforgeable in-process capability and is never passed to the system under evaluation;
7. cross-organization cases use an isolated ephemeral store per case;
8. the evidence builder deletes its SQLite scratch database after exporting the content-addressed JSON evidence required for verification.

## Sensitive-source model

The fixture intentionally includes synthetic restricted legal material to test minimum disclosure. The security property is not “nobody can ever infer anything sensitive.” It is narrower and testable:

- only the Legal adapter may read the source;
- GTM, Task Agent, Quote renderer, public trace, exception, and evidence pack must not contain the raw source;
- a purpose-bound derived obligation Claim may be admitted;
- source and projection digests preserve provenance without redistributing raw text;
- any unmediated read or forbidden-field disclosure fails the hard gate.

## Privacy canaries

Security cases use values prefixed with:

```text
ORGREBASE_CANARY_SECRET_
```

Any occurrence in a Quote, exception, WorkTrace, model log, public evidence artifact, or release report is a critical failure. `WorkspaceEvidenceExporter` and `EvidenceIndexVerifier` scan exported JSON. Privacy tests additionally cover cross-organization access, evaluator-gold access, wrong recipient/purpose, restricted source projection, and unmediated channels.

## Logging and observability policy

Public observability may include:

- stable run/task/actor/worker/model/tool IDs;
- schema, prompt-template, context, input, output, artifact, and policy digests;
- provider request ID when supplied;
- token counts, latency, status, error code, finish reason, and evidence class;
- event-chain and receipt references.

Public observability excludes by default:

- credentials, authorization headers, API keys, tokens, cookies, and environment secrets;
- raw model system/user prompts and free-form outputs;
- raw restricted source or contract text;
- unredacted TaskRequest values from real connectors;
- evaluator gold;
- personal/company identifiers from user walkthroughs.

For the optional Vertex/Gemini profile, public evidence also excludes GCP project IDs, ADC JSON, and project-scoped Vertex endpoints. `agentteams_preflight` and `probe_vertex_openai` report only whether credentials are configured; the published probe fixes the project field to `REDACTED`. The default demo does not call this API.

A deployment that needs raw debugging logs must use a separate access-controlled retention policy; those logs must not be copied into the public evidence directory.

## Retention and deletion

| Data | Reference retention rule |
|---|---|
| Synthetic benchmark and generated evidence | may be retained with the release; PolyForm Noncommercial and no real PII |
| Local Workspace SQLite scratch database | delete after evidence export in the demo; production policy is deployment-specific |
| Live model prompts/outputs | disabled in public evidence; provider-side retention is outside this project and must be reviewed |
| K8s/Matrix live exports | retain only the minimum IDs/digests/timestamps needed for verification; redact room/user content |
| User walkthrough source notes | keep outside the repository; retain only consented redacted structured records for the agreed period |
| Optional external datasets | cache only under their license and organizational policy; never silently vendor into OWB core |

## User validation and consent

`UserWalkthroughRecord` requires explicit consent and accepts only role labels, ratings, pilot intent, redacted findings, and redacted critical gaps. The repository rejects common direct-identifier markers and never requires name, email, phone, company, customer, or raw transcript.

Without real consented participants, the system reports `NOT_RUN`. Synthetic walkthroughs may test aggregation code but cannot be represented as user research. The recommended protocol is in [USER-AND-APPLICATION-SCENARIO](USER-AND-APPLICATION-SCENARIO.md).

## Optional external-data gate

Before enabling any optional adapter, record and verify:

1. exact upstream URL and version/commit;
2. content checksum;
3. SPDX license and required attribution;
4. whether redistribution is bundled, download-only, or prohibited;
5. PII/public-document review outcome;
6. allowed purposes and retention/deletion policy;
7. adapter output schema and redaction rules;
8. confirmation that the external data is non-scoring and cannot alter canonical OWB gold.

Entries with `PIN_AT_IMPLEMENTATION`, missing checksum, unresolved license, or potential PII must fail closed for release bundling.

## Privacy and data risk register

| Risk | Control in this release | Residual boundary |
|---|---|---|
| Raw Legal text reaches GTM or Quote | Legal-only adapter, derived Claim, actor projections, forbidden-field/canary tests | does not prove resistance to every semantic inference attack |
| Cross-organization leakage | org/task binding and one ephemeral store per benchmark case | production multi-tenancy/RLS is not implemented here |
| Model/provider retains sensitive prompts | public profile sends refs/digests; raw logging excluded | live provider retention and contractual terms require deployment review |
| Evaluator gold leaks into Agents | evaluator-only capability and separate files | local process administrator can still read repository files |
| Evidence pack leaks future connector data | allowlisted exporter, canary scan, scratch DB deletion | connector-specific redaction must be added before production |
| User study contains personal/company data | consent gate and structured redacted schema | regex/term checks are not a full DLP system |
| Optional dataset license is misstated or changes | link/download-only, manifest, checksum/version gate | operator must revalidate upstream terms at use time |
| Multiple individually allowed outputs enable re-identification | minimum projections and explicit non-claim | general aggregation-inference prevention is not solved |

## Explicit non-claims

The project does not claim:

- differential privacy;
- confidential computing or cryptographic isolation;
- formal non-interference for arbitrary Agent compositions;
- prevention of every aggregation or re-identification attack;
- exact semantic causality for arbitrary free-form LLM text;
- global completeness of a real enterprise dependency graph;
- production accuracy, ROI, or long-term reliability from synthetic benchmark results;
- that the bundled legacy AgentTeams live run proves the Workspace-specific formation coalition ran live.
