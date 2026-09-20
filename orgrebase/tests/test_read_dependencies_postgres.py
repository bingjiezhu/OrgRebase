from __future__ import annotations

import io
import json
import time
from contextlib import closing
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient
from test_source_worker import RECORD, install_source, source_metadata

from orgrebase.api import create_app
from orgrebase.runtime_config import open_workspace
from orgrebase.workspace.source_worker import run_source_sync

pytest_plugins = ("test_source_worker",)


def headers(deployment, subject="operator"):
    return {"Authorization": "Bearer " + deployment.access_token(subject)}


def predicate(deployment):
    return {
        "connector_id": deployment.connector_id,
        "record_ids": [RECORD],
        "operator": "eq",
        "field": "new_launch_date",
        "value": "2031-01-01",
    }


@pytest.mark.parametrize("source_changes", [False, True])
def test_real_postgres_jwt_http_query_witness_guards_actual_apply(deployment, monkeypatch, source_changes):
    install_source(monkeypatch, deployment)
    assert run_source_sync(deployment.path)["status"] == "SYNCED"
    with TestClient(
        create_app(deployment_settings=deployment.settings), base_url="http://localhost"
    ) as client:
        captured = client.post(
            "/api/workspace/read-witnesses", json=predicate(deployment), headers=headers(deployment)
        )
        assert captured.status_code == 200, captured.text
        assert captured.json()["witness"]["result"] == "FALSE"
        assert captured.json()["witness"]["matched_record_ids"] == []
        with closing(open_workspace(deployment.settings)) as workspace:
            source = workspace.store.get_object("claim:product.enterprise_plan")
            before_quote = workspace.current_quote()
        submitted = client.post(
            "/api/workspace/change-proposals",
            headers=headers(deployment),
            json={
                "event_id": "query-dependent-plan",
                "slot_id": "product_plan",
                "base_version": source.version,
                "base_digest": source.digest,
                "value": "Reviewed enterprise plan",
                "source_ref": "source:owner-reviewed-plan@r1",
                "read_dependencies": captured.json()["read_dependencies"],
            },
        )
        assert submitted.status_code == 200, submitted.text
        assert (
            submitted.json()["event"]["proposal"]["payload"]["read_dependencies"]
            == captured.json()["read_dependencies"]
        )
        preview = client.post("/api/workspace/preview/query-dependent-plan", headers=headers(deployment))
        assert preview.status_code == 200, preview.text
        time.sleep(4.1)
        approved = client.post(
            "/api/workspace/approve/query-dependent-plan",
            headers=headers(deployment, "owner"),
            json={"preview_digest": preview.json()["preview_digest"]},
        )
        assert approved.status_code == 200, approved.text
        if not source_changes:
            applied = client.post(
                "/api/workspace/apply/query-dependent-plan",
                headers=headers(deployment),
                json={"approval_digest": approved.json()["approval_digest"]},
            )
            assert applied.status_code == 200, applied.text
            with closing(open_workspace(deployment.settings)) as workspace:
                assert workspace.current_quote().version != before_quote.version
                assert workspace.current_quote().payload["product_plan"] == "Reviewed enterprise plan"
                assert (
                    workspace.store.get_object(source.id).payload["read_dependencies"]
                    == captured.json()["read_dependencies"]
                )
            return
        endpoint = deployment.config["source"]["instance_url"] + "/api/data/v9.2/quotes"
        calls = []

        class NewMatch:
            def open(self, request, timeout):
                metadata = source_metadata(request.full_url)
                row = {"quoteid": RECORD, "@odata.etag": 'W/"opaque-b"', "new_launch_date": "2031-01-01"}
                calls.append(request.full_url)
                value = (
                    metadata
                    if metadata is not None
                    else row
                    if "/quotes(" in request.full_url
                    else {"value": [row], "@odata.deltaLink": endpoint + "?$deltatoken=watermark-2"}
                )
                response = io.BytesIO(json.dumps(value).encode())
                response.status = 200
                return response

        monkeypatch.setattr("orgrebase.workspace.dataverse.build_opener", lambda *args: NewMatch())
        assert run_source_sync(deployment.path)["status"] == "SYNCED"
        assert any("/quotes(" in url for url in calls)
        # New data is still only a proposal; the original Quote/source are unchanged.
        with closing(open_workspace(deployment.settings)) as workspace:
            before_apply_head = workspace.store.audit_head()
            assert workspace.current_quote().digest == before_quote.digest
        rejected = client.post(
            "/api/workspace/apply/query-dependent-plan",
            headers=headers(deployment),
            json={"approval_digest": approved.json()["approval_digest"]},
        )
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["detail"]["code"] == "READ_DEPENDENCY_CHANGED", rejected.text
        with closing(open_workspace(deployment.settings)) as workspace:
            assert workspace.current_quote().digest == before_quote.digest
            assert workspace.store.get_object(source.id).digest == source.digest
            assert workspace.store.audit_head() == before_apply_head
            assert workspace._outcome_record("query-dependent-plan") is None
        fresh = client.post(
            "/api/workspace/read-witnesses", json=predicate(deployment), headers=headers(deployment)
        )
        assert fresh.status_code == 200, fresh.text
        assert fresh.json()["witness"]["result"] == "TRUE"
        assert fresh.json()["witness"]["matched_record_ids"] == [RECORD]


