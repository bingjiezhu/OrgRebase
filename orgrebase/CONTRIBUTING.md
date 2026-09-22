# Contributing to OrgRebase

OrgRebase welcomes small, evidence-backed changes that preserve the control-plane
invariants.

Public homepage: English [`README.md`](README.md) and Chinese [`README.zh-CN.md`](README.zh-CN.md).
Keep product claims and status labels in both files in sync.

The unique human-readable source of current product truth is
[`docs/SYSTEM-MAP.md`](docs/SYSTEM-MAP.md). Load detailed context through
[`docs/README.md`](docs/README.md) only when the task needs it. Historical numbered
Specs have been moved outside the runnable tree. Do not recreate that worklog pattern or
add a competing "current ledger"; update the smallest current contract, test, and L0/L1
document instead.

## Development

```bash
uv sync --locked --all-extras
make check-core
```

The core check requires no extra OAC checkout, PostgreSQL or service credentials
when this repository is cloned as a workspace (`orgrebase/` next to `oac-spec/`).
A product-only tree still uses `../oac-spec` or `ORGREBASE_OAC_ROOT`.
It runs lint, assets/schema checks, retained core evidence verification, an isolated
runtime-resource probe and the explicit `CORE_TESTS` list in the Makefile. It does
not claim complete integration coverage. The [first-run guide](README.md#first-run-verify-one-public-transaction)
also runs a fresh, bounded pricing/governance workflow.

A lightweight runtime source delivery can run the documented first run and `make check-core`,
but may omit complete historical evidence. Full `make check` also needs the full-profile
archive inputs referenced by its gates, in addition to OAC and PostgreSQL. Missing archives
are not evidence that a release passed; use the matching full source/evidence delivery.

For changes involving OAC integration, deployment or database behavior, supply the
admitted OAC source and [PostgreSQL tools](docs/AUTHENTICATED-DEPLOYMENT.md), then run
`make check`. Full checks require PostgreSQL; an unavailable database tool is a
failure.

Previously, full CI bound an external `OAC_REPOSITORY` and 40-character `OAC_REVISION`.
Now this workspace clone already contains `oac-spec/`. Ordinary push and pull-request
CI runs `make check-core` from `orgrebase/` with no OAC or service credentials. Full
OAC + PostgreSQL validation is `workflow_dispatch` only, with
`ORGREBASE_OAC_ROOT` pointing at the in-tree `oac-spec/`. A skipped full job cannot
qualify a release candidate. See [candidate qualification](docs/RELEASE-CANDIDATES.md)
and the [community note](docs/COMMUNITY.md).

Open an issue with the problem, threatened invariant, smallest interface change, and the
evidence that will prove it. Reuse and integration feedback uses the Reuse template.
Security reports follow [SECURITY.md](SECURITY.md).

Without `uv`:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Every behavior change needs a test for its invariant or failure boundary. New evidence
must declare one of the existing evidence classes; static assets, replay, and synthetic
fixtures must never be promoted to `LIVE_AGENTTEAMS`.

Use the status vocabulary literally: `DESIGNED`, `IMPLEMENTED`, `VALIDATED`, and
`NOT_RUN` are different facts. A passing unit test validates only its named boundary;
it does not make an external connector, AgentTeams cluster, enterprise baseline, or
production deployment live. Generated evidence must be reproducible from a documented
command, content-addressed where it crosses a trust boundary, free of secrets/raw
prompts, and reviewed before it is committed.

## Design rules

- Agents may propose; only the deterministic control plane writes canonical state.
- Absence of an admitted dependency is not proof of non-impact.
- Preserve immutable history; rollback is a compensating version, never deletion.
- Tool and Skill permissions must be explicit and default-deny.
- Agent runtimes submit candidates; deterministic approval and canonical writes remain
  in the control plane.
- Keep runtime lifecycle, Skill invocation, Tool call, approval, effect, and terminal
  evidence on one root `run_id`; never splice receipts from unrelated runs.
- Do not add a service, datastore, framework, or cloud dependency without a measured need.
- Keep product, OAC standard work, future research, machine evidence, and submission
  artifacts in their documented boundaries. A slide, video, historical replay, or
  completed Spec cannot promote a product claim.
- Before archiving superseded Specs, evidence, slides, recordings, or intermediate
  assets, create a manifest containing path, size, SHA-256, reason, and replacement.
  Preserve a recovery path; do not use destructive cleanup as product simplification.

Open an issue with the problem, threatened invariant, smallest interface change, and the
evidence that will prove it. Reuse and integration feedback uses the Reuse template.
Security reports follow [SECURITY.md](SECURITY.md).

## License and copyright

Project-owned OrgRebase material is Apache License 2.0, including documentation.
See [LICENSE.md](LICENSE.md). Earlier public revisions used PolyForm Noncommercial
1.0.0 plus a separate commercial grant, and one later commit used CC BY 4.0 for
prose. That history is in [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md).

By submitting a contribution, you confirm that you have the right to submit it, and you
license it to **Bingjie Zhu** under Apache-2.0 so it can be distributed with OrgRebase.
You keep copyright in your contribution unless a later CLA says otherwise.
