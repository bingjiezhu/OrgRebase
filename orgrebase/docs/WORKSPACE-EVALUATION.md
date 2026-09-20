# Evaluation protocol

## OrgWorkBench-Northstar v1.1

OWB runs a parallel deterministic `ReferenceWorkspaceBenchmarkSUT`. It is a mechanism/contract-conformance harness, not the packaged OrgRebase product path and not evidence that OrgRebase is superior to external systems.

The canonical benchmark is synthetic because the ground truth must include authority ownership, recipient policy, TaskTemplate slots, correct coalition, admitted premises, output-field lineage, complete dependency manifests, impact classifications, exact VMRC effects, successor graph state and privacy violations. Public enterprise text collections do not provide this complete organizational ground truth, while real company data would be unsuitable for an open reproducible benchmark.

The bundled suite contains 192 deterministic cases across 12 synthetic organizations:

- formation and coalition;
- change impact;
- repeatability/restart;
- security/privacy;
- Skill governance.

Public inputs and evaluator gold are stored separately. The runner denies normal SUT access to evaluator
gold through an in-process capability. This is a test-harness boundary, not an operating-system sandbox
against arbitrary Python code in the same process.

The reference SUT's change, security and Skill cases use declared rule tables that correspond to the
generator's expected tables. Formation and repeatability cases also construct outputs from public
inputs. Agreement validates these reference contracts and evaluator wiring; it does not measure the
production `ImpactEngine`'s accuracy. In particular, `0/72` change mismatches and `0/32` unauthorized-read
successes are reference-suite results, not measured enterprise error rates. Engine behavior is tested
separately, for example by `tests/test_bounded_non_impact_witness.py`, which calls `ImpactEngine` and
checks bounded non-impact witnesses and invalid counterexamples.

## Hard gates

The weighted score and hard-gate outcomes are retained separately. A score at or above 90 is insufficient
when any required hard gate fails; the overall result remains FAIL. The reference profile requires:

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

## Programmatic comparison profiles

The retained suite uses the following historical profile IDs:

- safe single Agent with admitted context;
- unified raw-context stress baseline;
- natural-language multi-Agent transport;
- broadcast/invalidate-all recovery.

`BaselineSystem` first runs the same reference SUT, then applies a declared transformation to its
output, such as changing the coalition, removing an edge, or invalidating all targets. These are
constructed negative/comparison controls, not independently implemented single-Agent systems,
competing products or executed alternative LLM prompts. Their score differences cannot establish
that multi-Agent execution is superior to a single Agent or that one architecture has lower real cost.

## Mechanism-removal controls

The same wrapper represents removal of:

- the reference monitor;
- actor-specific context projection;
- manifest/edge bijection;
- certificates;
- successor graph promotion;
- `UNKNOWN`;
- Skill requalification;
- exact candidate digest binding.

The benchmark is a deterministic conformance and safety study, not a production ROI claim and not evidence that an LLM solved 192 real enterprise tasks.

## Product-path checks are a separate measurement

The ProductPath runner builds/unpacks an actual wheel, starts Uvicorn against isolated databases,
and exercises public HTTP endpoints. Its evaluator runs separately from the product and compares
observations with declared gold. This measures product behavior within the selected synthetic
profile; it is not interchangeable with OWB's reference-table results.

The retained generations are `ProductPath-v0.1`, `ProductPath-v0.2-source-bound`, and
`ProductPath-v0.3-task-intake-bound`. Their source manifests, wire conventions, primary trace lengths
and mutation sets belong to their own versions. Do not call the older v0.2 result the current release
gate, combine counts across generations, or rewrite a frozen manifest to match today's source.
Use [historical versus current-build verification](HISTORICAL-BUILD-VERIFICATION.md) and
[the evidence map](VERIFICATION-EVIDENCE-MAP.md) to select the exact artifact and command.

A retained verifier replay checks archived observations and bindings; it does not execute today's
service. Current-build qualification must bind the newly built wheel and its fresh observations.
Mutation rejection covers the named perturbation: rejecting a stale content digest proves integrity,
while a re-digested but invalid authority or business transition requires a separate semantic check.
Neither kind alone establishes coverage of every failure mode.

```bash
make workspace-product-path-blackbox-check
```

This target's selected build and reported mode determine what was checked. It does not qualify
arbitrary enterprise handlers, customer identities, external connectors, distributed AgentTeams,
throughput, availability or ROI.

## Skill qualification is constructive, not statistical

The retained Golden Quote package qualification has eight constructed cases, one per partition:
`REPLAY`, `HELD_OUT`, `NEGATIVE_TRANSFER`, `PERMISSION`, `INJECTION`, `MALFORMED`,
`RESOURCE_OR_DEADLINE`, `CANARY`. In this case set, `HELD_OUT` changes a predeclared partition
label; it is not an independently sampled holdout. `CANARY` means a bounded local invocation, not
production traffic routing. A threshold of 1.0 requires every registered assertion to pass, with
security partitions acting as a veto. It is not a confidence level or a generalization estimate.

The package evaluator records `baseline=NOT_SCORED`; those receipts do not measure improvement
over an independently executed baseline. The older foundry benchmark has its own constructed
baseline/candidate action tables and must not be confused with package qualification. Real input
validation, permission checks, exact dependency bindings, quarantine and predecessor recovery have
separate negative tests. See [Skill packages](SKILL-LIST.md) for the actual adapters and reuse scope.

## Business value requires business observations

QuoteValue's normalized action costs are declared assumptions. The historical base scenario yields
49.375%, while its declared stress scenarios span -10.0% to 59.875%; these are sensitivity outcomes,
not observed savings or a confidence interval. A positive cost for each action does not guarantee a
positive saving. Calibrate the action costs with per-target observed `PRESERVE`, `REVIEW`, `HOLD`,
`REQUALIFY` and `REBUILD` effort before making an enterprise benefit claim.

[Paired business observations](BUSINESS-OBSERVATIONS.md) describes how to retain matched baseline
and product observations, failures and missing values. Public retail data can validate arithmetic;
it does not supply a customer's former workflow, employee time, adoption or willingness to pay.

## Success levels

### Local core ready

```text
all Workspace and legacy tests pass
Quote v1 → v2 → restart → v3 succeeds
OWB hard gates pass
evidence pack verifies
fixed-action Skill governance gates pass on 16 constructed cases
```

### External workflow validation

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
