# Historical evidence and successor coordinates

## Rule

Versioned evidence is immutable. A later source tree may prove compatibility with a historical
coordinate, but it cannot make the historical implementation byte closure equal to new bytes. If new
bytes are part of a new validation claim, publish a new coordinate.

Three checks are deliberately separate:

1. **coordinate integrity** verifies exact serialization, detached digests, internal ledgers and
   archived artifact cross-bindings;
2. **successor compatibility** reruns current behavior against frozen public inputs while preserving
   the historical implementation identity;
3. **current validation** binds the successor source, contract, fixtures and bounded claim under a new
   versioned manifest.

Passing one class does not imply either of the others.

## Applied boundaries

| Evidence | Immutable identity | Current-tree check |
|---|---|---|
| Supplier `v0.2-seed-2` | source/install summaries, wheel ledger, manifest and claim | historical ledger is checked internally and against its archived wheel/install; `make supplier-parity-check` remains an explicit compatibility replay, not a default historical-integrity prerequisite |
| Plan verification `v0.1-seed-1` | 39-case capsule, summaries, mutation record, source closure and manifest | the published manifest digest anchors its source ledger; `--write` refuses in-place re-signing |
| mechanics benchmark run manifests | exact input, result, metric and implementation closures | `benchmark-check` compares every behavioral field but retains the historical implementation bytes and detached digest |
| Evolution minimum `v0.1-seed-1` | three core Kinds, six root domains, schemas, public vectors, tests and claim ceiling | immutable predecessor; seed-2 captures and verifies every bound historical input byte |
| Evolution minimum `v0.1-seed-2` | original full Python source and CTK schema exporter; unchanged domain roots and claim ceiling | immutable predecessor; seed-3 captures its 41 material files and original manifest while retaining the seed-1 chain |
| Evolution minimum `v0.1-seed-3` | successor source, exact original predecessor captures and verified ancestor chain | `make evolution-evidence-check` checks the published current closure; publish only after combined source integration through the [seed-3 procedure](EVOLUTION-SEED3-PUBLICATION.md) |

## Successor publication gate

A successor evidence coordinate MUST:

- use a new coordinate and directory before any manifest is written;
- bind exact source/contract/schema/TCK bytes and one explicit base revision;
- state whether a clean archive and external implementation were replayed;
- retain rejected, `UNKNOWN`, counterexample and unresolved observations;
- preserve the predecessor manifest and all historical bytes;
- cap claims to the evidence actually carried by that coordinate.

Publication stops if the candidate needs an in-place historical rewrite, omits a changed semantic
material, loses a negative observation, or claims execution, interoperability, enterprise outcome, or
production readiness without the corresponding independent evidence.

## Current claim ceiling

The Spec 009 coordinate proves only a source-bound Python reference slice: three OAC Core Kinds, six
domain-separated root functions, one recomputed public root vector and nine named negative controls. It
does not prove independent implementation agreement, runtime execution provenance, autonomous
evolution, enterprise outcome effectiveness, complete OAC conformance, clean-archive reproduction or
production readiness.
