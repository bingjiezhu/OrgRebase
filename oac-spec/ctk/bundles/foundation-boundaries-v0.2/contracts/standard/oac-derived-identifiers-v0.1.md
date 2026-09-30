# OAC Derived Identifiers and Witness References v0.1 — Proposed Draft

**Status**: experimental normative contract; implementation admission is tracked by Spec 003

**Scope**: legacy Supplier Profile identities, new compiler-owned identities, and witness references

**Normative language**: MUST, MUST NOT, SHOULD, SHOULD NOT, and MAY follow BCP 14

## 1. Purpose

Independent implementations cannot reproduce OAC artifacts if an identifier's hash preimage or a
witness delimiter is hidden in reference code. This document freezes the current legacy formulas,
defines a full-digest domain-separated scheme for new compiler-owned choices, and removes ambiguity
from `resourceId#/json-pointer` references.

An identifier proves deterministic derivation from a declared preimage. It does not authenticate the
preimage, confer authority, prevent a malicious producer from lying, or replace source admission.

## 2. Common canonicalization rules

Unless a section explicitly says otherwise:

1. A preimage is a JSON value valid under the OAC I-JSON boundary.
2. It is serialized with RFC 8785 JSON Canonicalization Scheme (JCS).
3. Object member order is the RFC 8785 UTF-16 code-unit order.
4. SHA-256 is applied to those UTF-8 canonical bytes.
5. Hexadecimal output uses lowercase ASCII.
6. Array order is semantic. JCS does not sort arrays.

When this document says an array is “sorted,” the producer MUST first remove exact duplicate strings
and then sort ascending by Unicode scalar-value lexicographic order. This legacy array rule is distinct
from RFC 8785's UTF-16 ordering for object member names. All current reason codes are ASCII, but witness
IDs need not be.

## 3. Legacy 96-bit identities

The legacy Supplier Profile takes the first 24 lowercase hexadecimal characters of SHA-256. That is a
96-bit truncation. It remains normative only for byte compatibility with existing v0alpha1/v0.2
resources. It MUST NOT be used as a security name, collision-resistance claim, authorization token, or
new cross-enterprise identifier scheme.

Changing any legacy formula requires a new Profile version. Existing frozen artifacts MUST NOT be
rewritten in place.

### 3.1 ApplicabilityEvaluation ID

The exact preimage is:

```json
{
  "snapshotDigest": "<sha256 digest or literal MISSING>",
  "changeDigest": "<sha256 digest or literal MISSING>",
  "changeSubjectRef": "<SemanticChangeSet spec.subjectRef>",
  "sourceRef": "<evaluated edge, rule, or duty id>",
  "predicateVersion": "<registered predicate version>",
  "result": "TRUE|FALSE|UNKNOWN",
  "reasonCodes": ["<sorted unique reason codes>"],
  "witnessRefs": ["<sorted unique witness refs>"]
}
```

`snapshotDigest` and `changeDigest` use the exact detached digest string. The literal string `MISSING`
is used when the corresponding in-memory digest is absent. The public helper does not silently sort its
arguments: the producing semantic procedure supplies `reasonCodes` and `witnessRefs` already sorted by
the rule in Section 2. A clean-room implementation MUST therefore freeze the array bytes as shown and
MUST NOT assume that JCS sorts arrays.

```text
hex24 = lowercase_hex(SHA256(JCS(preimage)))[0:24]
evaluationId = "urn:oac:mvp:evaluation:" + hex24
```

Example frozen vector:

