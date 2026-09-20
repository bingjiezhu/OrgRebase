# Verification and evidence map

> This is an evidence-detail L1/L2 document. Start with the
> [L0 product truth](SYSTEM-MAP.md); historical run IDs, counts, and sealed Specs below do not define the current product.

This document maps every externally useful claim to source code, tests, machine evidence, and a reproduction command. A claim without one of these bindings must be presented as a design goal or `NOT_RUN`, not as an implemented result.

## Public transaction data and governed quote pricing

The [pricing demo runbook](PRICED-QUOTE-DEMO.md) provides the browser journey and offline replay commands.
The retained [UCI sample](../benchmark/public-retail-quote/v1/README.md) includes attribution, license, and pinned bytes.

| Claim | Implementation | Verification | Boundary |
|---|---|---|---|
| A rule change produces an actual successor quote with unit prices, quantities, discount, tax, and total | `workspace/pricing.py`, `workspace/execution.py`, existing Formation and Rebase | `scripts/run_public_quote_replay.py` runs the full local workflow and checks persisted amounts with a separate rational-arithmetic oracle | Historical transactions are real; organization, authority, discount and tax are controlled. Not native AgentTeams or customer integration evidence. |
| Source coverage and unsupported invoices are visible | `scripts/evaluate_public_quote_data.py`, fixed original XLSX digest | Rebuild the sample from source; check invoice inclusion/exclusion and full eligible-population pricing; read back replay artifacts | Requires the original XLSX. Full-population pricing is distinct from full-workflow replay and production load testing. |

Generate each run in a new output directory and inspect its report. Retained source data and historical PASS records do
not certify later code changes; this lane must not be merged into the frozen Golden's run identity.

## Spec 062 enterprise-adaptation evidence contract — controlled-local PASS

<!-- SPEC-062-VALIDATION-STATUS:START -->

**Current state:** `VALIDATED_CONTROLLED_LOCAL`.

The retained closed-world lane contains 20 entries. Its independent verifier imports neither OrgRebase nor the producer,
recomputes all bindings, invokes the public OAC CLI, and rejects six mutation classes. This does not promote enterprise
UAT, real connectors, or production readiness.

| Planned claim | Required implementation binding | Required machine evidence | Required independent check | Current status / boundary |
|---|---|---|---|---|
| A complete Evergreen Pack deterministically produces five candidate mappings while preserving source digests and Unknowns | Spec 062 mapping contracts and mapper | mapping set, roots, reason codes, zero-write receipt | replay plus digest-substitution and Unknown-erasure mutations | `PASS_CONTROLLED_LOCAL`; candidate mapping is not Source authority |
| OAC public boundary validates exact `OrganizationSnapshot` and `OrganizationalDemand` bytes | public CLI / installed-package invocation; no private compiler import | command/version/source fingerprint and exact input/output digests | five public CLI checks plus the retained clean dual-wheel check | `PASS`; validation is not Plan acceptance |
| Enterprise Contract Owner admits exact Source/Demand only after a server-side four-second gate | same `StateStore`; exact-owner/digest/freshness checks | review gate, approval and OAC `SourceAdmissionReceipt` | early/wrong-owner/stale/conflicting approval checks | `PASS_CONTROLLED_LOCAL`; observed wait `5323ms`; scripted identity is not enterprise IAM/UAT |
| OrgRebase proves Quote Formation compatibility without forging an OAC Plan certificate | independent parity verifier that does not import Workspace execution service | `QuoteFormationParityReceipt` with five obligations to seven attempts | parity omission and binding mutations | `PASS`; `oac_plan_produced=false`, authority is OrgRebase |
| Admitted capsule activates only an exact matching new execution | adaptation-required activation seam | capsule/profile/pack/adaptation/execution digest binding | cross-profile, changed-Pack, changed-run and stale approval checks | `PASS_CONTROLLED_LOCAL`; the frozen Golden is not retrofitted |
| Incomplete Veracier input stops at `HOLD` with no capsule or execution | same contracts and service path as the positive case | seven explicit Gaps and zero target writes | approval/activation fail-closed plus Unknown-erasure mutation | `PASS_CONTROLLED_LOCAL`; a negative fixture does not prove a second enterprise deployment |

The adaptation lane must retain its own `adaptation_run_id` and evidence root. Its entries must not be added to the frozen
Golden's 101 entries or the separate OAC synthetic reference's 51 entries. The only permitted cross-lifecycle relation is an
exact `adaptation_run_id -> capsule_digest -> execution_run_id` binding for a **new** execution.

```bash
make oac-quote-adaptation-check OAC_ROOT=../oac-spec
make workspace-oac-evolution-wheel-check OAC_ROOT=../oac-spec
```

<!-- SPEC-062-VALIDATION-STATUS:END -->

## Retained OAC-bound Golden closure — Specs 056–060, sealed by Spec 079

### Inspecting a collaboration decision

Use the current run's collaboration view to inspect intermediate work, rather than
inferring collaboration from the number of process launches. These checks answer
different questions and must keep their own evidence bindings:

| Question | Inspect | Implementation and focused verification | Limit |
|---|---|---|---|
| Why did the Reviewer request another attempt? | Reviewer A1's model advisory beside its deterministic decision, then Finance A2 and Reviewer A2 | `workspace/competition_worker.py`; `tests/workspace/test_golden_pilot_evidence.py` | An advisory may miss a missing domain. Acceptance follows the independent contract checks; it is not a majority vote between models. |
| Why did an Agent not receive some context? | Per-actor required slots and recorded exclusion reason counts in collaboration and the enterprise data journey | `workspace/context.py`, `context_residency.py`; `tests/workspace/test_context_residency.py`, `test_privacy.py` | Counts explain the recorded projection, not production isolation. The public view omits excluded source identifiers and free text; unknown reasons remain generic. Actual delivered-context equality is checked separately. |
| What did Element observe? | Published snapshot, exact task/attempt IDs and the read-back event receipt | `workspace/matrix_observation.py`; `tests/workspace/test_matrix_observation.py` | Translated role/status text leaves original codes and task IDs intact. This is one observer's snapshot, not Worker transport or approval authority. |

