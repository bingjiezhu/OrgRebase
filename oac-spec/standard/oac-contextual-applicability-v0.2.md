# OAC Contextual Applicability v0.2 — Proposed Profile Extension

**Status**: implemented experimental draft  
**Extends**: Organizational Agent Contract Core v0.1  
**Conformance status**: passing the reference TCK is mechanics evidence, not formal OAC conformance  
**Normative language**: MUST, MUST NOT, SHOULD, SHOULD NOT, and MAY follow BCP 14

## 1. Purpose

This extension defines how a frozen organizational source contract decides whether one dependency
edge, impact rule, or Unknown-transition duty applies to one semantic change. It prevents a compiler
from treating every graph edge as a broadcast channel or hiding subject/scope policy in code.

The extension is intentionally not a general policy language. It defines a closed, enumerable,
non-executable predicate grammar. Authentication, policy administration, runtime transport, model
reasoning, arbitrary attributes, regex, user code, and external effects remain outside its scope.

## 2. Versioned predicate boundary

An implementation MUST distinguish these wire shapes:

```text
LegacyTransferPredicate
  semanticType
  afterValues[1..n]

ContextualApplicabilityPredicate
  predicateVersion = oac.supplier.applicability/v0.2
  semanticType
  afterState
  afterValues[0..n]
  subjectSelector?
  scopeSelector?
  relationTypes[]
```

The v0.2 operator vocabulary is closed. A registered predicate version MUST appear only in the wire
type named by its registry entry; a contextual wire object claiming the legacy version is
`PREDICATE_DEFINITION_INVALID`, not legacy input. An implementation MUST reject unknown members
structurally and MUST return `UNKNOWN/PREDICATE_VERSION_UNSUPPORTED` for a well-formed future version
that is not implemented. It MUST NOT execute embedded code or call a model to interpret a predicate.

Legacy predicates normalize in memory to `afterState=known` with absent contextual selectors. That
normalization MUST NOT modify their stored bytes, detached digest, or canonical projection.

## 3. Authority and ownership

The applicable predicate instance MUST be owned by the exact frozen `OrganizationSnapshot`. A caller
cannot supply a replacement predicate at evaluation time. A predicate match grants no role,
principal, qualification, tool, delegation, data, or effect authority.

`SemanticChangeSet.scopeRefs` are context anchors consumed only by an explicit `ScopeSelector`. They
are not a global traversal allowlist and MUST NOT silently change legacy predicate behavior.

Candidate or disputed source facts cannot by themselves establish mandatory truth. Retracted facts
are excluded. A known `FALSE` atom independent of admission dominates an `UNKNOWN` admission atom
under the truth algebra below; therefore an inapplicable candidate edge does not manufacture a
discovery duty merely by existing. That result closes only the change-relative route: it neither
admits the edge nor proves a candidate/uncovered target unless an admitted FALSE cut also exists.

An authoritative FALSE cut for a candidate or uncovered target requires an admitted changed-subject
root, an admitted `TRUE` prefix from that root to each decisive cut source, an admitted edge, rule, or
duty that evaluates `FALSE`, a non-retracted target, and a complete manifest. Removing all decisive
cut sources MUST leave no topology route to the target. The proof MUST bind every decisive evaluation
ID. A downstream candidate source beyond an already decisive cut neither invalidates that cut nor
contributes authority to it.

## 4. Total three-valued evaluation

Every supported predicate MUST evaluate to exactly one of `TRUE`, `FALSE`, or `UNKNOWN`. Evaluation
is pure and total over the exact Snapshot and ChangeSet roots. It MUST NOT read benchmark case IDs,
annotations, expected roles, model responses, environment state, or mutable external data.

Conjunction uses strong Kleene logic:

| Contains `FALSE` | Contains `UNKNOWN` | Result |
|---|---|---|
| yes | any | `FALSE` |
| no | yes | `UNKNOWN` |
| no | no | `TRUE` |

`SubjectSelector` is a conjunction across each populated set: `subjectRefs`, `nodeTypes`, and
`domainRefs`. `ScopeSelector` declares non-empty `refs`, `matchMode=all|any`, and
`missingBehavior=false|unknown`. Missing behavior MUST be explicit; identifier spelling or URI
prefixes MUST NOT imply it.

`relationTypes` is valid only when the predicate is owned by a typed `DependencyEdge`. A contextual
ImpactRule or UnknownTransitionDuty with non-empty `relationTypes` is an invalid definition; absence of
a relation context MUST NOT be converted into a mandatory Unknown duty.

The literal string `"unknown"` is not the Unknown value-state. A known business value containing that
literal MUST yield `UNKNOWN/SYNTHETIC_UNKNOWN_VALUE` when used as a status value by this Profile.
Although Core observations may contain generic scalars, this Supplier Profile accepts only strings for
a known status. Other scalar types are unsupported semantics and MUST NOT prove non-impact.

## 5. Applicability evidence ledger

Each evaluated edge, rule, or duty MUST produce one `ApplicabilityEvaluation` containing:

- the exact source ref and predicate version;
- its three-valued result and stable reason codes;
- unique, sorted source-field witness refs;
- a stable evaluation ID derived from the exact root digests, change subject, evaluated source ref,
  version, result, reasons, and witnesses using SHA-256 over RFC 8785 canonical JSON.

The normative ID payload keys are `snapshotDigest`, `changeDigest`, `changeSubjectRef`, `sourceRef`,
`predicateVersion`, `result`, `reasonCodes`, and `witnessRefs` in their RFC 8785 canonical form.

