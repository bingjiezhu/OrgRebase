# OAC controlled runtime lowering v0.1

**Status**: experimental G1a contract  
**Profile**: `oac.runtime-lowering/zero-effect/v0.1`  
**Claim boundary**: local, deterministic, zero-effect lowering only

## 1. Purpose

This contract defines the first controlled projection from an accepted `OrganizationPlan` into a
runtime-neutral serial bundle. It closes one narrow gap: a Plan that is valid as an organizational
contract still needs exact identity, handler, evidence, and ordering bindings before any runtime can
consider it.

The projection is not execution. A produced `ZeroEffectRuntimeBundle` still carries
`requiresRuntimeAdmission: true`; neither the bundle nor its `RuntimeLoweringReceipt` grants a tool,
agent, service, or human permission to run. This version contains no prompt, command, credential,
network target, write target, AgentTeams transport, OrgRebase adapter, execution result, outcome
certificate, or enterprise-effect claim.

## 2. Registered resources

The generated `runtime-lowering-semantic-validation-rules.json` registry publishes reference-language
bindings for this extension. Its separately digest-bound
`normative-runtime-lowering-semantic-validation-rules.json` projection contains only
implementation-neutral rules. These extension registries do not widen the frozen Core semantic-rule
coordinate.

### 2.1 `RuntimeBinding` — source

A runtime owner supplies one sealed binding for one exact Plan and verifier result:

- `subjectPlanRef` binds the exact sealed `OrganizationPlan`;
- `subjectCertificateRef` binds the exact sealed PlanCertificate independently recomputed by the
  lowerer; a binding made for another verifier result is not reusable;
- `roleBindings` maps every Plan `roleInstanceRef` to the exact Plan `principalRef`, one opaque
  `runtimeSubject`, and the exact canonical set of `capabilityRefs` required by that role's handlers;
- `handlerBindings` maps every derived `obligationType` to a content-bound `handlerRef`,
  `handlerDigest`, required `capabilityRef`, exact aggregate `evidenceOutputRefs`, and
  `effectCeiling: zero_effect`;
- `effectCeiling` is `zero_effect`; and
- `targetWrites` is `0`.

The binding does not self-admit. The lowering caller MUST supply a runner-owned allowlist of admitted
binding digests. An allowlist MUST NOT be read from the Plan, certificate, binding, process
environment, or a produced bundle.

### 2.2 `ZeroEffectRuntimeBundle` — plan

A produced bundle binds the exact Snapshot, Change, Plan, recomputed certificate, and admitted
RuntimeBinding. It carries:

- one `obligationContractDigest` over projection coordinate
  `oac.obligation-contract/topology-free/v0.1`, the exact root pair, and the topology-free obligation
  projection;
- one `topologyDigest` over RoleInstances, WorkUnits, and immediate happens-before edges;
- one canonical serial step per WorkUnit;
- `effectCeiling: zero_effect`;
- `targetWrites: 0`; and
- `requiresRuntimeAdmission: true`.

The two digests intentionally separate contract equality from topology equality. Two accepted Plans
for the same roots MAY have the same obligation-contract digest and different topology digests.
The projection coordinate is not a Supplier or other business Profile identity; it names only this
implementation-neutral digest shape.

Standalone bundle admission can validate the closed resource, canonical step order, stable role
projections, and internal handler/evidence consistency. Without dereferencing the bound Plan it cannot
prove that those fields are the Plan's exact projection. That claim belongs to the contextual lowering
relation over all six inputs and the resulting receipt.

### 2.3 `RuntimeLoweringReceipt` — evidence

Every domain-valid lowering request returns one sealed receipt with status `PRODUCED` or `BLOCKED`.

- `PRODUCED` binds exactly one `bundleRef` and has no reason codes.
- `BLOCKED` has one or more stable reason codes and MUST NOT carry `bundleRef`.
- all receipts state `proofScope: lowering_only`, `runtimeInvoked: false`,
  `runtimeAdmissionPerformed: false`, and `targetWrites: 0`.

The receipt proves only which exact inputs were lowered or blocked by this procedure. It is not an
execution receipt or an `OutcomeCertificate`.

## 3. Inputs and admission

The operation consumes exactly:

