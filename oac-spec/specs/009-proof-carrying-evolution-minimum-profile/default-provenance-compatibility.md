# Default Outcome provenance compatibility

The default `OutcomeCertificate` contract has no explicit `profileBinding`. Its
`metadata.sourceRefs` MUST equal the ordered concatenation of the referenced evidence roles.
Each role remains internally unique; the same reference MAY appear in different roles and
therefore appear more than once in this metadata sequence. Admission MUST retain those exact
bytes. Missing, reordered, extra or incorrectly deduplicated provenance MUST be rejected by
the shared semantic verifier. This is a clarification of the prior default contract, not a
new permission to duplicate references within a role.

The opt-in disposable profile keeps its existing first-appearance unique provenance projection,
six-dimensional evidence closure and independent execution-authorization requirement. The
default compatibility rule MUST NOT relax that profile or other resource kinds. Both projections
use the existing version-selected helper and shared verifier. No source admission, execution or
Outcome authority is conferred by the metadata projection itself.

## Fixed regression input

`tests/fixtures/evolution/default-shared-observation.json` preserves the exact bytes that the
previous default implementation accepted. Its raw SHA-256 is
`393f2df03f5d493e4a65cb508a3b1c74bef607b5d3d07176862b3b0116f2ff54`.
This is a constructed contract boundary input, not enterprise outcome data. Typed verification,
raw admission and the public CLI consume that same file without re-sealing it. Explicit-profile
and malformed-provenance controls remain bound alongside the positive fixture.

## Source coordinate

This correction uses `oac.evolution.minimum/v0.1-seed-7`; seed-1 through seed-6 remain immutable.
The seed-6 manifest is preserved with detached digest
`sha256:bfcc0e1d7c55dd1f7045950c8c3861c184c5b643c613cac4346e69def0d4ab80`
and raw-file digest
`sha256:95fc8b1faeedd259e861d6f19fba5e6ca9ea31064f490ced24aac787eb79f8ee`.
Its 56 original source/schema/contract/vector materials are copied as regular single-link files
under the new coordinate's `predecessor-materials`, with every original digest and size checked.
Their bytes are available in the retained `oac_contract-0.3.0a0.tar.gz` build with raw SHA-256
`1a1538c6b85842e81f5324012c48bed69db1495fded90ff0ed0756e90adb37cd`.

The existing checker's exclusive `--publish-new` operation creates the new manifest only after
checking all predecessor and ancestor bindings. It must never replace an existing coordinate.
The original root vector, nine-control inventory and claim ceiling remain unchanged. This
coordinate additionally binds the default-provenance fixture and strict-profile regression
tests; it does not publish additional business experiments.

Finalize bound source, tests and task definitions before publication. Run the existing composite
gate and build against that source, and record their actual results in an external source-bound
run report. A manifest's existence or focused test pass is not a successful composite gate,
independent implementation agreement, production readiness or enterprise effectiveness.
