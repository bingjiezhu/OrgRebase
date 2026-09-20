# Security policy

OAC is an experimental contract, verifier and conformance-tooling project. A passing
certificate describes an exact verification scope; it is not permission to execute
an enterprise action or a claim of production security.

## Reporting a vulnerability

For the OAC source distributed with OrgRebase, use the maintainer's existing
[private security report channel](https://github.com/bingjiezhu/OrgRebase/security/advisories/new).
Identify the affected component as **OAC**, include the exact source revision and
package version, and provide a small synthetic reproduction. This reporting route
does not imply that OAC has its own published repository or release.

If the private form is unavailable, ask the maintainer for a private delivery
channel. Do not put credentials, private organizational data, unredacted traces or
an exploitable vulnerability in a public issue. Reports are handled on a best-effort
basis; no response or remediation deadline is promised.

## Scope

Report issues that allow invalid input or incomplete coverage to be accepted,
cross-namespace or digest substitution, unintended external effects, unsafe package
loading, or disclosure of source data. Include the expected rejection and observed
result, not just a positive fixture.

- Source inputs and provenance must remain distinct from generated candidates.
- `Unknown` must not become a positive assertion merely because evidence is absent.
- The verifier must remain independent from the compiler's preferred output.
- Compiler, verifier, TCK and benchmark do not grant external write authority.
- A certificate is valid only for its bound inputs and declared profile/version.

The current development revision is the maintenance target. Older experimental
formats are not an LTS promise. Consumers must pin the exact revision and run the
applicable compatibility checks before upgrading. For identity, database or
external-effect vulnerabilities in the product runtime, also follow
[OrgRebase's security policy](https://github.com/bingjiezhu/OrgRebase/blob/main/SECURITY.md).
