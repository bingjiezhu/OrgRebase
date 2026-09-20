# ADR 0004: Spec 003 Phase A Conformance Boundary

**Status**: Accepted

**Date**: 2026-08-23

**Method**: user-authorized Hard Grill self-questioning, repository evidence, and primary-source
conformance research

**Claim ceiling**: current implementation reaches code-independent runner/protocol mechanics only;
independent Supplier semantics, organizational independence, and complete OAC conformance remain
future gates

## Context

Spec 002 now has a meaningful contextual contract, but reference compiler and verifier still share the
same Supplier derivation implementation. The next step must discover ambiguous semantics rather than
duplicate the reference result. The user authorized Hard pressure and recommended self-answers so the
plan can continue without repeated clarification.

## Hard Grill decision record

### Question 1: What result is actually worth building now?

**Recommended answer:** Build exact A0 interoperability foundations first, then use Strong Kleene and
`closureMicro` only as an A1 experiment seed while a genuinely separate semantic kernel is built. Do
not call the microkernel full Supplier derivation or organizational conformance.

**Why it matters:** A second verifier without a frozen experiment protocol can reproduce the same
hidden assumptions and create stronger-looking but weaker evidence.

### Question 2: Does “plural-valid” allow several expected verdicts for one fixed plan?

**Recommended answer:** No. A scored fixed verifier input has exactly one expected domain verdict.
Plurality belongs to valid Plan/Agent/WorkUnit topology, and different valid plans may all receive the
same verdict.

**Why it matters:** Allowing several verdicts would turn semantic disagreement into test weakening and
make the acceptance relation unfalsifiable.

### Question 3: May an implementation omit a capability to avoid a failing core test?

**Recommended answer:** No. A frozen RequirementSet determines core requirements. Capability-driven
skips are allowed only for extensions already classified as not-scored.

**Why it matters:** Self-selected test surfaces reward under-declaration and make pass rates
non-comparable.

### Question 4: Can expected failures count as conformance passes?

**Recommended answer:** No. They are an implementation-local CI tool. The underlying required case
remains failed, and a stale expected-failure entry must be detected when the case starts passing.

**Why it matters:** Otherwise a baseline becomes a private amendment to the public standard.

### Question 5: What happens when runner uncertainty and OAC Unknown look similar?

**Recommended answer:** Keep `caseOutcome`, `sutStatus`, and `domainVerdict` as three independent axes.
Runner invalidity is `HARNESS_ERROR`; an OAC `UNKNOWN` exists only after a valid verifier response.

**Why it matters:** Collapsing them would blame enterprise semantics for lab defects or accidentally
accept a crashed verifier as conservative Unknown.

### Question 6: Who is the semantic oracle when implementations disagree?

**Recommended answer:** Neither the reference implementation nor a majority. Open a
DisagreementRecord, classify the stage and defect owner, and return `INDETERMINATE` until a versioned
normative resolution exists.

**Why it matters:** Independent implementations can share correlated mistakes, and a reference tool is
evidence, not hidden normative truth.

### Question 7: What is the smallest black-box boundary that prevents fixture gaming?

**Recommended answer:** One request/one response over JSON/stdio in a fresh process. Send only operation,
version context, resource profile, and exact semantic input bytes; never send case ID, expectation,
fixture class, requirement ID, benchmark label, or revealing filename.

**Why it matters:** A nominally black-box SUT can still hard-code public fixtures if the harness reveals
which answer is expected.

### Question 8: Should the bundle digest be the tarball SHA-256?

**Recommended answer:** No. Hash the RFC 8785 canonical artifact-ledger manifest projection. Treat
archive ordering, timestamps, permissions, and compression as transport metadata.

**Why it matters:** Semantically identical bundles should have one identity, while every listed and
unlisted byte must still be accounted for.

### Question 9: Are current derived IDs already suitable as a general standard?

**Recommended answer:** Preserve the 24-hex Evaluation/Path/Obligation formulas strictly for legacy
compatibility, but label their 96-bit truncation non-security. Use full domain-separated SHA-256 for new
compiler-owned RoleInstance, WorkUnit, and PlanDecision IDs.

**Why it matters:** Silently changing old formulas breaks frozen evidence; extending truncated/suffix
IDs as cross-enterprise names creates avoidable collision and ambiguity risk.

### Question 10: Should a verifier recompute every compiler-owned ID?

**Recommended answer:** No. In verifier mode those IDs are opaque unique reference anchors; semantics
comes from their bodies and graph relations. Compiler conformance may recompute a declared new ID
scheme, but `compilerVersion` is never parsed as a scheme oracle.

**Why it matters:** Recomputing one compiler's identifiers inside every verifier would prescribe one
topology and contradict OAC's central plural-plan thesis.

### Question 11: What should resource exhaustion mean?

**Recommended answer:** A machine-readable ResourceProfile is selected before execution. Under the
current stdio-v1 wire, exceeding it is protocol
`RESOURCE_EXHAUSTED/CONFORMANCE_RESOURCE_PROFILE_EXCEEDED`, with no Plan, derivation report, result, or
partial `ACCEPT`, `PROVISIONAL`, or `UNKNOWN`.

