from __future__ import annotations

import io
import json
import socket
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import anyio
import httpx2 as httpx
import pytest

from orgrebase.clock import FrozenClock, timestamp, utc_datetime
from orgrebase.digest import sha256_digest
from orgrebase.domain import Approval, FreshnessError, ObjectState
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
from orgrebase.workspace.source_worker import SourceFieldMapping, WorkspaceSourceAdmission

RECORD = "00000000-0000-0000-0000-000000000001"
OTHER = "00000000-0000-0000-0000-000000000002"
ADMISSION_DIGEST = sha256_digest({"test_admission": "record identity v1"})


def source_coverage_for_records(source_settings, records, now):
    from tests.workspace.test_read_dependencies import changed, coverage

    return changed(coverage(), connector_id=source_settings.connector_id,
        record_ids=sorted(row["record_id"] for row in records), fields=sorted(source_settings.fields),
        observed_at=now, expires_at=timestamp(utc_datetime(now) + timedelta(minutes=5)),
        records={row["record_id"]: {"revision": row.get("revision"), "deleted": row["deleted"],
            "field_digests": {field: sha256_digest(value) for field, value in row.get("fields", {}).items()},
            "observed_at": now} for row in records})


def settings(**changes: object) -> DataverseSettings:
    return DataverseSettings.model_validate({
        "connector_id": "source:quotes", "tenant_id": "tenant:one",
        "instance_url": "https://company.crm.dynamics.com",
        "record_ids": (RECORD,), "fields": ("new_currency",), **changes,
    })


def document(reader: DataverseReader, **row_changes: object) -> dict:
    return {
        "@odata.deltaLink": reader.settings.endpoint + "?$deltatoken=opaque-not-sortable",
        "value": [{"quoteid": RECORD, "@odata.etag": 'W/"000a"',
                   "new_currency": "EUR", **row_changes}],
    }


def test_scope_and_opaque_versions_are_preserved_without_guessing_order() -> None:
    reader = DataverseReader(settings(), lambda: "secret")
    value = document(reader)
    value["value"].append({"quoteid": OTHER, "raw_private_secret": "not persisted"})
    page = reader.parse_page(value)
    assert len(page.records) == 1
    assert page.records[0]["revision"] == 'W/"000a"'
    assert "raw_private_secret" not in json.dumps(page.records)
    assert not page.more


@pytest.mark.parametrize("cursor", [
    "https://attacker.example/api/data/v9.2/quotes?$deltatoken=a",
    "http://company.crm.dynamics.com/api/data/v9.2/quotes?$deltatoken=a",
    "https://company.crm.dynamics.com/api/data/v9.2/accounts?$deltatoken=a",
    "https://company.crm.dynamics.com/api/data/v9.2/quotes#fragment",
])
def test_cursor_cannot_send_credentials_to_another_origin_or_resource(cursor: str) -> None:
    reader = DataverseReader(settings(), lambda: pytest.fail("must validate before token access"))
    with pytest.raises(SourceError, match="SOURCE_CURSOR_SCOPE_MISMATCH"):
        reader.fetch(cursor)


def test_missing_fields_null_values_and_deletion_are_distinct() -> None:
    reader = DataverseReader(settings(), lambda: "secret")
    value = document(reader)
    del value["value"][0]["new_currency"]
    with pytest.raises(SourceError, match="SOURCE_FIELDS_UNAVAILABLE:new_currency"):
        reader.parse_page(value)
    assert reader.parse_page(document(reader, new_currency=None)).records[0]["fields"] == {
        "new_currency": None
    }
    value["value"] = [{"@odata.context": "https://example/$deletedEntity",
                       "id": RECORD, "reason": "deleted"}]
    assert reader.parse_page(value).records == ({"record_id": RECORD, "deleted": True},)


