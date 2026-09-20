# Community, reuse, and related projects / 社区、复用与相关项目

This note is the public record of how OrgRebase is meant to be reused. It does not
claim unaffiliated production deployments or upstream patches that are not linked
here. 本说明记录可核对的复用入口；未列出的第三方生产采用或上游补丁，这里不作宣称。

## Contribution and maintenance / 贡献与版本维护

| Channel / 渠道 | Where / 位置 |
|---|---|
| Bug, idea, and reuse reports | [Issues](https://github.com/bingjiezhu/OrgRebase/issues) (templates for bug, feature, and reuse) |
| Pull requests | [CONTRIBUTING.md](CONTRIBUTING.md) and the pull-request template |
| Security response | Private advisory: [SECURITY.md](SECURITY.md) |
| Conduct | [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) |
| Version tags | [Releases](https://github.com/bingjiezhu/OrgRebase/releases); product `orgrebase` is 0.4.0 |
| Dependency updates | Dependabot is configured for `/orgrebase`, `/oac-spec`, `/orgrebase/documentation`, and GitHub Actions. Version-update PRs are paused on this snapshot until a lockfile-complete review. Root `.github/workflows` must stay identical to `orgrebase/.github/workflows` |
| CI | `.github/workflows/ci.yml`: `make check-core` on every push; full OAC + PostgreSQL on `workflow_dispatch` |

GitHub Actions, issue templates, and Dependabot live in the repository-root
`.github/` directory. A copy is kept under `orgrebase/.github/` so the product tree
can verify the same workflow files.

## What is open to reuse / 开放复用面

| Asset | Path | Reuse contract |
|---|---|---|
| Control plane, WebUI, CLI | `orgrebase/src/orgrebase/` | PolyForm Noncommercial 1.0.0; commercial use needs a written grant |
| Skills | `orgrebase/skills/` | Same product terms; packages are versioned and fail-closed |
| Schemas and tests | `orgrebase/schemas/`, `orgrebase/tests/` | Same product terms |
| Quote replay (no model) | `orgrebase/scripts/run_public_quote_replay.py` | Public historical baskets; controlled discount/tax/authority |
| OAC contract, compiler, verifier, TCK | `oac-spec/` | Apache-2.0 (executable) and CC BY 4.0 (normative prose) |
| AgentTeams source pin | `orgrebase/vendor/agentteams/`, `orgrebase/agentteams/` | Apache-2.0 upstream, consumed here; see below |

Step-by-step adaptation is in [`orgrebase/docs/REUSE-AND-LICENSING.md`](orgrebase/docs/REUSE-AND-LICENSING.md).
The smallest no-model checks are the public quote first run and `make check-core`.

## AgentTeams and related projects / 与 AgentTeams 及相关项目的关系

AgentTeams v1.2.3 is an Apache-2.0 upstream. This repository pins a Git bundle for
offline reconstruction and keeps OrgRebase adapters, locks, and candidate-only
authority in project code. Presence of the pin does not relicense AgentTeams, does
not make a live cluster `LIVE`, and is **not** an upstream contribution to AgentTeams.

OAC is published in this same workspace so the contract can be reused under its own
Apache-2.0 / CC BY 4.0 terms without being nested inside the OrgRebase Python package.

No unaffiliated third-party production adoption is claimed. External reuse, Skill or
Pack adaptation, and integration attempts should be filed with the
[Reuse template](https://github.com/bingjiezhu/OrgRebase/issues/new?template=reuse.yml)
so they can be listed as feedback records.

## First-run record / 首跑记录

From `orgrebase/`, with CPython 3.12 and uv:

```bash
uv sync --locked --all-extras
uv run --frozen python scripts/run_public_quote_replay.py \
  --output ../quote-first-run --limit 1
```

Expected: invoice `560602`, `status=PASS`, `planned_cases=1`, `outcomes.PASS=1`.
This check needs no cloud credentials, PostgreSQL, or OAC CLI.