The historical Northstar cost counterfactual below is a separate evaluation from
the Evergreen run and the public UCI transaction replay. Its modelled percentage
must not be presented as savings measured in either of those runs.

The archived Golden execution is identified by the exact `run_id`,
public-pack digest, manifest digest, and content-addressed entry count recorded in
`evidence/release-facts.json` and recomputed from
`evidence/golden-competition/latest/pilot/verification.json`.  This document does
not duplicate those rotating release identifiers: a stale hard-coded UUID must
never be treated as current evidence.

| Current claim | Main implementation | Focused tests / verifier | Frozen evidence | Reproduction | Evidence boundary |
|---|---|---|---|---|---|
| One run binds the sealed Pack, 40 AgentTeams actions, 7 bindings, 5 Worker + 2 Reviewer processes, HTTP Tool, released quote-compose, exact-reviewed Formation and Quote v1 | `src/orgrebase/workspace/competition_run.py`, `controlled_agentteams_formation.py`, `pilot.py` | `test_golden_pilot_integration.py`, `test_golden_pilot_evidence.py`, independent stdlib verifier | `golden-run/summary.json`, `golden-run/process-receipts.json`, `manifest.json` | `python3 scripts/verify_golden_pilot_evidence.py --root evidence/golden-competition/latest/pilot` | `CONTROLLED_LOCAL_GOLDEN_COMPETITION`; pinned in-process TeamHarness with independent local processes, not distributed production |
| Finance A1 really abstains, Reviewer replans, the read-only HTTP Tool supplies exact bytes, Finance A2 and Reviewer then pass | `competition_run.py`, `competition_worker.py`, `tools.py` | Golden integration/evidence tests plus tamper negatives | `golden-run/process-inputs/`, `process-outputs/`, `prepared-formation-bundle.json` | same independent verifier | synthetic fault injection executed at runtime; Tool and Runtime canonical writes=`0` |
| The current retained Golden runs two schema-valid Reviewer advisories through Vertex `gemini-3.8-flash`; the deterministic Reviewer remains authoritative | `workspace/model_provider.py`, `competition_run.py` | Vertex-provider and Golden model/evidence tests | two Reviewer process outputs plus `golden-run/summary.json` and `manifest.json` | independent Golden verifier; every new live run must use a new run ID and its own receipts | `LIVE_MODEL`; advisory-only and model writes=`0`; this retained run is not production-provider qualification |
| Product Owner and Finance Owner each cross a four-second server gate and explicitly approve before Quote v3 reaches `2026-10-15 / EUR`; the public projections contain 16 verified digest-chained events | `workspace/pilot.py`, `service.py`, `store.py`, `api.py` | Golden pilot integration, approval/authority, restart and evidence tests | `state.json`, `quote-export.json`, `evidence-export.json`, restart receipts and `manifest.json` | run the browser journey, then the independent verifier | controlled-local Header identity; public pack deliberately excludes `workspace.sqlite3`; external IAM and human UAT remain `NOT_RUN` |
| The same run yields `IMPROVE structured-domain-handoff / SINGLE_RUN_SEED`; 8/8 passes, Skill Steward waits four seconds, approves, and a discover/load/invoke `RELEASE` dry-call succeeds | `workspace/experience.py`, `skill_packages.py`, API/service wiring | `test_governed_experience.py`, `test_golden_skill_release_projection.py`, experience verifier mutations | `state.json`, `evidence-export.json`, `manifest.json`, `verification.json` | same independent verifier; browser approval is separate from the two business approvals | `APPROVED_CANARY`; current Quote did not consume the candidate; canonical writes=`0`; multi-run generalization remains `NOT_RUN` |
| Console defaults to Simplified Chinese and switches `zh → en → zh` without refresh or loss of run/tab/stage/state | `demo/console/index.html`, `app.js`, `styles.css` | `tests/workspace/test_console_i18n.py`, console product-flow tests | UI is a projection of the same `state.json`; it is not a new authority | `uv run pytest tests/workspace/test_console_i18n.py -q` | language preference is non-authoritative and fail-soft |
| The current Golden consumes the admitted OAC activation as a Quote-v1 precondition, binds employee Task Intake to the same `run_id`, then compiles the admitted four-domain coalition into the exact AgentTeams task set | `workspace/task_intake.py`, `agentteams_execution_plan.py`, `competition_run.py`, `competition_worker.py` | Task-Intake, execution-plan and Golden integration/evidence tests; independent Golden verifier | `evidence/golden-competition/latest/pilot/evidence-export.json` plus `golden-run/summary.json`; activation digest, Formation receipt and planned/actual domains are cross-bound | `python3 scripts/verify_golden_pilot_evidence.py --root evidence/golden-competition/latest/pilot` | `VALIDATED_CONTROLLED_LOCAL`; same-run OAC-bound business path, planned=actual four domains; Agent/Tool/Skill remain candidate-only and production distribution is not claimed |
| The same Formation/Context/ExecutionPlan contract materializes a smaller Legal+Product coalition as exactly two domain tasks plus one Reviewer barrier | `workspace/formation_taskflow_probe.py`, runner and stdlib-only verifier | `test_agentteams_execution_plan.py`, `test_formation_taskflow_probe.py`; add/remove/substitute mutations | `evidence/formation-taskflow/latest/` | `python3 scripts/verify_formation_taskflow_probe.py --evidence evidence/formation-taskflow/latest --lock agentteams/teamharness-lock.json --checkout .tmp/agentteams-v1.2.2` | `CONTROLLED_LOCAL_FORMATION_COMPILED_AGENTTEAMS`; proves topology compilation, not live business admission or production execution |

Specs 056–057 remain the product/UI and released-Skill authority baselines. Specs 059–060 close the current
live-model and governed-experience increments. The following older packs remain useful scoped evidence, but none may
be spliced into the current Golden run.

