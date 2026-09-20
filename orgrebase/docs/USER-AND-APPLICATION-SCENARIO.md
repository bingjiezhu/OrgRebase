# Real user and application scenario

> Start with the [L0 product truth](SYSTEM-MAP.md). This file provides scenario and metric detail;
> its synthetic or modelled results are not real-enterprise acceptance.

## Two-sentence competition opening

> 企业成果已经交付，但产品发布日期、法务义务或财务政策会继续变化；传统流程只能广播通知、
> 人工搜索并全量重做，仍可能漏改、越权读取或沿用陈旧审批。
> OrgRebase 用 OAC 把现有成果的事实、权限与依赖编译为可验证组织模型：基线 Formation 先跑通固定版本
> AgentTeams 生命周期；变化到达后再从持久收据投影受影响领域的最小变化团队，复用已准入能力完成候选修正
> 与证据验收，只在规范写入前通知精确 Human Owner，
> 批准后选择性 Rebase。Enterprise Quote 是当前受控验证切片；0/72 impact mismatch、0/32 越权成功和
> normalized action cost 8.00→4.05 都是合成或模型化证据，真实企业 ROI 仍待 Shadow Pilot 验证。

## Why this is not just a script or one super-Agent

| Approach | What it is good at | Why it is insufficient for this task |
|---|---|---|
| Deterministic script | Schema checks, impact calculation, approval validation, atomic writes | It cannot own or interpret continuously changing Product, Legal, Finance and GTM knowledge |
| One super-Agent | Flexible synthesis in one context | It either sees excessive restricted data or impersonates several independent authorities; its completion cannot become business truth |
| Domain AgentTeam + deterministic control | Each domain sees its least-authority projection and submits independent evidence; control verifies and writes | This is the OrgRebase split: probabilistic work stays candidate-only, while approvals and canonical transitions remain deterministic |

Multiple Agents are therefore needed for **authority and evidence separation**, not to inflate Agent count. A
script still performs the parts that should be deterministic; the AgentTeam handles the bounded cross-domain work
whose inputs, owners and evidence differ.

## Product job to be done

OrgRebase Workspace serves an operations owner who must keep an existing business deliverable aligned with facts and
policies owned by several departments. The enterprise needs to know **which exact premises made that deliverable
trustworthy, which objects actually depend on a changed premise, which Domain Agents may prepare the repair, and which
Human Owner must authorize the canonical successor**.

The current competition scenario is deliberately narrow and executable:

> Evergreen Industries is admitted once through OAC. Quote v1 baseline Formation then runs the pinned Product / Legal /
> Finance / GTM AgentTeams lifecycle: Finance first abstains because `price_band` evidence is missing, Reviewer replans,
> a read-only HTTP Tool supplies the exact bytes, and only that baseline branch is retried before Quote v1 becomes the
> existing controlled-synthetic result. When an admitted launch-date or currency ChangeSet later arrives, OrgRebase
> freezes the affected universe, projects the minimum change team from durable dependency receipts, and reuses the
> already-admitted capabilities; current evidence does not claim a new native taskflow for each change. Deterministic
> Preview + VMRC still performs zero canonical writes. Only the affected Product or Finance Human Owner is
> notified; one explicit decision resumes automatic selective Rebase and produces Quote v2/v3 (`2026-10-15 / EUR`),
> field diff, receipts and rollback anchor. Model responses remain advisory and the recovery pattern becomes a governed
> `SINGLE_RUN_SEED` Skill candidate only after the business journey ends.

This is the current minimum vertical slice for enterprise work that crosses authority, privacy, change-management, human approval and organizational learning boundaries. The older Northstar/Acme flow remains a retained deterministic regression profile; it is not the current Golden authority line.

## Primary users and stakeholders

