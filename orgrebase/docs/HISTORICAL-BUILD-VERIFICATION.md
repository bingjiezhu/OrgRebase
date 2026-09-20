# Historical artifacts and current build verification

The retained Spec 045 pack records the wheel and dependency metadata used in its original execution.
Updating today's `uv.lock` or `pyproject.toml` does not invalidate those historical bytes, but the
historical run cannot qualify today's build.

The parent verifier exposes two explicit scopes:

```sh
# Current dependency/project metadata must match the retained SBOM.
python scripts/verify_semifinal_closure.py --evidence /path/to/pack

# Verify a historical artifact using its separately bound QuoteValue inputs.
PYTHONPATH=src uv run python scripts/verify_semifinal_closure.py \
  --evidence evidence/semifinal-closure/latest \
  --retained-build \
  --retained-quote-value-inputs evidence/semifinal-closure/supporting/quote-value-inputs \
  --lock agentteams/historical/teamharness-v1.2.2.json
```

Run these commands from the product root. The historical example selects the AgentTeams source
lock bound to that retained pack; another pack requires its own matching lock and input snapshot.

Both modes verify the exact wheel digest and byte count against the summary/SBOM, the full pack
index, the installed-wheel receipt, child verifier results and semantic authority constraints.
Malformed recorded build digests and wheel substitution are rejected in both modes. Neither mode
rewrites frozen evidence.

The strict default additionally requires current `uv.lock` and `pyproject.toml` bytes to match their
recorded SBOM digests. Drift returns `WHEEL_CURRENT_BUILD_BINDING`. Historical mode reports the
per-file comparison in `current_build_binding` and identifies its scope as `RETAINED_ARTIFACT`.
Historical source files are not recovered from digest strings; those strings remain recorded build
metadata. Matching these two inputs alone is not proof of whole-source equality or release readiness.

The GOAI retained Spec 045 compatibility check explicitly selects historical mode. Release facts
label it `HISTORICAL_COMPATIBILITY_BASELINE` and set `current_release_qualified=false`. The historical
aggregate can report `HISTORICAL_PASS_CURRENT_BUILD_UNQUALIFIED` or a failure; it cannot grant a current
code release by replaying an older artifact.

A current release requires separately retained evidence for its newly built wheel, current SBOM and
current execution/black-box gates. A historical compatibility pass cannot substitute for those gates.

## Retained QuoteValue input bytes

The historical QuoteValue receipt names nine source files, including observations originally stored
under `evidence/workspace/latest/`. Those paths can contain newer results today. Historical verification
therefore requires an explicit input snapshot outside the sealed parent pack:
`evidence/semifinal-closure/supporting/quote-value-inputs/`. This sidecar contains `manifest.json` and
the nine original input files under `source-root/`, preserving their receipt-relative paths and exact
bytes. It does not replace today's inputs or change the historical receipt, parent index or pack.

The manifest uses schema `orgrebase.retained-quote-value-inputs.v1`. It binds the original parent
`evidence-index.json` file hash and `pack_digest`, the original QuoteValue receipt's file hash and
record digest, and every source's ID, path, evidence class, SHA-256 and byte count. Each source entry
also records its recovery location. Recovery copies bytes that match the retained receipt; it does
not rerun the original source execution (`source_execution_replayed=false`).

The verifier checks the manifest digest, exact file set, parent and receipt bindings, and all nine
source files before using `source-root/` for QuoteValue verification. Missing or substituted inputs,
altered bindings, unexpected files, symlinks and paths outside the snapshot are rejected. Omitting
the snapshot in historical mode fails with `RETAINED_QUOTE_VALUE_INPUTS_REQUIRED`; it cannot silently
fall back to a changed `latest` file. The result identifies this input scope as
`RETAINED_CONTENT_ADDRESSED_INPUTS` and retains `current_release_qualified=false`.

Current mode continues to use `CURRENT_PROJECT_INPUTS` from the current project and enforces its
current-build bindings. Passing `--retained-quote-value-inputs` without `--retained-build` fails with
`RETAINED_INPUTS_IN_CURRENT_MODE`. The separate historical snapshot cannot qualify current inputs
or turn a historical compatibility check into a current release result.

The semantic mutation runner uses the same explicit historical scope:

```sh
PYTHONPATH=src uv run python scripts/verify_semifinal_mutations.py \
  --evidence evidence/semifinal-closure/latest \
  --retained-build \
  --retained-quote-value-inputs evidence/semifinal-closure/supporting/quote-value-inputs \
  --lock agentteams/historical/teamharness-v1.2.2.json
```

It verifies the retained baseline before checking that each mutation is rejected for its expected
semantic reason. Without historical mode it uses current build inputs. It does not automatically
select a historical source lock or fall back to retained inputs after a current-mode failure.

## Retained ProductPath verifier bytes

The old ProductPath manifest records exact runner/evaluator source hashes, but its wheel does not
contain those scripts. The matching Git objects were recovered once into
`benchmark/product-path-v0.3-task-intake-bound/retained-verifiers/`. The adjacent `provenance.json`
records commit `ae5af2af4332e62d3bb2b0dc8e65bf55c8a9ec59`, both blob IDs, sizes and SHA-256 values.
These files are read-only archival validation materials. They are not an additional product runtime
and do not replace the active evaluator in `scripts/`.

The explicit `verify_product_path_blackbox(..., rebuild_wheel=False, retained_verifiers=True)` mode
checks each archived script against the unchanged artifact manifest **before importing anything**.
It copies those exact bytes and the retained observation/corpus/wheel files into an isolated
temporary directory, then uses the ordinary verification pipeline to reproduce the complete
artifact manifest and report. Runner source is inspected for provenance and is never executed.
The result is labeled `RETAINED_VERIFIER_BYTES`, with `current_release_qualified=false`; substituting
an archived evaluator is rejected before import. This replay does not depend on a future checkout
retaining the original Git ref.

Current verification keeps `retained_verifiers=False`, uses the active evaluator, and by default
rebuilds the selected wheel from the current source. Asking for archived verifiers and a current
build claim together is rejected. Current runtime-contract v2 evidence must carry its explicit
marker and 14-event primary trace; the frozen unmarked contract retains its 12-event requirement.
Unknown markers and mixed counts fail closed.
