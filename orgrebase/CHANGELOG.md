# Changelog

## 0.5.0-beta.4 — 2026-09-30

- Fence source-sync failure updates in the same transaction as the claim check. An expired worker cannot downgrade source or coverage confirmed by its successor.
- Require the complete license attachment when loading a new Apache Skill package, including snapshots with recomputed digests. Historical package identities remain readable.
- Validate all Skill version-authoring inputs before writing planned files, so invalid input does not leave partially changed package metadata.
- Reject symlinked ancestors and changed input identities in the offline distribution builder.
- Resolve the complete lockfile dependency graph by exact identity, including optional and development groups; preserve extras and marker provenance and reject ambiguous references.
- Match development fact generation with explicit checks of that output, while keeping retained historical fact verification separate.
- Fix the operations client's default CA bundle explicitly across platforms; ambient certificate environment variables cannot replace it. Preserve explicit private CA configuration, certificate validation and hostname checks.
- Keep the pip installation requirements synchronized with the frozen runtime lock, including the current PyJWT security floor, and check this agreement in contributor CI.
- Pin GitHub Actions by immutable commit identity; run required checks on every pull request and main push. Build both components, their SBOMs and the documentation source download from the same verified commit. Sign artifact provenance and publish prereleases with immutable tags and assets.

- Generate public iterations from the canonical workspace with a fixed source policy and exact content/executable-bit parity checks; preserve older iterations instead of overlaying them.
- Emit SPDX license expressions in normal and offline distributions. New project-owned Skill versions carry Apache-2.0 and a complete license attachment; historical package bytes and restore identities remain unchanged.
- Bind explicit exact-version supplier metadata into deterministic SBOMs; unknown declarations remain explicit. Separate development fact generation from immutable historical qualification facts.

- Add `enterprise-pilot-init --template priced-quote` and package its typed initial-facts assets. The model-free browser path now supports organizational admission, confirmed task intake and a new Quote + Discount Memo workspace.
- Require structured pricing inputs to agree with the submitted source reference before registering a proposal; preserve the distinction between source approval, each deliverable owner's decision and Apply.
- Keep the task-completion heading tied to the current persisted intake receipt, expose the required role when task controls are unavailable, and align the change editor and output review in both languages.
- Support explicit private CA bundles in the bounded operations client while retaining certificate/hostname verification, redirect refusal and unchanged cursors on connection failure.
- Accept historical schema 5 for read-only operator commands and restore supported version 2/schema 5 backups into schema 6 with deletion replay, session invalidation, unresolved effects and recovery isolation preserved.

- Read completion history, archive and observability from one database snapshot, including checkpoint refresh, so concurrent writers cannot split verified audit facts across responses.
- Resolve the running version from the loaded checkout or installed package; health, OpenAPI and OTLP share it. Frozen release facts retain their original version with an explicit runtime relationship.
- Raise the PyJWT security floor to 2.14 and lock 2.15.1; retain exact dependency-audit and identity-regression results with release qualification.

- Project-owned OrgRebase and OAC source and documentation are Apache-2.0. Tag `v0.4.0` and earlier public revisions retain their historical licenses.
- Bound source and target HTTP work to whole-operation deadlines, added exact fenced source lease renewal, and bounded JWKS refresh, request bodies, logins, sessions, and security-event output.
- Added a deployment-scoped StateStore v6 dispatch ledger, bounded change worker, durable recovery continuation, and conservative notification intents with separate send, delivery, read, and unknown states.
- Added dependency-aware deterministic Formation/advisory concurrency and a native Vertex candidate V3 contract fixed to `gemini-3.8-flash`; live provider qualification remains opt-in and external.
- Added a governed Quote evidence-recovery content profile with reviewed exact-byte resources, independent behavior evaluation, persisted Principal separation, production adoption controls, and retraction/restoration.
- Added a content-addressed Quote + Discount Memo workspace profile with per-deliverable candidate evidence and owner approval, selective preserve/rebuild behavior, and atomic multi-object commit and recovery.
- Added enterprise onboarding status/preflight, CI on every pull request and main push, isolated artifact checks, and current claim-boundary documentation.
- Added controlled-local collection of ordinary change events into independently reviewed Quote recovery lessons. Same-case revisions, exact lesson-delta review, and current-source checks now have explicit records; cross-case aliasing and full deletion recovery remain open.
- Added a current-head/CAS foundation and frozen Finance experiment input revalidation across author, evaluator, and governor roles. This does not qualify a new Skill for publication or enable automatic adoption.
- Added an explicitly linked Finance successor attempt after a known terminal preview and authorized evidence recovery, with preserved prior Use records and shared policy-budget checks. Unknown outcomes still hold for reconciliation rather than being redispatched.
- Added low-sensitivity historical projections for learning content in state, history, export, and HTTP reads. New private-body storage and migration of older plaintext bundles are still open.
- Reworked the bilingual README and documentation around enterprise change, supported change paths and the bounded quote reference. The generated site binds its displayed product version and Apache-2.0 footer to source metadata; pull requests build a read-only documentation preview before `main` can deploy Pages.

