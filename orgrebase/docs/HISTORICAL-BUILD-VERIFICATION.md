# Artifact identity and historical verification

Current candidate validation and historical artifact verification have different inputs and purposes. Each result must identify the exact source, distribution and observations it checked.

## Current distributions

Build from the selected source and lockfile, record the wheel and source-distribution SHA-256 values, and install each in a new environment outside the checkout. Check that imports resolve to the installed package, metadata and API report the same version, required runtime assets are present, and the documented first run succeeds.

Use the [contributor checks](../CONTRIBUTING.md) and [release qualification gates](RELEASE-CANDIDATES.md) for the supported public source profile. A successful build, a local SBOM or an existing historical receipt does not establish GitHub provenance or customer acceptance.

## Historical artifacts

A historical receipt keeps the original artifact, source and dependency identities. Updating current `pyproject.toml` or `uv.lock` does not rewrite that evidence. A historical compatibility run cannot qualify a new distribution.

The retained closure verifier supports two explicit modes when a complete matching archive is supplied:

```sh
# Verify against current project and dependency bindings.
uv run --frozen python scripts/verify_semifinal_closure.py \
  --evidence /path/to/complete-pack

# Verify a historical artifact with its exact matching retained inputs.
uv run --frozen python scripts/verify_semifinal_closure.py \
  --evidence /path/to/complete-pack --retained-build \
  --retained-quote-value-inputs /path/to/matching-input-snapshot \
  --lock /path/to/matching-teamharness-lock.json
```

Run these commands from the product directory. The public source profile may omit the complete historical archives; missing files are not a successful verification. Historical mode verifies exact archived artifact bytes and explicit input bindings while reporting current-build drift. It does not repair or re-sign frozen records.

## Interpretation

A raw-file hash proves the captured bytes. Integrity, semantic correctness, execution observation, current authority and business acceptance require their own checks. Preserve failed, unknown and incomplete observations. The API's `release_context` distinguishes runtime version from retained release-facts version so that an older receipt cannot appear to certify the current service.
