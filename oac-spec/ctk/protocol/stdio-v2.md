# OAC CTK raw-resource adapter protocol v2

**Protocol version**: `oac.ctk.stdio/v2`  
**Status**: experimental A1 Supplier v0.2 portability capsule  
**Compatibility**: successor protocol; it does not mutate `oac.ctk.stdio/v1`

## 1. Process boundary

The runner starts a fresh process per request, writes exactly one RFC 8785 JSON object plus newline,
closes stdin, and accepts one response object. Stderr is diagnostic only. Request IDs, status layering,
process/output/time/depth ceilings, duplicate-key rejection, and environment isolation retain stdio-v1
semantics.

The SUT receives no case ID, expected output/digest, mutation name, fixture path, RequirementSet path,
or bundle path. Resources are raw JSON bytes carried as canonical Base64 strings so the SUT, rather
than the runner's host parser, owns resource admission.

## 2. Request and response

```json
{
  "protocolVersion": "oac.ctk.stdio/v2",
  "requestId": "request-1",
  "operation": "validateResource",
  "payload": {}
}
```

Successful responses use `sutStatus=COMPLETED` and exactly one `result`. Incomplete responses use one
`error={"code":"..."}` with `ERROR`, `UNSUPPORTED`, or `RESOURCE_EXHAUSTED`. An implementation MAY
add a string `detail`; it is diagnostic, non-normative, and unscored. Differential comparison binds
`sutStatus` and the stable `error.code`, never host-language error text.

## 3. Capabilities

`capabilities` has `{}` payload. The successor seed-2 evidence set reports the exact closed tracks in
its digest-bound `ProtocolCapabilitySet`:

```text
semantic-kernel × validateResource × oac.core.sealed-resource × v1 × oac.ctk.stdio/v2
semantic-kernel × derive           × oac.supplier.transfer.portability-capsule
                                  × v0.2-seed-2 × oac.ctk.stdio/v2
```

The resulting report remains bound to semantic coordinate
`profileId=oac.supplier.transfer/profileVersion=v0.2`. The capability coordinate names only the frozen
evidence set; it MUST NOT be interpreted as complete Supplier v0.2 support. An implementation MUST NOT
advertise `oac.supplier.transfer/v0.2` until a complete required set is frozen and passed. Declaring
`derive` does not declare `verify`. Missing or adding a track relative to the selected capability set
fails its cases.

## 4. SealedResource/v1 admission

Both operations decode Base64 strictly and admit the resulting bytes as `SealedResource/v1`:

1. valid UTF-8 JSON object with duplicate keys rejected;
2. finite RFC 8785 I-JSON domain;
   numbers are decoded as IEEE-754 binary64 before digest and typed validation;
3. explicit top-level `apiVersion`, `kind`, and non-null lowercase SHA-256 `digest`;
4. exact structural and semantic validation for the expected Kind;
5. claimed digest equals `sha256(JCS(raw decoded object minus only top-level digest))`;
6. typed validation preserves every explicit member without trimming, field loss, enum/time rewriting,
   or set reordering; schema-declared omission defaults may guide semantics but never enter the raw
   digest projection;
7. every timestamp is the whole-second UTC spelling `YYYY-MM-DDTHH:MM:SSZ`, year `0001..9999`;
8. every Base64 value is canonical RFC 4648 spelling: strict decode followed by re-encode MUST be
   byte-identical, including pad bits and padding.

Shape/type/envelope failures are `ERROR/CORE_SCHEMA_INVALID`; unknown Kind is
`ERROR/CORE_KIND_UNKNOWN`; duplicate JSON is `CORE_SCHEMA_INVALID`; finite
I-JSON failures are `ERROR/NON_I_JSON`; digest mismatch is `ERROR/ROOT_DIGEST_MISMATCH`; a valid typed
parse that rewrites the admitted map is `ERROR/CANONICAL_ADMISSION_MISMATCH`. Resource ceilings use
`RESOURCE_EXHAUSTED/CONFORMANCE_RESOURCE_PROFILE_EXCEEDED` and return no partial result.

## 5. validateResource

Input is closed:

```json
{"expectedKind":"OrganizationSnapshot","rawBase64":"..."}
```

`expectedKind` is `OrganizationSnapshot`, `SemanticChangeSet`, or `OrganizationPlan` in this capsule.
Output is:

```json
{
  "kind":"OrganizationSnapshot",
  "resourceId":"snapshot:example",
  "resourceDigest":"sha256:<64hex>"
}
```

The result binds the admitted raw bytes, not a normalized model projection.

## 6. derive

Input is closed:

```json
{"snapshotBase64":"...","changeBase64":"..."}
```

The resources MUST admit as `OrganizationSnapshot` and `SemanticChangeSet`, share the Supplier v0.2
authority envelope, and satisfy `profiles/supplier-change/profile.md`. Output is closed:

```json
{
  "report": {
    "apiVersion":"oac.derivation/v0alpha1",
    "kind":"ProfileDerivationReport"
  },
  "reportDigest":"sha256:<64hex>"
}
```

`reportDigest = sha256(JCS(report))`. The runner MUST recompute it and compare exact report JCS bytes;
the SUT's digest assertion is not a trust root. The report contains no RoleInstance, WorkUnit,
principal selection, PlanDecision, compiler version, plan status, or topology.

## 7. Claim boundary

Passing the initial portability capsule establishes only the named implementation's raw-admission and
topology-free Supplier derivation agreement on the exact frozen/generated set. It does not establish
fixed-plan verification, organizational independence, complete OAC conformance, production safety,
enterprise correctness, or standard consensus.

Fresh processes, empty working directories, environment allowlisting, and bounded I/O provide process
hygiene and isolation from accidental repository access. They are not a hostile-code sandbox: an
untrusted SUT requires an external no-network, read-only, CPU/memory/PID/file-size-bounded container or
VM policy.
