# Real user and application scenario

> Start with the [L0 product truth](SYSTEM-MAP.md). This file provides scenario and metric detail;
> its synthetic or modelled results are not real-enterprise acceptance.
> Recorded Golden counts below describe a retained reference run. Current usage and supported paths are documented in the [core approach](guide/approach.en.md) and [demo guide](guide/demo.en.md).

## Product overview

> 企业事实、政策、职责或产品方案发生变更后，已有成果需要重新核对依据、影响与责任。
> OrgRebase 将来源版本、任务候选、独立校验、精确批准与选择性更新连接成可复查的工作链。
> Enterprise Quote 是当前受控参考场景；本文中的合成或模型化指标不代表客户流程或真实企业 ROI。

## Domain tasks and deterministic control

| Component | Role in this design | Boundary |
|---|---|---|
| Deterministic control | Schema checks, dependency-based impact, approval validation and transactional writes | Uses admitted inputs; it does not invent enterprise facts or act as a human owner |
| Candidate Agent | Interprets bounded context and prepares source-bound candidates | Candidate output does not grant admission, approval or write authority |
| Domain task team | Separates scoped inputs and evidence handoffs across required domains | Adds coordination overhead; unrelated domains need not participate |

Domain tasks are useful when inputs, owners and evidence obligations differ. A single
candidate Agent with independent approval, or an ordinary workflow, can also separate
authority. The choice of multiple Agents must be justified by context boundaries and
handoffs, rather than assumed to improve quality or security.

## Product job to be done

OrgRebase Workspace serves an operations owner who must keep an existing business deliverable aligned with facts and
policies owned by several departments. The enterprise needs to know **which exact premises made that deliverable
trustworthy, which objects actually depend on a changed premise, which Domain Agents may prepare the repair, and which
Human Owner must authorize the canonical successor**.

The retained Golden reference scenario is deliberately narrow and executable:

> Evergreen Industries is admitted once through OAC. Quote v1 baseline Formation then runs the pinned Product / Legal /
> Finance / GTM AgentTeams lifecycle: Finance first abstains because `price_band` evidence is missing, Reviewer replans,
> a read-only HTTP Tool supplies the exact bytes, and only that baseline branch is retried before Quote v1 becomes the
> existing controlled-synthetic result. When an admitted launch-date or currency ChangeSet later arrives, OrgRebase
> freezes the affected universe, projects the minimum change team from durable dependency receipts, and reuses the
> already-admitted capabilities; that recorded run did not establish a new native taskflow for each change. Deterministic
> Preview + VMRC still performs zero canonical writes. Only the affected Product or Finance Human Owner is
> notified; an authorized Apply after approval produces Quote v2/v3 (`2026-10-15 / EUR`),
> field diff, receipts and rollback anchor. Model responses remain advisory and the recovery pattern becomes a governed
> `SINGLE_RUN_SEED` Skill candidate only after the business journey ends.

This records one controlled vertical slice across authority, privacy, change management,
approval and learning boundaries. It does not qualify later candidate source. The older
Northstar/Acme flow remains a retained deterministic regression profile.

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
7. Persisted dependency receipts project the required change team and reuse admitted capabilities. Current `golden` mode executes an isolated pinned AgentTeams lifecycle for that ChangeSet; the local reference mode remains deterministic. Historical run counts do not establish a fresh execution.
8. Deterministic control locks final Preview + VMRC and persistently pauses at the exact affected Human Owner; refresh or restart must preserve the run, digest and owner.
9. The Owner decision records Approval. Apply requires execution permission and a fresh server projection that still permits the action; the UI can continue automatically only when the same account satisfies both gates.
10. Selective Rebase produces Quote v2/v3, exact field diff, successor graph, archive and rollback anchor; preserved and `UNKNOWN` objects remain explicit.
11. The baseline AgentTeams lifecycle, Model, Tool, Skill, later change projections, Approval, Apply and Terminal evidence remain bound to the same business run; BPI and generic OAC reference runs stay separate evidence lanes.
```

The evaluator-facing full OAC walkthrough is `./run-semifinal-demo.sh` (default `interactive`): it creates a fresh recoverable workspace, derives the current OAC draft from the enterprise pack, and requires Contract Owner admission before Quote formation. Business candidates use local Ollama; the browser retains the explicit owner decisions. The retired `guided` mode refuses to copy historical mapping receipts into a new workspace. A fresh Vertex execution uses `./run-semifinal-demo.sh live` and receives a new run ID. `make serve` now requires production authentication configuration by default; `make serve-demo` explicitly starts a synthetic local demonstration. Neither replaces the full evaluator walkthrough. `make workspace-demo` remains the Northstar/Acme deterministic regression path. The exact step-to-code/evidence mapping is in [AGENT-TASK-CLOSURE](AGENT-TASK-CLOSURE.md) and [WORKSPACE-EVALUATION](WORKSPACE-EVALUATION.md).

## Reference workflow acceptance

The reference workflow requires an admitted enterprise Pack, one run identity and source-bound task/context receipts. Planned and actual domain tasks must agree; model and tool outputs remain candidates until the deterministic control plane verifies them.

The acceptance checks cover:

- source, Profile, context, output and dependency digests belong to the same run;
- missing evidence produces abstention or a hold, with a bound supplemental-evidence path;
- each business approval binds its exact owner, target version, digest and freshness;
- Apply has current execution permission and writes the allowed successors and receipts atomically;
- a released Skill is discovered and invoked only under its exact dependency and qualification contract;
- experience candidates cannot self-publish or retroactively change the run that produced them;
- unknown outcomes and external effects retain their separate recovery and reconciliation state.

Machine-readable thresholds live in `configs/workspace/metric-registry.json`; [evaluation](WORKSPACE-EVALUATION.md) describes their scope. Local reference success does not establish real connectors, external IAM, employee UAT, distributed production execution, SLA or ROI.

## Deployment and adoption path

The included release is a local, single-organization deterministic reference implementation. A safe adoption sequence is:

1. run with the bundled project-authored controlled synthetic fixture and OWB benchmark;
2. connect one read-only Product source and one controlled Quote renderer through adapters;
3. import owner-approved dependency coverage rather than inferring global completeness;
4. keep Agent output candidate-only while the deterministic control plane remains the only canonical writer;
5. conduct consented role-based walkthroughs before a production pilot;
6. verify trusted Source-byte resolution, recomputed digests, exact Source admission and runtime projection, then qualify read-only target effects, identity/tenant isolation, schema migration and external approval before a single-enterprise Shadow pilot;
7. add live AgentTeams transport only when K8s, Matrix, candidate bytes, Skill bytes, and provider request IDs can be correlated in one run;
8. move from local SQLite to an enterprise store only after the semantic and evidence contracts are stable.

The included reference supports controlled local evaluation. A single-enterprise read-only Shadow pilot requires all Source, identity, approval, mapping and zero-effect gates above. General enterprise production qualification remains open.

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
