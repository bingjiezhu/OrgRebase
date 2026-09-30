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
uv sync --locked --extra dev
make check-core
```

The core check requires no OAC checkout, PostgreSQL or service credentials,
including in a product-only source tree. Integration checks require a matching,
admitted OAC tree at `../oac-spec` or `ORGREBASE_OAC_ROOT`.
It runs lint, assets/schema checks, retained core evidence verification, an isolated
runtime-resource probe and the explicit `CORE_TESTS` list in the Makefile. It does
not claim complete integration coverage. The [first-run guide](README.md#first-run-verify-one-public-transaction)
also runs a fresh, bounded pricing/governance workflow.

For changes involving OAC integration, deployment or database behavior, install
the sibling OAC environment with `uv sync --locked --all-extras` in `oac-spec/`,
supply the admitted source and [PostgreSQL tools](docs/AUTHENTICATED-DEPLOYMENT.md),
then run these public checks from `orgrebase/`:

```bash
make check-core
make check-enterprise-boundaries
python3 -B scripts/build_source_snapshot.py public-check --snapshot-root ..
```

The OAC public check also requires Go 1.22. Enterprise boundary checks require
PostgreSQL; an unavailable database tool is a failure. These are source checks;
release qualification additionally binds the exact artifacts to installation,
SBOM and provenance results. Full `make check` remains an internal development
gate for a workspace carrying its complete historical archive inputs, OAC and
PostgreSQL. Those archives are outside the public source profile; a fresh public
clone cannot use that gate as its release qualification.

This workspace clone includes `oac-spec/`. On protected source and runtime-resource
changes, push and pull-request CI runs product core checks, the independent OAC
conformance gate, and PostgreSQL/OAC enterprise boundary checks. The public release
gate remains a maintainer-initiated `workflow_dispatch` job, with
`ORGREBASE_OAC_ROOT` pointing at the in-tree `oac-spec/`. A skipped release job does
not qualify a candidate. See [candidate qualification](docs/RELEASE-CANDIDATES.md)
and the [community note](docs/COMMUNITY.md).

Open an issue with the problem, threatened invariant, smallest interface change, and the
evidence that will prove it. Reuse and integration feedback uses the Reuse template.
Security reports follow [SECURITY.md](SECURITY.md).

Without `uv`:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
make check-core PYTHON=.venv/bin/python
```

The Makefile otherwise defaults to `uv run python`. Pass the same `PYTHON`
override to other Makefile checks when `uv` is unavailable. The pip installation
does not reproduce `uv.lock`; use the locked uv environment for release
qualification. Local SQLite runs also require a [patched linked SQLite
library](docs/guide/quickstart.en.md#sqlite-runtime).

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

Project-owned OrgRebase material, including documentation and Skills, is licensed
under [Apache-2.0](LICENSE). See [LICENSE.md](LICENSE.md) for scope and historical
release terms.

By intentionally submitting a contribution for inclusion, you confirm that you have
the right to submit it and offer it under Apache-2.0, as described in Section 5 of
the license. You retain copyright in your contribution. Contribution does not
transfer ownership or automatically accept a future contributor agreement.

Any separate contribution agreement applies only after the contributor and the
project explicitly accept it. This project does not require a CLA or a DCO sign-off
for contributions.