The former static `evidence/oac-bound-shadow/latest` pack is a **historical mechanism record** held in the
operator-side cleanup archive, not in the release source tree. Its producer, independent verifier and mutation tests
remain useful for regenerating and testing candidate-only execution-plan/fencing semantics, but the historical pack
is not a current release chain and must not be used to upgrade or replace the same-run Golden OAC → Task Intake →
Formation evidence above.

## Retained semifinal authority chain — Specs 041–053

The semifinal wedge has one primary user, `Quote Operations Owner`; Enterprise Quote is its bounded validation slice,
not the product boundary. Its machine story has two explicit authority layers:

1. the Spec 045 parent ends at `CANDIDATE_ACCEPTED` with zero canonical writes;
2. the Spec 051 successor binds that exact parent digest and the same root
   `run_id` before scripted domain-owner approval and StateStore canonical
   writes can end at `GOVERNED_APPLIED / work:quote_acme@v3`.

The parent dependency order remains: 041 value contract → 046 Shadow
observation contract → 042 native Taskflow → 049 four-domain coalition → 044
HTTP Tool → 043/048 installed-wheel Schema-qualified Skill → 047 causal OTLP →
045 candidate pack. Spec 050 is a separate public-data mechanism lane, not part
of that enterprise run. Spec 052 binds executable Skill rollback to the parent
run but preserves `candidate_only=true / target_writes=0`. Spec 053 then
content-addresses all four sources and their claim boundaries in one inventory;
it is not a fifth execution proof. Component-level passes do not substitute for
the relevant parent, successor or aggregate verifier.

