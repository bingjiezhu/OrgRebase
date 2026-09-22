# Reuse and licensing

OrgRebase separates the rules an enterprise supplies from the controls that make a
change reviewable, approved, and traceable. The current executable reference covers
one Quote per workspace. Reusing that reference is a smaller task than introducing
a new business object or qualifying a customer system.

## What stays and what changes

| Layer | Keep | Adapt for the enterprise | Source and acceptance |
|---|---|---|---|
| Governance engine | Source-version binding, impact analysis, independent verification, exact-owner approval, selective Apply, immutable receipts and recovery | Identity and workspace configuration, authority mapping and deployment policies | [Workflow](../src/orgrebase/workflow.py), [StateStore](../src/orgrebase/store.py), [approval authority](../src/orgrebase/workspace/approval_authority.py), [architecture boundaries](../tests/test_architecture_boundaries.py) |
| Quote rules and context | Pack validation and explicit admission before execution | Organization, customer/task inputs, domain facts, owners, capabilities and dependencies | [Evergreen Pack](../examples/enterprise-quote-pilot/evergreen/pack.json), [Pack schema](../schemas/workspace-enterprise-quote-pilot-pack.schema.json), [Pilot handler](../src/orgrebase/workspace/pilot.py) |
| Skills | Package discovery, content binding, evaluation, explicit release and exact predecessor recovery where supported | Candidate programs, input/output schemas and domain evaluation cases | [Current registry](../configs/workspace/skill-registry.json), [Skill list](SKILL-LIST.md); changing package bytes requires a new version and qualification |
| Source connectors | Source scope, signed-in owner confirmation, version evidence and coverage checks | Enterprise endpoint, source-field mappings and source-specific reader | [Source bindings](../src/orgrebase/workspace/source_bindings.py), [source onboarding](DATAVERSE-SOURCE-ONBOARDING.md) |
| Target connectors | Effect approval, queue, target barrier, idempotent identity and reconciliation through one Commit Gateway | Target-specific preconditions, write protocol and positive read-back evidence | [TargetAdapter](../src/orgrebase/commit_gateway.py), [Dataverse operations](DATAVERSE-TARGET-OPERATIONS.md) |
| Agent and observation transports | Candidate-only authority and same-run binding | Qualified AgentTeams deployment, model provider and optional Matrix configuration | [AgentTeams source lock](../agentteams/teamharness-lock.json), [model interfaces](MODEL-AGENT-TOOL-INTERFACES.md), [Matrix observation](MATRIX-OBSERVATION.md) |

The effect worker and Commit Gateway are part of the reusable control plane, not
connector code to discard. A new connector implements `TargetAdapter.execute(effect)`
and `TargetAdapter.query_effect(effect)` and returns a `TargetResolution` bound to the
same effect identity and request digest. An uncertain result remains `UNKNOWN`;
an absent response is not permission to issue a second write.

The current Dataverse target changes only the configured draft Quote's `name` and
`description`. It does not publish prices, line items, orders or quote status. An
internal Quote update and a confirmed external system effect are separate facts.

## Use impact analysis as a library

The pure impact modules can be imported without loading the HTTP service, model
adapters or database drivers. In the installed project environment, this reads the
bundled synthetic fixture and produces a preview without starting a server:

```python
from orgrebase.fixture import load_fixture
from orgrebase.impact import ImpactEngine, build_change_set

fixture = load_fixture()
preview = ImpactEngine(fixture).preview(build_change_set(fixture))
print(preview.counts)
```

This is analysis only. Approval, persistence and Apply still use the service and
its normal dependencies. The existing `from orgrebase import OrgRebaseService`
entry point remains available and loads the service when requested.

## Smallest adaptation path

For another enterprise using the supported Quote workflow:

