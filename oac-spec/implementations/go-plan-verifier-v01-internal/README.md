# Internal Go bounded fixed-Plan verifier

This directory contains the dedicated same-repository Go implementation for the experimental
`oac.ctk.stdio/v3` fixed-Plan verification track. The executable exposes exactly:

- `capabilities`, advertising the single
  `plan-verifier × verifyPlan × oac.supplier.plan-verification.plural-capsule × v0.1-seed-1`
  track; and
- `verifyPlan`, which admits one sealed `OrganizationSnapshot`, `SemanticChangeSet`, and
  `OrganizationPlan`, recomputes the bounded Supplier v0.2 derivation in Go, and evaluates the public
  fixed-Plan acceptance relation.

The implementation treats compiler-owned RoleInstance, WorkUnit, and Decision identifiers as opaque.
It does not call Python, a reference compiler, a reference verifier, or an external semantic service at
runtime. It supports only the selected current contextual resources; it does not support legacy Plan
v0.1 semantics.

Build and run:

```sh
go build -trimpath -o oac-go-plan-verifier .
./oac-go-plan-verifier < request.json
```

Verification:

```sh
go test -count=1 ./...
go vet ./...
```

This is an AI-authored, reference-source-exposed, same-repository differential implementation. The
public capsule and repository fixtures are used by tests, but production source contains no frozen case
identifier, expected verdict, or expected digest. A passing run is evidence only for the named bounded
relation and frozen RequirementSet. It is not clean-room work, organizational independence, complete
Supplier conformance, enterprise correctness, production safety, or standard adoption. See
`PROVENANCE.md` and `independence-statement.json`.
