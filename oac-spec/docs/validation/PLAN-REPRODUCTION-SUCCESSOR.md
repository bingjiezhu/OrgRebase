# Current fixed-Plan reproduction lineage

`make check` checks `experiments/plan-verification-portability/v0.1-repro-3`.
This is a new current-source reproduction, with the same three original incidents,
required/forbidden reason-code policy, deletion operators and exact-byte check.
Neither a different rejection nor a parser failure counts as a retained reduction.

Both `v0.1-repro-1` and `v0.1-repro-2` remain immutable. The immediate predecessor
has two fixed trust anchors in `scripts/minimize_plan_verification_disagreements.py`:
its detached manifest digest and its raw-file SHA-256. A single recursive rule
verifies every predecessor's full artifact inventory, manifest identity and exact
captured ancestor manifest before and after real-adapter generation. Older
generations are reached through digest commitments in their successor manifests;
the script does not add a separate algorithm or hard-coded branch for each era.
The new directory retains the exact predecessor manifest and records the checked
historical coordinates, digests, sizes and material counts. Re-signing changed
historical files cannot satisfy those anchors. Paths remain inside the publication
namespace; symlinks, cycles, excessive depth and aggregate material size fail closed.

The integration failure was expected source drift, not missing temporary files.
All 958 files of the integration candidate's original source freeze matched their
SHA-256 entries. The old reproduction's 82 artifacts and manifest were complete.
At the first successor, current source materials added seven files and changed fourteen files, including
the Go derivation fix and new business/annotation/outcome modules. Comparing that
current inventory to the old coordinate correctly failed. Updating the old manifest
or removing current modules from the inventory would conceal the difference.
The subsequent CLI raw-admission correction changes the compiler and CLI bytes,
so the final integrated build needs `v0.1-repro-3`; the successful integration-only
`v0.1-repro-2` remains evidence of the earlier source, rather than being rewritten.

Publication is explicit and refuses an existing output directory:

```sh
uv run python scripts/minimize_plan_verification_disagreements.py
make plan-disagreement-reproductions-check
```

Generation executes both real process adapters for every candidate and records a
complete terminal deletion sweep. Check independently repeats generation and
compares the complete artifact set and every byte, including current source,
runtime and rebuilt Go executable identities. It also rechecks the preserved
predecessor. Current nested Python modules are included in the source closure.
The predecessor is validated against its retained bytes; it is not regenerated
using current code or represented as a current-source execution.

These checks establish exact-build controlled-local reproducibility for the
declared deletion operators. They do not establish a global minimum, independent
implementing organizations, production effectiveness, or enterprise qualification.
Changing a bound source file, runtime or build requires another new coordinate.

The separate evolution seed-3 manifest is not republished by this operation.
The earlier integration-only seed-3 candidate is preserved with its exact bytes
and original source closure. Final shared publication is a distinct operation:
finish all source/schema/Spec changes, retain the unpublished earlier candidate
as historical evidence outside the new publication directory, then publish once
with `check_evolution_minimum_evidence.py --publish-new` only if the final coordinate
does not yet exist. The CLI correction changes two source-closure members, so
the earlier candidate cannot pass as the final source. Never replace an existing
published seed manifest to make a changed closure pass.
