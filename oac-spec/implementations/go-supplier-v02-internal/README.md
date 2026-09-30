# Internal Go bounded Supplier portability adapter

This directory contains the same-repository, internal differential implementation of the
`oac.ctk.stdio/v2` Supplier portability experiments. The source was generated and refined by the Codex worker
`go_supplier_v02`; that AI-authored provenance is disclosed in `PROVENANCE.md` and
`independence-statement.json`. It uses only the Go standard library and implements:

- `capabilities`;
- `validateResource` for sealed `OrganizationSnapshot`, `SemanticChangeSet`, and
  `OrganizationPlan` resources; and
- topology-free `derive` for the bounded capsule capability advertised by the adapter. Returned
  `ProfileDerivationReport` objects retain the `oac.supplier.transfer/v0.2` semantic coordinate, but
  that report coordinate MUST NOT be read as complete Supplier v0.2 capability.

It deliberately does not implement `verify`, compile a Plan topology, select principals, execute
effects, import a Python runtime, or read fixtures at SUT runtime. It is a separate-runtime internal
differential kernel for selected inputs, not an independent or full-Profile implementation. The
adapter reads one request from stdin and writes one response to stdout.

Build and run:

```sh
go build -o oac-supplier-v02-internal .
./oac-supplier-v02-internal < request.json
```

Verification:

```sh
gofmt -w *.go
go test -count=1 ./...
go vet ./...
```

The tests consume public portability artifacts as test data. Production code contains no capsule case
identifier, fixture path, expected report, or expected digest. An admitted passing run would show only
same-repository differential agreement on its exact bounded capability set and selected public
generated mutations. The included IndependenceStatement is provenance disclosure and explicitly does
not award clean-room, code, or organizational independence.
