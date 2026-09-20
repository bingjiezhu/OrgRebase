# Supplier Status Change Profile v0.2

**Status**: contextual technical slice implemented; v0alpha1 compatibility retained
**Executable reference**: `src/oac/applicability.py` and `src/oac/supplier.py`
**Effect ceiling**: `zero_effect`

## Applicability

This Profile accepts a sealed `OrganizationSnapshot` and admitted `SemanticChangeSet` only when:

1. `semanticType` is `supplier.status` and exactly one unique delta path is `/status`;
2. each delta operation is consistent with its explicit before/after value states, and a `known`
   Supplier status is a string rather than another generic `ObservedScalar` type;
3. snapshot and change share one namespace and governance root;
4. the snapshot `ownerRef` resolves to an admitted resource-custodian `RoleDefinition`; the changed
   subject and scope resolve inside the snapshot, and the change owner resolves to an admitted role;
5. the change `sourceRef` is a member of its pinned `metadata.sourceRefs`; and
6. both detached digests verify.

Here `OrganizationSnapshot.metadata.ownerRef` means the role accountable for maintaining this frozen
Snapshot root, not legal ownership of the enterprise. `governanceRef` and `sourceRefs` are required,
digest-covered external identifiers. This Profile checks equality or membership where specified; it
does not authenticate those external identities, verify their signatures, or prove upstream Source
admission.

Federation is not implemented. Cross-namespace roots MUST fail with
`RESOURCE_COHERENCE_VIOLATION`; callers cannot infer a federation relationship from matching names.
An otherwise valid `supplier.status` change with zero, multiple, or non-`/status` deltas MUST fail with
`SUPPLIER_STATUS_DELTA_SET_INVALID`; unrelated deltas cannot be silently ignored.

## Operation semantics

| Operation | Required state transition |
|---|---|
| `add` | `not_applicable` to any applicable state |
| `remove` | any applicable state to `not_applicable` |
| `replace` | an applicable state to a distinct known value |
| `invalidate` | known to `not_observable` |
| `unknown_transition` | known or `not_observable` to `unknown` |

An unknown or non-observable after-state is not the string `"unknown"`. It causes tri-valued Profile
evaluation and an explicit discovery/Unknown path where a predicate could otherwise transfer.
A numeric or Boolean known status is unsupported Profile semantics and MUST NOT prove bounded
non-impact.

## Versioned applicability

Two strict predicate wire shapes are supported:

- `TransferPredicate{semanticType, afterValues}` is the immutable v0alpha1 shape;
- `ContextualApplicabilityPredicate` carries explicit
  `predicateVersion=oac.supplier.applicability/v0.2`, state/value atoms, optional subject/scope
  selectors, and optional relation types.

The legacy shape normalizes in memory to `afterState=known` with no contextual selectors. Parsing,
normalization, evaluation, and resealing MUST NOT add contextual fields to its source wire form or
change its detached digest.

The contextual predicate is one bounded conjunction. Subject selectors may constrain admitted subject
ID, node type, and domain. Scope selectors match IDs in `SemanticChangeSet.scopeRefs` using `all` or
`any` and declare whether missing context is `FALSE` or `UNKNOWN`. `scopeRefs` are context anchors; they
are not a global graph allowlist unless an edge/rule predicate explicitly consumes them. Relation-type
selectors apply only where a typed relation is evaluated.

Evaluation uses strong Kleene conjunction:

- a known mismatch makes the result `FALSE`, even when another atom is Unknown;
- with no False atom, a missing/candidate/disputed input that could change the result makes it
  `UNKNOWN`;
- only all-true atoms produce `TRUE`;
- retracted inputs are excluded.

