# Supplier-change data readiness

## Decision

The public-data slice is sufficient for a **Shadow mechanics MVP**, but it is not ready for human
Gold, a benchmark-quality improvement claim, or enterprise effectiveness claims.

The strongest evidence is structural: ten strict exploratory cases, two different accepted SC-001
witness topologies, fifteen targeted rejected mutations, frozen RFC 8785 digests, and explicit Unknown
preservation. The weakest evidence is exactly where it should remain visible: there are zero qualified
independent human annotation rounds, no Supplier-to-EnterpriseOps executable mapping, and the first
counterfactual audit found a real transfer-semantics failure.

## What the public data actually provides

At EDiTh revision `844264a930674feacf6dee1844da77b0c4d66b2a`, `PROC-01` has an empty
`ANSWER_KEY.json.ground_truth`. Its `MASTER_INDEX.csv` nevertheless provides 19 task-relative records:
8 `AT_RISK`, 4 `NOT_AT_RISK`, 6 `REFERENCE`, and 1 `SUMMARY`. OAC preserves those labels verbatim but
does not reinterpret them as organizational roles, dependency paths, order, authority, or outcomes.

EnterpriseOps-Gym revision `c8e538eae8a6205294f0a86675fefdc1fac408f6` documents public SQL
final-state verifiers. This repository pins that future substrate but has not downloaded tasks,
started containers, or established a supplier-domain mapping.

## Result from the counterfactual pass

The first run found that unconditional graph traversal made SC-002
(`status_unverified -> operating`) activate 13 obligations. That failure led to an explicit contract
improvement: every dependency edge now declares a semantic type and applicable after-values. The
compiler and compiler-independent verifier both consume those frozen transfer predicates through the
same executable Profile oracle. SC-002 now activates
only its procurement status-verification duty, and SC-001 through SC-007 match their exploratory
candidate role sets.

The v0.2 contextual pass closes those three observed failures with admitted subject-relative rules,
scope selectors, and Unknown-transition duties. Candidate role sets and candidate verdicts now agree
on 10/10 development cases. A stricter obligation-type metric exposes a different gap: the frozen
Profile fully covers the candidate required types in only 5/10 cases. SC-003 through SC-007 therefore
remain open for adjudication or evidence-backed immutable successors. Gate G4 remains partial; the
implementation does not tune new branches to project-authored labels.

## Gate summary

| Gate | Status | Meaning |
|---|---|---|
| G1 source sufficiency | Not run | Requires qualified human review |
| G2 independent agreement | Not run | No pairwise F1 can be calculated |
| G3 structural mutation detection | Pass, mechanics only | 15/15 registered mutations rejected |
| G4 counterfactual sensitivity | **Partial pass** | roles 10/10; verdicts 10/10; required obligation types 5/10 |
| G5 plural validity | Pass, mechanics only | Two different SC-001 topologies accepted |
| G6 byte repeatability | Pass, mechanics only | Frozen local generator is deterministic |
| G7 matched baselines | Pass, mechanics only | Four deterministic runs share one ten-case input root; labels remain exploratory |

The candidate-label agreement results are deliberately reported without a superiority claim:

| System | Exact role-set matches | Micro precision | Micro recall |
|---|---:|---:|---:|
| OAC reference | 10/10 | 1.000000 | 1.000000 |
| fixed team | 2/10 | 0.640000 | 0.941176 |
| initiator only | 1/10 | 1.000000 | 0.294118 |
| graph only | 0/10 | 0.735294 | 0.735294 |

These numbers describe agreement with project-authored exploratory role candidates. They are not
human Ground Truth, enterprise effectiveness, or evidence that OAC is generally better.

The machine-readable source of this report is [`data-readiness.json`](data-readiness.json).
