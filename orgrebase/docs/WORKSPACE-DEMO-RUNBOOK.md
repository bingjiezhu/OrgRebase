# Workspace demo runbook

> **Reference regression only.** 本页固定初赛 Northstar / Acme Workspace 的可重现行为，不是
> 当前 Enterprise Pack 产品旅程。当前产品演示请使用 [Demo Runbook](DEMO-RUNBOOK.md) 和
> [Enterprise Pilot Runbook](ENTERPRISE-PILOT-RUNBOOK.md)。

当前产品的变化工作台仅在账号有执行权限、且批准后回读的同一提案仍允许 `APPLY` 时自动衔接应用；仅有批准权限时保留批准，交由有权执行者应用。下述固定脚本不模拟这一区分角色的交互。

## Run in a separate evidence directory

Run from the `orgrebase/` repository after `uv sync --locked --all-extras`.
Use a fresh output directory so the reference run does not overwrite retained repository evidence:

```bash
WORKSPACE_DEMO_OUT="$(mktemp -d "${TMPDIR:-/tmp}/orgrebase-reference.XXXXXX")"
uv run orgrebase workspace-demo --output-dir "$WORKSPACE_DEMO_OUT"
```

`make workspace-demo` invokes the same reference harness but writes to `evidence/workspace/latest`.
Use that target only in a disposable checkout intended to regenerate those files. This scripted reference
does not start the current clickable enterprise journey or provide external human approval evidence.

Equivalent Python entrypoint, using the same fresh output directory:

```bash
uv run python -m orgrebase workspace-demo --output-dir "$WORKSPACE_DEMO_OUT"
```

## What the command proves

1. **Before formation**: `work:quote_acme` does not exist.
2. **Task formation**: the enterprise Quote TaskTemplate selects Product, Legal, Finance and GTM; a separate public-summary oracle selects only Product + GTM.
3. **Minimal disclosure**: the Quote consumes a derived legal obligation Claim and never receives the restricted contract text or internal cost floor.
4. **Execution evidence**: Quote v1, WorkTrace, complete RuntimeDependencyManifest, WorkspaceGraphSnapshot v1, graph pointer and TaskReceipt commit atomically.
5. **Tool evidence**: the same persistent Form command invokes the read-only dependency tool as `gtm-steward`. The result, `ToolInvocationReceipt`, and `ToolCalledEvent` bind to `run:workspace:complete@v1`, survive retry/restart, appear in product State/downloaded evidence, and enter the Evidence Index with zero target writes.
6. **First change**: `product.launch_date` changes from `2026-09-01` to `2026-09-15`. Existing ImpactEngine/Certificates/VMRC classify Quote as `AFFECTED_HARD`, Finance as bounded unaffected, Partner Brief as `UNKNOWN`, and the launch-readiness Skill as requiring requalification.
7. **Human gate**: after each locked Preview the browser displays a four-second `HUMAN · OWNER REVIEW` hold. Expiry does not approve; the matching local Owner must still click, after which the UI returns to `AUTO · CONTROL PLANE`.
8. **Selective recovery**: Quote v2 changes the real `launch_date` field while preservation fields remain unchanged. Snapshot/pointer v2 commits in the same transaction.
9. **Restart**: the process closes and a new `WorkspaceService` reconstructs state from SQLite and verified artifacts.
10. **Second change**: `policy:finance.currency` changes from USD to EUR, producing Quote v3 and graph v3.
11. **Evaluation**: OrgWorkBench v1.1 runs 192 synthetic mechanism/contract-conformance cases plus baselines and ablations; these are not 192 full-stack enterprise deployments.
12. **Skill governance scaffold**: a fixed-action immutable candidate runs 16 constructed cases across eight partitions and reaches `CANARY` only when every gate passes; no trace-content induction occurs.
13. **Evidence**: the final content-addressed pack is independently verified.

`workspace-demo` programs the two expected local owner IDs to make this acceptance harness
deterministically replayable.  It persists and verifies the same staged commands as the API, but
`approval_input_mode=CONTROLLED_LOCAL_SCRIPTED_COMMAND`; it is not external human-click evidence.

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
