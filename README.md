<p align="right"><a href="README.zh-CN.md">简体中文</a></p>

# OrgRebase

Source-available workspace for governed multi-agent work evolution: **OrgRebase** (product) and **OAC** (Organizational Agent Contract).

One clone contains both trees. OrgRebase is PolyForm Noncommercial 1.0.0; it is **not** OSI Open Source. OAC keeps Apache-2.0 and CC BY 4.0 on its own paths.

[![CI](https://github.com/bingjiezhu/OrgRebase/actions/workflows/ci.yml/badge.svg)](https://github.com/bingjiezhu/OrgRebase/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/bingjiezhu/OrgRebase)](https://github.com/bingjiezhu/OrgRebase/releases)
[![License](https://img.shields.io/badge/license-PolyForm%20NC%20%2B%20Apache--2.0%2FCC--BY-lightgrey)](LICENSES.md)

| Path | What it is | Version |
|---|---|---|
| [`orgrebase/`](orgrebase/README.md) | Control plane, WebUI, Skills, schemas, tests, quote replay | 0.4.0 |
| [`oac-spec/`](oac-spec/README.md) | Contract, schemas, compiler, verifier, TCK | 0.3.0a0 |

```text
enterprise facts and rule changes
        -> OAC contracts (scope, authority, evidence)
        -> AgentTeams domain tasks (candidates only)
        -> OrgRebase preview, named-owner approval, selective apply
        -> successor business object and receipts
```

## First run (no model, no OAC CLI)

CPython 3.12–3.14 and [uv](https://docs.astral.sh/uv/). Network is required to install locked dependencies. The replay itself needs no cloud credentials, PostgreSQL, or OAC.

```bash
git clone https://github.com/bingjiezhu/OrgRebase.git
cd OrgRebase/orgrebase
uv sync --locked --all-extras
uv run --frozen python scripts/run_public_quote_replay.py \
  --output ../quote-first-run --limit 1
```

The output directory must not already exist. Open `../quote-first-run/index.html`. Invoice `560602` should report `status=PASS` with before/after quotes for the same basket. Omit `--limit 1` and pick a new output directory to replay the pinned sample.

Contributor check from `orgrebase/`:

```bash
make check-core
```

## What is published

| Area | Where to look |
|---|---|
| Core engine | `orgrebase/src/orgrebase/` |
| Agent identities and AgentTeams lock | `orgrebase/docs/AGENT-IDENTITIES.md`, `orgrebase/agentteams/` |
| Skills | `orgrebase/skills/`, `orgrebase/docs/guide/skills.en.md` |
| Schemas | `orgrebase/schemas/`, `oac-spec/schemas/` |
| HTTP / model / tool interfaces | `orgrebase/docs/MODEL-AGENT-TOOL-INTERFACES.md` |
| Tests and evaluation | `orgrebase/tests/`, `make check-core`; OAC TCK under `oac-spec/` |
| Reuse contract | `orgrebase/docs/REUSE-AND-LICENSING.md` |
| Deploy | `orgrebase/docs/guide/deployment.en.md`, `orgrebase/docs/AUTHENTICATED-DEPLOYMENT.md` |
| Dependencies | `orgrebase/uv.lock`, `oac-spec/uv.lock` |
| Changelog | `orgrebase/CHANGELOG.md` |

AgentTeams v1.2.3 is an Apache-2.0 upstream, pinned as a Git bundle for offline reconstruction. OrgRebase adapters and locks are project code; they do not relicense AgentTeams and do not make a live AgentTeams cluster `LIVE` by being present.

## Native reference journey

From `orgrebase/`, after this workspace clone, OAC is already at `../oac-spec`:

```bash
export ORGREBASE_VERTEX_PROJECT="YOUR_GCP_PROJECT"
export ORGREBASE_VERTEX_MODEL_ID="gemini-3.8-flash"
export ORGREBASE_OAC_ROOT="$(pwd)/../oac-spec"
./run-semifinal-demo.sh live
```

Keep credentials in the environment, not in the repository. A configured provider is not proof of a successful call.

## Contributing and security

- Issues: bug, feature, and reuse/feedback templates
- Community and reuse record: [`COMMUNITY.md`](COMMUNITY.md)
- [`orgrebase/CONTRIBUTING.md`](orgrebase/CONTRIBUTING.md)
- [`orgrebase/SECURITY.md`](orgrebase/SECURITY.md) (private advisory)
- [`orgrebase/CODE_OF_CONDUCT.md`](orgrebase/CODE_OF_CONDUCT.md)
- Version tags on [Releases](https://github.com/bingjiezhu/OrgRebase/releases)

Core CI runs on every push. Full OAC + PostgreSQL validation is `workflow_dispatch` only.

AgentTeams is pinned as an Apache-2.0 dependency. This repository does not claim an
upstream AgentTeams contribution or unaffiliated production deployments. Record a
reuse attempt with the Reuse issue template.

## License

See [LICENSES.md](LICENSES.md). Commercial use of OrgRebase needs a separate written grant.
