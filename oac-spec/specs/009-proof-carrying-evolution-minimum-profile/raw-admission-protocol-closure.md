# Raw admission protocol closure

This follow-up supersedes the completion scope of `raw-admission-successor.md`. Seed-4 remains an
immutable intermediate candidate, with its original source and manifest preserved by seed-5.
No historical evidence is rewritten or reclassified as a successful complete validation.

## Review observations retained

Independent review after seed-4 publication found that configured decoder exhaustion had the correct
reason code but the wrong exception class for CTK dispatch, and the OrganizationPlan pre-reader had
not received the configured depth limit. Reproductions returned ERROR/CORE_SCHEMA_INVALID for a
1200-level Plan and ERROR/CONFORMANCE_RESOURCE_PROFILE_EXCEEDED for a 10000-level Snapshot, instead
of RESOURCE_EXHAUSTED/CONFORMANCE_RESOURCE_PROFILE_EXCEEDED. The candidate's complete gate was
interrupted before source correction; that interrupted run is not a pass. Review of the remaining
raw protocol paths also found the same pre-existing decoder gap in CTK v1 canonicalize.

Product integration found a second consistency gap: the same OutcomeObservation can legitimately
appear as both an observation and dimension evidence. Concatenating role references made duplicate
metadata.sourceRefs, while the existing sealed-resource profile rejects that duplicate set member.
Removing the evidence role would lose part of the declared evidence closure.

## Accepted behavior and completed tasks

- [x] CTK v1 canonicalize and CTK v2's three resource kinds return the existing
  RESOURCE_EXHAUSTED/CONFORMANCE_RESOURCE_PROFILE_EXCEEDED pair for decoded resource depth or decoder
  capacity exhaustion. All pre-readers apply the configured depth limit before recursive projection.
- [x] ResourceProfileExceeded retains its exception identity. When the parser cannot observe the
  exact nesting depth, observed is None; no depth measurement is invented. The configured maximum
  is retained. Known measured-limit failures retain their existing numeric metadata.
- [x] Decoded request envelopes are depth-checked before JCS recursion. If an extreme envelope
  cannot be decoded reliably, the process returns a clear input failure with no response or guessed
  request identity. This remains a protocol input failure rather than a domain verdict.
- [x] CLI convenience decoding and in-memory canonicalization map recursive input failures to their
  existing CORE_SCHEMA_INVALID or NON_I_JSON reasons. Lowering keeps its existing input-digest error
  mapping; annotation document/archive endpoints keep their specific admission reasons.
- [x] OutcomeCertificate metadata.sourceRefs lists each source resource ID once, preserving the
  order of first occurrence across the existing source roles. `outcome_certificate_source_refs`
  is the one authoring/verification projection. Full references remain in observationRefs,
  evidenceRefs and each dimension. A verifier requires exact incoming metadata equality; admission
  never silently deduplicates, reorders, re-seals or changes an externally supplied resource.

Tests cover actual CLI and both stdio protocol surfaces, 65/1200/10000-level payloads, decoder failure
metadata, annotation document/archive classification and shared observation/evidence provenance.
Valid sparse raw roots still pass and altered digests or duplicate incoming sourceRefs still fail.
No root-domain calculation, JSON schema, authority rule or unknown-outcome rule changes.

## Final successor

`oac.evolution.minimum/v0.1-seed-5` preserves seed-4's 53 exact bound materials and manifest, verifies
all earlier ancestors and adds this follow-up to the current contract closure. It records a corrected
candidate, not a new business capability. Its claim remains reference source identity, public root
vector and negative inventory; it does not establish enterprise effectiveness, production readiness,
external execution provenance or independent implementation agreement.

Publish only after protocol and integration review have converged. Use the existing exclusive
`--publish-new` command after validating a disposable candidate. Run the complete gate once for the
final fixed source and build into a new output directory. Keep the interrupted predecessor run and
its review observations separate from the final validation result.
