# OAC SealedResource Admission v1 — Proposed Draft

**Profile ID**: `oac.core.sealed-resource`  
**Profile version**: `v1`  
**Status**: experimental A1 portability overlay  
**Normative language**: MUST, MUST NOT, SHOULD, SHOULD NOT, and MAY follow BCP 14

## 1. Purpose

Core v0alpha1 resources predate an independent parser/digest experiment. `SealedResource/v1` closes
the raw-wire admission behavior required by `oac.ctk.stdio/v2` without changing historical resource
digests or the frozen stdio-v1 bundle.

This overlay validates one exact byte sequence. It does not authenticate its source, promote its
authority, or imply that the referenced enterprise facts are true.

## 2. Raw JSON admission

Input MUST be UTF-8 JSON encoding exactly one object. A decoder MUST reject duplicate object member
names before constructing a map. The decoded value MUST be finite RFC 8785 I-JSON and canonicalizable
without accepting NaN, Infinity, invalid Unicode scalars, or implementation-specific numeric domains.

JSON numbers are decoded in the RFC 8785/ECMAScript IEEE-754 binary64 domain before either digest or
typed validation. Thus the source token `9007199254740993` denotes the decoded value
`9007199254740992`; an implementation MUST NOT hash the rounded value while exposing the arbitrary-
precision token integer to semantic code. A Kind/Profile may impose a narrower safe/exact integer
range after this shared decode.

Top-level `apiVersion`, `kind`, and `digest` MUST be explicit members. `kind` MUST be a registered Kind;
`digest` MUST be `sha256:` followed by 64 lowercase hexadecimal characters. Omission cannot be repaired
by a model default.

## 3. Detached digest

Let `R` be the raw decoded object. Let `P` be an exact shallow copy of `R` with only top-level
`digest` removed:

```text
resourceDigest = "sha256:" + lowercase_hex(SHA-256(RFC8785(P)))
```

The result MUST equal `R.digest`. Nested fields named `digest` remain in `P`. Member order and
insignificant input whitespace do not affect RFC 8785 bytes; string values, array order, member
presence, and timestamp spellings do.

## 4. No implicit normalization

After structural and semantic validation, an implementation MUST preserve every explicitly supplied
value and member presence in `R`. A parser MUST NOT change the admitted raw projection or use its typed
projection as the resource digest input. A parser MUST NOT:

- trim or case-fold a string;
- insert a missing default into the admitted raw projection;
- remove an explicit null or empty collection;
- reorder an array;
- rewrite a timestamp or number spelling into another semantic value before digest verification;
- upgrade an enum, status, authority, or source reference.

An owning published Schema MAY define omission semantics for an optional member. Typed evaluation may
use that declared default, but the member remains absent from `R` and from its digest projection. An
undeclared implementation default is forbidden. `NonBlankString/v1` decides whether at least one
scalar falls outside its frozen White_Space set; it does not change the string. Rewriting any explicit
member is `CANONICAL_ADMISSION_MISMATCH`.

Implementations whose model library cannot remember explicitly-set versus defaulted fields MUST
validate the raw map separately or reject the input. Library convenience is not a wire rule.

## 5. Timestamp lexical form

The v1 portability capsule admits timestamps only as:

```text
YYYY-MM-DDTHH:MM:SSZ
```

The calendar/time value MUST be valid RFC 3339 UTC at whole-second precision with calendar year
`0001` through `9999`. Fractional seconds,
lowercase separators, explicit `+00:00`, non-UTC offsets, missing seconds, and leap seconds are outside
this capsule even if a host library can normalize them. A future version may widen accepted spellings
without changing v1.

## 6. Set-owned arrays

The Supplier v0.2 capsule declares these arrays set-owned and therefore duplicate-free before semantic
derivation: metadata source refs; completeness covered node/relation refs and known gaps; change scope
refs; predicate after values, selector refs/types/domains, and relation types; required evidence,
qualifications, responsibility types, prerequisite obligation types; and separation constraint pairs
where the owning Profile declares uniqueness.

Array order remains part of the raw resource digest even when semantic evaluation projects a sorted
set. An implementation MUST reject duplicates rather than silently deduplicate them. Metamorphic tests
may reorder a set-owned array and reseal the resource; semantic facts may remain equivalent while all
root-bound derived IDs correctly change with the new root digest.

## 7. Error boundary

| Failure | Stable code |
|---|---|
| JSON grammar, duplicate key, missing/invalid envelope, schema/set violation | `CORE_SCHEMA_INVALID` |
| unregistered raw or expected Kind | `CORE_KIND_UNKNOWN` |
| finite I-JSON/canonicalization domain | `NON_I_JSON` |
| detached root digest mismatch | `ROOT_DIGEST_MISMATCH` |
| typed parse rewrites admitted raw map | `CANONICAL_ADMISSION_MISMATCH` |
| published resource ceiling | `CONFORMANCE_RESOURCE_PROFILE_EXCEEDED` with `RESOURCE_EXHAUSTED` |

No failure in this layer is an OAC Plan verdict.

## 8. Claim boundary

Agreement under this overlay establishes raw resource interoperability only for the named Kind/Profile
and vectors. It does not establish source authenticity, enterprise authority, semantic derivation
parity, plan verification, organizational independence, security, or production readiness.
