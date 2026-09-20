from __future__ import annotations

from pathlib import Path

import pytest

from orgrebase.domain import AuthorizationError, IntegrityError, ObjectState
from orgrebase.workspace.change_proposals import ChangeProposalInput, submit_change
from orgrebase.workspace.source_readmission import (
    SourceReadmissionCommand,
    SourceReadmissionInput,
    apply_group,
    approve_group,
    create_group,
    group_detail,
    list_groups,
    reject_group,
)
from tests.workspace.test_continuous_changes import make_service


def prepare(service, slots=("launch_date", "currency"), *, group_id="recover-1"):
    refs = []
    for index, slot in enumerate(slots):
        service.invalidate_source(slot, f"https://source.example/{slot}", "SOURCE_FIELD_MISSING")
        binding = next(item for item in service.enterprise_binding.resources if item.slot_id == slot)
        current = service.store.get_object(binding.object_id)
        result = submit_change(service, ChangeProposalInput(
            event_id=f"{group_id}-source-{index}", slot_id=slot,
            base_version=current.version, base_digest=current.digest,
            value=str(current.payload["canonical_value"]), source_ref=f"https://source.example/{slot}/verified",
            reason="责任人重新读取完整来源", operation="READMIT",
        ))
        refs.append({"event_id": result["event_id"], "event_digest": result["event_digest"]})
    return create_group(service, SourceReadmissionInput(group_id=group_id, events=refs, reason="共同恢复报价来源"))


def command(detail, owner=None, *, actor=None, reason=None):
    return SourceReadmissionCommand(group_digest=detail["group"]["digest"],
        preview_digest=detail["group"]["preview"]["digest"], owner_id=owner,
        actor_id=actor or owner, reason=reason)


def assert_advisory_covers_source_domains(service, group):
    expected = {}
    for delta in group["change_set"]["deltas"]:
        source = service.store.get_object(delta["object_id"], delta["base_version"])
        expected.setdefault(source.domain, set()).add(source.id)
    advisory = group["advisory"]
    tasks = advisory["orchestration_plan"]["tasks"]
    explanations = [task for task in tasks if task["allowed_output_kinds"] == ["SemanticExplanation"]]
    assert len(explanations) == len(expected)
    assert {task["authority_domain"]: set(task["context_scope"]) for task in explanations} == expected
    impact = next(task for task in tasks if task["allowed_output_kinds"] == ["ImpactCandidate"])
    assert set(impact["depends_on"]) == {task["id"] for task in explanations}
    sources_by_task = {task["id"]: expected[task["authority_domain"]] for task in explanations}
    all_sources = set().union(*expected.values())
    assert {handoff["task_id"] for handoff in advisory["handoffs"]} == {task["id"] for task in tasks}
    for handoff in advisory["handoffs"]:
        assert set(handoff["payload"]["change_object_ids"]) == sources_by_task.get(handoff["task_id"], all_sources)
        assert handoff["candidate_only"] is True
        assert handoff["payload"]["target_writes"] == 0
    assert advisory["ingestion_receipt"]["target_writes"] == 0
    assert all(not item["admitted_effects"] for item in advisory["ingestion_receipt"]["decisions"])


def test_two_sources_require_both_owners_and_commit_one_successor(tmp_path: Path):
    service = make_service(tmp_path / "group.sqlite")
    try:
        before = service.current_quote()
        detail = prepare(service)
        assert_advisory_covers_source_domains(service, detail["group"])
        assert detail["group"]["preview"]["state"] == "READY"
        assert len(detail["owners"]) == 2
        assert detail["allowed_actions"] == []
        owners = [item["owner_id"] for item in detail["owners"]]
        first = approve_group(service, "recover-1", command(detail, owners[0]))
        assert first["state"] == "REVIEW"
        assert service.current_quote().digest == before.digest
        for spec in detail["group"]["source_specs"]:
            assert service.store.get_object(spec["object_id"]).state == ObjectState.STALE
        with pytest.raises(IntegrityError, match="APPROVALS_INCOMPLETE"):
            apply_group(service, "recover-1", command(detail))
        with pytest.raises(AuthorizationError, match="APPROVER_MISMATCH"):
            approve_group(service, "recover-1", command(detail, owners[1], actor=owners[0]))
        second = approve_group(service, "recover-1", command(detail, owners[1]))
        assert second["allowed_actions"] == ["APPLY"]
        result = apply_group(service, "recover-1", command(detail))
        assert result["state"] == "APPLIED"
        assert service.current_quote().version == f"v{int(before.version[1:]) + 1}"
        assert service.current_quote().payload["launch_date"] == before.payload["launch_date"]
        assert service.current_quote().payload["currency"] == before.payload["currency"]
        assert result["outcome"]["rebase_receipt"]["metrics"]["facts_changed"] == 2
        assert result["outcome"]["rebase_receipt"]["metrics"]["work_items_rebased"] == 1
        assert len(result["outcome"]["rebase_receipt"]["approval_set"]["members"]) == 2
        assert service.changes.pending_count == 0
        assert service._business_complete()
        assert list_groups(service)["items"][0]["state"] == "APPLIED"
        assert apply_group(service, "recover-1", command(detail))["outcome"] == result["outcome"]
        for ref in detail["group"]["events"]:
            assert service._change_status(ref["event_id"]) == "GROUP_APPLIED"
        archive = service.completion_history()
        assert archive["source_readmission_groups"][0]["outcome"] == result["outcome"]
    finally:
        service.close()


