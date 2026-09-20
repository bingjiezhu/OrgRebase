"""Command-line transport for the authenticated Workspace API."""

from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class OperationError(RuntimeError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        raise OperationError("API_REDIRECT_REFUSED")


def workspace_command(
    base_url: str, action: str, *, event_id: str | None = None,
    digest: str | None = None, payload: dict[str, Any] | None = None,
    reason: str | None = None,
    token_variable: str = "ORGREBASE_ACCESS_TOKEN",
    workspace_id: str | None = None,
) -> dict[str, Any]:
    if workspace_id is not None and (
        not isinstance(workspace_id, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", workspace_id) is None
    ):
        raise OperationError("WORKSPACE_ID_INVALID")
    url = urlsplit(base_url)
    if (
        not url.hostname or url.username or url.password or url.query or url.fragment
        or url.path not in {"", "/"}
        or (url.scheme != "https" and not (
            url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost", "::1"}
        ))
    ):
        raise OperationError("API_HTTPS_OR_LOCALHOST_REQUIRED")
    if action == "state":
        method, path, body = "GET", "/api/workspace/state", None
    elif action == "register":
        if payload is None:
            raise OperationError("CHANGE_EVENT_INPUT_REQUIRED")
        method, path, body = "POST", "/api/workspace/changes", payload
    elif action == "reject":
        if not event_id:
            raise OperationError("CHANGE_EVENT_ID_REQUIRED")
        if not reason or not reason.strip() or len(reason) > 2000:
            raise OperationError("CHANGE_REJECTION_REASON_REQUIRED")
        method, path = "POST", f"/api/workspace/changes/{quote(event_id, safe='')}/reject"
        body = {"reason": reason.strip()}
    elif action in {"preview", "approve", "apply"}:
        if not event_id:
            raise OperationError("CHANGE_EVENT_ID_REQUIRED")
        if action != "preview" and not digest:
            raise OperationError("EXACT_PROPOSAL_DIGEST_REQUIRED")
        method, path = "POST", f"/api/workspace/{action}/{quote(event_id, safe='')}"
        body = {} if action == "preview" else {
            "preview_digest" if action == "approve" else "approval_digest": digest
        }
    else:
        raise OperationError("WORKSPACE_COMMAND_UNSUPPORTED")
    headers = {"Accept": "application/json"}
    if workspace_id is not None:
        headers["X-OrgRebase-Workspace"] = workspace_id
    token = os.environ.get(token_variable)
    if token:
        if any(value in token for value in "\r\n"):
            raise OperationError("ACCESS_TOKEN_INVALID")
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(
        base_url.rstrip("/") + path,
        data=json.dumps(body, ensure_ascii=False, allow_nan=False).encode() if body is not None else None,
        headers=headers, method=method,
    )
    try:
        with build_opener(_NoRedirect()).open(request, timeout=60) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise OperationError("API_RESPONSE_TOO_LARGE")
    except HTTPError as error:
        raise OperationError(f"API_REQUEST_REJECTED:{error.code}") from None
    except (URLError, TimeoutError, OSError):
        raise OperationError("API_UNAVAILABLE") from None
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise OperationError("API_RESPONSE_INVALID") from None
    if not isinstance(result, dict):
        raise OperationError("API_RESPONSE_INVALID")
    return result
