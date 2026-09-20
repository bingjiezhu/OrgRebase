# Validation evidence index

Validation reports are immutable, revision-scoped evidence. A later report does not rewrite an older
claim boundary.

| Slice | Code revision | Report | Meaning |
|---|---|---|---|
| M0 Shadow mechanics | `424004e` | [STATUS.md](STATUS.md) | Historical v0alpha1 mechanics evidence |
| M1 contextual applicability | `3e66c6853b1f0f2558d48af0da285790ea4c3903` | [STATUS-v0.2.md](STATUS-v0.2.md) | Current v0.2 technical-slice evidence |
| A0 conformance lab | working tree over `e301952` | [STATUS-v0.3-phase-a.md](STATUS-v0.3-phase-a.md) | Phase A runner/protocol and internal cross-language differential evidence |
| A1 Supplier seed-1 development | working tree over `e301952` | [STATUS-v0.4-a1-portability.md](STATUS-v0.4-a1-portability.md) | Superseded by ADR 0006: historical 34-observation selected set; capability/generator/summary/install evidence debt prevents a current validation claim |
| A1 Supplier seed-2 semantic matrix | working tree over `e301952` | [STATUS-v0.5-a1-semantic-matrix.md](STATUS-v0.5-a1-semantic-matrix.md) | Current bounded internal evidence: source and installed 52/52, seven incident/resolution pairs, two rejected mutants, and a closed v0alpha2 machine manifest; not independent/full-profile/clean-archive evidence |
| A1 plural fixed-Plan verification | working tree over `e301952` | [STATUS-v0.6-a1-plural-plan-verification.md](STATUS-v0.6-a1-plural-plan-verification.md) | Current bounded internal evidence: two accepted topologies in one acceptance fiber; 39/39 on Python and same-repository Go from source and isolated install; three permitted diagnostic variances, three killed aggregate mutants, and 14/14 killed exact source-rule mutants; not equivalence/independence/full-profile/enterprise evidence |
| Spec 009 minimum evolution profile | working tree over `e301952`, validated 2026-09-09 | [STATUS-2026-09-09-spec009.md](STATUS-2026-09-09-spec009.md) | Current composite gate: 480 tests and build pass; fresh wheel: 7 evolution tests and 33 TCK cases pass; frozen coordinate unchanged; same-reference zero-effect evidence |

The current cross-Spec implementation order and blockers are tracked in
[SPEC-PORTFOLIO-2026-09-09.md](SPEC-PORTFOLIO-2026-09-09.md). It is a working-tree audit, not
revision-scoped validation evidence. The earlier [v0.3 portfolio](SPEC-PORTFOLIO-v0.3.md) remains
historical. Spec 009's frozen task list is preserved byte-for-byte; its later T009-011/012 completion
is recorded in the current matrix and execution report.

ADR 0006's bounded semantic branch matrix and durable disagreement/evidence coordinate are recorded in
v0.5 without rewriting v0.4. The active Spec 003 priority is now plural fixed-plan verification, while
clean-archive replay and external organizational independence remain separate gates.

The seed-2 derivation machine coordinate is
[`evidence-manifest.json`](../../experiments/supplier-v02-portability/v0.2-seed-2/evidence-manifest.json).
Its isolated-install ledger is explicitly self-attested rather than cryptographic execution
provenance, excludes Python standard-library and operating-system bytes, and does not turn the
cross-host portable CI projection into an exact release identity.

The separate fixed-Plan machine coordinate is
[`evidence-manifest.json`](../../experiments/plan-verification-portability/v0.1-seed-1/evidence-manifest.json).
Its accepted-set witness records different obligation partitions and different Plan-induced order
reachability while requiring both Plans to realize the same mandatory contract. The public corpus is
fingerprintable and the Go verifier is not clean-room. The exact source-rule mutation ledger covers
14 enumerated branches, not all possible verifier defects; one RoleDefinition-admission conjunct is
equivalent or unreachable under the frozen seed-1 roots and is explicitly excluded from its denominator.

No report here is formal OAC conformance, Human Ground Truth, enterprise-effectiveness evidence, or
production authorization, supply-chain attestation, or proof of a universal enterprise standard.
