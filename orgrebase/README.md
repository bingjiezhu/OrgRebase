<p align="right"><strong>English</strong> · <a href="README.zh-CN.md">简体中文</a></p>

# OrgRebase

**Governed enterprise change, from source facts to approved results.**

OrgRebase coordinates changes to enterprise facts, policies, responsibilities and product plans across existing work. It binds a change to its sources and owners, identifies affected results, prepares a reviewable successor, and applies the exact result an authorized owner approves. Agents, Tools and Skills produce candidates and evidence. A deterministic control plane enforces scope, approval and canonical writes.

The runnable reference is **Enterprise Quote**, with Product, Legal, Finance and GTM domain roles. A discount adjustment is one example of enterprise change: the system identifies quotes that depend on the earlier value, preserves unaffected work and verifies the approved successor. The model-free first run uses public historical baskets. The priced-template walkthrough uses synthetic initial facts, and the native model-assisted journey uses a controlled synthetic enterprise Pack. Policies and identities in both paths are controlled inputs.

This tree contains **`0.5.0b4` Beta**. Its validation scope is controlled local execution; customer integration and production qualification require separate acceptance.

[![CI](https://github.com/bingjiezhu/OrgRebase/actions/workflows/ci.yml/badge.svg)](https://github.com/bingjiezhu/OrgRebase/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-source%20guides-0A66C2)](docs/guide/index.en.md)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

## Start here

| Goal | Documentation |
|---|---|
| Install and verify one transaction | [First run](#first-run-verify-one-public-transaction) · [Quickstart](docs/guide/quickstart.en.md) |
| Understand the design | [Core approach](docs/guide/approach.en.md) · [Architecture](docs/guide/architecture.en.md) |
| Review and approve a change in the WebUI | [Browser walkthrough](docs/guide/demo.en.md) |
| Adapt supported enterprise inputs | [Reuse guide](docs/REUSE-AND-LICENSING.md#try-an-enterprise-pack-without-changing-the-engine) · [Skills and Packs](docs/guide/skills.en.md) |
| Integrate or operate the service | [API interfaces](docs/MODEL-AGENT-TOOL-INTERFACES.md#public-api-surface) · [Deployment](docs/guide/deployment.en.md) |
| Contribute | [Development checks](#development-and-feedback) · [Contributing](CONTRIBUTING.md) |

Source guides are maintained alongside the code. The [deployed documentation](https://bingjiezhu.github.io/OrgRebase/en/) may reflect an earlier revision.

## First run: verify one public transaction

Use CPython 3.12–3.14 and [uv](https://docs.astral.sh/uv/). Run from the product directory, which contains `pyproject.toml`, `uv.lock` and `scripts/`. In a workspace clone, this directory is `OrgRebase/orgrebase`.

The local replay writes a SQLite database. Python must link SQLite 3.51.3 or later, or an official patched 3.44.x/3.50.x release. Check the [linked-library requirement and recovery steps](docs/guide/quickstart.en.md#sqlite-runtime) before using a system Python.

```bash
uv sync --locked
uv run --frozen python scripts/run_public_quote_replay.py \
  --output ../quote-first-run --limit 1
```

Dependency installation needs network access or a populated cache. The replay itself needs no model, cloud credentials, PostgreSQL or OAC installation. The output directory must not already exist; use a new path for each run.

Open `../quote-first-run/index.html`. The first default sample, invoice `560602`, should show `PASS`. In `report.json`, check `status=PASS` and the following fields under `measurement_summary`:

```text
planned_cases = 1
outcomes.PASS = 1
outcomes.FAILED = 0
outcomes.INCOMPLETE = 0
```

The command executes quote formation, Preview, scripted-owner approval, Apply and an independent amount check. It also exercises rejection paths. Basket records come from a public historical dataset; discount, tax and organizational identities are controlled inputs. This verifies the local pricing and governance path. Use the [browser walkthrough](docs/guide/demo.en.md) for human review and the [quickstart](docs/guide/quickstart.en.md) for the complete procedure.

## Create a priced workspace

From the `orgrebase/` directory, initialize a supported template with typed pricing inputs:

```bash
uv run --frozen orgrebase enterprise-pilot-init \
  --template priced-quote --output ../priced-draft
uv run --frozen orgrebase enterprise-pilot-draft-preflight --draft ../priced-draft
uv run --frozen orgrebase enterprise-pilot-seal \
  --draft ../priced-draft --output ../priced-pack
```

The template contains synthetic initial facts and owner mappings for the v2 Quote adapter. Creating or sealing it does not authorize a task or approve a business change. The [model-free browser walkthrough](docs/guide/demo.en.md#model-free-priced-workspace) runs a new Quote + Discount Memo workspace through contract admission, task confirmation, source review, both deliverable owners and the authorized executor. The reference starts at USD 95.00; the approved 10% discount produces USD 90.00. Use fresh output directories and a new database.

## Supported scope

| Enterprise change or capability | Implemented reference scope |
|---|---|
| Launch date, currency, product plan | Source-bound changes in the Enterprise Quote reference, with dependency checks and owner review |
| Quote basket and pricing policy | Explicit v2 pricing profile with deterministic amount calculation and independent verification |
| Responsibility handover | Opt-in policy requiring consent from both owners |
| Quote and Discount Memo | Explicit two-deliverable profile in a new isolated workspace |
| Experience and Skill evolution | Governed candidate and qualification slices; cross-task quality, full publication and adoption remain open |

New deliverable types require admitted handlers, typed sources, owners and acceptance cases. Real enterprise identity, source and target integrations need their own qualification. The existing Dataverse target adapter updates only a configured draft Quote's `name` and `description`; it does not write prices or line items. See the [system map](docs/SYSTEM-MAP.md), [priced Quote guide](docs/PRICED-QUOTE-DEMO.md) and [target operations](docs/DATAVERSE-TARGET-OPERATIONS.md) for exact boundaries.

To test supported configuration reuse, [create and seal an Enterprise Pack](docs/REUSE-AND-LICENSING.md#try-an-enterprise-pack-without-changing-the-engine) with different `product_plan` and `currency` facts. This exercise runs the existing handler without modifying the engine.

## Architecture at a glance

| Component | Responsibility |
|---|---|
| OAC | Proposed organizational contract for facts, owners, capabilities and admitted scope |
| AgentTeams | Task execution, bounded context and evidence handoffs |
| Agents, Tools and Skills | Candidate generation and evidence collection |
| OrgRebase control plane | Provenance and dependency checks, impact analysis, Preview, authority checks and recovery |
| Human owners and authorized executors | Review exact candidates and apply approved changes through the canonical write path |

Selective Rebase updates affected results while retaining unaffected work within a verified dependency boundary. Missing coverage remains `UNKNOWN`. Execution completion, candidate admission, independent acceptance, human approval and Apply are separate states. Approval alone does not modify business state; StateStore and RebaseWorkflow own canonical writes.

The [core approach](docs/guide/approach.en.md) explains these design choices. The [architecture guide](docs/guide/architecture.en.md) and [evidence recovery guide](docs/guide/agentteams.en.md) describe the implementation and recovery behavior.

## Run the reference journey

The public workspace contains sibling `orgrebase/` and `oac-spec/` projects. The model-assisted reference journey needs the matching OAC source, an authorized Vertex project and credentials stored outside the repository. From the product directory:

```bash
export ORGREBASE_VERTEX_PROJECT="YOUR_GCP_PROJECT"
export ORGREBASE_VERTEX_MODEL_ID="gemini-3.8-flash"
export ORGREBASE_OAC_ROOT="$(pwd)/../oac-spec"
./run-semifinal-demo.sh live
```

The explicit `live` launcher defaults to Vertex Gemini 3.8 Flash. Cloud calls may incur provider charges. Follow the [Vertex setup](docs/guide/models-vertex.en.md) and [browser walkthrough](docs/guide/demo.en.md). AgentTeams execution and independent contract checks run locally; Matrix and object-storage transports in this reference use controlled fixtures. External distributed execution needs separate validation.

Optional model paths and historical evidence checks are documented in [model interfaces](docs/MODEL-AGENT-TOOL-INTERFACES.md). The [deployment guide](docs/guide/deployment.en.md) covers authenticated PostgreSQL operation, source binding, target qualification and operational prerequisites.

## Development and feedback

Install the developer tools and run the bounded core check:

```bash
uv sync --locked --extra dev
make check-core
```

`make check-core` reconstructs the pinned AgentTeams checkout from the bundled source archive. It needs no OAC, PostgreSQL or service credentials. The full development-workspace `make check` also needs the matching OAC project, PostgreSQL tools and retained historical archives excluded from the public source profile. For checks supported by the public tree, use [Contributing](CONTRIBUTING.md) and the [public release gates](docs/RELEASE-CANDIDATES.md).

Use [Issues](https://github.com/bingjiezhu/OrgRebase/issues) for bugs, feature proposals and reproducible reuse attempts. Follow [Security](SECURITY.md) for private vulnerability reports. The [community guide](docs/COMMUNITY.md) lists public participation paths.

## Repository map

```text
src/orgrebase/                    runtime and deterministic control plane
examples/enterprise-quote-pilot/   bounded enterprise reference Packs
skills/                          governed Skill packages
schemas/                         contracts and projections
tests/                           behavior and boundary checks
docs/guide/                      user and contributor documentation
vendor/agentteams/                pinned reconstructable source bundle
```

`uv.lock` records the dependency resolution. OAC is maintained in the adjacent `oac-spec/` project and is not included in the OrgRebase Python package.

## Project status

The Beta has controlled local validation for its named reference paths. Customer IAM integration, employee UAT, production capacity, SLA and ROI remain separate acceptance work. See [release qualification](docs/RELEASE-CANDIDATES.md) for validation scope and release checks.

## License

Project-owned OrgRebase material is licensed under **[Apache-2.0](LICENSE)**.

Third-party code, dependencies, datasets and optional models retain their own terms and attribution notices. See [license scope](LICENSE.md), [third-party inventory](docs/THIRD-PARTY-INVENTORY.md) and [NOTICE.md](NOTICE.md).
