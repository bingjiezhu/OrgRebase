from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import update

from orgrebase.auth import Principal, request_principal
from orgrebase.clock import FrozenClock
from orgrebase.database import artifacts
from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.store import StateStore
from orgrebase.workspace import effects
from orgrebase.workspace.enterprise_binding import MEDIA as BINDING_MEDIA
from orgrebase.workspace.enterprise_binding import SEED_ID
from orgrebase.workspace.models import EnterpriseBinding, EnterpriseResourceBinding

MEDIA = "application/vnd.orgrebase.external-operation+json"
NOW = "2026-09-10T00:00:00Z"


@pytest.fixture
def workspace(tmp_path, fixture):
    with StateStore(tmp_path / "effects.sqlite", tenant_id=fixture.organization_id) as store:
        store.load_fixture(fixture)
        quote = next(item for item in fixture.objects if item.state.value in {"CURRENT", "ACTIVE"})
        binding = EnterpriseBinding(organization_id=fixture.organization_id, quote_object_id=quote.id,
            domain_pack_digest=sha256_digest({}), resources=(EnterpriseResourceBinding(
                slot_id="quote", object_id=quote.id, domain_id=quote.domain, owner_id="operator:controlled-local"),))
        with store.transaction() as connection:
            store.save_artifact(connection, SEED_ID, BINDING_MEDIA, binding.model_dump(mode="json"))
        yield SimpleNamespace(
            store=store,
            profile=SimpleNamespace(organization_id=fixture.organization_id),
            approval_identity_mode="CONTROLLED_LOCAL_IDENTITY",
            clock=FrozenClock(NOW),
            effective_workflow_run_id="run:effects",
            current_quote=lambda: store.get_object(quote.id),
            changes=SimpleNamespace(gaps=lambda: ()),
        )


@pytest.fixture
def config(workspace):
    return effects.EffectWorkerConfig(
        target=effects.DataverseDraftTargetSettings(
            tenant_id=workspace.profile.organization_id,
            instance_url="https://sales.example",
            quote_id="00000000-0000-0000-0000-000000000001",
        ),
        owner_id="operator:controlled-local",
        target_token_variable="TARGET_ACCESS",
        access_token_variable="WORKER_ACCESS",
    )


def seed(workspace, config, states, *, expired=False):
    quote = workspace.current_quote()
    runtime = effects.effect_runtime_revision()
    with workspace.store.transaction() as connection:
        for index, state in enumerate(states):
            identity = f"proposal-{index:04d}"
            proposal = {
                "proposal_id": identity,
                "effect_id": f"effect:{identity}",
                "configuration_digest": config.digest,
                "owner_id": config.owner_id,
                "workspace_id": config.workspace_id,
                "tenant_id": config.target.tenant_id,
                "runtime": runtime,
                "expires_at": NOW if expired else "2026-09-10T00:15:00Z",
                "quote_ref": quote.ref,
                "quote_digest": quote.digest,
                "execution_run_id": workspace.effective_workflow_run_id,
            }
            digest = workspace.store.save_artifact(
                connection, f"external-proposal:{identity}@r1", MEDIA, proposal
            )
            if state == "REJECTED":
                workspace.store.save_artifact(
                    connection, f"external-rejection:{identity}@r1", MEDIA,
                    {"proposal_digest": digest, "rejected_at": NOW},
                )
            elif state != "PENDING_APPROVAL":
                workspace.store.save_artifact(
                    connection, f"external-approval:{identity}@r1", MEDIA,
                    {"proposal_digest": digest, "approved_at": NOW},
                )
                workspace.store.put_effect(
                    connection, effect_id=proposal["effect_id"], target_key=config.target.target_key,
                    request_digest=sha256_digest({"proposal_id": identity}), request={}, created_at=NOW,
                )
                if state != "READY":
                    workspace.store.update_effect(
                        connection, effect_id=proposal["effect_id"], expected_state="READY",
                        state=state, updated_at=NOW,
                    )