| Role | Immediate goal | Current risk | What OrgRebase provides |
|---|---|---|---|
| Quote Operations Owner | Keep delivered Quotes aligned after an admitted upstream change | Old dates or policies may remain; full rebuild creates avoidable work; broad access may expose restricted text | One change entry point, affected Domain coalition, final Preview, exact-owner gate and versioned successor Quote |
| Product owner | Publish authoritative product facts once | Downstream teams may use stale copies without knowing which work depends on them | Versioned Claims and impact previews bound to the current Workspace graph |
| Legal steward | Interpret restricted material without disclosing the source | A general-purpose Agent may place raw clauses in GTM context or logs | Legal-only source access and a minimum-disclosure derived Claim |
| Finance steward | Keep pricing and currency policy authoritative | Broad broadcasts create unnecessary rework or miss hidden consumers | Typed policy dependencies and selective rebase |
| Enterprise AI / Agent platform team | Operate multiple Domain Agents under one governance model | A super-Agent receives excessive context and Agents may write canonical state | Deterministic admission, context, state, evidence, and AgentTeams transport boundaries |
| Risk, audit, or knowledge-governance reviewer | Verify why a result was trusted and what changed | Chat transcripts and screenshots are insufficient evidence | Content-addressed WorkTrace, manifests, certificates, receipts, and replayable tests |

The likely operational owner is an Enterprise AI Platform, Knowledge Operations, or Enterprise Architecture team. The likely economic buyer is a hypothesis, not a validated claim; the repository therefore reports the real-user study as `NOT_RUN` until consented external walkthroughs are supplied.

## Product, substrate, and enterprise intake

OrgRebase is not only the Quote application shown below. Its project category is
a **OAC-based organizational-work compilation and continuous-evolution
substrate**. OAC defines the portable organization contract and acceptance
relations; OrgRebase supplies the reference control plane, Runtime Host, and an
executable Reference Runtime slice. Evergreen Industries / Blue Harbor Quote is
the current bounded proof and both names are fictional identities in a
project-authored controlled synthetic fixture, not customer or enterprise
production data. Northstar / Acme remains
`RETAINED_REFERENCE_REGRESSION`, not a second current run.

Enterprise intake is deliberately separated from Runtime execution:

```text
EnterpriseSeedProfile manifest
→ strict manifest admission and typed Gap policy
→ restricted Source-byte admission and component projection checks
→ exact Reference Runtime compatibility
→ only then create or reopen canonical Workspace state
```

Passing the manifest intake does not mean a Handler exists for that enterprise.
The Supplier fixture proves that a second organization can be expressed and its
bounded Source package can be checked by the same contract; it remains
`INTAKE_ONLY` and cannot enter the Northstar Quote Runtime. Arbitrary-enterprise
Runtime generation, independent Outcome assurance, and successor evolution are
later gates.

## Data and evidence truth boundary

The repository deliberately keeps input provenance separate from execution
provenance:

| Evidence lane | What is real | What it does not claim |
|---|---|---|
| Evergreen / Blue Harbor Golden journey | The local service, AgentTeams control-plane actions, Tool and Skill invocations, time gates, approvals, state transitions and receipts actually execute | Its Pack and business identities are project-authored controlled synthetic data, not a customer deployment |
| BPI Challenge 2019 add-on | A public, anonymized Purchase-to-Pay event log is replayed through the typed-scope mechanism | It is not Quote data, causal Ground Truth, enterprise ROI, or a real OrgRebase customer onboarding |
| Enterprise Pilot | None is claimed as completed in this repository | Real Quote data, connectors, IAM, employee UAT, ROI and production operations require an external enterprise Pilot |

A real model call does not make its input real enterprise data, and a real
local receipt does not make a scripted local approval an enterprise employee
approval. Product copy, evidence exports and submission material must preserve
these distinctions.

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

## OrgRebase change-driven experience

```text
1. OAC admits the exact sealed, project-authored controlled synthetic Evergreen Pack and its authority map once.
2. Formation compiles the required Product / Legal / Finance / GTM baseline tasks and Reviewer barrier into pinned AgentTeams.
3. Domain Agents return source-bound baseline candidates; Finance A1 `ABSTAINS`, Reviewer A1 `REPLANS`, a read-only HTTP Tool supplies the missing fact, and Finance A2 plus Reviewer A2 close the evidence gap.
4. The released quote-compose Skill is discovered, qualified and invoked candidate-only with canonical target writes = 0; Quote v1, WorkTrace and the dependency graph become the existing baseline.
5. An upstream launch-date or currency change is admitted as a frozen ChangeSet under the same business run.
6. Impact discovery uses actual reads and exact source versions; no-path outside the frozen universe becomes `UNKNOWN`, not “unaffected”.
7. Persisted dependency receipts project the minimal change team and reuse admitted capabilities; current evidence does not claim a fresh native AgentTeams taskflow for each ChangeSet.
8. Deterministic control locks final Preview + VMRC and persistently pauses at the exact affected Human Owner; refresh or restart must preserve the run, digest and owner.
9. A single visible Owner decision records Approval and then automatically resumes Apply; these remain distinct receipts and states.
10. Selective Rebase produces Quote v2/v3, exact field diff, successor graph, archive and rollback anchor; preserved and `UNKNOWN` objects remain explicit.
11. The baseline AgentTeams lifecycle, Model, Tool, Skill, later change projections, Approval, Apply and Terminal evidence remain bound to the same business run; BPI and generic OAC reference runs stay separate evidence lanes.
```

