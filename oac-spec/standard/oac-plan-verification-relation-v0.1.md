# OAC bounded Plan verification relation v0.1

**Status**: experimental conformance-lab contract  
**Coordinate**: `oac.supplier.plan-verification.plural-capsule/v0.1-seed-1`  
**Scope**: selected Supplier v0.2 contextual resources only

## 1. Purpose and claim boundary

This contract tests whether a verifier can judge an arbitrary sealed `OrganizationPlan` against the
same sealed `OrganizationSnapshot` and `SemanticChangeSet` without comparing the Plan to one compiler
output. It is a bounded laboratory relation, not complete Supplier conformance, clean-room
independence, enterprise correctness, workflow equivalence, or proof that OAC is an industry standard.

For this coordinate:

```text
Accept_R(S, C, P) := Verify_R(S, C, P).verdict == ACCEPT
Fiber_R(S, C)     := { P | Accept_R(S, C, P) }
```

Two Plans in the same fiber are each valid members of the public relation. No equal-cost, equal-trace,
equal-outcome, or behavioral-equivalence conclusion follows.

## 2. Closed input and stable observation

The operation consumes the raw bytes of exactly one Snapshot, Change, and Plan. All three MUST first
pass `SealedResource/v1` admission. The verifier MUST recompute Supplier derivation facts from Snapshot
and Change and MUST NOT trust a compiler certificate, expected report, case identifier, mutation label,
or reference Plan.

The stable cross-implementation observation is:

```json
{
  "snapshotDigest": "sha256:<64 lowercase hex>",
  "changeDigest": "sha256:<64 lowercase hex>",
  "planDigest": "sha256:<64 lowercase hex>",
  "verdict": "ACCEPT | REJECT | PROVISIONAL | UNKNOWN",
  "reasonCodes": []
}
```

Digest fields bind the observation to admitted raw resources. `reasonCodes` MUST be sorted, unique, and
drawn from the capsule-bound core reason-code registry. An unregistered reason is a protocol-admission
failure, not a vendor escape hatch. Implementation messages, internal dimensions, witnesses, compiler
IDs, and certificate-build IDs are outside the comparison surface.

## 3. Acceptance relation

After exact root, authority-envelope, and Profile admission, a Plan is `ACCEPT` exactly when all of the
following hold for the selected coordinate:

1. its applicability evaluations, impact paths, obligations, and unresolved references equal the
   independently recomputed topology-free Supplier derivation;
2. its status preserves the recomputed unresolved state;
3. every required obligation is covered exactly once by a RoleInstance and exactly once by a WorkUnit;
4. every WorkUnit contribution and accountable binding is backed by its referenced RoleInstances;
5. every selected role and principal is admitted, active, eligible, qualified, mission-consistent, and
   bound only to obligations requiring that role;
6. every separation-of-duties constraint holds;
7. every WorkUnit emits all evidence required by its obligations;
8. the WorkUnit DAG realizes every required role/obligation order with the required reason references,
   contains no invalid order edge, and is acyclic;
9. the consideration ledger covers every governed role and principal plus every non-admitted
   dependency edge, `ImpactRule`, and `UnknownTransitionDuty`; node admission remains an applicability
   input in seed-1 but does not independently create a PlanDecision subject;
10. inclusion minimality names exactly the selected RoleInstances and WorkUnits and contains no selected
    element that supports no required obligation; and
11. all admitted Plan, RoleInstance, and WorkUnit effect ceilings remain `zero_effect`.

Any failed domain requirement yields `REJECT`. If no requirement fails but root applicability is
unknown, verdict is `UNKNOWN`. If no requirement fails and only derived non-root facts remain
unresolved, verdict is `PROVISIONAL`; otherwise it is `ACCEPT`.

`UNKNOWN` is a domain verdict. Timeout, crash, malformed JSON, invalid Base64, schema failure, digest
mismatch, and resource exhaustion are protocol statuses and MUST NOT be represented as `UNKNOWN`.

## 4. Topology freedom and material plurality

The verifier MUST treat compiler-owned RoleInstance, WorkUnit, and Decision IDs as opaque. It MUST NOT
recompute reference compiler IDs or compare RoleInstance/WorkUnit arrays to a golden topology.

