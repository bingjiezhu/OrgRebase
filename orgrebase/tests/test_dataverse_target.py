from __future__ import annotations

import json
from email import policy
from email.parser import BytesParser
from pathlib import Path

import httpx2 as httpx
import pytest

from orgrebase.commit_gateway import CommitGateway, EffectError, EffectRequest, ResolutionState
from orgrebase.dataverse_target import DataverseDraftTarget, DataverseDraftTargetSettings
from orgrebase.store import StateStore

QUOTE_ID = "00000000-0000-0000-0000-000000000123"


class DataverseProtocolServer:
    def __init__(self):
        self.quote = {"quoteid": QUOTE_ID, "statecode": 0, "name": "Enterprise quote",
                      "description": "Prior approved draft", "@odata.etag": 'W/"41"'}
        self.receipts = {}
        self.writes = 0
        self.requests = []
        self.lose_response = False
        self.defer = False
        self.empty_batch = False
        self.race = False
        self.deferred = None

    def __call__(self, request):
        self.requests.append(request)
        assert request.url.host == "sales.example"
        assert request.headers["Authorization"].split() == ["Bearer", "test-target-access"]
        path = request.url.path
        if path.endswith(f"quotes({QUOTE_ID})") and request.method == "GET":
            response = httpx.Response(200, json=self.quote)
            if self.race:
                self.quote = {**self.quote, "@odata.etag": 'W/"42"', "description": "Another user's edit"}
            return response
        if "orgrebase_effectreceipts(" in path and request.method == "GET":
            identity = path.rsplit("(", 1)[1][:-1]
            return httpx.Response(200, json=self.receipts[identity]) if identity in self.receipts else httpx.Response(404)
        if path.endswith("/orgrebase_effectreceipts") and request.method == "POST":
            row = json.loads(request.content)
            identity = row["orgrebase_effectreceiptid"]
            if identity in self.receipts:
                return httpx.Response(409)
            self.receipts[identity] = row
            return httpx.Response(204)
        if path.endswith("/$batch") and request.method == "POST":
            if self.empty_batch:
                return httpx.Response(200, content=b"nothing executed")
            if self.defer:
                self.deferred = request
                raise httpx.ReadTimeout("response deadline", request=request)
            result = self.batch(request)
            if self.lose_response:
                raise httpx.ReadError("response lost after commit", request=request)
            return result
        raise AssertionError(f"Unexpected request: {request.method} {path}")

    def batch(self, request):
        envelope = BytesParser(policy=policy.default).parsebytes(
            b"MIME-Version: 1.0\r\nContent-Type: " + request.headers["Content-Type"].encode() + b"\r\n\r\n" + request.content
        )
        parts = [part for part in envelope.walk() if part.get_content_type() == "application/http"]
        assert len(parts) == 2
        assert [part["Content-ID"] for part in parts] == ["1", "2"]
        receipt_raw, patch_raw = [part.get_payload(decode=True).decode() for part in parts]
        receipt = json.loads(receipt_raw.split("\r\n\r\n", 1)[1])
        changes = json.loads(patch_raw.split("\r\n\r\n", 1)[1])
        identity = receipt["orgrebase_effectreceiptid"]
        assert receipt_raw.startswith("POST /api/data/v9.2/orgrebase_effectreceipts HTTP/1.1")
        assert patch_raw.startswith(f"PATCH /api/data/v9.2/quotes({QUOTE_ID}) HTTP/1.1")
        if identity in self.receipts:
            return httpx.Response(409)
        if f'If-Match: {self.quote["@odata.etag"]}\r\n' not in patch_raw:
            content = ("--response\r\nContent-Type: application/http\r\nContent-Transfer-Encoding: binary\r\n\r\n"
                       "HTTP/1.1 412 Precondition Failed\r\nContent-Type: application/json\r\n\r\n{}\r\n--response--\r\n")
            return httpx.Response(200, content=content, headers={"Content-Type": "multipart/mixed; boundary=response"})
        assert set(changes).issubset({"name", "description"})
        self.quote = {**self.quote, **changes, "@odata.etag": 'W/"43"'}
        self.receipts[identity] = receipt
        self.writes += 1
        return httpx.Response(200, content=b"receipt is the positive result evidence")