The evaluator-facing full OAC walkthrough is `./run-semifinal-demo.sh` (default `interactive`): it creates a fresh recoverable workspace, derives the current OAC draft from the enterprise pack, and requires Contract Owner admission before Quote formation. Business candidates use local Ollama; the browser retains the explicit owner decisions. The retired `guided` mode refuses to copy historical mapping receipts into a new workspace. A fresh Vertex execution uses `./run-semifinal-demo.sh live` and receives a new run ID. `make serve` now requires production authentication configuration by default; `make serve-demo` explicitly starts a synthetic local demonstration. Neither replaces the full evaluator walkthrough. `make workspace-demo` remains the Northstar/Acme deterministic regression path. The exact step-to-code/evidence mapping is in [AGENT-TASK-CLOSURE](AGENT-TASK-CLOSURE.md) and [VERIFICATION-EVIDENCE-MAP](VERIFICATION-EVIDENCE-MAP.md).

## What success means

The current Golden profile is successful only when all of the following are true:

- one sealed project-authored controlled synthetic Pack, one unique `run_id` and one stable business `correlation_id` bind the whole journey;
- 40 pinned AgentTeams control-plane actions reconcile with 7 task bindings, 5 Worker and 2 Reviewer processes;
- Finance A1 `ABSTAIN` is followed by Reviewer `REPLAN`, an exact read-only Tool receipt, Finance A2 and Reviewer `PASS`;
- two Vertex `gemini-3.8-flash` responses are schema-valid, retain distinct provider response IDs, remain advisory-only and produce zero canonical writes;
- released quote-compose is discoverable, loadable and invoked only after its exact 8/8 qualification;
- Agent, Reviewer, Model, Tool and Skill remain candidate-only; exact-reviewed Formation and deterministic control own canonical writes;
- both business approvals satisfy server-side time, owner, digest and freshness gates before Quote v3 reaches `2026-10-15 / EUR`;
- the experience candidate stays `SINGLE_RUN_SEED`, current-Quote consumption is false, and only a separate Skill Steward decision admits CANARY;
- independent stdlib verification reports 101 entries, causal `PASS`, experience `PASS`, and no product imports;
- real connectors, external IAM/UAT, measured ROI, distributed production Workers and production SLA/HA/DR remain explicit `NOT_RUN` rather than being inferred from local success.

Thresholds are machine-readable in `configs/workspace/metric-registry.json` and explained in [WORKSPACE-EVALUATION](WORKSPACE-EVALUATION.md).

## Deployment and adoption path

The included release is a local, single-organization deterministic reference implementation. A safe adoption sequence is:

1. run with the bundled project-authored controlled synthetic fixture and OWB benchmark;
2. connect one read-only Product source and one controlled Quote renderer through adapters;
3. import owner-approved dependency coverage rather than inferring global completeness;
4. keep Agent output candidate-only while the deterministic control plane remains the only canonical writer;
5. conduct consented role-based walkthroughs before a production pilot;
6. require trusted Source-byte resolution, recomputed digests, a local Source-admission receipt, Runtime-consumption/projection proof, and then close Spec 030/031 zero-effect Shadow gates plus Spec 021 identity/tenant, schema-migration and external-approval gates before a single-enterprise read-only Shadow Pilot;
7. add live AgentTeams transport only when K8s, Matrix, candidate bytes, Skill bytes, and provider request IDs can be correlated in one run;
8. move from local SQLite to an enterprise store only after the semantic and evidence contracts are stable.

Adoption verdict: competition technical MVP=`GO`; single-enterprise isolated
read-only Shadow Pilot=`CONDITIONAL GO` only after all Source, identity,
approval, mapping, and zero-effect gates above close; arbitrary enterprise
production=`NO-GO`.

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
