# Tasks — Spec 009

- [x] **T009-001** Add strict `OrganizationalDemand`, `SourceAdmissionReceipt`, and `OutcomeCertificate` models.
- [x] **T009-002** Register kinds, reason codes, generated schemas and package exports.
- [x] **T009-003** Implement deterministic domain-separated `S/D/P/X/O/E` root projections.
- [x] **T009-004** Implement semantic closure checks without importing compiler or runtime implementation code.
- [x] **T009-005** Add positive, `UNKNOWN`, and completed-but-rejected fixtures.
- [x] **T009-006** Add root, digest, namespace, authority, self-certification and predecessor mutation vectors.
- [x] **T009-007** Expose validation through the public CLI/TCK.
- [x] **T009-008** Prove legacy resource bytes and digests remain unchanged.
- [x] **T009-009** Separate historical coordinate integrity from successor compatibility without re-signing old evidence.
- [x] **T009-010** Publish a versioned Spec 009 source/root-vector evidence coordinate with a falsifiable claim ceiling.
- [ ] **T009-011** Run `make check`, `uv build`, and clean built-wheel replay after the evidence coordinate is frozen.
- [ ] **T009-012** Update the conformance matrix only after all gates pass.


The current implementation generation for T009-010 is the seed-3 successor procedure described in
[the publication guide](../../docs/validation/EVOLUTION-SEED3-PUBLICATION.md). A candidate without the
new once-published manifest is not current evidence. T009-011/012 still require final combined gates;
no historical completion checkbox is changed by this generation update.


## Explicit disposable-local Outcome extension

- [x] **T009-D01** Add the closed versioned descriptor and opt-in binding without changing default wire values.
- [x] **T009-D02** Bind disposable execution kinds and independent sandbox grant in a separate execution-root domain through the shared projector.
- [x] **T009-D03** Enforce the exact six-dimensional evidence closure and preserve failure/unknown in the shared verifier.
- [x] **T009-D04** Publish registered reasons, generated schemas, semantic registry, public APIs, and constructed positive/negative/unknown fixtures.
- [x] **T009-D05** Exercise the existing public CLI and adversarial profile/kind/root/provenance/evidence mutations.
- [x] **T009-D06** Run and retain candidate regression, make check and built-wheel replay evidence, including the unresolved historical identity rejection; this is not an integrated qualification.
- [ ] **T009-D07** Parent integration: bind verified Spec 005 payload closure and freeze a new integrated source/evidence coordinate.

## Final source-bound gate accounting

The current task file is part of the seed-3 contract closure. It records the implementation and verification state when that closure is frozen. T009-011/012/D07 are finally adjudicated by the external, digest-bound completion record after the once-published coordinate and installed artifact actually pass. That record must identify this exact source, preserve all prior failures and distinguish internal implementation evidence from independent organizational qualification. No task-file edit may re-sign a previously published seed.

## Default provenance compatibility correction — 2026-09-13

- [x] **T009-C01** Preserve default Outcome `metadata.sourceRefs` as the exact ordered role sequence, including cross-role reuse; retain first-appearance uniqueness for the explicit disposable profile. Keep one profile-selected projection and the shared verifier.
- [x] **T009-C02** Admit unchanged historical default bytes through typed verification, raw admission and the public CLI. Add a pinned positive fixture and reject deduplicated, reordered, extra and within-role duplicate provenance; preserve explicit-profile duplicate rejection.
- [ ] **T009-C03** Adjudicate combined `make check` and `uv build` against this new working source. Keep frozen experiments and their historical source coordinates unchanged; a focused regression pass alone is not integrated qualification.

These corrections restore the prior default contract. They do not re-sign old certificates,
change schemas or roots, or promote experimental Outcome evidence to enterprise effectiveness.

The [default provenance correction](default-provenance-compatibility.md) is assigned the new
`oac.evolution.minimum/v0.1-seed-7` coordinate. T009-C03 records the required final gate, not
its execution result. The final source-bound disposition is recorded outside this frozen
contract closure in the 2026-09-13 boundary-fixes run report; changing a checkbox after
publication must not invalidate or silently re-sign the same coordinate.
