# ADR 0007: A1 Plural Fixed-Plan Verification Hard Grill

**Status**: accepted; bounded increment implemented, final portfolio gate pending  
**Date**: 2026-08-24  
**Pressure**: Hard  
**Depends on**: ADR 0006 bounded seed-2 result

## Target

OAC is intended to standardize organizational meaning and evidence without standardizing one Agent
framework or one generated organization topology. Seed-2 demonstrates bounded derivation portability.
The next falsifiable question is whether independently chosen Plan structures can satisfy one public
acceptance relation and whether invalid structures fail for the right normative reasons.

This ADR records a self-administered Hard Grill. Each question chooses the recommended answer under the
previously aligned goal of an implementable Organizational Agent Contract rather than another runtime.

## Question 1: Is the current 52/52 result enough to widen the OAC claim?

**Options**: A. yes, call the full Profile portable; B. add more derive cases indefinitely; C. keep the
bounded derivation claim and test topology-plural verification next; D. move directly to enterprise ROI;
E. call the internal Go implementation independent.

**Recommended answer**: C.

Seed-2 proves that public rules can resolve selected derivation ambiguity. It does not prove that the
contract accepts multiple legitimate ways to organize work, which is central to dynamic Agent systems.

## Question 2: What is the verifier oracle?

**Options**: A. exact bytes of one reference Plan; B. similarity to a reference topology; C. one unique
domain verdict plus required/forbidden reason policies over public constraints; D. compiler self-
acceptance; E. model judgment.

**Recommended answer**: C.

Exact Plan comparison would turn OAC into a topology template. The fixed input has one expected verdict,
but many RoleInstance/WorkUnit decompositions may be legal members of that verdict relation.

## Question 3: What must the SUT receive?

**Recommended answer**: raw sealed Snapshot, Change, and Plan resources plus the exact bounded verifier
capability coordinate. It must recompute relevant derivation and verification facts; it must not receive
case IDs, expected verdicts, expected reasons, reference reports, mutation names, or a trusted compiler
certificate that bypasses verification.

## Question 4: What is the smallest meaningful positive set?

**Recommended answer**: freeze at least one input pair with two topologically distinct Plans that satisfy
the same obligations, authority, evidence, order, Unknown preservation, effect ceiling, and minimality
rules. Distinction must be structural, not a renamed identifier or reordered set-like list.

At least one plan should separate work more finely while another groups compatible work, without
violating qualification or separation-of-duties rules.

## Question 5: Which negative controls are required?

**Recommended answer**: for the same frozen input, generate digest-bound mutants for obligation omission,
impact-path/evaluation forgery, Unknown erasure, unauthorized role/principal binding, missing evidence,
reversed or missing order, qualification failure, separation-of-duties failure, effect-ceiling breach,
and non-minimal redundant work. A verifier that accepts all positive plans but misses one required class
does not pass the bounded capability.

## Question 6: Should Python define truth when verifiers disagree?

**Recommended answer**: no. Freeze public verdict and reason requirements before executing the second
verifier. Preserve both observations in a new immutable disagreement incident; resolve through prose,
schema, registry, fixture, or implementation change without majority vote or reference-wins.

## Question 7: How should capabilities be widened?

**Recommended answer**: add a new bounded `verify` track and RequirementSet coordinate. Do not mutate the
seed-2 derive capability in place and do not advertise complete Supplier verification. The capability
names exactly the fixed positive/mutation relation tested.

## Question 8: What is the exit condition?

The bounded increment exits only when:

1. the public verification relation and reason policies are frozen before SUT execution;
2. two structurally different positive Plans pass both verifiers;
3. every required negative-control class is rejected by both with compliant reasons;
4. SUT requests reveal no case, expectation, oracle, or mutation metadata;
5. generated inputs, implementation closures, complete observations, and disagreements are digest-bound;
6. permissive accept-all, reason-erasure, and selected relation-rule-erasure mutants are rejected;
7. every claimed source-rule mutant is independently built, uniquely killed by a dedicated case, and
   any equivalent or unreachable conjunct is excluded from the denominator;
