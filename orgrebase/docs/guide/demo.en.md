# Demo and verification

## Native collaboration journey

Configure the explicit model path in [Vertex](models-vertex.en.md) or [DeepSeek](models-deepseek.en.md). In WebUI, check the execution mode, review the organizational contract, confirm the task, observe native collaboration, inspect candidates and impact, obtain the named owner's approval, and apply the change.

Observe collaboration while the task runs. On success, inspect the Quote under “Request and current deliverable.” Later changes have their own tasks and receipts; initial Formation is not evidence of a later execution. Diagnose failed model calls rather than reusing an older success.

## One public basket with native cloud-model collaboration

This path feeds UCI invoice 557670 into the existing native AgentTeams / Vertex workflow, using the same pricing and approval engine. Configure an authorized project and credentials using [Vertex setup](models-vertex.en.md), then run these commands from an installed product checkout. Use fresh names for both output directories; do not reuse a workspace with an existing Quote or approval.

```bash
uv run --frozen python scripts/run_public_quote_replay.py \
  --output ../retail-vertex-input-new --demo-invoice 557670

ORGREBASE_OAC_ROOT="$(pwd)/../oac-spec" \
ORGREBASE_OAC_ADAPTATION_MODE=required \
ORGREBASE_OAC_EXECUTION_MODE=LIVE_VERTEX \
ORGREBASE_WORKSPACE_TASK_INTAKE_REQUIRED=1 \
ORGREBASE_VERTEX_MODEL_ID=gemini-3.8-flash \
ORGREBASE_LOCAL_ROLE_SESSION_ORIGIN=http://127.0.0.1:8879 \
./run-enterprise-pilot.sh start \
  --pack ../retail-vertex-input-new/pack \
  --work-dir ../retail-vertex-runtime-new \
  --competition-mode golden --model-provider vertex-ai \
  --host 127.0.0.1 --port 8879
```

The first command checks the retained sample and seals a priced Pack only. It does not perform Formation, approval or Apply. Its suggested `demo-workspace.sqlite` is not the native workspace used here: this path uses a new database under `--work-dir`. Do not substitute the deterministic example's generated `start_command` for the Vertex command above.

This command enables the optional local role mode. The browser address, `ORGREBASE_LOCAL_ROLE_SESSION_ORIGIN` and listener port must match; use `http://127.0.0.1:8879` throughout this example, not `localhost`. Omit the environment variable to retain the ordinary local workflow.

The sidebar's “Switch demo role” menu lists roles defined by the server for the current workspace. Select a role and click “Use this role.” Switching discards unsent drafts while retaining registered proposals, approvals and run records. Browser cookies carry the session; mutations undergo CSRF, role and exact-owner checks. This is local role simulation, not employee authentication or enterprise SSO. Production continues to use [OIDC and server-owned membership](../AUTHENTICATED-DEPLOYMENT.md).

Open `http://127.0.0.1:8879/` and follow the role sequence below. Startup preloads the enterprise materials; the onboarding owner first reviews their provenance:

1. Select “Organization onboarding owner.” Review the materials and provenance, generate the organizational mapping candidate, inspect the actual model record, complete the three acknowledgements and server review gate, then admit it. Organizational admission does not approve a quote-rule change.
2. Switch to “Business requester.” Submit and confirm the quote task for this basket. Execute native domain tasks and the Reviewer, then inspect the baseline Quote, quantities, historical unit prices, currency and tax policy.
3. As the requester, create a change with “Pricing policy” and change “Discount (%)” from `0` to `10`. Enter the required source reference, for example `policy:controlled-volume-discount-10pct@v1`; preserve the tax rate, tax basis label, quantities, unit prices and currency. Enter a percentage; submission converts `10` to the API value `discount_bps: 1000`.
4. Check the impact, including this ChangeSet's own AT tasks, candidates, independent contract review, owner and before/after amounts. Preview saves candidate and audit evidence while leaving the official Quote and rules unchanged. The requester has no approval permission.
5. Switch to the proposal's “Finance owner,” reselect the same proposal, complete the review acknowledgement and approve the exact candidate. This role has no execution permission: the proposal should be approved while the official Quote remains v1.
6. Switch to “Change executor,” select that approved proposal and click “Apply approved proposal.” Verify the actual Apply receipt and successor Quote v2. Model output, preview amounts or an approval notification alone do not establish effectiveness.