| Spec / claim | Main implementation | Focused tests / verifier | Retained evidence | Reproduction | Current evidence boundary |
|---|---|---|---|---|---|
| 041: Quote Operator operating model, Product/Legal/Finance/GTM RACI, response-time and metric contracts | `docs/ENTERPRISE-QUOTE-OPERATING-MODEL.md`, `benchmark/quote-value-v0.1/`, `configs/workspace/quote-value-metric-registry.json`, `scripts/run_quote_value_benchmark.py` | `tests/workspace/test_quote_value_benchmark.py`; `scripts/verify_quote_value_evidence.py` | `evidence/quote-value/latest/quote-value-receipt.json`, `verification.json`, `evidence-index.json` | run `uv run python scripts/run_quote_value_benchmark.py --output-dir evidence/quote-value/latest`, then `uv run python scripts/verify_quote_value_evidence.py --receipt evidence/quote-value/latest/quote-value-receipt.json --project-root .` | `VALIDATED_SYNTHETIC_AND_MODELLED`: `0/72` change and `0/32` security results compare the table-driven reference SUT with declared gold (contract consistency, not ImpactEngine accuracy or production authorization rates); `2/8` hold decisions are controlled-local; the uncalibrated cost sensitivity range is `-10.0%–59.9%`, with a `49.4%` base scenario, not observed savings or enterprise ROI |
| 042: pinned AgentTeams native project/task lifecycle and deterministic candidate admission | `src/orgrebase/workspace/native_taskflow.py`, `agentteams/teamharness-lock.json`, `scripts/run_native_taskflow_slice.py` | `tests/workspace/test_native_taskflow.py`; `scripts/verify_native_taskflow_evidence.py` | `evidence/semifinal-closure/latest/agentteams/` | invoked as a child of `make semifinal-closure-check`, or run generator/verifier explicitly; checkout replay and retained-lock replay are distinguished | `VALIDATED_CONTROLLED_LOCAL` for the in-process pinned `call_tool` path. A separate Core proposal-plane live run binds K8s pods, Matrix publication and provider execution; Workspace/Golden autonomous Matrix-inbound Worker handoff remains `NOT_RUN`. Spec 051 proves StateStore worker recovery, not a killed distributed AgentTeams worker |
| 043: three discoverable exact packages, qualification/release decision and Quote Tool binding | `src/orgrebase/workspace/skill_packages.py`, `skills/`, `configs/workspace/skill-registry.json`, `scripts/run_skill_package_lifecycle.py` | `tests/workspace/test_skill_package_lifecycle.py`; `scripts/verify_skill_package_evidence.py`; clean wheel probe | `evidence/semifinal-closure/latest/skills/` | invoked as a child of `make semifinal-closure-check` | base lifecycle retained and hardened by Spec 048; serialized receipts do not re-authorize |
| 044: loopback Source/Tool HTTP, OTLP/HTTP, SQLite query/alerts/retention, capacity, backup/restore and SBOM | `src/orgrebase/workspace/controlled_local.py`, `src/orgrebase/workspace/controlled_local_evidence.py`, `scripts/run_controlled_local_evidence.py`, `scripts/generate_sbom.py` | `tests/workspace/test_controlled_local_ops.py`, `tests/workspace/test_controlled_local_evidence.py`, `tests/test_sbom.py`; `scripts/verify_controlled_local_evidence.py` | `evidence/semifinal-closure/latest/operations/` | invoked as a child of `make semifinal-closure-check` | `VALIDATED_CONTROLLED_LOCAL_RETAINED`; real CRM/CPQ/ERP/CLM, production IAM, HA, geographic DR and contractual SLA are `NOT_RUN` |
| 045: one Source-rooted run binds native AgentTeams, four-domain coalition, HTTP Tool→installed-wheel Skill, and causal OTLP/operations | `scripts/run_semifinal_closure.py`, `scripts/verify_semifinal_closure.py`, `scripts/verify_semifinal_mutations.py`, `Makefile` | product-independent parent verifier plus five child verifiers and nine semantic mutations, including coalition, OTLP order/parent/status, Tool, authority-root, telemetry-privacy, host-path and publication-surface attacks | `evidence/semifinal-closure/latest/summary.json` and `evidence-index.json` | `uv sync --all-extras && make semifinal-closure-check` | `RETAINED_PASS / CONTROLLED_LOCAL_INTEGRATED_VERTICAL_SLICE`; this parent terminal remains candidate accepted with zero writes. Its separately bound governed successor is Spec 051 |
| 046: strict de-identified Quote-process observation input, admission classes and seven replayable metrics | `src/orgrebase/workspace/quote_observations.py`, two exported JSON Schemas, `scripts/run_quote_shadow_admission.py` | `tests/workspace/test_quote_shadow_admission.py`; `scripts/verify_quote_shadow_admission.py` and semantic negatives | `evidence/semifinal-closure/latest/quote-shadow/` | invoked as a child of `make semifinal-closure-check`, or run generator/verifier explicitly | `VALIDATED_SYNTHETIC_CONTRACT`; real-enterprise Shadow pilot and observed business value remain `NOT_RUN` |
| 047: exact `SOURCE→AGENTTEAMS→TOOL→SKILL→TERMINAL` order, span parent chain and layer-status binding | controlled-local telemetry projector and `scripts/run_controlled_local_evidence.py` | controlled-local operations tests; operations verifier plus three OTLP mutations in the parent mutation suite | `evidence/semifinal-closure/latest/operations/` | invoked as a child of `make semifinal-closure-check` | `VALIDATED_CONTROLLED_LOCAL`; deterministic synthetic timing is not production propagation, latency or SLA evidence |
| 048: package-local input/output Schema bytes, no-network resolution, installed-wheel load/invoke and fresh requalification | `src/orgrebase/workspace/skill_packages.py`, `skills/`, `scripts/run_skill_package_lifecycle.py` | `tests/workspace/test_skill_package_lifecycle.py`; independent lifecycle verifier | `evidence/semifinal-closure/latest/skills/` including wheel/runtime and requalification receipts | invoked as a child of `make semifinal-closure-check` | `VALIDATED_FOCUSED_INSTALLED_WHEEL`; external trust and statistical generalization remain `NOT_RUN`. Executable direct-predecessor restore is separately validated by Spec 052 |
| 049: exactly one admitted Product/Legal/Finance/GTM result forms a content-addressed `CoalitionResultBinding` required by Quote Compose | `src/orgrebase/workspace/coalition.py`, `schemas/workspace-coalition-result-binding.schema.json`, semifinal runner/verifier | `tests/workspace/test_coalition_binding.py`; coalition substitution mutation | `evidence/semifinal-closure/latest/agentteams/coalition-result-binding.json` and bound Skill input/result | invoked as part of `make semifinal-closure-check` | `VALIDATED_CONTROLLED_LOCAL`; four controlled candidate roots are not four independent model results, business truth, or write authority |
| 050: pinned public OCEL bridge, object-centric projection, injected causal overlay and typed-impact comparison | `scripts/data_adapters/`, `scripts/run_public_process_bridge.py`, `configs/workspace/public-process-mappings/`, `benchmark/quote-value-v0.3-public-process/` | `tests/workspace/test_public_process_bridge.py`; product-independent `scripts/verify_public_process_bridge.py` plus semantic negatives | `evidence/public-process/latest/public-process-bridge-receipt.json` | `make public-process-bridge-check` or the complete `make semifinal-mvp-check` | `PUBLIC_SOURCE_DERIVED_SYNTHETIC / MECHANISM_VALIDATION_ONLY`; causal overlay is experimenter-injected, all seven enterprise-value metrics remain `NOT_RUN` |
| 051: exact parent promotion, two owner approvals, canonical Quote v3 writes and killed-process recovery | `src/orgrebase/workspace/governed_evolution.py`, `src/orgrebase/workspace/runtime_journal.py`, `scripts/run_governed_semifinal_apply.py` | `tests/workspace/test_runtime_journal.py`, governed-evolution tests; independent stdlib JSON/SQLite verifier and five semantic mutations | `evidence/semifinal-governed/latest/summary.json`, promotion/approval/apply receipts and runtime journal | `make semifinal-governed-check` after refreshing the exact parent, or `make semifinal-mvp-check` | `CONTROLLED_LOCAL_GOVERNED_CANONICAL_WRITE_WITH_SIGKILL_RECOVERY`; approvals are scripted, recovery is local SQLite WAL, production readiness is false |
| 052: exact retained 1.3.0 Skill direct-predecessor restoration and invocation | `src/orgrebase/workspace/skill_rollback.py`, frozen predecessor resources, `scripts/run_skill_predecessor_rollback.py` | `tests/workspace/test_skill_predecessor_rollback.py`; independent verifier, content-root-partitioned independent-runner exact retry and four negative probes | `evidence/skill-predecessor-rollback/latest/summary.json` and execution receipt | `make skill-predecessor-rollback-check` after refreshing the exact parent, or `make semifinal-mvp-check` | `CONTROLLED_LOCAL_EXECUTABLE_DIRECT_PREDECESSOR_ROLLBACK`; append-only release-head event, `candidate_only=true`, zero business writes, no production rollout claim |
| 053: unified content-addressed inventory and evaluator recommendation matrix | `scripts/build_semifinal_mvp_manifest.py` | `tests/workspace/test_semifinal_mvp_manifest.py`; stdlib-only `scripts/verify_semifinal_mvp_manifest.py` with authority/run/source-digest mutations | `evidence/semifinal-mvp/latest/summary.json` and `verification.json` | final stage of `make semifinal-mvp-check` | `SEMIFINAL_MVP_CONTROLLED_LOCAL_NOT_PRODUCTION`; 4 source packs, 28 rows = 19 scoped `VALIDATED` + 9 `NOT_RUN`, `production_ready=false`. Manifest PASS means inventory integrity, not production readiness |

### Retained-proof promotion rule

Spec 045 becomes a retained machine result only when all of the following are
true at the same time:

1. `evidence/semifinal-closure/latest/summary.json` and `evidence-index.json` exist;
2. the summary says `CANDIDATE_ACCEPTED`, `canonical_target_writes=0`, and
   `production_readiness=false`;
3. `scripts/verify_semifinal_closure.py` independently returns `PASS` against
   the exact pinned AgentTeams checkout;
4. Source, AgentTeams, the exact four-domain coalition, Tool, installed-wheel
   Schema-qualified Skill and causal operations receipts share the same root
   `run_id`; Tool precedes Skill, and the Skill input binds both the Tool result
   and all four accepted domain result roots;
5. the parent pack itself contains no Approval/Apply and keeps target writes at
   zero; live distributed workers, real enterprise value, external enterprise
   connectors and production HA/DR/SLA remain `NOT_RUN`.