@pytest.mark.parametrize("count", [1, 30, 500])
def test_list_queries_are_bounded_including_current_quote(workspace, config, monkeypatch, count):
    seed(workspace, config, ["READY"] * count)
    read_quote = Mock(wraps=workspace.current_quote)
    read_runtime = Mock(wraps=effects.effect_runtime_revision)
    read_source_gaps = Mock(wraps=workspace.changes.gaps)
    monkeypatch.setattr(workspace, "current_quote", read_quote)
    monkeypatch.setattr(effects, "effect_runtime_revision", read_runtime)
    monkeypatch.setattr(workspace.changes, "gaps", read_source_gaps)
    statements = []
    workspace.store.connection.set_trace_callback(statements.append)
    try:
        page = effects.list_effect_proposals(workspace, config, limit=count)
    finally:
        workspace.store.connection.set_trace_callback(None)
    selects = [" ".join(sql.lower().split()) for sql in statements if sql.lstrip().lower().startswith("select")]
    business = [sql for sql in selects if any(
        f" from {table} " in sql for table in ("artifacts", "effect_intents", "current_pointers")
    )]
    assert len(page["items"]) == count
    # Four original page reads plus one bounded organization context (pointer, marker, seed).
    assert len(business) == 7
    # Each business read has one combined tenant, recovery, schema and workspace guard.
    assert sum(" from store_metadata" in sql for sql in selects) == 7
    assert len(selects) == 14
    assert read_quote.call_count == read_runtime.call_count == read_source_gaps.call_count == 1


@pytest.mark.parametrize("role", ["operator", "approver", "executor"])
def test_list_matches_details_and_preserves_cursor_order(workspace, config, role):
    states = ["PENDING_APPROVAL", "READY", "REJECTED", "COMMIT_UNKNOWN", "CONFIRMED"]
    seed(workspace, config, states)
    principal = Principal(
        "https://identity.example", "owner", config.target.tenant_id,
        config.owner_id, frozenset({role}), int(time.time()) + 600,
    )
    token = request_principal.set(principal)
    try:
        first = effects.list_effect_proposals(workspace, config, limit=2)
        second = effects.list_effect_proposals(workspace, config, after=first["next_cursor"], limit=3)
        items = first["items"] + second["items"]
        assert [item["state"] for item in items] == states
        assert first["next_cursor"] == "external-proposal:proposal-0001@r1"
        assert second["next_cursor"] is None
        assert items == [effects.effect_detail(workspace, config, f"proposal-{index:04d}") for index in range(5)]
        assert items[0]["approval"] is items[0]["rejection"] is items[0]["effect"] is None
        assert items[2]["approval"] is items[2]["effect"] is None
        assert items[2]["rejection"] is not None
        assert "EXECUTE" not in items[3]["allowed_actions"]
    finally:
        request_principal.reset(token)


@pytest.mark.parametrize("states,expired", [([], False), (["CONFIRMED", "REJECTED"], False), (["READY"], True)])
def test_list_only_reads_quote_when_freshness_requires_it(workspace, config, monkeypatch, states, expired):
    seed(workspace, config, states, expired=expired)
    monkeypatch.setattr(workspace, "current_quote", Mock(side_effect=AssertionError("unnecessary quote read")))
    page = effects.list_effect_proposals(workspace, config)
    assert len(page["items"]) == len(states)
    if expired:
        assert page["items"][0]["blocked_reason"] == "EFFECT_AUTHORITY_EXPIRED"
        assert "EXECUTE" not in page["items"][0]["allowed_actions"]


def test_list_refreshes_quote_and_runtime_on_next_request(workspace, config, monkeypatch):
    seed(workspace, config, ["READY", "READY"])
    assert all(item["blocked_reason"] is None for item in effects.list_effect_proposals(workspace, config)["items"])
    quote = workspace.current_quote()
    monkeypatch.setattr(workspace, "current_quote", lambda: SimpleNamespace(ref=quote.ref, digest="changed"))
    assert {item["blocked_reason"] for item in effects.list_effect_proposals(workspace, config)["items"]} == {
        "EFFECT_SOURCE_QUOTE_CHANGED"
    }
    monkeypatch.setattr(effects, "effect_runtime_revision", lambda: {"revision_digest": "changed"})
    assert {item["blocked_reason"] for item in effects.list_effect_proposals(workspace, config)["items"]} == {
        "EFFECT_RUNTIME_REPLAN_REQUIRED"
    }


