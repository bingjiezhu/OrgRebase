# Organizational Agent Contract Core v0.1 — Proposed Draft

**Status**: experimental implementation draft  
**Conformance status**: no implementation may claim OAC conformance from this draft alone  
**Normative language**: MUST, MUST NOT, SHOULD, SHOULD NOT, and MAY follow BCP 14

## 1. Scope

OAC defines the observable contract between admitted enterprise organization facts, a bounded demand,
a generated task organization, and independent plan/outcome assurance. It does not define model
reasoning, message transport, workflow execution, identity proof, policy evaluation, telemetry storage,
or physical enterprise schemas.

## 2. Layering

```text
enterprise/BACM-aligned sources
        ↓ admitted projection
OrganizationSnapshot + SemanticChangeSet
        ↓ replaceable compiler
OrganizationPlan
        ↓ runtime-neutral verifier
PlanCertificate
        ↓ controlled zero-effect lowering (implemented extension profile)
ZeroEffectRuntimeBundle + RuntimeLoweringReceipt
        ↓ separate runtime admission and execution (future profile)
OutcomeCertificate
```

BACM is an upstream alignment target. Agent Spec and Open Workflow are downstream lowering targets.
MCP, A2A, OASF, Agent Skills, IAM/policy systems, PROV, and OpenTelemetry remain external bindings.

## 3. Document classes

- `SOURCE`: admitted or explicitly candidate organization/demand facts.
- `PLAN`: compiler-produced choices and obligations; never a source of enterprise authority.
- `EVIDENCE`: immutable observations and certificates about one exact plan or outcome.
- `CONFORMANCE`: TCK manifests and implementation claims.
- `BENCHMARK`: research labels and cases; never Core source authority.

`SOURCE` here is an envelope/document class for inputs and producer-owned resources. It is not the
`S_n` Source fact root in the proposed five-stage Evolution Kernel and does not by itself grant
enterprise truth authority. For example, `RuntimeBinding` is SOURCE-class because it is supplied to
lowering/admission, yet it remains runtime input and can never establish organizational truth. An
`S_n` claim additionally requires exact Source admission and the declared authority-assurance boundary.

A bundle MUST be class-homogeneous. Cross-class packages retain separate roots. A compiler output MUST
NOT mutate or semantically promote a Source resource.

## 4. Resource envelope

Every top-level resource contains:

```json
{
  "apiVersion": "oac.dev/v0alpha1",
  "kind": "OrganizationSnapshot",
  "metadata": {
    "id": "urn:oac:example:snapshot:supplier",
    "namespace": "urn:oac:example",
    "revision": 1,
    "ownerRef": "urn:oac:example:role:owner",
    "governanceRef": "urn:oac:example:governance:1",
    "createdAt": "2026-08-23T00:00:00Z",
    "effectiveFrom": "2026-08-23T00:00:00Z",
    "effectiveTo": null,
    "sourceRefs": []
  },
  "spec": {},
  "digest": "sha256:..."
}
```

Unknown members are rejected unless an implemented extension profile explicitly owns them. Revisions
are immutable. Material changes create successors; a mutable `latest` alias cannot be a frozen root.
The top-level digest is a wire-integrity coordinate, not an admitted organization fact, so it remains
outside `metadata`.

`ownerRef`, `governanceRef`, and `sourceRefs` are declared identifiers inside the frozen envelope. For
an `OrganizationSnapshot`, `ownerRef` MUST resolve to an admitted `RoleDefinition` in that Snapshot and
means the resource-custodian/accountability role for the frozen root, not legal ownership of the
enterprise. `governanceRef` and `sourceRefs` remain pinned external declarations. V0alpha1 checks their
presence and defined cross-resource equality or membership; it does not authenticate external
identities, verify signatures, or decide that the Snapshot itself was properly admitted. That
Source-admission decision is an upstream governance precondition. A matching digest proves bytes, not
authority.

## 5. Canonicalization and digest

The detached digest projection is the complete JSON resource with the top-level `digest` member omitted.
It MUST be
valid I-JSON and is serialized using RFC 8785 JCS. The digest is lowercase SHA-256, encoded as
`sha256:<64 lowercase hex characters>`.

Array order is semantic unless the owning Kind explicitly declares set projection and normalization.
An implementation MUST reject duplicate object keys, NaN/Infinity, digest mismatch, and an unresolved
required reference.

## 6. MVP source semantics

`OrganizationSnapshot` freezes the exact organization scope, domains, knowledge/evidence locators,
business objects, admitted role definitions, principals and qualifications, typed dependency edges,
separation rules, and completeness declarations used for one compilation.

`SemanticChangeSet` freezes typed before/after deltas, subject identity, source provenance, effective
time, and evaluation scope. It cannot infer organization authority. In the single-enterprise MVP it
MUST share the snapshot namespace and governance root; federation semantics are not implemented.

Dependency status is one of `ADMITTED`, `CANDIDATE`, `DISPUTED`, or `RETRACTED`. Only admitted edges
with implemented transfer semantics may establish mandatory impact. Candidate/disputed edges may
create an explicit Unknown or discovery obligation but cannot prove affected, unaffected, qualified,
or authorized status.

