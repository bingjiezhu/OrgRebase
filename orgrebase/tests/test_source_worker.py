from __future__ import annotations

import io
import json
import multiprocessing
import secrets
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event, Lock
from types import SimpleNamespace
from urllib.error import HTTPError

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from enterprise_pack_factory import make_enterprise_pack
from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.auth import (
    AuthenticationError,
    IdentitySettings,
    JWTAuthenticator,
    VerifiedJWKClient,
    request_authorization,
    request_principal,
)
from orgrebase.clock import timestamp
from orgrebase.domain import ObjectState
from orgrebase.private_records import PrivateRecordStore
from orgrebase.runtime_config import DeploymentSettings, open_workspace
from orgrebase.store import StateStore
from orgrebase.workspace.dataverse import (
    DataverseReader,
    DataverseSettings,
    SourceError,
    SourcePage,
    SourceRateLimited,
    SourceSynchronizer,
)
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.service import WorkspaceService
from orgrebase.workspace.source_worker import (
    SourceFieldMapping,
    WorkspaceSourceAdmission,
    _record_owned_source_failure,
    run_source_sync,
)

ORGANIZATION = "00000000-0000-0000-0000-000000000987"

pytest_plugins = ("test_postgres_store",)

RECORD = "00000000-0000-0000-0000-000000000001"
SECOND_RECORD = "00000000-0000-0000-0000-000000000002"


class _SharedSourceClock:
    """One controlled wall/monotonic time shared by independent OS processes."""

    def __init__(self, elapsed):
        self.elapsed = elapsed

    def monotonic(self):
        with self.elapsed.get_lock():
            return self.elapsed.value

    def now(self):
        return timestamp(datetime(2026, 9, 9, tzinfo=UTC) + timedelta(seconds=self.monotonic()))

    def advance(self, seconds):
        with self.elapsed.get_lock():
            self.elapsed.value += seconds


def _run_slow_source_process(
    store_path, pack_path, tenant_id, values, worker_id, elapsed,
    old_pending, old_release, new_claimed, new_release, results,
):
    """Spawn target: each worker opens its own service and database connection."""
    try:
        runtime = load_enterprise_quote_pilot_pack(pack_path)
        clock = _SharedSourceClock(elapsed)
        service = WorkspaceService(
            store_path=store_path, runtime_configuration=runtime,
            store_tenant_id=tenant_id, store_migrate=False, clock=clock,
        )
        try:
            source_settings = DataverseSettings(
                connector_id="source:combined-process-restart", tenant_id=tenant_id,
                instance_url="https://company.crm.dynamics.com",
                record_ids=(RECORD, SECOND_RECORD),
                fields=("new_launch_date", "new_currency"),
            )
            reader = DataverseReader(
                source_settings, lambda: "controlled-token", timeout=20,
                operation_timeout=180, max_reads=3, monotonic=clock.monotonic,
            )
            cursor = source_settings.endpoint + "?$deltatoken=complete"

            class Response:
                status = 200

                def __init__(self, payload, *, identity=None):
                    self.payload = json.dumps(payload).encode()
                    self.identity = identity
                    self.sent = False

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return None

                def read(self, _size):
                    if self.sent:
                        return b""
                    self.sent = True
                    if self.identity is not None:
                        if worker_id == "old" and self.identity == SECOND_RECORD:
                            old_pending.set()
                            if not old_release.wait(30):
                                raise AssertionError("old worker readback was not released")
                        clock.advance(16)
                    return self.payload

            class Opener:
                def open(self, request, timeout):
                    assert 0 < timeout <= 20
                    if "/quotes(" not in request.full_url:
                        if worker_id == "new":
                            new_claimed.set()
                            if not new_release.wait(30):
                                raise AssertionError("new worker fetch was not released")
                        return Response({"@odata.deltaLink": cursor, "value": []})
                    identity = request.full_url.split("quotes(", 1)[1].split(")", 1)[0]
                    return Response({
                        "quoteid": identity, "@odata.etag": 'W/"process-restart"',
                        "new_launch_date": values["launch_date"],
                        "new_currency": values["currency"],
                    }, identity=identity)

            reader.opener = Opener()
            receiver = WorkspaceSourceAdmission(service, source_settings, (
                SourceFieldMapping(record_id=RECORD, field="new_launch_date", slot_id="launch_date"),
                SourceFieldMapping(record_id=SECOND_RECORD, field="new_currency", slot_id="currency"),
            ))
            from orgrebase.workspace.source_bindings import SourceBindingConfig

            config = SourceBindingConfig.model_validate({
                "source": source_settings.model_dump(exclude={"fields"}),
                "organization_id": ORGANIZATION,
                "source_token_variable": "CONTROLLED_SOURCE_TOKEN",
                "access_token_variable": "CONTROLLED_ACCESS_TOKEN",
            })

            def verify(page):
                records = tuple(reader.read_record(identity) for identity in source_settings.record_ids)
                return SourcePage(records, page.cursor, False, {
                    "complete": True, "record_ids": list(source_settings.record_ids),
                    "method": "CURRENT_POINT_READ", "cross_source_atomic": False,
                })

            sync = SourceSynchronizer(
                service.store, reader, worker_id=worker_id, admit=receiver,
                admission_digest=receiver.admission_digest, clock=clock,
                verify_page=verify, lease_seconds=20, max_lease_renewals=10,
                page_failed=lambda connection, claim, error: _record_owned_source_failure(
                    service, config, receiver, connection, claim, error, now=clock.now,
                ),
            )
            result = sync.sync_page()
            results.put({"worker": worker_id, "status": "SYNCED", "result": result})
        finally:
            service.close()
    except BaseException as error:
        results.put({
            "worker": worker_id, "status": "ERROR", "error": str(error),
            "traceback": traceback.format_exc(),
        })


