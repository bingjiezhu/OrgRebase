# GitHub releases and documentation deployment

## Product source directory

The [OrgRebase repository](https://github.com/bingjiezhu/OrgRebase) is a workspace: `orgrebase/` contains the product and `oac-spec/` is an independent sibling contract project. One clone supplies both. Run `uv sync`, `make check-core` and the documentation build from `orgrebase/`. OAC is not embedded in the product Python package; deployment configuration, customer credentials and runtime databases stay outside source.

Protected source and runtime-resource changes trigger product core, public OAC conformance and PostgreSQL/OAC enterprise boundary checks. Historical internal archives are outside the public allowlist, so public CI does not treat retained `make check` or `make archive-replay-check` as qualification of new source. A maintainer starts the public release gate manually; build, isolated installation, SBOM and attestations must bind the same commit. A local Beta check is not a remote GitHub CI result.

## Before publishing

1. Confirm the target commit, licenses, third-party and model terms, data permission and credential exclusion.
2. Run applicable checks, build source and installable artifacts, verify checksums and reproduce the entry point from an extracted directory.
3. Prepare version notes, limitations and matching run identities; label controlled validation and production acceptance separately.
4. An authorized maintainer creates the tag and Release, then checks that artifacts, source revision, download access and notes agree.

## Build the bilingual site

Run from `orgrebase/` in a workspace clone:

```bash
uv sync --project documentation --locked --python 3.12.13
uv run --project documentation --frozen python documentation/build.py \
  --output /tmp/orgrebase-docs-new
python3 -m http.server 8018 --bind 127.0.0.1 --directory /tmp/orgrebase-docs-new
```

The output directory must be empty and outside the product source tree. MkDocs, Material and the i18n plugin use an independent lock. One build produces Chinese and English paths, with language switching that preserves the current page. Browse over HTTP to enable search.

To add a reviewed source download, supply both `--source-archive` and `--source-sha256`. The builder verifies the checksum and the bytes of documents, referenced source and site tooling. The download enters only generated site output. The distribution and site use the same source ZIP and checksum.

Pull requests build a read-only preview; documentation and site-input changes on `main` build and deploy [bingjiezhu.github.io/OrgRebase](https://bingjiezhu.github.io/OrgRebase/en/). A local build does not push or replace the live site. After merge, verify the live “Site source and downloads” page, licensing and language switching against the target revision. See the [site-maintenance guide](../DOCUMENTATION-SITE.md) for commands and scope.

## Versions and acceptance records

Release notes record the actual tag, commit, source and installable-artifact SHA-256 values, and acceptance results. The site's content digest covers only documents, referenced source and site tooling; it does not replace a complete product digest. Historical model receipts, current local checks and customer acceptance each have their own run identity. A download page must bind the source revision it actually provides.
