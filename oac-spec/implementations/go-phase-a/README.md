# OAC Phase A internal Go adapter

Internally authored Go implementation of the frozen `oac.ctk.stdio/v1` process boundary and its six
Phase A semantic-kernel operations:

- `canonicalize`
- `deriveIdentifier`
- `witness`
- `strongKleene`
- `closureMicro`
- `resourceCheck`

It uses only the Go standard library. The adapter reads one request object from stdin, writes one
response object plus a newline to stdout, and exits. Diagnostics are reserved for stderr.

## Build and run

```sh
cd implementations/go-phase-a
go test ./...
go build -trimpath -o ./oac-go-phase-a .
./oac-go-phase-a
```

For a conformance runner, build once and provide the resulting absolute binary path as the adapter
command. A direct smoke request is:

```sh
printf '%s\n' '{"protocolVersion":"oac.ctk.stdio/v1","requestId":"request-1","operation":"capabilities","payload":{}}' | ./oac-go-phase-a
```

## Evidence boundary

`adapter_test.go` evaluates the implementation against every JSON case currently present in the
public `ctk/bundles/phase-a-v0.1/cases` directory and asserts that the set contains exactly 24 cases.
Those tests are test-only consumers of the public bundle; the adapter process is never given a case
ID, expectation, fixture class, requirement reference, or CTK path.

The current local acceptance coordinate is 36 ledger artifacts, 24 required cases, and bundle digest
`sha256:5a8f498a1b1be52a1c61eaf5f1f2f073970b7a20f89f65f6aca3158bace3f526`.
The test suite recomputes that manifest projection identity, so a later bundle rebuild fails loudly
until this coordinate is deliberately reviewed and updated.

Passing this bundle makes the implementation a cross-language differential seed on the narrow frozen
Phase A operation surface, with a claim ceiling of evidence level 3. This same-repository/internal work
is not organizational independence, legal clean-room evidence, complete implementation independence,
complete OAC conformance, complete Supplier derivation, certification, security validation, or
production validation. In particular, `closureMicro` is only the published topology-neutral closure
microkernel. Any later `referenceAccess` or implementation-independence claim requires a separate,
reviewed provenance declaration.