@pytest.mark.parametrize("slots", [("currency", "launch_date", "product_plan"), ("product_plan", "launch_date")])
def test_source_scopes_share_only_their_actual_owner_and_source_order_is_stable(tmp_path: Path, slots):
    service = make_service(tmp_path / "scopes.sqlite")
    try:
        detail = prepare(service, slots)
        group = detail["group"]
        assert_advisory_covers_source_domains(service, group)
        for owner in detail["owners"]:
            approved = approve_group(service, group["id"], command(detail, owner["owner_id"]))
            member = next(item for item in approved["owners"] if item["owner_id"] == owner["owner_id"])
            assert member["source_approval"]["approval"]["authority_scope"] == sorted([group["change_set"]["id"], *owner["source_ids"]])
            assert member["review_evidence"]["review_wait_satisfied"] is True
        same = create_group(service, SourceReadmissionInput(group_id=group["id"], events=list(reversed(group["events"])), reason=group["reason"]))
        assert same["group"]["digest"] == group["digest"]
        result = apply_group(service, group["id"], command(detail))
        assert len(result["outcome"]["rebase_receipt"]["applied_claims"]) == len(slots)
        assert len(result["outcome"]["rebase_receipt"]["transitions"]) == 1
        included = result["outcome"]["rebase_receipt"]["context_manifests"][0]["included"]
        for spec in group["source_specs"]:
            assert any(item["object_ref"] == f"{spec['object_id']}@{spec['proposed_version']}" for item in included)
    finally:
        service.close()


@pytest.mark.parametrize("failure", ["first-source", "prepared", "graph-pointer", "group-outcome", "base-receipt", "idempotency"])
def test_every_source_group_write_rolls_back_as_one_unit(tmp_path: Path, monkeypatch, failure):
    from orgrebase.workspace.source_readmission import _key
    service = make_service(tmp_path / "rollback.sqlite")
    try:
        detail = prepare(service, ("launch_date", "currency", "product_plan"))
        for owner in detail["owners"]:
            approve_group(service, "recover-1", command(detail, owner["owner_id"]))
        before_quote, before_pointer = service.current_quote(), service.current_graph_pointer()
        before_sources = [service.store.get_object(spec["object_id"]) for spec in detail["group"]["source_specs"]]
        before_chain = service.store.verify_event_chain()
        if failure == "first-source":
            original = service.store.promote_version
            def fail(*args, **kwargs):
                original(*args, **kwargs)
                raise RuntimeError("injected source promotion failure")
            monkeypatch.setattr(service.store, "promote_version", fail)
        elif failure in {"group-outcome", "base-receipt"}:
            original = service.store.save_artifact
            def fail(connection, artifact_id, media_type, payload):
                result = original(connection, artifact_id, media_type, payload)
                if ((failure == "group-outcome" and artifact_id == _key("outcome", "recover-1"))
                        or (failure == "base-receipt" and media_type == "application/vnd.orgrebase.rebase-receipt+json")):
                    raise RuntimeError("injected receipt failure")
                return result
            monkeypatch.setattr(service.store, "save_artifact", fail)
        elif failure == "idempotency":
            original = service.store.save_idempotent
            def fail(*args, **kwargs):
                original(*args, **kwargs)
                raise RuntimeError("injected idempotency failure")
            monkeypatch.setattr(service.store, "save_idempotent", fail)
        with pytest.raises(RuntimeError):
            apply_group(service, "recover-1", command(detail), fail_after=failure if failure in {"prepared", "graph-pointer"} else None)
        assert service.current_quote() == before_quote
        assert service.current_graph_pointer() == before_pointer
        assert [service.store.get_object(item.id) for item in before_sources] == before_sources
        assert service.store.verify_event_chain() == before_chain
        assert group_detail(service, "recover-1")["outcome"] is None
        assert service.changes.pending_count == 3
    finally:
        service.close()


