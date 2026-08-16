# Real user and application scenario

## Product job to be done

OrgRebase Workspace serves an employee who must produce a business deliverable from facts and policies owned by several departments, while the enterprise needs to know **which exact premises made that deliverable trustworthy** and what must happen when one premise changes.

The canonical scenario is deliberately narrow and executable:

> A sales employee prepares an enterprise quote for Acme. Product owns packaging, launch date, and residency capability; Legal owns a restricted contract and may disclose only a derived notice obligation; Finance owns price band and currency policy; GTM owns the customer/task context. The quote is created, its actual dependencies are recorded, the launch date changes, the quote is selectively rebuilt, the process restarts, the currency changes, and a second rebuild produces Quote v3.

The canonical scenario is a minimum vertical slice for enterprise work that crosses authority, privacy, and change-management boundaries.

## Primary users and stakeholders

| Role | Immediate goal | Current risk | What OrgRebase provides |
|---|---|---|---|
| Sales / account employee | Produce a correct enterprise quote without manually chasing four departments | Old dates or policies may remain in a quote; restricted text may be copied into sales material | One task entry point, minimum Domain coalition, purpose-bound context, and a versioned Quote |
| Product owner | Publish authoritative product facts once | Downstream teams may use stale copies without knowing which work depends on them | Versioned Claims and impact previews bound to the current Workspace graph |
| Legal steward | Interpret restricted material without disclosing the source | A general-purpose Agent may place raw clauses in GTM context or logs | Legal-only source access and a minimum-disclosure derived Claim |
| Finance steward | Keep pricing and currency policy authoritative | Broad broadcasts create unnecessary rework or miss hidden consumers | Typed policy dependencies and selective rebase |
| Enterprise AI / Agent platform team | Operate multiple Domain Agents under one governance model | A super-Agent receives excessive context and Agents may write canonical state | Deterministic admission, context, state, evidence, and AgentTeams transport boundaries |
| Risk, audit, or knowledge-governance reviewer | Verify why a result was trusted and what changed | Chat transcripts and screenshots are insufficient evidence | Content-addressed WorkTrace, manifests, certificates, receipts, and replayable tests |

The likely operational owner is an Enterprise AI Platform, Knowledge Operations, or Enterprise Architecture team. The likely economic buyer is a hypothesis, not a validated claim; the repository therefore reports the real-user study as `NOT_RUN` until consented external walkthroughs are supplied.

## Existing workflow without OrgRebase

A typical manual process looks like this:

```text
Sales request
→ search product documents
→ message Product / Legal / Finance
→ copy answers into a quote
→ save a final document
→ later receive a launch-date or policy announcement
→ broadcast the change
→ manually search for affected work
→ ask owners to update or confirm
```

This workflow has four structural gaps:

1. the final quote does not carry a machine-verifiable record of the exact facts and policies it used;
2. a document appearing in an Agent prompt does not prove that the output actually depended on it;
3. no-path in an incomplete graph is easily mistaken for “unaffected”;
4. a rebuild may update the visible field but fail to publish the successor dependency graph needed for the next change.

## OrgRebase task experience

```text
1. Employee submits TaskRequest
2. Task Agent matches TaskTemplate and proposes requirement slots
3. Deterministic planner selects the minimum Product / Legal / Finance / GTM coalition
4. Domain providers return source-bound candidates
5. Control plane admits or rejects each candidate
6. Actor-specific context projections enforce authority, purpose, recipient, organization, and task scope
7. Quote renderer resolves values only through ExecutionReferenceMonitor
8. Quote v1, WorkTrace, dependency manifest, graph snapshot, pointer, and receipt commit atomically
9. A semantic ChangeSet produces Preview, ImpactCertificates, and an exact VMRC
10. Approved selective rebase produces Quote v2 and graph v2 atomically
11. Process restart reconstructs state from persisted pointers and artifacts
12. A second policy change produces Quote v3 and graph v3
13. Successful trajectories may become an immutable Skill candidate, but only an independent evaluator can move it to CANARY
```

The complete executable walkthrough is `make workspace-demo`; the exact step-to-code/evidence mapping is in [AGENT-TASK-CLOSURE](AGENT-TASK-CLOSURE.md) and [VERIFICATION-EVIDENCE-MAP](VERIFICATION-EVIDENCE-MAP.md).

## What success means

The local reference profile is successful only when all of the following are true:

- the Quote task selects exactly Product, Legal, Finance, and GTM;
- the public-summary control task selects only Product and GTM, proving the coalition is not fixed;
- no restricted legal text or privacy canary appears in Quote, trace, exception, model log, or public evidence;
- all required premises are resolved through the reference monitor and every output field has lineage;
- Quote v1 and its complete dependency evidence commit atomically;
- launch-date change finds Quote as `AFFECTED_HARD`, preserves a bounded-unaffected Finance object, keeps incomplete Partner work as `UNKNOWN`, and requalifies the dependent Skill;
- Quote v2 changes only allowed business fields and promotes the successor graph in the same transaction;
- after restart, the currency change produces Quote v3 and graph v3;
- evidence verification, hard gates, baselines, ablations, legacy regression, and exact-candidate Skill evaluation all pass.

Thresholds are machine-readable in `configs/workspace/metric-registry.json` and explained in [WORKSPACE-EVALUATION](WORKSPACE-EVALUATION.md).

## Deployment and adoption path

The included release is a local, single-organization deterministic reference implementation. A safe adoption sequence is:

1. run with the bundled synthetic fixture and OWB benchmark;
2. connect one read-only Product source and one controlled Quote renderer through adapters;
3. import owner-approved dependency coverage rather than inferring global completeness;
4. keep Agent output candidate-only while the deterministic control plane remains the only canonical writer;
5. conduct consented role-based walkthroughs before a production pilot;
6. add live AgentTeams transport only when K8s, Matrix, candidate bytes, Skill bytes, and provider request IDs can be correlated in one run;
7. move from local SQLite to an enterprise store only after the semantic and evidence contracts are stable.

No production connector, production ROI, or externally validated buyer demand is claimed by the current repository.

## Real-user validation protocol

The repository includes `UserWalkthroughRecord`, redaction checks, aggregation logic, API/CLI entry points, and objective thresholds. A valid first study should include at least five consented participants spanning business-user, Domain-owner, and platform/governance roles.

Each 30–45 minute session should test whether the participant can:

1. explain the problem after seeing the pre-OrgRebase workflow;
2. identify why each selected Domain Agent is necessary;
3. read the Quote lineage and understand why restricted text is absent;
4. interpret `AFFECTED`, bounded unaffected, and `UNKNOWN` correctly;
5. decide whether the evidence is useful enough for a pilot;
6. name critical adoption gaps without recording personal or company-identifying information.

The release gate is defined in `configs/workspace/metric-registry.json`. Until real, consented, redacted records exist, `evidence/workspace/latest/user-validation.json` must remain `NOT_RUN`.