@pytest.fixture
def deployment(postgres_runtime, tmp_path, monkeypatch):
    pack = make_enterprise_pack(tmp_path)
    runtime = load_enterprise_quote_pilot_pack(pack)
    postgres_dsn = postgres_runtime(tenant_id=runtime.profile.organization_id)["runtime_dsn"]
    membership = tmp_path / "members.json"
    members = {
        "tenant_id": runtime.profile.organization_id,
        "members": [
            {"subject": "operator", "actor_id": runtime.profile.default_task.actor_id,
             "roles": ["administrator"]},
            {"subject": "replacement", "actor_id": "worker:replacement", "roles": ["administrator"]},
        ],
    }
    owner = next(item.owner_id for item in runtime.enterprise_binding.resources if item.slot_id == "launch_date")
    members["members"].append({"subject": "owner", "actor_id": owner, "roles": ["approver"]})
    membership.write_text(json.dumps(members))
    identity = IdentitySettings("https://issuer.example", "orgrebase-api", "https://issuer.example/jwks",
                                runtime.profile.organization_id, membership)
    settings = DeploymentSettings(mode="production", identity=identity, database_url=postgres_dsn,
                                  enterprise_pack=str(pack), allowed_hosts=("localhost",))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="worker-key", alg="RS256", use="sig")
    monkeypatch.setattr(VerifiedJWKClient, "fetch_data", lambda self: {"keys": [jwk]})
    monkeypatch.setattr(DeploymentSettings, "from_environment", classmethod(lambda cls: settings))
    monkeypatch.setenv("ORGREBASE_WORKSPACE_TASK_INTAKE_REQUIRED", "0")

    def access_token(subject="operator"):
        now = int(time.time())
        return jwt.encode({"iss": identity.issuer, "aud": identity.audience, "sub": subject,
                           "iat": now, "exp": now + 300, "token_use": "access"},
                          key, algorithm="RS256", headers={"kid": "worker-key"})

    monkeypatch.setenv("TEST_WORKER_ACCESS", access_token())
    source_token = secrets.token_urlsafe(32)
    monkeypatch.setenv("TEST_SOURCE_ACCESS", source_token)
    with TestClient(create_app(deployment_settings=settings), base_url="http://localhost") as client:
        response = client.post("/api/workspace/form", headers={"Authorization": "Bearer " + access_token()})
        assert response.status_code == 200, response.text
    config = {
        "source": {"connector_id": "source:worker", "tenant_id": identity.tenant_id,
                   "instance_url": "https://company.crm.dynamics.com", "record_ids": [RECORD],
                   "entity_set": "quotes"},
        "organization_id": ORGANIZATION,
        "source_token_variable": "TEST_SOURCE_ACCESS", "access_token_variable": "TEST_WORKER_ACCESS",
    }
    path = tmp_path / "source.json"
    path.write_text(json.dumps(config))
    settings = replace(settings, source_config=path)
    deployment = SimpleNamespace(settings=settings, config=config, path=path, membership=membership,
                           members=members, access_token=access_token, source_token=source_token)
    install_source(monkeypatch, deployment)
    discovery = run_source_sync(path, discover=True)
    with TestClient(create_app(deployment_settings=settings), base_url="http://localhost") as client:
        proposal = client.post("/api/workspace/source-binding/proposals", headers={"Authorization": "Bearer " + access_token()},
            json={"inventory_digest": discovery["inventory_digest"], "generation_digest": discovery["generation_digest"],
                  "mappings": [{"record_id": RECORD, "field": "new_launch_date", "slot_id": "launch_date"}]} )
        assert proposal.status_code == 200, proposal.text
        digest = proposal.json()["proposal"]["proposal_digest"]
        confirmed = client.post("/api/workspace/source-binding/proposals/" + digest[7:] + "/confirm",
            headers={"Authorization": "Bearer " + access_token("owner")}, json={"proposal_digest": digest})
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["status"] == "CONFIRMED", confirmed.text
    deployment.connector_id = config["source"]["connector_id"] + ":" + digest[7:39]
    return deployment