@pytest.fixture
def setup_target():
    server = DataverseProtocolServer()
    settings = DataverseDraftTargetSettings(tenant_id="tenant:one", instance_url="https://sales.example", quote_id=QUOTE_ID)
    adapter = DataverseDraftTarget(settings, lambda: "test-target-access", transport=httpx.MockTransport(server))
    effect = EffectRequest(effect_id="effect:one", tenant_id=settings.tenant_id, target_key=settings.target_key,
                           action=adapter.action, expected_version='W/"41"', approval_digest="sha256:" + "a" * 64,
                           payload={"changes": {"description": "Owner approved revision"}})
    with adapter:
        yield server, adapter, effect


def gateway(store):
    return CommitGateway(store, tenant_id="tenant:one", worker_id="worker:one", authorize=lambda effect, action: None)


def test_atomic_conditional_update_and_receipt_are_replayed_without_second_write(setup_target):
    server, target, effect = setup_target
    with StateStore(tenant_id="tenant:one") as store:
        result = gateway(store).run(effect, target)
        assert result["receipt_id"] == target.receipt_id(effect)
        assert gateway(store).run(effect, target) == result
    assert server.writes == 1
    assert server.quote["description"] == effect.payload["changes"]["description"]


@pytest.mark.parametrize("field", ["name", "description"])
@pytest.mark.parametrize("value", [{"private": "nested data"}, ["nested data"], True, 17, 1.5])
def test_invalid_observed_text_cannot_be_used_for_a_conditional_write(setup_target, field, value):
    server, target, effect = setup_target
    server.quote[field] = value
    with pytest.raises(EffectError, match=r"^TARGET_QUOTE_FIELDS_INVALID$"):
        target.draft()
    with pytest.raises(EffectError, match=r"^TARGET_QUOTE_FIELDS_INVALID$"):
        target.execute(effect)
    assert server.writes == 0 and server.receipts == {}
    assert all(request.method == "GET" for request in server.requests)


@pytest.mark.parametrize("value", [None, "", "Existing tenant text " * 200])
def test_observed_text_preserves_nullable_and_larger_existing_tenant_values(setup_target, value):
    server, target, _ = setup_target
    server.quote.update(name=value, description=value)
    assert target.draft()["fields"] == {"name": value, "description": value}
    assert server.writes == 0


def test_response_loss_reconciles_after_database_reopen_without_resending(setup_target, tmp_path: Path):
    server, target, effect = setup_target
    server.lose_response = True
    with StateStore(tmp_path / "effects.sqlite", tenant_id="tenant:one") as store:
        with pytest.raises(EffectError, match="TARGET_RESPONSE_UNAVAILABLE"):
            gateway(store).run(effect, target)
        assert store.get_effect(effect.effect_id)["state"] == "COMMIT_UNKNOWN"
    with StateStore(tmp_path / "effects.sqlite", tenant_id="tenant:one") as store:
        assert gateway(store).run(effect, target, query_only=True)["outcome"] == "CONFIRMED"
    assert server.writes == 1


def test_missing_receipt_never_authorizes_a_retry_and_cancel_fences_late_batch(setup_target):
    server, target, effect = setup_target
    server.defer = True
    with StateStore(tenant_id="tenant:one") as store:
        with pytest.raises(EffectError, match="TARGET_RESPONSE_UNAVAILABLE"):
            gateway(store).run(effect, target)
        with pytest.raises(EffectError, match="COMMIT_UNKNOWN"):
            gateway(store).run(effect, target, query_only=True)
        assert store.get_target_barrier(effect.barrier_key) == effect.effect_id
        cancelled = target.cancel_effect(effect)
        assert cancelled.state == ResolutionState.REJECTED
        # The old request arrives after cancellation: its receipt insert conflicts,
        # so the entire changeset, including the quote update, cannot commit.
        assert server.batch(server.deferred).status_code == 409
        assert server.writes == 0
        with pytest.raises(EffectError, match="TARGET_EFFECT_CANCELLED"):
            gateway(store).run(effect, target, query_only=True)
        assert store.get_target_barrier(effect.barrier_key) is None


