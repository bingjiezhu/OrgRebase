<p align="right"><strong>English</strong> · <a href="README.zh-CN.md">简体中文</a></p>

# Organizational Agent Contract (OAC)

**OAC is an experimental, vendor-neutral contract for building and verifying enterprise work organizations.** It describes the facts and demand a task depends on, the obligations an organization must cover, the authority and ordering constraints it must preserve, and the evidence required before experience may become a governed successor.

```text
enterprise Source + Demand
  -> a set of contract-valid organizations
  -> independently certified Plan
  -> runtime-owned Execution reference
  -> independently certified Outcome
  -> candidate evolution material
  -> governed immutable Source successor
```

OAC is not an agent, workflow runtime, IAM system, knowledge base, or enterprise application. Codex, Claude, LangGraph, AG2, a human workflow, or another runtime may consume an OAC Profile. The standard defines the organizational contract and acceptance relation; it does not execute the work or acquire enterprise truth authority.

## The central idea

OAC defines a **valid set of organizations**, not one preferred agent graph. Two Plans may use materially different roles, WorkUnits, and edges while satisfying the same frozen obligations, qualifications, separation of duty, partial order, evidence requirements, and `UNKNOWN` semantics.

This makes the boundaries explicit:

- a compiler may propose a Plan but cannot certify it;
- a Runtime may report execution but cannot certify its own business success;
- an outcome authority may issue a bounded verdict but cannot publish a new Source;
- a candidate producer may propose reusable procedure material but cannot admit it;
- governance admits exact successor bytes; it does not grant a vague right to invent them later.

[Related work and contribution boundaries](docs/architecture/related-work.md) explains how these
contract choices relate to authorization consistency, provenance, and governed agent systems.

## Implemented minimum lifecycle profile

Spec 009 adds three strict, portable Kinds to the existing Source/Plan/lowering resources:

| Kind | What it records | What it does not grant |
|---|---|---|
| `OrganizationalDemand` | requester, accountable role, exact Source, objective, desired outcomes, evidence obligations, constraints, and effect ceiling | runtime or Source authority |
| `SourceAdmissionReceipt` | an authority decision over exact Source bytes, including predecessor/successor and candidate/governance refs | truth merely because a receipt exists |
| `OutcomeCertificate` | an independent verdict over exact Source, Demand, Plan, runtime-owned Execution and Observation refs | automatic promotion of experience |

The three Kinds are implemented in the model, Kind Registry, generated schemas, installed package, public CLI, and the evolution TCK/evidence gate. The profile also defines deterministic, domain-separated `S/D/P/X/O/E` roots and rejects root drift, cross-namespace substitution, self-certification, illegal positive promotion, and predecessor drift.

`ExecutionReceipt`, `OutcomeObservation`, procedure candidates, governance transactions, and the active Source pointer remain implementation-owned extensions referenced by exact resource identifiers. **This repository does not own a Runtime.**

The formal contract is [Spec 009](specs/009-proof-carrying-evolution-minimum-profile/spec.md). Its source/root-vector evidence remains bounded single-reference evidence; historical Spec status and external-independence gates remain explicit.

## Existing Supplier profile and plural Plans

The first Profile covers supplier status changes:

```text
OrganizationSnapshot + SemanticChangeSet
                ↓ reference compiler
         OrganizationPlan
                ↓ verifier module
          PlanCertificate
                ↓ controlled zero-effect lowerer
 ZeroEffectRuntimeBundle + lowering receipt
```

The compiler and verifier do not call each other, but they still share the published Supplier Profile derivation relation. This is module and topology separation, not an external clean-room semantic implementation.

The bounded public coordinate demonstrates:

- contextual subject, scope, relation, and tri-valued applicability with preserved `UNKNOWN`;
- two structurally different accepted SC-008 Plans under one obligation contract;
- registered negative cases for omissions, self-review, illegal order, forged applicability, digest drift, and Unknown erasure;
- internal Python/Go differential and mutation evidence on frozen coordinates.

OAC-specific EDiTh annotations remain exploratory rather than qualified expert Ground Truth. Same-repository Go implementations are useful falsification seeds, not organizational independence or certification.

## External OrgRebase reference journey

The peer OrgRebase repository consumes the public OAC CLI and wire resources in one controlled synthetic journey:

```text
Source/Demand admission
  -> BASE and SPLIT Plans
  -> runner-owned zero-effect execution
  -> controlled Observation
  -> independent ACCEPT/REJECT Outcome
  -> topology-independent Procedure candidate
  -> exact-byte governance
  -> immutable Profile/Snapshot successor
  -> pointer rollback
```

