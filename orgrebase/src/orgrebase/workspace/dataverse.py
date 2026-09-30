"""Read-only Dataverse change tracking with durable, bounded page admission."""

from __future__ import annotations

import json
import re
import ssl
import time
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    OpenerDirector,
    ProxyHandler,
    Request,
    build_opener,
)

import httpx2 as httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from orgrebase.clock import Clock, SystemClock, utc_datetime
from orgrebase.digest import sha256_digest
from orgrebase.private_records import PrivateRecordStore
from orgrebase.store import StateStore


class SourceError(RuntimeError):
    """Source failure with a stable code and no response body or credentials."""


class SourceRateLimited(SourceError):
    def __init__(self, retry_after: int) -> None:
        super().__init__("SOURCE_RATE_LIMITED")
        self.retry_after = retry_after


@dataclass
class SourceExecutionBudget:
    """One finite budget shared by a page fetch and all final readbacks."""

    max_elapsed: float
    max_reads: int
    max_bytes: int
    monotonic: Callable[[], float]
    on_progress: Callable[[], None] | None = None
    reads: int = 0
    bytes_read: int = 0

    def __post_init__(self) -> None:
        if (
            not 0 < self.max_elapsed <= 3600
            or not 1 <= self.max_reads <= 256
            or not 1 <= self.max_bytes <= 64 * 1024 * 1024
        ):
            raise ValueError("SOURCE_EXECUTION_BUDGET_INVALID")
        self.deadline = self.monotonic() + self.max_elapsed

    def remaining(self) -> float:
        remaining = self.deadline - self.monotonic()
        if remaining <= 0:
            raise SourceError("SOURCE_OPERATION_DEADLINE_EXCEEDED")
        return remaining

    def begin_read(self) -> float:
        if self.reads >= self.max_reads:
            raise SourceError("SOURCE_OPERATION_READ_BUDGET_EXCEEDED")
        self.reads += 1
        self.remaining()
        if self.on_progress is not None:
            self.on_progress()
        return self.remaining()

    def consume(self, size: int) -> None:
        self.bytes_read += size
        if self.bytes_read > self.max_bytes:
            raise SourceError("SOURCE_OPERATION_BYTE_BUDGET_EXCEEDED")
        self.remaining()

    def finish_read(self) -> None:
        self.remaining()


_SOURCE_BUDGET: ContextVar[tuple[int, SourceExecutionBudget] | None] = ContextVar(
    "source_execution_budget", default=None
)


class DataverseSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    connector_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")
    tenant_id: str = Field(min_length=1)
    instance_url: str
    record_ids: tuple[str, ...] = Field(min_length=1, max_length=1000)
    fields: tuple[str, ...] = Field(min_length=1, max_length=32)
    page_size: int = Field(default=100, ge=1, le=1000)
    ca_bundle: str | None = None

    @model_validator(mode="after")
    def valid_endpoint(self) -> DataverseSettings:
        origin = urlsplit(self.instance_url)
        if (
            origin.scheme != "https" or not origin.hostname or origin.username or origin.password
            or origin.query or origin.fragment or origin.path not in {"", "/"}
        ):
            raise ValueError("DATAVERSE_HTTPS_ORIGIN_REQUIRED")
        if len(set(self.record_ids)) != len(self.record_ids) or any(
            not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", value)
            for value in self.record_ids
        ):
            raise ValueError("DATAVERSE_RECORD_IDS_INVALID")
        if len(set(self.fields)) != len(self.fields) or any(
            not re.fullmatch(r"[a-z][a-z0-9_]{0,127}", value) for value in self.fields
        ):
            raise ValueError("DATAVERSE_FIELDS_INVALID")
        return self

    @property
    def endpoint(self) -> str:
        return f"{self.instance_url.rstrip('/')}/api/data/v9.2/quotes"

    @property
    def initial_url(self) -> str:
        fields = sorted({"quoteid", "modifiedon", *self.fields})
        return f"{self.endpoint}?{urlencode({'$select': ','.join(fields)})}"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        raise SourceError("SOURCE_REDIRECT_REFUSED")


