# Review readiness

> Review begins with the [L0 product truth](SYSTEM-MAP.md). This checklist is supporting evidence;
> it cannot promote a historical run, controlled synthetic slice, or passing test into current product acceptance.

Companion to `configs/workspace/review-readiness.json` and `scripts/verify_review_readiness.py`.

`make check` and `make test` run `make release-hygiene` before the full test suite. This inexpensive check reuses the review-readiness metadata rule and fails on `.DS_Store` or `__MACOSX` anywhere in the checked tree. It only prints its result; it does not create or rewrite published evidence. Run `python scripts/verify_review_readiness.py --hygiene-only --root PATH` to check another tree.

## Current result

| Review question | Result | What is proven | Scope boundary |
|---|---|---|---|
| Is the real user and application scenario clear? | **Yes for the documented synthetic reference slices** | The preliminary Quote journey and the current Véracier supplier-change journey name the enterprise facts, request, accountable authorities, evidence obligations, outcome criteria, rollback and external-validation protocol | Buyer demand, usefulness and enterprise fit are not externally validated; user study remains `NOT_RUN` |
| Do Agents form a complete task loop? | **Yes for one synthetic controlled OAC reference closure** | Source → Demand → two valid Plans → Runtime Execution → independent Outcome → topology-independent candidate → exact-byte scripted governance → immutable successor → rollback | This is not a live enterprise Agent team. Workspace-specific K8s/Matrix formation and a real human governance review remain `NOT_RUN` |
| Do core functions have verifiable material? | **Yes** | Exported schemas, packaged mechanism/contract conformance, the separate 20-entry OAC adaptation pack, 51-artifact/13-event OAC reference pack, the current 101-entry OAC-bound Golden verification, retained counterexamples, dual-wheel replay, mutation attacks, receipts and certificates | Synthetic/local results are not full-stack enterprise cases, production ROI, global graph completeness or cross-enterprise generalization; test counts are intentionally not frozen in prose |
| Is governed evolution more than a successful execution label? | **Yes for the controlled reference contract** | Both Plans execute through one interface; two complete runs receive `Outcome ACCEPT`, while one structurally complete run receives `Outcome REJECT`; only accepted Outcomes support the candidate, and `r1 → r2 → r1` remains reversible | Observations are controlled synthetic facts, governance is scripted, `human_review=NOT_RUN`, `real_enterprise=NOT_RUN`, `production_ready=false` |
| Does the submitted product path work from packaged bytes? | **Yes for the frozen local profile** | `ProductPath-v0.3-task-intake-bound` builds/unpacks the wheel, launches 15 Uvicorn processes across isolated case databases, performs three real restarts, passes 12/12 HTTP cases, kills 11/11 result/Source/Task-Intake/Approval-gate mutations, rejects 5/5 evaluator-integrity and 2/2 gate-boundary attacks, independently verifies five Source component roots and 5/5 Runtime projections, binds a 12-event primary journey, forces a real four-second Approval/State overlap, and proves that direct Form now returns `410 / WORKSPACE_TASK_INTAKE_REQUIRED` without state drift | Synthetic Northstar/Acme only; zero external participants; controlled-local Header identity, not enterprise IAM; no arbitrary-enterprise, production auth, scale or ROI claim |
| Are model, Agent architecture, and tool interfaces clear? | **Yes** | Structured model requests/receipts, bounded repair, Agent authority matrix, Domain read contracts, reference monitor, AgentTeams transport, APIs, and tool ceilings | Live model/provider behavior is optional and needs provider request IDs |
| Are data authorization, licensing, and privacy risks addressed? | **Yes for the bundled synthetic release** | Asset/license inventory, minimum-disclosure flow, canaries, logging/retention policy, external-data gate, consent protocol, and risk register | Production connector retention, multi-tenant isolation, provider terms, DLP, and general inference privacy require deployment-specific work |

## Reproduction

```bash
make semifinal-mvp-check
make workspace-oac-evolution-check OAC_ROOT=../oac-spec
make workspace-check
make workspace-review-readiness-check
make workspace-product-path-blackbox-check

# 仅在外层正式提交根（包含 PPT/PDF/源码 ZIP/视频）运行：
python3 -B verify-submission.py --deep
```

原先该命令针对含 PPT、PDF、源码 ZIP 和视频的外层提交根。现状：本仓库是 GitHub 产品根，不含那些决赛附件；不要在 clone 下来的产品仓库根运行该命令。决赛材料在仓库外单独目录。

The first command closes the current code and evidence graph without assuming that media files live inside
the source repository. The source snapshot deliberately excludes `submission/`; therefore
`make goai-semifinal-check` is a developer-only aggregate gate for a separately assembled checkout, not a
source-snapshot reproduction command. The outer `verify-submission.py --deep` is the sole authority for the
formal PPT/PDF/source-ZIP/video package. The fourth
command writes `evidence/workspace/latest/review-readiness.json` and fails if a required
document/code/test/evidence path disappears, if local evidence drifts from the published state, if
zero-participant user validation is mislabeled, if static/live AgentTeams evidence is mislabeled, if benchmark
license/PII facts drift, or if macOS metadata re-enters the source tree.