8. source and isolated-wheel replay agree on frozen material identities; and
9. the report still excludes clean-room independence, complete Profile coverage, enterprise correctness,
   and clean-archive reproducibility until their separate gates pass.

## Question 9: Are two accepted Plans "equivalent"?

**Options**: A. yes, shared verdict proves workflow equivalence; B. compare raw Plan bytes; C. define
membership in one public acceptance set and make no behavioral-equivalence claim; D. compare WorkUnit
counts; E. let the reference compiler decide.

**Recommended answer**: C.

For public relation `R`, this increment uses:

```text
Accept_R(S, C, P) := Verify_R(S, C, P).domainVerdict == ACCEPT
Fiber_R(S, C)     := { P | Accept_R(S, C, P) }
Plural_R(S, C)    := exists structurally distinct P1,P2 in Fiber_R(S,C)
```

Membership in the same fiber does not prove equal runtime traces, cost, latency, quality, enterprise
outcome, or behavior under failure. OAC MUST NOT call the Plans equivalent until an observation boundary
and an actual equivalence relation are separately standardized.

## Question 10: What counts as structurally distinct?

**Recommended answer**: compare topology after alpha-renaming and normalization of set-like members. A
different obligation-to-WorkUnit partition is material. Renamed IDs, array reorderings, or redundant
transitive edges are not. The plurality witness is harness-owned evidence and MUST NOT be disclosed to
the SUT.

The seed-1 target uses the current contextual SC-008 Plan and a legal split of its Operations WorkUnit.
The split changes the obligation partition and Plan-induced obligation-order reachability while
retaining the same public mandatory obligations, mandatory order relation, authority, qualifications,
evidence duties, Unknown policy, and zero-effect bound. The two induced reachability relations need not
be equal: grouping compatible obligations may strengthen order beyond the shared mandatory relation.
SC-010 is an auxiliary root for Unknown-preservation controls; it is not presented as the same fixed
input as SC-008.

## Question 11: Does this increment prove effect-ceiling verification?

**Recommended answer**: no. The current `OrganizationPlan` vocabulary admits only `zero_effect`.
Therefore a non-zero effect value is a sealed-resource admission failure
`ERROR/CORE_SCHEMA_INVALID`, not a domain-level `COMPLETED/REJECT` with
`EFFECT_CEILING_EXCEEDED`. Seed-1 records that admission control but leaves a domain effect-ceiling
mutant open until a successor Plan schema admits a wider effect vocabulary.

## Question 12: Does finer work conflict with inclusion minimality?

**Recommended answer**: no, provided minimality remains contract-relative. WorkUnit count, similarity to
the reference topology, cost, and global optimality are not hard acceptance rules. A non-minimal mutant
must add a removable role/work element that supports no required obligation; plan quality belongs in a
future soft-preference or metric layer.

## Question 13: Can the harness leak the case through a derived fixture ID?

**Recommended answer**: yes, unless identity construction is part of the oracle audit. Hashing a public
case ID merely hides its spelling; it remains reversible by dictionary lookup. Seed-1 therefore derives
transformed fixture identity from a domain-separated projection of Plan content, excludes case ID from
that projection, and tests that the former case-derived formula is absent. This removes a direct lookup
channel, but the public corpus remains fingerprintable and is not claimed to be adversarially secret.

## Question 14: May an implementation evade reason policy with an invented code?

**Recommended answer**: no. Completed results are admitted only when every reason code is present in the
capsule-bound registry, sorted, and unique. The RequirementSet itself is checked against the same
registry. A required code plus an arbitrary unregistered diagnostic is a protocol failure, not a pass.

## Question 15: Do two same-repository implementations prove independence?

**Recommended answer**: no. The Go verifier has a dedicated source/build coordinate so it cannot mutate
the frozen seed-2 Supplier evidence closure, but its provenance explicitly records reference-source
observation and same-repository authorship. Agreement is differential portability evidence only. A
clean-room or separately governed implementation remains an open gate.

