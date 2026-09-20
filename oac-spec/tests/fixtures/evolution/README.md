# Default Outcome provenance compatibility fixture

`default-shared-observation.json` is a constructed contract boundary case, not customer data.
The same observation is used in `observationRefs` and `evidenceRefs`; each role is internally unique.
The default profile's `metadata.sourceRefs` preserves both role occurrences in order.

The bytes were copied unchanged from the 2026-09-13 comparison audit's
`oac-provenance-probe/shared-observation.json`. That audit generated the input using an isolated
copy of the previous implementation and checked the exact same bytes with both full verifiers:
the previous verifier accepted them and the then-current verifier rejected provenance.

SHA-256: `393f2df03f5d493e4a65cb508a3b1c74bef607b5d3d07176862b3b0116f2ff54`.

The compatibility test reads these existing bytes through typed verification, raw admission,
and the public CLI; it does not regenerate or re-seal the positive fixture. Mutations are
separately constructed negative inputs. The explicit disposable profile retains unique
envelope provenance and its existing evidence closure requirements.