def source_metadata(url):
    if url.endswith("/WhoAmI"):
        return {"OrganizationId": ORGANIZATION}
    if "/EntityDefinitions" not in url:
        return None
    if "/Attributes" not in url:
        return {"LogicalName": "quote", "MetadataId": ORGANIZATION, "EntitySetName": "quotes",
                "PrimaryIdAttribute": "quoteid", "TableType": "Standard", "DataProviderId": None,
                "DataSourceId": None, "ChangeTrackingEnabled": True}
    return {"value": [{"LogicalName": "new_launch_date", "AttributeType": "String", "IsValidForRead": True,
                      "DisplayName": {"UserLocalizedLabel": {"Label": "Launch date"}}}]}

def install_source(monkeypatch, deployment, *, after_fetch=lambda: None):
    requests = []
    endpoint = deployment.config["source"]["instance_url"] + "/api/data/v9.2/quotes"

    class SourceTransport:
        def open(self, request, timeout):
            metadata = source_metadata(request.full_url)
            if metadata is not None:
                response = io.BytesIO(json.dumps(metadata).encode())
                response.status = 200
                return response
            requests.append(request.full_url)
            assert request.get_header("Authorization") == "Bearer " + deployment.source_token
            assert request.get_method() == "GET"
            assert 0 < timeout <= 30
            after_fetch()
            value = {
                "@odata.nextLink" if len(requests) == 1 else "@odata.deltaLink":
                    endpoint + ("?$skiptoken=page-2" if len(requests) == 1 else "?$deltatoken=watermark-1"),
                "value": [{"quoteid": RECORD, "@odata.etag": 'W/"opaque-a"',
                           "new_launch_date": "2030-01-01"}] if len(requests) == 1 else [],
            }
            if "/quotes(" in request.full_url:
                value = {"quoteid": RECORD, "@odata.etag": 'W/"opaque-a"', "new_launch_date": "2030-01-01"}
            response = io.BytesIO(json.dumps(value).encode())
            response.status = 200
            return response

    monkeypatch.setattr("orgrebase.workspace.dataverse.build_opener", lambda *args: SourceTransport())
    return requests