A harness may establish that two accepted Plans are materially distinct only after:

- alpha-normalizing compiler-owned IDs;
- treating declared set-like members as sets;
- ignoring raw array order; and
- comparing the partition of required obligations into WorkUnits; and
- recording the obligation-order reachability induced by each Plan's WorkUnit topology.

A changed obligation partition is material. Renaming IDs, reordering arrays, or adding a redundant
transitive order edge is not sufficient. Let `M_R(S,C)` be the mandatory obligation-order relation
derived from the roots and `I(P)` the obligation-order reachability induced by Plan `P`. Every accepted
Plan MUST realize `M_R(S,C)`, but two accepted Plans need not have equal `I(P)`: grouping compatible
obligations can impose additional order. That permitted strengthening is a material topology
difference, not workflow equivalence. The plurality witness therefore records both induced relations
without treating equality as an acceptance rule. This proof is harness-owned and MUST NOT enter the SUT
request.

## 5. Reason policy

Each scored case freezes one protocol status and, for completed domain verification, one verdict. A
case MAY require a subset of reason codes and forbid another subset. Implementations MAY add other
registered core reasons when they are true; they MUST NOT omit required reasons, emit forbidden
reasons, or emit unregistered reasons. Vendor-extension reasons are outside seed-1.

The seed-1 mutation lattice links each semantic fault class to at least one required core reason:

| Fault class | Required reason |
|---|---|
| Plan input roots do not bind the admitted roots | `INPUT_ROOT_MISMATCH` |
| nested resource identity/owner coherence fails | `RESOURCE_COHERENCE_VIOLATION` |
| obligation projection differs from independent derivation | `OBLIGATION_SET_MISMATCH` |
| missing obligation coverage | `OBLIGATION_UNSATISFIED` |
| applicability/evaluation forgery | `APPLICABILITY_EVALUATION_MISMATCH` |
| impact-path omission | `IMPACT_PATH_OMITTED` |
| Unknown erasure | `UNKNOWN_NOT_PRESERVED` |
| Plan status differs from recomputed unresolved state | `PLAN_STATUS_MISMATCH` |
| ineligible or unqualified authority binding | `QUALIFICATION_INVALID` |
| separation-of-duties collapse | `SEPARATION_OF_DUTIES_VIOLATION` |
| responsibility binding forgery | `RESPONSIBILITY_BINDING_INVALID` |
| missing evidence | `EVIDENCE_DUTY_MISSING` |
| missing or invalid required order | `ORDER_CONSTRAINT_MISSING` and fault-specific order reasons |
| WorkUnit order cycle | `ORDER_CYCLE` |
| removable unsupported work | `PLAN_NOT_MINIMAL` |

The matrix also contains positive controls for all four domain verdicts reachable in this coordinate:
two `ACCEPT` topologies, one `PROVISIONAL`, one `UNKNOWN`, and completed `REJECT` controls. Sealed root
and Plan digest tampering MUST fail as `ERROR/ROOT_DIGEST_MISMATCH` before domain verification.

The current Plan schema admits only `zero_effect`. A non-zero effect mutation is therefore scored as
sealed-resource admission `ERROR/CORE_SCHEMA_INVALID`, not as domain
`REJECT/EFFECT_CEILING_EXCEEDED`. Domain-level effect-ceiling mutation remains outside this coordinate.

## 6. Oracle non-evasion

The runner MUST NOT disclose the case ID, a case-derived fixture identifier, expectation, mutation
class, requirement ID, fixture path, reference report, reference Plan, or plurality projection. Every
SUT request contains only the protocol coordinate, a content-bound opaque request ID, and the three raw
Base64 resources. Transformed fixture identities are content-derived without a case-ID input.

An accept-all implementation, a reason-erasure implementation, and an implementation that erases a
selected family of relation rules MUST all fail the frozen RequirementSet. The corpus and transforms are
public and therefore fingerprintable; non-disclosure prevents a direct oracle field, not recognition by
a hostile implementation. Passing this selected set remains bounded self-consistency/portability
evidence until a separately maintained implementation and a held-out governance process satisfy their
own gates.
