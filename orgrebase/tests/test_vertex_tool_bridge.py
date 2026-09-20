import os
import subprocess
from copy import deepcopy
from io import BytesIO
from pathlib import Path

import pytest

from orgrebase.vertex_tool_bridge import (
    SignatureCache,
    capture_signatures,
    inject_missing_signatures,
    message_context_fingerprint,
    read_http_body,
    signature_diagnostics,
)

ROOT = Path(__file__).resolve().parents[1]


def test_vertex_token_refresh_uses_ephemeral_cookie_jar() -> None:
    source = (ROOT / "scripts/refresh_vertex_agentteams_token.sh").read_text(
        encoding="utf-8"
    )

    assert "orgrebase-higress-session-cookie.XXXXXX" in source
    assert "cookie_file=$(mktemp" in source
    assert "trap 'rm -f -- \"$cookie_file\"' EXIT" in source
    assert 'cookie_file="${TMPDIR:-/tmp}/orgrebase-higress-session-cookie"' not in source


def test_vertex_token_refresh_keeps_secrets_out_of_process_arguments() -> None:
    source = (ROOT / "scripts/refresh_vertex_agentteams_token.sh").read_text(
        encoding="utf-8"
    )

    assert "--rawfile username /dev/fd/3" in source
    assert "--rawfile password /dev/fd/4" in source
    assert "--rawfile token /dev/fd/3" in source
    assert "--arg username" not in source
    assert "--arg password" not in source
    assert "--arg token" not in source


