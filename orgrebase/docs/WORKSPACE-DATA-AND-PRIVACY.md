# Data, licensing, and privacy boundary

## Canonical data inventory

The executable reference profile is self-contained and uses fictional organizations, users, customers, policies, clauses, and changes. No real customer, employee, contract, credential, production telemetry, or user-research record is bundled.

| Asset | Location | Origin | License / redistribution | Data class | Use in score |
|---|---|---|---|---|---|
| OWB v1.1 open-core organizations and cases | `benchmark/orgworkbench/sources`, `public` | project-generated | PolyForm-Noncommercial-1.0.0; bundled | synthetic, no PII | canonical |
| OWB evaluator gold | `benchmark/orgworkbench/evaluator` | project-generated | PolyForm-Noncommercial-1.0.0; bundled but evaluator-isolated | synthetic answer key | canonical evaluator only |
| Private synthetic Domain source pack | `benchmark/orgworkbench/sources/private-synthetic-sources.jsonl` and local providers | project-generated | PolyForm-Noncommercial-1.0.0; bundled for local test | synthetic restricted values | canonical, never public Agent input |
| Northstar core fixture | `fixtures/canonical-enterprise.json` | project-generated | PolyForm-Noncommercial-1.0.0 | synthetic business objects | legacy/core reference |
| Evergreen Enterprise Quote Pack | `examples/enterprise-quote-pilot/evergreen` | project-authored fictional reference scenario | PolyForm-Noncommercial-1.0.0 | high-fidelity synthetic; no PII or customer source | Specs 056–057 baseline; Specs 059–060 Golden input |
| Golden HTTP Source/Tool/Skill payloads and receipts | `evidence/golden-competition/latest/pilot` | runtime-derived from the sealed Evergreen Pack and controlled fault injection | PolyForm-Noncommercial-1.0.0 | synthetic source bytes plus run metadata | current causal proof |
| Current Golden Vertex advisory receipts | same Golden evidence pack | two runtime calls to Vertex `gemini-3.8-flash` | sanitized receipt metadata may be redistributed under the project license; provider service terms remain separate | provider response ID, request/output digests, token/latency/finish metadata; no credential, GCP project, project endpoint, raw prompt or free-form output | controlled-local live-model advisory proof only; not production-provider qualification |
| Governed experience artifacts | `state.json`, `manifest.json`, and `evidence-export.json` in the same Golden pack | deterministically derived from the same-run Finance recovery chain | PolyForm-Noncommercial-1.0.0 | digest-bound candidate/evaluation/approval/release metadata; no new customer data | Spec 060 single-run-seed governance proof only |
| Employee Task Intake receipt | Workspace SQLite artifact, state and evidence export | runtime-derived from an employee work description and exact human confirmation | deployment-specific; public Demo uses the project-owned scenario | prompt digest/length plus candidate, approval, task, OAC and Formation digests; raw work description excluded | same-run causal start proof only; not a general prompt planner |
| Private Task Intake work-description record | access-controlled Workspace SQLite artifact only | exact employee-submitted text, bounded to 500 Unicode characters / 2048 UTF-8 bytes | deployment-specific; never copied into bundled evidence | raw work description plus exact run, actor, candidate, approval and Formation bindings | task-intent display for the current actor only; contributes no facts, authority or canonical writes |
| Microsoft sample-schema adapter | optional, not bundled as canonical rows | upstream sample repository | MIT; pin version/commit before use | public sample schema | non-scoring realism only |
| CUAD / ContractNLI adapters | optional link/download adapters | upstream datasets | CC-BY-4.0 attribution; separate/download-only | public contract text, review required | non-scoring Legal stress only |
| Consented walkthrough records | supplied separately by the operator | external participants | consent terms, not redistributed by default | redacted qualitative findings | external validation only |
| Live K8s/Matrix/model evidence | supplied separately by the operator | external runtime | deployment-specific | IDs, digests, timestamps; may be sensitive | live milestone only |

The machine-readable canonical inventory is `benchmark/orgworkbench/license-manifest.json`; the benchmark manifest binds cases, gold, organizations, private source pack, seed, splits, and digests. Optional external assets are never downloaded automatically and cannot replace OWB dependency, impact, or privacy gold.

### License boundary

Project-owned source, documentation, fixtures, synthetic benchmark assets, and Skill contracts use `PolyForm-Noncommercial-1.0.0`; commercial use requires a separate written license. The current Skill registry heads are `enterprise-launch-readiness@1.4.2`, `enterprise-quote-compose@1.3.1`, and `structured-domain-handoff@1.1.2`; each manifest-v2 package records the same distribution license and binds its exact contract, program, single canonical `SKILL.md`, and input/output Schema bytes. Bilingual discovery terms and Chinese/English references do not create a second executable Skill copy. The controlled lifecycle loads those resources from an isolated installed wheel, uses local-only Schema resolution, and retains fresh-requalification receipts. This remains process-local packaging evidence, not persistent external trust or cross-enterprise qualification. `enterprise-launch-readiness@1.3` is retained as an exact legacy contract for older runtime/GOAI evidence and is not the current registry head. FastAPI, Pydantic, Uvicorn, AgentTeams, and optional datasets retain their upstream licenses. See `LICENSE`, `COMMERCIAL-LICENSE.md`, `NOTICE.md`, and `THIRD-PARTY-INVENTORY.md`.

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