@pytest.mark.parametrize(("status", "code"), [
    (401, "SOURCE_AUTHENTICATION_FAILED"), (403, "SOURCE_PERMISSION_DENIED"),
    (404, "SOURCE_ENDPOINT_UNAVAILABLE"), (410, "SOURCE_CURSOR_EXPIRED"),
    (500, "SOURCE_REQUEST_FAILED"),
])
def test_http_failure_does_not_leak_tokens_or_response_text(status: int, code: str) -> None:
    reader = DataverseReader(settings(), lambda: "canary-token")

    class Failure:
        def open(self, request: object, timeout: float) -> None:
            raise HTTPError("secret-url", status, "private text", {}, io.BytesIO(b"canary-body"))

    reader.opener = Failure()
    with pytest.raises(SourceError) as error:
        reader.fetch(None)
    assert str(error.value) == code


def test_rate_limit_exposes_bounded_retry_deadline_without_sleeping() -> None:
    reader = DataverseReader(settings(), lambda: "token")

    class Limited:
        def open(self, request: object, timeout: float) -> None:
            raise HTTPError("url", 429, "limited", {"Retry-After": "120"}, io.BytesIO())

    reader.opener = Limited()
    with pytest.raises(SourceRateLimited) as error:
        reader.fetch(None)
    assert error.value.retry_after == 120


