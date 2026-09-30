# OAC CTK stdio adapter protocol v1

**Protocol version**: `oac.ctk.stdio/v1`  
**Status**: Phase A frozen experimental wire protocol

## 1. Process boundary

The runner starts a fresh adapter process for every request, writes exactly one JSON object to stdin,
closes stdin, and reads exactly one JSON object from stdout. Diagnostics may be written to stderr.
The process MUST NOT require the CTK bundle directory, case identifier, expectation, fixture class, or
requirement identifier. The runner sends only the operation and its semantic input.

The current runner uses a new empty temporary working directory and an OS/locale/PATH environment
allowlist for every invocation. It does not pass `PYTHONPATH` or arbitrary caller variables. This is an
expectation-leakage control, not a complete filesystem/network/syscall sandbox and not proof of
implementation independence. Each stdout/stderr stream and response JSON depth are bounded; duplicate
keys and values outside RFC 8785 I-JSON are invalid adapter output.

`maxRequestBytes` is the independent wire-request ceiling; it is not `maxCaseBytes` (the latter bounds
an artifact containing metadata and expectations). The runner refuses to start a SUT when its encoded
request exceeds that ceiling. An adapter reads at most `maxRequestBytes + 1`; an over-limit direct
invocation terminates without a partial domain result. `maxJsonDepth` counts the root value as depth 1
and applies to the request, operation input, decoded `canonicalize` value, and response.

The request fields are closed:

```json
{
  "protocolVersion": "oac.ctk.stdio/v1",
  "requestId": "request-1",
  "operation": "canonicalize",
  "payload": {}
}
```

A completed response is:

```json
{
  "protocolVersion": "oac.ctk.stdio/v1",
  "requestId": "request-1",
  "sutStatus": "COMPLETED",
  "result": {}
}
```

An incomplete response replaces `result` with `error: {"code": "..."}` and uses one of
`UNSUPPORTED`, `RESOURCE_EXHAUSTED`, or `ERROR`. Adapter timeout and crash are observations made by the
runner and are not OAC domain verdicts.

The Phase A error taxonomy is closed:

- an operation payload that violates its frozen CTK shape, type, enum, uniqueness, or closed-field
  contract uses `ERROR/CTK_INPUT_INVALID`;
- `canonicalize` bytes that violate JSON grammar or contain duplicate object keys use
  `ERROR/CORE_SCHEMA_INVALID`;
- a syntactically valid JSON value outside the supported finite RFC 8785 I-JSON number/string domain
  uses `ERROR/NON_I_JSON`;
- a structurally valid witness operation with a non-canonical witness/JSON-Pointer spelling uses
  `ERROR/APPLICABILITY_WITNESS_MISMATCH`;
- a published resource ceiling uses
  `RESOURCE_EXHAUSTED/CONFORMANCE_RESOURCE_PROFILE_EXCEEDED` with no partial result.

Reason-code choice is therefore part of conformance. A host-language exception class or a reference
implementation's historical behavior is not normative.

### 1.1 `NonBlankString/v1`

Every protocol field described as non-blank uses one frozen lexical primitive. A value is non-blank
when it contains at least one Unicode scalar outside this exact 25-code-point set:

```text
U+0009..U+000D, U+0020, U+0085, U+00A0, U+1680,
U+2000..U+200A, U+2028, U+2029, U+202F, U+205F, U+3000
```

