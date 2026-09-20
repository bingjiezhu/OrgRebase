# Contributing

OAC welcomes narrowly scoped changes that preserve the contract/runtime boundary and include
interoperability evidence.

Follow the [code of conduct](CODE_OF_CONDUCT.md). Report vulnerabilities through
the [private security channel](SECURITY.md), not a public issue. The
[changelog](CHANGELOG.md) distinguishes source changes from published releases.

## Developer Certificate of Origin

Every commit must include a `Signed-off-by` line, created with `git commit -s`. By signing off, the
contributor certifies the [Developer Certificate of Origin 1.1](https://developercertificate.org/).

## Change classes

- Normative prose changes require a compatibility note and matching schemas/TCK updates when relevant.
- Schema changes require positive, negative, prior-version, and canonicalization fixtures.
- Reference implementation changes cannot silently redefine normative behavior.
- New public datasets require immutable identity, license, provenance, and Ground Truth gap disclosure.
- Model-generated candidate text or labels must be declared and cannot satisfy independent-review gates.

Run `make check` before proposing a change. Do not mix a normative semantic change, benchmark-label
change, and implementation optimization in one commit.