**Why it matters:** Partial output under path explosion can look like legitimate bounded Unknown while
silently omitting mandatory closure.

### Question 12: What intermediate output is needed to debug semantic parity?

**Recommended answer:** Add a topology-free `ProfileDerivationReport` containing exact evaluations,
paths, obligations, required orders, and unresolved state before compiler choices. Keep
`ClosureMicroReport` as a deliberately smaller test projection and never substitute it for the Supplier
report.

**Why it matters:** Two final verdicts can agree while their derived obligations differ, and a final
mismatch cannot reveal which semantic phase diverged.

### Question 13: What qualifies as a second independent implementation?

**Recommended answer:** It shares published prose, schemas, registries, and CTK, but no executable
parser, canonicalizer, generated models, derivation, verifier, FFI, process, or service. Same-model
rewrites after source access are differential assets, not organizational clean-room evidence.

**Why it matters:** Language, repository, worktree, or container differences are cosmetic when the
semantic dependency remains shared.

### Question 14: When may the project say `OAC-compatible`?

**Recommended answer:** Only after A0 and A1 evidence, a separately maintained M2 implementation, no open
required disagreement, public governance, and a claim naming exact Profile, wire, RequirementSet,
bundle, capability, resource, and build digests.

**Why it matters:** A broad compatibility label before those gates would imply consensus and
portability the repository has not demonstrated.

### Question 15: Does a self-consistent bundle digest prove that the bundle is official?

**Recommended answer:** No. Content identity is not publication authority. The frozen A0 runner owns a
supported suite/standard/Profile coordinate and expected bundle digest outside the bundle, then uses
the trusted pinned schemas only after that check. A future generic runner requires a signed/versioned
registry or an expected digest delivered through an independent trusted channel.

**Why it matters:** Otherwise anyone can rewrite cases or schemas, recompute the ledger, and obtain a
perfect score against a private standard while retaining an official-looking suite name.

### Question 16: Should OAC reject every JSON numeric value above the safe-integer ceiling?

**Recommended answer:** No. RFC 8785 serializes every finite IEEE-754 binary64 value; Appendix B
explicitly includes `9007199254740992` and larger integer-valued doubles. Safe-integer constraints
belong only to fields whose application schema declares an integer contract.

**Why it matters:** Applying an application field limit inside the general JCS serializer creates
cross-language digest divergence and contradicts the public canonicalization dependency.

### Question 17: Which boundary owns a malformed operation payload?

**Recommended answer:** The CTK operation contract. Shape, type, enum, uniqueness, and closed-field
violations are `CTK_INPUT_INVALID`; `CORE_SCHEMA_INVALID` is reserved for JSON grammar/duplicate-key
defects inside decoded `canonicalize` bytes. Parsed non-I-JSON domain failures, witness grammar, and
resource exhaustion retain their own codes.

**Why it matters:** Reusing whichever exception name a host language happens to expose makes reason
codes non-portable and turns implementation details into accidental standards.

### Question 18: Does an internally authored Go pass establish an independent semantic kernel?

**Recommended answer:** No. It is a useful cross-language differential seed and can falsify Python
assumptions, but same-repository authorship, shared task context, and prior reference-source access keep
the claim at the current Level 3 ceiling.

**Why it matters:** Evidence can improve materially without relabeling its provenance. That distinction
preserves a meaningful future clean-room and organizational gate.

### Question 19: What exactly does “blank” mean across Python, Go, and JSON Schema?

**Recommended answer:** Freeze `NonBlankString/v1` to Unicode White_Space's explicit 25 code points,
not a language function or regex shorthand. U+001C..U+001F and U+FEFF remain non-blank discriminators;
all kernels and schemas carry the same positive and negative vectors.

**Why it matters:** Python `str.isspace`, Go `unicode.IsSpace`, and schema regex engines do not have one
portable extension set. An undefined lexical primitive can make identical wire input conform in one
implementation and fail in another before any OAC semantics run.

### Question 20: Can a POSIX directory walk prove one atomic bundle snapshot under hostile writes?

**Recommended answer:** No. A0 MUST anchor every component at an open root fd and refuse symlink
traversal, but publication also requires runner-owned immutable staging (or an equivalent filesystem
snapshot). Empty directories are not bundle identity. Linux `openat2` may harden a future runner; it is
not the portable macOS/Linux A0 contract.

**Why it matters:** Digest binding prevents altered semantic bytes from passing, but it does not turn
separate directory enumeration operations into snapshot isolation. The standard must state both the
implemented mechanism and its administrative precondition.

## Resolved differentials found during implementation

### `NonBlankString/v1` runtime drift

The witness input `resourceId="\u001c"` produced `ERROR/CTK_INPUT_INVALID` in Python and a completed
`%1C#` witness in Go. Python's whitespace predicate includes U+001C..U+001F; Go's does not, while JSON
Schema regex behavior depends on the validator dialect.