def test_worker_restarts_from_committed_cursor_without_replacing_canonical_facts(deployment, monkeypatch):
    requests = install_source(monkeypatch, deployment)
    before_principal = request_principal.get()
    before_authorization = request_authorization.get()
    first = run_source_sync(deployment.path, max_pages=1)
    assert first["status"] == "MORE_PAGES_PENDING"
    assert first["pages"][0]["records_admitted"] == 1
    assert first["external_writes"] == 0
    assert request_principal.get() is before_principal
    assert request_authorization.get() is before_authorization
    second = run_source_sync(deployment.path, max_pages=2)
    assert second["status"] == "SYNCED"
    assert len(requests) == 3 and requests[1].endswith("?$skiptoken=page-2")
    assert "/quotes(" in requests[2]
    service = open_workspace(deployment.settings)
    try:
        events = service.changes.all()
        assert len(events) == 1
        event = events[0]
        assert event.slot_id == "launch_date" and event.proposal.payload["canonical_value"] == "2030-01-01"
        current = service.store.get_object(event.proposal.id)
        assert current.digest == event.base_digest and current.version == event.base_version
        assert current.payload["canonical_value"] != "2030-01-01"
        assert service.store.get_source_checkpoint(deployment.connector_id)["cursor"].endswith("?$deltatoken=watermark-1")
    finally:
        service.close()


def test_late_worker_failure_preserves_replacement_coverage_and_canonical_state(deployment, monkeypatch):
    """Exercise the public worker wrapper around a real concurrent lease takeover."""
    from orgrebase.workspace.source_bindings import load_source_config, source_coverage

    class Clock:
        elapsed = 0.0
        started = datetime.now(UTC)

        def now(self):
            return timestamp(self.started + timedelta(seconds=self.elapsed))

        def monotonic(self):
            return self.elapsed

    clock = Clock()
    original_init = SourceSynchronizer.__init__

    def initialize(self, *args, **kwargs):
        kwargs["clock"] = clock
        original_init(self, *args, **kwargs)
        self.reader.monotonic = clock.monotonic

    monkeypatch.setattr(SourceSynchronizer, "__init__", initialize)
    old_pending, old_release = Event(), Event()
    request_lock = Lock()
    first_request = True

    def pause_old_fetch():
        nonlocal first_request
        with request_lock:
            pause = first_request
            first_request = False
        if pause:
            old_pending.set()
            assert old_release.wait(15), "old worker was not released"

    install_source(monkeypatch, deployment, after_fetch=pause_old_fetch)
    with ThreadPoolExecutor(max_workers=1) as executor:
        old = executor.submit(run_source_sync, deployment.path)
        try:
            assert old_pending.wait(15), "old worker did not claim and fetch a page"
            inspected = open_workspace(deployment.settings)
            try:
                old_claim = inspected.store.get_source_checkpoint(deployment.connector_id)
                assert old_claim["fence"] == 1 and old_claim["revision"] == 0
            finally:
                inspected.close()
            clock.elapsed = 121.0
            replacement = run_source_sync(deployment.path)
            assert replacement["status"] == "SYNCED"
            assert replacement["coverage"]["status"] == "COMPLETE"
            inspected = open_workspace(deployment.settings)
            try:
                winner = inspected.store.get_source_checkpoint(deployment.connector_id)
                source = inspected.store.get_object("claim:product.launch_date")
                quote = inspected.current_quote()
                audit = inspected.store.audit_head()
                assert winner["fence"] == old_claim["fence"] + 1
                assert winner["revision"] > old_claim["revision"]
            finally:
                inspected.close()
        finally:
            old_release.set()
        with pytest.raises(SourceError, match="SOURCE_STALE_CLAIM"):
            old.result(timeout=15)

    inspected = open_workspace(deployment.settings)
    principal = request_principal.set(
        JWTAuthenticator(deployment.settings.identity).authenticate("Bearer " + deployment.access_token())
    )
    try:
        assert inspected.store.get_source_checkpoint(deployment.connector_id) == winner
        assert inspected.store.get_object("claim:product.launch_date") == source
        assert inspected.current_quote() == quote
        assert inspected.store.audit_head() == audit
        assert source_coverage(inspected, load_source_config(deployment.path))["status"] == "COMPLETE"
    finally:
        request_principal.reset(principal)
        inspected.close()