def test_cancellation_cannot_hide_a_write_that_already_committed(setup_target):
    server, target, effect = setup_target
    server.lose_response = True
    with pytest.raises(EffectError):
        target.execute(effect)
    resolution = target.cancel_effect(effect)
    assert resolution.state == ResolutionState.CONFIRMED
    assert server.writes == 1
    assert len(server.receipts) == 1


def test_cancel_response_loss_leaves_unknown_until_positive_tombstone_readback(setup_target):
    server, target, effect = setup_target
    original = server.__call__

    def lose_cancel(request):
        response = original(request)
        if request.method == "POST" and request.url.path.endswith("/orgrebase_effectreceipts"):
            raise httpx.ReadTimeout("cancel result lost", request=request)
        return response

    target.transport = httpx.MockTransport(lose_cancel)
    with pytest.raises(EffectError, match="TARGET_RESPONSE_UNAVAILABLE"):
        target.cancel_effect(effect)
    assert target.query_effect(effect).state == ResolutionState.REJECTED
    assert server.writes == 0


def test_original_commit_between_cancel_lookup_and_tombstone_returns_confirmed(setup_target):
    server, target, effect = setup_target
    boundary, raw = target._batch(effect, effect.payload["changes"])
    late_request = httpx.Request("POST", target.settings.api_url + "/$batch", content=raw,
                                headers={"Content-Type": f"multipart/mixed; boundary={boundary}"})
    original = server.__call__

    def original_wins(request):
        if request.method == "POST" and request.url.path.endswith("/orgrebase_effectreceipts"):
            server.batch(late_request)
        return original(request)

    target.transport = httpx.MockTransport(original_wins)
    assert target.cancel_effect(effect).state == ResolutionState.CONFIRMED
    assert server.writes == 1


@pytest.mark.parametrize("parts", [0, 2])
def test_missing_or_multiple_inner_412_does_not_release_unknown_barrier(setup_target, parts):
    server, target, effect = setup_target
    original = server.__call__

    def ambiguous(request):
        if request.url.path.endswith("/$batch"):
            part = ("--response\r\nContent-Type: application/http\r\n\r\n"
                    "HTTP/1.1 412 Precondition Failed\r\n\r\n{}\r\n")
            return httpx.Response(200, content=part * parts + "--response--\r\n",
                                  headers={"Content-Type": "multipart/mixed; boundary=response"})
        return original(request)

    target.transport = httpx.MockTransport(ambiguous)
    with StateStore(tenant_id=effect.tenant_id) as store:
        with pytest.raises(EffectError, match="COMMIT_UNKNOWN"):
            gateway(store).run(effect, target)
        assert store.get_target_barrier(effect.barrier_key) == effect.effect_id
    assert server.writes == 0


def test_inner_version_conflict_rolls_back_receipt_and_requests_new_proposal(setup_target):
    server, target, effect = setup_target
    server.race = True
    with StateStore(tenant_id="tenant:one") as store:
        with pytest.raises(EffectError, match="TARGET_VERSION_CONFLICT"):
            gateway(store).run(effect, target)
        assert store.get_effect(effect.effect_id)["state"] == "REJECTED"
    assert server.writes == 0 and server.receipts == {}
    assert server.quote["description"] == "Another user's edit"


def test_http_200_without_any_inner_effect_remains_unknown(setup_target):
    server, target, effect = setup_target
    server.empty_batch = True
    with StateStore(tenant_id="tenant:one") as store:
        with pytest.raises(EffectError, match="COMMIT_UNKNOWN"):
            gateway(store).run(effect, target)
        assert store.get_target_barrier(effect.barrier_key) == effect.effect_id
    assert server.writes == 0


@pytest.mark.parametrize("payload", [{"changes": {"totalamount": 1}}, {"changes": {}}, {"changes": {"description": ""}}])
def test_pricing_empty_or_unapproved_fields_never_reach_the_target(setup_target, payload):
    server, target, effect = setup_target
    with pytest.raises(EffectError, match="TARGET_DRAFT_FIELDS_INVALID"):
        target.execute(effect.model_copy(update={"payload": payload}))
    assert server.requests == []