1. one `OrganizationSnapshot`;
2. one `SemanticChangeSet`;
3. one `OrganizationPlan`;
4. one supplied sealed `PlanCertificate`;
5. one sealed `RuntimeBinding`; and
6. a non-resource, runner-owned collection of admitted RuntimeBinding digests.

Malformed JSON, an unknown Kind, a missing detached digest, any supplied-resource digest mismatch, or
failure to construct one of these registered resources is a protocol/admission error. Before
reverification or any domain reason is computed, the lowerer MUST run detached-digest verification on
the Snapshot, Change, Plan, supplied PlanCertificate, and RuntimeBinding. It MUST NOT treat a claimed
digest as an alias for different supplied bytes. Admission error is not a `BLOCKED` domain result and
produces no OAC receipt. Error detail MUST identify at most the registered Kind, not the mismatching
payload. A CLI MAY return non-zero for that class. Once registered resources are admitted, `BLOCKED`
is an auditable result and SHOULD return exit status zero.

RuntimeBinding, ZeroEffectRuntimeBundle, and RuntimeLoweringReceipt MUST each carry the registered
ResourceRef kinds in one namespace with non-null owner/governance and exact ordered
`metadata.sourceRefs`. In particular, bundle `certificateRef` names PlanCertificate and a PRODUCED
receipt `bundleRef` names ZeroEffectRuntimeBundle.

## 4. Mandatory reverification gate

The lowerer MUST call the registered Plan verifier again with the exact Snapshot, Change, and Plan. It
MUST compare the supplied certificate and recomputed sealed certificate as complete JSON resources,
including detached digest, metadata, roots, dimensions, reasons, restrictions, unresolved refs,
certifier identity, and build identity.

Lowering is eligible only if all of these predicates hold:

```text
suppliedCertificate == recomputedSealedCertificate
recomputed.verdict == ACCEPT
recomputed.restrictions == []
recomputed.unresolvedRefs == []
forall dimension: dimension.verdict == PASS
```

A re-sealed `ACCEPT` certificate with any altered field is not trusted. A certificate from another
accepted Plan is not reusable. `PROVISIONAL`, `UNKNOWN`, and `REJECT` are never lowerable in v0.1.

## 5. Binding relation

For the exact Plan, the following relation MUST hold:

1. the binding roots pin the exact Plan and exact recomputed PlanCertificate, and its metadata envelope
   matches the Snapshot;
2. `roleBindings.roleInstanceRef` is a duplicate-free exact set equal to Plan RoleInstances;
3. every bound principal equals its Plan RoleInstance principal;
4. two different principals never resolve to the same `runtimeSubject`;
5. `handlerBindings.obligationType` is a duplicate-free exact set equal to the Plan obligation types;
6. each handler's evidence refs equal the UTF-8-byte-sorted union of required evidence for that type;
7. every RoleInstance capability set exactly equals the UTF-8-byte-sorted set required by its owned
   obligation handlers; missing capabilities and extra authority such as `admin` both fail;
8. the binding digest is valid and appears in the runner-owned allowlist; and
9. the Plan, every RoleInstance, every WorkUnit, the binding, and every handler remain zero-effect with
   zero target writes.

Missing and extra bindings are equally invalid. A binding is not made complete by silently dropping
Plan work or by inventing runtime work.

### 5.1 Lowering projection closure

An ACCEPT verifier result does not imply that every future runtime projection is representable. Before
bundle construction, the lowerer MUST independently require:

- at least one WorkUnit;
- duplicate-free, resolving RoleInstance and obligation refs consumed by lowering;
- an accountable RoleInstance present in each WorkUnit role set;
- duplicate-free obligation required-evidence sets; and
- each WorkUnit `evidenceOutputs` set to equal exactly the union required by its obligations.

Failure is domain `BLOCKED/LOWERING_PLAN_PROJECTION_UNSUPPORTED`, not a partial bundle and not an
uncaught bundle-model exception. This gate does not rewrite the Plan verifier's frozen acceptance
relation; it declares the narrower representable fiber of G1a lowering.

## 6. Canonical serial lowering

The lowerer projects the Plan WorkUnit DAG using Kahn's algorithm:

1. initialize the ready set with every zero-indegree WorkUnit;
2. order the ready set by ascending raw UTF-8 bytes of `workUnitId`;
3. remove the first item, emit one step, decrement its successors, and repeat;
4. order successors and newly ready items by the same byte relation; and
5. block with `LOWERING_ORDER_CYCLE` unless every WorkUnit is emitted exactly once.

