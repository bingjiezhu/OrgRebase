# Evaluation protocol

## OrgWorkBench-Northstar v1.1

The canonical benchmark is synthetic because the ground truth must include authority ownership, recipient policy, TaskTemplate slots, correct coalition, admitted premises, output-field lineage, complete dependency manifests, impact classifications, exact VMRC effects, successor graph state and privacy violations. Public enterprise text collections do not provide this complete organizational ground truth, while real company data would be unsuitable for an open reproducible benchmark.

The bundled suite contains 192 deterministic cases across 12 synthetic organizations:

- formation and coalition;
- change impact;
- repeatability/restart;
- security/privacy;
- Skill governance.

Public inputs and evaluator gold are stored separately. The system-under-evaluation cannot call the gold repository without an unforgeable in-process evaluator token.

## Hard gates

A score is calculated only after hard gates. The reference profile requires:

- stale Work recall = 100%;
- bounded-unaffected precision = 100%;
- restricted disclosure count = 0;
- cross-organization and evaluator-gold leaks = 0;
- unauthorized reads blocked = 100%;
- VMRC/certificate tamper rejected;
- object + successor graph transaction atomicity;
- idempotency conflicts rejected;
- exact Skill program digest executed;
- evidence-class integrity and license gate.

## Baselines

The same case IDs are evaluated against:

- safe single Agent with admitted context;
- unified raw-context stress baseline;
- natural-language multi-Agent transport;
- broadcast/invalidate-all recovery.

## Ablations

Mechanism value is tested by removing:

- the reference monitor;
- actor-specific context projection;
- manifest/edge bijection;
- certificates;
- successor graph promotion;
- `UNKNOWN`;
- Skill requalification;
- exact candidate digest binding.

The benchmark is a deterministic conformance and safety study, not a production ROI claim and not evidence that an LLM solved 192 real enterprise tasks.

## Success levels

### Local core ready

```text
all Workspace and legacy tests pass
Quote v1 → v2 → restart → v3 succeeds
OWB hard gates pass
evidence pack verifies
Skill exact-candidate safety gates pass
```

### Competition strong

Adds one independently verified Workspace-specific live AgentTeams run and externally conducted user walkthroughs. Synthetic walkthroughs never count as user research.

### Research strong

Adds multiple model profiles/seeds, hidden perturbation cases, paired confidence intervals, failure analysis and published generation/configuration metadata.

## External user validation (separate from OWB)

OWB measures deterministic conformance and safety; it cannot establish that real enterprise users understand the problem, value the trace, or would adopt the workflow. External walkthrough evidence is therefore scored separately and never increases the synthetic OWB score.

A first external milestone requires at least five consented, redacted participants spanning business-user, Domain-owner, and platform/governance roles. The published gate is:

```text
problem comprehension rate >= 0.80
median usefulness rating >= 4 / 5
median trace-value rating >= 4 / 5
PILOT or CONDITIONAL_PILOT responses >= 3
no critical gap repeated independently by two or more participants
```

The structured record stores role, ratings, pilot intent, redacted findings, and redacted critical gaps only. It does not store name, email, phone, company, customer, or raw transcript. Until valid records are supplied, the result must remain `NOT_RUN`; synthetic walkthroughs cannot count as user research.

The walkthrough script and target personas are defined in [USER-AND-APPLICATION-SCENARIO](USER-AND-APPLICATION-SCENARIO.md).
