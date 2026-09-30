# Provenance and claim boundary

This is an **internal, same-repository differential implementation** of the bounded fixed-Plan
verification relation. It is AI-authored, non-clean-room, non-independent, and not evidence of a
separately maintained OAC implementation.

## Source exposure and origin

The implementation reuses the repository's Go sealed-resource admission and Supplier derivation
foundation. The fixed-Plan verifier was written after observing the public relation, protocol, schemas,
fixtures, Python verifier, and reference adapter, including:

- `standard/oac-plan-verification-relation-v0.1.md`;
- `standard/oac-sealed-resource-v1.md`;
- `ctk/protocol/stdio-v3-plan-verification.md`;
- `schemas/*.json` and `ctk/schemas/*.json`;
- `profiles/supplier-change/**` and the public Plan fixtures;
- `src/oac/supplier.py` and `src/oac/verifier.py`; and
- `implementations/python-plan-verifier-reference/**`.

This explicit reference-source exposure prevents any code-independence or clean-room claim. The Go
binary nevertheless has a narrower separate-runtime property: it uses the Go standard library only and
has no Python runtime, FFI, subprocess, semantic service, fixture lookup, or reference implementation
dependency while handling a request.

## Self-attested build material

The source closure is the RFC 8785 JCS SHA-256 of a sorted array of
`{"path": <module-relative path>, "digest": <raw-file SHA-256>}` records. It includes `go.mod` and every
non-test `.go` file in this directory; tests, documentation, the statement, caches, hidden files, and
build products are excluded. The current closure is:

```text
sha256:2ef199e822f81e50fab1c58c9005421c2f00f8cf8305817368db4b61d448c589
```

The path-independent build-recipe projection and its JCS SHA-256 are:

```json
{"argv":["go","build","-trimpath","-o","<artifact>","."],"workingDirectory":"implementations/go-plan-verifier-v01-internal"}
```

```text
sha256:a5cf7b4f63b8ac3468bbd4574c452051d846b63b14b1e8fed74d9edbe853df2d
```

The environment projection and its JCS SHA-256 are:

```json
{"CGO_ENABLED":"1","GOARCH":"arm64","GOOS":"darwin","GOROOT":"/opt/homebrew/Cellar/go/1.25.6/libexec","GOVERSION":"go1.25.6"}
```

```text
sha256:06c2a7e7aa87d3885b89645fbc1a53d34332a0a6ab86ab66de64adb34a232d48
```

On 2026-08-24, two `-trimpath` builds with separate Go build caches produced byte-identical local
darwin/arm64 executables:

```text
sha256:b3723b3e0164d2e6a7aea98a459178f6e99bf6150b768d5d8eefe6f8453edf5a
```

These values disclose one dirty shared-worktree build rooted at Git base
`e301952ae08986e6398a636f04f883036bc88c8d`. They are self-attested provenance, not a clean archive,
signature, third-party reproduction, or conformance result.

## Claim ceiling

The maximum claim is a same-repository, separate-runtime differential implementation of the named
bounded Plan relation over its selected public capsule. It does not establish complete Supplier
verification, hidden-case generalization, behavioral equivalence, outcome correctness, enterprise
safety, production readiness, certification, industry consensus, or organizational independence. The
closed IndependenceStatement is provenance and dependency disclosure only; validation cannot award
independence.
