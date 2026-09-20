# GitHub releases and documentation deployment

## Product source directory

The [OrgRebase product repository](https://github.com/bingjiezhu/OrgRebase) maintains source, contracts, tests, documentation and licenses. Its root contains `pyproject.toml`, `src/`, `docs/`, `documentation/` and `LICENSE`; the corresponding directory in a complete source distribution is named `orgrebase/`. Deployment configuration, customer credentials and runtime databases are managed separately.

OAC is an independent dependency. Previously: the GitHub product root did not include OAC; a complete source distribution could supply sibling `orgrebase/` and `oac-spec/` trees, and a product-only checkout needed a matching revision at `../oac-spec` or `ORGREBASE_OAC_ROOT`. Now: a workspace publication puts both trees at the clone root, so default `../oac-spec` works. Why: they ship together without nesting OAC inside the product package or changing its Apache-2.0 / CC BY 4.0 terms. Full CI on the product-root layout binds `OAC_REPOSITORY` and a 40-character `OAC_REVISION`; a workspace layout should run checks from `orgrebase/` and use the in-repo `oac-spec/`. Core CI does not require OAC.

## Before publishing

1. Confirm the target commit, licenses, third-party and model terms, data permission and credential exclusion.
2. Run applicable checks, build source and installable artifacts, verify checksums and reproduce the entry point from an extracted directory.
3. Prepare version notes, limitations and matching run identities; label controlled validation and production acceptance separately.
4. An authorized maintainer creates the tag and Release, then checks that artifacts, source revision, download access and notes agree.

## Build the bilingual site

Run from the product root:

```bash
uv sync --project documentation --locked --python 3.12.13
uv run --project documentation --frozen python documentation/build.py \
  --output /tmp/orgrebase-docs-new
python3 -m http.server 8018 --bind 127.0.0.1 --directory /tmp/orgrebase-docs-new
```

The output directory must be empty and outside the product source tree. MkDocs, Material and the i18n plugin use an independent lock. One build produces Chinese and English paths, with language switching that preserves the current page. Browse over HTTP to enable search.

To add a reviewed source download, supply both `--source-archive` and `--source-sha256`. The builder verifies the checksum and the bytes of documents, referenced source and site tooling. The download enters only generated site output. The distribution and site use the same source ZIP and checksum.

The GitHub Pages workflow builds a preview by default. An authorized maintainer must configure Pages and select deployment to publish the site. See the [site-maintenance guide](../DOCUMENTATION-SITE.md) for commands and scope.

## Versions and acceptance records

Release notes record the actual tag, commit, source and installable-artifact SHA-256 values, and acceptance results. The site's content digest covers only documents, referenced source and site tooling; it does not replace a complete product digest. Historical model receipts, current local checks and customer acceptance each have their own run identity. A download page must bind the source revision it actually provides.