The Python distribution version is `0.5.0b4`, corresponding to SemVer tag
`v0.5.0-beta.4`. This is a Beta release. The validated scope is bounded local
reference behavior; customer deployment requires separate acceptance.

## 0.4.0 — 2026-09-20 (public workspace)

The public Git repository publishes this product tree next to `oac-spec/` so one
clone supplies the default sibling OAC path. Licenses remain path-specific.
Core CI runs from `orgrebase/` on every push; full OAC and PostgreSQL validation
is `workflow_dispatch` against the in-tree `oac-spec/`.

The annotated tag `v0.4.0` is the workspace-publish snapshot. The next `main`
commits retained product version 0.4.0 while adding the core-CI import fix and
hosted documentation site. That period's `main` tree and the tag are distinct
revisions; neither qualifies this later beta candidate.

Control-plane work included in this 0.4.0 tree:

- Enforce configured authentication in local deployments and reject change
  payloads that alter admitted metadata or carry unbound source observations.
- Verify the artifact bindings behind closed change projections; retain precise
  effect failure reasons and unresolved compensation obligations across cancellation.
- Bound console history polling while retaining the active item, explain invalid
  links and source configuration failures, and make onboarding actions easier to reach.
- Regenerate release facts from retained evidence and distinguish historical
  verification from qualification of the current build.
- Add an explicit runtime source-package profile with core checks, locked AgentTeams
  sources, recursive archive scanning and credential-file exclusions.

- Replace fixed example exclusions in generated renderer contexts with records
  of the actual admitted projections; preserve real domain exclusions and stored history.
- Show original responsibility, effective delegation and recorded decisions in
  change history, with bounded snapshot reads and unchanged command authorization.
- Explain per-manifest context usage in committed change lineage without exposing
  excluded source identifiers or content.
- Add a separately verified, read-only historical OWB reference-strategy table;
  retain every baseline, ablation and hard-gate result with explicit evidence scope.

- Add a no-model public-data first run, an executable rule-Pack reuse exercise,
  and a task-oriented documentation index with explicit dependency boundaries.
- Split credential-free core CI from complete OAC/PostgreSQL validation; release
  candidates still require both checks to succeed.
- Show model advice and deterministic review side by side, support validated
  console deep links, and clarify approval-time failures.
- Correlate unclassified HTTP failures with sanitized incident logs; preserve
  existing backups and report corrupted database metadata with stable errors.

- Batch verified preview, approval and outcome reads in the existing Workspace
  snapshot; preserve per-record integrity and current authorization checks.
- Expose optional native Uvicorn concurrency and TCP backlog settings, and add
  bounded fixed-arrival measurements against the authenticated business HTTP API.
- Record public quote replay plans, monotonic timings and failed/incomplete cases;
  show independently verified before/after totals in a portable HTML report.
- Explain source coverage scope, expiry and recovery steps in the console, and
  stop counting the same review interval again during automatic Apply or a retry.
- Added a read-only AgentTeams operations projection that distinguishes native
  Formation and per-ChangeSet taskflow evidence from deterministic advisories.
- Added a restart-stable human-interrupt projection over existing persisted review
  gates: `WAITING_HUMAN -> RESUMED -> COMPLETED`.
- Moved superseded implementation worklogs, media authoring files, render outputs,
  failed runs, and cold evidence archives outside the runnable source tree under a
  content-hashed recovery manifest.

## 0.4.0 — 2026-08-26 (semifinal candidate notes)

Semifinal candidate release focused on one auditable enterprise quote workflow.
The 2026-09-20 public workspace tag is the same product version; it adds the
sibling `oac-spec/` publication layout described above.

- Added the Quote Operator operating model, RACI, metric registry, synthetic value
  benchmark, and bounded selective-Rebase cost model.
- Added a pinned AgentTeams v1.2.2 native project/task lifecycle adapter with
  candidate-only authority, reassignment fencing, portable public evidence, and
  retained-lock replay.
- Unified Quote Compose, Structured Handoff, and Launch Readiness as discoverable,
  loadable, evaluable Skill packages with fail-closed release receipts.
- Added controlled-local Source/Tool HTTP connectors, OTLP/HTTP traces/logs/metrics,
  SQLite query and retention, deterministic alerts, capacity observation,
  backup/restore evidence, and CycloneDX SBOM generation.
- Added the Spec 045 same-run parent evidence gate. Production connectors,
  distributed AgentTeams workers, same-run Approval/Apply, real enterprise value,
  HA, geographic DR, and contractual SLA remain `NOT_RUN`.

## 0.3.0 — 2026-08-16

First public source-available release of OrgRebase Workspace.

- Deterministic Workspace loop: task formation, two governed changes, restart recovery, Quote v3.
- OrgWorkBench v1.1 open-core conformance on 192 synthetic cases.
- Governed Skill candidate evaluation with `CANARY` / `QUARANTINED` publish states.
- AgentTeams static assets and transport verifier. Workspace-specific live runs remain `NOT_RUN`.
- Dual license: PolyForm Noncommercial 1.0.0, with commercial use reserved.