1. Follow the [reference setup](../README.md#run-the-reference-journey) and complete
   the fictional example first.
2. Create a new Pack with `run-enterprise-pilot.sh init`; edit the generated JSON,
   then seal it into a new output directory. The [Pilot Runbook](ENTERPRISE-PILOT-RUNBOOK.md#7-使用企业自己的-pack)
   contains the commands; retain the original sealed sample.
3. Review `profile.json` and the five `components/` projections in the generated
   Pack. Facts, authority, capability and dependency inputs must agree. Sealing
   recomputes their bindings; it does not grant owner approval.
4. Start a separate workspace with that Pack. Admit the OAC contract through the
   designated owner, run the task, inspect a zero-write Preview, approve the exact
   change and verify the resulting Quote and receipts.
5. Qualify real source and target connections separately. Exercise wrong-owner,
   stale-version, missing-evidence and uncertain-effect cases before using customer
   data or enabling external writes. Use [authenticated deployment](AUTHENTICATED-DEPLOYMENT.md)
   for verified identity and PostgreSQL operation.

The Pilot handler supports `launch_date`, `currency` and `product_plan` changes for
`enterprise_quote@v1`; the reference walkthrough demonstrates the first two.
`enterprise_quote@v2` additionally supports `quote_basket` and `pricing_policy` changes
using admitted, typed sources and the implemented deterministic pricing rules. See the
[priced Quote demo](PRICED-QUOTE-DEMO.md) for its supported inputs and calculations.
It does not implement arbitrary pricing formulas or translate any free-text policy
into production code.

A new business object family needs a compatible contract/profile, a domain handler
that produces typed outputs and dependency evidence, and acceptance cases that verify
both intended changes and protected unchanged fields. Changing a Pack or adding a
prompt is insufficient. There is no supported drop-in registration API for arbitrary
business handlers; review the [Pilot compatibility check](../src/orgrebase/workspace/pilot.py)
and [OAC adaptation boundary](OAC-ORGREBASE-PRODUCT-BOUNDARY.md) before extending it.

## Try a rule Pack without changing the engine

This exercise changes two facts in a fictional enterprise, seals a new Pack and
runs the existing deterministic handler. It needs the locked product environment
only; it does not start a model, connect to a customer, or admit an OAC contract.
Run from the source directory. Use a new `../quote-adaptation` directory on each attempt.

```bash
uv run --frozen orgrebase enterprise-pilot-init --output ../quote-adaptation/draft
uv run --frozen python - <<'PY'
import json
from pathlib import Path

path = Path("../quote-adaptation/draft/components/knowledge.json")
component = json.loads(path.read_text())
for source in component["projection"]["source_values"]:
    if source["slot_id"] == "product_plan":
        source["value"] = "Workshop Enterprise"
        source["source_version"] = "controlled-workshop-v1"
    elif source["slot_id"] == "currency":
        source["value"] = "GBP"
        source["source_version"] = "controlled-workshop-v1"
path.write_text(json.dumps(component, ensure_ascii=False, indent=2) + "\n")
PY
uv run --frozen orgrebase enterprise-pilot-seal \
  --draft ../quote-adaptation/draft --output ../quote-adaptation/sealed
uv run --frozen orgrebase enterprise-pilot-check \
  --pack ../quote-adaptation/sealed --work-dir ../quote-adaptation/check
```

In `check/verification.json`, expect `status=PASS`, a final Quote reference ending
in `@v3`, two wrong-owner rejections, two stale-approval rejections and a passing
backup/restore check. Inspect `check/evidence/quote-export.json` and `check/evidence/pilot-loop.json`
for the actual facts and revisions. The initial currency is now GBP; the existing
controlled proposal still changes it to EUR. `Workshop Enterprise` remains the
product plan. The source template and engine have not changed.

This establishes configuration reuse of one supported workflow. Scripted approvals
and fictional owners are deliberate test inputs, not a customer's acceptance. For
human operation, use the sealed Pack in the [interactive Pilot](ENTERPRISE-PILOT-RUNBOOK.md#7-使用企业自己的-pack)
and satisfy its OAC/model prerequisites. Never edit a Pack already bound to a run.

When adapting actual inputs, use the five component boundaries:

| File | Supply | Required consistency |
|---|---|---|
| `profile.json`, `components/domain.json` | Organization, task and supported change families | Organization IDs, task identity and handler/profile must agree |
| `components/knowledge.json` | Typed source values and proposed values | Real source identity, version, sensitivity and authority; unknown facts stay unknown |
| `components/authority.json` | Owners, delegated rights and purposes | Same principals as the task and source references; synthetic names are not enterprise identities |
| `components/capability.json` | Agent/Skill capability and template bindings | Only supported capabilities with exact versions; names alone do not provide executable code |
| `components/dependency.json` | Required fields and typed dependencies | Every required output has admitted sources; preserve protected unchanged fields |

The sealer recomputes digests and validates the exact file set. Do not hand-edit
digest fields or declare an incomplete source `COMPLETE` to get a passing check.
Invalid, missing or inconsistent inputs must be corrected at their source.

## Reuse verification separately

The standard-library command in the [README](../README.md#run-the-reference-journey)
verifies the retained Golden without installing OrgRebase or OAC. It checks that
specific historical package; it is not a general-purpose business acceptance API.

Core verifier interfaces also exist at `POST /api/receipts/verify`,
`POST /api/receipts/rollback/verify`, `POST /api/impact-certificates/verify` and
`POST /api/minimal-rebase-certificates/verify`. Their request schemas are defined
in [the Core API](../src/orgrebase/api.py). Core and authenticated Workspace APIs
are different surfaces: production Workspace deployment disables the Core surface
by default. Do not expose the local Core API as an unauthenticated enterprise
service. Verification never approves or applies a change. See the
[evidence map](VERIFICATION-EVIDENCE-MAP.md) for precise verifier scope and tests.

## License scope

| Material | Current terms | Adoption boundary |
|---|---|---|
| OrgRebase engine, project-authored adapters, documentation and synthetic fixtures | [Apache-2.0](../LICENSE) and [CC BY 4.0](../LICENSES/CC-BY-4.0.txt), allocated by [LICENSE.md](../LICENSE.md) | Same path licenses as OAC. Earlier public revisions used PolyForm Noncommercial 1.0.0; see [COMMERCIAL-LICENSE.md](../COMMERCIAL-LICENSE.md). |
| Project-owned domain rules and Skill packages | Apache-2.0 for the current tree. Sealed package `license` and contract `distribution.license` may still read `PolyForm-Noncommercial-1.0.0` | That sealed identifier keeps historical content addresses stable. Check the exact package and resource digests. |
| OAC, in this workspace or beside it | Apache-2.0 for executable assets; CC BY 4.0 for normative prose, as allocated by its `LICENSE.md` | Same path model as OrgRebase. Its proposed-standard status does not establish external certification. |
| Upstream AgentTeams | Apache-2.0 | The [vendor notice](../vendor/agentteams/README.md) identifies the exact reconstructable source bundle. Project-authored integration code retains the product terms. |
| Python and database dependencies | Each distribution's own terms; see [third-party inventory](THIRD-PARTY-INVENTORY.md) and [NOTICE.md](../NOTICE.md) | Psycopg and psycopg-binary are LGPL-3.0-only in the current lock. Preserve their notices and review the exact distributions supplied to customers. |
| Enterprise data, hosted services and optional datasets | Customer contracts, provider terms or the stated dataset license | Product permission does not grant rights to third-party data or services. See [data and privacy](WORKSPACE-DATA-AND-PRIVACY.md). |

The installed `psycopg[binary]` profile uses separately installed distributions with
packaged client libraries, as described by [Psycopg's installation documentation](https://www.psycopg.org/psycopg3/docs/basic/install.html).
Do not describe this as a license-free database dependency or assume the whole dependency
tree has only one license. The generated SBOM records versions, relationships and
artifact bindings; its components currently have no license fields, so use the
distribution license files as well when reviewing a delivery.

Apache-2.0 section 3 is the patent license for executable project assets. The earlier
PolyForm text also contained a patent defense termination; that text remains the grant
for revisions that still carry it, including tag `v0.4.0`. This guide adds no further
patent grant, contribution agreement, or service commitment.

## What an evaluator can reproduce

The current reference has controlled-local validation. It does not establish paid
customer adoption, production capacity or financial savings. Keep verification scopes separate:

| Check | Prerequisites | What it establishes |
|---|---|---|
| Archived evidence verifier | Python standard library and the retained evidence package | Integrity and causal consistency of that completed historical run |
| Fresh public-data Quote replay | Locked product environment; no OAC/model/PostgreSQL | Actual pricing/governance execution over historical baskets under declared controlled rules |
| Pack adaptation exercise and core contributor check | Locked product environment; no OAC/model/PostgreSQL | Supported configuration reuse and bounded core behavior, not complete integration coverage |
| Fresh interactive journey | Locked product environment, sibling OAC implementation and configured model provider | New execution under the selected local configuration and its own receipts |
| Enterprise acceptance | Customer identities, source/target access, operational requirements and representative workload | Only the customer-specific behavior actually exercised and measured |

Previously: a complete source bundle could include `orgrebase/` and `oac-spec/` as
siblings while a standalone product clone did not supply OAC; full integration CI
required an explicit `OAC_REPOSITORY` and exact `OAC_REVISION`.
Now: the public GitHub workspace clone already has both trees, so default
`../oac-spec` works. Fork CI still runs `check-core` without service credentials.
Full integration CI is `workflow_dispatch` against the in-tree `oac-spec/`; a skipped
full job cannot qualify a release candidate. Check the actual delivered revision and
files before claiming that a public repository reproduces a newer local working tree.
Reuse attempts and integration feedback belong in the public Reuse issue template;
this guide does not list unaffiliated production deployments.

WebUI presents business review and operations; the backend enforces their authority.
Optional Element messages show a minimized
snapshot of the same run, from one observation account; they do not prove that Workers
received their tasks through Matrix. Installing or refreshing the observer cannot
approve or apply a business change.
