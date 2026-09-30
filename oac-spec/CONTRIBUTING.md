# Contributing

OAC welcomes narrowly scoped changes that preserve the contract/runtime boundary and include
interoperability evidence.

Follow the [code of conduct](CODE_OF_CONDUCT.md). Report vulnerabilities through
the [private security channel](SECURITY.md), not a public issue. The
[changelog](CHANGELOG.md) distinguishes source changes from published releases.

## License and copyright

Project-owned OAC material, including normative prose, is licensed under
[Apache-2.0](LICENSES/Apache-2.0.txt). See [LICENSE.md](LICENSE.md) for scope.

By intentionally submitting a contribution for inclusion, you confirm that you
have the right to submit it and offer it under Apache-2.0, as described in Section 5
of the license. You retain copyright. Contribution does not transfer ownership or
automatically accept a future contributor agreement.

A separate contribution agreement applies only after the contributor and the
project explicitly accept it. A CLA is not required.

## Developer Certificate of Origin

Every commit must include a `Signed-off-by` line, created with `git commit -s`. By signing off, the
contributor certifies the [Developer Certificate of Origin 1.1](https://developercertificate.org/).

## Change classes

- Normative prose changes require a compatibility note and matching schemas/TCK updates when relevant.
- Schema changes require positive, negative, prior-version, and canonicalization fixtures.
- Reference implementation changes cannot silently redefine normative behavior.
- New public datasets require immutable identity, license, provenance, and Ground Truth gap disclosure.
- Model-generated candidate text or labels must be declared and cannot satisfy independent-review gates.

From this public workspace, run `uv sync --locked --all-extras` in `oac-spec/`, then
`python3 -B ../orgrebase/scripts/build_source_snapshot.py public-check --snapshot-root ..`
before proposing a change. Retained `make check` and `make archive-replay-check` require
historical archive inputs outside the public source profile. Do not mix a normative
semantic change, benchmark-label change, and implementation optimization in one commit.