All five conditions currently hold for the named retained pack. This promotion
does not rewrite the parent terminal merely because a separate governed
successor now exists.

### Governed-successor promotion rule

Spec 051 becomes a retained governed result only when all of the following are
true at the same time:

1. its summary binds the current Spec 045 parent summary digest and the same
   root `run_id`;
2. the parent still says `CANDIDATE_ACCEPTED / canonical_target_writes=0`;
3. two exact domain-owner commands bind the expected preview, candidate and
   successor digests before StateStore writes;
4. the final state is `GOVERNED_APPLIED / work:quote_acme@v3` and the six
   canonical writes reconcile to initial, launch and currency Quote+graph pairs;
5. the durable journal independently replays both real `SIGKILL` windows as
   `RETRY_INTENT_RECONCILED` and `ADOPT_COMMITTED`;
6. `scripts/verify_governed_semifinal_apply.py` and all five rehashed semantic
   mutations pass.

These conditions hold for `evidence/semifinal-governed/latest/`. They prove
scripted exact-owner governance and local SQLite WAL recovery, not external
human validation, distributed recovery, enterprise connectors or production
readiness.

## Workspace claims

| Claim | Main implementation | Tests | Evidence artifact | Reproduction / verifier | Current evidence boundary |
|---|---|---|---|---|---|
| A real task forms Quote v1 instead of loading a pre-existing quote | `workspace/formation.py`, `task_agent.py`, `templates.py` | `test_formation.py`, `test_repeatability.py` | `formation/task-receipt.json`, `formation/quote-v1.json` | `make workspace-formation-check` | `LOCAL_DETERMINISTIC` |
| Quote task selects Product + Legal + Finance + GTM, while public summary selects Product + GTM | `workspace/planner.py` | `test_planner_and_governance.py`, `test_formation.py` | `formation/coalition-plan.json` | `make workspace-formation-check` | `LOCAL_DETERMINISTIC` |
| Agents cannot invent authority or self-admit Claims | `workspace/domain_agents.py`, `admission.py` | `test_planner_and_governance.py`, `test_privacy.py` | `formation/admission-decision-*.json` | `make workspace-formation-check` | `LOCAL_DETERMINISTIC` |
| Restricted Legal source is not disclosed to GTM/renderer | `workspace/context.py`, `domain_agents.py`, `evidence.py` | `test_privacy.py`, `test_failures.py` | `formation/task-context.json`, evidence-index canary scan | `make workspace-check` | synthetic restricted source; no real contract |
| Dependencies reflect values actually resolved during rendering | `workspace/execution.py` | `test_formation.py`, `test_failures.py` | `formation/work-trace.json`, `trace-coverage.json` | `make workspace-formation-check` | `LOCAL_DETERMINISTIC` |
| Quote v1 has a complete runtime dependency manifest and graph snapshot | `workspace/execution.py`, `graph.py`, `formation.py` | `test_formation.py`, `test_contracts_and_store.py` | runtime manifest, graph snapshot v1, TaskReceipt | `make workspace-formation-check` | `LOCAL_DETERMINISTIC` |
| Task Intake `prepare → admit → run` and the resulting Workspace execution share one idempotent bound read-only tool call | `task_intake.py`, `tools.py`, `workspace/service.py`, `workspace/evidence.py` | Task-Intake, ProductPath retry/restart/export, tool-trace and evidence-tamper tests | ProductPath raw observations plus product State/evidence download and called-event binding | `make workspace-product-path-blackbox-check` | candidate/confirmation/run share `run_id` and workspace nonce; direct Form returns `410 / WORKSPACE_TASK_INTAKE_REQUIRED`; zero pre-formation target writes |
| No complete manifest means no bounded-unaffected conclusion | existing `impact.py`, Workspace snapshot bridge | benchmark/legacy impact tests, `test_benchmark.py` | Preview certificates and OWB results | `make workspace-benchmark-check` | closed declared boundary only |
| Impact classifications are independently recomputable | `certificates.py`, `impact.py` | certificate/core tests, Workspace rebase tests | launch/currency Preview + VMRC | `make workspace-rebase-check` | same deterministic algorithm; no global causal claim |
| VMRC contains exactly the necessary effects | `certificates.py` | mutation tests in Workspace/core suites | launch/currency minimal-rebase-certificate | `make workspace-rebase-check` | exact locked target scope |
| Quote v2 changes launch date but preserves unrelated fields | `workspace/rebuild.py` | `test_rebase.py`, `test_failures.py` | `rebase/launch-quote.json`, Workspace receipt | `make workspace-rebase-check` | `LOCAL_DETERMINISTIC` |
| Successor Quote and graph promote atomically | `workspace/rebuild.py`, `store.py` | fault injection and repeatability tests | launch/currency graph pointer and Workspace receipts | `make workspace-rebase-check` | local SQLite transaction |
| Restarted process handles a second currency change | `workspace/service.py`, `store.py` | `test_repeatability.py`, `test_rebase_repeatability.py` | final Quote and graph pointer | `make workspace-repeatability-check` | local persistent store |
| The exact Spec 045 candidate is promoted under the same root run and only StateStore performs canonical writes | `workspace/governed_evolution.py`, `scripts/run_governed_semifinal_apply.py` | governed-evolution and runtime-journal tests; independent governed verifier and five mutations | `evidence/semifinal-governed/latest/`, including parent binding, two approvals and apply receipts | `make semifinal-mvp-check` | `CONTROLLED_LOCAL_GOVERNED_CANONICAL_WRITE_WITH_SIGKILL_RECOVERY`; approvals are controlled scripted commands, external human validation is `NOT_RUN` |
| A fresh process resumes after two deliberate worker `SIGKILL` windows without duplicate canonical effects | `workspace/runtime_journal.py`, governed worker/runner | `tests/workspace/test_runtime_journal.py`; verifier reconstructs SQLite state and receipts | runtime journal, process-recovery receipt, final Quote v3 | `make semifinal-governed-check` against the exact current parent | local SQLite WAL + fencing + idempotent retry/adoption; network partition, node/disk loss, HA/DR and general external-effect exactly-once remain `NOT_RUN` |
| One Web Console completes the explicit staged Quote v1→v3 journey, same-chain read-only tool receipt, and two downloads across a real server restart | `api.py`, `workspace/service.py`, `demo/console/` | `test_product_closure.py`, `test_console_product_flow.py`, API tests | `evidence/product-closure/2026-08-25/final-browser-acceptance.json`; immutable 2026-08-24 screenshots/downloads | follow the product-closure evidence reproduction shape | `LOCAL_REAL_BROWSER_CONTROLLED`; two fresh processes, same SQLite file; synthetic profile |
| Wrong owner/digest, retired one-shot routes, and concurrent State read fail safely | `api.py`, `workspace/service.py` | product-closure authority/digest negatives and `test_state_waits_for_an_inflight_approval_transaction` | 2026-08-24 intentional `403`/`409`; 2026-08-25 two `410` responses with identical state digest | `uv run pytest tests/workspace -q` | local demo identities; no production authentication claim |
| Built-wheel public product path passes an independent black-box gate | public HTTP API and packaged wheel; evaluator imports no product code | `ProductPath-v0.3-task-intake-bound`: 12 cases, forced-overlap concurrency, 11 result/Source/Task-Intake/Approval-gate mutations, five evaluator-integrity attacks, two gate-boundary attacks, five Source-root and 5/5 Runtime-projection commitments | report + retained wheel/raw observations/source-artifact manifest | `make workspace-product-path-blackbox-check` | `12/12`, `11/11`, `5/5`, `2/2`; PP-001 has 12 events; Task-Intake and four-second review-gate scopes are explicit; direct Form is rejected with `410`; synthetic Northstar/Acme, zero external participants, no enterprise generalization claim |
| Exact Skill candidate bytes/digest are evaluated | `workspace/skill_foundry.py` | `test_skill_foundry.py` | `skill/candidate.json`, `skill/evaluation-receipt.json` | `make workspace-skill-check` | fixed action table + task/trace refs + 16 constructed cases; governance scaffold only, not trajectory induction |
| Benchmark passes hard gates, baselines, and ablations | parallel `ReferenceWorkspaceBenchmarkSUT`, `workspace/benchmark.py`, OWB assets | `test_benchmark.py` | `evaluation/owb-results.json` | `make workspace-benchmark-check` | 192 synthetic mechanism/contract cases; not product superiority, production accuracy, user value, or ROI |
| Evidence pack is content-addressed and privacy-canary scanned | `workspace/evidence.py` | `test_evidence.py` | `evidence-index.json` and 41 bound entries | `make workspace-evidence-check` | local deterministic artifacts |