def test_group_rejection_blocks_old_approval_and_members_cannot_escape_group(tmp_path: Path):
    service = make_service(tmp_path / "rejection.sqlite")
    try:
        detail = prepare(service)
        owner = detail["owners"][0]["owner_id"]
        approve_group(service, "recover-1", command(detail, owner))
        ref = detail["group"]["events"][0]
        with pytest.raises(IntegrityError, match="SOURCE_GROUP_COMMAND_REQUIRED"):
            service.preview_change(ref["event_id"])
        with pytest.raises(IntegrityError, match="SOURCE_GROUP_COMMAND_REQUIRED"):
            service.reject_change(ref["event_id"], actor_id=owner, reason="cannot detach member")
        rejection = reject_group(service, "recover-1", command(detail, owner, reason="来源仍不可靠"))
        assert rejection["state"] == "REJECTED"
        with pytest.raises(IntegrityError, match="SOURCE_GROUP_REJECTED"):
            apply_group(service, "recover-1", command(detail))
        assert service.changes.pending_count == 0
        assert not service._business_complete()  # Invalidated sources remain unresolved.
        for ref in detail["group"]["events"]:
            assert service._change_status(ref["event_id"]) == "REJECTED"
    finally:
        service.close()


def test_group_cannot_be_rejected_after_committed_outcome_on_reopen(tmp_path: Path):
    service = make_service(tmp_path / "restart.sqlite")
    try:
        detail = prepare(service)
        for owner in detail["owners"]:
            approve_group(service, "recover-1", command(detail, owner["owner_id"]))
        original = apply_group(service, "recover-1", command(detail))["outcome"]
        runtime = service.runtime_configuration
        service.close()
        from orgrebase.workspace.service import WorkspaceService
        service = WorkspaceService.reopen(tmp_path / "restart.sqlite", runtime_configuration=runtime)
        assert apply_group(service, "recover-1", command(detail))["outcome"] == original
        with pytest.raises(IntegrityError, match="SOURCE_GROUP_ALREADY_APPLIED"):
            reject_group(service, "recover-1", command(detail, detail["owners"][0]["owner_id"], reason="too late"))
    finally:
        service.close()


def test_group_common_predecessor_drift_denies_all_source_promotions(tmp_path: Path):
    service = make_service(tmp_path / "drift.sqlite")
    try:
        detail = prepare(service)
        for owner in detail["owners"]:
            approve_group(service, "recover-1", command(detail, owner["owner_id"]))
        with service.store.transaction() as connection:
            service.store.transition_current(connection, service.quote_object_id, ObjectState.STALE)
        before = service.store.verify_event_chain()
        from orgrebase.domain import FreshnessError
        with pytest.raises(FreshnessError, match="SOURCE_GROUP_BASE_CHANGED"):
            apply_group(service, "recover-1", command(detail))
        assert service.store.verify_event_chain() == before
        for spec in detail["group"]["source_specs"]:
            assert service.store.get_object(spec["object_id"]).state == ObjectState.STALE
    finally:
        service.close()


