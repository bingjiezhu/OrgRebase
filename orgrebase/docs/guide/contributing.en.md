# Contributing and security feedback

Prefer small changes with a clear problem and a verifiable boundary. Preserve the separation between candidates, admission, approval and canonical writes.

## Development and checks

```bash
uv sync --locked --all-extras
make check-core
```

`check-core` is a bounded contributor check. It needs no sibling OAC checkout, PostgreSQL or service credentials, and does not replace full integration qualification. Full `make check` also needs the admitted OAC source, PostgreSQL tools and the corresponding historical archives. A lightweight distribution may omit those archives; skips or earlier results cannot qualify a complete release.

Behavior changes should include failure cases and verification appropriate to their risk. For authorization or consistency fixes, preserve tests for wrong owners, stale digests, retries and unknown outcomes, and check that they still detect the corresponding errors.

## Documentation and translation

Core guides use matching `.zh.md`/`.en.md` files. Update both languages when commands, parameters, authority or licensing change. Detailed references keep an original-language label. Documentation tooling remains separate from business dependencies. See [building and publishing](publishing.en.md).

## Feedback channels

Use [project Issues](https://github.com/bingjiezhu/OrgRebase/issues) for ordinary problems, with an exact source version, execution mode, reproduction steps and redacted errors. Reuse questions and integration attempts use the Reuse template. Report sensitive vulnerabilities through the private channel in [SECURITY](../../SECURITY.md), not a public issue containing credentials or customer material.

The original [contribution terms](../../CONTRIBUTING.md), [community note](../COMMUNITY.md) and [code of conduct](../../CODE_OF_CONDUCT.md) apply. Having feedback channels does not establish third-party adoption, upstream contributions or a response SLA.
