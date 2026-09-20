# Security policy

## Supported version

The latest `main` revision is supported while OrgRebase is pre-1.0.

## Reporting

Do not publish vulnerabilities, credentials, restricted business data, or unredacted
traces in a public issue. Use GitHub's **Security → Report a vulnerability** (private
advisory) for this repository:
https://github.com/bingjiezhu/OrgRebase/security/advisories/new

Include the affected revision, threat model, minimal reproduction, and whether canonical
state, authorization, evidence integrity, or context isolation is affected. If that form
is unavailable, contact the maintainer through their GitHub profile.

Reports are handled on a best-effort basis. No guaranteed response or remediation
time is currently offered. Keep sensitive reproduction material in the private
reporting channel while a report is being assessed.

## Trust boundary

The current qualification is controlled-local; it is not customer production acceptance.
Ordinary `orgrebase serve`, `make serve`, and the ASGI factory default to production mode
and refuse startup without valid authentication configuration. Follow
[Authenticated deployment](docs/AUTHENTICATED-DEPLOYMENT.md) for identity, membership,
storage and browser-session settings. Application checks do not supply a TLS terminator,
secret manager or a customer's deployment controls.

Synthetic demonstrations explicitly use `orgrebase serve --local-demo` or `make serve-demo`.
The CLI restricts this unauthenticated mode to loopback; use it only on a trusted development
machine. A hostile process running as the same OS user is outside the local threat model
because it can modify the SQLite database, evidence files, Git repository or process memory.
Real deployments require separately protected workload identity, storage, ingress, secrets
and retention controls.

The local launchers use a private process umask, SQLite stores tighten their files to `0600`,
backup/restore copies use the same private-file guard, loopback HTTP rejects untrusted Host
headers, and the task-description response is marked `private, no-store`. These controls reduce
accidental local disclosure; the demo Actor header is not production authentication and the
stored task text must still contain synthetic or approved development data only.

The optional Vertex thought-signature bridge is also a controlled-local adapter. Its
process-local cache and ClusterIP service do not provide tenant isolation, workload identity,
cache TTL, or Kubernetes network policy. Do not place it in a shared cluster until those
controls and an authenticated caller boundary are designed and tested together.

The live AgentTeams collector accepts exported Matrix/provider metadata and queries the
current Kubernetes context. Run it in a dedicated evidence workspace with least-privilege,
read-only cluster credentials. Redact prompts, outputs, headers, tokens and source content;
retain only identities, event/request IDs, timestamps, models and content digests needed for
verification.

## Security invariants

- Agent output is candidate-only and cannot directly become canonical state.
- Apply and rollback approvals bind exact content digests and a fresh revision lock.
- Revision drift rejects before target writes; idempotency keys bind request digests.
- Restricted source content is excluded from Agent, model-call and telemetry payloads.
- Event chains, receipts and evidence manifests are content-addressed and fail on tampering.
- Public evidence finalization rejects symlinks, special files, root escapes, oversized files,
  private-input field names, and credential-shaped content before hashing the pack.
- The controlled OTLP store accepts structured operational facts only; raw `body`/`message`
  text and credential-shaped content fail before SQLite persistence.
- Unknown or incomplete coverage remains `UNKNOWN`; absence is never an authorization proof.
- No secret belongs in AgentTeams manifests, evidence bundles, logs, fixtures or Git history.
- The Vertex/Higress token refresh helper accepts only an explicit loopback HTTP endpoint and
  supplies credentials over file descriptors/stdin rather than process arguments.
- The Git tool accepts an allowlisted relative file only, rejects symlinks and multiple
  worktrees, disables hooks/signing, verifies committed blob bytes and uses a durable
  `PREPARED → EXTERNAL_COMMITTED → RECORDED` journal.
- Cross-system rollback never hides residual effects: failures persist `ROLLBACK_PARTIAL`
  with the exact outstanding Git commit and one idempotent recovery action.
- LIVE evidence requires fresh nonce/run binding across Kubernetes, Matrix, runtime Skill
  bytes, candidate artifacts and at least three distinct provider calls. Natural-language
  claims, screenshots, fixtures and file names cannot upgrade evidence class.

## Production hardening still required

- Configure and validate the actual IdP and auditable membership/owner assignments with the
  existing authorization boundary; verify approval and rollback signers and revocation in
  the customer's deployment.
- Qualify the chosen SQLite or PostgreSQL deployment's encryption, roles, backups, restore
  isolation and independently protected audit retention. Local database tests do not
  establish the customer's recovery or isolation guarantees.
- Sign release artifacts and evidence receipts with managed keys, publish provenance/SBOM,
  scan dependencies and container images, and pin runtime images by digest.
- Sandbox every tool execution with explicit egress and filesystem policy. The local Git
  adapter is intentionally narrow and must not become a general shell escape.
- Establish deletion, legal hold, incident response, key rotation and recovery testing before
  any real enterprise data is admitted.
