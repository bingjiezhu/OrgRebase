# Workspace demo runbook

## One-command local demo

```bash
make workspace-demo
```

Equivalent CLI:

```bash
PYTHONPATH=src python3 -m orgrebase workspace-demo \
  --output-dir evidence/workspace/latest
```

## What the command proves

1. **Before formation**: `work:quote_acme` does not exist.
2. **Task formation**: the enterprise Quote TaskTemplate selects Product, Legal, Finance and GTM; a separate public-summary oracle selects only Product + GTM.
3. **Minimal disclosure**: the Quote consumes a derived legal obligation Claim and never receives the restricted contract text or internal cost floor.
4. **Execution evidence**: Quote v1, WorkTrace, complete RuntimeDependencyManifest, WorkspaceGraphSnapshot v1, graph pointer and TaskReceipt commit atomically.
5. **Tool evidence**: `gtm-steward` calls the read-only dependency tool for Quote v1. The result, `ToolInvocationReceipt`, and `ToolCalledEvent` bind to `run:workspace:complete@v1` and enter the Evidence Index.
6. **First change**: `product.launch_date` changes from `2026-09-01` to `2026-09-15`. Existing ImpactEngine/Certificates/VMRC classify Quote as `AFFECTED_HARD`, Finance as bounded unaffected, Partner Brief as `UNKNOWN`, and the launch-readiness Skill as requiring requalification.
7. **Selective recovery**: Quote v2 changes the real `launch_date` field while preservation fields remain unchanged. Snapshot/pointer v2 commits in the same transaction.
8. **Restart**: the process closes and a new `WorkspaceService` reconstructs state from SQLite and verified artifacts.
9. **Second change**: `policy:finance.currency` changes from USD to EUR, producing Quote v3 and graph v3.
10. **Evaluation**: OrgWorkBench v1.1 runs 192 synthetic cases plus baselines and ablations.
11. **Skill Foundry**: the exact immutable candidate runs all eight partitions and reaches `CANARY` only when every safety gate passes.
12. **Evidence**: the final content-addressed pack is independently verified.

## Expected final summary

```text
Final Quote: work:quote_acme@v3
launch_date: 2026-09-15
currency: EUR
Workspace graph pointer: v3
Workspace run: run:workspace:complete@v1
OWB deterministic conformance: PASS
Skill candidate: CANARY
Workspace AgentTeams live transport: NOT_RUN unless separate live evidence is supplied
```

## Negative paths

```bash
make workspace-rebase-check
make workspace-repeatability-check
make workspace-agentteams-conformance-check
```

These cover Preview drift, VMRC mutation, unsupported premises, raw-source leakage, transaction failure after successor boundaries, restart, spoofed Matrix senders, nonce replay, candidate mismatch, missing Skill/provider evidence, `latest` image tags and cross-run splicing.