Every declared dependency edge, ImpactRule, and UnknownTransitionDuty remains present in the
applicability-evaluation ledger, including a retracted source. Retraction contributes a
`FALSE/RETRACTED_SOURCE_EXCLUDED` source-authority atom and prevents that source from materializing a
path; it does not erase the source from the audit projection. Candidate/disputed source authority uses
`CANDIDATE_INPUT_NOT_AUTHORITY` in the predicate evaluation for every source kind. When that source is
a dependency edge, closure additionally places `CANDIDATE_EDGE_NOT_AUTHORITY` on the guarded path.
These two reason locations distinguish predicate authority from edge propagation authority.

Selector matching is not admission and cannot grant a role, principal, qualification, permission, or
effect. The predicate language contains no code, regex, model call, arbitrary attribute lookup, nested
Boolean expression, or vendor policy callback.

## Impact closure

The exact propagating relation set for Profile `oac.supplier.transfer/v0.2` is closed:

```text
business_dependency
contractual_dependency
financial_exposure
compliance_dependency
operational_dependency
```

`traceability`, `similarity`, and `correlation` are non-propagating. Implementations MUST NOT infer
propagation from a relation name, graph reachability alone, or another implementation's enum order.

- The changed admitted subject is the seed path. Candidate/disputed subjects remain Unknown and set
  `rootApplicabilityUnknown=true`; no rule or later admitted edge can upgrade that root authority.
  A retracted changed subject is not a legal active derivation root and fails before closure with
  `RETRACTED_SOURCE_EXCLUDED`. The seed is a closure anchor and does not implicitly emit an obligation.
- Only declared propagating relation types are considered.
- An edge transfers only when its versioned applicability result is `TRUE`. `FALSE` is not traversed;
  `UNKNOWN` may only form an explicit guarded path/duty.
- An Affected traversal additionally requires admitted endpoints and edge plus coverage under the
  completeness manifest. Candidate/disputed or uncovered inputs preserve Unknown.
- A simple path never revisits a node. All independent node-simple paths up to `maxDepth` remain
  observable. A path at the limit is truncated only when at least one next edge evaluates TRUE or
  UNKNOWN; predicate-false outgoing edges cannot manufacture truncation Unknown.
- An admitted contextual ImpactRule whose predicate is TRUE can create a rule-origin path and
  obligation. It cannot upgrade a candidate/retracted subject, target, role, or rule.
- A digest-covered `UnknownTransitionDuty` fires only when its own predicate evaluates UNKNOWN. Duties
  with the same semantic key aggregate all path/evaluation witnesses into one obligation.
- Every `prerequisiteObligationType` MUST resolve to exactly one obligation in the frozen closure and
  derive a verifier-visible order. Missing, ambiguous, cyclic, and same-role prerequisites are v0.2
  Profile errors; they MUST NOT disappear silently.
- `unaffected_proven` is change-relative, not admission of its target. Inside a complete manifest it
  may cover an admitted, covered node with no applicable path or with every route semantically closed
  by `FALSE`. A candidate/disputed or uncovered target requires an authoritative FALSE cut: admitted
  changed-subject root, admitted `TRUE` prefix, admitted `FALSE` frontier source, complete manifest,
  no bypassing route, and all decisive frontier evaluation IDs bound into the path. A candidate root
  cannot establish that proof. A downstream candidate source beyond an admitted cut contributes no
  authority. Missing paths under a partial/unknown boundary are never converted to non-impact;
  retracted targets emit an `out_of_declared_scope` path with origin `excluded` and reason
  `RETRACTED_SOURCE_EXCLUDED`.

Every non-seed dependency-origin Affected path produces obligation coverage through its target's
admitted owner role; a missing or non-admitted owner is `OBLIGATION_UNSATISFIED`. Rule-origin paths use
the rule's admitted required role. Seed work arises only from an explicit rule or duty.
Unknown paths with an explicit duty use that duty; other graph/boundary Unknown paths aggregate through
the manifest's admitted discovery role. A contextual aggregate explicitly declares its
`discoveryTargetRef`, `discoveryObligationType`, and `discoveryEvidence`; none is inferred from an
identifier. Legacy manifests without that triple retain the v0alpha1 per-target discovery behavior:
`obligationType=discover_dependency` and
`requiredEvidence=[dependency_admission_decision]`. A partial or unknown completeness manifest with
neither `knownGaps` nor `discoveryTargetRef` emits the canonical implicit gap target
`urn:oac:boundary:unobserved`; implementations MUST NOT invent a local sentinel identifier.

