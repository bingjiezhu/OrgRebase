# OAC A1 Supplier v0.2 Seed-1 Portability Development Report

**Original validation date**: 2026-08-23
**Evidence status**: superseded by ADR 0006; historical development report, not current admitted
validation evidence
**Source state**: uncommitted working tree on `main`, based on
`e301952ae08986e6398a636f04f883036bc88c8d`
**Reference package at the time**: `oac-contract 0.3.0a0`
**Claim ceiling**: same-repository, separate-runtime differential observations on a public selected
set; not complete Supplier support, independent implementation, clean-room evidence, conformance,
enterprise correctness, production validation, or final installed-artifact evidence

## Historical result

Seed-1 asked a narrow development question: could the Python reference path and an internally authored
Go runtime consume the same selected raw roots and emit the same topology-free report observations?
The recorded selected set answered yes:

| Selected surface | Historical observation |
|---|---:|
| frozen raw-root validation | 5/5 |
| public raw-admission/error probes | 20/20 |
| frozen SC-008/009/010 derivations | 3/3 exact report-JCS agreement |
| public generated/metamorphic derivations | 6/6 exact observation agreement |
| total selected observations | 34/34 |
| disagreements inside the selected set after DIS-001 resolution | 0 |
| canned-three golden-report mutant | rejected by the public generated `scope-order` input |

These observations were public and selected, not held out. They do not measure every Supplier v0.2
semantic branch. A later wider sweep found disagreements on adjacent valid inputs, so the table MUST
NOT be summarized as “zero full-profile disagreement” or “complete derivation portability.”

The three historical frozen report digests remain useful content identifiers:

- SC-008: `sha256:e6fbff4c5bc58b11b71ef7663ae3b7f83a3230ce38efd237325dfffdcafce5cf`
- SC-009: `sha256:331d886277809b8ba4ff684317d6fbb4889b37300e9a71fa752fe206acaa32ec`
- SC-010: `sha256:be307dff07197cd3aeea809baf786f25cebbf2cec553cd7ebde1d2ca61760d1c`

They are Profile-relative development oracles, not human organizational Ground Truth.

## Useful defects exposed

Seed-1 still produced valuable falsification evidence:

1. `SealedResource/v1` separated raw decoded identity from historical model-materialized defaults.
2. Historical SC-008/009/010 Change resources were copied and raw-resealed without modifying A0 roots.
3. Normative semantic rules were projected without Python/runtime bindings.
4. Supplier relation, witness, report-order, and path-prefix rules became public contract text.
5. A1 schemas were isolated from the frozen A0 seven-schema/24-case coordinate.
6. Public generation exposed `DIS-001`: candidate authority removed a prerequisite successor, while the
   Go kernel initially returned a partial completed order. The fail-closed rule and regression were
   retained instead of deleting the mismatch.

The internal Go IndependenceStatement also records same-repository maintenance, AI generation, and
reference exposure. It is provenance/dependency disclosure, not an independence certificate.

## Why this report is superseded

ADR 0006 found that seed-1 did not bind one coherent, durable evidence coordinate:

- the capsule-pinned stdio-v2 text advertised a broader Supplier capability than the bounded adapter
  tuple ultimately intended;
- the public generated inputs were useful, but their generator was linked to mutable harness/source
  state rather than a frozen recipe/source/runtime ledger;
- the isolated install replay predated the final selected-set/capability state, so this report withdraws
  the earlier “installed A1 34/34” statement;
- previously listed parity-summary, disagreement, IndependenceStatement, build, wheel, and sdist
  digests did not all describe the same final source/capability/run coordinate and therefore are not a
  valid final summary; and
- a wider semantic branch sweep found additional input-level disagreements outside the 34 selected
  observations.

Accordingly, earlier draft statements such as “complete validation gates,” “final 34/34,” and “zero
open disagreements” are historical within-set notes only. They are not admitted successor evidence.
The old capsule and DIS-001 remain preserved; they are not rewritten to impersonate a new run.

## Current evidence boundary

Seed-1 supports only these statements:

- stdio-v2 raw admission and `ProfileDerivationReport` comparison are implementable mechanisms;
- a separate-runtime internal Go kernel reproduced the complete report shape for the named selected
  inputs without calling Python semantics at runtime; and
- differential implementation plus public generation can expose specification and implementation
  defects that reference self-tests miss.

It does not support:

- complete Supplier v0.2 branch coverage;
- a genuinely hidden or held-out suite;
- fixed-plan verification parity or plural-plan acceptance by two verifiers;
- an independently maintained or legal clean-room implementation;
- a final reproducible installed-artifact result;
- real enterprise labels, outcomes, ROI, security, or production suitability; or
- a claim that one contract adapts every enterprise.

## Successor gate

The next highest-value work is ADR 0006, not immediate fixed-plan `verify`:

```text
semantic branch decisions
        ↓
immutable incidents + separate resolutions
        ↓
digest-bound recipe / generator / capability set / implementation observations
        ↓
source + clean-build + isolated-install replay
        ↓
only then: plural fixed-plan verify
```

The successor report must publish its own exact coordinate and measured counts. This superseded report
does not predict or pre-fill either.
