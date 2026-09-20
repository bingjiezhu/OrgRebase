from __future__ import annotations

import json
from time import perf_counter

import psycopg

from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.event_projection import append_scope, empty_scopes
from orgrebase.store import StateStore


def test_real_postgres_100k_history_cold_open_and_keyset_are_bounded(
    postgres_runtime, monkeypatch, record_property
):
    database = postgres_runtime(tenant_id="org:history-scale")

    def measure_open():
        started = perf_counter()
        with StateStore(database["runtime_dsn"], tenant_id="org:history-scale", migrate=False) as store:
            head = store.audit_head()
        return perf_counter() - started, head

    empty_seconds, _ = measure_open()
    previous = "sha256:" + "0" * 64
    scopes = empty_scopes()
    started = perf_counter()
    with psycopg.connect(database["migration_dsn"]) as writer:
        with writer.cursor().copy(
            "COPY domain_events(workspace_id,sequence_no,event_type,payload_json,previous_digest,event_digest) FROM STDIN"
        ) as copy:
            for sequence in range(1, 100001):
                kind = "HISTORY_SELECTED" if sequence % 1000 == 0 else "HISTORY"
                payload = {"sequence": sequence}
                digest = sha256_digest(
                    {
                        "sequence_no": sequence,
                        "event_type": kind,
                        "payload": payload,
                        "previous_digest": previous,
                    }
                )
                copy.write_row(("default", sequence, kind, canonical_json(payload), previous, digest))
                scopes = append_scope(
                    scopes,
                    {
                        "sequence_no": sequence,
                        "event_type": kind,
                        "previous_digest": previous,
                        "event_digest": digest,
                    },
                )
                previous = digest
        writer.execute(
            "UPDATE workspace_registry SET audit_sequence=100000,audit_head=%s,event_scopes_json=%s WHERE workspace_id='default'",
            (previous, canonical_json(scopes)),
        )
        writer.execute("ANALYZE domain_events")
    seed_seconds = perf_counter() - started
    statements = []
    original = StateStore._execute

    def observe(self, connection, statement):
        statements.append(str(statement))
        return original(self, connection, statement)

    monkeypatch.setattr(StateStore, "_execute", observe)
    cold_seconds, head = measure_open()
    assert head == {"sequence_no": 100000, "head_digest": previous}
    # Runtime startup validates metadata and the indexed head, never replays the audit table.
    assert not any("FROM domain_events" in statement for statement in statements)
    with StateStore(database["runtime_dsn"], tenant_id="org:history-scale", migrate=False) as store:
        started = perf_counter()
        page = store.event_page(after=99900, limit=50)
        page_seconds = perf_counter() - started
        assert len(page["items"]) == 50 and page["next_cursor"] == 99950
        assert page["items"][0]["sequence_no"] == 99901
        changes = store.event_page(after=97000, limit=2, event_types=("HISTORY_SELECTED",))
        assert [item["sequence_no"] for item in changes["items"]] == [98000, 99000]
        assert changes["next_cursor"] == 99000
        plan = store.connection.execute(
            "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) SELECT * FROM domain_events WHERE workspace_id='default' AND event_type='HISTORY_SELECTED' AND sequence_no>97000 ORDER BY sequence_no LIMIT 3"
        ).fetchone()[0][0]
        assert "Seq Scan" not in json.dumps(plan)
        assert "Index" in json.dumps(plan)
        assert store.verify_event_chain() == {"status": "PASS", "events": 100000, "head_digest": previous}
    record_property(
        "history_scale",
        json.dumps(
            {
                "backend": "PostgreSQL",
                "rows": 100000,
                "empty_open_seconds": empty_seconds,
                "cold_open_seconds": cold_seconds,
                "page_50_seconds": page_seconds,
                "seed_seconds": seed_seconds,
                "query_plan": plan,
            },
            sort_keys=True,
        ),
    )
