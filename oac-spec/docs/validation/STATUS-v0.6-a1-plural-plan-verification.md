# OAC A1 Supplier Seed-1 Plural Fixed-Plan Verification Report

**Validation date**: 2026-08-24  
**Evidence status**: current bounded working-tree validation evidence  
**Source state**: uncommitted working tree on `main`, based on
`e301952ae08986e6398a636f04f883036bc88c8d`  
**Reference package**: `oac-contract 0.3.0a0`  
**Claim ceiling**: two same-repository implementations satisfy one public 39-case fixed-Plan relation,
including two structurally different accepted organizations and 14 uniquely killed exact Go
source-rule erasures. This is not workflow equivalence, verifier completeness, clean-room or
organizational independence, complete Supplier/OAC conformance, enterprise outcome evidence, standard
consensus, certification, security evidence, or production authorization

## Result

ADR 0007 asks whether OAC can constrain organizational meaning without freezing one Agent topology.
For one bounded Supplier coordinate, the answer is yes:

| Surface | Source tree | Isolated install |
|---|---:|---:|
| Python verifier | 39/39 | 39/39 |
| same-repository Go verifier | 39/39 | 39/39 |
| exact cross-implementation observations | 36 | 36 |
| permitted diagnostic variance | 3 | 3 |
| scored indeterminate | 0 | 0 |
| aggregate response mutants killed | 3/3 | 3/3 |

The frozen verdict lattice is:

| Outcome | Cases |
|---|---:|
| `ACCEPT` | 2 |
| `PROVISIONAL` | 1 |
| `UNKNOWN` | 1 |
| completed `REJECT` | 28 |
| admission `ERROR` | 7 |
| total | 39 |

The two accepted SC-008 Plans realize the same root-derived mandatory obligations and mandatory order
relation but have different obligation-to-WorkUnit partitions and different Plan-induced order
reachability. They are two members of one bounded acceptance fiber. They are not claimed to have equal
runtime behavior, cost, trace, latency, quality, or enterprise outcome.

## Exact source-rule mutation gate

The mutation runner copies the Go verifier to a fresh temporary tree, requires every source preimage to
occur exactly once, applies one rule erasure, builds a fresh binary, and executes all 39 cases. It does
not post-process verifier responses.

- baseline: 39/39;
- exact source mutants required: 14;
- exact source mutants killed: 14;
- each mutant result: 38 pass / 1 fail;
- each failed case equals that mutant's single dedicated kill case;
- exact-JCS summary digest:
  `sha256:c07978322372e5e5029b5184f1b0bd21d50ac1c426d625405480f880452aaa01`.

The covered families are exactly-once obligation coverage, accountable binding, active/admitted
principal binding, qualification references, order reasons, dangling and duplicate order controls,
minimality considered/non-removable sets, RoleInstance/WorkUnit effect admission, and candidate/retracted
decision content.

One selected RoleDefinition admission conjunct is classified
`EQUIVALENT_OR_UNREACHABLE_UNDER_SEED1`. Supplier derivation already requires the affected owner role
to be admitted, while fixed-Plan qualification binds every RoleInstance role to the derived
obligation's required role. No admitted coherent seed-1 root isolates that conjunct from obligation
projection and role matching. It is excluded from the 14/14 denominator rather than counted as killed.

## Evidence coordinate

| Material | Identity |
|---|---|
| capsule detached digest | `sha256:e9ca42382e0793fd108361c103d815c23cdb64385b6eccdac31dbe82676a17f5` |
| capsule raw bytes | `sha256:c36826b6b91cfa335d6b52e1f14816bdeea417812f28247ccba3749bdca90c4a` |
| capability set | `sha256:213eca08115c2773588a91e88b4ff92d89c1bf0ce587ec08f89624c2850b65f9` |
| RequirementSet | `sha256:4c8e2d705faacda1f71d1687aaa4a73634ed5764ad04d7d02ca5e0148cedc089` |
| source parity summary | `sha256:d970c4cb46efd481d9cc5ab8c53ee247832be6581e353157d88e2bf5b0f4eebc` |
| installed parity summary | `sha256:14e2c0704ee7eb56129ecaf2546a013b8ab6798541644579779d37b2a7ba7222` |
| installed material ledger | `sha256:b4bab1d169a92309c8e8a8c50b3f76a669a021b7febf22ed74d462ac1373cdc8` |
| rule mutation summary | `sha256:c07978322372e5e5029b5184f1b0bd21d50ac1c426d625405480f880452aaa01` |
| source closure | `sha256:3015f4451b819efbc2d1e41b9389526fa1efd9c852b6c2a4d4f9b7a2fae8d19b` |
| evidence closure | `sha256:44b93e65c75bff606a594b3897f7012ba6f3d15ecea11b917bccba9bcf27b612` |
| evidence manifest detached digest | `sha256:f0ccc77267d4680c27a2d6c7dc802e307754afbd34dfcab5fad4360c4036a8e9` |
| evidence manifest raw bytes | `sha256:d2c946589b41c23b44425f55613844cf8cc012bacb91a3a96d02e38185a41b07` |