def test_real_postgres_jwt_http_unfinished_page_cannot_issue_negative_witness(deployment, monkeypatch):
    install_source(monkeypatch, deployment)
    assert run_source_sync(deployment.path, max_pages=1)["status"] == "MORE_PAGES_PENDING"
    with TestClient(
        create_app(deployment_settings=deployment.settings), base_url="http://localhost"
    ) as client:
        response = client.post(
            "/api/workspace/read-witnesses", json=predicate(deployment), headers=headers(deployment)
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "READ_DEPENDENCY_UNKNOWN"
    with closing(open_workspace(deployment.settings)) as workspace:
        assert workspace.store.artifact_page(artifact_id_prefix="read-witness:")["items"] == ()


def test_real_postgres_jwt_http_permission_loss_cannot_issue_false(deployment, monkeypatch):
    install_source(monkeypatch, deployment)
    assert run_source_sync(deployment.path)["status"] == "SYNCED"

    class LostPermission:
        def open(self, request, timeout):
            raise HTTPError(request.full_url, 403, "denied", {}, io.BytesIO())

    monkeypatch.setattr("orgrebase.workspace.dataverse.build_opener", lambda *args: LostPermission())
    with pytest.raises(RuntimeError, match="SOURCE_PERMISSION_DENIED"):
        run_source_sync(deployment.path)
    with TestClient(
        create_app(deployment_settings=deployment.settings), base_url="http://localhost"
    ) as client:
        response = client.post(
            "/api/workspace/read-witnesses", json=predicate(deployment), headers=headers(deployment)
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "READ_DEPENDENCY_UNKNOWN"


def test_real_postgres_jwt_http_expired_observation_cannot_issue_negative_witness(deployment, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from orgrebase.clock import SystemClock, timestamp

    install_source(monkeypatch, deployment)
    assert run_source_sync(deployment.path)["status"] == "SYNCED"
    future = timestamp(datetime.now(UTC) + timedelta(seconds=901))
    monkeypatch.setattr(SystemClock, "now", lambda self: future)
    with TestClient(
        create_app(deployment_settings=deployment.settings), base_url="http://localhost"
    ) as client:
        response = client.post(
            "/api/workspace/read-witnesses", json=predicate(deployment), headers=headers(deployment)
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "READ_DEPENDENCY_UNKNOWN"


def test_real_postgres_commit_scope_lock_orders_source_invalidation(deployment, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from orgrebase.auth import JWTAuthenticator, request_principal
    from orgrebase.workspace.read_dependencies import (
        ReadDependencyError,
        ReadQuery,
        capture_read_witness,
        dependency_payload,
        validate_read_dependencies,
    )
    from orgrebase.workspace.source_bindings import load_source_config, mark_unavailable

    install_source(monkeypatch, deployment)
    assert run_source_sync(deployment.path)["status"] == "SYNCED"
    principal = JWTAuthenticator(deployment.settings.identity).authenticate(headers(deployment)["Authorization"])
    token = request_principal.set(principal)
    try:
        with (
            closing(open_workspace(deployment.settings)) as reader,
            closing(open_workspace(deployment.settings)) as writer,
        ):
            witness = capture_read_witness(reader, ReadQuery.model_validate(predicate(deployment)))
            original = reader.store.get_object("claim:product.enterprise_plan")
            premise = original.model_copy(update={"payload": {
                **original.payload, "read_dependencies": dependency_payload(witness),
            }})
            config = load_source_config(deployment.path)
            writer_pid = writer.store.connection.execute("SELECT pg_backend_pid()").fetchone()[0]
            reader_pid = reader.store.connection.execute("SELECT pg_backend_pid()").fetchone()[0]
            started = Event()

            def invalidate():
                started.set()
                mark_unavailable(writer, config, "SOURCE_PERMISSION_DENIED")

            with ThreadPoolExecutor(max_workers=1) as pool:
                with reader.store.transaction():
                    validate_read_dependencies(reader, (premise,), reader.clock.now())
                    future = pool.submit(invalidate)
                    assert started.wait(5)
                    deadline = time.monotonic() + 5
                    blockers = ()
                    while time.monotonic() < deadline:
                        blockers = reader.store.connection.execute(
                            "SELECT pg_blocking_pids(%s)", (writer_pid,)
                        ).fetchone()[0]
                        if reader_pid in blockers:
                            break
                        time.sleep(0.01)
                    assert reader_pid in blockers, "Source invalidation did not wait on the actual commit scope"
                    assert not future.done()
                    validate_read_dependencies(reader, (premise,), reader.clock.now())
                future.result(timeout=5)
            with reader.store.transaction(), pytest.raises(ReadDependencyError, match="READ_DEPENDENCY_UNKNOWN"):
                validate_read_dependencies(reader, (premise,), reader.clock.now())
    finally:
        request_principal.reset(token)