def _exercise_slow_source_process_replacement(store_path, pack_path):
    runtime = load_enterprise_quote_pilot_pack(pack_path)
    tenant_id = runtime.profile.organization_id
    service = WorkspaceService(
        store_path=store_path, runtime_configuration=runtime, store_tenant_id=tenant_id,
        store_migrate=not str(store_path).startswith("postgresql://"),
    )
    try:
        service.form_quote()
        values = {
            "launch_date": "2030-01-01",
            "currency": service.store.get_object("policy:finance.currency").payload["canonical_value"],
        }
        assert service.store.get_object("claim:product.launch_date").payload["canonical_value"] != values["launch_date"]
        assert service.changes.all() == ()
    finally:
        service.close()

    context = multiprocessing.get_context("spawn")
    elapsed = context.Value("d", 0.0)
    old_pending, old_release = context.Event(), context.Event()
    new_claimed, new_release = context.Event(), context.Event()
    results = context.Queue()
    args = (str(store_path), str(pack_path), tenant_id, values)
    controls = (elapsed, old_pending, old_release, new_claimed, new_release, results)
    old = context.Process(
        target=_run_slow_source_process, args=(*args, "old", *controls),
        name="source-old-worker",
    )
    new = context.Process(
        target=_run_slow_source_process, args=(*args, "new", *controls),
        name="source-replacement-worker",
    )
    old.start()
    try:
        assert old_pending.wait(30), "old worker did not reach its second slow readback"
        with StateStore(store_path, tenant_id=tenant_id, migrate=False) as inspected:
            first_claim = inspected.get_source_checkpoint("source:combined-process-restart")
            assert first_claim["lease_owner"] == "old"
            assert first_claim["fence"] == 1 and first_claim["revision"] == 0
            assert first_claim["cursor"] is None
        # The first readback took 16 virtual seconds. The second is in flight;
        # moving past its bounded lease makes the old process a late return.
        with elapsed.get_lock():
            assert elapsed.value == 16
            elapsed.value = 40
        new.start()
        assert new_claimed.wait(30), "replacement did not acquire and start the source page"
        with StateStore(store_path, tenant_id=tenant_id, migrate=False) as inspected:
            replacement_claim = inspected.get_source_checkpoint("source:combined-process-restart")
            assert replacement_claim["lease_owner"] == "new"
            assert replacement_claim["fence"] == first_claim["fence"] + 1
            assert replacement_claim["revision"] == 0 and replacement_claim["cursor"] is None

        old_release.set()
        stale = results.get(timeout=30)
        assert stale["worker"] == "old", stale
        assert stale["status"] == "ERROR" and stale["error"] == "SOURCE_STALE_CLAIM", stale
        old.join(timeout=10)
        assert old.exitcode == 0
        with StateStore(store_path, tenant_id=tenant_id, migrate=False) as inspected:
            still_owned = inspected.get_source_checkpoint("source:combined-process-restart")
            assert still_owned["lease_owner"] == "new" and still_owned["fence"] == replacement_claim["fence"]
            assert still_owned["revision"] == 0 and still_owned["cursor"] is None
            assert inspected.list_artifacts(artifact_id_prefix="source-observation:") == ()
            assert inspected.get_object("claim:product.launch_date").state == ObjectState.CURRENT
            assert inspected.get_object("policy:finance.currency").state == ObjectState.CURRENT
            assert not any(event["event_type"] == "SOURCE_BINDING_UNAVAILABLE"
                           for event in inspected.event_envelopes())

        new_release.set()
        completed = results.get(timeout=30)
        assert completed["worker"] == "new" and completed["status"] == "SYNCED", completed
        new.join(timeout=10)
        assert new.exitcode == 0
        assert completed["result"]["records_admitted"] == 2
        assert completed["result"]["revision"] == 1

        # Fresh connection: inspect durable inbox, business admission and cursor
        # without relying on either process's in-memory registry or counters.
        checked = WorkspaceService(
            store_path=store_path, runtime_configuration=runtime, store_tenant_id=tenant_id,
            store_migrate=False,
        )
        try:
            checkpoint = checked.store.get_source_checkpoint("source:combined-process-restart")
            assert checkpoint["revision"] == 1
            assert checkpoint["fence"] == replacement_claim["fence"]
            assert checkpoint["cursor"].endswith("?$deltatoken=complete")
            assert checkpoint["lease_owner"] is None and checkpoint["lease_until"] is None
            inbox = PrivateRecordStore(checked.store, _SharedSourceClock(elapsed)).read(
                completed["result"]["page_ref"]
            )
            assert inbox["readback"] == {
                "complete": True, "record_ids": [RECORD, SECOND_RECORD],
                "method": "CURRENT_POINT_READ", "cross_source_atomic": False,
            }
            assert len(inbox["records"]) == 2
            checked.changes.refresh()
            events = checked.changes.all()
            assert len(events) == 1
            assert events[0].slot_id == "launch_date"
            assert events[0].proposal.payload["canonical_value"] == values["launch_date"]
            assert len(checked.store.list_artifacts(artifact_id_prefix="source-observation:")) == 2
            assert checked.store.get_object("claim:product.launch_date").payload["canonical_value"] != values["launch_date"]
        finally:
            checked.close()
    finally:
        old_release.set()
        new_release.set()
        for process in (old, new):
            if process.pid is not None:
                process.join(timeout=2)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
        results.close()