Classification: `SPECIFICATION_DEFECT` plus implementation/schema portability defects. The resolution
freezes the 25-code-point Unicode White_Space set in `NonBlankString/v1`, removes runtime predicates and
`\S`, and tests all 25 blank code points in both witness-encode input and post-decode resource
admission, plus U+001C, U+001F, and U+FEFF discriminators. The frozen 24-case RequirementSet is not
silently expanded; these checks are executable cross-language/schema gates pending a successor scored
suite.

### Intermediate-directory symlink swap

The first loader inventory rejected symlinks, then reopened `root/path` with `O_NOFOLLOW`. Because that
flag protects only the final component, replacing `cases/` with a symlink after inventory still loaded
the externally moved, digest-identical cases.

Classification: `HARNESS_DEFECT`. The loader now keeps an open root directory descriptor and resolves
every intermediate directory and final file relative to it with no-follow semantics; a deterministic
swap regression must fail. A0 also states the remaining boundary honestly: directory enumeration is
not an atomic snapshot against arbitrary concurrent writers, so official execution requires
runner-owned immutable staging or an equivalent snapshot.

### `closureMicro` depth/FALSE frontier

The internally authored Go Phase A implementation exposed a specification ambiguity absent from the
24 frozen cases. For `maxDepth=1`, `A -TRUE-> B -FALSE-> C`, Python omitted the FALSE frontier while Go
retained it. A mixed boundary with both FALSE and TRUE continuations additionally showed that frontier
authority could accidentally be computed after truncation had changed B from affected to unknown.

Classification: `SPECIFICATION_DEFECT` in the experimental `closureMicro` depth/frontier relation. The
reference implementation did not win by status. The resolved rule is:

- depth limits non-FALSE propagation;
- FALSE is an evaluated cut, is not traversed, and spends no path prefix;
- a boundary FALSE frontier may contain `maxDepth + 1` edge refs;
- its authority uses the pre-truncation prefix state;
- a sibling non-FALSE continuation still marks the boundary path unknown/truncated.

Both implementations and direct regression vectors must agree before the differential seed is
admitted. This resolution does not mark the future general DisagreementRecord workflow complete.

### RFC 8785 numeric domain and C0-CANON-004

The Go seed rejected `1e20`, `1e21`, `1e30`, `9007199254740992.0`, and other finite values whenever the
parsed double happened to be an integer above `2^53-1`. The frozen C0-CANON-004 fixture independently
encoded the same error by expecting integer `2^53` to fail. Python happened to accept exponent/fraction
spellings but rejected the integer spelling, because host types leaked into admission.

Classification: `SPECIFICATION_DEFECT` plus `FIXTURE_DEFECT` and implementation defects. Neither
implementation won. RFC 8785 Section 3.2.2.3 and Appendix B are the authority: canonicalization parses
the finite binary64 value and emits ECMAScript serialization; application safe-integer limits do not
alter the JCS algorithm. C0-CANON-004 now completes as `9007199254740992`; both kernels carry the full
serializable Appendix B regression set; `NaN`/`Infinity` tokens remain JSON grammar failures, while a
syntactically valid non-finite overflow is `NON_I_JSON`.

### Self-resealed bundle false pass

The original loader accepted a re-ledgered case missing `requirementRefs` and carrying an unknown field,
and it accepted an alien manifest/Profile while capability matching remained hard-coded to the real
Profile. Both could report `requiredPassed=true`.

Classification: `HARNESS_DEFECT`. The A0 resolution deliberately narrows scope: one frozen coordinate,
one runner-owned external digest trust anchor, pinned-schema admission before SUT launch, exact manifest
capability binding, one captured read per artifact, special-file rejection, and runner-owned process
ceilings. This does not claim a generic federation trust service; signatures and a multi-suite registry
remain successor work.

## Decision

Implement Spec 003 as:

```text
A0 Interop Foundation
→ A1 Independent Semantic Kernel
→ M2 external organizational gate
```

Freeze one verdict per fixed verifier case, Plan-level plurality, immutable RequirementSets,
capability non-evasion, layered results, exact `oac.ctk.stdio/v1`, exact directory-bundle
`oac.ctk.bundle/v0alpha1`, domain-separated IDs, canonical witnesses, non-partial exhaustion,
topology-free derivation reports, and disagreement governance before elevating any claim. The current
24-case pass can elevate only black-box/code-independent harness mechanics; it cannot self-award an
independent semantic claim.

## Rejected alternatives

- another Python wrapper around `supplier.py`;
- exact golden OrganizationPlan comparison;
- capability-selected core test subsets;
- expected-failure-as-pass;
- reference-wins or majority-vote adjudication;
- raw tarball identity;
- silent replacement of legacy IDs;
- compiler-version ID guessing;
- partial semantic results after exhaustion;
- calling an internally generated second kernel organizational independence.

## Reversibility

Legacy resources and plans remain valid. New ID and witness rules are additive and versioned. A failed
Phase A experiment can be revised through successor standards and RequirementSets without mutating old
digests. No runtime or enterprise source data is changed by this decision.