```json
{
  "snapshotDigest": "sha256:76073e824054b6a6b7de66e2b728069dea6ba83164f47d116f9f6eb0d4b1fe65",
  "changeDigest": "sha256:69ec666f7453286983111ec0254f8c67213156561224e05d958eea8aedc8d40c",
  "changeSubjectRef": "alternative:acieries-savoie",
  "sourceRef": "rule:contextual-nuclear-continuity",
  "predicateVersion": "oac.supplier.applicability/v0.2",
  "result": "FALSE",
  "reasonCodes": [
    "APPLICABILITY_AFTER_VALUE_MISMATCH",
    "APPLICABILITY_SUBJECT_MISMATCH"
  ],
  "witnessRefs": [
    "change:SC-008#/digest",
    "change:SC-008#/spec/admissionStatus",
    "change:SC-008#/spec/deltas/0/after/state",
    "change:SC-008#/spec/deltas/0/after/value",
    "change:SC-008#/spec/scopeRefs",
    "change:SC-008#/spec/semanticType",
    "change:SC-008#/spec/subjectRef",
    "snapshot:veracier-proc01-contextual#/digest",
    "snapshot:veracier-proc01-contextual#/spec/impactRules/5/admissionStatus",
    "snapshot:veracier-proc01-contextual#/spec/impactRules/5/applicability",
    "snapshot:veracier-proc01-contextual#/spec/nodes/11/admissionStatus",
    "snapshot:veracier-proc01-contextual#/spec/nodes/13/admissionStatus",
    "snapshot:veracier-proc01-contextual#/spec/nodes/13/domainRef",
    "snapshot:veracier-proc01-contextual#/spec/nodes/13/nodeType"
  ]
}
```

The expected ID is:

```text
urn:oac:mvp:evaluation:0e3aa6ac2a7fc57c529b19d7
```

### 3.2 ImpactPath ID

The exact preimage is:

```json
{
  "snapshot": "<snapshot digest>",
  "change": "<change digest>",
  "target": "<targetRef>",
  "state": "affected|unaffected_proven|unknown|out_of_declared_scope",
  "edges": ["<edge refs in path traversal order>"],
  "rules": ["<rule refs in semantic path order>"],
  "evaluations": ["<evaluation refs in matching semantic path order>"],
  "duties": ["<duty refs in semantic path order>"],
  "origin": "<path origin>",
  "truncated": false
}
```

All ten members are always present; empty collections are `[]`. If an unsealed in-memory root reaches
the legacy helper, `snapshot` or `change` is JSON `null`; ordinary admitted Profile artifacts carry the
detached digest strings. The arrays are not set-sorted at hash time:

- dependency `edges` and their `evaluations` use root-to-target traversal order;
- a rule path currently uses an empty edge array and a singleton rule/evaluation array;
- a duty path currently uses a singleton evaluation and zero or one duty ref;
- a bounded non-impact proof uses its already sorted decisive evaluation-ref set;
- seed, topology-only, gap, and excluded paths may have empty arrays.

`reasonCodes` is deliberately not part of this legacy path preimage. Implementations MUST reproduce the
formula as written rather than add it.

```text
hex24 = lowercase_hex(SHA256(JCS(preimage)))[0:24]
pathId = "urn:oac:mvp:path:" + hex24
```

Example preimage:

```json
{
  "snapshot": "sha256:f4a1f14f6284c0a9355300ad8e42c49885b6a3e3c8fa9766c4ca5811dc116cf0",
  "change": "sha256:fc74047b43ad4a1231450db3519a2f2bb956188772e00df64058c4e303703928",
  "target": "supplier:forges-martelliere",
  "state": "affected",
  "edges": [],
  "rules": ["rule:insolvency-exposure"],
  "evaluations": ["urn:oac:mvp:evaluation:1d260d4d7125fb3204ce44f3"],
  "duties": [],
  "origin": "rule",
  "truncated": false
}
```

Expected ID:

```text
urn:oac:mvp:path:01dfe6ccceff9a61aeb1c639
```

### 3.3 CoverageObligation ID

Obligations are first grouped by the exact semantic key
`(origin, target, role, type, state)`. Supporting path refs are deduplicated and sorted using Section 2.
The exact preimage is:

```json
{
  "snapshot": "<snapshot digest>",
  "change": "<change digest>",
  "origin": "<obligation origin>",
  "target": "<targetRef>",
  "role": "<requiredRoleRef>",
  "type": "<obligationType>",
  "state": "affected|unknown",
  "paths": ["<sorted unique supporting path refs>"]
}
```

`domainRef` and `requiredEvidence` are not members of the current legacy identity preimage. They remain
semantically verified fields, but implementations MUST NOT silently add them to this hash.

```text
hex24 = lowercase_hex(SHA256(JCS(preimage)))[0:24]
obligationId = "urn:oac:mvp:obligation:" + hex24
```

Example preimage:

