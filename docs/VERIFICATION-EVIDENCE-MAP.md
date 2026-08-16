# Verification and evidence map

This document maps every externally useful claim to source code, tests, machine evidence, and a reproduction command. A claim without one of these bindings must be presented as a design goal or `NOT_RUN`, not as an implemented result.

## Workspace claims

| Claim | Main implementation | Tests | Evidence artifact | Reproduction / verifier | Current evidence boundary |
|---|---|---|---|---|---|
| A real task forms Quote v1 instead of loading a pre-existing quote | `workspace/formation.py`, `task_agent.py`, `templates.py` | `test_formation.py`, `test_repeatability.py` | `formation/task-receipt.json`, `formation/quote-v1.json` | `make workspace-formation-check` | `LOCAL_DETERMINISTIC` |
| Quote task selects Product + Legal + Finance + GTM, while public summary selects Product + GTM | `workspace/planner.py` | `test_planner_and_governance.py`, `test_formation.py` | `formation/coalition-plan.json` | `make workspace-formation-check` | `LOCAL_DETERMINISTIC` |
| Agents cannot invent authority or self-admit Claims | `workspace/domain_agents.py`, `admission.py` | `test_planner_and_governance.py`, `test_privacy.py` | `formation/admission-decision-*.json` | `make workspace-formation-check` | `LOCAL_DETERMINISTIC` |
| Restricted Legal source is not disclosed to GTM/renderer | `workspace/context.py`, `domain_agents.py`, `evidence.py` | `test_privacy.py`, `test_failures.py` | `formation/task-context.json`, evidence-index canary scan | `make workspace-check` | synthetic restricted source; no real contract |
| Dependencies reflect values actually resolved during rendering | `workspace/execution.py` | `test_formation.py`, `test_failures.py` | `formation/work-trace.json`, `trace-coverage.json` | `make workspace-formation-check` | `LOCAL_DETERMINISTIC` |
| Quote v1 has a complete runtime dependency manifest and graph snapshot | `workspace/execution.py`, `graph.py`, `formation.py` | `test_formation.py`, `test_contracts_and_store.py` | runtime manifest, graph snapshot v1, TaskReceipt | `make workspace-formation-check` | `LOCAL_DETERMINISTIC` |
| Main Workspace run performs a bound read-only tool call | `tools.py`, `workspace/service.py`, `workspace/evidence.py` | `test_tool_trace.py`, evidence tamper tests | `tool/dependency-evidence-invocation.json`, called event | `make workspace-evidence-check` | same run, tool, receipt, request, result and graph binding |
| No complete manifest means no bounded-unaffected conclusion | existing `impact.py`, Workspace snapshot bridge | benchmark/legacy impact tests, `test_benchmark.py` | Preview certificates and OWB results | `make workspace-benchmark-check` | closed declared boundary only |
| Impact classifications are independently recomputable | `certificates.py`, `impact.py` | certificate/core tests, Workspace rebase tests | launch/currency Preview + VMRC | `make workspace-rebase-check` | same deterministic algorithm; no global causal claim |
| VMRC contains exactly the necessary effects | `certificates.py` | mutation tests in Workspace/core suites | launch/currency minimal-rebase-certificate | `make workspace-rebase-check` | exact locked target scope |
| Quote v2 changes launch date but preserves unrelated fields | `workspace/rebuild.py` | `test_rebase.py`, `test_failures.py` | `rebase/launch-quote.json`, Workspace receipt | `make workspace-rebase-check` | `LOCAL_DETERMINISTIC` |
| Successor Quote and graph promote atomically | `workspace/rebuild.py`, `store.py` | fault injection and repeatability tests | launch/currency graph pointer and Workspace receipts | `make workspace-rebase-check` | local SQLite transaction |
| Restarted process handles a second currency change | `workspace/service.py`, `store.py` | `test_repeatability.py`, `test_rebase_repeatability.py` | final Quote and graph pointer | `make workspace-repeatability-check` | local persistent store |
| Exact Skill candidate bytes/digest are evaluated | `workspace/skill_foundry.py` | `test_skill_foundry.py` | `skill/candidate.json`, `skill/evaluation-receipt.json` | `make workspace-skill-check` | declarative sandboxed candidate |
| Benchmark passes hard gates, baselines, and ablations | `workspace/benchmark.py`, OWB assets | `test_benchmark.py` | `evaluation/owb-results.json` | `make workspace-benchmark-check` | 192 synthetic cases; not production accuracy |
| Evidence pack is content-addressed and privacy-canary scanned | `workspace/evidence.py` | `test_evidence.py` | `evidence-index.json` and 41 bound entries | `make workspace-evidence-check` | local deterministic artifacts |

