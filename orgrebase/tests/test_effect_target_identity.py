from __future__ import annotations

from contextlib import ExitStack

import httpx2 as httpx
import pytest
from test_dataverse_target import QUOTE_ID, DataverseProtocolServer

from orgrebase.commit_gateway import CommitGateway, EffectError, EffectRequest
from orgrebase.dataverse_target import DataverseDraftTarget, DataverseDraftTargetSettings
from orgrebase.digest import sha256_digest
from orgrebase.store import StateStore


def effect(origin: str, *, identifier: str = "effect:first", version: str = 'W/"41"'):
    settings = DataverseDraftTargetSettings(tenant_id="org:test", instance_url=origin, quote_id=QUOTE_ID)
    request = EffectRequest(effect_id=identifier, tenant_id=settings.tenant_id, target_key=settings.target_key,
                            action=DataverseDraftTarget.action, expected_version=version,
                            approval_digest="sha256:" + "a" * 64,
                            payload={"changes": {"description": "Approved " + identifier}})
    return settings, request


@pytest.mark.parametrize(("first", "second"), [
    ("https://SALES.example:443/", "https://sales.example"),
    ("HTTPS://Sales.Example", "https://sales.example"),
    ("https://sales.example:0443", "https://sales.example"),
    ("https://sales.example.", "https://sales.example"),
    ("https://[0:0:0:0:0:0:0:1]:443", "https://[::1]"),
    ("https://bücher.example", "https://xn--bcher-kva.example"),
])
def test_equivalent_targets_share_only_the_conflict_identity(first, second):
    _, original = effect(first)
    _, equivalent = effect(second)
    assert original.target_key != equivalent.target_key
    assert original.digest != equivalent.digest
    assert original.barrier_key == equivalent.barrier_key


def test_unrelated_targets_tenants_and_opaque_adapters_remain_distinct():
    _, original = effect("https://sales.example")
    _, different_port = effect("https://sales.example:8443")
    _, different_host = effect("https://other.example")
    other_quote = original.model_copy(update={"target_key": original.target_key.replace(QUOTE_ID, "00000000-0000-0000-0000-000000000124")})
    other_tenant = original.model_copy(update={"tenant_id": "org:other"})
    assert len({item.barrier_key for item in (original, different_port, different_host, other_quote, other_tenant)}) == 5
    opaque = original.model_copy(update={"action": "opaque.action", "target_key": "Target:Exact/Resource"})
    assert opaque.barrier_key == sha256_digest({"tenant": opaque.tenant_id, "target": opaque.target_key})


def test_existing_approval_and_receipt_keep_the_frozen_release_identity():
    settings, original = effect("https://SALES.example:443/")
    target = DataverseDraftTarget(settings, lambda: "not-used")
    assert original.digest == "sha256:ac2ffb71c0250753e4b9ade439bc40713be13a25020bd12e06325c36c767936a"
    assert target.receipt_id(original) == "2d63ba7c-ada5-576f-8ae8-331d64c98113"
    assert settings.instance_url == "https://SALES.example:443/"
    assert original.barrier_key == "sha256:3886cd7aeb8f7eec25d0809fe0eb0d1ea101d09284cb17a9f364d45e26111fd1"


@pytest.mark.parametrize("origin", [
    "https://sales.example:70000", "https://@sales.example", "https://%73ales.example",
    "https://sales..example", "https://[fe80::1%25eth0]",
])
def test_unrepresentable_target_origins_fail_before_a_worker_can_use_credentials(origin):
    with pytest.raises(ValueError, match="DATAVERSE_TARGET_IDENTITY_INVALID"):
        effect(origin)


def test_equivalent_origin_cannot_bypass_unknown_in_another_postgres_workspace(postgres_runtime):
    database = postgres_runtime()
    server = DataverseProtocolServer()
    with ExitStack() as stack:
        first_store = stack.enter_context(StateStore(database["runtime_dsn"], tenant_id="org:test", migrate=False))
        first_store.register_workspace("second", profile_digest="sha256:" + "b" * 64, pack_digest=None,
                                       quote_object_id="quote:second", created_at="2026-09-09T00:00:00Z")
        second_store = stack.enter_context(StateStore(database["runtime_dsn"], tenant_id="org:test",
                                                      workspace_id="second", migrate=False))
        settings, original = effect("https://SALES.example:443/")
        alternate, next_request = effect("https://sales.example", identifier="effect:second", version='W/"43"')
        target = DataverseDraftTarget(settings, lambda: "test-target-access", transport=httpx.MockTransport(server))
        next_target = DataverseDraftTarget(alternate, lambda: "test-target-access", transport=httpx.MockTransport(server))
        first_gateway = CommitGateway(first_store, tenant_id="org:test", worker_id="first", authorize=lambda *_: None)
        second_gateway = CommitGateway(second_store, tenant_id="org:test", worker_id="second", authorize=lambda *_: None)
        request_bytes = original.model_dump_json()
        request_digest = original.digest
        receipt_id = target.receipt_id(original)
        server.lose_response = True
        with pytest.raises(EffectError, match="TARGET_RESPONSE_UNAVAILABLE"):
            first_gateway.run(original, target)
        server.lose_response = False
        assert second_store.get_effect(original.effect_id) is None
        with pytest.raises(EffectError, match="TARGET_EFFECT_UNRESOLVED"):
            second_gateway.run(next_request, next_target)
        assert server.writes == 1 and len(server.receipts) == 1
        assert first_gateway.run(original, target, query_only=True)["outcome"] == "CONFIRMED"
        assert original.model_dump_json() == request_bytes and original.digest == request_digest
        assert target.receipt_id(original) == receipt_id and receipt_id in server.receipts
        assert second_gateway.run(next_request, next_target)["outcome"] == "CONFIRMED"
        assert server.writes == 2
