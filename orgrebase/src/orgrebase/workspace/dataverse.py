"""Read-only Dataverse change tracking with durable, bounded page admission."""

from __future__ import annotations

import json
import re
import ssl
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

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
    ) -> None:
        if not 0 < timeout <= 30:
            raise ValueError("SOURCE_TIMEOUT_INVALID")
        self.settings = settings
        self.token = token
        self.timeout = timeout
        self.opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context(
            cafile=settings.ca_bundle)), _NoRedirect())

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
        url = self._validate_url(cursor or self.settings.initial_url)
        return self.parse_page(self._read(url, tracking=True))

    def metadata(self, path: str) -> dict[str, Any]:
        if len(path) > 8192 or not re.fullmatch(r"/(?:WhoAmI|EntityDefinitions(?:\([^\r\n]*\))?(?:/Attributes(?:/Microsoft\.Dynamics\.CRM\.[A-Za-z]+AttributeMetadata)?)?(?:\?[^\r\n]*)?)", path):
            raise SourceError("SOURCE_METADATA_PATH_FORBIDDEN")
        return self._read(self.settings.instance_url.rstrip('/') + '/api/data/v9.2' + path)

    def read_record(self, record_id: str) -> dict[str, Any]:
        if record_id not in self.settings.record_ids:
            raise SourceError("SOURCE_RECORD_SCOPE_MISMATCH")
        document = self._read(self.settings.endpoint + f"({record_id})?" + urlencode({
            "$select": ",".join(sorted({"quoteid", "modifiedon", *self.settings.fields})),
        }))
        if document.get("quoteid") != record_id:
            raise SourceError("SOURCE_READBACK_IDENTITY_MISMATCH")
        return self._live_record(document)

    def _read(self, url: str, *, tracking: bool = False) -> dict[str, Any]:
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
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise SourceError("SOURCE_RESPONSE_UNEXPECTED")
                payload = response.read(2 * 1024 * 1024 + 1)
                if len(payload) > 2 * 1024 * 1024:
                    raise SourceError("SOURCE_PAGE_TOO_LARGE")
        except HTTPError as error:
            if error.code == 429:
                delay = error.headers.get("Retry-After", "60")
                raise SourceRateLimited(min(int(delay), 86400) if delay.isdigit() else 60) from None
            code = {401: "SOURCE_AUTHENTICATION_FAILED", 403: "SOURCE_PERMISSION_DENIED",
                    404: "SOURCE_ENDPOINT_UNAVAILABLE", 410: "SOURCE_CURSOR_EXPIRED"}.get(
                        error.code, "SOURCE_REQUEST_FAILED"
                    )
            raise SourceError(code) from None
        except (URLError, TimeoutError, OSError):
            raise SourceError("SOURCE_UNAVAILABLE") from None
        try:
            document = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            raise SourceError("SOURCE_RESPONSE_INVALID_JSON") from None
        if not isinstance(document, dict):
            raise SourceError("SOURCE_SCHEMA_DRIFT")
        return document

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
        verify_page: Callable[[SourcePage], SourcePage] | None = None,
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
        self.verify_page = verify_page
        if retention_seconds == 0:
            raise SourceError("SOURCE_DURABLE_INBOX_RETENTION_REQUIRED")
        self.inbox = PrivateRecordStore(store, self.clock, retention_seconds=retention_seconds)

    def sync_page(self) -> dict[str, Any]:
        connector_id = self.reader.settings.connector_id
        now = utc_datetime(self.clock.now())
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
                now=now.timestamp(), lease_seconds=120,
            )
            if claim is None:
                raise SourceError("SOURCE_SYNC_IN_PROGRESS")
        page_id = "source-page:" + sha256_digest({
            "connector": connector_id, "revision": claim["revision"], "binding": binding,
        }).removeprefix("sha256:")
        try:
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
        except BaseException:
            with self.store.transaction() as connection:
                self.store.release_source_claim(
                    connection, connector_id=connector_id, worker_id=self.worker_id,
                    fence=claim["fence"],
                )
            raise
        return {
            "connector_id": connector_id, "revision": checkpoint["revision"],
            "records_admitted": len(inbox["records"]), "more": inbox["more"],
            "page_ref": page_id, "external_writes": 0,
        }
