# OAC Contextual Applicability v0.2 Validation

**Validation date**: 2026-08-23  
**Code evidence revision**: `3e66c6853b1f0f2558d48af0da285790ea4c3903`  
**Version**: `0.2.0a0`  
**Status**: implemented and reproducible contextual technical slice  
**Claim ceiling**: internal Profile consistency and zero-effect contract mechanics; no formal
conformance, Human Gold, enterprise effectiveness, runtime portability, or production authorization

The historical M0 report remains unchanged in [STATUS.md](STATUS.md). This report is additive evidence
for M1 rather than a rewrite of an older result.

## What is proven

The implemented slice evaluates Snapshot-owned, versioned subject/scope/relation/status predicates
with Strong-Kleene `TRUE/FALSE/UNKNOWN`, emits a witness-bound applicability ledger, derives paths,
obligations and orders, and verifies a producer-chosen zero-effect plan from the frozen roots.

The following distinctions are executable rather than prose-only:

- root Unknown maps to `UNKNOWN`; bounded derived gaps map to `PROVISIONAL`;
- semantic FALSE is distinct from an authoritative FALSE cut for candidate/uncovered targets;
- the changed subject is a closure seed, while non-seed Affected dependency targets require admitted
  owner roles;
- a zero-obligation no-op requires a non-empty all-FALSE bounded-non-impact proof;
- role-wide dependency ordering and obligation-specific prerequisite ordering bind exact per-edge
  reasons, including plural WorkUnit topologies;
- legacy v0alpha1 inputs and plural witnesses retain their original bytes and detached digests.

The verifier does not import compiler strategy, but compiler and verifier still share
`src/oac/supplier.py` as one executable Profile oracle. This is not code-independent semantic
verification; Spec 003 owns that gate.

## Reproduced gates

| Gate | Result |
|---|---|
| locked revision | signed-off implementation commit `3e66c68` |
| clean archived revision | `uv sync --all-extras --locked` succeeded |
| lint | Ruff passed over `src`, `tests`, `scripts`, and `tck` |
| Schema drift | generated Draft 2020-12 schemas and registries matched |
| benchmark drift | all four deterministic Run Manifests matched |
| TCK | 33/33 passed from source, clean archive, and installed wheel |
| tests | 160/160 passed from source and clean archive |
| branch coverage | 90.90%, threshold 90% |
| registered mutation families | 15/15 rejected by the test suite |
| plural witnesses | both frozen legacy plans remained `ACCEPT`; contextual split-role matrix remained `ACCEPT` |
| adversarial replay | 34 focused vectors passed; no reproducible P0/P1 remained |
| repeatability | repeated source demos produced identical bytes and Plan/Certificate digests |
| legacy compatibility | frozen v0alpha1 snapshots and plural witnesses had zero Git byte diff |
| installed wheel outside repository | demo `ACCEPT`; package-owned TCK 33/33; predicate registry readable |
| reproducible wheel | source-tree and clean-archive wheel bytes were identical |

The clean archive and isolated wheel environment are retained recoverably outside the repository.
Local test caches were moved out of the source tree. Exact recovery coordinates remain in the internal
audit log rather than the distributable source tree.

## Deterministic artifacts

