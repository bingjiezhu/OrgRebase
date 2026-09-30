# Contributing

This repository is a workspace: `orgrebase/` (product) and `oac-spec/` (contract).

From the repository root, product changes follow
[`orgrebase/CONTRIBUTING.md`](orgrebase/CONTRIBUTING.md).

```bash
cd orgrebase
uv sync --locked --all-extras
make check-core
```

`make check-core` verifies and reconstructs the exact pinned AgentTeams
checkout from the included Git bundle on a fresh clone. For an isolated
preflight or offline troubleshooting, run
`uv run --frozen python scripts/fetch_pinned_agentteams.py` directly. Neither
path needs a live AgentTeams service.

From the repository root, OAC changes follow
[`oac-spec/CONTRIBUTING.md`](oac-spec/CONTRIBUTING.md).

```bash
cd oac-spec
uv sync --locked --all-extras
cd ..
python3 -B orgrebase/scripts/build_source_snapshot.py public-check --snapshot-root .
(cd oac-spec && uv build)
```

CI checks protected code and runtime resources on each push or pull request:
`orgrebase` core, the PostgreSQL/OAC enterprise boundary suite, and an
independent public OAC conformance gate plus wheel/source build. The enterprise
suite requires PostgreSQL 17 and the complete sibling `oac-spec/` tree; the OAC
gate also needs Go 1.22. A maintainer starts the public release gates with
`workflow_dispatch`. On `main`, successful release gates can build a release
candidate, install both the wheel and source archive in fresh environments,
run the CLI/resource/public-quote smoke, and attach an SBOM and attestations.
These checks qualify the tested source and artifacts, not a customer deployment.
The retained `make archive-replay-check` needs historical evidence excluded
from this public source profile and is not a public release gate.

Open an issue with the problem, the smallest interface change, and the evidence that
will prove it. Reuse and integration feedback uses the Reuse template. See
[COMMUNITY.md](COMMUNITY.md). Security reports use [SECURITY.md](SECURITY.md), not a
public issue.

Dependabot is configured. Version-update PRs are paused on this snapshot until a
lockfile-complete review. Do not merge a pip PR unless `uv.lock` is updated in the same
change. Do not merge a GitHub Actions PR unless both `.github/workflows/` and
`orgrebase/.github/workflows/` stay identical.

## Contribution rights

Contributors retain their copyrights. Contributions intentionally submitted for
inclusion are offered under Apache-2.0, unless an independently signed and accepted
agreement explicitly governs those contributions. A future agreement does not
automatically change existing grants or transfer ownership. This repository imposes
no automatic copyright assignment. OAC's existing DCO sign-off requirement is
described in its [contribution guide](oac-spec/CONTRIBUTING.md); a DCO certifies
provenance and does not add a relicensing grant.