## AgentTeams and user validation claims

| Claim | Code / assets | Verifier | Current status | Required evidence to upgrade status |
|---|---|---|---|---|
| Registered Workspace Worker catalog and identities are well formed | `agentteams/workspace/`, `workspace/transport.py` | `make workspace-agentteams-static-check` | `PASS_STATIC` | the catalog is a candidate pool; the per-run task set is selected and compiled separately |
| Transport rejects wrong worker/domain/task/run/nonce/artifact/Skill/provider bindings | `workspace/transport.py`, `live_evidence.py` | `make workspace-agentteams-conformance-check` | tested locally | negative conformance is not a live run |
| Pinned upstream AgentTeams native projectflow/taskflow actions execute through `call_tool`, including cancel/reassign and stale-attempt rejection | `workspace/native_taskflow.py`, source lock and runner/verifier | Spec 042 focused tests and independent verifier | `VALIDATED_CONTROLLED_LOCAL`; retained under the Spec 045 parent | does not prove independent Worker consumption, Matrix handoff, provider/model execution or canonical business writes |
| Product/Legal/Finance/GTM ran together in the current Golden controlled-local AgentTeams path | `competition_run.py`, pinned TeamHarness lock, process bridge | independent Golden verifier plus integration/evidence tests | `VALIDATED_CONTROLLED_LOCAL`: 40 actions, 7 bindings, 5 Worker + 2 Reviewer processes under the exact frozen run | no upgrade needed for the scoped local claim; this does not prove distributed production |
| Formation can select a strict subset and make it the actual pinned AgentTeams topology | `agentteams_execution_plan.py`, `formation_taskflow_probe.py` | independent verifier plus task-set mutations | `VALIDATED_CONTROLLED_LOCAL`: Legal+Product only, one Reviewer, 20 actions, `topology_match=true` | external distributed Worker consumption and enterprise value remain `NOT_RUN` |
| Product/Legal/Finance/GTM run as autonomous distributed production AgentTeams Workers | live verifier and collection scripts | `REQUIRE_LIVE=1 make workspace-agentteams-check` | `NOT_RUN` for the current Golden claim | production Worker/Team exports, authenticated transport, exact candidate/Skill/model bindings, failure recovery and one run/nonce/time window |
| Real users understand and want the workflow | `workspace/user_validation.py`, CLI/API and protocol docs | `make workspace-user-validation-check` | `NOT_RUN` | at least five consented, redacted external participant records meeting published thresholds |

The repository also contains a separate five-Agent `LIVE_AGENTTEAMS` evidence pack for the Core change-advisory slice.
That evidence is preserved under `evidence/agentteams/` and `evidence/release-facts.json`; it cannot be spliced into the
current Golden pack and does not upgrade its four-Domain controlled-local coalition to distributed production evidence.

The retained Core run at
`evidence/agentteams/fresh-live/2026-08-25-561171ed039b/README.md` is available in
the full workspace; the runtime source distribution omits that historical evidence tree. It
includes Product, Legal, Finance and GTM proposals plus a Skill curator output. Those
roles belong to that historical proposal-plane run. The Vertex Golden and the local
Ollama rehearsal have their own run IDs, providers and verifiers; adding their role
counts together would not prove one larger collaborating team. Use the evidence
package's own verifier and manifest, rather than a verifier for another package shape.

