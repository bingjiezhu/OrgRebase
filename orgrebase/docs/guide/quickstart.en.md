# Installation and quickstart

Verify installation, pricing and the approval path with one public historical transaction before adding a model.

## Obtain a matching version

This guide covers `0.5.0b4` Beta. Check the version in `pyproject.toml` before running; verify the SHA-256 when using a source download. The public workspace has sibling `orgrebase/` and `oac-spec/` directories. The product directory contains `pyproject.toml`, `uv.lock` and `scripts/`; this quickstart does not need OAC.

The package declares Python 3.12–3.14 support; this version's isolated first run was verified with 3.12.13. Install [uv](https://docs.astral.sh/uv/), then run from a matching repository revision:

```bash
git clone https://github.com/bingjiezhu/OrgRebase.git
cd OrgRebase/orgrebase
uv sync --locked
uv run --frozen python -c "import sqlite3; print(sqlite3.sqlite_version)"
uv run --frozen python scripts/run_public_quote_replay.py \
  --output ../quote-first-run --limit 1
```

For a source archive, enter the extracted directory containing `pyproject.toml` and
`scripts/` before `uv sync`. Initial dependency
installation needs network access or a complete cache. The offline-learning and developer
extras are not needed for this run.

The output directory must not exist. Open `../quote-first-run/index.html`. In `report.json`, check `status=PASS` and, inside `measurement_summary`, `planned_cases=1`, `outcomes.PASS=1`, `outcomes.FAILED=0` and `outcomes.INCOMPLETE=0`. The first default sample is invoice 560602; the [interactive pricing demo](demo.en.md) uses 557670 for a different purpose.

This command executes Formation, Preview, scripted-owner approval, Apply and an independent amount check. Candidates are local deterministic outputs. It needs no model, OAC, PostgreSQL or cloud credentials, and does not establish native AgentTeams execution or employee UAT.

<a id="sqlite-runtime"></a>

## SQLite runtime for local runs

The replay and local WebUI write file-backed SQLite databases. The selected Python interpreter must link **SQLite 3.51.3 or later**, **3.44.6 or later within the 3.44.x branch**, or **3.50.7 or later within the 3.50.x branch**. These versions include the upstream [WAL-reset fix](https://sqlite.org/wal.html#walreset). The command above reports Python's linked library; the standalone `sqlite3` command can use a different version.

`SQLITE_WAL_RUNTIME_UNSUPPORTED:<version>` means the linked library lacks an accepted fix. Install or rebuild a supported Python interpreter with patched SQLite, recreate the project environment using that interpreter, and check its linked version before rerunning. Installing only a newer SQLite CLI does not update Python's library. Preserve existing databases and their WAL files while changing the environment; see [storage and migration](../STATE-STORE-MIGRATIONS.md). Production deployments use PostgreSQL.

## Create a supported enterprise template

The public replay checks a historical basket. To prepare your own reviewable initial facts, initialize the separate priced Quote template from the product directory:

```bash
uv run --frozen orgrebase enterprise-pilot-init \
  --template priced-quote --output ../priced-draft
uv run --frozen orgrebase enterprise-pilot-draft-preflight --draft ../priced-draft
uv run --frozen orgrebase enterprise-pilot-seal \
  --draft ../priced-draft --output ../priced-pack
```

Each output path must be new. Replace the synthetic facts, owners and source references before an enterprise deployment. Sealing validates and packages exact inputs; it does not admit a contract, authorize a task or apply a change. The [model-free browser guide](demo.en.md#model-free-priced-workspace) adds the matching OAC prerequisites and explicit Quote + Discount Memo configuration.

## Next steps

- Contributor checks: `uv sync --locked --extra dev`, then `make check-core`.
- Cloud-model reference journey: [Vertex](models-vertex.en.md); [DeepSeek](models-deepseek.en.md) is an optional Reviewer protocol path without a verified live business closure.
- Supported configuration reuse: [Skills and enterprise Packs](skills.en.md).
- Customer identity and database setup: [Deployment](deployment.en.md).

If installation fails, retain the exit code and error. Check the Python interpreter, linked SQLite version, network access and lockfile first. An older HTML report cannot substitute for a failed fresh run.