## Question 16: Is a negative-only matrix enough?

**Recommended answer**: no. The final lattice contains positive controls for `ACCEPT`, `PROVISIONAL`,
and `UNKNOWN`, plus `REJECT` and admission `ERROR` controls. This prevents an implementation from
collapsing all unresolved states into rejection while still passing the invalid-Plan cases.

## Question 17: What did the red team change before accepting evidence?

**Recommended answer**: expand the frozen matrix to 25 cases; add sealed digest, input-root, nested
coherence, obligation projection, Plan status, mission, cycle, and decision-ledger probes; bind reasons
to the registry; remove case-derived fixture identity; add a relation-rule-erasure mutant; and isolate
the Go Plan verifier from the prior Supplier implementation coordinate. Existing 13-case evidence is
superseded rather than reinterpreted.

## Question 18: Does “every non-admitted semantic input” include a candidate node?

**Recommended answer**: not in this coordinate, and the prose must say so. The Supplier Profile uses
node admission while evaluating applicability, but the seed-1 PlanDecision projection is closed over
governed roles/principals and non-admitted dependency edges, `ImpactRule` objects, and
`UnknownTransitionDuty` objects. A candidate node does not independently create a PlanDecision subject.
A future coordinate may widen that projection, but it must update compiler, positive Plans, verifiers,
fixtures, and evidence together rather than relying on an ambiguous umbrella phrase.

## Question 19: Is the 25-case red-team matrix enough to close the verification claim?

**Recommended answer**: no. Exact source-rule erasure showed that a verifier could delete admission,
qualification, responsibility, order, minimality, or decision-content branches and still pass the
earlier aggregate controls. The frozen coordinate therefore expands to 39 cases: two `ACCEPT`, one
`PROVISIONAL`, one `UNKNOWN`, 28 completed `REJECT`, and seven admission `ERROR`. Python and the
same-repository Go verifier pass 39/39 from source and isolated install; 36 observations are exact,
three are registered permitted diagnostic variance, and zero are scored indeterminate.

## Question 20: May a mutation score hide a rule that cannot be isolated?

**Recommended answer**: no. Fourteen enumerated exact Go source-rule erasures are built in fresh
temporary trees and executed against all 39 cases. Each is uniquely killed by its single dedicated
case. The selected RoleDefinition admission conjunct cannot be independently falsified under an
admitted coherent seed-1 root: derivation already requires the affected owner role to be admitted and
Plan binding requires the RoleInstance role to equal the derived obligation's required role. It is
therefore reported as `EQUIVALENT_OR_UNREACHABLE_UNDER_SEED1` and excluded from the denominator. A
bounded honest 14/14 is stronger than an inflated 15/15.

## Question 21: Does fixed-Plan verification preserve the original OrgRebase thesis?

**Recommended answer**: only if it remains the contract layer of an enterprise work-evolution loop.
The current result proves that one frozen organizational change can admit more than one legal Agent /
WorkUnit topology while rejecting selected semantic corruption. It does not yet observe enterprise
changes, execute the accepted Plan, certify outcomes, or promote repeated trajectories into governed
Skills. OrgRebase remains the product thesis; OAC defines the portable organizational contract and
verification boundary. Specs 004–006 must add human evidence, outcome shadowing, and governed learning
without weakening the authority rules established here.

## Decision

```text
seed-2 bounded derivation portability
        ↓
public fixed-plan acceptance-set relation + unique verdict/reason policy
        ↓
two structurally distinct valid Plans
        ↓
39-case invalid/uncertain/positive lattice
        ↓
Python + internal Go verifier observations and durable disagreements
        ↓
14 uniquely killed exact Go source-rule erasures
        ↓
only then: external implementation and wider compatibility governance
```

This next increment directly tests the OAC design principle: an organizational contract can constrain
meaning, authority, evidence, and safety while allowing the Agent system to choose its own internal
architecture.
