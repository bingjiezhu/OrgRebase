# Evolution minimum seed-3 publication

This is the successor generation procedure for `oac.evolution.minimum/v0.1-seed-3`, using evidence
format `oac.evolution.evidence/v0alpha3`. The format adds an exact captured predecessor manifest and
an explicitly verified ancestor ledger. The three minimum-profile core Kinds, six root domains,
public root vector, nine named controls and single-reference claim ceiling are unchanged.

## Preserved inputs

The original seed-2 manifest has detached digest
`sha256:3e1f2bcffae12f28c837ae1a49a1cb0a963ce3f93a43e6ac6416a0a1c0d70cc9` and raw-file SHA-256
`sha256:3ff6a2fe67fe55e180af10f81f85f814d8bcf3a1c2d405997d2568742cc34394`.

Before integrating changed source, copy its 41 exact source/schema/contract/vector files into
`experiments/evolution-minimum/v0.1-seed-3/predecessor-materials/`, retaining their repository-relative
paths. Copy the original manifest to `v0.1-seed-3/predecessor-manifest.json`. Every copy must be a normal
single-link file with the original SHA-256 and byte length. Do not hardlink or regenerate old files
from new models. This successor's captured materials have already been obtained that way; preserve
them when merging the implementation.

Both original seed-1 and seed-2 directories remain immutable. The checker validates the original and
captured seed-2 manifest, its 41 captured materials, then the original seed-1 manifest and all 21
historical materials referenced by seed-2. A missing or altered ancestor cannot be hidden by sealing
a new manifest. Checking historical bytes is separate from rerunning current behavior.

## Final integration order

1. Merge all intended Python modules, Go changes, generated schemas, tests and documentation into one
   candidate source tree. Resolve shared model/export/package changes before publishing this manifest.
2. Run schema export and review the resulting diff. Finalize any tasks or status documents included in
   the evidence contract closure. A later edit to a bound file invalidates this coordinate; it must
   not be repaired by overwriting the published manifest.
3. Confirm `sourceState.baseRevision` names the actual common base revision. Leave
   `workingTree=uncommitted` and `cleanArchiveReplayed=false` unless separately evidenced before this
   manifest is built. Keep execution and independent-implementation claims outside this minimum gate.
4. Validate a disposable copy first. The copy must retain genuine historical material bytes, not
   fabricated predecessor records. Publish seed-3 once in that copy and run its evidence tests.
5. When the final combined source is fixed, run from the repository root:

```sh
uv run python scripts/export_schemas.py --check
uv run python scripts/check_evolution_minimum_evidence.py --publish-new
uv run python scripts/check_evolution_minimum_evidence.py
uv run pytest tests/test_evolution.py tests/test_evolution_evidence.py
make check
uv build
```

The publish command builds and validates all material bindings before opening the new manifest with
exclusive creation. Invalid historical material creates no output manifest. A second publication
attempt fails and preserves the first manifest. This command is not a rewrite or migration mode.

`make check` retains its existing `evolution-evidence-check` prerequisite. No test or source exclusion
is needed. The current source closure recursively includes every Python module under `src/oac`,
including new nested packages, plus the checker, schema exporter and CTK contract implementation.
The minimum-profile schema/contract closure retains its declared scope. Whole-project Go, packaged
business descriptors, annotation material and outcome execution need their own exact combined
source/package and evidence gates; this narrow manifest cannot substitute for them.

## Failure and rollback

If final validation fails after publication, retain the failed coordinate and its observations.
Change the source in a new candidate and publish a new successor coordinate. Do not delete the
manifest and rerun `--publish-new`, loosen `check()` equality, trim the module inventory or re-sign
seed-1/seed-2 to recover a green gate. Final retained archive and wheel replay evidence belong under
a new combined validation coordinate and must reference this exact successor manifest.