@pytest.mark.parametrize("revoked", [None, "grant", "membership", "scope", "expiry"])
def test_group_preserves_original_owner_and_rechecks_delegate_at_apply(tmp_path: Path, revoked):
    import time
    from contextlib import contextmanager
    from datetime import timedelta

    from orgrebase.auth import AuthenticationError, Principal, request_principal
    from orgrebase.clock import FrozenClock, timestamp, utc_datetime
    from orgrebase.workspace.approval_authority import (
        CoordinationInput,
        DelegationInput,
        grant_delegation,
        revoke_delegation,
    )
    service = make_service(tmp_path / "delegated.sqlite")
    try:
        detail = prepare(service)
        owner = detail["owners"][0]["owner_id"]
        members = {item["owner_id"]: item["owner_id"] for item in detail["owners"]}
        members["delegate"] = "actor:delegate"
        allowed = set(members)
        service.identity_issuer = "https://identity.example"
        def verify(subject, actor, action):
            if members.get(subject) != actor or action != "approve":
                raise AuthenticationError("AUTH_MEMBERSHIP_DENIED", 403)
        def scope(subject):
            if subject not in allowed:
                raise AuthenticationError("AUTH_WORKSPACE_DENIED", 403)
        service.verify_membership = verify
        service.authorize_workspace_subject = scope
        @contextmanager
        def identity(subject, role="approver"):
            token = request_principal.set(Principal(service.identity_issuer, subject, service.profile.organization_id,
                members.get(subject, subject), frozenset({role}), int(time.time()) + 900))
            try:
                yield
            finally:
                request_principal.reset(token)
        event = next(event for event in detail["source_events"] if event["owner_id"] == owner)
        with identity(owner):
            grant_delegation(service, event["event_id"], DelegationInput(subject="delegate", actor_id="actor:delegate",
                event_digest=event["digest"], reason="负责人出差期间授权", valid_seconds=60))
        with identity("delegate"):
            approved = approve_group(service, "recover-1", command(detail, owner, actor="actor:delegate"))
            member = next(item for item in approved["owners"] if item["owner_id"] == owner)
            assert member["source_approval"]["owner_id"] == owner
            assert member["source_approval"]["approval"]["actor_id"] == "actor:delegate"
            assert member["authorities"][event["event_id"]]["grant"]["owner"]["actor_id"] == owner
        other = next(item["owner_id"] for item in detail["owners"] if item["owner_id"] != owner)
        with identity(other):
            approve_group(service, "recover-1", command(detail, other))
        if revoked == "grant":
            with identity(owner):
                revoke_delegation(service, event["event_id"], CoordinationInput(event_digest=event["digest"], reason="收回授权"))
        elif revoked == "membership":
            members.pop("delegate")
        elif revoked == "scope":
            allowed.remove("delegate")
        elif revoked == "expiry":
            service.clock = FrozenClock(timestamp(utc_datetime(service.clock.now()) + timedelta(seconds=61)))
        before = service.current_quote()
        with identity("executor", "executor"):
            if revoked is None:
                assert apply_group(service, "recover-1", command(detail))["state"] == "APPLIED"
            else:
                with pytest.raises((AuthenticationError, AuthorizationError, RuntimeError)):
                    apply_group(service, "recover-1", command(detail))
                assert service.current_quote() == before
                for spec in detail["group"]["source_specs"]:
                    assert service.store.get_object(spec["object_id"]).state == ObjectState.STALE
    finally:
        service.close()


def test_group_impact_unknown_source_cannot_be_hidden_by_another_hard_source(tmp_path: Path):
    from orgrebase.impact import ImpactEngine
    from orgrebase.workspace.source_readmission import _fixture, load_group
    service = make_service(tmp_path / "unknown.sqlite")
    try:
        prepare(service)
        group = load_group(service, "recover-1")
        fixture = _fixture(service, group)
        target = service.quote_object_id
        currency = "policy:finance.currency"
        changed = tuple(edge.model_copy(update={"relation": "UNRECOGNIZED_EXTERNAL_RELATION", "digest": ""})
                        if edge.source_id == currency and edge.target_id == target else edge for edge in fixture.dependencies)
        fixture = fixture.model_copy(update={"dependencies": changed})
        result = ImpactEngine(fixture).preview(group.change_set)
        quote = next(item for item in result.results if item.object_id == target)
        assert quote.classification == "UNKNOWN"
        assert result.state == "BLOCKED"
        certificate = next(item for item in result.certificates if item.subject_id == target)
        witnesses = certificate.traversal_commitment["source_witnesses"]
        assert {item["result"]["classification"] for item in witnesses} == {"AFFECTED_HARD", "UNKNOWN"}
        assert len(witnesses) == 2
    finally:
        service.close()


def test_group_certificate_rejects_resigned_missing_source_witness(tmp_path: Path):
    from orgrebase.certificates import ImpactCertificateVerifier
    from orgrebase.domain import ImpactCertificate
    from orgrebase.workspace.source_readmission import _fixture, load_group
    service = make_service(tmp_path / "certificate.sqlite")
    try:
        prepare(service)
        group = load_group(service, "recover-1")
        certificate = next(item for item in group.preview.certificates if item.subject_id == service.quote_object_id)
        payload = certificate.model_dump(mode="json")
        payload["traversal_commitment"]["source_witnesses"].pop()
        payload.pop("digest")
        forged = ImpactCertificate.model_validate(payload)
        with pytest.raises(IntegrityError, match="canonical recomputation"):
            ImpactCertificateVerifier(_fixture(service, group)).verify(forged.model_dump(mode="json"), group.change_set)
    finally:
        service.close()


