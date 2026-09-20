# OAC Conformance Protocol v0.1 — Phase A Proposed Draft

**Status**: experimental; the currently executable wire and bundle format are frozen as
`oac.ctk.stdio/v1` and `oac.ctk.bundle/v0alpha1`

**Current claim ceiling**: code-independent Phase A harness mechanics for the exact 24-case bundle.
This is not complete OAC conformance, an independent Supplier implementation, clean-room evidence,
organizational independence, certification, security validation, or enterprise validation.

**Normative language**: MUST, MUST NOT, SHOULD, SHOULD NOT, and MAY follow BCP 14.

## 1. What Phase A freezes

The current Phase A surface is deliberately smaller than the future OAC conformance program:

| Surface | Current exact status | Not established |
|---|---|---|
| bundle | self-contained directory bundle, closed `v0alpha1` manifest, JCS ledger identity | archive extraction, signatures, publication service |
| requirements | 24 required cases, zero not-scored cases | complete Core/Profile requirement coverage |
| capability | `role × operation × profileId × profileVersion × wireVersion` tracks | a global OAC-compatible capability |
| wire | fresh process, one JSON request/response over stdio | streaming, HTTP, sessions, runtime agents |
| operations | canonicalization, IDs, witness, Strong Kleene samples, micro closure, resource check | full Supplier derive/verify/compile |
| result | layered case/SUT observations; no domain verdict in current cases | certification or business correctness |
| independence | runner package has no `oac` dependency/import | an independent semantic implementation |

The `closureMicro` operation is a topology-neutral microkernel. It tests a small public graph-closure
relation and contains no RoleInstance, WorkUnit, principal selection, or PlanDecision. It is not the
complete Supplier Profile derivation and MUST NOT be reported as such.