Fixed action sequences in native-taskflow probes and replay expectations are test
oracles, not evidence that a fresh task made a dynamic decision. Conversely, a
deterministic acceptance decision can legitimately override a model advisory. Skill
evaluation may use fixture logical timestamps; those timestamps are not elapsed
production latency or a reason to rewrite a historical release record. The
[Skill list](SKILL-LIST.md) describes which packages perform bounded contract checks
and which generate a typed business candidate.

## Model and tool verification

| Surface | Contract / implementation | Tests | Evidence statement |
|---|---|---|---|
| Current retained Golden structured advisory | `workspace/models.py`, `VertexAIStructuredProvider`, `competition_run.py` | Vertex-provider and Golden evidence tests | exact retained run records two schema-valid `gemini-3.8-flash` advisories under `LIVE_MODEL`; deterministic Reviewer authoritative; zero writes |
| Optional fresh Vertex structured advisory | `VertexAIStructuredProvider` | `test_vertex_model_provider.py`, Golden model/evidence tests | a fresh bounded `gemini-3.7-flash` or `gemini-3.8-flash` run requires external credentials, a new run ID and provider response IDs; it cannot inherit the retained Golden |
| Generic structured model request/receipt | `workspace/models.py`, `model_provider.py` | `test_model_provider.py` | live status requires provider request ID; missing credentials is `NOT_RUN` |
| Domain transport | `workspace/ports.py`, `transport.py` | `test_transport.py`, `test_model_and_transport.py` | target writes fixed to zero; candidates revalidated by control plane |
| Dependency evidence tool | `tools.py`, `workspace/service.py`, `docs/TOOL-CONTRACT.md` | tool/core and Workspace tool-trace tests | read-only, exact graph revision, same-run receipt + called event + audit event |
| Git artifact tool | `git_tool.py` | `test_git_tool.py` | real local commit/revert evidence under `LOCAL_REAL_TOOL` |
| Pydantic/JSON Schema contracts | `domain.py`, `workspace/models.py`, schema exporter | contract/store tests | `scripts/export_contract_schemas.py --check` detects drift |
| Privacy-safe observability | `observability.py`, `workspace/evidence.py` | observability/evidence/privacy tests | no prompts, restricted source, or privacy canary in public evidence |

## One-command verification and retained evidence / 一键验证与保留证据

Start the current fresh OAC-gated product journey without a new cloud call:

```bash
./run-semifinal-demo.sh
```

The default `interactive` entry creates a fresh working database and derives the current OAC draft from the enterprise
pack. It keeps Quote v1 blocked behind the four-second contract-owner gate, then runs OAC-bound Task Intake and
AgentTeams business formation with local Ollama candidates. It does not import historical mapping receipts, mutate
the retained Golden, or make a new Vertex call.

Verify the current retained Golden independently:

```bash
python3 scripts/verify_golden_pilot_evidence.py \
  --root evidence/golden-competition/latest/pilot
```

The accepted result is the exact release tuple recomputed by that verifier and
published under `current_semifinal_delivery.golden_quote_delivery` in
`evidence/release-facts.json`: `run_id`, entry count, causal status, experience
status, public-pack digest, and manifest digest must all come from the same
retained evidence generation.  Any copied UUID or digest outside that tuple is
historical unless the current release facts bind it.

Recreate the live Vertex path with credentials kept outside the repository:

```bash
gcloud auth application-default login
export GOOGLE_CLOUD_PROJECT="your-project-id"
uv sync --locked --all-extras
./run-enterprise-pilot.sh \
  --model-provider vertex-ai \
  --vertex-project "$GOOGLE_CLOUD_PROJECT"
```

A fresh run has a new run ID and must pass its own verifier. Running `./run-enterprise-pilot.sh` without
`--model-provider` selects an offline Ollama run, but that new run cannot inherit the retained Golden's identity or
evidence. A fresh Vertex run is likewise an independent run with its own provider response IDs; neither path retrofits
the retained local-Ollama Golden.

Complete retained Specs 041–053 controlled-local semifinal evidence:

```bash
uv sync --all-extras
make semifinal-mvp-check
```

The target runs in causal order:

1. refresh and independently verify the zero-write Spec 045 candidate parent,
   including all nine semantic mutations;
2. replay the separate public OCEL mechanism lane offline;
3. restore and invoke the exact Skill direct predecessor against that parent,
   including four fail-closed probes;
4. bind the exact refreshed parent to the governed successor, perform scripted
   owner approvals and canonical Quote v3 writes, recover two real `SIGKILL`
   windows, and reject five governed mutations.
5. build and independently verify the unified Spec 053 manifest over the exact
   four frozen sources, 28 recommendation rows and all explicit `NOT_RUN`
   promotion blockers.

Read the `latest/` directories only after this serial target finishes. During a
refresh, a downstream pack can temporarily still bind the previous parent
digest; such an intermediate state is not a retained result. A failed generator
or verifier cannot be counted as PASS. The frozen packs named in this document
all passed their independent verifiers; future runs must re-establish that fact
rather than trusting prose.

Candidate-only review remains available as:

```bash
make semifinal-closure-check
```

OAC governed-evolution substrate and preliminary Quote non-regression:

```bash
make workspace-oac-evolution-check OAC_ROOT=/absolute/path/to/oac-spec
```

Focused Workspace regression review:

```bash
make workspace-check
make workspace-review-readiness-check
make workspace-product-path-blackbox-check
```

Full release review, including Core ProofPack, coverage, compile, and offline distributions:

```bash
make workspace-release-check
```

Air-gapped environments with the Python dependencies already installed may use:

```bash
PYTHONPATH=src python -m pytest -q
PYTHONPATH=src python scripts/workspace_evidence.py --verify evidence/workspace/latest
PYTHONPATH=src python scripts/verify_review_readiness.py
```

## Evidence-class interpretation