Each emitted step MUST contain only:

- the zero-based `stepIndex` and exact `workUnitRef`;
- exact role-instance/principal/runtime-subject projections for all WorkUnit roles;
- the exact accountable role-instance/principal/runtime-subject projection;
- exact obligation refs;
- one content-bound handler projection per obligation;
- exact WorkUnit evidence output refs;
- exact immediate predecessor WorkUnit refs;
- `effectCeiling: zero_effect`; and
- `targetWrites: 0`.

Set-like projections use ascending raw UTF-8 byte order. The topology digest canonicalizes nested
RoleInstance obligation/qualification refs, WorkUnit role/obligation/evidence refs, and order-edge
reason refs independently; permutations that preserve membership therefore preserve the topology
digest. The bundle MUST NOT include an executable
prompt, shell command, tool command, credential, secret, bearer token, endpoint write instruction, or
an extension map capable of carrying those values.

## 7. Stable reason codes

The v0.1 lowerer uses the append-only core registry. Its lowering codes are:

| Code | Meaning |
|---|---|
| `LOWERING_CERTIFICATE_MISMATCH` | supplied and recomputed sealed certificates differ |
| `LOWERING_VERDICT_NOT_ACCEPT` | recomputed verdict is not `ACCEPT` |
| `LOWERING_RESTRICTIONS_PRESENT` | recomputed restrictions are non-empty |
| `LOWERING_UNRESOLVED_REFS_PRESENT` | recomputed unresolved refs are non-empty |
| `LOWERING_DIMENSION_NOT_PASS` | a recomputed dimension is not `PASS` |
| `LOWERING_BINDING_NOT_ADMITTED` | binding digest is not runner-admitted |
| `LOWERING_INPUT_DIGEST_INVALID` | a non-binding sealed input digest is missing or does not match canonical bytes; admission error, no receipt |
| `LOWERING_BINDING_DIGEST_INVALID` | binding digest is missing or does not match canonical bytes; admission error, no receipt |
| `LOWERING_BINDING_ROOT_MISMATCH` | binding Plan/certificate root or authority envelope differs |
| `LOWERING_BINDING_INCOMPLETE` | role mapping is missing, duplicate, or extra |
| `LOWERING_PRINCIPAL_MISMATCH` | bound and selected principals differ |
| `LOWERING_RUNTIME_SUBJECT_COLLISION` | different principals collapse to one subject |
| `LOWERING_HANDLER_INCOMPLETE` | handler type/evidence mapping is not exact |
| `LOWERING_CAPABILITY_MISSING` | a required handler capability is absent |
| `LOWERING_CAPABILITY_SET_MISMATCH` | role capabilities are not the exact canonical required set, including extras |
| `LOWERING_EFFECT_EXPANSION` | an admitted value exceeds the zero-effect ceiling |
| `LOWERING_ORDER_CYCLE` | the WorkUnit graph cannot be fully topologically emitted |
| `LOWERING_PLAN_PROJECTION_UNSUPPORTED` | an admitted Plan is not exactly representable by the closed G1a step projection |

Except for the explicitly admission-level digest-invalid codes, simultaneously true domain reasons
are returned as a sorted unique set. Implementations MUST NOT turn a failure into a partial bundle.

## 8. Determinism

For identical admitted input resources, identical runner allowlist membership, and the same normative
verifier build, the reference lowering result MUST have identical RFC 8785 bytes and detached digests.
The algorithm must not depend on wall-clock time, filesystem iteration order, locale, network state,
runtime discovery, model output, or random values.

## 9. Open boundaries

This version deliberately leaves the following work open:

- a separately governed runtime-admission protocol;
- an AgentTeams, Codex, Claude, MCP, or OrgRebase transport adapter;
- safe-twin execution and execution receipts;
- side-effect policies above zero-effect;
- cancellation, retry, compensation, concurrency, and scheduling semantics;
- `OutcomeCertificate` and enterprise-effect measurement;
- clean-room cross-implementation conformance for lowering; and
- promotion of repeated outcomes into governed skills.

No implementation may infer any of these claims from a G1a bundle or receipt.
