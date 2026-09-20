from __future__ import annotations

import json
from contextlib import closing
from time import perf_counter

import psycopg
import pytest
from enterprise_pack_factory import make_enterprise_pack
from psycopg import sql
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import dialect

from orgrebase.clock import FrozenClock
from orgrebase.database import domain_events, workspace_changes
from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.domain import IntegrityError, ObjectState, VersionedObject
from orgrebase.event_projection import append_scope, scope_view
from orgrebase.store import StateStore
from orgrebase.store_schema_v2 import POSTGRES_SCHEMA_V2
from orgrebase.workspace.changes import ChangeRegistry
from orgrebase.workspace.models import ChangeEvent
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.service import WorkspaceService


def _proposal(service, event_id):
    binding = next(item for item in service.enterprise_binding.resources if item.slot_id == "product_plan")
    base = service.store.get_object(binding.object_id)
    payload = base.model_dump(mode="json", exclude={"digest"})
    payload.update(
        version="v2",
        state=ObjectState.PROPOSED,
        payload={**base.payload, "canonical_value": "Reviewed enterprise plan"},
        source_refs=("source:history-fixture@r1",),
    )
    return ChangeEvent(
        event_id=event_id,
        organization_id=service.profile.organization_id,
        slot_id=binding.slot_id,
        owner_id=binding.owner_id,
        base_version=base.version,
        base_digest=base.digest,
        proposal=VersionedObject.model_validate(payload),
        occurred_at="2026-09-09T00:00:00Z",
    )


def test_change_projection_is_transactional_and_pending_resolution_is_idempotent(tmp_path):
    runtime = load_enterprise_quote_pilot_pack(make_enterprise_pack(tmp_path))
    with closing(WorkspaceService(runtime_configuration=runtime, review_duration_seconds=0)) as service:
        event = _proposal(service, "transactional-change")
        before = service.store.history_projection()
        with pytest.raises(RuntimeError, match="rollback"), service.store.transaction() as connection:
            service.changes.register(event, connection=connection)
            assert service.changes.get(event.event_id) == event
            assert service.store.history_projection()["pending_change_count"] == 1
            raise RuntimeError("rollback")
        assert service.store.history_projection() == before
        with pytest.raises(KeyError):
            service.changes.get(event.event_id)
        service.changes.register(event)
        with service.store.transaction() as connection:
            service.changes.reject(event.event_id, event.owner_id, "Declined", connection=connection)
        service.changes.refresh()
        assert service.changes.count == 1 and service.changes.pending_count == 0
        with service.store.transaction() as connection:
            service.changes.reject(event.event_id, event.owner_id, "Declined", connection=connection)
        service.changes.refresh()
        assert service.changes.pending_count == 0
        assert service.store.audit_head()["sequence_no"] == before["events"] + 2


def test_event_subject_lookup_and_tail_validate_exact_boundaries():
    with StateStore() as store:
        for index in range(120):
            store.record_event(
                "OAC_READ" if index % 3 == 0 else "HISTORY", {"kind": f"subject-{index % 5}", "index": index}
            )
        exact = store.event_page(event_types=("HISTORY",), subject_key="subject-4", descending=True, limit=2)
        assert [item["payload"]["index"] for item in exact["items"]] == [119, 109]
        following = store.event_page(
            event_types=("HISTORY",),
            subject_key="subject-4",
            descending=True,
            limit=2,
            after=exact["next_cursor"],
        )
        assert [item["payload"]["index"] for item in following["items"]] == [104, 94]
        assert store.event_by_digest(exact["items"][0]["event_digest"]) == exact["items"][0]
        projection = store.history_projection(record_limit=10)
        assert len(projection["records"]) == 10 and projection["records"][0]["sequence_no"] == 111
        assert scope_view(
            projection["scope_projection"], events=120, head_digest=projection["head_digest"]
        ) == WorkspaceService._event_scopes({**store.verify_event_chain(), "records": store.event_records()})
        # Corruption outside the bounded tail is explicitly the full audit's responsibility.
        with store.transaction() as connection:
            connection.execute("UPDATE domain_events SET payload_json='{}' WHERE sequence_no=1")
        assert store.history_projection()["verification"] == "PERSISTED_APPEND_PROJECTION"
        with pytest.raises(IntegrityError, match="event chain failed"):
            store.verify_event_chain()
        with store.transaction() as connection:
            connection.execute("DELETE FROM domain_events WHERE sequence_no=120")
        with pytest.raises(IntegrityError, match="AUDIT_HEAD_MISMATCH"):
            store.history_projection()


