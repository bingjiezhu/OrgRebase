"""Conditional draft metadata updates with a transactional Dataverse receipt."""

from __future__ import annotations

import json
import re
import ssl
import time
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from functools import wraps
from types import TracebackType
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx2 as httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from orgrebase.clock import timestamp, utc_datetime
from orgrebase.commit_gateway import EffectError, EffectRequest, ResolutionState, TargetResolution
from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.effect_identity import DATAVERSE_DRAFT_ACTION, dataverse_target_identity

_FIELDS = {"name": 300, "description": 2000}
_ETAG = re.compile(r'W/"[^"\r\n]{1,200}"')


@dataclass
class TargetExecutionBudget:
    """A total deadline and resource budget across one target command."""

    max_elapsed: float
    max_requests: int
    max_bytes: int
    monotonic: Callable[[], float]
    requests: int = 0
    bytes_read: int = 0

    def __post_init__(self) -> None:
        if (
            not 0 < self.max_elapsed <= 600
            or not 1 <= self.max_requests <= 64
            or not 1 <= self.max_bytes <= 32 * 1024 * 1024
        ):
            raise ValueError("TARGET_EXECUTION_BUDGET_INVALID")
        self.deadline = self.monotonic() + self.max_elapsed

    def remaining(self) -> float:
        remaining = self.deadline - self.monotonic()
        if remaining <= 0:
            raise EffectError("TARGET_OPERATION_DEADLINE_EXCEEDED")
        return remaining

    def begin_request(self) -> float:
        if self.requests >= self.max_requests:
            raise EffectError("TARGET_OPERATION_REQUEST_BUDGET_EXCEEDED")
        self.requests += 1
        return self.remaining()

    def consume(self, size: int) -> None:
        self.bytes_read += size
        if self.bytes_read > self.max_bytes:
            raise EffectError("TARGET_OPERATION_BYTE_BUDGET_EXCEEDED")
        self.remaining()

    def expired(self) -> bool:
        return self.monotonic() >= self.deadline


_TARGET_BUDGET: ContextVar[tuple[int, TargetExecutionBudget] | None] = ContextVar(
    "target_execution_budget", default=None
)


def _bounded_operation(method: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(method)
    def invoke(self: DataverseDraftTarget, *args: Any, **kwargs: Any) -> Any:
        with self.execution_budget():
            return method(self, *args, **kwargs)

    return invoke


class DataverseDraftTargetSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    tenant_id: str = Field(min_length=1, max_length=256)
    instance_url: str
    quote_id: str
    receipt_entity_set: str = Field(default="orgrebase_effectreceipts", pattern=r"^[a-z][a-z0-9_]{1,100}$")
    receipt_primary_key: str = Field(default="orgrebase_effectreceiptid", pattern=r"^[a-z][a-z0-9_]{1,100}$")
    ca_bundle: str | None = Field(default=None, min_length=1, max_length=4096)

    @model_validator(mode="after")
    def valid_target(self) -> DataverseDraftTargetSettings:
        url = urlsplit(self.instance_url)
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or url.path not in {"", "/"} or url.query or url.fragment):
            raise ValueError("DATAVERSE_HTTPS_ORIGIN_REQUIRED")
        if str(UUID(self.quote_id)) != self.quote_id:
            raise ValueError("DATAVERSE_CANONICAL_QUOTE_ID_REQUIRED")
        dataverse_target_identity(self.target_key)
        return self

    @property
    def api_url(self) -> str:
        return f"{self.instance_url.rstrip('/')}/api/data/v9.2"

    @property
    def target_key(self) -> str:
        return f"{self.api_url}/quotes({self.quote_id})"