def test_final_completion_expiry_rolls_back_after_receipt_and_idempotency_writes(tmp_path: Path, monkeypatch):
    from datetime import timedelta

    from orgrebase.clock import FrozenClock, timestamp, utc_datetime
    service = make_service(tmp_path / "late-expiry.sqlite")
    try:
        detail = prepare(service)
        for owner in detail["owners"]:
            approve_group(service, "recover-1", command(detail, owner["owner_id"]))
        original = service.store.save_idempotent
        def expire(*args, **kwargs):
            original(*args, **kwargs)
            service.clock = FrozenClock(timestamp(utc_datetime(service.clock.now()) + timedelta(minutes=16)))
        monkeypatch.setattr(service.store, "save_idempotent", expire)
        before = service.current_quote()
        chain = service.store.verify_event_chain()
        with pytest.raises(RuntimeError, match="EXPIRED"):
            apply_group(service, "recover-1", command(detail))
        assert service.current_quote() == before
        assert service.store.verify_event_chain() == chain
        assert group_detail(service, "recover-1")["state"] == "EXPIRED"
        for owner in group_detail(service, "recover-1")["owners"]:
            assert owner["allowed_actions"] == ["REJECT"]
    finally:
        service.close()


@pytest.mark.parametrize("kind", ["outcome", "approval", "group", "rejection"])
def test_resigned_projection_artifact_cannot_replace_journal_committed_proof(tmp_path: Path, kind):
    from sqlalchemy import update

    from orgrebase.database import artifacts
    from orgrebase.digest import canonical_json, sha256_digest
    from orgrebase.workspace.source_readmission import MEDIA, _approval_key, _key
    service = make_service(tmp_path / "anchor.sqlite")
    try:
        detail = prepare(service)
        for owner in detail["owners"]:
            approve_group(service, "recover-1", command(detail, owner["owner_id"]))
        if kind == "rejection":
            reject_group(service, "recover-1", command(detail, detail["owners"][0]["owner_id"], reason="Reject pending review"))
        else:
            apply_group(service, "recover-1", command(detail))
        identity = _approval_key("recover-1", detail["owners"][0]["owner_id"]) if kind == "approval" else "recover-1"
        key = _key(kind, identity)
        payload = service.store.load_artifact(key, MEDIA).payload
        if kind == "outcome":
            payload["quote"]["payload"]["product_plan"] = "UNCOMMITTED DISPLAY"
        elif kind == "approval":
            payload["review_evidence"]["approved_at_epoch_ms"] += 1
        elif kind == "group":
            payload["reason"] = "UNCOMMITTED REASON"
            from orgrebase.workspace.source_readmission import SourceReadmissionGroup
            payload.pop("digest")
            payload = SourceReadmissionGroup.model_validate(payload).model_dump(mode="json")
        else:
            payload["reason"] = "UNCOMMITTED REJECTION"
        before = service.store.verify_event_chain()
        with service.store.transaction() as connection:
            service.store.execute(connection, update(artifacts).where(artifacts.c.artifact_id == key).values(
                payload_json=canonical_json(payload), payload_digest=sha256_digest(payload)))
        with pytest.raises(IntegrityError, match="EVENT_BINDING_INVALID"):
            group_detail(service, "recover-1")
        assert service.store.verify_event_chain() == before
    finally:
        service.close()


def test_http_group_routes_require_exact_digest_and_explicit_owner_scope(tmp_path: Path):
    from fastapi.testclient import TestClient

    from orgrebase.api import create_app
    service = make_service(tmp_path / "http.sqlite")
    try:
        detail = prepare(service)
        with TestClient(create_app(workspace_service=service)) as client:
            assert client.get("/api/workspace/source-readmission-groups").status_code == 200
            assert client.get("/api/workspace/source-readmission-groups/recover-1").json()["group"]["digest"] == detail["group"]["digest"]
            payload = command(detail).model_dump(mode="json")
            denied = client.post("/api/workspace/source-readmission-groups/recover-1/approve", json=payload)
            assert denied.status_code == 403
            payload["group_digest"] = "sha256:" + "0" * 64
            assert client.post("/api/workspace/source-readmission-groups/recover-1/apply", json=payload).status_code == 409
            for owner in detail["owners"]:
                response = client.post("/api/workspace/source-readmission-groups/recover-1/approve",
                                       json=command(detail, owner["owner_id"]).model_dump(mode="json"))
                assert response.status_code == 200, response.json()
            assert client.post("/api/workspace/source-readmission-groups/recover-1/apply", json=command(detail).model_dump(mode="json")).json()["state"] == "APPLIED"
    finally:
        service.close()