An edge's existence does not mean that every change traverses it. Each propagating edge MUST bind a
Profile-owned `transferPredicate` that names the applicable semantic type and after-values. The Profile
MUST evaluate that predicate from the frozen change; a compiler-global status allowlist is not source
authority. A known false predicate does not traverse the edge and may support bounded non-impact only
inside an applicable complete manifest. A predicate that cannot be evaluated from an unknown value
preserves an explicit Unknown whenever the edge could otherwise transfer. A retracted
edge or rule contributes neither current impact nor discovery authority.

No reachable path proves impact only within the frozen graph. Bounded non-impact additionally requires
an applicable complete manifest. Otherwise the result is Unknown.

## 7. Obligation closure

For a frozen snapshot `S`, change `C`, and profile `P`, the Profile derives a finite obligation set
`Ω(S,C,P)`. Each implemented MVP obligation preserves:

- stable identity, target, domain, required role, and obligation type;
- one or more origin paths and required evidence duties;
- affected, Unknown, gap, and truncation state under the snapshot completeness boundary.

The compiler may refine or add plan-choice obligations. It MUST NOT delete, weaken, rename, or erase an
origin in the base obligation set.

## 8. OrganizationPlan

A plan binds exact source/change digests and contains task-level RoleInstances, qualified principal
bindings, WorkUnits, obligation coverage, happens-before constraints, decision/separation gates,
consideration decisions, unresolved items, and a scoped minimality claim.

A RoleInstance narrows an admitted RoleDefinition for one demand. It cannot create a new authoritative
role, principal, qualification, or delegation. A WorkUnit declares organizational intent and effect
ceiling but is not a workflow-runtime instruction.

A Profile MAY allow a WorkUnit to combine obligations from multiple contributing RoleInstances. Every
listed RoleInstance MUST contribute at least one obligation, their union MUST cover the WorkUnit's
obligations, and its accountable RoleInstance MUST be one such contributor. This WorkUnit-level
accountability does not replace each obligation's role-specific authority. A Profile that requires
stronger all-obligation accountability MUST declare the compatible wire rule explicitly.

MVP plans are `planned` or `guarded_unresolved`. Infeasibility and abstention are future-profile
semantics, not v0alpha1 wire values. A Shadow plan MUST NOT contain an external-effect WorkUnit.

## 9. Plural-valid plan semantics

The standard does not define one golden topology. For a frozen constraint set `K`, a candidate plan `p`
is acceptable only when:

```text
Accept_K(p) =
  WellFormed(p)
  ∧ FrozenRootsMatch(p)
  ∧ CoversMandatoryObligations_K(p)
  ∧ QualificationAndAuthorityValid_K(p)
  ∧ SeparationValid_K(p)
  ∧ EvidenceDutiesCovered_K(p)
  ∧ PartialOrderValid_K(p)
  ∧ ForbiddenEffectsAbsent_K(p)
  ∧ UnknownPreserved_K(p)
  ∧ MinimalityProfileSatisfied_K(p)
```

Witness plans demonstrate existence but do not enumerate the full valid set. Topological or principal
differences alone do not make a plan invalid.

## 10. Compiler-independent plan assurance

A verifier consumes only frozen wire artifacts, public registries, and declared Profile rules. It MUST
NOT consume compiler hidden reasoning or mutable implementation state. It returns dimensioned results,
stable reason codes, bounded witnesses, and one of `ACCEPT`, `REJECT`, `PROVISIONAL`, or `UNKNOWN`.
The v0alpha1 reference compiler and verifier intentionally share one public executable Supplier
Profile oracle. This proves compiler/topology independence, not semantic independence of that oracle;
a second Profile implementation is a separate conformance gate.

`PROVISIONAL` applies only to a Profile-authorized guarded plan with named pending evidence. `UNKNOWN`
is not acceptance. Plan assurance is not evidence of runtime completion or business outcome.

## Semantic validation rules

JSON Schema is the structural layer only. Every registered semantic rule MUST additionally publish a
stable `ruleId`, `ruleVersion`, language-neutral scope and quantifier, normative statement/reference,
and stable failure reason code. A conforming implementation evaluates those rules from their published
semantics; a Python class or function name is never the normative procedure.

The development registry MAY colocate non-normative `implementationBindings` that point to reference
entrypoints. Such bindings are excluded from the normative registry projection and its future identity
digest. An implementation MUST NOT infer a missing semantic rule by reverse-engineering another
implementation binding.

For `RuntimeLoweringReceipt`, `PRODUCED` requires one bundle reference and no reason codes, while
`BLOCKED` requires at least one reason and forbids a bundle reference. The complete lowering procedure
is owned by `standard/oac-runtime-lowering-v0.1.md`; this cross-field rule does not imply runtime
admission, execution, or outcome evidence.

## 11. MVP claim firewall

The v0.1 reference tools and exploratory supplier cases prove only contract mechanics. They do not
establish an open-standard consensus, a complete C0/C2/C4 claim, enterprise effectiveness, data-label
validity, runtime portability, real-world completeness, production safety, or a world-first result.