def test_sqlite_slow_source_restart_and_late_worker_return_are_one_fenced_page(tmp_path):
    pack = make_enterprise_pack(tmp_path)
    _exercise_slow_source_process_replacement(tmp_path / "slow-source.sqlite3", pack)


def test_postgres_slow_source_restart_and_late_worker_return_are_one_fenced_page(tmp_path, postgres_runtime):
    pack = make_enterprise_pack(tmp_path)
    tenant_id = load_enterprise_quote_pilot_pack(pack).profile.organization_id
    database = postgres_runtime(tenant_id=tenant_id)
    _exercise_slow_source_process_replacement(database["runtime_dsn"], pack)


@pytest.mark.parametrize("failure", ["different_subject", "revoked_member"])
def test_worker_rechecks_identity_before_page_admission_and_cleans_context(deployment, monkeypatch, failure):
    def revoke():
        if failure == "different_subject":
            monkeypatch.setenv("TEST_WORKER_ACCESS", deployment.access_token("replacement"))
        else:
            deployment.membership.write_text(json.dumps({**deployment.members, "members": []}))

    requests = install_source(monkeypatch, deployment, after_fetch=revoke)
    principal, authorization = request_principal.get(), request_authorization.get()
    expected = (SourceError, "SOURCE_WORKER_IDENTITY_CHANGED") if failure == "different_subject" else (
        AuthenticationError, "AUTH_MEMBERSHIP_DENIED",
    )
    with pytest.raises(expected[0], match=expected[1]):
        run_source_sync(deployment.path)
    assert len(requests) == 1
    assert request_principal.get() is principal and request_authorization.get() is authorization
    service = open_workspace(deployment.settings)
    try:
        assert service.changes.all() == ()
        assert (service.store.get_source_checkpoint(deployment.connector_id) or {}).get("cursor") is None
    finally:
        service.close()


@pytest.mark.parametrize("status", [403, 429])
def test_worker_distinguishes_lost_source_permission_from_backpressure(deployment, monkeypatch, status):
    class RejectedSource:
        def open(self, request, timeout):
            raise HTTPError(request.full_url, status, "rejected", {"Retry-After": "15"}, io.BytesIO())

    monkeypatch.setattr("orgrebase.workspace.dataverse.build_opener", lambda *args: RejectedSource())
    expected = (SourceError, "SOURCE_PERMISSION_DENIED") if status == 403 else (SourceRateLimited, "SOURCE_RATE_LIMITED")
    with pytest.raises(expected[0], match=expected[1]):
        run_source_sync(deployment.path)
    service = open_workspace(deployment.settings)
    try:
        source = service.store.get_object("claim:product.launch_date")
        if status == 403:
            assert source.state == ObjectState.STALE
            assert service.current_quote().state == ObjectState.REVIEW_REQUIRED
            assert service.changes.gaps()[0]["slot_id"] == "launch_date"
        else:
            assert source.state == ObjectState.CURRENT
            assert service.current_quote().state == ObjectState.CURRENT
            assert service.changes.gaps() == ()
        assert service.changes.all() == ()
        assert (service.store.get_source_checkpoint(deployment.connector_id) or {}).get("cursor") is None
    finally:
        service.close()