- contextual full Snapshot digest: `sha256:76073e824054b6a6b7de66e2b728069dea6ba83164f47d116f9f6eb0d4b1fe65`
- contextual root-Unknown Plan digest: `sha256:6e38d786134bb75a083fa916667c70f481c58327e3b0c32633335bd8c7ef8556`
- legacy witness A digest: `sha256:4d65b836b51881fca5f2f30b97c809c2cacb01a491f64529514517947045f5ce`
- legacy witness B digest: `sha256:adf3958db849222a903bcd0af8a31c3aa45a07920a31c1ff965317535a114949`
- matched input-set digest: `sha256:67ba894d95b23181cb850c318a6923e34e7d1f3611ac3b1b9360796fcb831799`
- benchmark fixture-set digest: `sha256:a6a6fccd55aa76034c87436b0e3f97966adce0ae1a89520afd221f1d8b588406`
- benchmark input-closure digest: `sha256:6af686f553dab6f011bad1d13d8a36c560954c8ecf95cae6199acb57200456b8`
- reference implementation-closure digest: `sha256:9e3972a368f31634d17058f16101a5bbf14ba65f1027ab89025add2dad4f9e5f`
- reference Run Manifest digest: `sha256:6c81cadea767822d54e8646377b45e81d1445adfc1672622e7fa117f2a4bd2bb`
- demo Plan digest: `sha256:57ce82672300e63553230c6700b89933c9bbcdf9d1c397e5023f924e98d8b694`
- demo Certificate digest: `sha256:446bf870af8750522da31cb9ac82e3619729e2d67090222025e031c19446067c`
- wheel SHA-256: `73e56f771bc35b1e6ca3f59f1a8478797bfc2133770c8c7721b12e81e4cb6e3d`

## Exploratory matched-input result

| System | Exact candidate role sets | Candidate verdicts | Full required-obligation-type coverage | Micro precision | Micro recall |
|---|---:|---:|---:|---:|---:|
| OAC reference | 10/10 | 10/10 | 5/10 | 1.000000 | 1.000000 |
| fixed team | 2/10 | not evaluated | not evaluated | 0.640000 | 0.941176 |
| initiator only | 1/10 | not evaluated | not evaluated | 1.000000 | 0.294118 |
| graph only | 0/10 | not evaluated | not evaluated | 0.735294 | 0.735294 |

These are agreements against project-authored exploratory candidate constraints, not Human Gold.
SC-003 through SC-007 remain explicit required-obligation-type gaps. The result does not establish
superiority, enterprise quality, or correctness of those labels.

## Red-team properties retained

- Candidate sources can participate in Strong-Kleene semantic FALSE, but candidate roots and
  candidate-authored cuts cannot prove candidate/uncovered target non-impact.
- An authoritative cut requires an admitted root, admitted TRUE prefix, admitted FALSE frontier,
  complete boundary, and no bypass route; downstream candidate sources do not pollute the proof.
- Missing/non-admitted owners on non-seed Affected dependency targets fail closed.
- Pure seed, empty-graph, and empty-rule zero-obligation plans fail without an all-FALSE no-op proof.
- False traversal, Unknown erasure, evaluation/witness forgery, missing duty, dangling legacy refs,
  duplicate semantic obligations, and forged hash-derived IDs are rejected.
- Split-role endpoint omission, role-cycle hiding, prerequisite under-binding, and reason over-binding
  are rejected while plural valid topologies remain accepted.

## Gates intentionally still open

1. **Normative identifier closure (P2)**: full Path and Obligation ID payloads need the same explicit
   standard grammar as Evaluation IDs before clean-room interoperability can be claimed.
2. **Derived identifier robustness (P2)**: suffix-based RoleInstance, WorkUnit, and Decision IDs need a
   bounded collision-resistant construction.
3. **Witness-reference grammar (P2)**: `resourceId#/pointer` needs an escaping and parsing rule because
   Core identifiers do not currently forbid `#`.
4. **Resource profile (P2)**: simple-path enumeration needs published node/edge/depth/path budgets or
   another deterministic exhaustion rule before hostile enterprise-scale inputs are supported.
5. **Independent implementation**: Spec 003 requires parser-, derivation-, and verifier-independent
   black-box agreement; the reference tools still share one Profile oracle.
6. **Human evidence**: zero qualified independent annotation/adjudication rounds exist.
7. **Outcome evidence**: no enterprise twin mapping or OutcomeCertificate run exists.
8. **Standardhood**: no external implementer, multi-stakeholder governance, compatibility history, or
   standards-body recognition exists.

The next implementation priority is Spec 003, beginning with the four P2 interoperability closures
above. Runtime breadth, connector count, and self-learning remain downstream evidence gates.
