# OAC Shadow Mechanics MVP Validation

**Validation date**: 2026-08-23  
**Code evidence revision**: `424004e`  
**Status**: implemented and reproducible technical MVP  
**Claim ceiling**: zero-effect contract mechanics; no formal conformance, Human Gold, enterprise
effectiveness, runtime portability, or production authorization

## What is proven

The implemented slice accepts the exact chain:

```text
OrganizationSnapshot + SemanticChangeSet
              ↓ reference compiler
       OrganizationPlan
              ↓ compiler-independent verifier
        PlanCertificate
```

It proves that one admitted Supplier status change can deterministically produce an obligation-bound,
zero-effect task organization; that two different WorkUnit topologies can satisfy the same frozen
constraints; and that targeted authority, omission, qualification, separation, order, evidence,
minimality, digest, and Unknown corruptions are rejected.

The verifier is independent of compiler strategy, topology, hidden reasoning, and mutable state. It
shares `src/oac/supplier.py` as the executable Profile oracle with the compiler, so this result does
not prove semantic-implementation independence.

## Reproduced gates

| Gate | Result |
|---|---|
| clean archived revision | `uv sync --all-extras --locked` succeeded |
| lint | Ruff passed over `src`, `tests`, `scripts`, and `tck` |
| Schema drift | Draft 2020-12 schemas and 15-rule semantic registry matched generators |
| benchmark drift | four deterministic Run Manifests matched generators |
| TCK | 22/22 passed |
| tests | 74/74 passed |
| branch coverage | 90.88%, threshold 90% |
| plural witness | SC-001 plans with 5 and 4 WorkUnits both returned `ACCEPT` |
| registered mutation families | 8/8 rejected, target at least 90% |
| source-tree demo repeatability | identical plan and certificate digests on repeated runs |
| clean-revision demo | `ACCEPT`, 17 obligations, `zero_effect` |
| installed wheel outside repository | demo `ACCEPT`; package-owned TCK 22/22 |

The final revision was expanded and validated in a recoverably retained clean directory outside the
repository; it does not depend on untracked source files. The isolated installed-wheel check is also
retained outside the repository. Exact recovery coordinates remain in the internal audit log rather
than the distributable source tree.

## Deterministic artifacts

- snapshot digest: `sha256:f4a1f14f6284c0a9355300ad8e42c49885b6a3e3c8fa9766c4ca5811dc116cf0`
- plan digest: `sha256:a0b616f375f80efb6964debac1fa1263930b6615a893a31a34ce48a39420403e`
- certificate digest: `sha256:d424f5332e480c9d01314a5cf1b6212edc3e70b6f8158a53cb92ea82c45c928c`
- matched input-set digest: `sha256:cdf226d4077f7375566e2f766ee1def5e430ca6590a207fc396b1dc438a14dcc`
- input-closure digest: `sha256:aa61274ba07c2800c543c3385dcfafbd58f18cbac8db03770084f9c912303fdf`
- reference implementation-closure digest: `sha256:6ba0f0bcfc8f3688fddfdd7cd4aa8882fc428e172060d2f2701a958251ea230c`
- wheel SHA-256: `e5868bb1b17a65576c0f638d6a0ba4739e73e045d83cb87d25bde2cb7227a3ec`

The wheel bytes reproduced exactly between the working tree and clean archived revision. Both source
distributions built successfully, but their raw archive bytes are not used as a reproducibility claim.

## Exploratory matched-input result

| System | Exact candidate role-set matches | Micro precision | Micro recall |
|---|---:|---:|---:|
| OAC reference | 7/10 | 0.935484 | 0.852941 |
| fixed team | 2/10 | 0.640000 | 0.941176 |
| initiator only | 1/10 | 1.000000 | 0.294118 |
| graph only | 0/10 | 0.705882 | 0.705882 |

These are agreement scores against project-authored exploratory candidate roles, not Human Gold. They
show useful engineering behavior and three visible Profile gaps; they do not prove superiority.

## Red-team changes incorporated

- Cross-namespace/governance/owner/source/scope roots now fail closed; federation is unsupported.
- The Snapshot owner is an admitted resource-custodian RoleDefinition; external source and governance
  identifiers remain declared/pinned rather than falsely treated as authenticated.
- Delta operations are bound to before/after value states; a forged `remove` cannot be accepted.
- Supplier changes contain exactly one `/status` delta; unrelated deltas cannot be silently ignored.
- Plan and certificate envelopes bind exact source roots and authority context.
- Happens-before edges reject duplicates, dangling endpoints, arbitrary reasons, cycles, and
  non-required role-pair projections.
- WorkUnit contributors and accountability must have relevant obligation bindings.
- Certificate witnesses are deterministically sorted, capped at 32, and report omitted count.
- JSON Schema is explicitly structural; machine-readable semantic rules and parity vectors publish the
  required second validation layer.
- The wheel includes its full Apache-2.0 text and package-owned schemas, Profile machine assets, and TCK.
- Promotion-only annotation states are rejected until Spec 004 supplies proof-carrying human review.
- Exploratory constraints use `CandidateConstraintSet`; no wire/schema type calls them Gold.
- Run Manifest v1.1 publishes complete input and implementation file closures, including annotation
  packets and the scorer; mutation tests prove either closure changes when its bytes change.

## Gates intentionally still open

1. **Profile validity**: SC-008, SC-009, and SC-010 require Spec 002 subject/scope/Unknown semantics;
   candidate-role alignment remains 7/10.
2. **Independent implementation**: Spec 003 requires a parser-, derivation-, and verifier-independent
   implementation; current tools share one Profile oracle.
3. **Human evidence**: zero qualified independent annotation rounds and zero adjudicators exist.
4. **Outcome evidence**: EnterpriseOps-Gym is pinned but not mapped, downloaded, or executed.
5. **Runtime and learning**: no transport, external write, OutcomeCertificate, Pattern admission, Skill
   promotion, UI, enterprise connector, or self-authorized evolution is implemented.
6. **Standardhood**: no external implementer, multi-stakeholder governance, compatibility history, or
   standards-body recognition exists.

These are the next evidence gates in Specs 002–006, not hidden completion work inside MVP 001.
