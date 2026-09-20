# Disposable local Outcome profile v0.1

This opt-in extension binds a portable `OutcomeCertificate` to evidence from a disposable local sandbox. It reuses the Spec 009 verifier and root projections. It neither executes a tool nor grants credentials. Its examples are constructed contract fixtures with synthetic reference digests, not customer runs, qualified human judgments, or observed business outcomes.

## Closed descriptor and selection

`profile.json` is package-pinned using RFC 8785 canonical JSON and SHA-256. The only supported binding is:

```json
{"profileId":"oac.outcome.disposable-local","profileVersion":"v0.1","profileDigest":"sha256:73d922826b1eef350a13a81fb4626ccfff1aca68b426eacc79d9db84311d9e58"}
```

`OutcomeCertificate.spec.profileBinding` explicitly selects this contract. Unknown IDs/versions, a different digest, an edited descriptor, an extra callback, and mixed default/disposable kind pairs fail closed. A missing binding always selects the unchanged Spec 009 minimum profile; kind names never infer this extension. Null optional fields serialize as absent. Existing unbound certificate bytes and zero-effect execution-root vectors remain unchanged.

## Plan and execution authority

The referenced `OrganizationPlan`, its role instances and work units retain the core `zero_effect` ceiling. An accepted `PlanCertificate` proves only its declared structural verification scope; it cannot authorize sandbox writes. The product controller separately issues and consumes a one-use local sandbox grant after checking approval, frozen profile/mapping, exact plan work units, scope, tool capabilities, order and budget. This grant never grants production access.

The execution root requires exactly these external reference kinds:

| Coordinate | Required kind |
| --- | --- |
| runtimeBindingRef | DisposableRuntimeBinding |
| runtimeBundleRef | DisposableRuntimeBundle |
| executionReceiptRef | ExecutionReceipt |
| executionAuthorizationRef | DisposableExecutionGrant |

These remain external runtime-owned payloads, not registered OAC runtime implementations. The binding and bundle payloads must explicitly identify the same grant and retain the plan's zero-effect ceiling. The product receipt validator is responsible for checking those payloads and the grant's trusted issuer, exact scope, freshness, one-use consumption, initial state, work-unit ordering, actual effects, reset and observation integrity. Passing a fabricated `ResourceRef` to OAC does not satisfy these duties. OAC verifies exact references, namespaces, roots, provenance and verdict consistency; it does not load external payloads, authenticate human identities, verify signatures, or rerun the business oracle.

The new `X` root uses domain `oac.state/disposable-execution-evidence/v0.1`. It commits `profileId`, `profileVersion`, `profileDigest`, `executionAuthorization` (one exact grant ref), and the existing `runtimeBinding`, `runtimeBundle`, `executionReceipt`, `executionEvidence` sections. The new domain and grant prevent treating disposable writes as a `ZeroEffectRuntimeBundle`. `S`, `D`, `P`, `O`, and `E` projection procedures remain unchanged. The certificate digest commits its complete body, including profile and all six dimensions.

## Six dimensions and non-success conservation

The dimension-name set must be exactly `task_goal`, `forbidden_effects`, `evidence_completion`, `scope`, `replayability`, `unresolved_observations`; order is not semantic, duplicates and extra names are invalid.

- `PASS` requires at least one evidence ref, no reasons and no unresolved refs.
- `FAIL` requires at least one evidence ref, at least one reason and no unresolved refs in that dimension.
- `UNKNOWN` requires at least one reason and at least one exact unresolved ref; known partial evidence may also be retained.
- Each dimension evidence ref must appear in top-level `evidenceRefs`. All dimension refs must share the certificate namespace; each reference group must be unique. Reasons must be sorted and unique.
- Top-level `reasonCodes` and `unresolvedRefs` must equal the exact unions of dimension reasons and unresolved refs. Extra or silently dropped unknown observations are invalid.
- Any `FAIL` requires overall `REJECT`, retaining any other unknown dimensions. Otherwise any `UNKNOWN` requires overall `UNKNOWN`. Only six `PASS` dimensions permit `ACCEPT`. `PROVISIONAL` is not a supported verdict for this explicit profile.

The oracle must be an `OutcomeOracle`, its observation profile an `ObservationProfile`, and actors `Principal` refs. Relabelling an actor, runtime binding/bundle, receipt, execution evidence or grant with the same namespace and ID as the oracle does not establish separation.

## Public API and CLI

```python
from oac.evolution import (
    outcome_certificate_source_refs,
    project_execution_root,
    verify_outcome_certificate,
)
from oac.outcome_profiles import disposable_outcome_binding, get_outcome_profile

profile = disposable_outcome_binding()
descriptor = get_outcome_profile(profile)
x_root = project_execution_root(
    disposable_binding_ref, disposable_bundle_ref, execution_receipt_ref,
    execution_evidence_refs,
    profile_binding=profile,
    execution_authorization_ref=controller_grant_ref,
)
# Supply profileBinding=profile, executionAuthorizationRef=controller_grant_ref,
# executionRoot=x_root and all ordinary frozen S/D/P + observation coordinates
# when constructing OutcomeCertificateSpec. Construct ResourceMetadata with:
source_ids = outcome_certificate_source_refs(spec)
# Seal with oac.canonical.seal_resource after setting that exact sourceRefs tuple.
verify_outcome_certificate(certificate)
```

`outcome_certificate_source_refs` preserves the original Spec 009 order and appends the grant ID last for this profile. It is a convenience projection, not admission. The full constructed examples are in `examples/accept.json`, `examples/reject.json`, `examples/unknown.json` and `examples/reject-with-unknown.json`.

```sh
uv run oac validate-evolution profiles/outcome-profiles/disposable-local-v0.1/examples/accept.json
uv run oac validate-evolution profiles/outcome-profiles/disposable-local-v0.1/examples/reject-with-unknown.json
```

Both commands report a valid certificate; validity does not mean its business verdict is ACCEPT. `oac validate` alone remains structural validation. The same public `validate-evolution` command works from a built wheel outside the source checkout. No oracle callback or unrestricted profile plug-in is accepted.

## Verification ceiling

Unit and CLI mutation tests exercise this reference-only contract, default compatibility and reject/unknown preservation. They do not prove actual sandbox behavior, public-task performance, real organization independence, customer economics, production permission, formal independent conformance or general enterprise effectiveness. A production bridge must validate and retain the separately executable payload closure before issuing this certificate. Historical source-closure signatures are immutable: implementation changes require a new evidence coordinate, not edits to old seed-2 identities.
