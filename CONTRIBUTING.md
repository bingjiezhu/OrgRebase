# Contributing

This repository is a workspace: `orgrebase/` (product) and `oac-spec/` (contract).

Product changes: follow [`orgrebase/CONTRIBUTING.md`](orgrebase/CONTRIBUTING.md).

```bash
cd orgrebase
uv sync --locked --all-extras
make check-core
```

OAC changes: follow [`oac-spec/CONTRIBUTING.md`](oac-spec/CONTRIBUTING.md).

```bash
cd oac-spec
uv sync --locked --all-extras
```

Open an issue with the problem, the smallest interface change, and the evidence that
will prove it. Reuse and integration feedback uses the Reuse template. See
[COMMUNITY.md](COMMUNITY.md). Security reports use [SECURITY.md](SECURITY.md), not a
public issue.

Dependabot is configured. Version-update PRs are paused on this snapshot until a
lockfile-complete review. Do not merge a pip PR unless `uv.lock` is updated in the same
change. Do not merge a GitHub Actions PR unless both `.github/workflows/` and
`orgrebase/.github/workflows/` stay identical.
