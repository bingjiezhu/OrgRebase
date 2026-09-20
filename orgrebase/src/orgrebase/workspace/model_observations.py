"""Per-call model observations, independent of frozen business receipts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from orgrebase.digest import canonical_json, verify_content_digest
from orgrebase.domain import ContentAddressedModel
from orgrebase.http_errors import public_error
from orgrebase.workspace.models import ModelRequest, ModelResponseReceipt

_FIELDS = ("input_tokens", "output_tokens", "thinking_tokens", "total_tokens", "cached_tokens")


class ModelUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["REPORTED", "PARTIAL", "UNAVAILABLE", "NOT_APPLICABLE"]
    basis: Literal["provider_response", "not_dispatched", "unavailable"]
    input_tokens: StrictInt | None = Field(default=None, ge=0)
    output_tokens: StrictInt | None = Field(default=None, ge=0)
    thinking_tokens: StrictInt | None = Field(default=None, ge=0)
    total_tokens: StrictInt | None = Field(default=None, ge=0)
    cached_tokens: StrictInt | None = Field(default=None, ge=0)
    reported_fields: tuple[str, ...] = ()
    invalid_fields: tuple[str, ...] = ()


def observed_usage(payload: Any, fields: Mapping[str, str]) -> ModelUsage:
    source = payload if isinstance(payload, Mapping) else {}
    values: dict[str, int | None] = {name: None for name in _FIELDS}
    invalid = []
    for name, source_name in fields.items():
        value = source.get(source_name)
        if type(value) is int and value >= 0:
            values[name] = value
        elif value is not None:
            invalid.append(name)
    reported = tuple(name for name in _FIELDS if values[name] is not None)
    complete = values["input_tokens"] is not None and values["output_tokens"] is not None and not invalid
    return ModelUsage(
        status="REPORTED" if complete else "PARTIAL" if reported else "UNAVAILABLE",
        basis="provider_response",
        **values,
        reported_fields=reported,
        invalid_fields=tuple(invalid),
    )


def legacy_token_count(value: Any) -> int:
    """V1 wire compatibility only; these defaults are never cost evidence."""
    return value if type(value) is int and value >= 0 else 0


def _identifier(value: Any) -> str | None:
    # Opaque provider IDs may start with URL-safe base64 punctuation.
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.:/-]{0,255}", value)
        else None
    )


class ModelAttemptObservation(ContentAddressedModel):
    schema_version: Literal["orgrebase.model-attempt-observation.v1"] = (
        "orgrebase.model-attempt-observation.v1"
    )
    dispatch_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    phase: Literal["INTENT", "RESULT"]
    run_ref: str
    task_ref: str
    request_digest: str
    request_attempt: int
    provider: str
    requested_model: str
    observed_model: str | None
    provider_request_id: str | None
    dispatch_state: Literal["NOT_DISPATCHED", "DISPATCH_MAY_HAVE_OCCURRED", "RESPONSE_RECEIVED"]
    response_state: str
    request_body_digest: str | None
    request_body_bytes: int | None
    response_body_bytes: int | None
    response_body_digest: str | None
    response_receipt_digest: str | None
    usage: ModelUsage
    duration_ms: int
    observed_at: str
    persistence: Literal["DURABLE", "NON_DURABLE", "WRITE_FAILED"]
    error_code: str | None
    legacy_receipt_usage_is_cost_evidence: Literal[False] = False


def _write_record(directory: Path, record: ModelAttemptObservation) -> None:
    missing = []
    current = directory
    while not current.exists():
        missing.append(current)
        current = current.parent
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    for created in reversed(missing):
        descriptor = os.open(created.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    target = directory / f"{record.dispatch_id}.{record.phase.lower()}.json"
    temporary = directory / f".{record.dispatch_id}.{uuid4().hex}.tmp"
    try:
        with temporary.open("xb") as stream:
            os.chmod(temporary, 0o600)
            stream.write(canonical_json(record.model_dump(mode="json")).encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        # link never overwrites another observation, unlike replace/rename.
        os.link(temporary, target)
        temporary.unlink()
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


class ModelAttemptObserver:
    """Append observations for this run; never retry or execute a model call."""

    def __init__(self, directory: str | Path | None = None) -> None:
        self.directory = Path(directory) if directory is not None else None
        self._records: list[ModelAttemptObservation] = []
        self._lock = threading.Lock()

    @contextmanager
    def observe(self, request: ModelRequest) -> Iterator[ModelAttempt]:
        attempt = ModelAttempt(self, request)
        try:
            yield attempt
        except BaseException as error:
            attempt.error_code = public_error(RuntimeError(type(error).__name__))["code"]
            raise
        finally:
            record = attempt.record("RESULT")
            if self.directory is not None:
                try:
                    _write_record(self.directory, record)
                except OSError:
                    record = ModelAttemptObservation.model_validate(
                        {
                            **record.model_dump(mode="json", exclude={"digest"}),
                            "persistence": "WRITE_FAILED",
                            "error_code": record.error_code or "MODEL_OBSERVATION_RESULT_WRITE_FAILED",
                        }
                    )
            with self._lock:
                self._records.append(record)

    def summary(self, *, run_ref: str | None = None) -> dict[str, Any]:
        with self._lock:
            records = tuple(
                record for record in self._records if run_ref is None or record.run_ref == run_ref
            )
        return {
            "schema_version": "orgrebase.model-attempt-collection.v1",
            "coverage": "DURABLE"
            if records and all(r.persistence == "DURABLE" for r in records)
            else "INCOMPLETE",
            "coverage_scope": "OBSERVED_CALLS_IN_THIS_COLLECTOR_NOT_ACCOUNT_COMPLETENESS_OR_PRICING",
            "records": [record.model_dump(mode="json") for record in records],
            "legacy_receipt_usage_is_cost_evidence": False,
        }


class ModelAttempt:
    def __init__(self, observer: ModelAttemptObserver, request: ModelRequest) -> None:
        self.observer, self.request = observer, request
        self.dispatch_id = uuid4().hex
        self.started = time.monotonic()
        self.dispatch_state = "NOT_DISPATCHED"
        self.raw_request: bytes | None = None
        self.response_bytes: int | None = None
        self.response_digest: str | None = None
        self.observed_model: str | None = None
        self.provider_request_id: str | None = None
        self.usage = ModelUsage(status="NOT_APPLICABLE", basis="not_dispatched")
        self.receipt: ModelResponseReceipt | None = None
        self.error_code: str | None = None
        self.write_failed = False

    def before_send(self, raw_request: bytes) -> bool:
        self.raw_request = raw_request
        self.dispatch_state = "DISPATCH_MAY_HAVE_OCCURRED"
        self.usage = ModelUsage(status="UNAVAILABLE", basis="unavailable")
        if self.observer.directory is not None:
            try:
                _write_record(self.observer.directory, self.record("INTENT"))
            except OSError:
                self.dispatch_state = "NOT_DISPATCHED"
                self.usage = ModelUsage(status="NOT_APPLICABLE", basis="not_dispatched")
                self.error_code = "MODEL_OBSERVATION_INTENT_WRITE_FAILED"
                self.write_failed = True
                return False
        return True

    def response(self, raw_response: bytes) -> None:
        self.dispatch_state = "RESPONSE_RECEIVED"
        self.response_bytes = len(raw_response)
        self.response_digest = "sha256:" + hashlib.sha256(raw_response).hexdigest()

    def metadata(self, *, usage: ModelUsage, observed_model: Any, provider_request_id: Any = None) -> None:
        self.usage = usage
        self.observed_model = _identifier(observed_model)
        self.provider_request_id = _identifier(provider_request_id)

    def record(self, phase: Literal["INTENT", "RESULT"]) -> ModelAttemptObservation:
        error = self.error_code or (self.receipt.error_code if self.receipt is not None else None)
        return ModelAttemptObservation(
            dispatch_id=self.dispatch_id,
            phase=phase,
            run_ref=self.request.run_id,
            task_ref=self.request.task_ref,
            request_digest=self.request.digest,
            request_attempt=self.request.attempt,
            provider=self.request.provider,
            requested_model=self.request.model_id,
            observed_model=self.observed_model,
            provider_request_id=self.provider_request_id,
            dispatch_state=self.dispatch_state,
            response_state=self.receipt.status if self.receipt else "UNKNOWN",
            request_body_digest=("sha256:" + hashlib.sha256(self.raw_request).hexdigest())
            if self.raw_request is not None
            else None,
            request_body_bytes=len(self.raw_request) if self.raw_request is not None else None,
            response_body_bytes=self.response_bytes,
            response_body_digest=self.response_digest,
            response_receipt_digest=self.receipt.digest if self.receipt else None,
            usage=self.usage,
            duration_ms=max(0, int((time.monotonic() - self.started) * 1000)),
            observed_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            persistence="WRITE_FAILED"
            if self.write_failed
            else "DURABLE"
            if self.observer.directory is not None
            else "NON_DURABLE",
            error_code=public_error(RuntimeError(error))["code"] if error else None,
        )


def provider_observations(provider: Any, *, run_ref: str) -> dict[str, Any]:
    """An injected provider without this observer supplies no cost evidence."""
    observer = getattr(provider, "observer", None)
    return (observer if isinstance(observer, ModelAttemptObserver) else ModelAttemptObserver()).summary(
        run_ref=run_ref
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("MODEL_OBSERVATION_DUPLICATE_FIELD")
        value[key] = item
    return value


def read_model_attempts(directory: str | Path) -> dict[str, Any]:
    """Read this directory only; an orphan intent remains possibly dispatched."""
    attempts: dict[str, dict[str, ModelAttemptObservation]] = {}
    selected = Path(directory).absolute()
    if any(path.is_symlink() for path in (selected, *selected.parents)):
        raise ValueError("MODEL_OBSERVATION_SYMLINK_DENIED")
    total_bytes = 0
    count = 0
    with os.scandir(selected) as entries:
        paths = []
        for entry in entries:
            count += 1
            if count > 4096:
                raise ValueError("MODEL_OBSERVATION_FILE_LIMIT")
            if entry.is_symlink():
                raise ValueError("MODEL_OBSERVATION_SYMLINK_DENIED")
            if entry.name.endswith(".json"):
                paths.append(Path(entry.path))
    for path in sorted(paths):
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("MODEL_OBSERVATION_REGULAR_FILE_REQUIRED")
            raw = stream.read(65_537)
        total_bytes += len(raw)
        if len(raw) > 65_536 or total_bytes > 16_777_216:
            raise ValueError("MODEL_OBSERVATION_SIZE_LIMIT")
        payload = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(payload, dict) or not verify_content_digest(payload, payload.get("digest", "")):
            raise ValueError("MODEL_OBSERVATION_DIGEST_MISMATCH")
        record = ModelAttemptObservation.model_validate(payload)
        if path.name != f"{record.dispatch_id}.{record.phase.lower()}.json":
            raise ValueError("MODEL_OBSERVATION_FILENAME_MISMATCH")
        attempts.setdefault(record.dispatch_id, {})[record.phase] = record
    values = []
    complete = bool(attempts)
    identity_fields = (
        "run_ref",
        "task_ref",
        "request_digest",
        "request_attempt",
        "provider",
        "requested_model",
        "request_body_digest",
        "request_body_bytes",
    )
    for phases in attempts.values():
        intent, result = phases.get("INTENT"), phases.get("RESULT")
        if (
            intent is not None
            and result is not None
            and any(getattr(intent, name) != getattr(result, name) for name in identity_fields)
        ):
            raise ValueError("MODEL_OBSERVATION_INTENT_RESULT_MISMATCH")
        record = result or intent
        assert record is not None
        values.append(record)
        if (
            result is None
            or result.persistence != "DURABLE"
            or (result.dispatch_state != "NOT_DISPATCHED" and intent is None)
        ):
            complete = False
    return {
        "schema_version": "orgrebase.model-attempt-collection.v1",
        "coverage": "DURABLE" if complete else "INCOMPLETE",
        "coverage_scope": "PROVIDED_DIRECTORY_NOT_ACCOUNT_COMPLETENESS_OR_PRICING",
        "records": [
            record.model_dump(mode="json") for record in sorted(values, key=lambda record: record.dispatch_id)
        ],
        "legacy_receipt_usage_is_cost_evidence": False,
    }