def test_worker_accepts_renewed_token_only_for_the_same_principal(deployment, monkeypatch):
    install_source(monkeypatch, deployment, after_fetch=lambda: monkeypatch.setenv(
        "TEST_WORKER_ACCESS", deployment.access_token(),
    ))
    assert run_source_sync(deployment.path)["status"] == "SYNCED"


@pytest.mark.parametrize("code", [
    "SOURCE_PERMISSION_DENIED", "SOURCE_OPERATION_DEADLINE_EXCEEDED",
    "SOURCE_OPERATION_BYTE_BUDGET_EXCEEDED", "SOURCE_OPERATION_READ_BUDGET_EXCEEDED",
])
def test_current_worker_failure_still_invalidates_source_atomically(deployment, monkeypatch, code):
    def fail_data_read():
        raise SourceError(code)

    install_source(monkeypatch, deployment, after_fetch=fail_data_read)
    with pytest.raises(SourceError, match=code):
        run_source_sync(deployment.path)
    service = open_workspace(deployment.settings)
    try:
        checkpoint = service.store.get_source_checkpoint(deployment.connector_id)
        assert checkpoint["revision"] == 0 and checkpoint["cursor"] is None
        assert checkpoint["lease_owner"] is None and checkpoint["lease_until"] is None
        assert service.store.get_object("claim:product.launch_date").state == ObjectState.STALE
        assert service.current_quote().state == ObjectState.REVIEW_REQUIRED
        events = service.store.event_envelopes()
        assert any(event["event_type"] == "SOURCE_BINDING_UNAVAILABLE"
                   and event["payload"]["reason"] == code for event in events)
        assert any(event["event_type"] == "WORKSPACE_SOURCE_INVALIDATED"
                   and event["payload"]["reason"] == code for event in events)
    finally:
        service.close()


def test_failed_error_projection_rolls_back_and_preserves_original_failure(deployment, monkeypatch):
    def fail_data_read():
        raise SourceError("SOURCE_PERMISSION_DENIED")

    def fail_invalidation(self, error, *, connection=None):
        assert connection is self.service.store.connection
        raise RuntimeError("projection failed")

    install_source(monkeypatch, deployment, after_fetch=fail_data_read)
    monkeypatch.setattr(WorkspaceSourceAdmission, "source_unavailable", fail_invalidation)
    with pytest.raises(SourceError, match="SOURCE_PERMISSION_DENIED") as caught:
        run_source_sync(deployment.path)
    assert caught.value.__notes__ == ["SOURCE_FAILURE_RECORDING_FAILED"]
    service = open_workspace(deployment.settings)
    try:
        checkpoint = service.store.get_source_checkpoint(deployment.connector_id)
        assert checkpoint["lease_owner"] is None and checkpoint["lease_until"] is None
        assert service.store.get_object("claim:product.launch_date").state == ObjectState.CURRENT
        assert service.current_quote().state == ObjectState.CURRENT
        assert not any(event["event_type"] in {"SOURCE_BINDING_UNAVAILABLE", "WORKSPACE_SOURCE_INVALIDATED"}
                       for event in service.store.event_envelopes())
    finally:
        service.close()