class DataverseDraftTarget:
    """One narrow action: change draft name/description without pricing writes.

    An append-only receipt table must be provisioned in the same Dataverse
    environment. Its primary key fences both late writes and explicit cancels.
    A missing receipt is UNKNOWN, never permission to resend an operation.
    Each command owns and closes its adapter; request credentials stay live.
    """

    action = DATAVERSE_DRAFT_ACTION

    def __init__(self, settings: DataverseDraftTargetSettings, token: Callable[[], str],
                 *, transport: httpx.BaseTransport | None = None,
                 async_transport: httpx.AsyncBaseTransport | None = None,
                 operation_timeout: float = 60, max_requests: int = 16,
                 max_operation_bytes: int = 8 * 1024 * 1024,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        if (
            not 0 < operation_timeout <= 600
            or not 1 <= max_requests <= 64
            or not 1 <= max_operation_bytes <= 32 * 1024 * 1024
        ):
            raise ValueError("TARGET_EXECUTION_BUDGET_INVALID")
        if transport is not None and async_transport is not None:
            raise ValueError("TARGET_TRANSPORT_AMBIGUOUS")
        self.settings = settings
        self.token = token
        self.transport = transport
        self.async_transport = async_transport
        self.operation_timeout = operation_timeout
        self.max_requests = max_requests
        self.max_operation_bytes = max_operation_bytes
        self.monotonic = monotonic
        self._client: httpx.Client | None = None
        self._closed = False

    @contextmanager
    def execution_budget(self):
        current = _TARGET_BUDGET.get()
        if current is not None and current[0] == id(self):
            yield current[1]
            return
        budget = TargetExecutionBudget(
            self.operation_timeout, self.max_requests, self.max_operation_bytes, self.monotonic
        )
        token = _TARGET_BUDGET.set((id(self), budget))
        try:
            yield budget
        finally:
            _TARGET_BUDGET.reset(token)

    def _active_budget(self) -> TargetExecutionBudget:
        current = _TARGET_BUDGET.get()
        if current is not None and current[0] == id(self):
            return current[1]
        return TargetExecutionBudget(
            self.operation_timeout, self.max_requests, self.max_operation_bytes, self.monotonic
        )

    def __enter__(self) -> DataverseDraftTarget:
        if self._closed:
            raise EffectError("TARGET_CLIENT_CLOSED")
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 traceback: TracebackType | None) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            if self._client is not None:
                self._client.close()

    def _http_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                transport=self.transport, timeout=20, follow_redirects=False,
                verify=ssl.create_default_context(cafile=self.settings.ca_bundle), trust_env=False,
            )
        return self._client

    def _request(self, method: str, path: str, *, body: bytes | None = None,
                 content_type: str = "application/json", prefer: str | None = None) -> tuple[int, dict[str, str], bytes]:
        # Paths are constructed from validated server configuration, never a response link.
        if not path.startswith("/") or path.startswith("//") or "\r" in path or "\n" in path:
            raise EffectError("TARGET_PATH_INVALID")
        if self._closed:
            raise EffectError("TARGET_CLIENT_CLOSED")
        budget = self._active_budget()
        budget.begin_request()
        token = self.token()
        if not token or any(character in token for character in "\r\n"):
            raise EffectError("TARGET_CREDENTIAL_UNAVAILABLE")
        budget.remaining()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json",
                   "OData-Version": "4.0", "OData-MaxVersion": "4.0", "Content-Type": content_type}
        if prefer is not None:
            if prefer != "odata.maxpagesize=100":
                raise EffectError("TARGET_PREFERENCE_UNSUPPORTED")
            headers["Prefer"] = prefer
        if self.transport is None or self.async_transport is not None:
            return self._request_async(
                method, path, headers=headers, body=body, budget=budget,
            )
        try:
            client = self._http_client()
            client.cookies.clear()
            with client.stream(
                method, self.settings.api_url + path, headers=headers, content=body,
                timeout=min(20, budget.remaining()),
            ) as response:
                content = bytearray()
                iterator = iter(response.iter_bytes())
                while True:
                    budget.remaining()
                    try:
                        part = next(iterator)
                    except StopIteration:
                        break
                    content.extend(part)
                    budget.consume(len(part))
                    if len(content) > 2 * 1024 * 1024:
                        raise EffectError("TARGET_RESPONSE_TOO_LARGE")
                budget.remaining()
                if 300 <= response.status_code < 400:
                    raise EffectError("TARGET_REDIRECT_REFUSED")
                return response.status_code, dict(response.headers), bytes(content)
        except (httpx.HTTPError, OSError):
            if budget.expired():
                raise EffectError("TARGET_OPERATION_DEADLINE_EXCEEDED") from None
            raise EffectError("TARGET_RESPONSE_UNAVAILABLE") from None

    def _request_async(
        self, method: str, path: str, *, headers: dict[str, str], body: bytes | None,
        budget: TargetExecutionBudget,
    ) -> tuple[int, dict[str, str], bytes]:
        import anyio

        async def send_request() -> tuple[int, dict[str, str], bytes]:
            try:
                with anyio.fail_after(budget.remaining()):
                    async with httpx.AsyncClient(
                        transport=self.async_transport,
                        timeout=min(20, budget.remaining()), follow_redirects=False,
                        verify=ssl.create_default_context(cafile=self.settings.ca_bundle),
                        trust_env=False,
                    ) as client:
                        async with client.stream(
                            method, self.settings.api_url + path, headers=headers, content=body,
                            timeout=min(20, budget.remaining()),
                        ) as response:
                            content = bytearray()
                            async for part in response.aiter_bytes():
                                content.extend(part)
                                budget.consume(len(part))
                                if len(content) > 2 * 1024 * 1024:
                                    raise EffectError("TARGET_RESPONSE_TOO_LARGE")
                            budget.remaining()
                            if 300 <= response.status_code < 400:
                                raise EffectError("TARGET_REDIRECT_REFUSED")
                            return response.status_code, dict(response.headers), bytes(content)
            except TimeoutError:
                raise EffectError("TARGET_OPERATION_DEADLINE_EXCEEDED") from None
            except (httpx.HTTPError, OSError):
                if budget.expired():
                    raise EffectError("TARGET_OPERATION_DEADLINE_EXCEEDED") from None
                raise EffectError("TARGET_RESPONSE_UNAVAILABLE") from None

        return anyio.run(send_request)

    @_bounded_operation
    def read_metadata(self, path: str) -> dict[str, Any]:
        if len(path) > 8192 or not re.match(r"^/(?:WhoAmI$|EntityDefinitions(?:[(/?]|$))", path):
            raise EffectError("TARGET_METADATA_PATH_FORBIDDEN")
        status, _, raw = self._request("GET", path)
        if status != 200:
            raise EffectError("TARGET_METADATA_UNAVAILABLE")
        document = self._json(raw)
        self._active_budget().remaining()
        return document

    @_bounded_operation
    def effect_inventory_page(self, *, since: str, until: str, cursor: str | None = None) -> dict[str, Any]:
        """Enumerate target receipts so recovery can discover lost local intents."""
        start, end = utc_datetime(since), utc_datetime(until)
        if start >= end:
            raise EffectError("TARGET_INVENTORY_INTERVAL_INVALID")
        fields = [self.settings.receipt_primary_key, "createdon", "orgrebase_effectid", "orgrebase_requestdigest",
                  "orgrebase_tenantid", "orgrebase_targetkey", "orgrebase_predecessorversion",
                  "orgrebase_approvaldigest", "orgrebase_payloadhash", "orgrebase_outcome"]
        tenant = self.settings.tenant_id.replace("'", "''")
        target = self.settings.target_key.replace("'", "''")
        parameters = {
            "$select": ",".join(fields),
            "$filter": f"orgrebase_tenantid eq '{tenant}' and orgrebase_targetkey eq '{target}' and createdon ge {timestamp(start)} and createdon lt {timestamp(end)}",
            "$orderby": "createdon asc," + self.settings.receipt_primary_key + " asc",
        }
        path = "/" + self.settings.receipt_entity_set + "?" + urlencode(parameters)

        def continuation(value: Any) -> str:
            if not isinstance(value, str) or len(value) > 32_768 or any(character in value for character in "\r\n"):
                raise EffectError("TARGET_INVENTORY_CURSOR_INVALID")
            link = urlsplit(value)
            origin = urlsplit(self.settings.api_url)
            if (link.scheme != origin.scheme or link.netloc != origin.netloc or link.fragment
                    or link.path != origin.path + "/" + self.settings.receipt_entity_set):
                raise EffectError("TARGET_INVENTORY_CURSOR_SCOPE_MISMATCH")
            pairs = parse_qsl(link.query, keep_blank_values=True)
            query = dict(pairs)
            if (len(query) != len(pairs) or set(query) != {*parameters, "$skiptoken"}
                    or not query["$skiptoken"] or any(query[key] != value for key, value in parameters.items())):
                raise EffectError("TARGET_INVENTORY_CURSOR_QUERY_MISMATCH")
            return "/" + self.settings.receipt_entity_set + "?" + link.query

        if cursor is not None:
            path = continuation(cursor)
        status, _, raw = self._request("GET", path, prefer="odata.maxpagesize=100")
        if status != 200:
            raise EffectError("TARGET_INVENTORY_UNAVAILABLE")
        document = self._json(raw)
        records = document.get("value")
        if not isinstance(records, list) or len(records) > 100:
            raise EffectError("TARGET_INVENTORY_PAGE_INVALID")
        results = []
        for record in records:
            if not isinstance(record, dict) or any(key not in record for key in fields):
                raise EffectError("TARGET_INVENTORY_RECORD_INVALID")
            if (record["orgrebase_tenantid"] != self.settings.tenant_id
                    or record["orgrebase_targetkey"] != self.settings.target_key
                    or not isinstance(record["orgrebase_outcome"], str)
                    or record["orgrebase_outcome"] not in {"CONFIRMED", "REJECTED"}):
                raise EffectError("TARGET_INVENTORY_RECORD_SCOPE_MISMATCH")
            try:
                moment = utc_datetime(record["createdon"])
                valid_id = str(UUID(record[self.settings.receipt_primary_key])) == record[self.settings.receipt_primary_key]
            except (ValueError, TypeError, AttributeError):
                raise EffectError("TARGET_INVENTORY_RECORD_INVALID") from None
            if (not valid_id or not start <= moment < end or not isinstance(record["orgrebase_effectid"], str)
                    or not 1 <= len(record["orgrebase_effectid"]) <= 512
                    or any(not isinstance(record[key], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", record[key])
                           for key in ("orgrebase_requestdigest", "orgrebase_approvaldigest", "orgrebase_payloadhash"))
                    or not isinstance(record["orgrebase_predecessorversion"], str)
                    or not _ETAG.fullmatch(record["orgrebase_predecessorversion"])):
                raise EffectError("TARGET_INVENTORY_RECORD_INVALID")
            expected_id = str(uuid5(NAMESPACE_URL, canonical_json({"target": self.settings.api_url,
                              "tenant": self.settings.tenant_id, "effect": record["orgrebase_effectid"]})))
            if record[self.settings.receipt_primary_key] != expected_id:
                raise EffectError("TARGET_INVENTORY_RECEIPT_ID_MISMATCH")
            results.append({key: record[key] for key in fields})
        next_link = document.get("@odata.nextLink")
        if next_link is not None:
            continuation(next_link)
        self._active_budget().remaining()
        return {"items": results, "next_cursor": next_link,
                "interval": {"since": timestamp(start), "until": timestamp(end)}}

    @staticmethod
    def _json(raw: bytes) -> dict[str, Any]:
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            raise EffectError("TARGET_RESPONSE_INVALID_JSON") from None
        if not isinstance(value, dict):
            raise EffectError("TARGET_RESPONSE_INVALID_JSON")
        return value

    def validate_request(self, effect: EffectRequest) -> dict[str, str]:
        if effect.tenant_id != self.settings.tenant_id or effect.target_key != self.settings.target_key:
            raise EffectError("TARGET_BINDING_MISMATCH")
        if effect.action != self.action or not _ETAG.fullmatch(effect.expected_version):
            raise EffectError("TARGET_ACTION_OR_VERSION_INVALID")
        if set(effect.payload) != {"changes"} or not isinstance(effect.payload["changes"], dict):
            raise EffectError("TARGET_PAYLOAD_INVALID")
        changes = effect.payload["changes"]
        if not changes or not set(changes).issubset(_FIELDS) or any(
            not isinstance(value, str) or not value.strip() or len(value) > _FIELDS[field]
            for field, value in changes.items()
        ):
            raise EffectError("TARGET_DRAFT_FIELDS_INVALID")
        return changes

    def receipt_id(self, effect: EffectRequest) -> str:
        return str(uuid5(NAMESPACE_URL, canonical_json({"target": self.settings.api_url,
                         "tenant": effect.tenant_id, "effect": effect.effect_id})))

    def _receipt(self, effect: EffectRequest, outcome: str) -> dict[str, Any]:
        return {
            self.settings.receipt_primary_key: self.receipt_id(effect),
            "orgrebase_name": self.receipt_id(effect),
            "orgrebase_effectid": effect.effect_id,
            "orgrebase_requestdigest": effect.digest,
            "orgrebase_tenantid": effect.tenant_id,
            "orgrebase_targetkey": effect.target_key,
            "orgrebase_predecessorversion": effect.expected_version,
            "orgrebase_approvaldigest": effect.approval_digest,
            "orgrebase_payloadhash": sha256_digest(effect.payload),
            "orgrebase_outcome": outcome,
        }

    @_bounded_operation
    def query_effect(self, effect: EffectRequest) -> TargetResolution:
        self.validate_request(effect)
        path = f"/{self.settings.receipt_entity_set}({self.receipt_id(effect)})"
        status, _, raw = self._request("GET", path)
        if status == 404:
            return self._resolution(effect, ResolutionState.UNKNOWN, reason="TARGET_RECEIPT_NOT_OBSERVED")
        if status != 200:
            raise EffectError("TARGET_RECEIPT_LOOKUP_UNAVAILABLE")
        document = self._json(raw)
        outcome = document.get("orgrebase_outcome")
        if outcome not in {"CONFIRMED", "REJECTED"}:
            raise EffectError("TARGET_RECEIPT_OUTCOME_INVALID")
        expected = self._receipt(effect, outcome)
        if any(document.get(key) != value for key, value in expected.items()):
            raise EffectError("TARGET_RECEIPT_BINDING_MISMATCH")
        evidence = {"receipt_id": self.receipt_id(effect), "request_digest": effect.digest,
                    "target_key": effect.target_key, "outcome": outcome,
                    "receipt_digest": sha256_digest(expected)}
        self._active_budget().remaining()
        return self._resolution(effect, ResolutionState(outcome), evidence=evidence,
                                reason="TARGET_EFFECT_CANCELLED" if outcome == "REJECTED" else None)

    @_bounded_operation
    def draft(self) -> dict[str, Any]:
        status, _, raw = self._request("GET", f"/quotes({self.settings.quote_id})?$select=quoteid,statecode,name,description")
        if status != 200:
            raise EffectError("TARGET_QUOTE_UNAVAILABLE")
        value = self._json(raw)
        if value.get("quoteid") != self.settings.quote_id or not _ETAG.fullmatch(str(value.get("@odata.etag", ""))):
            raise EffectError("TARGET_QUOTE_BINDING_INVALID")
        if type(value.get("statecode")) is not int or value["statecode"] != 0:
            raise EffectError("TARGET_QUOTE_NOT_DRAFT")
        fields = {name: value.get(name) for name in _FIELDS}
        # Readback becomes immutable evidence. Do not persist arbitrary nested
        # data or coerce it into text. Existing tenant text can exceed our write limits.
        if any(field is not None and not isinstance(field, str) for field in fields.values()):
            raise EffectError("TARGET_QUOTE_FIELDS_INVALID")
        self._active_budget().remaining()
        return {"target_key": self.settings.target_key, "version": value["@odata.etag"],
                "fields": fields}

    @_bounded_operation
    def execute(self, effect: EffectRequest) -> TargetResolution:
        changes = self.validate_request(effect)
        prior = self.query_effect(effect)
        if prior.state != ResolutionState.UNKNOWN:
            return prior
        draft = self.draft()
        if draft["version"] != effect.expected_version:
            return self._resolution(effect, ResolutionState.REJECTED, reason="TARGET_VERSION_CONFLICT",
                                    evidence={"observed_version": draft["version"], "writes_sent": 0})
        if all(draft["fields"].get(name) == value for name, value in changes.items()):
            return self._resolution(effect, ResolutionState.REJECTED, reason="TARGET_NO_SEMANTIC_DELTA",
                                    evidence={"observed_version": draft["version"], "writes_sent": 0})
        boundary, body = self._batch(effect, changes)
        status, headers, raw = self._request("POST", "/$batch", body=body,
                                            content_type=f"multipart/mixed; boundary={boundary}")
        # HTTP 200 can represent an empty batch or a failed inner operation.
        observed = self.query_effect(effect)
        if observed.state != ResolutionState.UNKNOWN:
            return observed
        conditional_rejection = status == 200 and self._conditional_rejection(
            headers.get("content-type", ""), raw
        )
        self._active_budget().remaining()
        if conditional_rejection:
            return self._resolution(effect, ResolutionState.REJECTED, reason="TARGET_VERSION_CONFLICT",
                                    evidence={"atomic_changeset_rejected": True, "http_status": 412})
        return observed

    @_bounded_operation
    def cancel_effect(self, effect: EffectRequest) -> TargetResolution:
        """Fence a missing/in-flight effect by claiming the same target receipt key.

        The tombstone wins or the original transaction wins; it cannot report
        absence from a GET and then permit a delayed write to commit afterwards.
        """
        self.validate_request(effect)
        current = self.query_effect(effect)
        if current.state != ResolutionState.UNKNOWN:
            return current
        self._request("POST", f"/{self.settings.receipt_entity_set}",
                      body=canonical_json(self._receipt(effect, "REJECTED")).encode())
        return self.query_effect(effect)

    def _batch(self, effect: EffectRequest, changes: dict[str, str]) -> tuple[str, bytes]:
        boundary, changeset = f"batch_{uuid4().hex}", f"changeset_{uuid4().hex}"
        operations = [
            ("POST", f"/api/data/v9.2/{self.settings.receipt_entity_set}", {}, self._receipt(effect, "CONFIRMED")),
            ("PATCH", f"/api/data/v9.2/quotes({self.settings.quote_id})",
             {"If-Match": effect.expected_version}, changes),
        ]
        lines = [f"--{boundary}", f"Content-Type: multipart/mixed; boundary={changeset}", ""]
        for index, (method, path, headers, payload) in enumerate(operations, 1):
            lines.extend([f"--{changeset}", "Content-Type: application/http", "Content-Transfer-Encoding: binary",
                          f"Content-ID: {index}", "", f"{method} {path} HTTP/1.1", "Content-Type: application/json",
                          "OData-Version: 4.0", "OData-MaxVersion: 4.0"])
            lines.extend(f"{key}: {value}" for key, value in headers.items())
            lines.extend(["", canonical_json(payload)])
        lines.extend([f"--{changeset}--", f"--{boundary}--", ""])
        return boundary, "\r\n".join(lines).encode()

    @staticmethod
    def _conditional_rejection(content_type: str, raw: bytes) -> bool:
        if "\r" in content_type or "\n" in content_type or not content_type.startswith("multipart/mixed;"):
            return False
        message = BytesParser(policy=policy.default).parsebytes(
            b"Content-Type: " + content_type.encode("ascii", errors="replace") + b"\r\nMIME-Version: 1.0\r\n\r\n" + raw
        )
        if message.defects or not message.is_multipart():
            return False
        part = message
        while part.is_multipart():
            children = list(part.iter_parts())
            if part.defects or part.get_content_type() != "multipart/mixed" or len(children) != 1:
                return False
            part = children[0]
        if part.defects or part.get_content_type() != "application/http":
            return False
        payload = part.get_payload(decode=True)
        return isinstance(payload, bytes) and bool(re.match(rb"HTTP/1\.1 412 [^\r\n]*\r\n", payload))

    def _resolution(self, effect: EffectRequest, state: ResolutionState, *,
                    evidence: dict[str, Any] | None = None, reason: str | None = None) -> TargetResolution:
        return TargetResolution(state, effect.effect_id, effect.digest,
                                operation_id=self.receipt_id(effect) if state == ResolutionState.CONFIRMED else None,
                                evidence=evidence, reason=reason)
