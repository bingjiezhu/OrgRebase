"""Preserve Vertex Gemini tool-call thought signatures across OpenAI clients.

Gemini 3.x returns a provider extension at
``tool_calls[].extra_content.google.thought_signature``.  Some OpenAI-compatible
clients retain the standard tool call but discard that extension before the
tool result turn.  Vertex rejects the follow-up without the signature.

This bridge is deliberately narrow: it forwards only Chat Completions, caches
the opaque extension by tool-call id, and restores it when the client omitted
it.  It never logs request bodies, authorization headers, or signatures.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import urllib.error
import urllib.request
from collections import OrderedDict
from collections.abc import Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, BinaryIO

MAX_BODY_BYTES = 8 * 1024 * 1024
MAX_SIGNATURES = 1024
ALLOWED_PATHS = {"/chat/completions", "/v1/chat/completions"}


def read_http_body(stream: BinaryIO, headers: Mapping[str, str]) -> bytes:
    """Read a bounded fixed-length or chunked HTTP request body."""

    transfer_encoding = headers.get("Transfer-Encoding", "").lower()
    if "chunked" in transfer_encoding:
        body = bytearray()
        while True:
            size_line = stream.readline(128)
            if not size_line.endswith(b"\n"):
                raise ValueError("invalid chunk header")
            try:
                chunk_size = int(size_line.split(b";", 1)[0].strip(), 16)
            except ValueError as error:
                raise ValueError("invalid chunk size") from error
            if chunk_size == 0:
                while stream.readline(8192) not in {b"\r\n", b"\n", b""}:
                    pass
                return bytes(body)
            if len(body) + chunk_size > MAX_BODY_BYTES:
                raise OverflowError("request body too large")
            chunk = stream.read(chunk_size)
            if len(chunk) != chunk_size or stream.read(2) != b"\r\n":
                raise ValueError("truncated chunk")
            body.extend(chunk)

    try:
        content_length = int(headers.get("Content-Length", "0"))
    except ValueError as error:
        raise ValueError("invalid content length") from error
    if content_length <= 0:
        raise ValueError("missing content length")
    if content_length > MAX_BODY_BYTES:
        raise OverflowError("request body too large")
    body = stream.read(content_length)
    if len(body) != content_length:
        raise ValueError("truncated request body")
    return body


class SignatureCache:
    """Small bounded in-memory cache; signatures are opaque and short-lived."""

    def __init__(self, max_entries: int = MAX_SIGNATURES) -> None:
        self._entries: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._max_entries = max_entries
        self._lock = threading.Lock()

    def put(self, tool_call_id: str, extra_content: dict[str, Any]) -> None:
        with self._lock:
            self._entries[tool_call_id] = extra_content
            self._entries.move_to_end(tool_call_id)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def get(self, tool_call_id: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._entries.get(tool_call_id)
            if value is not None:
                self._entries.move_to_end(tool_call_id)
            return value

    def contains(self, tool_call_id: str) -> bool:
        with self._lock:
            return tool_call_id in self._entries

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


def inject_missing_signatures(payload: dict[str, Any], cache: SignatureCache) -> int:
    """Restore cached provider extensions without changing standard fields."""

    injected = 0
    messages = payload.get("messages", [])
    if not isinstance(messages, list):
        return 0
    for message_index, message in enumerate(messages):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for tool_call in message.get("tool_calls") or []:
            if not isinstance(tool_call, dict) or tool_call.get("extra_content") is not None:
                continue
            tool_call_id = tool_call.get("id")
            extra_content = cache.get(tool_call_id) if isinstance(tool_call_id, str) else None
            semantic_key = _semantic_cache_key(
                message_context_fingerprint(messages[:message_index]), tool_call
            )
            if extra_content is None and semantic_key is not None:
                extra_content = cache.get(semantic_key)
            if extra_content is not None:
                tool_call["extra_content"] = extra_content
                injected += 1
    return injected


def _canonical_arguments(value: Any) -> str | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return value
    if isinstance(value, (dict, list, int, float, bool)) or value is None:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return None


def _tool_call_fingerprint(tool_call: dict[str, Any]) -> str | None:
    function = tool_call.get("function")
    if not isinstance(function, dict) or not isinstance(function.get("name"), str):
        return None
    arguments = _canonical_arguments(function.get("arguments", ""))
    if arguments is None:
        return None
    material = f"{function['name']}\0{arguments}".encode()
    return hashlib.sha256(material).hexdigest()


def message_context_fingerprint(messages: list[Any]) -> str:
    """Fingerprint stable conversation context without retaining its contents."""

    # System prompts can contain a regenerated clock, and OpenClaw may normalize
    # old tool-result blocks between consecutive requests.  The most recent
    # user turn is the stable task anchor across a whole tool loop and differs
    # naturally between independently assigned Workers.
    stable_messages: list[dict[str, Any]] = []
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            stable_messages = [message]
            break
    material = json.dumps(
        stable_messages,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return hashlib.sha256(material).hexdigest()


def _semantic_cache_key(context_fingerprint: str, tool_call: dict[str, Any]) -> str | None:
    call_fingerprint = _tool_call_fingerprint(tool_call)
    if call_fingerprint is None:
        return None
    return f"semantic:{context_fingerprint}:{call_fingerprint}"


def signature_diagnostics(payload: dict[str, Any], cache: SignatureCache) -> str:
    """Return content-free counters for live protocol diagnosis.

    Tool-call ids are represented only by short one-way fingerprints.  This is
    enough to compare the provider response with the client's follow-up while
    keeping prompts, tool arguments, signatures, and identifiers out of logs.
    """

    assistant_messages = 0
    tool_calls = 0
    missing = 0
    hits = 0
    fingerprints: list[str] = []
    shapes: set[str] = set()
    for message in payload.get("messages", []):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        assistant_messages += 1
        shapes.add("+".join(sorted(str(key) for key in message if key != "content")))
        for tool_call in message.get("tool_calls") or []:
            if not isinstance(tool_call, dict):
                continue
            tool_calls += 1
            if tool_call.get("extra_content") is None:
                missing += 1
            tool_call_id = tool_call.get("id")
            if isinstance(tool_call_id, str):
                fingerprints.append(hashlib.sha256(tool_call_id.encode()).hexdigest()[:10])
                if cache.contains(tool_call_id):
                    hits += 1
    return (
        f"assistant_messages={assistant_messages} tool_calls={tool_calls} "
        f"missing={missing} cache_hits={hits} cache_size={len(cache)} "
        f"id_fingerprints={','.join(fingerprints) or '-'} "
        f"assistant_shapes={','.join(sorted(shapes)) or '-'}"
    )


def _capture_tool_calls(
    tool_calls: Any,
    states: dict[int, dict[str, Any]],
) -> None:
    if not isinstance(tool_calls, list):
        return
    for fallback_index, tool_call in enumerate(tool_calls):
        if not isinstance(tool_call, dict):
            continue
        index = tool_call.get("index")
        if not isinstance(index, int):
            index = fallback_index
        state = states.setdefault(index, {"function": {"name": "", "arguments": ""}})
        tool_call_id = tool_call.get("id")
        if isinstance(tool_call_id, str):
            state["id"] = tool_call_id
        extra_content = tool_call.get("extra_content")
        if isinstance(extra_content, dict):
            state["extra_content"] = extra_content
        function = tool_call.get("function")
        if not isinstance(function, dict):
            continue
        state_function = state["function"]
        name = function.get("name")
        if isinstance(name, str):
            state_function["name"] += name
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            state_function["arguments"] += arguments
        elif arguments is not None:
            state_function["arguments"] = arguments


def capture_signatures(
    body: bytes,
    content_type: str,
    cache: SignatureCache,
    *,
    context_fingerprint: str = "",
) -> int:
    """Capture signatures from JSON or SSE while leaving the response untouched."""

    documents: list[dict[str, Any]] = []
    if "text/event-stream" in content_type:
        for line in body.splitlines():
            if not line.startswith(b"data: ") or line == b"data: [DONE]":
                continue
            try:
                document = json.loads(line[6:])
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(document, dict):
                documents.append(document)
    else:
        try:
            document = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            document = None
        if isinstance(document, dict):
            documents.append(document)

    states_by_choice: dict[int, dict[int, dict[str, Any]]] = {}
    for document in documents:
        for fallback_choice_index, choice in enumerate(document.get("choices", [])):
            if not isinstance(choice, dict):
                continue
            choice_index = choice.get("index")
            if not isinstance(choice_index, int):
                choice_index = fallback_choice_index
            states = states_by_choice.setdefault(choice_index, {})
            message = choice.get("message") or choice.get("delta") or {}
            if isinstance(message, dict):
                _capture_tool_calls(message.get("tool_calls"), states)

    captured = 0
    for states in states_by_choice.values():
        for tool_call in states.values():
            extra_content = tool_call.get("extra_content")
            if not isinstance(extra_content, dict):
                continue
            tool_call_id = tool_call.get("id")
            if isinstance(tool_call_id, str):
                cache.put(tool_call_id, extra_content)
            semantic_key = _semantic_cache_key(context_fingerprint, tool_call)
            if semantic_key is not None:
                cache.put(semantic_key, extra_content)
            captured += 1
    return captured


class VertexToolBridgeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    cache = SignatureCache()
    upstream_url = ""
    upstream_timeout_seconds = 120

    def log_message(self, format: str, *args: Any) -> None:
        # Log only the normal request line/status; never bodies, headers, or cache data.
        super().log_message(format, *args)

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._respond(HTTPStatus.OK, b'{"status":"ok"}', "application/json")
            return
        self._respond(HTTPStatus.NOT_FOUND, b'{"error":"not_found"}', "application/json")

    def do_POST(self) -> None:
        request_path = self.path.split("?", 1)[0]
        if request_path not in ALLOWED_PATHS:
            self._respond(HTTPStatus.NOT_FOUND, b'{"error":"unsupported_path"}', "application/json")
            return
        try:
            raw_body = read_http_body(self.rfile, self.headers)
        except OverflowError:
            self._respond(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                b'{"error":"invalid_content_length"}',
                "application/json",
            )
            return
        except ValueError:
            self._respond(HTTPStatus.BAD_REQUEST, b'{"error":"invalid_http_body"}', "application/json")
            return
        try:
            payload = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._respond(HTTPStatus.BAD_REQUEST, b'{"error":"invalid_json"}', "application/json")
            return
        if not isinstance(payload, dict):
            self._respond(HTTPStatus.BAD_REQUEST, b'{"error":"invalid_payload"}', "application/json")
            return

        messages = payload.get("messages")
        context_fingerprint = message_context_fingerprint(messages) if isinstance(messages, list) else ""
        diagnostics = signature_diagnostics(payload, self.cache)
        injected = inject_missing_signatures(payload, self.cache)
        forwarded_body = json.dumps(payload, separators=(",", ":")).encode()
        suffix = request_path.removeprefix("/v1")
        request = urllib.request.Request(
            f"{self.upstream_url.rstrip('/')}{suffix}",
            data=forwarded_body,
            method="POST",
            headers={
                "Authorization": self.headers.get("Authorization", ""),
                "Content-Type": "application/json",
                "Accept": self.headers.get("Accept", "*/*"),
                "User-Agent": "orgrebase-vertex-tool-bridge/0.1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.upstream_timeout_seconds) as response:
                response_body = response.read()
                status = response.status
                content_type = response.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as error:
            response_body = error.read()
            status = error.code
            content_type = error.headers.get("Content-Type", "application/json")
        except (TimeoutError, urllib.error.URLError):
            self._respond(
                HTTPStatus.BAD_GATEWAY,
                b'{"error":"upstream_unavailable"}',
                "application/json",
            )
            return

        captured = capture_signatures(
            response_body,
            content_type,
            self.cache,
            context_fingerprint=context_fingerprint,
        )
        self.log_message(
            "bridge upstream_status=%d signatures_injected=%d signatures_captured=%d %s",
            status,
            injected,
            captured,
            diagnostics,
        )
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(response_body)))
        self.send_header("X-OrgRebase-Signatures-Injected", str(injected))
        self.end_headers()
        try:
            self.wfile.write(response_body)
        except (BrokenPipeError, ConnectionResetError):
            # The downstream may cancel a background compaction request.  The
            # upstream response has already completed, so this is not a server
            # fault and should not emit a noisy traceback.
            return

    def _respond(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    upstream_url = os.environ.get("VERTEX_UPSTREAM_URL", "").strip()
    if not upstream_url.startswith("https://aiplatform.googleapis.com/"):
        raise SystemExit("VERTEX_UPSTREAM_URL must use the Vertex AI HTTPS endpoint")
    VertexToolBridgeHandler.upstream_url = upstream_url
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), VertexToolBridgeHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
