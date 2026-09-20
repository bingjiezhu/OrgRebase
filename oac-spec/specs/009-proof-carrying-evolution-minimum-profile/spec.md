# Spec 009 — Proof-Carrying Evolution Minimum Profile

**Status**: `ACTIVE / NARROW IMPLEMENTATION AUTHORIZED`  
**Profile**: `oac.evolution.minimum/v0.1`  
**Depends on**: OAC Core, Supplier Change Profile v0.2, Runtime Lowering v0.1, Spec 008 data contract  
**Maximum claim**: bounded lifecycle-contract conformance; no autonomous evolution or production claim

## Purpose

OAC currently standardizes Source snapshots, semantic changes, acceptable Plans, independent Plan
certificates and zero-effect runtime lowering. It intentionally stops before execution. That boundary is
correct, but it leaves three portable lifecycle facts unnamed: why work was requested, whether the observed
business outcome satisfied the request, and which exact Source material was admitted after governance.

This profile adds only those portable records:

```text
OrganizationSnapshot + SourceAdmissionReceipt + OrganizationalDemand + SemanticChangeSet
  -> OrganizationPlan + PlanCertificate
  -> runtime-owned ExecutionReceipt (extension ResourceRef only)
  -> OutcomeCertificate
  -> candidate evolution material
  -> SourceAdmissionReceipt for an immutable successor
```

OAC does not execute handlers, decide enterprise truth, select a vendor runtime, or automatically promote
experience. Runtime execution receipts and candidate/procedure payloads remain implementation-owned
extensions referenced by exact `ResourceRef`.

## Normative resources

### `OrganizationalDemand`

An immutable request envelope. It MUST bind the requester, accountable role, exact Snapshot, subject and
trigger refs, objective, desired outcomes, evidence obligations, constraints, priority and effect ceiling.
It MUST NOT grant authority merely because a principal or role is named.

### `SourceAdmissionReceipt`

An independent admission decision over exact Source resources. It MUST bind admitted subject refs, the intake
manifest digest, intake profile and rule-set digests, proposer, decision authority, reviewers, verdict,
reason codes, unresolved refs and admitted refs. For a successor, it MUST bind the exact predecessor and the
candidate/governance decision refs. It records an authority decision; it does not manufacture Source truth.

### `OutcomeCertificate`

An independent verdict over exact Snapshot, Demand, Change, Plan, PlanCertificate, runtime-owned
ExecutionReceipt and observation roots. Verdicts are `ACCEPT`, `REJECT`, `PROVISIONAL`, and `UNKNOWN`.
Dimensions, typed reason codes, oracle identity/build, observation profile, evidence refs and effect
reconciliation are mandatory. `ExecutionReceipt.COMPLETED` is never sufficient for `ACCEPT`.

## Lifecycle roots

The profile defines domain-separated projections, all serialized using RFC 8785 then SHA-256:

| Root | Projection |
|---|---|
| `S` | ordered Snapshot and admitted Source receipt refs |
| `D` | Demand and SemanticChangeSet refs |
| `P` | OrganizationPlan and PlanCertificate refs |
| `X` | RuntimeBinding, Runtime bundle, ExecutionReceipt and execution-evidence refs |
| `O` | `S`, `D`, `P`, `X` plus OutcomeCertificate ref |
| `E` | candidate, support/counterexample Outcome, replay/regression and governance-decision refs |

Root calculation MUST be deterministic, order-explicit and domain-separated. Missing, duplicated, cross-
namespace, stale or digest-invalid refs fail closed. The root functions describe closure; they do not confer
admission or effect authority.

## Permanent authority rules

1. Source, Demand, Plan, Execution, Outcome and Evolution are distinct records and authorities.
2. The compiler cannot certify its own Plan; the Runtime cannot certify its own business Outcome.
3. A proposal producer cannot admit its own Source successor.
4. `REJECT`, `PROVISIONAL`, and `UNKNOWN` Outcomes may be counterevidence but never positive promotion proof.
5. Experience yields candidates only. Promotion requires an exact-digest governance decision and an immutable
   successor; rollback changes an active pointer outside OAC Core and never rewrites history.
6. Unknown fields fail structural validation. Unknown facts remain `UNKNOWN`; they cannot be normalized to
   unaffected or accepted.
7. This profile permits only `zero_effect` in v0.1.

## Acceptable-plan-set rule

The profile accepts any exact `OrganizationPlan` whose independently recomputed `PlanCertificate` is clean
`ACCEPT`; it does not compare that Plan to a preferred compiler output. Alternative valid topologies may have
different `P` and `X` roots while satisfying the same Demand and Outcome dimensions. Ranking may choose among
accepted candidates, but rank never establishes validity, runtime permission, Outcome success, or Source
authority.

## Conformance classes

- **C0-WIRE**: parse, strict schema, detached digest, registry and round-trip conformance.
- **C1-ROOTS**: independently compute identical `S/D/P/X/O/E` roots and reject registered mutations.
- **C2-OUTCOME**: prove at least one `Plan ACCEPT -> Execution COMPLETED -> Outcome REJECT` vector.
- **C3-GOVERNANCE**: prove wrong authority, self-admission, rejected evidence, predecessor drift and digest
  substitution cannot admit a successor.

An implementation MUST NOT claim C2 or C3 from schema-only tests.

## Acceptance criteria

- Every new kind is present in the kind registry, deterministic schemas and built wheel.
- A second implementation can validate fixtures without importing the reference compiler or OrgRebase.
- Positive, unknown and completed-but-rejected Outcome fixtures have stable exact digests.
- Mutation fixtures cover unknown fields, root drift, cross-namespace roots, runtime self-certification,
  authority collision, illegal verdict promotion and predecessor drift.
- Legacy OrganizationPlan, PlanCertificate and runtime-lowering bytes remain unchanged.
- `make check` and `uv build` pass from a clean extraction.

## Non-goals

No scheduler, workflow DSL, Agent framework, connector, IAM system, vector database, automatic Skill learning,
production execution or claim of cross-enterprise generalization is authorized.
