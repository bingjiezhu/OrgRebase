<p align="right"><strong>English</strong> · <a href="README.zh-CN.md">简体中文</a></p>

# OrgRebase

**Governed enterprise change, from source facts to approved results.**

OrgRebase coordinates changes to enterprise facts, policies, responsibilities and product plans across existing work. It binds a change to its sources and owners, identifies affected results, prepares a reviewable successor, and applies the exact result an authorized owner approves. Agents, Tools and Skills produce candidates and evidence. A deterministic control plane enforces scope, approval and canonical writes.

The runnable reference is **Enterprise Quote**, with Product, Legal, Finance and GTM domain roles. A discount adjustment is one example of enterprise change: the system identifies quotes that depend on the earlier value, preserves unaffected work and verifies the approved successor. The model-free first run uses public historical baskets. The priced-template walkthrough uses synthetic initial facts, and the native model-assisted journey uses a controlled synthetic enterprise Pack. Policies and identities in both paths are controlled inputs.

This tree contains OrgRebase **`0.5.0b4` Beta** and OAC **`0.3.0a0`**. Validation covers bounded local reference paths; customer integration and production qualification require separate acceptance.

[![CI](https://github.com/bingjiezhu/OrgRebase/actions/workflows/ci.yml/badge.svg)](https://github.com/bingjiezhu/OrgRebase/actions/workflows/ci.yml)
[![Documentation](https://img.shields.io/badge/docs-source%20guides-0A66C2)](orgrebase/docs/guide/index.en.md)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

## Get started

From the repository root, use CPython 3.12–3.14 and [uv](https://docs.astral.sh/uv/). Installing locked dependencies needs network access or a populated cache; this replay needs no model, cloud credentials, PostgreSQL, or OAC CLI.

The replay writes a local SQLite database. Python must link SQLite 3.51.3 or later, or an official patched 3.44.x/3.50.x release; see the [linked-library check and upgrade guidance](orgrebase/docs/guide/quickstart.en.md#sqlite-runtime).

```bash
cd orgrebase
uv sync --locked
uv run --frozen python scripts/run_public_quote_replay.py \
  --output ../quote-first-run --limit 1
```

The output directory must not exist before the run. Open `../quote-first-run/index.html`; invoice `560602` should show `PASS`. In `report.json`, expect `status=PASS` and, under `measurement_summary`, `planned_cases=1`, `outcomes.PASS=1`, `outcomes.FAILED=0` and `outcomes.INCOMPLETE=0`. The replay performs quote formation, Preview, scripted-owner approval, Apply and an independent amount check. See the [full quickstart](orgrebase/docs/guide/quickstart.en.md).

## Create a priced workspace

From the `orgrebase/` directory, initialize a supported template with typed pricing inputs:

```bash
uv run --frozen orgrebase enterprise-pilot-init \
  --template priced-quote --output ../priced-draft
uv run --frozen orgrebase enterprise-pilot-draft-preflight --draft ../priced-draft
uv run --frozen orgrebase enterprise-pilot-seal \
  --draft ../priced-draft --output ../priced-pack
```

The template contains synthetic initial facts and owner mappings for the v2 Quote adapter. Creating or sealing it does not authorize a task or approve a business change. The [model-free browser walkthrough](orgrebase/docs/guide/demo.en.md#model-free-priced-workspace) runs a new Quote + Discount Memo workspace through contract admission, task confirmation, source review, both deliverable owners and the authorized executor. The reference starts at USD 95.00; the approved 10% discount produces USD 90.00. Use fresh output directories and a new database.

## How it works

| Boundary | Responsibility |
|---|---|
| [OAC](oac-spec/README.md) | Proposed organizational contract for facts, owners, capabilities, and admitted scope |
| AgentTeams, Agents, Tools, Skills | Bounded tasks, handoffs, candidates, and evidence |
| OrgRebase control plane | Source and dependency checks, impact, exact Preview, admission, and recovery |
| Human Owner and authorized executor | Approve the precise change, then apply it through the canonical write path |

The control plane can reuse unaffected results because it tracks dependencies and actual reads. Missing coverage remains `UNKNOWN`; task completion and model output never grant permission to update business state. Each approved successor retains its change, owner, version, and receipt. The [core approach](orgrebase/docs/guide/approach.en.md) explains the design and its limits.

## Supported reference scope

| Enterprise change | Current reference behavior |
|---|---|
| Launch date, currency, product plan | Source-bound changes to the Enterprise Quote reference, with affected work and owner review |
| Quote basket and pricing policy | Explicit v2 pricing profile; controlled local amount verification, not customer tax or exchange-rate advice |
| Responsibility handover | Opt-in policy requiring consent from both owners |
| Quote + Discount Memo | Explicit two-deliverable profile in an isolated workspace; new deliverable families still require admitted handlers |
| Experience and Skill evolution | Governed candidate and qualification slices; cross-task quality, full publication and adoption remain open |

These paths have bounded local validation. New deliverable types and real enterprise integrations need their own admission and acceptance. The [Enterprise Pack exercise](orgrebase/docs/REUSE-AND-LICENSING.md#try-an-enterprise-pack-without-changing-the-engine) shows how to adapt `product_plan` and `currency` facts without changing the engine.

## Documentation and participation

| Goal | Entry point |
|---|---|
| Understand the product and authority model | [Core approach](orgrebase/docs/guide/approach.en.md) · [Architecture](orgrebase/docs/guide/architecture.en.md) |
| Run the quote workflow or inspect the WebUI | [Quickstart](orgrebase/docs/guide/quickstart.en.md) · [Demo guide](orgrebase/docs/guide/demo.en.md) |
| Reuse the control plane, Skills, or an enterprise Pack | [Reuse guide](orgrebase/docs/REUSE-AND-LICENSING.md) · [Skills](orgrebase/docs/guide/skills.en.md) |
| Configure a model or authenticated deployment | [Vertex](orgrebase/docs/guide/models-vertex.en.md) · [Deployment](orgrebase/docs/guide/deployment.en.md) |
| Browse this revision's documentation | [English](orgrebase/docs/guide/index.en.md) · [中文](orgrebase/docs/guide/index.zh.md) · [deployed site](https://bingjiezhu.github.io/OrgRebase/en/) (may lag source) |
| Contribute or report a vulnerability | [Contributing](CONTRIBUTING.md) · [Community](COMMUNITY.md) · [Security](SECURITY.md) |

The repository contains `orgrebase/` (runtime, WebUI, Skills, tests, and documentation) and `oac-spec/` (contracts, compiler, verifier, and conformance tests). The Python package lives in `orgrebase/`; OAC remains an adjacent project. The [product README](orgrebase/README.md) covers the native AgentTeams journey and development checks.

## Project status

The Beta has **controlled local validation** for its named reference paths. Public replay, local PostgreSQL and protocol-fixture checks establish the behavior exercised in those environments. Customer identity integration, employee UAT, production capacity, SLA and ROI remain separate acceptance work. The [release qualification guide](orgrebase/docs/RELEASE-CANDIDATES.md) describes validation scope and release checks.

Every pull request and main push runs product, OAC and enterprise-boundary CI; release qualification uses a separate maintainer-initiated gate. Check the workflow result for the revision you use. The deployed [Pages site](https://bingjiezhu.github.io/OrgRebase/en/) may reflect an earlier commit.

## License

Project-owned OrgRebase and OAC material is licensed under **[Apache-2.0](LICENSE)**.

Third-party code, dependencies, datasets and optional models retain their own terms and attribution notices. See the [component license index](LICENSES.md).