Every predicate-derived traversal, rule, duty, or false-closure path MUST bind the evaluations that
justify it. Path identity MUST change when those evaluation refs change. A topology-only bounded
non-impact path MAY have no evaluation refs when, inside a complete manifest, no `TRUE` or `UNKNOWN`
route from the changed subject reaches its admitted target. Its proof is the exact Snapshot root and
completeness manifest already bound by the Plan, and the verifier MUST recompute that negative
reachability; an empty ledger is never accepted as proof for a route closed by predicates. A plan
verifier MUST reject missing, altered, forged, dangling, or false-path bindings.

A topology-only path exception is local to that path. A Plan with an `affected` seed and zero
obligations is valid only when at least one `bounded_non_impact` / `unaffected_proven` path has a
non-empty evaluation set and every referenced evaluation is `FALSE`. An empty or purely topological
closure MUST NOT be accepted as a proved no-op.

## 6. Transfer and bounded non-impact

Only a `TRUE` edge predicate may extend an affected path. An `UNKNOWN` evaluation may extend only an
Unknown path and MUST NOT grant mandatory authority. A `FALSE` evaluation MUST NOT be traversed.

Inside a complete declared boundary, an admitted target may be `unaffected_proven` when no `TRUE` or
`UNKNOWN` route from the changed subject reaches it. This includes a topology-only proof where no
route exists. A predicate-closed route to an admitted, covered target binds all semantically decisive
`FALSE` evaluations, including a candidate source whose independent mismatch makes the conjunction
conclusively false. This is not an authority grant because the target is already admitted and covered.

A candidate or uncovered target has the stronger authority rule from Section 3: only an admitted
root, admitted `TRUE` prefix, and admitted `FALSE` frontier can establish its change-relative
non-impact, and only those authoritative frontier evaluations are bound. A candidate root cannot
establish this proof. Neither proof form admits its target.

The changed subject's `affected` path is a closure seed, not an implicit business obligation. Every
non-seed dependency-origin `affected` path MUST resolve its target's admitted `ownerRoleRef` and emit
coverage through that role. A rule-origin path uses the rule's admitted `requiredRoleRef`. Seed work
exists only when an explicit rule or duty emits it.

`maxDepth` applies to predicate-aware traversal. A possible continuation cut off at the bound yields
Unknown. Nodes conservatively reachable beyond that frontier through admitted `TRUE` or `UNKNOWN`
continuations MUST NOT be relabeled `unaffected_proven`; an already false continuation does not
contaminate the bounded non-impact proof.

## 7. Rules, Unknown duties, and ordering

A contextual impact rule emits its obligation only for `TRUE`. An admitted
`UnknownTransitionDuty` emits its obligation only for `UNKNOWN`. Its target, required role,
obligation type, evidence, prerequisite obligation types, and admission state are Snapshot-owned.

An explicit Unknown duty for a semantic target supersedes generic discovery for the same unknown
closure. Implementations MUST aggregate supporting path refs and evidence rather than create one
generic work item per missing entity.

A completeness manifest MAY declare the all-or-none triple `discoveryTargetRef`,
`discoveryObligationType`, and non-empty `discoveryEvidence`. Implementations MUST NOT infer an
obligation type from the target identifier.

Every `prerequisiteObligationType` MUST resolve to exactly one emitted predecessor obligation and MUST
compile to a verifier-visible happens-before relation. Missing, ambiguous, cyclic, or silently dropped
prerequisites are invalid Profile closure. This v0.2 Supplier Profile prohibits same-role
prerequisites; an implementation MUST NOT silently drop one because its own default projection uses
one WorkUnit per role.

Dependency and prerequisite witnesses have different quantifiers. A role-level dependency order
binds only its supporting `ImpactPath` IDs and covers the Cartesian product of every obligation-bearing
predecessor and successor WorkUnit. An obligation-specific prerequisite binds its exact predecessor
obligation ID, successor obligation ID, and supporting path ID only on WorkUnits that carry those
obligations. When both apply to the same role pair and WorkUnit pair, the one physical edge carries the
set union of the applicable reasons; other dependency-matrix edges MUST NOT cite prerequisite
obligations their endpoints do not carry. The derived role-order graph MUST itself be acyclic; a
producer cannot split roles across an acyclic WorkUnit graph to hide a contract cycle.

## 8. Verdict mapping

- A root predicate input required to interpret the change is Unknown: `UNKNOWN`.
- Root applicability is known but admitted closure retains named, bounded gaps: `PROVISIONAL`.
- All mandatory checks pass with no unresolved refs: `ACCEPT`.
- Any structural, authority, evaluation, witness, duty, order, integrity, or false-traversal failure:
  `REJECT`.

`UNKNOWN` and `PROVISIONAL` prohibit activation. Neither is an enterprise outcome claim.

## 9. Verification and compatibility

A conforming verifier MUST derive contextual applicability from the frozen roots and public Profile,
not trust compiler ledger entries. It MUST still accept legacy v0alpha1 plans whose contextual fields
are absent when their original contract closure is valid.

Conformance evidence for this draft requires at least:

- atomic truth-table, subject, scope, relation, version, and synthetic-Unknown vectors;
- byte/digest equivalence for legacy resources;
- positive contextual subject/scope/root-Unknown cases;
- mutations for evaluation omission/result/witness forgery, false traversal, duty omission,
  prerequisite removal/reversal, and Unknown collapse;
- a second independent implementation before any cross-implementation conformance claim.

Project-authored role or obligation labels are not Human Gold. Agreement with them MAY be reported as
an exploratory mechanics result only when the exact inputs, implementation closure, and claim limit
are published.