This is the [Unicode 17.0 White_Space property](https://www.unicode.org/Public/17.0.0/ucd/PropList.txt)
frozen into `NonBlankString/v1`; later Unicode releases do not silently change v1. U+001C..U+001F and
U+FEFF are therefore non-blank discriminators. Adapters, runners, and schemas MUST use the explicit
set and MUST NOT delegate this decision to a host `isspace`/`IsSpace` predicate or `\s`/`\S` regex
dialect.

## 2. Capability statement

`operation=capabilities` has an empty payload. A track is scoped by the complete tuple
`role × operation × profileId × profileVersion × wireVersion`. Phase A core cases use role
`semantic-kernel`, Profile `oac.phase-a` version `v0.1`, and this wire version. Missing a core
operation makes its required cases fail; it never converts them to skipped cases.

```json
{
  "implementationId": "example.cleanroom",
  "implementationVersion": "0.1.0",
  "adapterProtocolVersion": "oac.ctk.stdio/v1",
  "tracks": [{
    "role": "semantic-kernel",
    "operation": "canonicalize",
    "profileId": "oac.phase-a",
    "profileVersion": "v0.1",
    "wireVersion": "oac.ctk.stdio/v1"
  }]
}
```

## 3. Operations

### 3.1 `canonicalize`

Payload: `{"rawBase64": "..."}`. The bytes decode to one JSON value. The adapter MUST reject duplicate
object keys, invalid UTF-8/Unicode scalar values, and values outside the RFC 8785 I-JSON domain. Every
finite IEEE-754 binary64 value is in the canonicalization number domain. In particular,
`9007199254740992` canonicalizes to those same bytes, as required by RFC 8785 Appendix B; safe-integer
limits apply only to fields whose application schema declares an integer contract. It returns RFC
8785 bytes and their SHA-256:

```json
{
  "canonicalBase64": "...",
  "digest": "sha256:<64 lowercase hex>"
}
```

JSON syntax/duplicate-key failures (including the non-JSON tokens `NaN` and `Infinity`) use
`ERROR/CORE_SCHEMA_INVALID`; values that parse as JSON but are not in the finite JCS I-JSON domain use
`ERROR/NON_I_JSON`. Exceeding `maxJsonDepth` uses resource exhaustion. Object member order follows
UTF-16 code units.

### 3.2 `deriveIdentifier`

Payload has `identifierKind` and a `fields` object. `evaluation`, `path`, and `obligation` use the exact
legacy formulas in `standard/oac-derived-identifiers-v0.1.md`. `role-instance`, `work-unit`, and
`plan-decision` use the full-digest v1 envelope in that document. Result:
`{"identifier": "urn:oac:..."}`.

### 3.3 `witness`

- encode payload: `{"mode":"encode","resourceId":"...","pointer":"/decoded/pointer"}`;
- decode payload: `{"mode":"decode","witnessRef":"..."}`.

Encoding returns `witnessRef`; decoding returns `resourceId` and decoded RFC 6901 `pointer`. Alternate
percent spellings, lowercase percent hex, malformed JSON Pointer escapes, invalid UTF-8, or more than
one literal `#` use `ERROR/APPLICABILITY_WITNESS_MISMATCH`. Unknown mode, missing/extra fields, wrong
types, overlong values, and a resource ID that is blank under `NonBlankString/v1` are CTK input
errors during encode. A decode whose canonical percent component resolves to a blank resource ID uses
`ERROR/APPLICABILITY_WITNESS_MISMATCH`; it is an invalid witness, not a malformed operation shape.

### 3.4 `strongKleene`

Payload: `{"operator":"all|any","values":["TRUE|FALSE|UNKNOWN", ...]}`. Result is one `result`.
For `all`, `FALSE` dominates `UNKNOWN`, then `TRUE`; the empty conjunction is `TRUE`. For `any`,
`TRUE` dominates `UNKNOWN`, then `FALSE`; the empty disjunction is `FALSE`.

### 3.5 `closureMicro`

This operation freezes a topology-neutral semantic kernel, not the full Supplier Profile and not an
Agent topology compiler.

Input fields are closed:

- `root`: declared, non-retracted node ID;
- `maxDepth` and `maxPathPrefixes`: positive integers no greater than the published
  `maxSemanticDepth` and `maxPathPrefixes` ceilings;
- `nodes`: unique `{id, admission}` where admission is `admitted`, `candidate`, `disputed`, or
  `retracted`, with count no greater than `maxNodes`;
- `edges`: unique `{id, source, target, result, admission, covered}`; endpoints resolve to nodes,
  `result` is `TRUE|FALSE|UNKNOWN`, `covered` is boolean, and count is no greater than `maxEdges`.

Exceeding one of those published limits returns resource exhaustion. A retracted root is an invalid
semantic input rather than a candidate/Unknown authority source.

Semantics:

1. The root path has state `affected` only for an admitted root; a candidate or disputed root is
   `unknown` with
   `CANDIDATE_INPUT_NOT_AUTHORITY`.
2. Edges are considered in ascending ID order. Every node-simple path is retained. Retracted edges or
   targets and cycle-forming continuations are excluded.
3. `FALSE` is not traversed and therefore does not consume a path prefix. It emits a `falseFrontier`
   even when its outgoing edge is observed from a retained prefix already at `maxDepth`. Such a
   boundary frontier may have `len(edgeRefs) = maxDepth + 1`. Its `authoritative` flag is computed from
   the prefix state before any truncation marker and is true only when that prefix is affected, the
   edge and both endpoints are admitted, and `covered=true`.
4. A non-false continuation is `affected` only when its prefix is affected, its result is `TRUE`, the
   edge and both endpoints are admitted, and it is covered. Every other continuation is `unknown`.
5. `UNKNOWN`, non-admitted edge, non-admitted endpoint, and uncovered edge add respectively
   `APPLICABILITY_INPUT_UNKNOWN`, `CANDIDATE_EDGE_NOT_AUTHORITY`,
   `CANDIDATE_INPUT_NOT_AUTHORITY`, and `GRAPH_COVERAGE_PARTIAL`.
6. `maxDepth` limits only non-false propagation. At exactly `maxDepth`, a remaining non-false,
   non-retracted, node-simple continuation marks the current path `unknown`, `truncated=true`, and adds
   `IMPACT_SEARCH_TRUNCATED`; it is not expanded. FALSE frontiers from the same prefix remain recorded
   under Rule 3.
7. The seed counts as one path prefix; FALSE frontiers do not. Exceeding `maxPathPrefixes` returns
   `RESOURCE_EXHAUSTED/CONFORMANCE_RESOURCE_PROFILE_EXCEEDED` and no partial result.
8. Paths are sorted by `(edgeRefs, targetRef, state)`, false frontiers by `(edgeRefs, edgeId)`, and
   reason/unresolved sets lexicographically.

The completed result is a `ClosureMicroReport` with `rootRef`, `paths`, `falseFrontiers`, and
`unresolvedRefs`. It contains no RoleInstance, WorkUnit, principal choice, or decision topology.

### 3.6 `resourceCheck`

The payload maps published resource-profile dimension names to observed non-negative integers. Unknown
dimensions are invalid. Values at the ceiling return `COMPLETED` with `withinProfile=true`; values over
the ceiling return `RESOURCE_EXHAUSTED/CONFORMANCE_RESOURCE_PROFILE_EXCEEDED` and no partial result.

## 4. Scoring boundary

The runner owns `caseOutcome`; the adapter owns `sutStatus`; an OAC verifier operation would own
`domainVerdict`. These states MUST NOT be substituted for one another. Fixed verifier input has one
expected domain verdict. Multiple legal Agent/Plan topologies are represented as multiple artifacts
or constraint checks, never as a set of mutually inconsistent acceptable verdicts.
