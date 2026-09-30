# Reproducing Plan acceptance from raw inputs

A reproducible Plan check binds the sealed Snapshot, Change and Plan, the selected Profile and verifier capability, and the implementation and dependency identities used for the check. Input admission follows the [raw CLI contract](CLI-ADMISSION.md); expected verdicts and reason policies belong to the evaluation harness, not the verifier request.

## Material identity

Record exact file hashes and byte counts in the reproduction ledger. The verifier recomputes resource digests and relevant derivation facts from the admitted inputs. A compiler certificate, reference verdict or historical report cannot replace those checks.

A reproduction coordinate records all expected observations, including acceptance, rejection, protocol errors and `UNKNOWN`. Negative controls must bind the changed input bytes and the constraint they exercise. Renaming a case or retaining a previous success summary is not a fresh execution.

## Source and installed execution

Use the [public source gate](README.md) for the checks included in this workspace. For installation validation, build the selected OAC distribution, install it in a new environment, verify that imports resolve to that installation, and run the same declared CLI/TCK inputs. Record the artifact hash, environment and actual outputs.

The included Go implementations provide same-project cross-language checks. Agreement applies to the selected constraints and inputs; it does not establish a clean-room implementation, workflow equivalence, complete Profile coverage or enterprise correctness.

Complete historical reproduction also needs its exact source, generated inputs, observations and predecessor ledgers. Those archives are outside the public source profile. Changed source or dependencies use a new [evidence coordinate](HISTORICAL-EVIDENCE-VERSIONING.md), preserving the predecessor rather than rewriting it.
