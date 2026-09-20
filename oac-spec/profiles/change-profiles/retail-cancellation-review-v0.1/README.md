# Retail cancellation review change profile v0.1

This development profile permits an explicitly mapped retail cancellation request to use the existing
zero-effect impact, obligation, role assignment and plan verification relation. It has no independent
implementation qualification and grants no execution authority.

## Identity and semantics

| Coordinate | Required value |
| --- | --- |
| CLI profile | `oac.retail.cancellation-review/v0.1` |
| `profileId` | `oac.retail.cancellation-review` |
| `profileVersion` | `v0.1` |
| `profileDigest` | `sha256:2238581f84f2e2d2980e3e21d4143b4cad54d2216a78e0e879ac62c5856ea491` |
| Change semantic type | `retail.cancel_requested` |
| Sole delta path | `/request` |
| Known values | Strings; the string `unknown` cannot replace an explicit Unknown state |
| Effect ceiling | `zero_effect` |

The digest is SHA-256 over the RFC 8785 canonical form of the complete adjacent `profile.json`.
The descriptor is closed, has a 16,384-byte input limit, and admits no callbacks, executable
validators, effect policy override, extra fields or arbitrary path. The descriptor schema provides
structure; the package-pinned digest and explicit profile selection provide the semantic boundary.

The descriptor reuses the existing applicability language and resource budget by their exact names.
That reuse does not qualify this retail profile under the Supplier conformance capsules.
Neither `pending` nor `confirmed` is an upstream organizational truth label: the snapshot's admitted
rules determine the meaning of those values for this local mapping.

## Required verification procedure

1. Select the profile explicitly from the package's finite supported choices. Do not derive that
   selection from an untrusted plan or accept a caller-provided descriptor as authority.
2. Resolve and validate the package descriptor. Its complete canonical digest must equal the pinned
   profile digest. Missing, altered or unregistered profiles fail closed.
3. Admit the exact snapshot, change and plan roots. Preserve source provenance, namespace, owner,
   governance, input revisions and digests using the existing typed or raw admission boundary.
4. Require an admitted `retail.cancel_requested` change with exactly one `/request` delta. Pass that
   path into every existing predicate evaluation, including edges, rules and Unknown transition
   duties. The rest of the derivation algorithm is shared with Supplier.
5. Require the plan's `spec.profileBinding` to match all three identity fields above. Removing or
   changing a binding and resealing the plan must yield `REJECT` with
   `CHANGE_PROFILE_BINDING_MISMATCH` under this profile.
6. Run the existing topology-neutral relation checks over paths, obligations, role qualification,
   evidence, ordering, unresolved observations and the zero-effect ceiling. The verifier never
   imports the compiler or requires one preferred topology.
7. Emit the original public `PlanCertificate` kind with the selected `spec.profileBinding`, exact
   source roots, exact subject plan root and computed verdict. A downstream gate must check the
   expected profile binding and roots and recompute verification; a supplied `ACCEPT` string or
   a self-consistent detached certificate digest is insufficient.

`oac validate` is structural validation and is not a substitute for this procedure. The `verify`
command retains the original CLI convention: exit 0 means a certificate was emitted, including a
`REJECT` certificate. Callers must inspect `spec.verdict` and the exact binding, not only exit status.

## Usage

```sh
python -m oac compile snapshot.json change.json \
  --profile oac.retail.cancellation-review/v0.1 -o plan.json
python -m oac verify snapshot.json change.json plan.json \
  --profile oac.retail.cancellation-review/v0.1 -o certificate.json
```

The installed `oac` command calls the same CLI. The library equivalents are `compile_change` and
`verify_change` with a required `profile=` argument. Existing `compile_supplier_change`, `verify_plan`
and CLI invocations without `--profile` still select `oac.supplier.transfer/v0.2`. Supplier plans and
certificates omit `profileBinding`; the four established Supplier regression resources retain their
canonical bytes. Frozen Supplier stdio-v2/v3 adapters reject the new field, including explicit null,
so that their wire boundary and Go qualification scope remain Supplier-only.

## Execution and evidence boundary

This profile does not extend `RuntimeBinding`, `ZeroEffectRuntimeBundle`, the default lowering command,
or Spec 009 outcome verification. Public sandbox writes require a separate disposable runtime
descriptor, authority gate, tool scope, budget, state reset receipt and independent outcome oracle.
Do not label a real sandbox write as a zero-target-write bundle.

The local task 113 fixture has three obligations, two roles, no dependency edges and no prerequisite
rules. Its generated plan therefore imposes no review-before-cancel constraint. A consumer must not
invent such ordering from role names. This deliberately limited case compares preselected roles; it
does not establish general superiority over graph-only or fixed-team systems.

Future business profiles require a reviewed versioned descriptor, an explicit registry addition and
counterexamples for that profile's actual business semantics. This package does not claim support
for arbitrary retail operations, policy compliance, autonomous execution, enterprise reuse or ROI.
