# Python reference Plan-verifier adapter

This fresh-process adapter exposes only the bounded
`oac.supplier.plan-verification.plural-capsule/v0.1-seed-1` capability over
`oac.ctk.stdio/v3`.

It reuses the repository's Python Supplier derivation and Plan verifier. It is a reference adapter, not
an independent semantic implementation. The two mutant entrypoints are deliberately invalid SUTs used
to prove that the RequirementSet detects an accept-all verifier and required-reason erasure.