If evidence is insufficient before approval, the finance owner can return the same proposal for evidence. The requester supplies current admitted evidence and selects a registered executor instance, then obtains a new preview, review and approval. Earlier rounds are preserved; an executor still applies the official Quote change. See [evidence recovery for the same ChangeSet](agentteams.en.md#evidence-recovery). This differs from rejecting the 20% proposal below: rejection does not automatically open a recovery round.

Ordinary local mode has no role sessions; production accounts use actual membership permissions. If an account has both approval and execution permission, the UI checks the permitted actions again after approval and proceeds to Apply automatically. Approval permission alone leaves the proposal approved for an executor.

For this fixed sample, expect GBP 1,928.45 at 0% discount and GBP 1,735.61 in Quote v2 after exact approval and Apply of the 10% policy. Switch back to the requester and create a `20`% discount proposal with a new source reference, for example `policy:controlled-discount-20pct-review@v1`. Preview it, switch to the corresponding finance owner, enter a reason and record rejection. The official result must remain v2 / GBP 1,735.61. Preserve the basket and tax rate and check actual receipts; rejecting an unapplied proposal is not a rollback of an applied version.

For capability governance, use “Skill steward” to run isolated core-Skill compatibility and recovery validation and review the experience candidate. Read the required owner from the current API rather than substituting the quote approver. These operations neither change the current Quote nor constitute production canary traffic. Switch back to a requester with export permission to export the Quote and audit records.

These are executable instructions, not a predeclared successful run. Accept the outcome using that run's own identity, model receipts, approval and Apply records. UCI items, quantities and historical prices are public observed fields; organization, roles, the 20% tax rate and discount policy are controlled settings. Amount differences are not enterprise ROI. The model-free path below remains a separate arithmetic check with a separate execution identity.

## Public-transaction pricing

Run from an installed product checkout with a new output directory:

```bash
uv run --frozen python scripts/run_public_quote_replay.py \
  --output ../retail-demo-new --demo-invoice 557670

ORGREBASE_ENTERPRISE_PACK="$(pwd)/../retail-demo-new/pack" \
ORGREBASE_WORKSPACE_DB="$(pwd)/../retail-demo-new/demo-workspace.sqlite" \
ORGREBASE_CHANGE_MODEL_PROVIDER=local-deterministic \
uv run --frozen orgrebase serve --local-demo --host 127.0.0.1 --port 8879
```

Open `http://127.0.0.1:8879/`. Preparation does not pre-create a successful Quote or approval. Submit and confirm the task, run it, then inspect six product lines and amounts. Propose a discount change from 0% to 10%. Preview leaves the current result unchanged; the exact owner's approval permits the successor.

For this fixed sample, the subtotal is GBP 1,607.04. Under a controlled 20% exclusive tax, the total is GBP 1,928.45 before the discount and GBP 1,735.61 afterward. The difference is a policy calculation, not labor-cost savings. Product quantities and unit prices come from public UCI historical records; tax, discount, organization and permissions are controlled settings.

## Completion criteria

Check the current run, candidate/task bindings, exact approval, Apply receipt, final Quote and event chain together. Rejection should preserve the existing result, and refresh should not repeat execution. Scenarios, historical archives and model providers retain separate identities; do not combine them into a stronger success claim.

[Full pricing reference (Chinese)](../PRICED-QUOTE-DEMO.md) · [Historical verification scope (English)](../HISTORICAL-BUILD-VERIFICATION.md).

## Inspect collaboration and execution evidence

Under Real collaboration, expand each actor to inspect its task, minimum input context, observed Tool / Skill calls, candidate outputs, handoff targets, and receipt timeline. Baseline formation and subsequent changes are separate phases. Under Business evolution and acceptance, select a specific change and inspect its own task details, owner decision, and before/after result.

A complete walkthrough also includes enterprise materials and the organization contract, published Skill source and controlled recovery, the current run archive, independent archive rechecks, and operations. Connector configuration, candidate approval and archive verification each display their actual state.

Under **Capabilities & Assurance → Run records & validation**, choose **Current task records**, **Public-data case**, or **Reliability checks** to inspect delivery and approval, explore procurement-rule impact scope, or review formation and recovery results.
