# OAC CTK fixed-Plan verification protocol v3

**Protocol version**: `oac.ctk.stdio/v3`  
**Status**: experimental bounded Plan-verification successor  
**Compatibility**: does not mutate `oac.ctk.stdio/v1` or `oac.ctk.stdio/v2`

## 1. Process boundary

The runner starts a fresh process for one request, writes exactly one RFC 8785 JSON object plus LF,
closes stdin, and accepts exactly one response object. Stderr is diagnostic only. The runner owns
absolute request, response, time, decoded-depth, and process ceilings.

The SUT receives no case ID, expected verdict, expected reason, mutation name, RequirementSet path,
fixture path, reference report, or topology witness. Raw resources use canonical RFC 4648 Base64 so the
SUT owns duplicate-safe parsing, typed admission, and digest verification.

## 2. Envelope

Requests are closed:

```json
{
  "protocolVersion": "oac.ctk.stdio/v3",
  "requestId": "opaque-request-id",
  "operation": "verifyPlan",
  "payload": {}
}
```

Success uses `sutStatus=COMPLETED` and exactly one `result`. Admission or input failure uses
`sutStatus=ERROR` and exactly one `error={"code":"..."}`. Unsupported operations use `UNSUPPORTED`;
resource ceilings use `RESOURCE_EXHAUSTED`. A diagnostic `detail` is unscored.

## 3. Capabilities

`capabilities` has `{}` payload. The seed-1 capability set contains exactly one track:

```text
plan-verifier × verifyPlan × oac.supplier.plan-verification.plural-capsule
              × v0.1-seed-1 × oac.ctk.stdio/v3
```

Declaring this track does not declare complete Supplier verification, derivation portability,
organizational independence, compiler conformance, outcome verification, or production safety.

## 4. verifyPlan

The payload is closed:

```json
{
  "snapshotBase64": "...",
  "changeBase64": "...",
  "planBase64": "..."
}
```

The SUT MUST admit the bytes as `OrganizationSnapshot`, `SemanticChangeSet`, and `OrganizationPlan`
under `SealedResource/v1`, then evaluate
`standard/oac-plan-verification-relation-v0.1.md`. The completed result is closed:

```json
{
  "snapshotDigest": "sha256:<64hex>",
  "changeDigest": "sha256:<64hex>",
  "planDigest": "sha256:<64hex>",
  "verdict": "ACCEPT",
  "reasonCodes": []
}
```

All digest fields MUST equal the admitted resource digests. `reasonCodes` MUST be sorted, unique, and
registered. The runner compares the unique verdict plus required/forbidden reason policies; it does
not compare a certificate byte-for-byte.

## 5. Bounded wire ceiling

Seed-1 limits the complete request to 1 MiB, including Base64 expansion. This is intentionally smaller
than the current standalone Plan resource ceiling and therefore is not a complete transport for every
schema-admitted Plan. A future protocol MUST add streaming or harmonize these limits before making a
general Plan-verification claim.