```json
{
  "snapshot": "sha256:f4a1f14f6284c0a9355300ad8e42c49885b6a3e3c8fa9766c4ca5811dc116cf0",
  "change": "sha256:fc74047b43ad4a1231450db3519a2f2bb956188772e00df64058c4e303703928",
  "origin": "dependency_path",
  "target": "ledger:supplier-exposure",
  "role": "role:finance-exposure",
  "type": "assess_dependency_impact",
  "state": "affected",
  "paths": ["urn:oac:mvp:path:4b638050545b15f30c157c4b"]
}
```

Expected ID:

```text
urn:oac:mvp:obligation:00b6233a370d4a75696b0527
```

## 4. Full-digest compiler-owned IDs

New RoleInstance, WorkUnit, and PlanDecision identities use this exact envelope. This is the envelope
implemented by the current reference compiler and exercised by `C0-ID-004`; adding Profile fields would
create a different identifier and is therefore prohibited under v1:

```json
{
  "scheme": "oac.id/sha256-rfc8785/v1",
  "kind": "<kind-token>",
  "roots": {
    "snapshotDigest": "sha256:<64hex>",
    "changeDigest": "sha256:<64hex>"
  },
  "body": {}
}
```

The kind token is one of `role-instance`, `work-unit`, or `plan-decision`. The digest and URN are:

```text
hex64 = lowercase_hex(SHA256(JCS(envelope)))
id = "urn:oac:id:sha256:v1:" + kind-token + ":" + hex64
```

Including both `scheme` and `kind` in the preimage is mandatory domain separation. The two root digests
bind the identifier to its source pair. Profile and wire versions are declared by the surrounding
CapabilityStatement and resource, not repeated inside this v1 hash envelope. `profileId`,
`profileVersion`, `compilerId`, and `compilerVersion` MUST NOT be added to this envelope. Compiler
versions change independently from the semantic identity contract and are not an external guessing
mechanism.

### 4.1 RoleInstance body

```json
{
  "roleDefinitionRef": "<id>",
  "principalRef": "<id>",
  "obligationRefs": ["<ids in compiler-defined canonical order>"]
}
```

### 4.2 WorkUnit body

```json
{
  "roleInstanceRefs": ["<sorted unique ids>"],
  "accountableRoleInstanceRef": "<id>",
  "obligationRefs": ["<ids in compiler-defined canonical order>"]
}
```

### 4.3 PlanDecision body

```json
{
  "subjectRef": "<id>",
  "inputClass": "<admission or decision input class>",
  "disposition": "included|excluded|unresolved",
  "reasonCodes": ["<sorted unique reason codes>"]
}
```

These are the exact current compiler projections, not every semantic field later present on the
RoleInstance or WorkUnit wire object. Before invoking the generic helper, a compiler MUST normalize
every set-like array according to its published compiler contract; the helper hashes the supplied body
without sorting it. New ID-affecting body fields require a new ID-scheme version. An extension MUST NOT
silently change a v1 preimage.

### 4.4 Frozen vector

For roots consisting of 64 `1` and 64 `2` hexadecimal digits and this body:

```json
{
  "roleDefinitionRef": "urn:a:reviewer",
  "principalRef": "urn:p:one",
  "obligationRefs": ["urn:o:1"]
}
```

the expected identifier is:

```text
urn:oac:id:sha256:v1:role-instance:3032a9b81c1fb0704fb0079f1fd4a0bc6d4b21b10e52de746d5145bae490b33e
```

### 4.5 Verification and migration

- The current reference compiler emits the v1 scheme; another compiler declares only the scheme it
  actually implements and has tested.
- Existing suffix-derived `urn:oac:mvp:role-instance:*`, `work-unit:*`, and `decision:*` values remain
  valid in already supported plans.
- A plan verifier MUST treat compiler-owned IDs as opaque unique anchors and verify referential and
  semantic integrity. It MUST NOT require a valid plural plan to reproduce the reference compiler's ID.
- Compiler conformance tests MAY recompute v1 IDs for artifacts emitted by a compiler that declares the
  v1 capability.
- The current `oac.phase-a/v0.1` `deriveIdentifier` contract binds this scheme through its operation
  specification and vectors; its compact CapabilityStatement has no `identifierSchemes` member. A
  successor capability/profile MAY add an explicit scheme field. A consumer MUST NOT infer the scheme
  by parsing `compilerVersion` or suffix shape.

This separation preserves plural topology: semantic verification does not turn one compiler's body
projection into the only valid OrganizationPlan.