def _copy_released_v2(source, dsn):
    from test_workspace_migrations import _ORDER

    with psycopg.connect(dsn) as target:
        for name in _ORDER:
            contract = POSTGRES_SCHEMA_V2[name]
            fields = [
                sql.SQL("{} {} {}").format(
                    sql.Identifier(column), sql.SQL(native), sql.SQL("NOT NULL" if nullable == "NO" else "")
                )
                for column, (native, nullable) in contract["columns"].items()
            ]
            fields.extend(sql.SQL(value) for value in contract["constraints"])
            target.execute(
                sql.SQL("CREATE TABLE {} ({})").format(sql.Identifier(name), sql.SQL(",").join(fields))
            )
            columns = tuple(contract["columns"])
            rows = source.connection.execute(
                "SELECT " + ",".join('"' + key + '"' for key in columns) + ' FROM "' + name + '"'
            ).fetchall()
            for row in rows:
                values = dict(zip(columns, row, strict=True))
                if name == "store_metadata":
                    values["schema_version"] = 2
                target.execute(
                    sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                        sql.Identifier(name),
                        sql.SQL(",").join(map(sql.Identifier, columns)),
                        sql.SQL(",").join(sql.Placeholder() for _ in columns),
                    ),
                    tuple(values.values()),
                )


@pytest.mark.parametrize("corrupt", [False, True])
def test_real_postgres_released_v2_replays_real_change_artifacts_without_rewriting(postgres_dsn, corrupt):
    from test_workspace_migrations import _original_rows

    with closing(WorkspaceService(review_duration_seconds=0)) as legacy:
        legacy.form_quote()
        preview = legacy.preview_change("currency")
        approval = legacy.approve_change(
            "currency", actor_id=legacy.change_owner["currency"], preview_digest=preview.preview.digest
        )
        legacy.apply_approved_change("currency", approval_digest=approval["approval_digest"])
        legacy.preview_change("launch_date")
        _copy_released_v2(legacy.store, postgres_dsn)
        with psycopg.connect(postgres_dsn, autocommit=True) as connection:
            connection.execute("UPDATE store_metadata SET tenant_id=%s", (legacy.profile.organization_id,))
            if corrupt:
                connection.execute(
                    "UPDATE artifacts SET payload_digest=%s WHERE artifact_id='workspace-change:currency@r1'",
                    ("sha256:" + "f" * 64,),
                )
            before = _original_rows(connection)
        if corrupt:
            with pytest.raises(IntegrityError, match="CHANGE_PROJECTION_ARTIFACT_BINDING_INVALID"):
                StateStore(postgres_dsn, tenant_id=legacy.profile.organization_id)
            with psycopg.connect(postgres_dsn, autocommit=True) as connection:
                assert _original_rows(connection) == before
                assert connection.execute("SELECT to_regclass('workspace_registry')").fetchone()[0] is None
            return
        with StateStore(postgres_dsn, tenant_id=legacy.profile.organization_id) as migrated:
            after = _original_rows(migrated.connection)
            assert {
                key: [tuple(row) for row in rows] for key, rows in after.items() if key != "store_metadata"
            } == {key: rows for key, rows in before.items() if key != "store_metadata"}
            assert migrated.history_projection() == legacy.store.history_projection()
            registry = ChangeRegistry(migrated, legacy.domain_pack, legacy.enterprise_binding)
            assert registry.count == 2 and registry.pending_ids() == ("launch_date",)
            with migrated.read_connection() as connection:
                rows = migrated.execute(
                    connection, select(workspace_changes).order_by(workspace_changes.c.ordinal)
                ).fetchall()
            assert next(row for row in rows if row["event_id"] == "currency")["resolution"] == "APPLIED"
            assert (
                next(row for row in rows if row["event_id"] == "currency")["approval_expires_at"] is not None
            )
            assert (
                next(row for row in rows if row["event_id"] == "launch_date")["preview_snapshot_digest"]
                == legacy.current_snapshot().digest
            )
            assert (
                registry.journal("WORKSPACE_CHANGE_APPROVED", subject_key="currency", limit=1)[0]
                == legacy.changes.journal("WORKSPACE_CHANGE_APPROVED")[0]
            )