An admitted UnknownTransitionDuty that evaluates UNKNOWN requires an admitted target and admitted
required role. If its required role is candidate, disputed, retracted, or missing, derivation fails with
`UNKNOWN_TRANSITION_DUTY_INVALID` before prerequisite-order construction; an implementation cannot
silently drop the duty and report only a later secondary error.

An Affected-seed Plan with zero obligations is a valid no-op only when at least one
`bounded_non_impact` / `unaffected_proven` path has non-empty `evaluationRefs` and every referenced
evaluation is `FALSE`. Topology-only paths may individually carry no evaluation refs, but an empty or
purely topological closure does not prove a zero-obligation no-op.

Every edge, rule, and duty evaluation records exact source-field witnesses. Its identifier is the first
24 hexadecimal characters of SHA-256 over RFC 8785 bytes of:

```json
{
  "snapshotDigest": "<snapshot digest>",
  "changeDigest": "<change digest>",
  "changeSubjectRef": "<SemanticChangeSet subject id>",
  "sourceRef": "<edge, rule, or duty id>",
  "predicateVersion": "<version>",
  "result": "TRUE|FALSE|UNKNOWN",
  "reasonCodes": ["<sorted stable codes>"],
  "witnessRefs": ["<sorted exact source fields>"]
}
```

The wire ID is `urn:oac:mvp:evaluation:<24-hex>`. Path IDs use the same construction style and include
the ordered evaluation refs in addition to snapshot/change, target, state, edges/rules/duties, origin,
and truncation. Obligation IDs bind snapshot/change, semantic key, resolution state, and every sorted
supporting path ref. These formulas are normative for this experimental Profile rather than hidden
reference-code behavior.

### Applicability witness projection

All witness arrays are deduplicated and sorted by Unicode code point after canonical witness encoding.
The following decoded JSON Pointers are exact:

| Atom | Required pointer(s) |
|---|---|
| snapshot/change digest presence | `/digest` on each root |
| change authority | `/spec/admissionStatus` |
| semantic type | `/spec/semanticType` |
| status delta missing/non-unique | `/spec/deltas` |
| status after state/value | `/spec/deltas/{index}/after/state`, `/spec/deltas/{index}/after/value` |
| changed subject | `/spec/subjectRef` |
| scope membership | `/spec/scopeRefs` |
| missing subject/scope node | `/spec/nodes` |
| subject/scope node authority | `/spec/nodes/{index}/admissionStatus` |
| subject node type/domain | `/spec/nodes/{index}/nodeType`, `/spec/nodes/{index}/domainRef` |
| edge predicate/status/relation | `/spec/dependencyEdges/{index}/transferPredicate`, `/admissionStatus`, `/relationType` under that entry |
| contextual rule predicate/status | `/spec/impactRules/{index}/applicability`, `/admissionStatus` under that entry |
| legacy rule predicate | `/spec/impactRules/{index}` |
| duty predicate/status | `/spec/unknownTransitionDuties/{index}/applicability`, `/admissionStatus` under that entry |
| relation context absent on rule/duty | `/spec/dependencyEdges` |
| missing applicability source | `/spec` and `/spec/dependencyEdges` |

Resource identifiers in these witnesses are the exact root `metadata.id`; pointers are encoded through
the canonical witness grammar. Array indices refer to admitted raw array order and therefore cannot be
re-sorted before witness construction.

Subject-selector evaluation first tests `subjectRefs` against the change's `/spec/subjectRef`, then
tests the resolved node's `/admissionStatus`. A `candidate`, `disputed`, or `retracted` node terminates
that subject-node branch: `nodeType` and `domainRef` atoms are not evaluated and their pointers are not
included as witnesses. This prevents non-authoritative attributes from influencing either truth or the
evaluation identity.

