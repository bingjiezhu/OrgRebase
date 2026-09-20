# Supplier-Change Profile: exploratory data slice

This directory is the first data-backed OAC profile. It deliberately separates three kinds of truth:

1. **Upstream facts**: immutable dataset identity and exact `PROC-01` labels copied from the pinned
   EDiTh metadata files.
2. **OAC candidate constraints**: locally authored, inspectable hypotheses about obligations,
   admissible roles, order, evidence, minimality, and Unknown preservation.
3. **Witnesses and mutations**: mechanics-only fixtures used to test whether a verifier accepts a
   solution set rather than one golden Agent graph.

The implemented normative proposal is [`profile.md`](profile.md); the v0.2 contextual wire contract is
published in [`../../standard/oac-contextual-applicability-v0.2.md`](../../standard/oac-contextual-applicability-v0.2.md).
`src/oac/supplier.py` is their non-normative executable reference, not hidden compiler policy.

The case set intentionally mixes immutable roots: SC-001 through SC-007 continue to use the frozen
v0alpha1 Snapshot, while SC-008 through SC-010 use immutable contextual successors. This proves that
the extension does not require rewriting legacy source bytes. It also keeps a real gap visible:
candidate roles and verdicts agree on 10/10 development cases, but candidate obligation types are
fully covered on only SC-001, SC-002, SC-008, SC-009, and SC-010. SC-003 through SC-007 require human
adjudication or new evidence-backed Profile successors, not label-shaped compiler branches.

Nothing in this directory is human Gold. All ten cases have `annotationStatus: exploratory`. EDiTh's
`ANSWER_KEY.json` calls its answer field `ground_truth`, but that field is `{}` for `PROC-01` at the
pinned revision. `MASTER_INDEX.csv` still contains 19 useful document classifications; those labels
remain upstream retrieval/relevance metadata and never become OAC authority by implication.

## Frozen source

- EDiTh revision: `844264a930674feacf6dee1844da77b0c4d66b2a`
- EnterpriseOps-Gym revision: `c8e538eae8a6205294f0a86675fefdc1fac408f6`
- EDiTh PDFs downloaded by this repository: **none**
- EnterpriseOps-Gym runtimes/tasks executed by this repository: **none**

See [`datasets/`](datasets/), [`source-labels/`](source-labels/), the explanatory
[`annotations/`](annotations/), and strict [`OrgChangeCase` resources](cases/). The
machine-readable readiness result lives at
[`../../benchmark/data-readiness.json`](../../benchmark/data-readiness.json).

## Interpretation rule

An `evidenceRefs` entry proves only that a reviewer or generator used that source anchor. It does not
prove the derived organizational conclusion. Promotion beyond `exploratory` requires the future
Annotation Evidence Profile: two qualified independent human rounds, distinct adjudication, and a
proof-carrying promotion certificate. The current Profile rejects promotion-only lifecycle values rather than
accepting an unverifiable status edit.