def test_real_postgres_100k_service_cold_start_state_and_history_are_bounded(
    postgres_runtime, tmp_path, monkeypatch, record_property
):
    runtime = load_enterprise_quote_pilot_pack(make_enterprise_pack(tmp_path))
    database = postgres_runtime(tenant_id=runtime.profile.organization_id)
    kwargs = dict(
        store_path=database["runtime_dsn"],
        store_tenant_id=runtime.profile.organization_id,
        store_migrate=False,
        runtime_configuration=runtime,
        review_duration_seconds=0,
        clock=FrozenClock("2026-09-09T00:00:00Z"),
    )
    with closing(WorkspaceService(**kwargs)) as service:
        service.form_quote()
        started = perf_counter()
        for index in range(1000):
            event = _proposal(service, f"history-{index:04d}")
            with service.store.transaction() as connection:
                service.changes.register(event, connection=connection)
                service.changes.reject(
                    event.event_id, event.owner_id, "Historical owner rejection", connection=connection
                )
        registration_seconds = perf_counter() - started
        checkpoint = service.store.get_workspace("default")
        quote_digest = service.current_quote().digest
    # Bulk fixture loading preserves each immutable envelope and the same reducer.
    # The 1,000 change admissions/rejections above use the actual application write path.
    previous = checkpoint["audit_head"]
    scopes = json.loads(checkpoint["event_scopes_json"])
    with psycopg.connect(database["migration_dsn"]) as connection:
        with connection.cursor().copy(
            "COPY domain_events(workspace_id,sequence_no,event_type,payload_json,previous_digest,event_digest) FROM STDIN"
        ) as copy:
            for sequence in range(checkpoint["audit_sequence"] + 1, 100001):
                payload = {"fixture_index": sequence}
                envelope = {
                    "sequence_no": sequence,
                    "event_type": "HISTORY_OBSERVED",
                    "payload": payload,
                    "previous_digest": previous,
                }
                digest = sha256_digest(envelope)
                copy.write_row(
                    ("default", sequence, "HISTORY_OBSERVED", canonical_json(payload), previous, digest)
                )
                scopes = append_scope(scopes, {**envelope, "event_digest": digest})
                previous = digest
        connection.execute(
            "UPDATE workspace_registry SET audit_sequence=100000,audit_head=%s,event_scopes_json=%s WHERE workspace_id='default'",
            (previous, canonical_json(scopes)),
        )
        connection.execute("ANALYZE domain_events")
        connection.execute("ANALYZE workspace_changes")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("ordinary workspace query attempted full history replay")

    statements = []
    original = StateStore._execute

    def observe(self, connection, statement):
        statements.append(statement)
        return original(self, connection, statement)

    with monkeypatch.context() as patch:
        for name in ("event_records", "event_envelopes", "verify_event_chain", "list_artifacts"):
            patch.setattr(StateStore, name, forbidden)
        patch.setattr(ChangeRegistry, "all", forbidden)
        patch.setattr(ChangeRegistry, "pending_ids", forbidden)
        patch.setattr(StateStore, "_execute", observe)
        started = perf_counter()
        with closing(WorkspaceService(**kwargs)) as service:
            cold_seconds = perf_counter() - started
            started = perf_counter()
            state = service.state()
            state_seconds = perf_counter() - started
            assert state["quote"]["digest"] == quote_digest and state["business_complete"] is True
            assert state["change_history"]["total"] == 1000 and state["change_history"]["pending"] == 0
            assert len(state["change_events"]) == 50 and len(state["event_chain"]["records"]) == 100
            assert state["event_chain"]["events"] == 100000
            started = perf_counter()
            page = service.change_history(after=900, limit=50)
            page_seconds = perf_counter() - started
            assert len(page["items"]) == 50 and page["next_cursor"] == 950
            started = perf_counter()
            assert service.changes.get("history-0000").event_id == "history-0000"
            detail_seconds = perf_counter() - started
            assert service.state()["event_chain"]["head_digest"] == previous
            with service.store.read_connection() as connection:
                for table in (domain_events, workspace_changes):
                    statement = select(table).where(table.c.workspace_id == "default")
                    column = table.c.sequence_no if table is domain_events else table.c.ordinal
                    compiled = (
                        statement.where(column > 900)
                        .order_by(column)
                        .limit(51)
                        .compile(
                            dialect=dialect(),
                            compile_kwargs={"literal_binds": True},
                        )
                    )
                    plan = connection.execute(
                        "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + str(compiled)
                    ).fetchone()[0][0]
                    assert "Seq Scan" not in json.dumps(plan), plan
        # Both domain-event reads and change-page reads have SQL bounds or an exact key.
        for statement in statements:
            rendered = str(statement)
            if "FROM domain_events" in rendered or "FROM workspace_changes" in rendered:
                assert "LIMIT" in rendered or "event_digest =" in rendered, rendered
    with StateStore(
        database["runtime_dsn"], tenant_id=runtime.profile.organization_id, migrate=False
    ) as store:
        assert store.verify_event_chain() == {"status": "PASS", "events": 100000, "head_digest": previous}
    record_property(
        "service_history_scale",
        json.dumps(
            {
                "events": 100000,
                "registered_changes": 1000,
                "backend": "PostgreSQL",
                "runtime_role": "RLS_RESTRICTED",
                "cold_start_seconds": cold_seconds,
                "state_seconds": state_seconds,
                "history_page_50_seconds": page_seconds,
                "exact_detail_seconds": detail_seconds,
                "real_registration_seconds": registration_seconds,
                "event_tail_size": 100,
                "visible_change_rows_per_state": 50,
                "full_audit_separately_passed": True,
            },
            sort_keys=True,
        ),
    )