### Evergreen generation and rights statement

Evergreen Industries, Blue Harbor, every employee/owner identifier, policy, date, price band, clause and Quote value
in the current Specs 059–060 Golden scenario is fictional and authored for this project. The fixture was not copied, scraped or sampled
from a customer system and contains no real person, company, contract or quote. `enterprise-pilot-seal` recomputes
the Pack's exact content digests; the Golden run then derives task envelopes, the intentional Finance-A1 missing-
evidence fault, HTTP Source/Tool results and receipts from those project-owned bytes. The injected fault is labelled
`SYNTHETIC_FAULT_INJECTION`; the resulting ABSTAIN/REPLAN/Tool/PASS control flow is executed at runtime. Project
ownership and redistribution follow `PolyForm-Noncommercial-1.0.0`; commercial use requires the separate grant in
`COMMERCIAL-LICENSE.md`.

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

The employee task entry uses a separate minimized chain:

```text
raw work description (bounded private tenant-store record)
→ server-side scope / authority / OAC validation
→ prompt digest + exact admitted Task candidate
→ human confirmation digest
→ same-transaction private source-text binding
→ atomic Formation + Task Intake run receipt
→ state / evidence projection without raw text
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
9. the private work-description endpoint requires the exact current task actor; State, run responses, Events, OTLP and evidence exports expose only its digest and length;
10. a public proof package must never copy the live Workspace database verbatim after private text is retained: build a fresh evidence-only SQLite projection that omits private records and compacts new pages, or omit the database from the public package.

The current retained Golden sent only the exact Reviewer projection required for a structured `REPLAN` or `PASS`
advisory to Vertex `gemini-3.8-flash`. Both attempts record a provider response ID; the deterministic Reviewer
recomputes the verdict and remains authoritative. After Quote v3,
experience extraction consumes same-run digests and bounded status metadata rather than reopening raw Domain sources;
the resulting `SINGLE_RUN_SEED` remains candidate-only until the Skill Steward approves it.

## Sensitive-source model

Browser views clear private task descriptions, source-mapping confirmations and Skill
drafts when the signed-in identity changes or the session ends. Pending reads and draft
responses from the previous identity cannot restore those views. Ordinary state refreshes
under the same identity preserve unsaved edits. This browser boundary supplements the
server's authorization checks; hiding a panel does not grant or revoke backend access.

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

For Vertex/Gemini, public evidence also excludes GCP project IDs, ADC JSON, API keys, access tokens, authorization
headers, and project-scoped endpoint values. The current frozen Golden records two schema-valid Vertex
`gemini-3.8-flash` advisories, provider response IDs, request/output digests and token/latency/finish metadata; it does
not retain credentials or raw prompt/free-form response text.
`run-semifinal-demo.sh` defaults to a fresh `interactive` workspace and local Ollama candidate calls; it does not copy
frozen Quote state or historical mapping receipts. An explicit `ORGREBASE_DEMO_WORK_DIR` can reopen an existing
workspace, whose historical evidence remains identified as history. The default path makes no Vertex call.
An explicitly requested fresh Vertex run must receive a new run ID, retain its own provider response IDs and pass its
own verifier; it cannot inherit the retained Golden's evidence.

A deployment that needs raw debugging logs must use a separate access-controlled retention policy; those logs must not be copied into the public evidence directory.

The bundled controlled-local OTLP receiver intentionally accepts only structured operational
facts. Log bodies are limited to the generated `LAYER:STATUS` form, sensitive field names and
credential-shaped strings are rejected before ingestion, and rejection records retain only a
digest plus reason code. This is a public-proof minimization policy, not a general-purpose log
collector configuration.

## Retention and deletion

| Data | Reference retention rule |
|---|---|
| Synthetic benchmark and generated evidence | may be retained with the release; PolyForm Noncommercial and no real PII |
| Local Workspace SQLite scratch database | delete after evidence export in the demo; production policy is deployment-specific |
| Live model prompts/outputs | disabled in public evidence; provider-side retention is outside this project and must be reviewed |
| Experience candidate and governance receipts | may be retained with the synthetic Golden pack; `SINGLE_RUN_SEED`, zero canonical writes, and exact source-run binding must remain attached |
| Employee Task Intake run receipt | retain digest/length and exact candidate/approval/task/OAC/Formation bindings with the Workspace audit window; do not retain raw work description in State, Event, OTLP or public evidence |
| Private Task Intake work-description record | 24-hour default, configurable 0–7 days; exact task actor access; expiry-denied reads and administrator expiry sweeps; deletion tombstones survive backup recovery; see [lifecycle](PRIVATE-DATA-LIFECYCLE.md) |
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
| One run's recovery pattern is over-generalized | `SINGLE_RUN_SEED`, eight-partition gate, four-second Skill Steward approval, CANARY-only release and no current-Quote consumption | multi-run and cross-enterprise generalization remain `NOT_RUN` |
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
- that the controlled-local AgentTeams coalition is a distributed production deployment;
- that one approved experience candidate proves multi-run or cross-enterprise learning;
- that the bundled legacy AgentTeams live run upgrades the current Workspace-specific Golden coalition to production evidence.