def test_list_reuses_source_gaps_within_the_page_and_refreshes_on_next_request(workspace, config, monkeypatch):
    seed(workspace, config, ["READY"] * 30)
    gaps = Mock(return_value=({"slot_id": "launch_date", "state": "STALE"},))
    monkeypatch.setattr(workspace.changes, "gaps", gaps)
    page = effects.list_effect_proposals(workspace, config)
    assert gaps.call_count == 1
    assert {item["blocked_reason"] for item in page["items"]} == {"EFFECT_SOURCE_READMISSION_REQUIRED"}
    assert all("EXECUTE" not in item["allowed_actions"] for item in page["items"])
    gaps.return_value = ()
    page = effects.list_effect_proposals(workspace, config)
    assert gaps.call_count == 2
    assert all(item["blocked_reason"] is None and "EXECUTE" in item["allowed_actions"] for item in page["items"])


@pytest.mark.parametrize("kind", ["proposal", "approval", "rejection"])
def test_list_rejects_corrupt_artifacts_in_every_read_batch(workspace, config, kind):
    seed(workspace, config, ["REJECTED" if kind == "rejection" else "READY"])
    with workspace.store.transaction() as connection:
        workspace.store.execute(connection, update(artifacts).where(
            artifacts.c.artifact_id == f"external-{kind}:proposal-0000@r1"
        ).values(payload_digest="sha256:" + "0" * 64))
    with pytest.raises(IntegrityError, match="ARTIFACT_DIGEST_MISMATCH"):
        effects.list_effect_proposals(workspace, config)


def test_batch_artifact_reads_preserve_media_type_validation(workspace):
    with workspace.store.transaction() as connection:
        workspace.store.save_artifact(connection, "wrong-media", "application/json", {})
    with pytest.raises(IntegrityError, match="ARTIFACT_MEDIA_TYPE_MISMATCH"):
        workspace.store.load_artifacts(["missing", "wrong-media"], MEDIA)


@pytest.mark.parametrize("identities", [["one"] * 1001, "one", [None], [""]])
def test_batch_reads_reject_unbounded_or_invalid_ids(workspace, identities):
    with pytest.raises(ValueError, match="STATE_STORE_BATCH_IDS_INVALID"):
        workspace.store.load_artifacts(identities)
    with pytest.raises(ValueError, match="STATE_STORE_BATCH_IDS_INVALID"):
        workspace.store.get_effects(identities)


def test_batch_reads_use_exact_ids_and_omit_missing_records(workspace):
    identity = "effect:'%_)"
    with workspace.store.transaction() as connection:
        workspace.store.save_artifact(connection, identity, MEDIA, {"id": identity})
        workspace.store.put_effect(
            connection, effect_id=identity, target_key="target", request_digest="digest", request={}, created_at=NOW,
        )
        assert workspace.store.load_artifacts([identity, "missing", identity])[identity] == workspace.store.load_artifact(identity)
        assert workspace.store.get_effects([identity, "missing", identity]) == {
            identity: workspace.store.get_effect(identity, connection=connection)
        }
    assert workspace.store.load_artifacts([]) == workspace.store.get_effects([]) == {}


def test_batch_reads_do_not_cross_postgres_workspace_boundaries(postgres_runtime):
    database = postgres_runtime()
    with StateStore(database["migration_dsn"], tenant_id="org:test", migrate=False) as operator:
        operator.register_workspace(
            "other", profile_digest="sha256:" + "1" * 64, pack_digest=None,
            quote_object_id="quote:other", created_at=NOW,
        )
    for scope in ("default", "other"):
        with StateStore(database["runtime_dsn"], tenant_id="org:test", workspace_id=scope, migrate=False) as store:
            assert store.runtime_role_safe()
            with store.transaction() as connection:
                store.save_artifact(connection, "same-id", MEDIA, {"scope": scope})
                store.put_effect(
                    connection, effect_id=f"effect:{scope}", target_key="target", request_digest=scope,
                    request={"scope": scope}, created_at=NOW,
                )
    for scope in ("default", "other"):
        with StateStore(database["runtime_dsn"], tenant_id="org:test", workspace_id=scope, migrate=False) as store:
            assert store.load_artifacts(["same-id", "missing"], MEDIA)["same-id"].payload == {"scope": scope}
            records = store.get_effects(["effect:default", "effect:other", "missing"])
            assert list(records) == [f"effect:{scope}"]
            assert records[f"effect:{scope}"] == store.get_effect(f"effect:{scope}")
