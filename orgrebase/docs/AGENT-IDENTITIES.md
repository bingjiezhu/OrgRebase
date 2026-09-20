# Agent Identity inventory

This is the human-readable companion to the on-disk AgentTeams assets. It covers two collaboration planes that share the same deterministic control plane and must not be collapsed into one Team.

Machine sources:

- Workspace Worker cards: `agentteams/workspace/identities/*.json`
- Workspace Team / Workers: `agentteams/workspace/team.yaml`
- Workspace capability cards: `src/orgrebase/workspace/templates.py`
- Core `AgentIdentity` contracts: `agentteams/identities/*.json`
- Core Team / Workers: `agentteams/team.yaml`
- Core frozen intents: `orchestration/task-intents.json`

## Workspace formation coalition

This is the current product loop: an employee Quote task binds each required Domain to one eligible provider from the Product / Legal / Finance / GTM capability pool. A deterministic formation decision and task context are compiled into a sealed `AgentTeamsExecutionPlan`; the pinned AgentTeams task set may contain only the selected Domain tasks plus one Reviewer barrier. Controlled-local evidence proves both a four-domain Quote run and a separate Product+Legal two-domain run. Distributed Workers, Kubernetes/Matrix transport, and production scheduling remain `NOT_RUN`.

Multiple Agents are useful here because each domain receives scoped context, produces source-bound candidates, and can supply missing evidence without reopening every domain. This adds task and handoff overhead; a single-domain task need not recruit unrelated workers. Separate approval authority is not unique to multi-Agent systems: one candidate Agent with human approval, or an ordinary workflow, can also separate these responsibilities. The current Quote demonstration's missing-evidence recovery is a controlled scenario, not proof of general autonomous routing.

| Agent | Identity / authority | Inputs | Candidate outputs | Forbidden | Collaboration |
|---|---|---|---|---|---|
| Employee Task Agent (`TemplateBoundTaskInterpreter`) | Task interpretation only; not a Worker in `agentteams/workspace/team.yaml` | `TaskRequest`, template catalog | `TemplateCandidate`, `TaskRequirementCandidate` | Invent undeclared slots; approve its own interpretation; write canonical state | Hands a template-bound requirement set to the planner |
| `product-steward` | Product domain; `candidate_only` | Actor projection, Product sources, declared slots (`product_plan`, `launch_date`, `data_residency`) | Product `ClaimCandidate` bundle | Speak for Legal / Finance / GTM; admit Claims; disclose other domains | Returns source-bound Product candidates |
| `legal-steward` | Legal domain; restricted source; `raw_restricted_disclosure=false` | Legal projection, restricted synthetic source, slot `notice_required` | Minimum-disclosure legal `ClaimCandidate` | Disclose raw contract text to GTM or the renderer; admit Claims | Only a purpose-bound derived obligation may leave Legal |
| `finance-steward` | Finance domain | Finance projection, price/currency policy refs | Price band and currency Policy candidates | Read Legal restricted source; approve Apply | Finance-only candidates for Quote slots |
| `gtm-steward` | GTM domain | Customer/task projection, partner terms | GTM requirement / Claim candidates | Read restricted Legal source; mark Work stale/current | Customer-safe candidates; no state transition |

Workspace identity JSON on disk is the transport card (`worker_id`, `domain_id`, `candidate_only`, allowed output schemas). Slot, tool, purpose, and cost ceilings live on the capability cards compiled into the source lock.

## Core change-advisory team

This plane explains an already locked ChangeSet / Preview. It is the original five-Agent AgentTeams Team. A frozen `LIVE_AGENTTEAMS` receipt exists for this slice only and does not upgrade the Workspace formation coalition.

| Agent | Identity / authority | Inputs | Candidate outputs | Forbidden | Collaboration |
|---|---|---|---|---|---|
| `change-coordinator` | Team Leader; coordination only | Compiled `DelegationTask`, `ChangeSetRevision`, `RevisionLock`, Preview digest | `TaskGraph`, `CandidateSummary` | Admit facts; rewrite the DAG; approve; Apply; publish Skills | Splits and aggregates inside the compiled plan |
| `product-steward` | Product interpreter | Product source refs, ChangeSet candidate, Preview | `ClaimDeltaCandidate`, `SemanticExplanation` | Self-admit; speak for other domains; Apply | Returns source-cited Product semantics |
| `legal-steward` | Restricted legal interpreter | Legal minimum context, ChangeSet scope | `MinimalClaimCandidate`, `CoverageAssertionCandidate` | Disclose full source to GTM; decide GTM Work state; approve | Publishes a purpose-bound derived Claim |
| `gtm-steward` | Sales / Support dependency interpreter | GTM Work refs, admitted Product delta | `ImpactCandidate`, `CoverageGapCandidate` | Read restricted Legal source; mark Work state; overrule ImpactEngine | Must abstain or return `UNKNOWN` without evidence |
| `skill-curator` | Skill-governance candidate author | Redacted trajectories, Skill contract, impact candidate | `SkillPatchCandidate`, `ConfounderList` | Read held-out answers; edit evaluator; expand permissions; publish | Produces a candidate; control plane owns CANARY / quarantine |

## Control-plane roles that are not AgentTeams Workers

| Role | May do | Must not do |
|---|---|---|
| Coalition Planner | Bind required slots to exactly one current, healthy provider per Domain with the required purpose and schemas; reject missing or ambiguous providers | Treat declared cost as provider ranking, solve an undeclared cross-domain set-cover problem, or treat AgentTeams semantic matching as canonical authority |
| Admission / Context compiler | Decide authority, purpose, recipient, organization, task scope, freshness | Delegate authorization to an LLM |
| Execution Reference Monitor | Expose admitted values and record exact reads | Give the renderer a raw store handle |
| Impact / VMRC / Apply | Classify impact, bind approval, commit successor state | Treat Agent candidates as effect plans |
| Skill Evaluator | Run exact candidate bytes and issue CANARY / QUARANTINED | Substitute a repository implementation for the candidate |