def test_real_postgres_change_projection_has_database_enforced_workspace_scope(postgres_runtime, tmp_path):
    runtime = load_enterprise_quote_pilot_pack(make_enterprise_pack(tmp_path))
    database = postgres_runtime(tenant_id=runtime.profile.organization_id)
    kwargs = dict(
        store_path=database["runtime_dsn"],
        store_tenant_id=runtime.profile.organization_id,
        store_migrate=False,
        runtime_configuration=runtime,
        review_duration_seconds=0,
    )
    with closing(WorkspaceService(**kwargs)) as first:
        first.store.register_workspace(
            "second",
            profile_digest=first.profile_digest,
            pack_digest=first.deployment_binding["pack_digest"],
            quote_object_id=first.quote_object_id,
            created_at="2026-09-09T00:00:00Z",
        )
        event = _proposal(first, "same-id")
        first.changes.register(event)
        with closing(WorkspaceService(**kwargs, workspace_id="second")) as second:
            second.changes.register(event)
            with second.store.transaction() as connection:
                second.changes.reject(
                    event.event_id, event.owner_id, "Second workspace only", connection=connection
                )
            first.changes.refresh()
            second.changes.refresh()
            assert first.changes.pending_count == 1 and second.changes.pending_count == 0
            rows = first.store.connection.execute(
                "SELECT workspace_id,event_id,resolution FROM workspace_changes"
            ).fetchall()
            assert [tuple(row) for row in rows] == [("default", "same-id", None)]
            assert (
                first.store.connection.execute(
                    "UPDATE workspace_changes SET resolution='REJECTED' WHERE workspace_id='second'"
                ).rowcount
                == 0
            )
            assert (
                first.store.connection.execute(
                    "DELETE FROM workspace_changes WHERE workspace_id='second'"
                ).rowcount
                == 0
            )
            assert second.changes.page()["items"] == (event,)
            assert (
                second.changes.journal("WORKSPACE_CHANGE_REJECTED", subject_key="same-id", limit=1)[0][
                    "payload"
                ]["reason"]
                == "Second workspace only"
            )
            assert first.changes.journal("WORKSPACE_CHANGE_REJECTED", subject_key="same-id", limit=1) == ()
        before = first.store.history_projection()
        with pytest.raises(RuntimeError, match="STATE_STORE_RESET_LOCAL_SESSION_ONLY"):
            first.reset()
        assert first.store.history_projection() == before


def test_local_reset_rechecks_authorization_inside_its_transaction():
    with StateStore() as store:
        store.record_event("PRESERVED", {})
        before = store.history_projection()

        def revoke():
            assert store.connection.in_transaction
            raise PermissionError("revoked")

        with pytest.raises(PermissionError, match="revoked"):
            store.reset_local_session(authorize=revoke)
        assert store.history_projection() == before
        store.reset_local_session()
        assert store.history_projection()["events"] == 0
