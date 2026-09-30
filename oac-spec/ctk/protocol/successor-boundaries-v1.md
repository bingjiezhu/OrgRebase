# Foundation boundary successor suite v0.2

This coordinate adds a reviewed corpus and harness observations, not a new semantic kernel.
The old `oac-phase-a/0.1.0` directory, its 24 cases, and `RunResult/v0alpha1` remain unchanged.
The successor uses `oac.ctk.bundle/v0alpha2`, suite `oac-foundation-boundaries/0.2.0`, and the
same six public stdio-v1 operations with the same `oac.phase-a/v0.1` capability coordinate.
The directory ledger is validated by the existing descriptor-anchored loader and an external
published digest. Neither a resealed bundle nor a signature can publish a new suite coordinate.

## Targets and scoring

Every successor case has an explicit `testTarget`: `SUT` or `HARNESS`. SUT requests continue to
contain only protocolVersion, requestId, operation, and payload. Case identity, expectations,
requirements, and paths never enter a SUT request. SUT comparison uses the existing scorer.

HARNESS cases are closed, runner-owned recipes. Bundle attacks must first load a valid copied
baseline, apply exactly the declared mutation, and observe the declared rejection. Adapter
probes run a short local process through the same bounded `invoke` used for SUTs. Malformed
bytes, duplicate keys, invalid UTF-8, wrong request identity, partial resource results, timeout,
crash, output overflow, and a blocked stdin writer are observations of the harness boundary.
They do not grant a SUT a passing timeout or suppress an expected implementation failure.
`targetSummary` reports the two denominators separately. Every required case must pass and the
SUT capability response must be valid for `requiredPassed=true`; no expected-failure list exists.

The resourceCheck cases freeze exact and one-over values for all 18 published counters. Actual
parser, closure, witness, input/output, and timeout enforcement is also exercised where a legal
stdio-v1 request can reach the boundary. Counter vectors alone do not assert peak-memory,
customer-scale throughput, or all possible combined-limit workloads.

## Result and disagreements

`oac.ctk.run-result/v0alpha2` includes resourceProfileDigest, implementationBuild and its digest,
diagnostics and their digest, emitted disagreements and their digest, and resultDigest over
the result without resultDigest. All structural digests use RFC 8785 plus SHA-256; the resource
profile digest identifies the exact ledger bytes, not a rewritten JSON object.

The operator supplies explicit `--build-input` files. The runner records exact byte digests of
those named SUT materials, the executed interpreter/binary, its own modules, and the command
digest. It checks the same materials after the run and rejects a changed build. This is a
self-attested local material observation, not proof that every file was executed, a full SBOM,
organizational independence, or cryptographic execution attestation. It never scans unrelated
directories or guesses the source of `python -m`.

Diagnostics retain the bounded decoded response and normalized stderr text, and commit the request envelope, response, stderr, observed
status, stage, error, and elapsed duration. `stderrTextDigest` is explicitly a text digest after
the existing bounded UTF-8 decoding; it is not a raw-output byte claim. HARNESS diagnostics use
the frozen {probe} recipe as their request commitment and explicitly declare requestKind=HARNESS_RECIPE; SUT records declare STDIO_REQUEST. Exact timing remains an observation and need
not reproduce byte-identically across runs.

Every failing comparison emits `oac.ctk.disagreement/v0.2`: the published expectation and actual
observation are separately digest-bound. New records are OPEN and UNCLASSIFIED, with an
unassigned review owner and no invented resolution. SUT failures are INDETERMINATE pending
classification; they still fail the required gate. Harness failures remain HARNESS_ERROR.
The runner cannot resolve a disagreement by reference-wins or majority vote.

## Bounded archive transport

`--archive` admits `oac.ctk.archive/v1` ZIP (stored or deflated entries only) transport containing only ordinary files. The loader
rejects encrypted, special, linked, privileged, duplicate, case-colliding, non-normalized,
traversal, drive-qualified, and conflicting parent/file paths. Count, raw-byte, expanded-byte,
and per-entry declared-size ceilings apply before or during materialization. Materialization
occurs only in a private temporary directory, cleaned on success or failure. There is no
`extractall`, destination overwrite, filesystem owner restoration, or executable bit restoration.

Archive serialization is transport only. After extraction, the same published directory bundle
loader must validate all bytes. Equivalent safe ZIP encodings therefore share the original
directory bundle identity, while changed/unlisted content remains rejected.

Optional Ed25519 verification requires the runner `signatures` extra and an operator-pinned
public key supplied outside the archive. A signature statement contains exactly archiveFormatVersion,
archiveDigest, bundleDigest, and signature{algorithm,keyId,value}. The signature preimage is the
UTF-8 bytes `oac.ctk.archive-signature/v1`, one NUL, then RFC 8785 of the statement without signature.
keyId is SHA-256 of the raw 32-byte Ed25519 public key; value is canonical Base64 of the 64-byte
signature. Both raw archive bytes and the resulting published bundle identity must match. Unknown
algorithms, absent extras, missing key/signature pairs, or a bundle-supplied key cannot downgrade
to unsigned acceptance. The unsigned path still requires the externally published bundle digest.

The result boundary remains bounded local CTK evidence, not complete Profile conformance,
independent-organization implementation, security certification, or enterprise effectiveness.
