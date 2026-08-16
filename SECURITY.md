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

## Trust boundary

This repository is a local reference implementation, not a production security
boundary. `make serve` binds to localhost and has no production authentication, tenancy,
rate limiting, TLS termination, or secret manager. Run it only on a trusted development
machine with synthetic data. A hostile process running as the same OS user is outside the
local threat model because it can modify the SQLite database, evidence files, Git repository,
or process memory. Production deployment requires a dedicated workload identity, isolated
storage, authenticated ingress, centralized authorization, secret injection and retention
controls.

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
- Unknown or incomplete coverage remains `UNKNOWN`; absence is never an authorization proof.
- No secret belongs in AgentTeams manifests, evidence bundles, logs, fixtures or Git history.
- The Git tool accepts an allowlisted relative file only, rejects symlinks and multiple
  worktrees, disables hooks/signing, verifies committed blob bytes and uses a durable
  `PREPARED → EXTERNAL_COMMITTED → RECORDED` journal.
- Cross-system rollback never hides residual effects: failures persist `ROLLBACK_PARTIAL`
  with the exact outstanding Git commit and one idempotent recovery action.
- LIVE evidence requires fresh nonce/run binding across Kubernetes, Matrix, runtime Skill
  bytes, candidate artifacts and at least three distinct provider calls. Natural-language
  claims, screenshots, fixtures and file names cannot upgrade evidence class.

## Production hardening still required

- Replace the local policy adapter with centrally managed authorization and auditable role
  assignment; authenticate every approval and rollback signer.
- Put SQLite/event storage behind transactional, backed-up infrastructure with encryption,
  tenant isolation and independently protected audit retention.
- Sign release artifacts and evidence receipts with managed keys, publish provenance/SBOM,
  scan dependencies and container images, and pin runtime images by digest.
- Sandbox every tool execution with explicit egress and filesystem policy. The local Git
  adapter is intentionally narrow and must not become a general shell escape.
- Establish deletion, legal hold, incident response, key rotation and recovery testing before
  any real enterprise data is admitted.