@pytest.mark.parametrize(
    "console_url",
    (
        "https://127.0.0.1:18081",
        "http://higress.example.com:18081",
        "http://127.0.0.1.example.com:18081",
        "http://localhost@higress.example.com:18081",
        "http://127.0.0.1:18081/admin",
        "http://127.0.0.1:65536",
    ),
)
def test_vertex_token_refresh_rejects_non_loopback_console_url(
    console_url: str,
) -> None:
    script = ROOT / "scripts/refresh_vertex_agentteams_token.sh"
    environment = {**os.environ, "HIGRESS_CONSOLE_URL": console_url}

    completed = subprocess.run(
        ["bash", str(script)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )

    assert completed.returncode != 0
    assert "HIGRESS_CONSOLE_URL" in completed.stderr


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
    assert request["messages"][2]["tool_calls"][0]["id"] == "provider-id"
    assert request["messages"][3]["tool_call_id"] == "provider-id"


def test_restores_exact_provider_part_after_argument_object_normalization() -> None:
    cache = SignatureCache()
    prior_messages = [{"role": "user", "content": "apply bounded update"}]
    provider_part = {
        "id": "provider-exact",
        "type": "function",
        "function": {
            "name": "bounded_update",
            "arguments": '{"alpha":1,"beta":2}',
        },
        "extra_content": {
            "google": {"thought_signature": "opaque-exact"},
            "provider_metadata": {"mode": "sequential"},
        },
    }
    response = rb'''{
      "choices": [{"message": {"tool_calls": [{
        "id": "provider-exact",
        "type": "function",
        "function": {
          "name": "bounded_update",
          "arguments": "{\"alpha\":1,\"beta\":2}"
        },
        "extra_content": {
          "google": {"thought_signature": "opaque-exact"},
          "provider_metadata": {"mode": "sequential"}
        }
      }]}}]
    }'''
    assert capture_signatures(
        response,
        "application/json",
        cache,
        context_fingerprint=message_context_fingerprint(prior_messages),
    ) == 1
    request = {
        "messages": [
            *prior_messages,
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_local_exact",
                        "function": {
                            "name": "bounded_update",
                            "arguments": {"beta": 2, "alpha": 1},
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_local_exact", "content": "ok"},
        ]
    }

    assert inject_missing_signatures(request, cache) == 1
    assert request["messages"][1]["tool_calls"][0] == provider_part
    assert request["messages"][2]["tool_call_id"] == "provider-exact"


def test_restores_two_sequential_provider_ids_and_tool_result_bindings() -> None:
    cache = SignatureCache()
    prior_messages = [{"role": "user", "content": "inspect then message"}]
    context = message_context_fingerprint(prior_messages)
    first_response = b'''{
      "choices": [{"message": {"tool_calls": [{
        "id": "provider-first",
        "type": "function",
        "function": {"name": "agents_list", "arguments": "{}"},
        "extra_content": {"google": {"thought_signature": "opaque-first"}}
      }]}}]
    }'''
    second_response = rb'''{
      "choices": [{"message": {"tool_calls": [{
        "id": "provider-second",
        "type": "function",
        "function": {
          "name": "agents_message",
          "arguments": "{\"agent\":\"legal-steward\"}"
        },
        "extra_content": {"google": {"thought_signature": "opaque-second"}}
      }]}}]
    }'''
    assert capture_signatures(
        first_response, "application/json", cache, context_fingerprint=context
    ) == 1
    assert capture_signatures(
        second_response, "application/json", cache, context_fingerprint=context
    ) == 1

    request = {
        "messages": [
            *prior_messages,
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_local_first",
                        "type": "function",
                        "function": {"name": "agents_list", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_local_first", "content": "[]"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_local_second",
                        "type": "function",
                        "function": {
                            "name": "agents_message",
                            "arguments": "{\"agent\":\"legal-steward\"}",
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_local_second", "content": "ok"},
        ]
    }

    assert inject_missing_signatures(request, cache) == 2
    first_call = request["messages"][1]["tool_calls"][0]
    second_call = request["messages"][3]["tool_calls"][0]
    assert first_call == {
        "id": "provider-first",
        "type": "function",
        "function": {"name": "agents_list", "arguments": "{}"},
        "extra_content": {"google": {"thought_signature": "opaque-first"}},
    }
    assert request["messages"][2]["tool_call_id"] == "provider-first"
    assert second_call == {
        "id": "provider-second",
        "type": "function",
        "function": {
            "name": "agents_message",
            "arguments": '{"agent":"legal-steward"}',
        },
        "extra_content": {"google": {"thought_signature": "opaque-second"}},
    }
    assert request["messages"][4]["tool_call_id"] == "provider-second"


def test_does_not_guess_between_parallel_identical_semantic_calls() -> None:
    cache = SignatureCache()
    prior_messages = [{"role": "user", "content": "run two identical probes"}]
    response = rb'''{
      "choices": [{"message": {"tool_calls": [
        {
          "id": "provider-parallel-a",
          "type": "function",
          "function": {"name": "probe", "arguments": "{\"scope\":\"same\"}"},
          "extra_content": {"google": {"thought_signature": "opaque-a"}}
        },
        {
          "id": "provider-parallel-b",
          "type": "function",
          "function": {"name": "probe", "arguments": "{\"scope\":\"same\"}"},
          "extra_content": {"google": {"thought_signature": "opaque-b"}}
        }
      ]}}]
    }'''
    assert capture_signatures(
        response,
        "application/json",
        cache,
        context_fingerprint=message_context_fingerprint(prior_messages),
    ) == 2
    request = {
        "messages": [
            *prior_messages,
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_local_a",
                        "function": {"name": "probe", "arguments": {"scope": "same"}},
                    },
                    {
                        "id": "call_local_b",
                        "function": {"name": "probe", "arguments": {"scope": "same"}},
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "call_local_a", "content": "a"},
            {"role": "tool", "tool_call_id": "call_local_b", "content": "b"},
        ]
    }
    original = deepcopy(request)

    assert inject_missing_signatures(request, cache) == 0
    assert request == original

    compacted = {
        "messages": [
            *prior_messages,
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_local_after_compaction",
                        "function": {"name": "probe", "arguments": {"scope": "same"}},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_local_after_compaction",
                "content": "unknown",
            },
        ]
    }
    compacted_original = deepcopy(compacted)
    assert inject_missing_signatures(compacted, cache) == 0
    assert compacted == compacted_original


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


def test_restores_rewritten_id_when_client_preserved_the_extension() -> None:
    cache = SignatureCache()
    prior_messages = [{"role": "user", "content": "list agents"}]
    response = b'''{
      "choices": [{"message": {"tool_calls": [{
        "id": "provider-id",
        "function": {"name": "agents_list", "arguments": "{}"},
        "extra_content": {"google": {"thought_signature": "opaque"}}
      }]}}]
    }'''
    assert capture_signatures(
        response,
        "application/json",
        cache,
        context_fingerprint=message_context_fingerprint(prior_messages),
    ) == 1
    request = {
        "messages": [
            *prior_messages,
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_local",
                        "function": {"name": "agents_list", "arguments": "{}"},
                        "extra_content": {"google": {"thought_signature": "opaque"}},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_local", "content": "[]"},
        ]
    }

    assert inject_missing_signatures(request, cache) == 1
    assert request["messages"][1]["tool_calls"][0]["id"] == "provider-id"
    assert request["messages"][2]["tool_call_id"] == "provider-id"
    assert request["messages"][1]["tool_calls"][0]["extra_content"] == {
        "google": {"thought_signature": "opaque"}
    }


def test_never_overwrites_client_preserved_extension() -> None:
    cache = SignatureCache()
    provider_part = {
        "id": "call_789",
        "extra_content": {"google": {"thought_signature": "client"}},
    }
    cache.put("call_789", provider_part)
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
    original = deepcopy(request)
    assert inject_missing_signatures(request, cache) == 0
    assert request == original


def test_signature_diagnostics_exposes_only_protocol_shape() -> None:
    cache = SignatureCache()
    cache.put(
        "call_private",
        {
            "id": "call_private",
            "extra_content": {"google": {"thought_signature": "secret"}},
        },
    )
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
