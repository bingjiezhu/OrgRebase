# Installation and quickstart

Verify installation, pricing and the approval path with one public historical transaction before adding a model.

## Obtain a matching version

Use the source revision matching this documentation. If the site provides a source download, verify its SHA-256. The product source directory contains `pyproject.toml`, `uv.lock` and `scripts/`. Previously, cloning the product alone did not supply OAC. A workspace GitHub publication includes sibling `orgrebase/` and `oac-spec/`. This quickstart does not need OAC.

Run from that product source directory with Python 3.12.13 and uv. Initial dependency installation needs network access or a complete cache.

```bash
uv sync --locked --all-extras
uv run --frozen python scripts/run_public_quote_replay.py \
  --output ../quote-first-run --limit 1
```

The output directory must not exist. Open `../quote-first-run/index.html`. In `report.json`, check `status=PASS` and, inside `measurement_summary`, `planned_cases=1`, `outcomes.PASS=1`, `outcomes.FAILED=0` and `outcomes.INCOMPLETE=0`. The first default sample is invoice 560602; the [interactive pricing demo](demo.en.md) uses 557670 for a different purpose.

This command executes Formation, Preview, scripted-owner approval, Apply and an independent amount check. Candidates are local deterministic outputs. It needs no model, OAC, PostgreSQL or cloud credentials, and does not establish native AgentTeams execution or employee UAT.

## Next steps

- Full cloud-model journey: [Vertex](models-vertex.en.md) or [DeepSeek](models-deepseek.en.md).
- Supported configuration reuse: [Skills and enterprise Packs](skills.en.md).
- Customer identity and database setup: [Deployment](deployment.en.md).

If installation fails, retain the exit code and error. Check Python, network access and the lockfile first. An older HTML report cannot substitute for a failed fresh run.