@dataclass(frozen=True)
class SourcePage:
    records: tuple[dict[str, Any], ...]
    cursor: str
    more: bool
    readback: dict[str, Any] | None = None


class DataverseReader:
    def __init__(
        self, settings: DataverseSettings, token: Callable[[], str], *, timeout: float = 20,
        operation_timeout: float = 180, max_reads: int = 64,
        max_operation_bytes: int = 16 * 1024 * 1024,
        monotonic: Callable[[], float] = time.monotonic,
        async_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not 0 < timeout <= 30:
            raise ValueError("SOURCE_TIMEOUT_INVALID")
        if (
            not 0 < operation_timeout <= 3600
            or not 1 <= max_reads <= 256
            or not 1 <= max_operation_bytes <= 64 * 1024 * 1024
        ):
            raise ValueError("SOURCE_EXECUTION_BUDGET_INVALID")
        self.settings = settings
        self.token = token
        self.timeout = timeout
        self.operation_timeout = operation_timeout
        self.max_reads = max_reads
        self.max_operation_bytes = max_operation_bytes
        self.monotonic = monotonic
        self.async_transport = async_transport
        self.opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context(
            cafile=settings.ca_bundle)), _NoRedirect())

    @contextmanager
    def execution_budget(
        self, *, on_progress: Callable[[], None] | None = None,
    ):
        current = _SOURCE_BUDGET.get()
        if current is not None and current[0] == id(self):
            yield current[1]
            return
        budget = SourceExecutionBudget(
            self.operation_timeout, self.max_reads, self.max_operation_bytes,
            self.monotonic, on_progress,
        )
        token = _SOURCE_BUDGET.set((id(self), budget))
        try:
            yield budget
        finally:
            _SOURCE_BUDGET.reset(token)

    def _active_budget(self) -> SourceExecutionBudget:
        current = _SOURCE_BUDGET.get()
        if current is not None and current[0] == id(self):
            return current[1]
        return SourceExecutionBudget(
            self.operation_timeout, self.max_reads, self.max_operation_bytes, self.monotonic
        )

    def _validate_url(self, value: str) -> str:
        url = urlsplit(value)
        expected = urlsplit(self.settings.endpoint)
        if (
            url.scheme != expected.scheme or url.netloc != expected.netloc
            or url.path != expected.path or url.fragment or url.username or url.password
        ):
            raise SourceError("SOURCE_CURSOR_SCOPE_MISMATCH")
        return value

    def fetch(self, cursor: str | None) -> SourcePage:
        with self.execution_budget() as budget:
            url = self._validate_url(cursor or self.settings.initial_url)
            page = self.parse_page(self._read(url, tracking=True))
            budget.remaining()
            return page

    def metadata(self, path: str) -> dict[str, Any]:
        with self.execution_budget() as budget:
            if len(path) > 8192 or not re.fullmatch(r"/(?:WhoAmI|EntityDefinitions(?:\([^\r\n]*\))?(?:/Attributes(?:/Microsoft\.Dynamics\.CRM\.[A-Za-z]+AttributeMetadata)?)?(?:\?[^\r\n]*)?)", path):
                raise SourceError("SOURCE_METADATA_PATH_FORBIDDEN")
            document = self._read(self.settings.instance_url.rstrip('/') + '/api/data/v9.2' + path)
            budget.remaining()
            return document

    def read_record(self, record_id: str) -> dict[str, Any]:
        with self.execution_budget() as budget:
            if record_id not in self.settings.record_ids:
                raise SourceError("SOURCE_RECORD_SCOPE_MISMATCH")
            document = self._read(self.settings.endpoint + f"({record_id})?" + urlencode({
                "$select": ",".join(sorted({"quoteid", "modifiedon", *self.settings.fields})),
            }))
            if document.get("quoteid") != record_id:
                raise SourceError("SOURCE_READBACK_IDENTITY_MISMATCH")
            record = self._live_record(document)
            budget.remaining()
            return record

    def _read(self, url: str, *, tracking: bool = False) -> dict[str, Any]:
        budget = self._active_budget()
        token = self.token()
        if not token or any(character in token for character in "\r\n"):
            raise SourceError("SOURCE_CREDENTIAL_UNAVAILABLE")
        headers = {
            "Authorization": f"Bearer {token}", "Accept": "application/json",
            "OData-Version": "4.0", "OData-MaxVersion": "4.0",
        }
        if tracking:
            headers["Prefer"] = f"odata.track-changes,odata.maxpagesize={self.settings.page_size}"
        request = Request(url, headers=headers, method="GET")
        if self.async_transport is not None or isinstance(self.opener, OpenerDirector):
            payload = self._read_async(url, headers=headers, budget=budget)
            try:
                document = json.loads(payload)
            except (ValueError, UnicodeDecodeError):
                raise SourceError("SOURCE_RESPONSE_INVALID_JSON") from None
            if not isinstance(document, dict):
                raise SourceError("SOURCE_SCHEMA_DRIFT")
            budget.remaining()
            return document
        try:
            remaining = budget.begin_read()
            with self.opener.open(request, timeout=min(self.timeout, remaining)) as response:
                if response.status != 200:
                    raise SourceError("SOURCE_RESPONSE_UNEXPECTED")
                payload = bytearray()
                is_closed = getattr(response, "isclosed", None)
                while not callable(is_closed) or not is_closed():
                    remaining = budget.remaining()
                    if callable(is_closed):
                        sock = getattr(
                            getattr(getattr(response, "fp", None), "raw", None), "_sock", None
                        )
                        if sock is None:
                            raise SourceError("SOURCE_DEADLINE_ENFORCEMENT_UNAVAILABLE")
                        sock.settimeout(min(self.timeout, remaining))
                        chunk = response.read1(65_536)
                    else:
                        chunk = response.read(65_536)
                    if not chunk:
                        break
                    payload.extend(chunk)
                    budget.consume(len(chunk))
                    if len(payload) > 2 * 1024 * 1024:
                        raise SourceError("SOURCE_PAGE_TOO_LARGE")
                budget.finish_read()
        except HTTPError as error:
            try:
                if error.code == 429:
                    delay = error.headers.get("Retry-After", "60")
                    raise SourceRateLimited(min(int(delay), 86400) if delay.isdigit() else 60) from None
                code = {401: "SOURCE_AUTHENTICATION_FAILED", 403: "SOURCE_PERMISSION_DENIED",
                        404: "SOURCE_ENDPOINT_UNAVAILABLE", 410: "SOURCE_CURSOR_EXPIRED"}.get(
                            error.code, "SOURCE_REQUEST_FAILED"
                        )
                raise SourceError(code) from None
            finally:
                error.close()
        except (URLError, TimeoutError, OSError, ValueError):
            if self.monotonic() >= budget.deadline:
                raise SourceError("SOURCE_OPERATION_DEADLINE_EXCEEDED") from None
            raise SourceError("SOURCE_UNAVAILABLE") from None
        try:
            document = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            raise SourceError("SOURCE_RESPONSE_INVALID_JSON") from None
        if not isinstance(document, dict):
            raise SourceError("SOURCE_SCHEMA_DRIFT")
        budget.remaining()
        return document

    def _read_async(
        self, url: str, *, headers: dict[str, str], budget: SourceExecutionBudget,
    ) -> bytes:
        import anyio

        budget.begin_read()

        async def send_request() -> bytes:
            try:
                with anyio.fail_after(budget.remaining()):
                    async with httpx.AsyncClient(
                        transport=self.async_transport,
                        timeout=min(self.timeout, budget.remaining()), follow_redirects=False,
                        verify=ssl.create_default_context(cafile=self.settings.ca_bundle),
                        trust_env=False,
                    ) as client:
                        async with client.stream(
                            "GET", url, headers=headers, timeout=min(self.timeout, budget.remaining())
                        ) as response:
                            if response.status_code == 429:
                                delay = response.headers.get("Retry-After", "60")
                                raise SourceRateLimited(
                                    min(int(delay), 86400) if delay.isdigit() else 60
                                )
                            if 300 <= response.status_code < 400:
                                raise SourceError("SOURCE_REDIRECT_REFUSED")
                            if response.status_code != 200:
                                code = {
                                    401: "SOURCE_AUTHENTICATION_FAILED",
                                    403: "SOURCE_PERMISSION_DENIED",
                                    404: "SOURCE_ENDPOINT_UNAVAILABLE",
                                    410: "SOURCE_CURSOR_EXPIRED",
                                }.get(response.status_code, "SOURCE_REQUEST_FAILED")
                                raise SourceError(code)
                            payload = bytearray()
                            async for chunk in response.aiter_bytes():
                                if len(payload) + len(chunk) > 2 * 1024 * 1024:
                                    raise SourceError("SOURCE_PAGE_TOO_LARGE")
                                payload.extend(chunk)
                                budget.consume(len(chunk))
                            budget.finish_read()
                            return bytes(payload)
            except TimeoutError:
                raise SourceError("SOURCE_OPERATION_DEADLINE_EXCEEDED") from None
            except (httpx.HTTPError, OSError):
                if budget.monotonic() >= budget.deadline:
                    raise SourceError("SOURCE_OPERATION_DEADLINE_EXCEEDED") from None
                raise SourceError("SOURCE_UNAVAILABLE") from None

        return anyio.run(send_request)

    def parse_page(self, document: Any) -> SourcePage:
        if not isinstance(document, dict) or not isinstance(document.get("value"), list):
            raise SourceError("SOURCE_SCHEMA_DRIFT")
        next_link = document.get("@odata.nextLink")
        delta_link = document.get("@odata.deltaLink")
        if bool(next_link) == bool(delta_link) or not isinstance(next_link or delta_link, str):
            raise SourceError("SOURCE_CHANGE_TRACKING_REQUIRED")
        cursor = self._validate_url(next_link or delta_link)
        allowed = {value.lower() for value in self.settings.record_ids}
        records = []
        for row in document["value"]:
            if not isinstance(row, dict):
                raise SourceError("SOURCE_SCHEMA_DRIFT")
            deleted = row.get("reason") == "deleted" and str(row.get("@odata.context", "")).endswith(
                "/$deletedEntity"
            )
            identity = row.get("id") if deleted else row.get("quoteid")
            if not isinstance(identity, str):
                raise SourceError("SOURCE_RECORD_ID_MISSING")
            if identity.lower() not in allowed:
                continue
            if deleted:
                records.append({"record_id": identity.lower(), "deleted": True})
                continue
            records.append(self._live_record(row))
        return SourcePage(tuple(records), cursor, bool(next_link))

    def _live_record(self, row: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(row.get("@odata.etag"), str) or not row["@odata.etag"]:
            raise SourceError("SOURCE_REVISION_MISSING")
        missing = sorted(set(self.settings.fields) - set(row))
        if missing:
            raise SourceError(f"SOURCE_FIELDS_UNAVAILABLE:{','.join(missing)}")
        return {"record_id": row["quoteid"].lower(), "revision": row["@odata.etag"],
                "fields": {key: row[key] for key in self.settings.fields},
                "modified_at": row.get("modifiedon"), "deleted": False}


class SourceSynchronizer:
    """Persist a fetched page before admission; acknowledge only the admitted page."""

    def __init__(
        self, store: StateStore, reader: DataverseReader, *, worker_id: str,
        admit: Callable[[Any, Mapping[str, Any], str, str], None], admission_digest: str,
        clock: Clock | None = None,
        retention_seconds: int = 86_400,
        page_committed: Callable[[Any, dict[str, Any], dict[str, Any], str], None] | None = None,
        page_failed: Callable[[Any, dict[str, Any], BaseException], None] | None = None,
        verify_page: Callable[[SourcePage], SourcePage] | None = None,
        lease_seconds: float = 120,
        max_lease_renewals: int = 72,
    ) -> None:
        if store.tenant_id != reader.settings.tenant_id:
            raise SourceError("SOURCE_STORE_TENANT_MISMATCH")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", admission_digest):
            raise SourceError("SOURCE_ADMISSION_DIGEST_REQUIRED")
        self.store = store
        self.reader = reader
        self.worker_id = worker_id
        self.admit = admit
        self.admission_digest = admission_digest
        self.clock = clock or SystemClock()
        self.page_committed = page_committed
        self.page_failed = page_failed
        self.verify_page = verify_page
        if not 1 <= lease_seconds <= 600 or not 1 <= max_lease_renewals <= 256:
            raise SourceError("SOURCE_LEASE_POLICY_INVALID")
        self.lease_seconds = lease_seconds
        self.max_lease_renewals = max_lease_renewals
        if retention_seconds == 0:
            raise SourceError("SOURCE_DURABLE_INBOX_RETENTION_REQUIRED")
        self.inbox = PrivateRecordStore(store, self.clock, retention_seconds=retention_seconds)

    def _renew_claim(
        self, claim: dict[str, Any], *, binding_id: str, binding: str,
        budget: SourceExecutionBudget, renewals: list[int], max_lease_until: float,
    ) -> None:
        try:
            current_binding = self.store.load_artifact(binding_id, "application/json").payload
        except KeyError:
            raise SourceError("SOURCE_BINDING_CHANGED_REQUALIFICATION_REQUIRED") from None
        if current_binding != {"digest": binding}:
            raise SourceError("SOURCE_BINDING_CHANGED_REQUALIFICATION_REQUIRED")
        remaining = budget.remaining()
        now = utc_datetime(self.clock.now()).timestamp()
        # Avoid a database write for every fast chunk/request. Renew only when
        # the current lease cannot cover one complete bounded I/O plus commit
        # grace. Slow reads still renew under the same fence before they start.
        required = min(self.reader.timeout, remaining) + 5.0
        if claim.get("lease_until") is not None and claim["lease_until"] > now + required:
            return
        if renewals[0] >= self.max_lease_renewals:
            raise SourceError("SOURCE_LEASE_RENEWAL_BUDGET_EXCEEDED")
        # Never turn a finite operation into an indefinitely extending lease.
        # The small grace covers the final inbox/checkpoint transaction only.
        extension = min(self.lease_seconds, max(1.0, remaining + 5.0))
        with self.store.transaction() as connection:
            renewed = self.store.renew_source_claim(
                connection,
                connector_id=self.reader.settings.connector_id,
                worker_id=self.worker_id,
                fence=claim["fence"],
                expected_revision=claim["revision"],
                expected_cursor=claim["cursor"],
                now=now,
                lease_seconds=extension,
                max_lease_until=max_lease_until,
            )
        if renewed is None:
            raise SourceError("SOURCE_STALE_CLAIM")
        claim.update(renewed)
        renewals[0] += 1

    def sync_page(self) -> dict[str, Any]:
        connector_id = self.reader.settings.connector_id
        now = utc_datetime(self.clock.now())
        max_lease_until = now.timestamp() + self.reader.operation_timeout + 5.0
        binding = sha256_digest({
            "source": self.reader.settings.model_dump(mode="json"),
            "admission_digest": self.admission_digest,
        })
        with self.store.transaction() as connection:
            binding_id = f"source-binding:{connector_id}"
            try:
                saved = self.store.load_artifact(binding_id, "application/json").payload
            except KeyError:
                self.store.save_artifact(
                    connection, binding_id, "application/json", {"digest": binding}
                )
            else:
                if saved != {"digest": binding}:
                    raise SourceError("SOURCE_BINDING_CHANGED_REQUALIFICATION_REQUIRED")
            claim = self.store.claim_source(
                connection, connector_id=connector_id, worker_id=self.worker_id,
                now=now.timestamp(),
                lease_seconds=min(self.lease_seconds, self.reader.operation_timeout + 5.0),
            )
            if claim is None:
                raise SourceError("SOURCE_SYNC_IN_PROGRESS")
        page_id = "source-page:" + sha256_digest({
            "connector": connector_id, "revision": claim["revision"], "binding": binding,
        }).removeprefix("sha256:")
        renewals = [0]
        try:
            with self.reader.execution_budget(
                on_progress=lambda: self._renew_claim(
                    claim, binding_id=binding_id, binding=binding,
                    budget=budget, renewals=renewals, max_lease_until=max_lease_until,
                )
            ) as budget:
                inbox = self.inbox.read(page_id)
                if inbox is None:
                    if self.inbox.record_status(page_id) != "MISSING":
                        raise SourceError("SOURCE_INBOX_UNAVAILABLE_RECONCILIATION_REQUIRED")
                    page = self.reader.fetch(claim["cursor"])
                    if self.verify_page is not None:
                        page = self.verify_page(page)
                    inbox = {
                        "connector_id": connector_id, "binding_digest": binding,
                        "previous_cursor": claim["cursor"], "cursor": page.cursor,
                        "records": list(page.records), "more": page.more,
                        "observed_at": self.clock.now(),
                        **({"readback": page.readback} if page.readback is not None else {}),
                    }
                    self._renew_claim(
                        claim, binding_id=binding_id, binding=binding,
                        budget=budget, renewals=renewals, max_lease_until=max_lease_until,
                    )
                    with self.store.transaction() as connection:
                        current = self.store.get_source_checkpoint(connector_id, connection=connection)
                        if (
                            current is None or current["lease_owner"] != self.worker_id
                            or current["fence"] != claim["fence"]
                            or current["revision"] != claim["revision"]
                            or current["cursor"] != claim["cursor"]
                            or current["lease_until"] is None
                            or current["lease_until"] <= utc_datetime(self.clock.now()).timestamp()
                        ):
                            raise SourceError("SOURCE_STALE_CLAIM") from None
                        self.inbox.write(
                            connection, record_id=page_id, scope_ref=binding_id,
                            owner_id=connector_id, payload=inbox,
                        )
                self._renew_claim(
                    claim, binding_id=binding_id, binding=binding,
                    budget=budget, renewals=renewals, max_lease_until=max_lease_until,
                )
                with self.store.transaction() as connection:
                    for record in inbox["records"]:
                        self.admit(connection, record, inbox["observed_at"], page_id)
                    checkpoint = self.store.commit_source_page(
                        connection, connector_id=connector_id, expected_cursor=claim["cursor"],
                        cursor=inbox["cursor"], worker_id=self.worker_id, fence=claim["fence"],
                        now=utc_datetime(self.clock.now()).timestamp(),
                    )
                    if self.page_committed is not None:
                        self.page_committed(connection, inbox, checkpoint, page_id)
        except BaseException as error:
            try:
                with self.store.transaction() as connection:
                    if self.page_failed is not None:
                        # Admission owns business lock ordering and must fence
                        # this exact claim before recording source unavailability.
                        self.page_failed(connection, claim, error)
                    self.store.release_source_claim(
                        connection, connector_id=connector_id, worker_id=self.worker_id,
                        fence=claim["fence"],
                    )
            except BaseException:
                # A failed error projection rolls back as a unit. Retain the
                # original failure, and release only this worker's own fence.
                error.add_note("SOURCE_FAILURE_RECORDING_FAILED")
                try:
                    with self.store.transaction() as connection:
                        self.store.release_source_claim(
                            connection, connector_id=connector_id, worker_id=self.worker_id,
                            fence=claim["fence"],
                        )
                except BaseException:
                    error.add_note("SOURCE_CLAIM_RELEASE_FAILED")
            raise
        return {
            "connector_id": connector_id, "revision": checkpoint["revision"],
            "records_admitted": len(inbox["records"]), "more": inbox["more"],
            "page_ref": page_id, "external_writes": 0,
        }