| Evidence class | Meaning |
|---|---|
| `CONTROLLED_LOCAL_GOLDEN_COMPETITION` | The exact sealed synthetic Pack, OAC activation, Task Intake, pinned in-process AgentTeams control plane, independent local Worker/Reviewer processes, Tool, released Skill, deterministic writes, human gates and evidence share one verified run; not distributed or production |
| `LIVE_VERTEX_MODEL` | Vertex AI returned a provider response ID and schema-valid structured result; in this project it is advisory-only and never approval or canonical-write authority |
| `APPROVED_CANARY / SINGLE_RUN_SEED` | A source-bound experience candidate passed eight partitions and a Skill Steward gate, then completed a controlled release dry-call; it is not multi-run or cross-enterprise generalization evidence |
| `VALIDATED_SYNTHETIC_AND_MODELLED` | Synthetic gold, controlled-local observations and declared counterfactual cost assumptions were independently replayed; it is not enterprise-observed value |
| `MODELLED_COUNTERFACTUAL` | A dimensionless result from a versioned cost model and declared scenarios; not money, time saved, a confidence interval or ROI |
| `OBSERVED_LOCAL_PRODUCT` | A count or timing observed in the local synthetic product path; not an enterprise process baseline |
| `CONTROLLED_LOCAL_NATIVE_TASKFLOW` | Exact pinned upstream native actions ran through the controlled-local adapter; not a distributed AgentTeams Worker handoff |
| `CONTROLLED_LOCAL_INSTALLED_WHEEL_SCHEMA_AND_REQUALIFICATION` | Exact Skill, contract, program and Schema bytes were loaded from an isolated installed wheel, validated, qualified, invoked and freshly requalified; release authority remains process-local and external trust/generalization are `NOT_RUN` |
| `CONTROLLED_LOCAL_REAL_HTTP` | A real loopback TCP/HTTP process boundary was crossed with synthetic data; not an external enterprise connector |
| `CONTROLLED_LOCAL_INTEGRATED_VERTICAL_SLICE` | Source, native AgentTeams, exact four-domain coalition, HTTP Tool→installed-wheel Schema-qualified Skill and causal OTLP share one verified root run and end at `CANDIDATE_ACCEPTED` with zero canonical writes; usable only for an existing independently verified retained Spec 045 pack |
| `PUBLIC_SOURCE_DERIVED_SYNTHETIC` | Exact pinned public-simulation rows plus deterministic projection and experimenter-injected causal overlay were replayed offline; mechanism validation only, never enterprise Shadow or ROI |
| `CONTROLLED_LOCAL_EXECUTABLE_DIRECT_PREDECESSOR_ROLLBACK` | Exact retained predecessor bytes became the append-only Skill release head and the old restricted program was invoked; candidate-only, zero business writes, not production rollout |
| `CONTROLLED_LOCAL_GOVERNED_CANONICAL_WRITE_WITH_SIGKILL_RECOVERY` | An exact candidate parent was promoted under the same root run by two scripted owner commands; StateStore wrote Quote v3 and local SQLite WAL recovered two real killed-process windows; not external-human, distributed, enterprise or production evidence |
| `SEMIFINAL_MVP_CONTROLLED_LOCAL_NOT_PRODUCTION` | Four exact source packs and 28 evaluator-facing rows are content-addressed with maturity and claim boundaries; manifest PASS is inventory integrity, while `production_ready=false` and nine `NOT_RUN` blockers remain explicit |
| `LOCAL_DETERMINISTIC` | Executed locally with deterministic inputs and machine checks |
| `LOCAL_REAL_TOOL` | A real bounded local tool side effect occurred, such as Git commit/revert |
| `SYNTHETIC_FIXTURE` | Result is based on generated fictional business data |
| `PASS_STATIC` | Configuration/schema/source-lock checks passed; no live runtime claim |
| `LOCAL_OLLAMA_MODEL` | A loopback Ollama request returned a schema-valid result from an exact observed local model-manifest digest. This remains the local interactive-run evidence class; it is not an external-provider claim, and it is not the current retained Golden. |
| `LIVE_MODEL` | External model request returned a provider request ID and valid structured result |
| `LIVE_AGENTTEAMS` | One fully correlated external AgentTeams run satisfied the fail-closed verifier |
| `USER_STUDY` | Consented, redacted external participant records were supplied |
| `NOT_RUN` | Required external prerequisites were absent; the capability was not executed |

## Known limits

The current Spec 079-sealed Golden closure still records the following as `NOT_RUN`: real enterprise data,
connectors and ROI; distributed production AgentTeams; killed-process durable resume inside the exact Golden run;
realized savings against a semantic full-rebuild executor; external IAM and human UAT; production SLA, HA,
geographic DR and multi-tenancy; and multi-run/cross-enterprise experience generalization. Vertex model execution,
an approved CANARY experience candidate, or a 101-entry verifier `PASS` does not close any of those boundaries.

The evidence proves behavior within the declared synthetic task family and
committed graph universe. It does not prove that a real enterprise has no
missing dependencies, that arbitrary free-form text has exact causal
attribution, that a deployment achieves ROI, or that the Workspace-specific
AgentTeams/user-study milestones have already occurred. Quote-cycle elapsed
time, policy-confirmation active work, first-pass rework, real omission rate,
actual savings and enterprise ROI remain `NOT_RUN`.

The Spec 045 parent still ends at candidate acceptance with zero writes. Spec
051 now connects that exact parent and root run to two scripted owner approvals,
canonical Quote v3 writes and two local SQLite-WAL `SIGKILL` recoveries. It does
not prove external-human approval, a killed distributed AgentTeams worker,
general exactly-once behavior for external side effects, business compensation,
CRM/CPQ/ERP/CLM connectivity, tenant isolation, high availability,
disaster-site failover or contractual SLA.

Spec 050 proves typed-dependency and `UNKNOWN` mechanics only after an
experimenter supplies the causal overlay; it does not prove causal discovery or
enterprise value. Spec 052 proves execution of one exact direct predecessor on
one restricted input and keeps business target writes at zero; it does not
prove canonical Quote rollback, multi-node Skill Registry consensus, production
canary traffic or cross-version quality.

The black-box evaluator also records a P1 digest-contract migration debt:
Quote export matches RFC 8785, while Evidence export's legacy integral-float
spelling does not. Existing evidence remains verified with an independent
implementation of the declared product contract; no silent historical rewrite
is permitted.