The current product path starts work only through the three-step Task Intake protocol:
`POST /api/workspace/task-intake/prepare` creates a zero-write candidate, `admit` records the matching
employee confirmation, and `run` forms Quote v1 under the same `run_id` and workspace nonce. Direct
`POST /api/workspace/form` is a retired bypass and must return
`410 / WORKSPACE_TASK_INTAKE_REQUIRED` without state drift. The 2026-08-25 product-closure record predates
this v0.3 migration and is retained only as historical v0.2 browser evidence: explicit Product and Finance
approval clicks cross a real server restart on the same SQLite file, both downloads return `200`, and the
browser console records zero errors. The structured historical record remains at
`evidence/product-closure/2026-08-25/README.md` in the full workspace; the runtime
source distribution deliberately excludes this historical browser-evidence tree.
The immutable 2026-08-24 record separately preserves intentional wrong-owner `403`, wrong-digest `409`,
screenshots, downloads, and the concurrent State-read regression.

## Current external milestones

```text
Workspace local deterministic E2E: PASS
OWB v1.1 open-core conformance: PASS on reference implementation
Governed Skill candidate: CANARY on fixed table + 16 constructed cases; NOT trajectory-learned
OAC governed-evolution reference closure: PASS (synthetic controlled; 51 artifacts / 13 events)
OAC enterprise-adaptation preflight: PASS (20 entries; READY/HOLD; 6/6 mutations; 5323ms review; zero writes)
Plural Plan evidence: BASE + SPLIT PASS with distinct topologies and one obligation contract
Execution / Outcome separation: PASS (ACCEPT/COMPLETED/REJECT retained)
Governed Source transition: PASS (scripted r1→r2 promotion and r2→r1 rollback)
Independent evaluator / dual-wheel replay: PASS / PASS
Northstar preliminary Quote exact regression: PASS
Semifinal integrated candidate closure: PASS (retained; nine semantic mutations rejected)
Source snapshot runtime/evidence replay: verify from the current snapshot receipt; the former six-chain/OAC-bound-shadow wording is retired
Formal assembled submission: verify from the outer shallow and fresh-extraction deep receipts; prose is not authority
Repo-local aggregate media gate: NOT_APPLICABLE_TO_SOURCE_SNAPSHOT
Semifinal same-run chain: Source → native AgentTeams Taskflow → four-domain Coalition → authenticated HTTP Tool → installed-wheel quote-compose Skill → causal OTLP → CANDIDATE_ACCEPTED
Semifinal canonical target writes: 0
Semifinal production readiness: false
Workspace AgentTeams static/conformance: PASS
Core proposal-plane live AgentTeams: PASS_SEPARATE_RUN (does not upgrade Workspace/Golden)
Workspace/Golden autonomous Matrix-inbound AgentTeams: NOT_RUN
Spec 045 parent Approval/Apply: NOT_RUN (parent remains CANDIDATE_ACCEPTED / zero-write)
Current Golden controlled-local Approval/Apply: PASS
External human/IAM Approval: NOT_RUN
Real consented user validation: NOT_RUN
Real enterprise connectors / production ROI: NOT_RUN / not claimed
Current Golden OAC activation → Task Intake → Formation binding: PASS_SAME_RUN
Static OAC-bound shadow pack: OPERATOR_ARCHIVED_HISTORICAL_MECHANISM_ONLY (not shipped; not a current release chain)
Separate legacy OAC local Runtime-admission bridge: LOCAL_RUNTIME_ADMISSION_PASS
Legacy admission-bridge Handler / Agent execution: NOT_RUN
Legacy admission-bridge OutcomeCertificate: NOT_IMPLEMENTED_IN_THIS_BRIDGE
Evolution governance human review: NOT_RUN (SCRIPTED_GOVERNANCE_IDENTITY only)
ProductPath-v0.3-task-intake-bound built-wheel black box: PASS (12/12; mutations 11/11; PP-001 events 12)
ProductPath integrity attacks: PASS (evaluator 5/5; gate 2/2 rejected)
External ProductPath participants: 0
Evidence export strict RFC 8785 identity: P1 MIGRATION DEBT
```

The older five-Agent live advisory evidence is preserved for the original Core slice. It does not upgrade the Workspace formation coalition.

Enterprise adoption verdicts are intentionally separate: competition technical MVP=`GO`; a single-enterprise
isolated read-only Shadow Pilot remains `CONDITIONAL GO` until the enterprise-seed admission, exact Source
roots, identity/tenant isolation, migration, external human approval and zero-effect gates in
[`docs/OPERATIONS-ENVELOPE.md`](OPERATIONS-ENVELOPE.md),
[`docs/WORKSPACE-DATA-AND-PRIVACY.md`](WORKSPACE-DATA-AND-PRIVACY.md) and the Spec 045 completion matrix are
closed for that pilot; arbitrary-enterprise production=`NO-GO`.
