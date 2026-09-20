# Independently anchored audit checkpoints

A hash chain inside a writable database detects accidental corruption but cannot, by itself, distinguish an authorized history from a complete rewrite by a database administrator. `audit_checkpoint` adds an independently signed checkpoint of the existing event chain. It does not create a second event log.

## Signing and custody

New checkpoints use `orgrebase.audit-checkpoint.v2`, binding schema version, deployment ID, tenant ID, workspace ID, event sequence number, chain-head digest, capture time and the exact `orgrebase-python-json-v1` canonical serialization scheme. An Ed25519 signature covers a schema-specific, domain-separated encoding of that payload. The signing operation first verifies the captured chain; it refuses an unbound tenant. Tenant and workspace are taken from the selected database store, not from an unsigned checkpoint label.

An independent administrative process loads the private signing key. The API, database, source worker, candidate process and evidence payload must never receive that private key. Export the signed checkpoint and retain it outside the database operator's rewrite boundary. Pin the public key through an independently trusted deployment channel. A public key bundled alongside an untrusted backup is not its own trust anchor.

Real organizational key custody, rotation, retention and incident procedures remain deployment acceptance work. A generated test key proves the cryptographic implementation, not organizational separation of duties.

The operator command is `orgrebase audit sign --tenant <tenant> --workspace <workspace> --deployment <deployment> --private-key <private.pem> --output <new-checkpoint.json>`. It reads the database location from `ORGREBASE_WORKSPACE_DB` (or the name selected by `--database-env`), never a DSN command-line argument. Unlike the HTTP workspace command, this direct database command defaults to the historical `default` workspace when `--workspace` is omitted; use the explicit ID when signing each configured PostgreSQL workspace. Private keys must be regular, non-symlink PEM files owned by the current operator and unreadable by group/others. The command creates a new mode-0600 checkpoint, refuses overwrites, and reports its digest without key material. It uses a read-only database connection and refuses to sign a quarantined restore.

## Verification and rollback detection

`verify_database_checkpoint` verifies the signature against the supplied trusted public key, deployment and actual database tenant/workspace; it then replays the database event chain and compares the exact signed prefix. Invalid signatures, altered chain heads, mismatched tenants/deployments/workspaces and truncated history fail closed. Even identical event chains in two workspaces do not make their v2 checkpoints interchangeable.

Historical v1 payloads and signatures remain unchanged. Verification accepts them only for the historical `default` workspace and reports `workspace_binding: legacy-default-only`; v1 cannot supply a signed workspace identity. V2 reports `workspace_binding: signed`. Both report `workspace_id` and `checkpoint_schema_version`. Do not add a workspace field to an old signed payload, re-sign it as historical proof, or use v1 to qualify a non-default workspace. Capture a new v2 checkpoint of the selected current chain instead.

Supply the latest externally retained checkpoint digest (`expected_checkpoint_digest`) or a trusted minimum sequence number (`minimum_sequence`) when checking recovery. Without either freshness anchor, an older valid signed checkpoint can still verify: cryptography alone cannot tell a legitimate older checkpoint from a rollback. A minimum sequence prevents rollback below that sequence; an exact checkpoint digest pins the selected signed record.

The result reports `anchored_through`, `unanchored_events`, and whether the whole current chain matches the checkpoint. A valid prefix plus later events does not claim the later events were independently anchored. Create a new checkpoint on the deployment's schedule and after relevant operational milestones, and monitor anchoring lag.

Use `orgrebase audit verify --tenant <tenant> --workspace <workspace> --deployment <deployment> --public-key <trusted-public.pem> --checkpoint <checkpoint.json> --checkpoint-digest <externally-pinned-digest>`. An explicitly supplied nonnegative integer `--minimum-sequence` is an alternative sequence floor. The CLI requires one of those external freshness inputs. Verification may inspect a quarantined database through an operator-only read connection; it cannot migrate the schema, seed, bind a new tenant, release quarantine or write events.

The capture time is signer-observed UTC, not a trusted external timestamp-authority receipt. An offline checkpoint validates historical integrity; it does not grant current IAM permissions, approve a business change or prove an external target effect.

## Validation

`tests/test_audit_checkpoint.py` uses real Ed25519 signing and verification. It rejects wrong keys, scopes and anchors, detects truncation, and detects a rewritten but internally self-consistent database chain. It also reports an appended unanchored tail accurately, verifies an unchanged v1 signature, and exercises non-default workspace signing/verification through the CLI against temporary PostgreSQL with a normal RLS runtime role. These are controlled local checks. Restore qualification must compose them with current deletion-ledger replay, current authorization and external-effect reconciliation.