All fields described as non-blank use `NonBlankString/v1`: at least one scalar MUST fall outside the
exact set `U+0009..U+000D, U+0020, U+0085, U+00A0, U+1680, U+2000..U+200A, U+2028, U+2029,
U+202F, U+205F, U+3000`. This is the
[Unicode 17.0 White_Space set](https://www.unicode.org/Public/17.0.0/ucd/PropList.txt) frozen for this
contract. U+001C..U+001F and U+FEFF are deliberately non-blank. A host whitespace predicate or regex
`\s`/`\S` dialect is not normative; JSON Schema only recommends an ECMA-262 pattern dialect, so the
published schemas use the explicit class.

## 2. Result layers

Three state spaces remain separate:

```text
caseOutcome   runner comparison result
sutStatus     adapter/process observation
domainVerdict OAC verifier result, when a future verify operation completes
```

The current runner can emit these case outcomes:

```text
PASS | FAIL | NOT_SCORED | HARNESS_ERROR
```

Future differential and multi-verifier work reserves `NOT_RUN` and `INDETERMINATE`. They MUST NOT be
invented by mapping a current adapter status.

The stdio adapter response can emit:

```text
COMPLETED | UNSUPPORTED | RESOURCE_EXHAUSTED | ERROR
```

The runner may additionally observe `TIMEOUT` or `CRASH` when no valid adapter response exists.
`HARNESS_ERROR` means the harness could not perform a valid comparison, for example because the
bundled case/expectation shape is invalid. OAC `UNKNOWN` is a future `domainVerdict`; it MUST NOT be
used for a harness, process, or resource error.

A future fixed verifier request MUST yield exactly one of:

```text
ACCEPT | REJECT | PROVISIONAL | UNKNOWN
```

when `sutStatus=COMPLETED`. Plan plurality belongs to distinct legal Plan/topology artifacts, never to
an array of acceptable verdicts for one fixed input.

## 3. Current CTK bundle

### 3.1 Directory and manifest

The current bundle is a directory with `bundle.json` at its root. The manifest is a closed object with
exactly these fields:

```json
{
  "bundleFormatVersion": "oac.ctk.bundle/v0alpha1",
  "suiteId": "oac-phase-a",
  "suiteVersion": "0.1.0",
  "standardVersion": "oac-conformance/v0.1-draft.1",
  "profileId": "oac.phase-a",
  "profileVersion": "v0.1",
  "requirementSetRef": "requirements.json",
  "resourceProfileRef": "resource-profile.json",
  "artifacts": [
    {
      "path": "cases/C0-CANON-001.json",
      "mediaType": "application/vnd.oac.ctk.case+json",
      "size": 0,
      "digest": "sha256:<64 lowercase hex>"
    }
  ],
  "extensions": {},
  "bundleDigest": "sha256:<64 lowercase hex>"
}
```

`artifacts` lists every regular bundle file except `bundle.json`. Builder output orders entries by
logical POSIX path. Each entry contains only `path`, `mediaType`, `size`, and `digest`.
`requirementSetRef` and `resourceProfileRef` MUST resolve to listed ledger entries.

The current self-contained bundle has 36 ledger artifacts:

```text
24 cases
+ RequirementSet + ResourceProfile
+ 2 normative standards + stdio-v1 protocol
+ 7 CTK JSON Schemas
= 36
```

The contract copies live below `contracts/` and are byte-for-byte synchronized from the public source
standards, protocol, and schemas before the ledger is built. `build_bundle.py --check` fails on source
versus bundled-copy drift as well as manifest drift. A clean-room implementer can therefore read the
complete Phase A contract from the bundle without repository source access.

### 3.2 Bundle identity

Let `projection` be the complete stored manifest with only the top-level `bundleDigest` member removed:

```text
bundleDigest = "sha256:" + lowercase_hex(SHA256(JCS(projection)))
```

Identity therefore binds the artifact ledger and all version/profile fields, not tar/zip bytes,
timestamps, permission bits, compression, or an archive filename. The SHA-256 of each artifact is over
its exact stored bytes.

The exact current digest MUST be checked against a trust anchor outside the bundle. The A0 runner
release carries one frozen coordinate/digest registry entry; it does not accept a bundle's own digest
as proof that the bundle is official. The digest is intentionally not repeated inside this standard:
this standard is itself a ledger artifact, so embedding the containing bundle digest would create an
impossible content-addressing self-reference. A digest is evidence only for the exact artifact bytes;
changing a case, contract, schema, requirement, or resource profile requires a rebuilt bundle identity
and a reviewed runner trust-anchor update. A future multi-suite runner requires a signed/versioned
registry or an explicit digest obtained through an independent trusted channel.

### 3.3 Safe loading

Before launching a SUT, the directory loader MUST reject:

- an absolute path, backslash separator, `.` segment, `..` segment, or non-normalized POSIX path;
- a missing artifact, duplicate or Unicode-case-fold-colliding ledger path, or manifest/reference field
  outside the closed shape;
- a symlinked, hardlinked (`st_nlink != 1`), or non-regular manifest or listed entry;
- a symlinked root/directory or any FIFO, socket, device, or other non-regular physical entry;
- size or SHA-256 mismatch;
- an unlisted physical file or a listed file missing from disk;
- duplicate JSON object keys or non-object JSON artifacts;
- a bundle digest that does not match the JCS manifest projection or the runner's external trust
  anchor;
- a suite/standard/profile coordinate outside the runner's frozen support registry;
- a manifest, RequirementSet, ResourceProfile, or case that fails its trusted pinned JSON Schema.

Every artifact MUST be opened without following symlinks and captured once; size, digest, JSON parsing,
schema admission, and execution use those captured bytes. Re-reading a mutable path after ledger
verification creates a time-of-check/time-of-use gap and is forbidden.

The A0 loader anchors traversal at an open root directory descriptor and opens every intermediate
component relative to that descriptor with no-follow semantics. The physical inventory covers files
and non-directory special entries; empty directories are not bundle identity. This does not create an
atomic filesystem snapshot against arbitrary concurrent writers. A conforming publication run MUST
therefore stage the materialized bundle in a runner-owned location that the SUT and untrusted writers
cannot mutate for the duration of admission and execution, or use an equivalent immutable snapshot.

Current `v0alpha1` validates an already materialized directory. Before trusting the embedded profile,
it bootstraps manifest bytes, artifact count, total artifact bytes, and JSON-depth ceilings; it then
enforces bundle file/byte and per-case byte ceilings from that profile. Adapter timeout, request,
output, and JSON-depth settings are additionally bounded by runner-owned absolute ceilings; embedded
data may tighten but MUST NOT relax them. Safe tar/zip extraction, signature verification,
decompression accounting beyond the materialized directory, and media sniffing are A1 hardening work
and MUST NOT be claimed from the directory loader.

`extensions` is preserved metadata. An extension string cannot self-award independence, compatibility,
or certification and is not part of RequirementSet scoring.

## 4. Frozen RequirementSet

The exact current shape is:

```json
{
  "requirementSetId": "oac-phase-a-v0.1-required",
  "standardVersion": "oac-conformance/v0.1-draft.1",
  "profileVersion": "v0.1",
  "required": ["<case ID>"],
  "notScored": []
}
```

The object itself is pinned by its raw artifact digest in the bundle ledger. `required` and
`notScored[].caseId` MUST be disjoint, case IDs MUST be unique, and the union MUST classify every and
only bundled case. The current RequirementSet contains exactly 24 required cases and no not-scored
case:

```text
7 canonicalization + 4 identifier + 2 witness + 2 resource
+ 4 Strong Kleene + 5 closureMicro = 24
```

A core operation missing from the CapabilityStatement makes each affected required case fail. The SUT
cannot declare it away. An implementation-owned expected-failure baseline, if a surrounding CI system
uses one, MUST NOT alter `caseOutcome`, `requiredPassed`, or the RequirementSet. An expected failure is
still a conformance failure; a now-passing baseline entry is stale. The current runner does not
implement an expected-failure suppression facility.

## 5. CapabilityStatement

The `capabilities` operation has an empty payload and returns:

```json
{
  "implementationId": "example.cleanroom",
  "implementationVersion": "0.1.0",
  "adapterProtocolVersion": "oac.ctk.stdio/v1",
  "tracks": [
    {
      "role": "semantic-kernel",
      "operation": "canonicalize",
      "profileId": "oac.phase-a",
      "profileVersion": "v0.1",
      "wireVersion": "oac.ctk.stdio/v1"
    }
  ]
}
```

A track is uniquely scoped by the complete tuple:

```text
role × operation × profileId × profileVersion × wireVersion
```

Current operation tokens are:

```text
canonicalize | deriveIdentifier | witness | strongKleene | closureMicro | resourceCheck
```

Support for one operation, Profile, Profile version, or wire version implies nothing about another.
The current CapabilityStatement is closed: a missing identity/protocol field, duplicate five-tuple,
unknown operation/role/wire token, or extra top-level field invalidates the statement and exposes no
eligible tracks. A declared required track that returns `UNSUPPORTED`, or an omitted track required by
a bundled case, fails that case. Successor capability vocabularies require a versioned contract rather
than an implementation-defined self-award.

## 6. Black-box JSON/stdio wire v1

### 6.1 Process boundary

The runner starts a fresh adapter process per request in a new empty temporary working directory,
writes exactly one RFC 8785 JSON object plus a newline to stdin, closes stdin, captures stdout/stderr
separately through temporary files, and waits for process exit. It passes only a small OS/locale/PATH
environment allowlist, removes `PYTHONPATH` and arbitrary repository variables, disables Python user
site loading, and sets `NO_PROXY=*`. Each output stream is bounded before JSON admission. Responses
with duplicate keys, non-JSON numbers, non-I-JSON values, or excessive JSON depth are adapter parse
failures.

These controls reduce ambient expectation/source leakage and memory amplification. They are not a
filesystem, syscall, network, process-tree, or legal clean-room sandbox; code/dependency independence
still requires separate build/source evidence.

The independent runner package depends on `rfc8785`, `jsonschema`, and the Python standard library. It
MUST NOT import or depend on the `oac` package. A SUT adapter may implement OAC in any language or
process.

### 6.2 Request

The request is a closed object:

```json
{
  "protocolVersion": "oac.ctk.stdio/v1",
  "requestId": "request-1",
  "operation": "canonicalize",
  "payload": {}
}
```

`requestId` is an opaque correlation token. Because every invocation uses a fresh process, its current
fixed value exposes no cross-case state. The runner sends the semantic `input` object as `payload`. It
MUST NOT send `caseId`, `expect`, fixture class, requirement reference, benchmark label, bundle path, or
reference output. The adapter is not given the CTK directory.

`maxRequestBytes` independently bounds the encoded stdio request; `maxCaseBytes` bounds the containing
case artifact and is not a wire limit. The runner MUST refuse to launch the SUT for an over-limit
request. `maxJsonDepth` counts the root as depth 1 and applies to request, operation input, decoded
`canonicalize` JSON, and response.

### 6.3 Response

A completed response is exactly:

```json
{
  "protocolVersion": "oac.ctk.stdio/v1",
  "requestId": "request-1",
  "sutStatus": "COMPLETED",
  "result": {}
}
```

A non-completed response is exactly:

```json
{
  "protocolVersion": "oac.ctk.stdio/v1",
  "requestId": "request-1",
  "sutStatus": "UNSUPPORTED|RESOURCE_EXHAUSTED|ERROR",
  "error": {"code": "<stable reason code>"}
}
```

The current envelope permits additional members inside `error`, such as a non-normative `detail`, but
only `error.code` is compared. A non-completed response has no `result` and no domain verdict. Timeout,
start failure, non-zero exit, oversized stdout, malformed JSON, wrong request ID/version, or invalid
closed envelope is a runner observation rather than an OAC business verdict. Stderr is diagnostic and
unscored.

Operation payload shape/type/enum/uniqueness/closed-field violations use
`ERROR/CTK_INPUT_INVALID`. Only decoded `canonicalize` bytes that violate JSON grammar or duplicate-key
rules use `ERROR/CORE_SCHEMA_INVALID`; syntactically valid decoded JSON outside the finite RFC 8785
I-JSON domain uses `ERROR/NON_I_JSON`. A structurally valid witness operation with a non-canonical
witness or JSON Pointer uses `ERROR/APPLICABILITY_WITNESS_MISMATCH`. Resource ceilings use
`RESOURCE_EXHAUSTED/CONFORMANCE_RESOURCE_PROFILE_EXCEEDED`. Host-language exception types and
reference-implementation history do not select the code.

## 7. Current case and expectation contract

Each bundled case has:

```json
{
  "caseId": "<stable ID>",
  "operation": "<operation token>",
  "requirementRefs": ["<public requirement ID>"],
  "input": {},
  "expect": {
    "sutStatus": "COMPLETED",
    "result": {}
  }
}
```

The current comparator recognizes only these expectation members:

```text
sutStatus, result, errorCode, domainVerdict,
requiredReasonCodes, forbiddenReasonCodes
```

Exact `result`, when present, is compared structurally. `requiredReasonCodes` and
`forbiddenReasonCodes` are set comparisons against `result.reasonCodes`. An unknown expectation member,
invalid case shape, or invalid reason policy is `HARNESS_ERROR`; it is not charged to the SUT.

A future `verify` case MUST use one scalar `domainVerdict`; `allowedVerdicts` is forbidden. Multiple
legal plans are separate candidate inputs evaluated against the same public acceptance relation.

## 8. A0 semantic operations

### 8.1 `canonicalize`

Input is `{"rawBase64":"..."}`. The decoded bytes contain one JSON value. The operation rejects
duplicate object keys and input outside the RFC 8785/I-JSON domain. It returns:

```json
{
  "canonicalBase64": "<RFC 8785 bytes>",
  "digest": "sha256:<64 lowercase hex>"
}
```

Duplicate/syntax invalidity, including the non-JSON tokens `NaN` and `Infinity`, uses
`ERROR/CORE_SCHEMA_INVALID`; parsed values outside the finite I-JSON number/string domain use
`ERROR/NON_I_JSON`; excessive depth uses resource exhaustion. RFC 8785 serializes finite IEEE-754
binary64 values and does not impose an application safe-integer limit: integer `2^53` MUST complete as
`9007199254740992`. Safe-integer limits remain valid only for fields whose schema declares an integer
contract. Required vectors cover duplicate keys, `2^53-1`, integer `2^53`, `-0.0`, a lone surrogate,
and UTF-16 object-key ordering where a supplementary-plane key sorts before `U+E000`. Both executable
kernels additionally carry RFC 8785 Appendix B regression vectors.

### 8.2 `deriveIdentifier`

Input contains `identifierKind` and `fields`. Evaluation, Path, and Obligation use the exact legacy
formulas in `oac-derived-identifiers-v0.1.md`; RoleInstance, WorkUnit, and PlanDecision use its full
SHA-256 v1 envelope. The result is `{"identifier":"urn:oac:..."}`.

The current RequirementSet contains one vector for each legacy kind and one RoleInstance v1 vector. It
does not yet exhaustively test WorkUnit/PlanDecision, array permutations, or every malformed preimage.

### 8.3 `witness`

- encode input: `{"mode":"encode","resourceId":"...","pointer":"..."}`;
- decode input: `{"mode":"decode","witnessRef":"..."}`.

Encoding returns `witnessRef`; decoding returns the decoded `resourceId` and RFC 6901 `pointer`.
Alternate/lowercase percent escapes, invalid UTF-8, invalid RFC 6901 `~` escapes, or a non-unique
literal `#` use `ERROR/APPLICABILITY_WITNESS_MISMATCH`. Unknown mode, wrong/missing/extra fields,
wrong types, overlong values, and a resource ID blank under `NonBlankString/v1` are CTK input errors.
For decode, a percent component that resolves to a blank resource ID is instead a non-canonical witness
and uses `ERROR/APPLICABILITY_WITNESS_MISMATCH`.

### 8.4 `strongKleene`

Input is `{"operator":"all|any","values":["TRUE|FALSE|UNKNOWN",...]}` and output is one `result`.
For `all`, FALSE dominates UNKNOWN and empty input is TRUE. For `any`, TRUE dominates UNKNOWN and empty
input is FALSE. The current four cases sample the decisive/Unknown interactions; they do not by
themselves constitute an exhaustive independent truth-table implementation.

### 8.5 `closureMicro`

Input is a closed micrograph containing:

- unique nodes `{id, admission}` and a declared, non-retracted root;
- unique directed edges `{id, source, target, result, admission, covered}`;
- positive `maxDepth` and `maxPathPrefixes`, all bounded by the published node, edge, semantic-depth,
  and path-prefix ceilings.

The frozen micro-relation is:

1. An admitted root begins `affected`; a candidate or disputed root begins `unknown` with
   `CANDIDATE_INPUT_NOT_AUTHORITY`.
2. Edges are traversed in ascending ID order and every retained path is node-simple.
3. Retracted edges/targets and cycle-forming continuations are not traversed.
4. FALSE is not traversed and does not consume a path prefix. It emits a `falseFrontier` even from a
   retained prefix at `maxDepth`; the frontier may therefore carry `maxDepth + 1` edge refs. Authority
   is computed from the prefix state before truncation and requires that state to be affected, both
   endpoints and the edge admitted, and the edge covered.
5. A continuation remains affected only when its prefix is affected, its result is TRUE, both
   endpoints and the edge are admitted, and the edge is covered. Every other retained continuation is
   unknown with the applicable stable reasons.
6. `maxDepth` limits non-FALSE propagation. A viable non-FALSE continuation beyond it marks the current
   path unknown/truncated with `IMPACT_SEARCH_TRUNCATED`, without suppressing FALSE frontiers observed
   from that same boundary prefix.
7. The seed counts as one path prefix; FALSE frontiers do not. Exceeding `maxPathPrefixes` returns
   resource exhaustion with no partial report.
8. Paths, frontiers, reason codes, and unresolved refs use the deterministic orders published by the
   stdio protocol document.

The completed `ClosureMicroReport` contains only root, paths, FALSE frontiers, and unresolved refs. It
does not contain Supplier selectors, applicability predicates, obligations, required orders, or Plan
topology. Five cases exercise TRUE reachability, FALSE cut, candidate/Unknown/coverage propagation,
truncation, and path-budget exhaustion.

### 8.6 `resourceCheck`

Input maps known resource-profile limit names to observed non-negative integers. Values at a limit
complete; the first value over a limit returns
`RESOURCE_EXHAUSTED/CONFORMANCE_RESOURCE_PROFILE_EXCEEDED` and no partial result.

## 9. Machine-readable resource profile

The current exact profile ID is:

```text
oac.supplier.transfer/conformance-resource-profile/v1
```

Its machine-readable limits are:

```json
{
  "maxBundleFiles": 512,
  "maxBundleBytes": 67108864,
  "maxCaseBytes": 1048576,
  "maxRequestBytes": 1048576,
  "maxResourceBytes": 8388608,
  "maxPlanBytes": 16777216,
  "maxJsonDepth": 64,
  "maxNodes": 256,
  "maxEdges": 1024,
  "maxRules": 512,
  "maxDuties": 512,
  "maxSemanticDepth": 32,
  "maxPathPrefixes": 4096,
  "maxEvaluations": 2048,
  "maxWitnessRefsPerEvaluation": 256,
  "maxTotalWitnessRefs": 8192,
  "adapterTimeoutMs": 5000,
  "maxAdapterOutputBytes": 1048576
}
```

Exhaustion is a protocol-level non-completion:

```text
sutStatus = RESOURCE_EXHAUSTED
error.code = CONFORMANCE_RESOURCE_PROFILE_EXCEEDED
result = absent
domainVerdict = absent
```

It MUST NEVER become partial `ACCEPT`, `PROVISIONAL`, or `UNKNOWN`. The current two resource cases test
only `maxNodes` exactly at and one above the ceiling; complete boundary coverage for every counter is
A1 work.

## 10. RunResult and scoring

The current runner emits `oac.ctk.run-result/v0alpha1` with:

```text
runResultVersion
bundleDigest
requirementSetDigest
capabilityStatement + capabilityStatementDigest
environment + environmentDigest
requiredPassed
summary {required, passed, failed, notScored}
caseResults[]
```

Each case result contains:

```text
caseId, caseOutcome, sutStatus, stage, reasonCodes, domainVerdict, elapsedMs
```

`domainVerdict` is `null` for all current operations. `requiredPassed=true` requires a completed
capability query, all required cases present, and every required case outcome PASS. Missing core
capability, `UNSUPPORTED`, expected error mismatch, or result mismatch cannot become a skip.

The run binds the RequirementSet's raw artifact digest. It does not yet bind a resource-profile digest,
adapter build digest, diagnostics digest, or independent implementation statement. Those fields are A1
evidence requirements, not current output.

## 11. Topology-free ProfileDerivationReport

The reference package can currently construct this closed resource:

```json
{
  "apiVersion": "oac.derivation/v0alpha1",
  "kind": "ProfileDerivationReport",
  "profileId": "oac.supplier.transfer",
  "profileVersion": "v0.2",
  "snapshotDigest": "sha256:<64hex>",
  "changeDigest": "sha256:<64hex>",
  "applicabilityEvaluations": [],
  "impactPaths": [],
  "obligations": [],
  "requiredOrders": [],
  "unresolvedRefs": [],
  "rootApplicabilityUnknown": false
}
```

It MUST contain no RoleInstance, WorkUnit, principal choice, PlanDecision, compiler ID/version, plan
status, or minimality topology. Its source arrays preserve the Supplier derivation's deterministic
public order. `requiredOrders` exposes predecessor/successor role refs, reason refs, `roleWide`,
dependency reason refs, and prerequisite reason groups.

This resource is implemented in the reference package, but it is not a current CTK operation and has
not been reproduced by an independent kernel. `closureMicro` MUST NOT be substituted for it. A1 adds a
clean-room `derive` operation and byte/digest comparison for this exact projection.

## 12. Semantic-validation rule projection

The current development registry stores behavioral fields and `implementationBindings` side by side so
the Python package remains auditable. Only this projection is normative for a rule:

```text
ruleId
description
appliesToKinds
scope
failureReasonCode
jsonSchemaEnforced
ruleVersion
normativeRef
quantifier
```

Top-level and per-rule `implementationBindings` name Python CLI/library/validator symbols. They are
non-normative and MUST be excluded when computing a future normative rule-registry identity. Changing a
Python module or method cannot change OAC semantics. A separately emitted normative registry digest is
not implemented in A0 and remains an A1 task.

## 13. A1/M2 evidence resources

### 13.1 DisagreementRecord

A1 MUST preserve, at minimum:

```json
{
  "disagreementId": "<id>",
  "caseId": "<id>",
  "bundleDigest": "sha256:<64hex>",
  "stage": "ADAPTER|HARNESS|PARSE|SCHEMA|CANONICAL_BYTES|DERIVATION|VERDICT|REASON_SET|WITNESS_BINDING|RESOURCE_EXHAUSTION",
  "observations": [],
  "classification": "IMPLEMENTATION_DEFECT|SPECIFICATION_DEFECT|FIXTURE_DEFECT|HARNESS_DEFECT|CONTESTED_SEMANTICS",
  "status": "OPEN|RESOLVED",
  "owner": "<governance owner>",
  "affectedVersion": "<version>",
  "resolutionRef": null,
  "regressionCaseRef": null
}
```

No majority count or reference designation decides correctness. An open scored semantic disagreement
produces `INDETERMINATE`. Resolution changes a versioned specification, fixture, registry, or
implementation and adds a regression case; it does not overwrite old evidence.

### 13.2 IndependenceStatement

A1 records source/build/dependency digests, language/runtime, maintainership, shared normative inputs,
shared executable dependencies, reference-source access, code generation, FFI, process, and service
calls. Sharing prose, schemas, registries, and CTK is expected. Sharing an executable parser,
canonicalizer, generated model, semantic kernel, verifier, FFI, or semantic service defeats the
corresponding code-independence claim.

An internally produced implementation after reference-source access is useful differential evidence,
but it is not organizational independence and MUST NOT be called a legal clean room. M2 requires a
separately maintained implementation and external evidence review.

### 13.3 Future operations

The successor A1 wire may add `derive`, `verify`, and `compile`, but only through a new versioned
CapabilityStatement/wire contract. A fixed `verify` input has one verdict. `compile` output is checked
against the public valid-plan relation by qualified independent verifiers; it is never compared with
one golden topology. An unresolved verifier disagreement is INDETERMINATE, never reference-wins or
majority-wins.

## 14. Claim ladder and stop rules

Evidence levels are:

1. reference self-test;
2. external black-box harness pass;
3. code-independent Phase A harness mechanics;
4. code-independent semantic-kernel agreement on a frozen RequirementSet;
5. cross-implementation interoperability with resolved disagreements;
6. organizational independence;
7. separately governed bounded compatibility claim.

The current implementation cannot advance beyond level 3. In particular, the phrase
`code-independent Phase A` refers to the runner/protocol boundary, not to an independently reproduced
Supplier semantic kernel.

Withhold the next level when any of these holds:

- runner or comparator imports/calls reference semantics;
- the second adapter wraps the same semantic library, FFI, process, service, or generated model;
- a fixed verifier case has multiple acceptable verdicts;
- a compiler is compared with one exact Plan topology;
- a missing capability or expected-failure baseline hides a required failure;
- resource exhaustion emits partial semantics;
- an open disagreement is reported as PASS or decided by majority/reference preference;
- a bundle/RequirementSet/wire/build identity is omitted from the claimed coordinate;
- a Phase A result is used to imply certification, security, production readiness, organizational
  independence, enterprise value, or complete OAC conformance.

## 15. Primary references

- [RFC 8785 — JSON Canonicalization Scheme](https://www.rfc-editor.org/rfc/rfc8785)
- [RFC 6901 — JSON Pointer](https://www.rfc-editor.org/rfc/rfc6901)
- [RFC 3986 — URI Generic Syntax](https://www.rfc-editor.org/rfc/rfc3986)
- [RFC 6920 — Naming Things with Hashes](https://www.rfc-editor.org/rfc/rfc6920)
- [MCP Conformance Framework](https://github.com/modelcontextprotocol/conformance)
- [Kubernetes Conformance Test Requirements](https://github.com/kubernetes/community/blob/main/contributors/devel/sig-architecture/conformance-tests.md)
- [OPA Bundles](https://www.openpolicyagent.org/docs/management-bundles) and
  [OPA Capabilities](https://www.openpolicyagent.org/docs/cli#capabilities)
- [W3C JSON-LD Test Vocabulary](https://w3c.github.io/json-ld-api/tests/vocab.html),
  [SHACL](https://www.w3.org/TR/shacl/), and
  [EARL](https://www.w3.org/WAI/standards-guidelines/act/report/earl/)
- [Cedar Specification and DRT](https://github.com/cedar-policy/cedar-spec)
- [NIST Conformance Testing](https://www.nist.gov/itl/ai/applied-ai-research-group/conformance-testing)
- [OpenTelemetry Versioning and Stability](https://opentelemetry.io/docs/specs/otel/versioning-and-stability/)
