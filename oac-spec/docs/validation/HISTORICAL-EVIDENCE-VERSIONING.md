# Evidence versioning

Versioned evidence coordinates are immutable. Current source may demonstrate compatibility with frozen inputs, but a historical source closure cannot be relabeled as new source. A claim about changed source requires a new coordinate and manifest.

## Current minimum-profile coordinate

`oac.evolution.minimum/v0.1-seed-8` is the current full-workspace source-bound
coordinate. Seed-7 is its exact historical predecessor; the seed-7 manifest and
all 60 original materials, including the earlier versions of both validation
guides, are captured under new seed-8 paths. Seed-1 through seed-7 manifests and
captures retain their original bytes.

The current checker verifies those captures and the complete ancestor chain before
it verifies current source equality. A historical coordinate cannot pass as current
source, and a changed current guide cannot reuse the published seed-8 manifest.
This evidence format and its source/root-vector claim ceiling are unchanged; the
coordinate does not establish enterprise outcomes or production qualification.

## Verification scopes

| Scope | Meaning |
| --- | --- |
| Coordinate integrity | Exact serialization, detached digests, material ledgers and archived artifact bindings agree within the supplied coordinate |
| Successor compatibility | The selected current implementation is exercised against frozen public inputs, preserving the original historical identities |
| Current validation | The new implementation, contract, fixtures and observations are bound under a new versioned manifest |

Passing one scope does not imply either of the others. A raw-file SHA-256 identifies exact bytes; a detached resource digest identifies the canonical resource representation. Their meanings must remain distinct in a material ledger.

## Successor requirements

A successor coordinate uses a new directory and identity before its manifest is written. It binds the exact source, contract, schemas, TCK inputs and dependency identities needed for the stated checks, records its predecessor, and retains rejected, `UNKNOWN`, counterexample and unresolved observations.

The manifest must state whether a clean source archive, isolated installation and independently maintained implementation were actually exercised. Preserve predecessor bytes and manifests; do not regenerate old observations to make a new implementation match them. An unexpected file, changed binding or missing material is a verification failure within the declared closure.

The [public validation gate](README.md) checks the supplied reference source profile. Complete historical archive replay requires its separately obtained inputs. Neither compatibility nor matching digests establish runtime provenance, external human review, autonomous promotion, enterprise outcomes or production readiness.
