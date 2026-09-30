# Supplier v0.2 portability experiments

The files directly under this directory are the historical A1 `v0.2-seed-1` development capsule, not
current validation evidence or an OAC conformance certificate. `capsule.json` binds exact raw
Snapshot/Change artifacts, copied contracts, and reference `ProfileDerivationReport` JCS bytes for
SC-008, SC-009, and SC-010. The expected reports are reference-authored development oracles, not human
Gold labels. Legacy A0 inputs remain untouched: capsule copies were re-sealed against the
`SealedResource/v1` raw-map digest before derivation.

Seed-1 recorded 34 selected Python/Go observations. ADR 0006 superseded its evidence claim because the
capsule-pinned protocol, final bounded capability tuple, mutable generator linkage, summary identities,
and installed replay were not one coherent final coordinate; a wider branch sweep also found semantic
disagreements outside the selected set. Keep the root artifacts readable as history rather than
rewriting them in place.

The implemented successor lives under `v0.2-seed-2/` with a new capsule ID/digest. The builder targets
that successor location; it does not rebuild the historical root coordinate:

```bash
uv run python scripts/build_supplier_v02_capsule.py
```

The current parity command likewise targets the successor semantic-branch evidence set when its pinned
inputs are present:

```bash
uv run python scripts/check_supplier_v02_parity.py
```

Seed-2 records 52/52 bounded source-tree and isolated-wheel observations: 25 admission cases, three
frozen derivations, and 24 public generated semantic-branch cases. Seven real historical disagreements
and seven separate resolutions are preserved under `v0.2-seed-2/disagreements/`; two named mutants are
rejected. Exact source and installed summaries plus replay outputs are stored under the successor
directory and indexed by `docs/validation/STATUS-v0.5-a1-semantic-matrix.md`.

The historical seed-1 summary scored raw admission separately from derivation: five unique frozen roots
plus public adversarial probes covered envelope loss, explicit-value rewrite, fractional and offset
timestamp aliases, duplicate keys, noncanonical Base64 pad bits, root-digest mismatch, unknown Kind,
and the finite `2^53` JCS boundary. Those are named selected probes, not full admission coverage.
Successful derive reports were required to validate against the capsule-pinned
`ProfileDerivationReport` Schema and bind the exact profile and admitted raw-root digests before JCS
comparison.

The SUT sees only raw Base64 resources. It never receives a case ID, fixture path, capsule digest,
expected report, expected digest, or mutation name. The seed-1 generated/metamorphic set is public; it
must not be described as hidden. No genuinely held-out suite exists yet. Historical agreement proves
only the named selected observations and does not prove clean-room independence, complete Supplier
support, OAC conformance, enterprise correctness, or standard consensus.