def test_concurrent_worker_without_claim_does_not_revoke_valid_coverage(deployment, monkeypatch):
    from orgrebase.clock import utc_datetime
    from orgrebase.workspace.source_bindings import load_source_config, source_coverage

    install_source(monkeypatch, deployment)
    assert run_source_sync(deployment.path)["coverage"]["status"] == "COMPLETE"
    service = open_workspace(deployment.settings)
    try:
        with service.store.transaction() as connection:
            busy = service.store.claim_source(
                connection, connector_id=deployment.connector_id, worker_id="current-worker",
                now=utc_datetime(service.clock.now()).timestamp(), lease_seconds=120,
            )
        source, quote = service.store.get_object("claim:product.launch_date"), service.current_quote()
    finally:
        service.close()
    with pytest.raises(SourceError, match="SOURCE_SYNC_IN_PROGRESS"):
        run_source_sync(deployment.path)
    service = open_workspace(deployment.settings)
    principal = request_principal.set(
        JWTAuthenticator(deployment.settings.identity).authenticate("Bearer " + deployment.access_token())
    )
    try:
        assert service.store.get_source_checkpoint(deployment.connector_id) == busy
        assert service.store.get_object("claim:product.launch_date") == source
        assert service.current_quote() == quote
        assert source_coverage(service, load_source_config(deployment.path))["status"] == "COMPLETE"
        assert not any(event["event_type"] == "SOURCE_BINDING_UNAVAILABLE"
                       for event in service.store.event_envelopes())
    finally:
        request_principal.reset(principal)
        service.close()


def test_expired_worker_failure_without_replacement_has_no_write_authority(deployment, monkeypatch):
    class Clock:
        elapsed = 0.0
        started = datetime.now(UTC)

        def now(self):
            return timestamp(self.started + timedelta(seconds=self.elapsed))

        def monotonic(self):
            return self.elapsed

    clock = Clock()
    original_init = SourceSynchronizer.__init__

    def initialize(self, *args, **kwargs):
        kwargs["clock"] = clock
        original_init(self, *args, **kwargs)
        self.reader.monotonic = clock.monotonic

    def fail_after_expiry():
        clock.elapsed = 121
        raise SourceError("SOURCE_PERMISSION_DENIED")

    monkeypatch.setattr(SourceSynchronizer, "__init__", initialize)
    install_source(monkeypatch, deployment, after_fetch=fail_after_expiry)
    with pytest.raises(SourceError, match="SOURCE_PERMISSION_DENIED"):
        run_source_sync(deployment.path)
    service = open_workspace(deployment.settings)
    try:
        assert service.store.get_source_checkpoint(deployment.connector_id)["lease_owner"] is None
        assert service.store.get_object("claim:product.launch_date").state == ObjectState.CURRENT
        assert service.current_quote().state == ObjectState.CURRENT
        assert not any(event["event_type"] == "SOURCE_BINDING_UNAVAILABLE"
                       for event in service.store.event_envelopes())
    finally:
        service.close()


def test_failure_from_replaced_source_binding_has_no_write_authority(deployment, monkeypatch):
    from orgrebase.workspace import source_bindings

    original = source_bindings.active_binding

    def fail_after_replacement():
        def replaced(*args, **kwargs):
            binding = original(*args, **kwargs)
            return {**binding, "binding_digest": "sha256:" + "0" * 64}

        monkeypatch.setattr(source_bindings, "active_binding", replaced)
        raise SourceError("SOURCE_PERMISSION_DENIED")

    install_source(monkeypatch, deployment, after_fetch=fail_after_replacement)
    with pytest.raises(SourceError, match="SOURCE_PERMISSION_DENIED"):
        run_source_sync(deployment.path)
    service = open_workspace(deployment.settings)
    try:
        assert service.store.get_object("claim:product.launch_date").state == ObjectState.CURRENT
        assert service.current_quote().state == ObjectState.CURRENT
        assert not any(event["event_type"] == "SOURCE_BINDING_UNAVAILABLE"
                       for event in service.store.event_envelopes())
    finally:
        service.close()


@pytest.mark.parametrize("budget", [0, 101])
def test_worker_rejects_invalid_page_budget_before_opening_config(tmp_path, budget):
    with pytest.raises(ValueError, match="SOURCE_PAGE_BUDGET_INVALID"):
        run_source_sync(tmp_path / "does-not-exist.json", max_pages=budget)


@pytest.mark.parametrize("config", [[], {}, {"source": {}, "mappings": [],
    "source_token_variable": "TOKEN;read", "access_token_variable": "ACCESS"}])
def test_worker_rejects_invalid_configuration_before_database_access(tmp_path, config):
    path = tmp_path / "source.json"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="SOURCE_CONFIG_INVALID"):
        run_source_sync(path)
