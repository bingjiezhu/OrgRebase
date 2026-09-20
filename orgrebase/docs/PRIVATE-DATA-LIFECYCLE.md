# Private task-input lifecycle

Private work descriptions use `PrivateRecordStore` in the same tenant-bound StateStore and the same Formation transaction. They are separate from immutable business artifacts because operational deletion and immutable audit history have different retention requirements. There is one persistence path for new and migrated private text.

## Retention and access

`ORGREBASE_PRIVATE_RETENTION_SECONDS` defaults to 86400 seconds, accepts 0–604800, and rejects malformed or unbounded policies. Zero persists a digest-bound tombstone immediately and never writes the raw description. The retained record binds record ID, task/run scope, actor, payload digest, creation and expiry times.

Reads require the authenticated tenant and the exact task actor. Expired or deleted records return no raw data. Expiry is checked against the runtime Clock before decoding the payload. A retry with the same record ID and digest neither renews the retention window nor recreates deleted text; a different payload or actor/scope binding fails.

The service records only references, digests and deletion metadata in event history. It never appends the original private description to a deletion event. Digest commitments and task metadata remain potentially personal information: their retention and access must be covered by the organization's audit policy.

## Deletion and physical removal

`DELETE /api/workspace/task-intake/work-description` requires the record owner. The response reports whether data was removed; repeated calls are idempotent. The database update clears `content_json` only while the record has not already been deleted. Concurrent removals preserve the first deletion event and timestamp.

An administrator can call `POST /api/workspace/privacy/purge` to clear at most 1000 expired rows per call. Run this operation on a bounded schedule and monitor its result; read-time expiry alone does not physically remove the stored raw text. PostgreSQL WAL, replicas, backups and provider logs have their own storage lifetimes. This application-level deletion does not claim immediate byte erasure from those systems, encryption-key destruction, or control over model-provider retention.

### Run the expiry sweep

Retention is **not a built-in background scheduler**. Expiry immediately blocks application reads,
but raw `content_json` remains until an explicit delete, purge, or recovery-maintenance sweep.
The deployment owner must arrange and monitor the sweep for every configured workspace. A service
that starts successfully has not thereby demonstrated retention enforcement on stored bytes.

The following bounded maintenance client uses the existing administrator API. Save it outside the
source checkout as `purge-private-inputs.py`. Supply `ORGREBASE_ORIGIN` (HTTPS origin),
`ORGREBASE_WORKSPACE_ID` and a short-lived `ORGREBASE_ADMIN_TOKEN` through the deployment's
secret manager. Its subject must have the `administrator` role and access to the selected workspace.
Do not put tokens in a crontab, command-line arguments or source files.

```python
import os
from urllib.parse import urlsplit

import httpx2 as httpx

origin = os.environ["ORGREBASE_ORIGIN"].rstrip("/")
parsed = urlsplit(origin)
if (parsed.scheme != "https" or not parsed.netloc or parsed.path
        or parsed.query or parsed.fragment or parsed.username or parsed.password):
    raise SystemExit("PURGE_HTTPS_ORIGIN_REQUIRED")
headers = {
    "Authorization": "Bearer " + os.environ["ORGREBASE_ADMIN_TOKEN"],
    "X-OrgRebase-Workspace": os.environ["ORGREBASE_WORKSPACE_ID"],
}
total = 0
try:
    with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as client:
        for batch in range(10):
            response = client.post(origin + "/api/workspace/privacy/purge", headers=headers)
            if response.status_code != 200:
                raise SystemExit(f"PURGE_HTTP_{response.status_code}")
            value = response.json()
            if not isinstance(value, dict):
                raise SystemExit("PURGE_RESPONSE_INVALID")
            deleted = value.get("deleted")
            if type(deleted) is not int or not 0 <= deleted <= 1000 or value.get("limit") != 1000:
                raise SystemExit("PURGE_RESPONSE_INVALID")
            total += deleted
            print(f"purge_batch={batch + 1} deleted={deleted} total={total}")
            if deleted < 1000:
                break
        else:
            raise SystemExit("PURGE_BATCH_BUDGET_EXHAUSTED_CHECK_BACKLOG")
except (httpx.HTTPError, ValueError):
    raise SystemExit("PURGE_REQUEST_FAILED") from None
```

Run it with the locked product environment after the secret manager has injected the variables:

```bash
# Replace both paths; this does not configure a scheduler.
uv run --project /path/to/orgrebase --frozen python /path/to/private-ops/purge-private-inputs.py
```

For periodic operation, wrap that command in the organization's existing systemd timer, Kubernetes
CronJob or other scheduler. Choose cadence and retry budget from the approved retention policy;
prevent overlapping jobs for a workspace, refresh credentials before expiry, retain only counts and
exit status, and alert on nonzero exit or missed runs. Hitting ten full batches means more work may
remain, not success. A successful partial batch is an observation of that sweep, not a permanent
claim that no record will expire later. The application does not currently expose an oldest-expired-
record-age metric; do not report one as measured. Backups, replicas and WAL still require their own
retention controls below.

One-time migration moves the former private work-description artifact to this record table and removes only that exact artifact. It verifies the old media type and payload digest first. The old format lacks a reliable original creation timestamp; the migration records that limitation and starts the configured retention window at migration time. It does not reconstruct missing historical text or rewrite canonical business artifacts.

## Restoring a backup

Before restoring, preserve the **current** deletion ledger independently of the backup being restored. `GET /api/workspace/privacy/deletion-ledger` is administrator-only and returns binding/tombstone metadata without raw descriptions. Treat the ledger as private administrative state; an old ledger shipped inside an old backup cannot prove current deletion requirements.

The storage restore operation keeps the restored database in recovery quarantine. Its maintenance path replays the administrator-supplied current ledger with `reapply_deletions`, purges expired text, and checks restoration evidence before any release decision. A ledger entry must match the exact record/scope/owner/digest binding. Duplicate or malformed entries fail atomically. If the backup predates the original record, replay creates a tombstone so a later retry or inbox replay cannot resurrect the deleted description.

Replaying deletions is not permission recovery. Current identity membership, current authorization and external target state must also be revalidated. A backup's old membership or an old successful approval must not become the authority to resume writes. The production operator must retain the latest deletion ledger and follow the storage recovery runbook; this repository does not claim a tested customer backup service or organization-approved retention policy.

## Candidate-process exposure

Candidate subprocesses receive an allowlisted environment and a temporary HOME. Database credentials, target-write tokens, ambient cloud credentials, user Python imports and unrelated inherited variables are excluded; file descriptors are closed. Only a reviewer configured for Vertex can receive the explicit model-only Vertex token or API key. Ambient user gcloud credentials are not an implicit fallback in these subprocesses.

This limits credential exposure through environment inheritance. It is not an operating-system sandbox: arbitrary code running under the same OS identity can have other filesystem or network access. Production worker identities, mounted files and network policy must enforce the stronger isolation required by the deployment.

Tests cover expiry, zero retention, owner rejection, tamper detection, concurrent-safe idempotency, one-time migration, old-backup deletion replay, missing-record tombstones and actual child-process environment/file-descriptor inheritance. PostgreSQL restore and recovery quarantine have separate integration tests.
