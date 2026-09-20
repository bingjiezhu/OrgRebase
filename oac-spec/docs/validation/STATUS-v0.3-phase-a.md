# OAC Conformance Lab Phase A v0.3 Validation

**Validation date**: 2026-08-23  
**Source state**: uncommitted working tree on `main`, based on
`e301952ae08986e6398a636f04f883036bc88c8d`  
**Reference package**: `oac-contract 0.3.0a0`  
**Runner package**: `oac-ctk-runner 0.1.0a0`  
**Status**: executable A0 interoperability MVP  
**Claim ceiling**: Level 3, code-independent Phase A runner/protocol mechanics plus an internally
authored cross-language differential seed

This report applies to the exact current working-tree bytes, not to a clean or signed Git revision.
The CTK bundle is digest-bound; the repository as a whole is not. No result below establishes an
independent Supplier semantic kernel, clean-room or organizational independence, complete OAC
conformance, certification, security, production readiness, enterprise effectiveness, or open-standard
status.

## Implemented boundary

- one self-contained 36-artifact `oac.ctk.bundle/v0alpha1` directory bundle;
- one exact 24-required/zero-not-scored RequirementSet;
- an external Python runner package with no product-`oac` import or dependency;
- a one-request/one-response stdio protocol with closed operations, layered result states, capability
  coordinates, runner-owned process ceilings, and expectation-leakage controls;
- external coordinate/digest trust anchoring plus pinned-schema admission before SUT launch;
- root-fd-relative, component-by-component no-follow bundle loading, single artifact-byte capture, and
  an explicit immutable-staging precondition rather than an atomic-filesystem-snapshot claim;
- exact legacy identifier preimages, full-digest compiler-owned identifiers, canonical witness refs,
  `NonBlankString/v1`, Strong Kleene samples, a topology-neutral closure microkernel, and a machine
  resource profile;
- a reference Python adapter and same-repository Go seed that exercise the black-box boundary without
  promoting the Go seed to an independent semantic implementation.

## Reproduced gates

| Gate | Result |
|---|---|
| complete `make check` | passed |
| Ruff | passed over `src`, `tests`, `scripts`, `tck`, and `ctk` |
| schema/registry drift | passed |
| benchmark drift | passed |
| bundle contract-copy/ledger drift | passed |
| Python tests | 279 passed |
| branch coverage | 90.58%, threshold 90% |
| legacy reference TCK | 33/33 passed |
| independent runner + Python adapter | 24/24 required passed; 0 failed; 0 not-scored |
| independent runner + Go adapter | 24/24 required passed; 0 failed; 0 not-scored |
| Go cross-language probes | 14/14 passed, plus 2/2 direct request-boundary probes |
| Go quality gates | `go test -count=1 ./...` and `go vet ./...` passed |
| isolated wheel install | reference TCK 33/33 and both adapters' CTK 24/24 passed from a non-repository cwd |
| repeated builds | reference and runner wheels and sdists were byte-identical across two builds |
| diff hygiene | `git diff --check` passed |

No standalone Python type-checker is currently configured, so task T003-A1201 remains open despite
the combined gates above. T003-A1202/A1204 also remain open because this uncommitted source state was
not reproduced from a clean Git archive.

## Deterministic artifacts

- CTK bundle digest:
  `sha256:5a8f498a1b1be52a1c61eaf5f1f2f073970b7a20f89f65f6aca3158bace3f526`
- RequirementSet artifact digest:
  `sha256:c012a80e3528e1ac9fe8cfb2810f0753c406c15825ea1307e7e2f50f2680d17f`
- ResourceProfile artifact digest:
  `sha256:828c0e3753880d17d6847d613d59209b51eeb4e600f3861167bfd5cfac612dda`
- Python capability-statement digest:
  `sha256:51c37248f1df2e940be150f374c3494838bd04e872fbab37b772b32661020d30`
- Go capability-statement digest:
  `sha256:ad46a421e6fcb690d1e8ccd47387eb9e153be147cdd34d169350c1e1e9f85c0b`
- Go adapter binary digest:
  `sha256:e11ccf5af42d502c2c8c0f2bf5e6d59df97d399ab3ff128a1a49e6eca617e359`
- reference wheel digest:
  `sha256:6ed2b793600495f7f862683f5d3d3c3200e402878aa7d68ccb2e5e61f206ea0f`
- runner wheel digest:
  `sha256:af9f474483d6e699337fc5dfe9622030206fd17a2d94e3f6fbb50edcbc2a53d4`
- reference sdist digest:
  `sha256:c98d17b5fcab2228d8cd11ebc732cebced5434eae022e2da51fea9cf625dbc27`
- runner sdist digest:
  `sha256:dc1fae57443445cda64cda9417151864a3f77d0e8a49b2027e607a462910677a`

The isolated build/install workspace is retained recoverably outside the repository after validation
cleanup. Its exact recovery coordinate remains in the internal audit log rather than the distributable
source tree.

## Defects found by Hard Grill and differential red-team

The implementation exercise changed the standard instead of treating the reference as automatically
correct:

1. **closure depth/FALSE frontier**: Python and Go exposed an ambiguous boundary rule. The standard now
   separates FALSE cuts from depth-limited non-FALSE propagation and freezes pre-truncation authority.
2. **RFC 8785 numeric domain**: Go, Python, and the old C0-CANON-004 fixture disagreed at integer-looking
   finite binary64 values. RFC 8785 Appendix B is now authoritative and both kernels carry its
   serializable vectors.
3. **self-resealed bundle false pass**: a malformed or alien bundle could previously reseal itself and
   pass. The runner now has an external trust anchor, frozen coordinate, and pinned-schema admission.
4. **runtime whitespace drift**: Python rejected U+001C..U+001F while Go accepted them. The contract now
   freezes the 25-code-point Unicode White_Space set as `NonBlankString/v1`; runtime predicates and
   schema `\S` are removed, and both encode input and decoded witness resource IDs are covered.
5. **intermediate-directory symlink TOCTOU**: `O_NOFOLLOW` on the final component did not protect a
   replaced parent directory. The loader now traverses from an anchored root fd, and the deterministic
   swap reproducer fails closed.
6. **legacy diagnostic collapse**: an execution-time `TypeError` could be mislabeled
   `TCK_MANIFEST_INVALID`. Manifest validation and execution are now separate boundaries.
7. **process cleanup race**: a host-denied process-group kill could break the output-ceiling gate. The
   runner now falls back to killing its owned process and still reaps it.

These are specification, fixture, harness, and implementation defects—not evidence that either
implementation wins by status. The detailed decisions and error costs are recorded in ADR 0004.

## Gates intentionally still open

1. **A1 semantic independence**: no separately authored full Supplier parser/derive/verify kernel or
   valid IndependenceStatement exists.
2. **Full semantic surface**: current scored operations stop at foundations and `closureMicro`; they do
   not reproduce `ProfileDerivationReport`, plural-plan verification, or the complete mutation set.
3. **Disagreement program**: the schema and manual resolutions exist, but generated/minimized
   DisagreementRecord emission is not implemented.
4. **Publication trust**: safe archive materialization, signatures, and a multi-suite signed registry
   remain successor work.
5. **Revision evidence**: the current result needs a clean committed revision, clean-archive replay,
   source/archive digest parity, and a configured type-checker before a release evidence claim.
6. **M2 governance**: no external maintainer, public compatibility process, certification program, or
   standards-body recognition exists.

The next evidence-increasing step is not another internal adapter. It is a published A1 input-only
implementation kit plus an independently maintained Supplier semantic kernel that emits the full
topology-free derivation report and fixed-plan verdicts through the existing runner.