## AgentTeams and user validation claims

| Claim | Code / assets | Verifier | Current status | Required evidence to upgrade status |
|---|---|---|---|---|
| Fixed Workspace Worker pool and identities are well formed | `agentteams/workspace/`, `workspace/transport.py` | `make workspace-agentteams-static-check` | `PASS_STATIC` | none for static claim |
| Transport rejects wrong worker/domain/task/run/nonce/artifact/Skill/provider bindings | `workspace/transport.py`, `live_evidence.py` | `make workspace-agentteams-conformance-check` | tested locally | negative conformance is not a live run |
| Product/Legal/Finance/GTM ran together in Workspace AgentTeams | live verifier and collection scripts | `REQUIRE_LIVE=1 make workspace-agentteams-check` | `NOT_RUN` in bundled Workspace evidence | K8s Team/Worker exports, Matrix joined membership/events, exact candidate bytes/digests, Skill bytes/digest, model/provider request IDs, one run/nonce/time window |
| Real users understand and want the workflow | `workspace/user_validation.py`, CLI/API and protocol docs | `make workspace-user-validation-check` | `NOT_RUN` | at least five consented, redacted external participant records meeting published thresholds |

The repository also contains a separate five-Agent `LIVE_AGENTTEAMS` evidence pack for the Core change-advisory slice. That evidence is preserved under `evidence/agentteams/` and `evidence/release-facts.json`; it does not prove that the four-Domain Workspace formation coalition ran live.

## Model and tool verification

| Surface | Contract / implementation | Tests | Evidence statement |
|---|---|---|---|
| Structured model request/receipt | `workspace/models.py`, `model_provider.py` | `test_model_provider.py` | live status requires provider request ID; missing credentials is `NOT_RUN` |
| Domain transport | `workspace/ports.py`, `transport.py` | `test_transport.py`, `test_model_and_transport.py` | target writes fixed to zero; candidates revalidated by control plane |
| Dependency evidence tool | `tools.py`, `workspace/service.py`, `docs/TOOL-CONTRACT.md` | tool/core and Workspace tool-trace tests | read-only, exact graph revision, same-run receipt + called event + audit event |
| Git artifact tool | `git_tool.py` | `test_git_tool.py` | real local commit/revert evidence under `LOCAL_REAL_TOOL` |
| Pydantic/JSON Schema contracts | `domain.py`, `workspace/models.py`, schema exporter | contract/store tests | `scripts/export_contract_schemas.py --check` detects drift |
| Privacy-safe observability | `observability.py`, `workspace/evidence.py` | observability/evidence/privacy tests | no prompts, restricted source, or privacy canary in public evidence |

## One-command verification

Fast local review:

```bash
make workspace-check
make workspace-review-readiness-check
```

Full release review, including Core ProofPack, coverage, compile, and offline distributions:

```bash
make workspace-release-check
```

Air-gapped environments with the Python dependencies already installed may use:

```bash
PYTHONPATH=src python -m pytest -q
PYTHONPATH=src python scripts/workspace_evidence.py --verify evidence/workspace/latest
PYTHONPATH=src python scripts/verify_review_readiness.py
```

## Evidence-class interpretation

| Evidence class | Meaning |
|---|---|
| `LOCAL_DETERMINISTIC` | Executed locally with deterministic inputs and machine checks |
| `LOCAL_REAL_TOOL` | A real bounded local tool side effect occurred, such as Git commit/revert |
| `SYNTHETIC_FIXTURE` | Result is based on generated fictional business data |
| `PASS_STATIC` | Configuration/schema/source-lock checks passed; no live runtime claim |
| `LIVE_MODEL` | External model request returned a provider request ID and valid structured result |
| `LIVE_AGENTTEAMS` | One fully correlated external AgentTeams run satisfied the fail-closed verifier |
| `USER_STUDY` | Consented, redacted external participant records were supplied |
| `NOT_RUN` | Required external prerequisites were absent; the capability was not executed |

## Known limits

The evidence proves behavior within the declared synthetic task family and committed graph universe. It does not prove that a real enterprise has no missing dependencies, that arbitrary free-form text has exact causal attribution, that a deployment achieves ROI, or that the Workspace-specific AgentTeams/user-study milestones have already occurred.
