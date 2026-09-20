# Synthetic annotation protocol example

These documents exercise a caller-pinned local protocol. All identities and qualification statements
are synthetic; no real reviewer, two-round human annotation, Human Gold or enterprise outcome is
claimed. The example is derived from an unchanged retained Supplier case and its exact source roots.

`trusted-inputs.json` is an explicit test-harness pin, outside both archives. A real host must obtain
its trusted pins and authenticated subjects from its own governance boundary, not copy trust values
from a candidate archive. `approved.json` contains two blind deliveries, two committed synthetic
reviews, distinct adjudication, promotion, and a benchmark claim. `retracted.json` appends an
immutable source withdrawal and invalidates the claim. The prior bytes remain preserved.

Load either archive with `restore_annotation_archive` and its corresponding external test pin.
Use `qualify_annotation_benchmark` on the current ledger. Qualification must succeed for the first
archive and fail with `ANNOTATION_RETRACTED` for the second. This verifies protocol mechanics only.

The mandatory `qualificationBasis` is `CONTROLLED_LOCAL_FIXTURE` in authority, qualifications,
promotion and benchmark claims. The public benchmark guard defaults to
`GOVERNANCE_ATTESTED_HUMAN` and rejects this sample with `ANNOTATION_GOLD_NOT_QUALIFIED`.
Protocol replay must explicitly request the controlled-local basis. Changing only the authority
basis cannot elevate fixture qualification records.
