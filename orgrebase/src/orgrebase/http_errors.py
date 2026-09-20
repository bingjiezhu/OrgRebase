"""Public error details shared by Workspace HTTP entry points."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from uuid import uuid4


@dataclass
class RequestIncident:
    """Request-local diagnostics; never contains exception messages or input values."""

    incident_id: str | None = None
    exception_type: str | None = None
    error_code: str | None = None

    def capture(self, exc: Exception, code: str) -> str:
        if self.incident_id is None:
            self.incident_id = uuid4().hex
            self.exception_type = type(exc).__name__
            self.error_code = code
        return self.incident_id

    def log(self, *, status: int, route: str | None) -> None:
        if self.incident_id is None:
            return
        # Fixed format and route template only: no raw URL, headers, exception
        # message, traceback locals, actor identity or business input.
        logging.getLogger("orgrebase.http").error(
            "request_failed incident_id=%s exception_type=%s code=%s status=%s route=%s",
            self.incident_id, self.exception_type, self.error_code, status, route or "unmatched",
        )


_request_incident: ContextVar[RequestIncident | None] = ContextVar("request_incident", default=None)


@contextmanager
def request_incident_scope() -> Iterator[RequestIncident]:
    incident = RequestIncident()
    token = _request_incident.set(incident)
    try:
        yield incident
    finally:
        _request_incident.reset(token)


def public_error(exc: Exception, *, message_as_code: bool = False) -> dict[str, Any]:
    """Filter public details while retaining each HTTP surface's existing code contract."""

    raw_message = str(exc)
    raw_code = getattr(exc, "code", None)
    message_code = raw_message.partition(":")[0]
    valid_message_code = re.fullmatch(r"[A-Z][A-Z0-9_]{2,127}", message_code) is not None
    candidate = str(raw_code) if isinstance(raw_code, str) else message_code
    if message_as_code and valid_message_code:
        candidate = message_code
    code = (
        candidate
        if re.fullmatch(r"[A-Z][A-Z0-9_]{2,127}", candidate)
        else "WORKSPACE_REQUEST_FAILED"
    )
    safe_message = (
        message_code
        if valid_message_code
        else code
    )
    if code == "PROFILE_VALIDATION_FAILED":
        profile_reason = next(
            (
                item
                for item in re.findall(r"\bPROFILE_[A-Z0-9_]{3,127}\b", raw_message)
                if item != code
            ),
            None,
        )
        if profile_reason is not None:
            safe_message = f"{code}:{profile_reason}"
    detail: dict[str, Any] = {
        "code": code,
        # Never reflect exception text: subprocess stderr, host paths, or user
        # content may be embedded in it. The UI translates this stable code.
        "message": safe_message,
    }
    remaining_ms = getattr(exc, "remaining_ms", None)
    if isinstance(remaining_ms, int):
        detail["remaining_ms"] = remaining_ms
    not_before = getattr(exc, "not_before", None)
    if isinstance(not_before, str):
        detail["not_before"] = not_before
    return detail


def public_http_error(exc: Exception, *, message_as_code: bool = False) -> dict[str, Any]:
    """Attach diagnostics only at the HTTP error boundary, not during stored-error projection."""
    detail = public_error(exc, message_as_code=message_as_code)
    incident = _request_incident.get()
    if detail["code"] == "WORKSPACE_REQUEST_FAILED" and incident is not None:
        detail["incident_id"] = incident.capture(exc, detail["code"])
    return detail