## 5. Witness reference v1

### 5.1 Grammar

```text
witness-ref = encoded-resource "#" encoded-pointer
```

There is exactly one literal `#` delimiter. Parsers split once at that delimiter. Any `#` inside either
decoded component is percent-encoded as `%23`.

`encoded-resource` is the UTF-8 byte sequence of the resource ID, percent-encoded under RFC 3986. The
unescaped safe set is the URI unreserved set plus the following reserved characters; `%`, `#`, `[`, and
`]` are not safe:

```text
ALPHA / DIGIT / "-" / "." / "_" / "~" /
"!" / "$" / "&" / "'" / "(" / ")" / "*" / "+" / "," / ";" / "=" /
":" / "/" / "?" / "@"
```

This keeps common URN-like IDs and path-shaped resource IDs readable while encoding `#`, `%`, spaces,
brackets, and non-ASCII bytes when they occur in a resource ID.

`encoded-pointer` is the UTF-8 byte sequence of an RFC 6901 JSON Pointer. Its unescaped safe set is the
URI unreserved set plus `/` and `~`; reserved punctuation such as `#`, `%`, `?`, `:`, and sub-delimiters
is encoded. The decoded pointer is either empty, which identifies the complete JSON document, or begins
with `/`. Within pointer tokens, `~0` represents `~` and `~1` represents `/`; any other `~` escape is
invalid.

### 5.2 Canonical percent encoding

- Every encoded byte uses `%HH` with uppercase hexadecimal digits.
- A byte in the declared safe set MUST NOT be percent-encoded.
- `%` itself is encoded as `%25`.
- Decoding is strict UTF-8 and happens exactly once.
- Overlong, malformed, truncated, lowercase, or non-UTF-8 escapes are invalid.
- After decoding and RFC 6901 validation, re-encoding MUST reproduce the original byte string exactly.
- URI host case rules, dot-segment removal, Unicode normalization, and form-url-encoding rules do not
  apply.

Examples:

```text
change:SC-008#/spec/subjectRef
```

is both a legacy simple ref and a canonical v1 ref.

For decoded resource ID `urn:oac:knowledge#quality/%/供应商` and decoded pointer
`/spec/a~1b/~0token/#/%/值`, the canonical reference is:

```text
urn:oac:knowledge%23quality/%25/%E4%BE%9B%E5%BA%94%E5%95%86#/spec/a~1b/~0token/%23/%25/%E5%80%BC
```

The lowercase variant `%e4%be%9b` is rejected rather than normalized silently.

### 5.3 Compatibility

Existing witness refs whose resource component contains only the resource safe set and whose pointer
is already a valid safe-character RFC 6901 pointer remain byte-identical. Existing frozen refs that are
not canonical v1 MAY be consumed only under their legacy Profile rules. Migration creates a new
resource/Profile revision; it does not rewrite the old digest root.

## 6. Required conformance vectors

The current 24-case RequirementSet includes the three legacy positives, one RoleInstance v1 positive,
one Unicode/reserved witness positive, and one lowercase-percent negative. A successor A1
RequirementSet must add the following before making a broad identifier/witness compatibility claim:

- every legacy example preimage and expected ID in this document;
- v1 kind-domain separation for equal bodies;
- scheme, kind, root, body-field, scalar, and array-order sensitivity;
- full 64-hex output and lowercase syntax;
- empty and non-empty JSON Pointers;
- resource and pointer components containing `#`, `%`, `/`, `~`, spaces, and non-ASCII UTF-8;
- invalid `~`, malformed percent escapes, lowercase escapes, double decoding, lone surrogates, and
  invalid UTF-8;
- simple legacy references that must remain byte-identical.

## 7. References

- [RFC 8785 — JSON Canonicalization Scheme](https://www.rfc-editor.org/rfc/rfc8785)
- [RFC 6901 — JSON Pointer](https://www.rfc-editor.org/rfc/rfc6901)
- [RFC 3986 — URI Generic Syntax](https://www.rfc-editor.org/rfc/rfc3986)
- [RFC 6920 — Naming Things with Hashes](https://www.rfc-editor.org/rfc/rfc6920), used as design
  precedent for algorithm-identified hash names; OAC's URN is not an RFC 6920 `ni` URI.
