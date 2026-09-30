# OAC Conformance Test Kit

`ctk/` is the implementation-independent experimental test surface introduced by Spec 003. It is
separate from the legacy reference-development kit in `tck/`.

```text
frozen bundle + RequirementSet
              |
              v
code-independent black-box runner
          /             \
 reference adapter     another-language adapter
```

The runner never imports an OAC implementation and never sends case IDs or expectations to an adapter.
The frozen A0 runner admits only its reviewed suite/standard/Profile coordinate and externally pinned
bundle digest, then validates the ledger resources through the pinned JSON Schemas. A bundle's own
self-resealed digest is not publication authority. Multi-suite signature/registry trust remains future.
The current `phase-a-v0.1` bundle has 24 required foundation/micro-kernel cases. Passing it establishes
only the exact tested Phase A surface; it is not full Supplier Profile conformance, organizational
independence, certification, security, or enterprise-effectiveness evidence.

Run the reference protocol self-test with `make independent-ctk`. A second implementation must be run
through the same bundle and runner; disagreement is evidence to investigate, not a majority vote.
`make go-phase-a-check` runs the internally authored Go differential seed, including RFC 8785 numeric
and malformed-input probes. That is useful cross-language evidence, not an independent Supplier kernel.