def test_mismatched_receipt_cannot_be_used_as_success_evidence(setup_target):
    server, target, effect = setup_target
    target.execute(effect)
    server.receipts[target.receipt_id(effect)]["orgrebase_requestdigest"] = "sha256:" + "0" * 64
    with pytest.raises(EffectError, match="TARGET_RECEIPT_BINDING_MISMATCH"):
        target.query_effect(effect)


def test_query_of_a_ready_or_absent_effect_cannot_dispatch_anything(setup_target):
    server, target, effect = setup_target
    with StateStore(tenant_id="tenant:one") as store:
        with pytest.raises(EffectError, match="EFFECT_NOT_DISPATCHED"):
            gateway(store).run(effect, target, query_only=True)
        assert store.get_effect(effect.effect_id) is None
    assert server.requests == []


def test_target_redirect_does_not_forward_credentials(setup_target):
    _, target, effect = setup_target
    seen = []

    def redirect(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://other.example/collect"})

    target.transport = httpx.MockTransport(redirect)
    with pytest.raises(EffectError, match="TARGET_REDIRECT_REFUSED"):
        target.query_effect(effect)
    assert len(seen) == 1


def test_request_validation_does_not_allocate_client_or_read_credentials(setup_target, monkeypatch):
    _, target, effect = setup_target

    def unexpected(*args, **kwargs):
        raise AssertionError("Validation must remain local")

    target.token = unexpected
    monkeypatch.setattr(httpx, "Client", unexpected)
    assert target.validate_request(effect) == effect.payload["changes"]


def test_reused_client_refreshes_credentials_without_retaining_cookies(setup_target, monkeypatch):
    _, target, _ = setup_target
    tokens = iter(["first", "second", "", "third"])
    requests, clients = [], []
    client_type = httpx.Client

    def create_client(**kwargs):
        client = client_type(**kwargs)
        clients.append(client)
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        assert kwargs["timeout"] == 20
        return client

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True}, headers={"Set-Cookie": "session=unwanted; Path=/"})

    monkeypatch.setattr(httpx, "Client", create_client)
    target.token = lambda: next(tokens)
    target.transport = httpx.MockTransport(respond)
    assert target.read_metadata("/WhoAmI") == {"ok": True}
    assert target.read_metadata("/WhoAmI") == {"ok": True}
    with pytest.raises(EffectError, match="TARGET_CREDENTIAL_UNAVAILABLE"):
        target.read_metadata("/WhoAmI")
    assert target.read_metadata("/WhoAmI") == {"ok": True}
    assert len(clients) == 1 and not clients[0].is_closed
    assert [request.headers["Authorization"] for request in requests] == [
        "Bearer first", "Bearer second", "Bearer third",
    ]
    assert all("Cookie" not in request.headers for request in requests)
    target.close()
    target.close()
    assert clients[0].is_closed
    with pytest.raises(EffectError, match="TARGET_CLIENT_CLOSED"):
        target.read_metadata("/WhoAmI")
    with pytest.raises(EffectError, match="TARGET_CLIENT_CLOSED"), target:
        pytest.fail("A closed target must not reopen")
    assert len(clients) == 1 and len(requests) == 3


@pytest.mark.parametrize("failure,code", [
    ("oversize", "TARGET_RESPONSE_TOO_LARGE"),
    ("read_error", "TARGET_RESPONSE_UNAVAILABLE"),
    ("redirect", "TARGET_REDIRECT_REFUSED"),
])
def test_failed_response_closes_stream_and_command_transport(setup_target, failure, code):
    _, target, _ = setup_target

    class ResponseStream(httpx.SyncByteStream):
        closed = False

        def __iter__(self):
            if failure == "oversize":
                yield b"x" * (2 * 1024 * 1024)
                yield b"x"
            elif failure == "read_error":
                raise httpx.ReadError("Interrupted response")
            else:
                yield b"{}"

        def close(self):
            self.closed = True

    class Transport(httpx.MockTransport):
        close_count = 0

        def close(self):
            self.close_count += 1

    stream = ResponseStream()
    transport = Transport(lambda request: httpx.Response(302 if failure == "redirect" else 200, stream=stream))
    target.transport = transport
    with pytest.raises(EffectError, match=code), target:
        target.read_metadata("/WhoAmI")
    assert stream.closed and transport.close_count == 1
    target.close()
    assert transport.close_count == 1