class _BudgetClock:
    def __init__(self) -> None:
        self.elapsed = 0.0
        self.started = datetime(2026, 9, 9, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.elapsed

    def now(self) -> str:
        return timestamp(self.started + timedelta(seconds=self.elapsed))

    def advance(self, seconds: float) -> None:
        self.elapsed += seconds


class _AdvancingResponse:
    status = 200

    def __init__(self, payload: dict, clock: _BudgetClock, delay: float) -> None:
        self.payload = json.dumps(payload).encode()
        self.clock = clock
        self.delay = delay
        self.sent = False
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def read(self, _size: int) -> bytes:
        if self.sent:
            return b""
        self.sent = True
        self.clock.advance(self.delay)
        return self.payload

    def close(self) -> None:
        self.closed = True


def test_normal_eight_slow_readbacks_renew_one_fence_and_commit_cursor_once() -> None:
    identities = tuple(f"00000000-0000-0000-0000-{index:012d}" for index in range(1, 9))
    source_settings = settings(record_ids=identities)
    clock = _BudgetClock()
    reader = DataverseReader(
        source_settings, lambda: "token", operation_timeout=140, max_reads=16,
        monotonic=clock.monotonic,
    )
    requests = []

    class Opener:
        def open(self, request, timeout):
            requests.append((request.full_url, timeout))
            if "/quotes(" not in request.full_url:
                return _AdvancingResponse({
                    "@odata.deltaLink": source_settings.endpoint + "?$deltatoken=complete",
                    "value": [],
                }, clock, 0)
            identity = request.full_url.split("quotes(", 1)[1].split(")", 1)[0]
            return _AdvancingResponse({
                "quoteid": identity, "@odata.etag": 'W/"slow"',
                "new_currency": "EUR", "modifiedon": "2026-09-09T00:00:00Z",
            }, clock, 16)

    reader.opener = Opener()

    def verify(page: SourcePage) -> SourcePage:
        records = tuple(reader.read_record(identity) for identity in identities)
        return SourcePage(records, page.cursor, False, {"complete": True})

    admitted = []
    with StateStore(tenant_id="tenant:one") as store:
        sync = SourceSynchronizer(
            store, reader, worker_id="slow-reader", admit=lambda _c, row, *_: admitted.append(row),
            admission_digest=ADMISSION_DIGEST, clock=clock, verify_page=verify,
            lease_seconds=20, max_lease_renewals=20,
        )
        result = sync.sync_page()
        checkpoint = store.get_source_checkpoint(source_settings.connector_id)
    assert result["records_admitted"] == 8 and not result["more"]
    assert len(admitted) == 8 and len(requests) == 9
    assert clock.elapsed == 128
    assert checkpoint["revision"] == 1
    assert checkpoint["cursor"].endswith("?$deltatoken=complete")
    assert checkpoint["lease_owner"] is None and checkpoint["lease_until"] is None


def test_source_socket_read_is_interrupted_at_total_deadline_and_cursor_stays_put() -> None:
    client_socket, server_socket = socket.socketpair()

    class BlockingResponse:
        status = 200

        def __init__(self):
            self.closed = False
            self.fp = SimpleNamespace(raw=SimpleNamespace(_sock=client_socket))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

        def isclosed(self):
            return self.closed

        def read1(self, size):
            return client_socket.recv(size)

        def close(self):
            if not self.closed:
                self.closed = True
                client_socket.close()
                server_socket.close()

    response = BlockingResponse()
    reader = DataverseReader(
        settings(), lambda: "token", timeout=1, operation_timeout=0.03,
    )
    reader.opener = SimpleNamespace(open=lambda *_args, **_kwargs: response)
    with StateStore(tenant_id="tenant:one") as store:
        sync = SourceSynchronizer(
            store, reader, worker_id="reader", admit=lambda *_: pytest.fail("must not admit"),
            admission_digest=ADMISSION_DIGEST, lease_seconds=1,
        )
        started = time.monotonic()
        with pytest.raises(SourceError, match="SOURCE_OPERATION_DEADLINE_EXCEEDED"):
            sync.sync_page()
        elapsed = time.monotonic() - started
        checkpoint = store.get_source_checkpoint(reader.settings.connector_id)
    assert elapsed < 0.5 and response.closed
    assert checkpoint["cursor"] is None and checkpoint["revision"] == 0
    assert checkpoint["lease_owner"] is None


@pytest.mark.parametrize("phase", ["headers", "body"])
def test_source_async_transport_cancels_headers_and_body_at_total_deadline(phase) -> None:
    class BlockingStream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b"{"
            await anyio.sleep(0.2)
            yield b"}"

        async def aclose(self):
            self.closed = True

    stream = BlockingStream()

    async def respond(_request):
        if phase == "headers":
            await anyio.sleep(0.2)
            return httpx.Response(200, json={"value": []})
        return httpx.Response(200, stream=stream)

    reader = DataverseReader(
        settings(), lambda: "token", timeout=1, operation_timeout=0.03,
        async_transport=httpx.MockTransport(respond),
    )
    started = time.monotonic()
    with pytest.raises(SourceError, match="SOURCE_OPERATION_DEADLINE_EXCEEDED"):
        reader.fetch(None)
    assert time.monotonic() - started < 0.12
    if phase == "body":
        assert stream.closed


@pytest.mark.parametrize("limit", ["reads", "bytes"])
def test_source_operation_budgets_stop_before_cursor_commit(limit) -> None:
    page = {
        "@odata.deltaLink": settings().endpoint + "?$deltatoken=budget",
        "value": [],
    }
    record = {
        "quoteid": RECORD, "@odata.etag": 'W/"budget"', "new_currency": "EUR",
    }
    page_bytes = json.dumps(page).encode()
    record_bytes = json.dumps(record).encode()
    requests = []

    async def respond(request):
        requests.append(str(request.url))
        return httpx.Response(200, content=record_bytes if "/quotes(" in request.url.path else page_bytes)

    reader = DataverseReader(
        settings(), lambda: "token", max_reads=1 if limit == "reads" else 2,
        max_operation_bytes=(len(page_bytes) + len(record_bytes) - 1 if limit == "bytes" else 1024),
        async_transport=httpx.MockTransport(respond),
    )

    def verify(page_result: SourcePage) -> SourcePage:
        return SourcePage((reader.read_record(RECORD),), page_result.cursor, False)

    expected = (
        "SOURCE_OPERATION_READ_BUDGET_EXCEEDED"
        if limit == "reads" else "SOURCE_OPERATION_BYTE_BUDGET_EXCEEDED"
    )
    with StateStore(tenant_id="tenant:one") as store:
        sync = SourceSynchronizer(
            store, reader, worker_id="budget-reader", admit=lambda *_: pytest.fail("must not admit"),
            admission_digest=ADMISSION_DIGEST, verify_page=verify,
        )
        with pytest.raises(SourceError, match=expected):
            sync.sync_page()
        checkpoint = store.get_source_checkpoint(reader.settings.connector_id)
    assert len(requests) == (1 if limit == "reads" else 2)
    assert checkpoint["cursor"] is None and checkpoint["revision"] == 0


def test_page_admission_failure_rolls_back_cursor_and_replays_durable_inbox() -> None:
    reader = DataverseReader(settings(), lambda: "token")
    fetched = []

    def fetch(cursor: str | None) -> SourcePage:
        fetched.append(cursor)
        return SourcePage(({"record_id": RECORD}, {"record_id": OTHER}), "opaque-next", False)

    reader.fetch = fetch
    fail = True
    with StateStore(tenant_id="tenant:one") as store:
        def admit(connection: object, record: dict, observed: str, page_ref: str) -> None:
            store.save_artifact(connection, "admitted:" + record["record_id"], "application/json", record)
            if fail and record["record_id"] == OTHER:
                raise RuntimeError("injected second record failure")

        sync = SourceSynchronizer(
            store, reader, worker_id="reader", admit=admit,
            admission_digest=ADMISSION_DIGEST,
            clock=FrozenClock("2026-09-09T00:00:00Z"),
        )
        with pytest.raises(RuntimeError, match="second record failure"):
            sync.sync_page()
        assert store.get_source_checkpoint("source:quotes")["cursor"] is None
        assert not store.artifact_exists("admitted:" + RECORD)
        fail = False
        result = sync.sync_page()
        assert result["records_admitted"] == 2
        assert len(fetched) == 1
        assert store.get_source_checkpoint("source:quotes")["cursor"] == "opaque-next"


def test_reader_rejects_cross_tenant_store() -> None:
    with (
        StateStore(tenant_id="tenant:two") as store,
        pytest.raises(SourceError, match="SOURCE_STORE_TENANT_MISMATCH"),
    ):
        SourceSynchronizer(
            store, DataverseReader(settings(), lambda: "token"), worker_id="reader",
            admission_digest=ADMISSION_DIGEST,
            admit=lambda *args: None,
        )


def test_expired_reader_cannot_publish_an_inbox_after_another_worker_takes_over() -> None:
    reader = DataverseReader(settings(), lambda: "token")
    with StateStore(tenant_id="tenant:one") as store:
        def fetch(cursor: str | None) -> SourcePage:
            with store.transaction() as connection:
                store.claim_source(
                    connection, connector_id="source:quotes", worker_id="successor",
                    now=1788912121, lease_seconds=120,
                )
            return SourcePage((), reader.settings.endpoint + "?$deltatoken=next", False)

        reader.fetch = fetch
        sync = SourceSynchronizer(
            store, reader, worker_id="old", admit=lambda *args: pytest.fail("stale page admitted"),
            admission_digest=ADMISSION_DIGEST,
            clock=FrozenClock("2026-09-09T00:00:00Z"),
        )
        with pytest.raises(SourceError, match="SOURCE_STALE_CLAIM"):
            sync.sync_page()
        assert store.get_source_checkpoint("source:quotes")["lease_owner"] == "successor"
        assert not store.list_artifacts(artifact_id_prefix="source-page:")


def test_unadmitted_private_page_expires_without_refetch_or_resurrection() -> None:
    reader = DataverseReader(settings(), lambda: "token")
    fetched = []

    def fetch(cursor: str | None) -> SourcePage:
        fetched.append(cursor)
        return SourcePage(({"record_id": RECORD, "private_value": "confidential"},), "next", False)

    reader.fetch = fetch
    with StateStore(tenant_id="tenant:one") as store:
        def reject(*args: object) -> None:
            raise SourceError("ADMISSION_FAILED")

        sync = SourceSynchronizer(store, reader, worker_id="reader", admit=reject,
                                  admission_digest=ADMISSION_DIGEST,
                                  clock=FrozenClock("2026-09-09T00:00:00Z"))
        with pytest.raises(SourceError, match="ADMISSION_FAILED"):
            sync.sync_page()
        assert not store.list_artifacts(artifact_id_prefix="source-page:")
        sync.clock = sync.inbox.clock = FrozenClock("2026-09-10T00:00:01Z")
        with pytest.raises(SourceError, match="SOURCE_INBOX_UNAVAILABLE_RECONCILIATION_REQUIRED"):
            sync.sync_page()
        assert len(fetched) == 1
        assert store.get_source_checkpoint("source:quotes")["cursor"] is None
        assert sync.inbox.purge_expired() == 1
        assert len(sync.inbox.deletion_ledger()) == 1


def test_pending_inbox_cannot_be_reinterpreted_under_a_changed_admission_contract() -> None:
    reader = DataverseReader(settings(), lambda: "unused")
    fetched = []
    failed = True

    def fetch(cursor: str | None) -> SourcePage:
        fetched.append(cursor)
        return SourcePage(({"record_id": RECORD, "value": "source code"},), "next", False)

    reader.fetch = fetch
    with StateStore(tenant_id="tenant:one") as store:
        def original_mapping(connection: object, record: dict, observed: str, page_ref: str) -> None:
            store.save_artifact(connection, "mapped-result", "application/json", {"currency": "GBP"})
            if failed:
                raise RuntimeError("ADMISSION_INTERRUPTED")

        original_digest = sha256_digest({"value_map": {"source code": "GBP"}})
        original = SourceSynchronizer(
            store, reader, worker_id="original", admit=original_mapping,
            admission_digest=original_digest, clock=FrozenClock("2026-09-09T00:00:00Z"),
        )
        with pytest.raises(RuntimeError, match="ADMISSION_INTERRUPTED"):
            original.sync_page()
        before = store.get_source_checkpoint("source:quotes")
        assert before["cursor"] is None
        assert not store.artifact_exists("mapped-result")

        def changed_mapping(*args: object) -> None:
            pytest.fail("durable page must not be admitted under a changed mapping")

        changed = SourceSynchronizer(
            store, reader, worker_id="changed", admit=changed_mapping,
            admission_digest=sha256_digest({"value_map": {"source code": "JPY"}}),
            clock=FrozenClock("2026-09-09T00:00:00Z"),
        )
        with pytest.raises(SourceError, match="SOURCE_BINDING_CHANGED_REQUALIFICATION_REQUIRED"):
            changed.sync_page()
        assert store.get_source_checkpoint("source:quotes") == before
        assert len(fetched) == 1
        failed = False
        original.sync_page()
        assert store.load_artifact("mapped-result").payload == {"currency": "GBP"}
        assert store.get_source_checkpoint("source:quotes")["cursor"] == "next"
        assert len(fetched) == 1


def test_source_change_and_null_use_the_same_governed_workspace_flow(tmp_path: Path, monkeypatch) -> None:
    pack = load_enterprise_quote_pilot_pack(
        Path(__file__).resolve().parents[2] / "examples/enterprise-quote-pilot/evergreen"
    )
    service = WorkspaceService(store_path=tmp_path / "source.db", runtime_configuration=pack)
    try:
        with pytest.raises(SourceError, match="SOURCE_BASELINE_FORMATION_REQUIRED"):
            WorkspaceSourceAdmission(service, settings(tenant_id=service.profile.organization_id), (
                SourceFieldMapping(record_id=RECORD, field="new_currency", slot_id="currency"),
            ))
        service.form_quote()
        source_settings = settings(tenant_id=service.profile.organization_id)
        receiver = WorkspaceSourceAdmission(service, source_settings, (
            SourceFieldMapping(record_id=RECORD, field="new_currency", slot_id="currency"),
        ))
        changed_receiver = WorkspaceSourceAdmission(service, source_settings, (
            SourceFieldMapping(record_id=RECORD, field="new_currency", slot_id="currency",
                               value_map={"GBP": "JPY"}),
        ))
        assert receiver.admission_digest != changed_receiver.admission_digest
        before = service.current_quote()
        record = {"record_id": RECORD, "revision": 'W/"unorderable-a"',
                  "fields": {"new_currency": "GBP"}, "deleted": False}
        monkeypatch.setattr("orgrebase.workspace.read_dependencies._coverage",
            lambda workspace, now: source_coverage_for_records(source_settings, [record], now))
        with service.store.transaction() as connection:
            receiver(connection, record, service.clock.now(), "page:initial")
        service.changes.refresh()
        event = service.changes.all()[-1]
        assert event.proposal.payload["canonical_value"] == "GBP"
        assert service.current_quote().digest == before.digest
        preview = service.preview_change(event.event_id)
        approval = service.approve_change(
            event.event_id, actor_id=event.owner_id, preview_digest=preview.preview.digest,
        )
        service.apply_approved_change(event.event_id, approval_digest=approval["approval_digest"])
        assert service.current_quote().payload["currency"] == "GBP"
        with service.store.transaction() as connection:
            receiver(connection, {**record, "revision": 'W/"next"', "fields": {"new_currency": None}},
                     service.clock.now(), "page:null")
        assert service.current_quote().state == ObjectState.REVIEW_REQUIRED
        assert service.state()["source_gaps"]
        with pytest.raises(RuntimeError):
            service.export_quote()
    finally:
        service.close()


@pytest.mark.parametrize("next_value", ["JPY", "GBP", "original"])
def test_new_source_observation_closes_old_preview_and_approval(tmp_path, monkeypatch, next_value):
    from tests.workspace.test_continuous_changes import make_service

    service = make_service(tmp_path / "latest-source.sqlite")
    try:
        source_settings = settings(tenant_id=service.profile.organization_id)
        receiver = WorkspaceSourceAdmission(service, source_settings, (
            SourceFieldMapping(record_id=RECORD, field="new_currency", slot_id="currency"),
        ))
        original = service.current_quote()
        before_source = service.store.get_object("policy:finance.currency")
        record = {"record_id": RECORD, "revision": 'W/"z"', "deleted": False,
                  "fields": {"new_currency": "GBP"}}
        latest = source_coverage_for_records(source_settings, [record], service.clock.now())
        monkeypatch.setattr("orgrebase.workspace.read_dependencies._coverage", lambda workspace, now: latest)
        with service.store.transaction() as connection:
            receiver(connection, record, service.clock.now(), "page:old")
        event = service.changes.all()[-1]
        preview = service.preview_change(event.event_id)
        approval = service.approve_change(event.event_id, actor_id=event.owner_id,
                                         preview_digest=preview.preview.digest)
        newer = {**record, "revision": 'W/"a"', "fields": {"new_currency":
            before_source.payload["canonical_value"] if next_value == "original" else next_value}}
        with service.store.transaction() as connection:
            receiver(connection, newer, service.clock.now(), "page:new")
        latest = source_coverage_for_records(source_settings, [newer], service.clock.now())
        assert service._change_status(event.event_id) == "STALE"
        with pytest.raises(FreshnessError, match="SOURCE_OBSERVATION_REPLACED"):
            service.preview_change(event.event_id)
        with pytest.raises(FreshnessError, match="SOURCE_OBSERVATION_REPLACED"):
            service.approve_change(event.event_id, actor_id=event.owner_id, preview_digest=preview.preview.digest)
        with pytest.raises(RuntimeError, match="WORKSPACE_CHANGE_NOT_APPLICABLE"):
            service.apply_approved_change(event.event_id, approval_digest=approval["approval_digest"])
        # Replaying the old inbox record cannot move the committed coverage back.
        with service.store.transaction() as connection:
            receiver(connection, record, service.clock.now(), "page:old")
        assert service._change_status(event.event_id) == "STALE"
        assert service.current_quote() == original
        assert service.store.get_object(before_source.id) == before_source
    finally:
        service.close()


def test_source_update_rechecks_observation_inside_apply_transaction(tmp_path, monkeypatch):
    from tests.workspace.test_continuous_changes import make_service
    from tests.workspace.test_read_dependencies import changed

    service = make_service(tmp_path / "source-race.sqlite")
    try:
        source_settings = settings(tenant_id=service.profile.organization_id)
        receiver = WorkspaceSourceAdmission(service, source_settings, (
            SourceFieldMapping(record_id=RECORD, field="new_currency", slot_id="currency"),
        ))
        record = {"record_id": RECORD, "revision": 'W/"old"', "deleted": False,
                  "fields": {"new_currency": "GBP"}}
        initial = source_coverage_for_records(source_settings, [record], service.clock.now())
        unavailable = changed(initial, status="UNKNOWN", reasons=["SOURCE_PERMISSION_DENIED"])
        attack = False
        checks = []

        def read(workspace, now):
            from orgrebase.database import in_transaction
            active = in_transaction(workspace.store.connection)
            checks.append(active)
            return unavailable if attack and active else initial

        monkeypatch.setattr("orgrebase.workspace.read_dependencies._coverage", read)
        with service.store.transaction() as connection:
            receiver(connection, record, service.clock.now(), "page:old")
        event = service.changes.all()[-1]
        preview = service.preview_change(event.event_id)
        approval = service.approve_change(event.event_id, actor_id=event.owner_id,
                                         preview_digest=preview.preview.digest)
        before_quote, before_source = service.current_quote(), service.store.get_object(event.proposal.id)
        before_audit = service.store.audit_head()
        checks.clear()
        attack = True
        with pytest.raises(FreshnessError, match="SOURCE_PERMISSION_DENIED"):
            service._execute_apply(kind=event.event_id, bundle=preview,
                approval=Approval.model_validate(approval["approval"]))
        assert True in checks
        assert service.current_quote() == before_quote
        assert service.store.get_object(before_source.id) == before_source
        assert service.store.audit_head() == before_audit
    finally:
        service.close()


def test_same_value_new_revision_does_not_block_other_manual_changes_after_apply(tmp_path, monkeypatch):
    from tests.workspace.test_continuous_changes import apply, make_service, proposal

    service = make_service(tmp_path / "source-same-value.sqlite")
    try:
        source_settings = settings(tenant_id=service.profile.organization_id)
        receiver = WorkspaceSourceAdmission(service, source_settings, (
            SourceFieldMapping(record_id=RECORD, field="new_currency", slot_id="currency"),
        ))
        record = {"record_id": RECORD, "revision": 'W/"old"', "deleted": False,
                  "fields": {"new_currency": "GBP"}}
        latest = source_coverage_for_records(source_settings, [record], service.clock.now())
        monkeypatch.setattr("orgrebase.workspace.read_dependencies._coverage", lambda workspace, now: latest)
        with service.store.transaction() as connection:
            receiver(connection, record, service.clock.now(), "page:old")
        event = service.changes.all()[-1]
        apply(service, event.event_id)
        applied = service.store.get_object(event.proposal.id)
        newer = {**record, "revision": 'W/"new"'}
        with service.store.transaction() as connection:
            receiver(connection, newer, service.clock.now(), "page:new")
        latest = source_coverage_for_records(source_settings, [newer], service.clock.now())
        assert service.changes.all()[-1].event_id == event.event_id
        assert service._change_status(event.event_id) == "APPLIED"
        manual = proposal(service, "manual-launch", "launch_date", "2031-06-01")
        service.register_change(manual)
        apply(service, manual.event_id)
        assert service.current_quote().payload["launch_date"] == "2031-06-01"
        assert service.store.get_object(applied.id) == applied
    finally:
        service.close()


def test_source_readmission_group_closes_when_one_observation_is_replaced(tmp_path, monkeypatch):
    from orgrebase.workspace.source_readmission import (
        SourceReadmissionInput,
        apply_group,
        approve_group,
        create_group,
        group_detail,
    )
    from tests.workspace.test_continuous_changes import make_service
    from tests.workspace.test_source_readmission_groups import command

    service = make_service(tmp_path / "source-group-latest.sqlite")
    try:
        source_settings = settings(tenant_id=service.profile.organization_id,
                                   fields=("new_currency", "new_launch_date"))
        receiver = WorkspaceSourceAdmission(service, source_settings, (
            SourceFieldMapping(record_id=RECORD, field="new_currency", slot_id="currency"),
            SourceFieldMapping(record_id=RECORD, field="new_launch_date", slot_id="launch_date"),
        ))
        for slot in ("currency", "launch_date"):
            service.invalidate_source(slot, f"https://source.example/{slot}", "SOURCE_FIELDS_UNAVAILABLE")
        record = {"record_id": RECORD, "revision": 'W/"old"', "deleted": False,
                  "fields": {"new_currency": "GBP", "new_launch_date": "2031-06-01"}}
        latest = source_coverage_for_records(source_settings, [record], service.clock.now())
        monkeypatch.setattr("orgrebase.workspace.read_dependencies._coverage", lambda workspace, now: latest)
        with service.store.transaction() as connection:
            receiver(connection, record, service.clock.now(), "page:old")
        events = tuple(event for event in service.changes.all() if event.event_id.startswith("source:"))
        detail = create_group(service, SourceReadmissionInput(group_id="source-latest", reason="共同恢复来源",
            events=[{"event_id": event.event_id, "event_digest": event.digest} for event in events]))
        for owner in detail["owners"]:
            approve_group(service, "source-latest", command(detail, owner["owner_id"]))
        newer = {**record, "revision": 'W/"new"', "fields": {**record["fields"], "new_currency": "JPY"}}
        with service.store.transaction() as connection:
            receiver(connection, newer, service.clock.now(), "page:new")
        latest = source_coverage_for_records(source_settings, [newer], service.clock.now())
        before_quote, before_audit = service.current_quote(), service.store.audit_head()
        assert group_detail(service, "source-latest")["state"] == "EXPIRED"
        assert all(service._change_status(event.event_id) == "EXPIRED" for event in events)
        with pytest.raises(FreshnessError, match="SOURCE_OBSERVATION_REPLACED"):
            approve_group(service, "source-latest", command(detail, detail["owners"][0]["owner_id"]))
        with pytest.raises(FreshnessError, match="SOURCE_OBSERVATION_REPLACED"):
            apply_group(service, "source-latest", command(detail))
        assert service.current_quote() == before_quote
        assert service.store.audit_head() == before_audit
    finally:
        service.close()


@pytest.mark.parametrize("error_code", [
    "SOURCE_PERMISSION_DENIED", "SOURCE_FIELDS_UNAVAILABLE:new_currency",
])
def test_source_read_failure_invalidates_quote_and_preserves_exact_reason(
    tmp_path: Path, error_code: str,
) -> None:
    pack = load_enterprise_quote_pilot_pack(
        Path(__file__).resolve().parents[2] / "examples/enterprise-quote-pilot/evergreen"
    )
    service = WorkspaceService(store_path=tmp_path / "source-failure.db", runtime_configuration=pack)
    try:
        service.form_quote()
        receiver = WorkspaceSourceAdmission(service, settings(tenant_id=service.profile.organization_id), (
            SourceFieldMapping(record_id=RECORD, field="new_currency", slot_id="currency"),
        ))
        receiver.source_unavailable(SourceError(error_code))
        assert service.current_quote().state == ObjectState.REVIEW_REQUIRED
        gaps = service.state()["source_gaps"]
        assert any(gap["slot_id"] == "currency" for gap in gaps)
        evidence = [event["payload"] for event in service.store.event_envelopes()
                    if event["event_type"] == "WORKSPACE_SOURCE_INVALIDATED"]
        assert any(item["reason"] == error_code.partition(":")[0] for item in evidence)
        assert any(item["source_ref"].endswith("#new_currency") for item in evidence)
        with pytest.raises(RuntimeError):
            service.export_quote()
    finally:
        service.close()
