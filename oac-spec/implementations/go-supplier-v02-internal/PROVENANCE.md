# Provenance and claim boundary

This is an **internal, same-repository differential implementation**. It is not clean-room work, not
organizationally independent, and not evidence of an independently maintained OAC implementation.
Its source was generated and refined by the Codex worker `go_supplier_v02`; it is therefore explicitly
AI-generated internal work rather than independently authored external evidence.

The initial seed-1 implementation was authored from this explicit read whitelist:

- `standard/oac-core-v0.1.md`
- `standard/oac-conformance-v0.1.md`
- `standard/oac-derived-identifiers-v0.1.md`
- `profiles/supplier-change/profile.md`
- `ctk/protocol/stdio-v2.md`
- `schemas/*.json`
- `ctk/schemas/*.json`
- `profiles/supplier-change/inputs/veracier-proc01-contextual.snapshot.json`
- `profiles/supplier-change/inputs/veracier-proc01-truncated-contextual.snapshot.json`
- `profiles/supplier-change/inputs/SC-008.change.json`
- `profiles/supplier-change/inputs/SC-009.change.json`
- `profiles/supplier-change/inputs/SC-010.change.json`
- `experiments/supplier-v02-portability/**` after its public generation
- `implementations/go-phase-a/**`, limited to reusing the author's Go JCS and stdio foundation ideas

That historical authoring restriction no longer describes the current seed-2 revision. During the
ADR 0006 red-team and resolution cycle, maintainers compared complete Python/Go observations and
inspected relevant Python evaluator, Supplier derivation, regression-test, and public generator source
to locate shared and implementation-specific defects. The current Go source therefore has explicit
reference-source exposure and MUST NOT be presented as code-independent or clean-room evidence.

The executable still has no Python runtime, third-party Go module, FFI, subprocess, or semantic service
dependency at SUT runtime. That separate-runtime property is narrower than source independence and does
not change the disclosed same-repository provenance.

## Reproducible build evidence

The source digest in `independence-statement.json` is the SHA-256 of RFC 8785 JCS over a sorted array of
`{"path": <module-relative path>, "digest": <raw-file SHA-256>}` records. The closed production set is
`go.mod` plus every non-test `.go` file in this directory; `_test.go`, documentation, the statement
itself, caches, hidden files, and build products are excluded. For seed 2 that closure is
`sha256:db73a67c4133d39c74ce10d8fa99ceaa489487f94603d4359c2bddd8af81b364`.

The build recipe digest is RFC 8785 JCS SHA-256 over this path-independent projection:

```json
{"argv":["go","build","-trimpath","-o","<artifact>","."],"workingDirectory":"implementations/go-supplier-v02-internal"}
```

The environment digest uses the same construction over:

```json
{"CGO_ENABLED":"1","GOARCH":"arm64","GOOS":"darwin","GOROOT":"/opt/homebrew/Cellar/go/1.25.6/libexec","GOVERSION":"go1.25.6"}
```

Two builds in distinct temporary directories produced byte-identical executables with raw digest
`sha256:cd24a7d582ea9e31af20e2b266549a0f9d92fec4a1d549d5dac75a734c5cb238`. The embedded Go build
metadata discloses base revision `e301952ae08986e6398a636f04f883036bc88c8d` and `vcs.modified=true`;
this is a reproducible build from the disclosed dirty working tree, not a clean Git archive claim.

The claim ceiling is raw sealed-resource admission plus topology-free Supplier v0.2 differential
agreement for `oac.ctk.stdio/v2`. It does not establish `verify`, complete OAC conformance, production
safety, enterprise correctness, standard consensus, certification, or organizational independence.
