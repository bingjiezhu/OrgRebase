# Raw admission and immutable publication successor

This implementation follow-up closes the 2026-09-10 audit findings F5–F7. It retains Spec 009's
three core Kinds, six root domains, candidate-only authority boundary and reference-only claim.
Existing historical contracts and evidence remain unchanged.

## Accepted behavior and tasks

- [x] `validate-evolution` accepts the same raw-sealed optional-member omissions as sealed admission
  for OrganizationalDemand, SourceAdmissionReceipt and OutcomeCertificate. Demand also preserves
  the exact admitted Snapshot root. Each validator shares its field rules with the typed authoring
  API; no external resource is re-sealed or assigned a digest over inserted defaults.
- [x] Configured JSON depth is checked before recursive numeric normalization. Decoder exhaustion
  returns an existing stable validation reason rather than leaking RecursionError. Sealed profile
  admission retains CONFORMANCE_RESOURCE_PROFILE_EXCEEDED; annotation documents and archives retain
  ANNOTATION_DOCUMENT_INVALID and ANNOTATION_ARCHIVE_INVALID. Malformed unbounded convenience
  decoding remains CORE_SCHEMA_INVALID. Python's global recursion limit is unchanged.
- [x] Benchmark publication requires a previously absent output directory. Use
  `uv run python scripts/run_benchmark.py --output /path/to/new-coordinate` to write new evidence.
  An existing coordinate is rejected before any manifest is written; `--check` remains read-only.
- [x] The existing CI job has a 45-minute budget, allowing the measured approximately 21-minute
  complete local gate plus environment setup and packaging. This is a budget correction, not a
  claim that a hosted runner has completed successfully.

Regressions live in `tests/test_cli_evolution_admission.py`, `tests/test_raw_json_depth.py` and
`tests/test_benchmark_publication.py`. They include all three sparse roots, altered-digest rejection,
exact depth boundaries, a 1200-level input, archive error classification, partial-write prevention,
and repeated-publication refusal. Existing semantic and profile tests still apply.

## Evidence coordinate

`oac.evolution.minimum/v0.1-seed-4` uses the existing `oac.evolution.evidence/v0alpha3` format. Seed-3's
published closure bound every Python source byte. Its publication contract explicitly requires a
successor for later bound-file edits; altering that equality or re-signing seed-3 would erase the
historical identity. Seed-4 therefore captures only the exact source/schema/contract/vector materials
already named by seed-3, plus its original manifest. It creates no new benchmark traces or runtime
execution archives. The checker verifies seed-3 and every earlier ancestor before publication.

The successor current closure adds this follow-up and the three regression files. Source identity,
root-vector recomputation and negative inventory remain separate from enterprise effectiveness or
runtime evidence. Current validation results are recorded outside the historical coordinates.

The publication procedure remains exclusive: finalize bound files, verify a disposable candidate,
then publish this new coordinate once with
`uv run python scripts/check_evolution_minimum_evidence.py --publish-new`. Run `make check` and
`uv build --out-dir /path/to/new-build` for the resulting source. A later bound-file change requires
another successor; never delete or overwrite a published manifest to obtain a passing result.