### Path-prefix accounting

`maxPathPrefixes` counts every attempted `ImpactPath` materialization, immediately before its ID is
inserted into the derivation map. The deterministic accounting sequence is:

1. the subject seed;
2. each non-FALSE, node-simple dependency prefix in breadth-first order, with outgoing edges sorted by
   `edgeId`;
3. non-FALSE rules sorted by `ruleId`;
4. UNKNOWN duties sorted by `dutyId`;
5. explicit `knownGaps` sorted by value, or the one implicit boundary gap;
6. complete-boundary residual node results sorted by `nodeId`.

FALSE edges, retracted endpoints/sources, cycle revisits, and depth-over continuations do not spend a
prefix. A prefix at `maxDepth` does spend one even when it becomes Unknown/truncated. Exceeding the
budget fails before a partial report is returned. Set-owned input arrays, including `knownGaps`,
covered refs/relation types, scope refs, selector refs, required evidence/qualifications, and
prerequisite obligation types, MUST reject duplicates before accounting; duplicate inputs cannot buy
multiple budget entries that later collapse to one Path ID.

### ProfileDerivationReport projection

The report arrays are deterministic sequences:

- `applicabilityEvaluations` by `evaluationId`;
- `impactPaths` by `pathId`;
- `obligations` by `obligationId`;
- `requiredOrders` by `(predecessorRoleRef, successorRoleRef, roleWide, reasonRefs,
  dependencyReasonRefs, prerequisiteReasonGroups)`;
- `unresolvedRefs` as sorted unique strings.

`unresolvedRefs` contains every emitted Unknown path, only those UNKNOWN applicability evaluations
referenced by an emitted path, and the change resource ID when `rootApplicabilityUnknown=true`.
An unrelated UNKNOWN negative-control evaluation stays visible in the evaluation ledger but does not
become an unresolved organizational dependency merely by existing.

Within each item, every declared set projection is sorted unique; path `edgeRefs` remains traversal
order, and `prerequisiteReasonGroups` is an outer sorted-unique sequence of inner sorted-unique reason
sets. Report JCS bytes are compared before any Agent topology is selected.

## Plan-choice freedom

The Profile constrains obligations, admitted role/principal bindings, separation, required evidence,
role-level order, Unknown preservation, zero effects, and inclusion minimality. It does not prescribe
one WorkUnit topology.

A WorkUnit MAY combine obligations when every listed RoleInstance contributes at least one of them,
their union covers the WorkUnit exactly once at plan level, and the singular accountable RoleInstance
is one contributing member. Accountability here owns the combined zero-effect WorkUnit; it does not
transfer or replace each obligation's role-specific authority. A happens-before edge MUST correspond
only to required role pairs and carry exactly the reasons applicable to its concrete endpoints. A
dependency order binds its supporting path IDs across the full Cartesian product of obligation-bearing
WorkUnits for both roles. A prerequisite order binds its predecessor obligation, successor obligation,
and supporting path only to WorkUnits that carry those obligations. If both apply to one physical edge,
its reasons are their exact union; another dependency-matrix edge cannot cite prerequisite obligations
its endpoints do not carry. Topology splitting cannot discharge the contract. The derived role order
itself MUST be acyclic independently of the proposed WorkUnit graph. Extra, duplicate,
self-referential, dangling, over-bound, or arbitrary-reason order edges are invalid.

## Assurance boundary

The verifier is independent of compiler strategy, topology, hidden reasoning, and mutable planner
state. The reference compiler and verifier both call the same executable Profile oracle. Agreement
therefore does not detect a shared semantic defect in that oracle; Spec 003 requires a code-independent
implementation before portability or conformance claims.

Root applicability Unknown maps to certificate `UNKNOWN`. A known root with only bounded derived gaps
may map to `PROVISIONAL`; this distinction prevents a discovery plan from being mistaken for a known
business transition.
