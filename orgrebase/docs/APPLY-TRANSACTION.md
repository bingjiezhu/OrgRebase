# Apply transaction and failure boundaries

`RebaseWorkflow.apply` in [`workflow.py`](../src/orgrebase/workflow.py) is the canonical state transition. A completed AgentTeams task, admitted candidate, or accepted review does not perform this transition. The following order describes the implementation, rather than a proposed decomposition into independent phases.

## Before the write transaction

The workflow verifies the minimal certificate and its exact preview binding, admitted deltas and stored proposals, approval scope and owner coverage, and revision freshness. It verifies advisory evidence through the configured advisory verifier (or the bound agent-run, ingestion, and compilation checks). It computes the request digest and checks the idempotency record. Repeating an already completed identical request returns its receipt; the idempotency key is not permission to submit a different request.

It classifies certified effects as rebuild, preserve, hold for review, or requalify; captures expected objects; and runs Skill qualification. These preparations cannot make an unapproved rule current. Qualification failure prevents entry into the write transaction when requalification is required. The first `_authorized_timestamp` call also validates authorization: its return value is replaced inside the transaction, but removing the call would remove a preflight check.

## Inside one `StateStore.transaction()`

1. Reauthorize the caller when the deployment supplies `authorize_commit`, revalidate approval time, and repeat the idempotency check. Recheck current source versions and the expected versions, digests, and states of affected objects. Drift rejects the operation.
2. Promote admitted source versions and prepare the required context manifests. Rebuild affected objects through their registered handlers, verify each payload, and insert successor versions. Mark unknown effects `REVIEW_REQUIRED`; activate qualified Skill candidates as `CANARY`. Preserve-within-boundary objects remain unchanged.
3. Run `_assert_content_oracle` against the resulting state. Build the receipt containing transitions, context, evidence bindings, and qualification results.
4. If configured, invoke `apply_extension.commit` with this same connection, then persist its receipt, the base receipt, the `REBASE_APPLIED` event, and the idempotency result.
5. Run the completion authorization callback and check approval time again. Only after the context manager's tenant and registered commit checks pass does the database commit. The receipt is returned after that boundary.

[`StateStore.transaction`](../src/orgrebase/store.py) rejects nested transactions and rolls back on exceptions, cancellation, or commit failure. A transaction error must not be presented as a successful state change merely because a receipt object was constructed in memory. PostgreSQL and SQLite use their respective database transaction mechanisms; this is not a distributed transaction across remote systems.

## External effects and retries

The canonical receipt proves this local state transition, not completion of a remote CRM write. Target delivery uses separately tracked effect intents, identity binding, read-back and reconciliation. An ambiguous remote result remains unresolved until reconciled; retrying apply is not a substitute for querying that remote effect. Approval and evidence must still match the exact change being applied.

## Backup operator diagnostic

`orgrebase database backup --output ...` requires a new directory. `BACKUP_OUTPUT_ALREADY_EXISTS` exits with status 2 and leaves the existing file, directory, or symbolic link untouched. Preserve that backup and select a new output directory; do not remove it merely to make a retry succeed. Other backup failures retain their own failure behavior and must be investigated separately.

## Concurrency and operational limits

A store instance serializes its transaction with its connection lock. The workflow currently retains the last context manifests on the instance for its in-transaction content check; this does not establish general-purpose thread safety outside that store transaction. Do not share or mutate one workflow across independent stores.

There is no automatic whole-transaction retry in `StateStore.transaction`. A caller handling a transient database conflict must retry the complete operation with the same request identity, rechecking current authorization and freshness. Do not retry a fragment after an external effect or treat a changed request as an identical retry. Connection pooling and distributed throughput are not established by this transaction contract.

Backup inventory opens workspace-scoped read connections so each can join the same exported PostgreSQL snapshot with its own workspace context. Restore creates an isolated database and proceeds through multiple database operations; a failed restore can leave that isolated database for inspection. It must not be automatically treated as a usable restore or silently dropped before investigation.
