from io import BytesIO

import pytest

from orgrebase.vertex_tool_bridge import (
    SignatureCache,
    capture_signatures,
    inject_missing_signatures,
    message_context_fingerprint,
    read_http_body,
    signature_diagnostics,
)


def test_captures_json_signature_and_restores_missing_extension() -> None:
    cache = SignatureCache()
    response = b'''{
      "choices": [{"message": {"tool_calls": [{
        "id": "call_123",
        "type": "function",
        "function": {"name": "agents_list", "arguments": "{}"},
        "extra_content": {"google": {"thought_signature": "opaque"}}
      }]}}]
    }'''
    assert capture_signatures(response, "application/json", cache) == 1

    request = {
        "messages": [
            {"role": "assistant", "tool_calls": [{"id": "call_123", "type": "function"}]}
        ]
    }
    assert inject_missing_signatures(request, cache) == 1
    assert request["messages"][0]["tool_calls"][0]["extra_content"] == {
        "google": {"thought_signature": "opaque"}
    }


def test_captures_streamed_signature_by_tool_index() -> None:
    cache = SignatureCache()
    response = b"\n".join(
        [
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_456"}]}}]}',
            (
                b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
                b'"extra_content":{"google":{"thought_signature":"opaque-stream"}}}]}}]}'
            ),
            b"data: [DONE]",
        ]
    )
    assert capture_signatures(response, "text/event-stream", cache) == 1
    request = {"messages": [{"role": "assistant", "tool_calls": [{"id": "call_456"}]}]}
    assert inject_missing_signatures(request, cache) == 1


def test_restores_signature_when_client_rewrites_tool_call_id() -> None:
    cache = SignatureCache()
    prior_messages = [
        {"role": "system", "content": "clock: 10:00"},
        {"role": "user", "content": "list agents"},
    ]
    response = b"\n".join(
        [
            (
                b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,'
                b'"id":"provider-id","function":{"name":"agents_list","arguments":""}}]}}]}'
            ),
            (
                b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,'
                b'"function":{"arguments":"{}"},"extra_content":{"google":'
                b'{"thought_signature":"opaque"}}}]}}]}'
            ),
            b"data: [DONE]",
        ]
    )
    assert (
        capture_signatures(
            response,
            "text/event-stream",
            cache,
            context_fingerprint=message_context_fingerprint(prior_messages),
        )
        == 1
    )

    request = {
        "messages": [
            {"role": "system", "content": "clock: 10:01"},
            {"role": "user", "content": "list agents"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "client-rewritten-id",
                        "function": {"name": "agents_list", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "client-rewritten-id", "content": "[]"},
        ]
    }
    assert inject_missing_signatures(request, cache) == 1
    assert request["messages"][2]["tool_calls"][0]["extra_content"] == {
        "google": {"thought_signature": "opaque"}
    }


def test_context_fingerprint_ignores_tool_result_replay_normalization() -> None:
    user = {"role": "user", "content": "bounded task"}
    first = [
        {"role": "system", "content": "clock: 10:00"},
        user,
        {"role": "assistant", "tool_calls": [{"id": "provider-id"}]},
        {"role": "tool", "content": {"result": 1}},
    ]
    replayed = [
        {"role": "system", "content": "clock: 10:01"},
        user,
        {"role": "assistant", "tool_calls": [{"id": "client-id"}]},
        {"role": "tool", "content": "{\"result\":1}"},
    ]
    assert message_context_fingerprint(first) == message_context_fingerprint(replayed)


def test_never_overwrites_client_preserved_extension() -> None:
    cache = SignatureCache()
    cache.put("call_789", {"google": {"thought_signature": "cached"}})
    request = {
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_789",
                        "extra_content": {"google": {"thought_signature": "client"}},
                    }
                ],
            }
        ]
    }
    assert inject_missing_signatures(request, cache) == 0
    assert request["messages"][0]["tool_calls"][0]["extra_content"]["google"][
        "thought_signature"
    ] == "client"


def test_signature_diagnostics_exposes_only_protocol_shape() -> None:
    cache = SignatureCache()
    cache.put("call_private", {"google": {"thought_signature": "secret"}})
    request = {
        "messages": [
            {
                "role": "assistant",
                "content": "private prompt",
                "tool_calls": [{"id": "call_private", "arguments": "private args"}],
            }
        ]
    }
    result = signature_diagnostics(request, cache)
    assert "assistant_messages=1" in result
    assert "tool_calls=1" in result
    assert "cache_hits=1" in result
    assert "call_private" not in result
    assert "private prompt" not in result
    assert "private args" not in result
    assert "secret" not in result


def test_reads_chunked_request_body() -> None:
    stream = BytesIO(b"4\r\ntest\r\n6\r\n-body!\r\n0\r\n\r\n")
    assert read_http_body(stream, {"Transfer-Encoding": "chunked"}) == b"test-body!"


def test_rejects_truncated_chunk() -> None:
    with pytest.raises(ValueError, match="truncated chunk"):
        read_http_body(BytesIO(b"4\r\nab\r\n"), {"Transfer-Encoding": "chunked"})