That journey is an **external reference implementation of the profile**, not a Runtime owned by OAC and not production proof for the standard. Its current evidence is synthetic, zero-effect, and scripted-governance only. Human review, real-enterprise use, authenticated enterprise Source, and production deployment remain `NOT_RUN`.

From the two-directory source snapshot:

```bash
cd orgrebase
uv sync --all-extras
make workspace-oac-evolution-check
```

The defensible combined claim belongs to the OrgRebase reference: one complete synthetic controlled OAC loop plus byte-exact regression of its preliminary Quote loop. It is not a claim that OAC itself executed an enterprise or generalized across enterprises.

## Quick start

Requires Python `>=3.12,<3.15` and [uv](https://docs.astral.sh/uv/).
The current validated baseline is **CPython 3.12.13**. The installation range also permits 3.13 and 3.14,
but equivalent installation and behavior validation has not been established for those versions.
Select CPython 3.12.13 explicitly when reproducing those records.

```bash
uv sync --all-extras
uv run oac demo
uv run oac tck
make evolution-evidence-check
make runtime-lowering-check
make check
```

Useful public commands:

```text
oac validate             strict registered-resource validation
oac validate-evolution   Spec 009 semantic validation
oac digest               detached RFC 8785 + SHA-256 digest
oac compile              Supplier Profile reference compiler
oac verify               compiler-separated Plan verification
oac lower                controlled zero-effect lowering
oac registry             machine-readable registries
oac tck                  manifest-driven development TCK
```

`make check` is fail-closed against checked-in path and installed-material commitments. In a relocated or extracted copy, `make archive-replay-check` recomputes the live Plan-verification result and compares only its documented host-portable semantic projection with the immutable seed-1 coordinate before running the same composite gate. It never rewrites the frozen parity summary, installed ledger, or evidence manifest.

## Evidence boundary

| Claim | Status |
|---|---|
| Strict wire kinds, detached digests, registries, schemas, and installed package | implemented and checked |
| Contextual Supplier Profile and bounded plural-valid Plan relation | implemented and checked on frozen public coordinates |
| Spec 009 minimum Demand/Admission/Outcome kinds and lifecycle-root negative inventory | implemented and checked |
| Zero-effect reference lowering | implemented; still requires external Runtime admission |
| External OrgRebase controlled synthetic reference loop | `PASS` in that repository; not OAC production evidence |
| Qualified human Ground Truth | `NOT_RUN` |
| Externally maintained semantic implementation | `NOT_RUN` |
| Real enterprise effect, runtime portability, or production authorization | `NOT_RUN` / not claimed |

OAC is an experimental proposed draft. It does not claim formal certification, verifier completeness, enterprise effectiveness, or autonomous organizational evolution.

## Repository map

| Path | One responsibility |
|---|---|
| `standard/` | proposed normative Core, conformance, applicability, lowering, and identifier texts |
| `schemas/` | generated wire schemas and Kind/reason registries |
| `profiles/` | Supplier Profile resources, source provenance, and exploratory annotations |
| `src/oac/` | non-normative reference models, compiler, verifier, lowerer, roots, and public CLI |
| `tck/` | development positive, negative, mutation, repeatability, and evolution vectors |
| `ctk/` | self-contained bundles, protocols, schemas, and code-independent runner |
| `implementations/` | disclosed same-repository cross-language falsification seeds |
| `experiments/` | revision-scoped portability and disagreement evidence |
| `specs/` | Spec Kit requirements and evidence-gated proposals |
| `docs/` | decisions, research, architecture, validation reports, and [roadmap](docs/ROADMAP.md) |

## Licensing

Contribution guidance is in [CONTRIBUTING](CONTRIBUTING.md), participation rules in
the [code of conduct](CODE_OF_CONDUCT.md), and private vulnerability reporting in
[SECURITY](SECURITY.md). See the [changelog](CHANGELOG.md) for source history;
unreleased source versions are not published-release or conformance claims.

Normative standard text is available under [CC BY 4.0](LICENSES/CC-BY-4.0.txt). Schemas, TCK/CTK, examples, and reference code are available under [Apache-2.0](LICENSES/Apache-2.0.txt). See [LICENSE.md](LICENSE.md), [NOTICE](NOTICE.md), [CONTRIBUTING](CONTRIBUTING.md), and the [patent non-assertion](PATENT-NON-ASSERTION.md).
