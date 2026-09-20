# Raw CLI acceptance closure

This successor closes the remaining command-routing gap after
`raw-admission-protocol-closure.md`. Seed-5 and its predecessors retain their exact
materials and claim limits. A valid raw-sealed resource with an integral JSON number
written as `1.0` was still rejected by the CLI's preliminary strict typed parse,
although sealed admission accepted the same bytes. The preliminary parse also read
the resource again before semantic verification. This was an input-boundary defect,
not evidence that raw inputs should be re-sealed or receive model defaults.

## Command contracts

| Command | Input admission | Digest and defaults |
| --- | --- | --- |
| `validate` | Decode the raw JSON once, normalize its existing binary64 numeric domain, then validate structure | Missing or null digest and valid-format stale digest are permitted; typed defaults support draft inspection |
| `digest` | The same structural draft validation | Compute SHA-256 over JCS of the supplied map minus only its top-level digest; absent defaults stay absent |
| `validate --verify-digest` | Admit the original sealed bytes using the registered raw kind | Require explicit sealed envelope fields and the exact raw-map digest |
| `validate-evolution` | Admit each resource argument once, then dispatch on the admitted resource | All three Spec 009 roots and the Demand Snapshot retain their original digest identity |
| `compile`, `verify`, `lower` | Existing expected-kind sealed admission | Existing profiles, semantic checks and lowering error mapping remain in force |

Integer, decimal and exponent spellings of the same integral binary64 value have
the same acceptance and digest. This includes values outside JCS's safe Python-int
domain such as `1e20`; normalizing for schema validation must not later narrow the
accepted raw numeric domain when computing the digest. Fractions, booleans and
strings remain invalid for strict integer fields. Duplicate JSON keys, non-finite
numbers, malformed JSON and invalid digest formats remain rejected.

Unknown kinds retain `CORE_KIND_UNKNOWN` before sealed-envelope checks. Wrong
registered evolution roots or Snapshot kinds retain `EVOLUTION_REF_KIND_MISMATCH`;
a Demand without `--snapshot` retains `EVOLUTION_ROOT_INCOMPLETE`. Missing/null
sealed digests and stale sealed digests remain distinct admission errors. No
command repairs supplied bytes, writes them back or mixes their identity with
materialized defaults. Historical typed SDK, TCK and demo behavior is unchanged.

## Tasks and validation evidence

- [x] Replace the preliminary typed CLI routing with a single admitted input record.
- [x] Keep structural draft validation separate from sealed verification while sharing raw decoding.
- [x] Preserve raw numeric equivalence, sparse membership, root identity and stable routing errors.
- [x] Cover all three evolution roots and Snapshot, both validation flag modes,
  raw digest computation, draft/default behavior and one read per resource argument.
- [x] Retain all existing source, root-domain, unknown-outcome and authority boundaries.

`oac.evolution.minimum/v0.1-seed-6` captures seed-5's 54 exact bound materials and
manifest, verifies every earlier ancestor and binds this follow-up plus the CLI
acceptance tests. The fixed-Plan current-source check separately uses the existing
`v0.1-repro-4` successor mechanism, with unchanged exact artifact and byte comparison.
Its approximately 10 MB of retained process traces is test evidence, not added
production logic. Both writers refuse to overwrite published coordinates.

The previous seed-5 complete check was terminated by a damaged local Python
interpreter, after its pytest stage had passed. That run is environment-invalid,
not a completed validation of seed-5. Final validation uses the restored ARM
CPython 3.12.13 runtime and a private candidate environment. Publication and full
check results are recorded separately from this source contract. These materials
establish controlled-local input consistency and exact source/reproduction closure;
they do not establish deployment readiness, enterprise effectiveness or business ROI.