## Shared identity rules

- Every Agent output is `candidate_only`. The deterministic control plane is the only canonical writer.
- Missing a bound plan, task, ChangeSet, Preview, or RevisionLock digest is `ABSTAIN`; ingestion also rejects the candidate.
- The orchestration compiler rejects cycles, undeclared capabilities, undeclared output types, and Agent-added tasks.
- High-risk state changes require human approval bound to an exact target set, authorization root, expiry, and Preview digest.
- Domain conflicts are not voted. Authority, admitted sources, and recomputable certificates decide. Incomplete evidence stays `UNKNOWN`.

## Appendix A field tables (contest template)

The following tables make the current Workspace product loop self-contained. The compact JSON files in `agentteams/workspace/identities/` remain transport cards; capability ceilings are source-locked separately.

### Employee Task Agent

| Appendix A field | Value |
|---|---|
| Name | `employee-task-agent` (`TemplateBoundTaskInterpreter`) |
| Role | Convert a user task into template-bound requirement candidates; not an AgentTeams Worker |
| Capabilities | Match declared deliverable intent; propose a template; extract declared requirement slots; abstain on ambiguity |
| Inputs | `TaskRequest`, active `TaskTemplateCatalogSummary` |
| Outputs | `TemplateCandidate`, `TaskRequirementCandidate`, interpretation receipt |
| Dependencies | Template catalog, schema validator, deterministic template matcher |
| Decision Boundary | Cannot invent slots, select authority, admit Claims, call write tools, approve, or change canonical state |
| Trace | task ID, template/catalog digests, candidate digests, reason codes, interpretation receipt digest |

### Product Steward

| Appendix A field | Value |
|---|---|
| Name | `product-steward` |
| Role | Product-domain authority interpreter in the discoverable capability pool |
| Capabilities | Interpret product plan, launch date, and residency sources; return source-bound candidates |
| Inputs | Exact `DomainDelegationTask`, Product actor projection, requested slots, Product source refs |
| Outputs | Product `ClaimCandidate` bundle under the allowed output schemas |
| Dependencies | Product Domain adapter, `structured-domain-handoff@1.1.2`, capability card |
| Decision Boundary | Cannot speak for Legal/Finance/GTM, admit its own candidate, widen context, approve Apply, or write state |
| Trace | run/nonce, delegation digest, worker/domain identity, projection/source/output digests, transport receipt |

### Legal Steward

| Appendix A field | Value |
|---|---|
| Name | `legal-steward` |
| Role | Restricted Legal-source interpreter in the discoverable capability pool |
| Capabilities | Read the Legal projection; derive the minimum purpose-bound notice obligation; abstain outside Legal scope |
| Inputs | Exact `DomainDelegationTask`, Legal actor projection, restricted synthetic source, `notice_required` slot |
| Outputs | Minimum-disclosure Legal `ClaimCandidate` bundle |
| Dependencies | Legal-only Domain adapter, purpose policy, `structured-domain-handoff@1.1.2` |
| Decision Boundary | Cannot expose raw source text, read other domains, admit its candidate, approve Apply, or write state |
| Trace | run/nonce, delegation/projection/source/derivation/output digests, redaction decision, transport receipt |

### Finance Steward

| Appendix A field | Value |
|---|---|
| Name | `finance-steward` |
| Role | Finance-policy interpreter in the discoverable capability pool |
| Capabilities | Interpret price-band and currency policies; return task-scoped Policy/Claim candidates |
| Inputs | Exact `DomainDelegationTask`, Finance actor projection, price/currency policy refs |
| Outputs | Finance candidate bundle under the allowed output schemas |
| Dependencies | Finance Domain adapter, policy sources, `structured-domain-handoff@1.1.2` |
| Decision Boundary | Cannot read Legal sources, expose cost-floor secrets, admit policy, approve Apply, or write state |
| Trace | run/nonce, delegation/projection/policy/output digests, worker/domain binding, transport receipt |

### GTM Steward

| Appendix A field | Value |
|---|---|
| Name | `gtm-steward` |
| Role | Customer-task and deliverable interpreter in the discoverable capability pool |
| Capabilities | Interpret customer/task intent and partner terms; return structured GTM candidates and uncertainty |
| Inputs | Exact `DomainDelegationTask`, GTM actor projection, customer/task fields, partner-term source |
| Outputs | GTM requirement/`ClaimCandidate` bundle |
| Dependencies | GTM Domain adapter, `structured-domain-handoff@1.1.2`, capability card |
| Decision Boundary | Cannot read restricted Legal text, mark Work state, overrule impact, approve Apply, or write state |
| Trace | run/nonce, delegation/projection/source/output digests, reason codes, transport receipt |

The Core `agentteams/identities/*.json` store the same eight fields directly for the five-Agent change-advisory Team.

### AgentTeams capability mapping

| AgentTeams capability | OrgRebase mapping |
|---|---|
| Role orchestration | Fixed Worker pool in `agentteams/workspace/team.yaml` and Core `agentteams/team.yaml` |
| Task decomposition | `CoalitionPlanner` / `OrchestrationCompiler` emit exact delegation tasks; Agents cannot add tasks |
| Context passing | Actor-scoped projections inside the delegation task; cross-domain traffic is admitted Claims only |
| Collaborative execution | The sealed plan materializes the exact selected task set through pinned AgentTeams; controlled-local topology parity is verified, while distributed Worker transport remains `NOT_RUN` |
| State tracking | AgentTeams task status is not canonical state; `StateStore` is the only normative writer |