The seed-1 capsule remains byte-for-byte frozen. Source/install replay and the outer ledger/manifest may
refresh, but unrelated later reason-code additions do not rebuild the published 39-case coordinate.

The machine authority is
`experiments/plan-verification-portability/v0.1-seed-1/evidence-manifest.json`. This report explains
that evidence; it does not replace it.

## Disagreement policy

Three cases produce the same protocol status and scored verdict but different registered diagnostic
reason supersets. Each difference has an immutable incident plus a separate resolution and is
classified `PERMITTED_DIAGNOSTIC_VARIANCE`. No majority rule or reference-wins policy is used. There
are zero open scored disagreements. The three inputs have not yet been reduced into smaller standalone
reproducers, so task T003-A1106 remains open.

## Build and quality replay

`make check` passed on 2026-08-24:

- Ruff, schema drift, benchmark drift, CTK bundle drift, Phase-A CTK, both Go kernels, Supplier seed-2
  evidence, Plan capsule/parity/source-mutation/evidence gates, and legacy TCK passed;
- legacy TCK: 33/33;
- Phase-A external CTK: 24/24 required;
- Python tests: 470 passed;
- coverage: 90.35%, above the 90% gate;
- Plan source replay: Python 39/39 and Go 39/39;
- Plan isolated replay: Python 39/39 and Go 39/39;
- exact source mutation gate: 14/14.

No standalone static type checker is configured. Therefore T003-A1201 remains open despite the green
existing composite gate. No clean Git archive was created from this uncommitted working tree, so
T003-A1202/A1204 also remain open.

## What this establishes

This checkpoint supports one narrow OAC thesis: an organizational contract can define a public
acceptance set that allows more than one Agent/WorkUnit topology while independently rejecting selected
omission, forgery, Unknown, authority, evidence, order, qualification, and minimality defects. The
source mutation gate makes 14 specific semantic branches falsifiable instead of relying on a broad
accept-all control.

For OrgRebase, this is the contract layer of an enterprise work-evolution engine. It is not yet the
whole loop. Observing a live enterprise change, executing an accepted Plan, certifying the outcome,
and promoting repeated trajectories into governed Skills remain separate Specs 004–006 gates.

## What remains open

1. The Go verifier is same-repository, AI-assisted, and reference-source-observed; it is not clean-room
   or organizationally independent.
2. The 39 cases are public selected cases, not a hidden or exhaustive suite.
3. The 14 mutants cover enumerated exact Go source erasures, not arbitrary bugs or a complete mutation
   basis.
4. Three permitted diagnostic variances have incident/resolution records but are not minimized into
   smaller standalone reproducers.
5. There is no clean-archive replay, signed suite registry, external maintainer, public certification
   body, qualified Human Ground Truth, enterprise-twin OutcomeCertificate, ROI, or production-safety
   evidence.
6. Spec 007 G1a now validates controlled-local, zero-effect lowering into a runtime-neutral bundle.
   It does not provide a runtime adapter, execute that bundle, issue an OutcomeCertificate, or establish
   an OrgRebase AgentTeams bridge or enterprise effect.

The next evidence-increasing increment is clean-archive replay followed by a separately maintained
implementation. The next product-increasing increment is an explicit runtime-admission adapter plus
one disposable enterprise outcome-shadow run; neither should widen the current standard claim before
its own evidence gate passes.
