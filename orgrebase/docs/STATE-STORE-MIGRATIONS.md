# StateStore persistence and recovery

StateStore has one business implementation. SQLAlchemy Core builds parameterized statements for SQLite and PostgreSQL; each native driver executes its own dialect. There is no second PostgreSQL business workflow or SQL placeholder replacement layer.

PostgreSQL 17 is the deployment backend verified by the integration suite. SQLite remains suitable for local runs and isolated development. A different PostgreSQL major version must pass the same schema, transaction, and restore qualification before deployment.

Writable file-backed SQLite stores and runtime journals require the upstream WAL-reset fix: SQLite 3.51.3 or later, or the official backports in the 3.44 branch from 3.44.6 and the 3.50 branch from 3.50.7. Both entry points reject an unsupported engine with `SQLITE_WAL_RUNTIME_UNSUPPORTED:<version>` before creating a file, connecting, or migrating. Upgrade the SQLite library linked to the selected Python interpreter; installing a newer standalone SQLite CLI does not necessarily change Python's library. PostgreSQL, in-memory StateStore and explicit read-only historical inspection are unaffected. The protection addresses concurrent writers/checkpoints on one WAL file; it does not certify that historical data is uncorrupted. See [SQLite's WAL-reset advisory](https://sqlite.org/wal.html).

## Schema version 5

The eight original tables keep their existing business records and content addresses. SQLite migration accepts an empty database, the complete released unversioned schema, or version 1. It validates the previous definitions and foreign keys, creates the version 2 tables, and updates the version marker in one transaction. The version 1 DDL is unchanged.

Version 2 adds these responsibilities:

| Table | Responsibility |
| --- | --- |
| `store_metadata` | Persistent enterprise binding, schema version, and recovery isolation |
| `effect_intents` | Exact external request, outcome, lease owner, and monotonically increasing fence |
| `target_barriers` | One unresolved effect per target, independent of lease expiry |
| `source_checkpoints` | Opaque ingestion cursor, committed page revision, and fenced page claim |
| `private_records` | Expiring private content and deletion tombstones |

Version 3 scopes business tables by `(workspace_id, existing_key)` in the same enterprise database. The default workspace adopts every released version 0/1/2 business column without changing payload JSON, digests, event numbers, or predecessor links. A migration verifies the historical event chain and initializes its head and read projections in `workspace_registry`. The PostgreSQL version 2 descriptor is retained only as a migration compatibility contract; it does not execute a second business workflow.

`workspace_registry` contains immutable profile/domain-pack/Quote bindings and one audit head per workspace. PostgreSQL effects retain one global `effect_id` primary key and an origin workspace; target barriers retain a global target key. Browser login transactions, sessions, and revocation fences are tenant-wide and do not change with Quote selection. The effect ledger also holds requested action, command revision, and request time; there is no separate command queue.

PostgreSQL initializes the same business tables from Core definitions. Audit sequences, revisions, attempts, and fences use 64-bit integers. The schema validator checks exact column types and nullability, primary/foreign/unique constraints, parsed CHECK expressions, forced row security policies, and the exact registry binding guard. Unexpected triggers or views are rejected. Unexpected uniqueness constraints, future versions, and incompatible schemas fail closed. Additional non-unique indexes are permitted.

Schema validation preserves quoted literal values: changing `READY` to `ready` is a semantic schema change. PostgreSQL CHECK validation preserves operator grouping rather than comparing only the number of constraints.

Schema migrations do not repair arbitrary databases, rewrite old digests, or modify retained release/evidence databases during tests. Every later released schema change requires a new version and a tested transition.

Version 4 changes only the derived Dataverse target identity in `effect_intents.target_key` and `target_barriers.target_key`. HTTPS host case, the default port, equivalent IPv6 spelling, IDNA and one trailing DNS dot identify the same target. The original approved request JSON and digest, API URL, receipt ID, result JSON, timestamps, lease/fence and queued command values remain unchanged. All intents, including terminal records, receive the same projection rule. Generic/Git effects retain their previous target identity. No business event or audit head is rewritten or appended by this migration.

This normalization covers origin spelling, not arbitrary CNAMEs or different Dataverse environment domains. A deployment must bind one formally identified Dataverse environment; DNS or customer aliases do not independently prove environment equivalence.

Version 5 adds two non-unique indexes to the existing effect ledger. `effect_intents_pending_commands` covers `(workspace_id, requested_at, effect_id)` only for rows with a requested action, so the worker can read pending commands in order without scanning completed history. `effect_intents_workspace_state` covers `(workspace_id, state, effect_id)` for scoped state and recovery queries. The migration verifies both definitions before changing the version marker. A conflicting index with the same name fails the transaction; a version 5 runtime rejects missing or incompatible managed indexes. Other non-unique indexes remain permitted.

The version 4-to-5 transition changes no business rows, target identities, event bytes, audit heads, receipts or command values. Stop the old services, preserve a backup and current deletion ledger, then run `orgrebase database migrate --tenant org:example --runtime-role orgrebase_app` with operator credentials. Index creation occurs inside the existing migration transaction and can block concurrent writes, so use a maintenance window. Qualify the database before restarting applications and workers that require schema version 5. This transition does not clear existing recovery isolation or repeat the version 3 target remapping.

### Upgrade a version 2 or 3 effect database

1. Stop every old API, source worker and effect worker, and fence their outbound target access. A migration cannot retract an already sent request, and a version check in new code cannot constrain an old binary. Preserve a current backup and deletion ledger before changing the database.
2. Use operator credentials with PostgreSQL `SUPERUSER` or `BYPASSRLS` for this transition; the migration must see every workspace's effect and claim. Ordinary runtime roles cannot perform the migration. For a version 3 database, the explicit historical inspection options below preserve its old target projections and cannot write or upgrade it.
3. Run `orgrebase database migrate --tenant org:example --runtime-role orgrebase_app`. For an existing version 2/3 database containing any effect, this command commits `recovery_required=1` in a separate transaction before attempting the upgrade. A later validation failure cannot roll back that isolation. Empty databases can upgrade without this manual recovery gate. SQLite is local-only: persist the same recovery flag in a separate operator transaction before opening its nonempty old database with `StateStore(..., maintenance=True)`.
4. The transition rejects an unexpired effect or source lease, inconsistent Dataverse request/tenant/ID/digest, invalid old projection, an orphan or mismatched barrier, and `DISPATCHING`/`COMMIT_UNKNOWN` without a barrier. It checks all currently occupied barriers before changing any projection. Two occupied old aliases that converge on one target produce `STATE_STORE_EFFECT_TARGET_COLLISION`; both old barriers and all old requests remain intact at version 3. Multiple READY or historical terminal intents for one target are permitted when they do not occupy conflicting barriers.
5. After a rejected transition, keep the deployment stopped and isolated. Inspect each original effect's target receipt using the original request identity. Do not choose a barrier to discard, regenerate approval identities, or automatically replay a request. Resolve the conflicting history with independently verified target evidence before another migration attempt.
6. After a successful transition, qualify the current version 5 schema and reconcile unresolved target effects before the deployment's operator-controlled recovery procedure releases access. The version 4 target remapping is followed by the version 5 index migration. The migration and qualification commands do not clear recovery isolation. Restart only application and worker artifacts that require schema version 5.

Historical inspection is explicit:

```sh
orgrebase database backup --tenant org:example --read-schema-version 3 --output old-v3-backup
orgrebase database qualify --tenant org:example --read-schema-version 3
orgrebase database deletion-ledger --tenant org:example --read-schema-version 3 --output current-deletions.json
```

These commands use the configured database environment variable. The Python equivalent is `StateStore(..., maintenance=True, read_only=True, read_schema_version=3)`. Use `4` instead for a released version 4 database. Both selectors validate and report the actual historical schema and enterprise, and reject transactions. Normal runtime opening requires version 5. The recovery-inventory command accepts the same historical selector and validates old projected keys against the original request before making read-only target queries; it does not CANCEL, EXECUTE or release a barrier. Restoring a version 4 backup upgrades its indexes but reports `derived_target_identity_migrated=false`, because its target identities were already normalized.


## Enterprise isolation

The supported deployment model is one enterprise per database, with dedicated credentials. This is not shared-database multi-tenancy.

`StateStore(postgres_uri, tenant_id="org:example")` binds a new empty database to one enterprise. Reopening with another enterprise is rejected before business access. The instance checks the persistent binding and expected schema on reads, at the start of write transactions and immediately before COMMIT. Existing unbound business data cannot be assigned to an enterprise merely by opening it with a new identifier; it needs an explicit migration and evidence of ownership.

PostgreSQL requires an explicit enterprise identifier. Bound SQLite databases also require the same identifier on reopen. The HTTP deployment must derive this identifier from its configured enterprise and verified identity policy, never from an arbitrary request header or a user-selected connection string.

Application roles must not own the database or have schema-changing privileges in a production deployment. Migration and restore credentials belong to an operator role. The `postgres_runtime(tenant_id=...)` integration fixture creates an operator-migrated database and a distinct ordinary login role. Production tests exercise the ordinary role. `runtime_role_safe` rejects superuser/BYPASSRLS, schema creation privileges, database/table ownership, and membership permitting elevated roles.

Provision with `orgrebase database migrate --tenant org:example --runtime-role orgrebase_app` using operator credentials. Production opens use `StateStore(..., migrate=False)`; runtime schema validation never creates or upgrades tables. Registered WorkspaceService instances have separate connections and locks. Opening a workspace requires a server-owned registry/configuration binding; a client cannot select a connection string or invent an unregistered workspace.

PostgreSQL FORCE RLS restricts business rows and effects to the connection's bound workspace, with additional explicit Core predicates. The registry is globally readable and registerable within the enterprise database; updates affect only the bound workspace and its existing profile/pack/Quote binding cannot change. Target barriers are globally readable, can be acquired only for an owned effect with the same target, and can be deleted only for an owned terminal effect. An expired or UNKNOWN effect cannot release a barrier through ordinary SQL. Maintenance mode does not bypass PostgreSQL permissions or RLS.

The isolation boundary covers normal SQL on a correctly bound runtime connection. It does not defend against a database administrator or malicious code deliberately changing PostgreSQL session configuration. Current identity and server-owned workspace selection remain mandatory. SQLite accepts only `default`; multi-workspace production has one PostgreSQL path.

## Transactions and concurrent writers

All writes use `with store.transaction() as connection`. Cancellation, ordinary exceptions, and a failed COMMIT roll back the unit. A failed rollback closes the connection. Nested transactions and foreign store connections are rejected.

Within a write transaction, current-object reads lock the stable `current_pointers` rows first, in object-ID order for a set, then resolve their version keys in a fresh SQL statement. This avoids PostgreSQL READ COMMITTED rechecking a newly committed pointer against an older JOIN snapshot in which its new version is invisible. Ordinary reads and read-only operator sessions retain the single-statement view; competing updates still use the exact locked version key as their precondition. This is an object-row locking rule, not a deployment-wide lock or a retry that hides a missing object.

SQLite uses `BEGIN IMMEDIATE`, WAL, `synchronous=FULL`, foreign keys, and a 10-second busy timeout. These settings are local durability mechanisms, not cross-host availability.

PostgreSQL starts an ordinary transaction. It locks competing current pointers and applies a version precondition in the UPDATE. A transaction-scoped advisory lock serializes the same idempotency key, including its initially absent row. Audit append briefly locks only its workspace registry head row, reads the previous digest, and assigns the next contiguous sequence; a database sequence is intentionally not used because aborted transactions would leave gaps. Unrelated effect/source rows do not share a global writer lock.

Database locks must not span a model call, connector request, or target operation. CommitGateway persists dispatch state and commits before target I/O. Source synchronization claims a page, performs the GET outside its database transaction, and checks the claim again before publishing the inbox page.

`read_connection()` protects the instance's connection and enterprise binding. It does not promise a multi-statement repeatable-read snapshot. `count_records()` uses one statement. Outside a write transaction, `state_snapshot()` also uses one statement; inside a write transaction it locks the selected pointers before reading their version rows as described above.

## Effects and source pages

`effect_intents` is the live effect ledger used by CommitGateway. Claiming increments the fence; an expired lease does not alter an effect's business state. `DISPATCHING` and `COMMIT_UNKNOWN` require target reconciliation. Target barriers do not expire with workers, and only a confirmed or rejected effect can release its barrier.

Confirmation can update the effect, save its receipt and idempotency result, append an event, and release its target barrier inside one StateStore transaction. A fence prevents a superseded worker from finalizing current state. It does not make a remote system transactional: the target adapter still needs exact request identity, target preconditions, result lookup, and evidence for any final absence.

Source checkpoints preserve connector cursors as opaque strings. The fenced lease, previous cursor, and current time must match when a page is acknowledged. Admitting the page's records and advancing the cursor occur in the same transaction. An interrupted admission cannot skip a page. The immutable inbox is reused after restart; it is not a second authoritative source cursor.

The historical `external_operation_journal` table remains readable for retained evidence. New Git effects use CommitGateway. The separately retained semifinal RuntimeJournal is not the production scheduler or business authority.

## Bounded reads

`audit_head()` reads an indexed workspace row without replaying history. `event_page(after=..., limit=..., event_types=..., subject_key=..., descending=False)` pages by monotonic workspace event sequence, optionally filtering indexed event types and exact change/run subjects. `event_by_digest()` performs one indexed exact lookup. `artifact_page(artifact_id_prefix=..., after=..., limit=..., descending=False)` pages by stable artifact identifier in either direction. Descending pages use `< after`; ascending pages use `> after`. Limits are 1–500. Artifact prefixes treat `%` and `_` literally and preserve case. Artifact payloads are checked directly from each selected row; family reads do not issue a query for every artifact.

`workspace_changes` is a derived read index, maintained in the same transaction as the canonical event append. It binds each registration to its immutable artifact and stores registration order, base version/digest, validity, preview snapshot/expiry, approval expiry, and terminal disposition. It never approves a proposal or applies a business change. Historical migration backfills this index using the same reducer and refuses inconsistent artifact commitments; original event JSON, sequence numbers and digests remain unchanged.

`workspace_registry` carries constant-size event-scope anchors and change/pending counts. `history_projection()` reads the registry and up to 100 trailing events in one SQL snapshot. It verifies each returned envelope, consecutive tail links and the checkpoint binding; its `PERSISTED_APPEND_PROJECTION` marker explicitly excludes a fresh audit of rows outside that tail. Quote/OAC scope windows use the same formatter for full audit and projected views.

WorkspaceService cold startup reads the current checkpoint rather than replaying the journal. Ordinary state uses up to 50 visible changes by default (100 maximum, plus an active change if outside that window); details and approval/run evidence use exact indexed keys. Change-history cursors retain registration ordinals, so old cursors remain valid; database queries use `ordinal > after`, not OFFSET. The active-change query first excludes resolved, superseded, scheduled, expired and obsolete-preview records in SQL, then checks current approval authority on candidate pages of 50. Many still-current candidates whose authorities have just been revoked can require multiple pages: identity correctness is preserved rather than silently truncating this search. Full completion archives and evidence exports remain explicit full-history operations.

Pagination does not turn a partial page into proof of a complete audit history. `verify_event_chain()` and complete evidence export remain separate explicit operations. The full verifier reads the event rows and stored head in one database snapshot and rejects a valid-prefix truncation as well as changed events.

## Backup and isolated restore

The executable operator entry point is:

```sh
orgrebase database backup \
  --database-env ORGREBASE_WORKSPACE_DB \
  --tenant org:example \
  --output /private/backups/orgrebase/2026-09-09

orgrebase database qualify \
  --database-env ORGREBASE_WORKSPACE_DB \
  --tenant org:example

orgrebase database restore \
  --database-env ORGREBASE_RESTORE_ADMIN_DB \
  --tenant org:example \
  --backup /private/backups/orgrebase/2026-09-09 \
  --deletion-ledger /private/recovery/current-deletion-ledger.json
```

Connection strings are read from the named environment variables. Passwords are not placed in subprocess command arguments, reports, or backup manifests. Native `pg_dump` and `pg_restore` are required. Use an approved secrets provider for the environment or libpq password file; do not paste credentials into a shell history entry.

`orgrebase database deletion-ledger --tenant org:example --output current-deletion-ledger.json` exports a private current ledger for every registered workspace. Supply it from current authoritative deletion storage when restoring an older backup.

Backup creates a new private directory containing a custom-format dump, version 2 manifest, and scoped deletion ledger. The dump and every workspace manifest, chain, binding, and count are captured from the same exported PostgreSQL MVCC snapshot. Qualification explicitly visits every registered scope with the same snapshot, including effects and source checkpoints; it does not interpret the default workspace as the whole database. Files are private to the operator. Their hashes detect accidental substitution or corruption; the bundle still needs a trusted storage and provenance boundary. A hash inside a freely replaceable manifest is not an independent signature.

Restore never overwrites an existing database. It creates a new database, revokes `PUBLIC` access, restores with one native transaction, and persists `recovery_required=1`. It validates the restored version against the backup manifest and checks the original workspace chains/counts, effects, barriers and cursors before version 4 remaps any derived target identity. Released version 2 backups first pass through the same version 2-to-3 migration to expose a read-only inspection checkpoint; that step preserves their original target keys and event bytes. Version 3 backups are inspected directly in historical read-only mode. After remapping, workspace bindings, counts and audit heads must still match that original snapshot. Restore then invalidates every browser session and pending OIDC login transaction, retains revocation fences, reapplies the operator-supplied current deletion ledger and purges expired private content. Failed restores remain isolated for investigation; the operator must inspect and remove them explicitly.

Application instances refuse both reads and writes while recovery is required. Only an explicit maintenance instance can inspect the isolated database. `StateStore(read_only=True, maintenance=True)` validates the current schema and enterprise binding without migration, file creation, or tenant binding. SQLite opens with `mode=ro` and `query_only`; PostgreSQL sets the session's default transaction mode to read-only. The business write transaction API also rejects writes. Backup, qualification, and audit inspection use this boundary. `qualify` reports unresolved effects, target barriers, and source checkpoints; it never sends a target request or automatically releases application access.

The deletion ledger supplied at restore must be current, obtained from the authoritative deletion process. The ledger captured at backup is historical and may omit later erasure requests. An entry absent from an older backup creates a deletion tombstone, preventing later connector replay from recreating the deleted content. A differently bound record, malformed ledger, duplicate entry, or missing/extra workspace in a scoped ledger fails closed. A released unscoped ledger is accepted only for a default-only restore. Identity membership is reloaded from current deployment policy; no identity authority is recovered from the application database.

Before any operator enables a recovered deployment, independently establish:

1. Every target effect since the backup's horizon is accounted for, including effects whose local IDs were lost after that snapshot. An empty restored local ledger is not evidence that no remote effect occurred.
2. Unresolved effects and target barriers are reconciled using target evidence; no blind replay is permitted.
3. Deletion requests and current identity/authorization policy have been reapplied. Offline JWT validation alone does not prove immediate IdP revocation.
4. The application artifact and schema version match, and approved recovery objectives were measured under the actual deployment's failure model.

The current operator command deliberately does not clear recovery isolation. External target enumeration, production IdP revocation, managed backup/PITR, HA, and customer RPO/RTO acceptance require deployment evidence. The real local dump/restore drill is an executable prerequisite, not a substitute for those checks.

## Verification

The tests create their own PostgreSQL 17 cluster on a private Unix socket and unique databases. They do not start or stop an existing user service. The fixtures clean up their own databases and server. If native PostgreSQL tools are absent, local tests report a skip, which does not qualify a PostgreSQL release. CI sets `ORGREBASE_REQUIRE_POSTGRES_TESTS=1` so missing native tools fail the gate instead of silently skipping it.

```sh
uv run pytest -W error \
  tests/test_store_migrations.py \
  tests/test_store_transactions.py \
  tests/test_postgres_store.py \
  tests/test_store_operations.py \
  tests/test_workspace_migrations.py \
  tests/test_store_schema_v4.py \
  tests/test_workspace_store.py \
  tests/test_store_history_scale.py \
  tests/test_history_projection.py
```

Coverage includes unchanged legacy rows/digests, cancelled transactions, commit-time foreign-key failure, concurrent first open, competing pointer updates, same-key idempotency, concurrent audit append, independent target claims, stale fences, target barriers, atomic source pages, tenant mismatch, bounded reads, altered schema constraints, real dump/restore, post-backup deletion replay, recovery isolation, and refusal to overwrite or restore a corrupted backup.


The storage-only 100,000-event test measures storage open and a bounded page independently of service startup. It asserts that current-schema open never selects the event table, verifies an actual indexed PostgreSQL query plan, and separately replays all 100,000 valid event hashes. Local timing measurements are diagnostic evidence, not production latency or availability guarantees.

A separate service-level PostgreSQL test uses a restricted runtime role, 1,000 actual change registrations/rejections, and a total of 100,000 valid event envelopes. It measures cold WorkspaceService construction, state, a 50-change page, and an exact historical detail outside the visible window. Full-history methods are forbidden during ordinary reads, SQL reads must be bounded or exact, query plans are checked, and a separate full audit replays every event. Bulk generic history is a scale fixture, not evidence of 100,000 customer business processes. Tests also cover real version 2 preview/approval/outcome migration, corrupt artifact rollback, same-ID changes in two workspaces, and authorization failure inside a local reset transaction. Local reset uses the single StateStore implementation and is unavailable to PostgreSQL or tenant-bound stores.
