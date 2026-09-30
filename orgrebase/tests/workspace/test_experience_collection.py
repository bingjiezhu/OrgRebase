from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from orgrebase.auth import AuthenticationError, Principal, request_principal
from orgrebase.change_events import ChangeEvent
from orgrebase.clock import FrozenClock
from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, IntegrityError, ObjectState, VersionedObject
from orgrebase.private_records import PrivateRecordStore
from orgrebase.store import StateStore
from orgrebase.workspace.change_recovery import REQUEST_MEDIA, RESUME_MEDIA, _prefix, _sealed
from orgrebase.workspace.experience_collection import (
    COLLECTION_MEDIA,
    OBSERVATION_MEDIA,
    ExperienceCollector,
)
from orgrebase.workspace.experience_contracts import CaseObservationV2
from orgrebase.workspace.service import WORKSPACE_OUTCOME_MEDIA_TYPE


class _Changes:
    def __init__(self, event):
        self.event = event

    def get(self, event_id):
        if event_id != self.event.event_id:
            raise KeyError(event_id)
        return self.event


def _workspace(path, *, persist_request=True, source_backed=True):
    store = StateStore(path, tenant_id="org:test")
    source_ref = "https://source.example/quotes(11111111-1111-1111-1111-111111111111)#product_plan"
    page_ref = "source-page:test"
    observation_ref = "source-observation:" + sha256_digest({
        "connector": "test-connector", "record": "11111111-1111-1111-1111-111111111111",
        "revision": "rev:1", "field": "product_plan",
    })[7:]
    proposal = VersionedObject(
        id="claim:product.enterprise_plan",
        version="source-observed-v2" if source_backed else "v2",
        kind="ClaimVersion",
        label="Product plan", domain="product", state=ObjectState.PROPOSED,
        payload={
            "canonical_value": "private business value",
            **({"source_observation_ref": observation_ref} if source_backed else {}),
        },
        source_refs=(source_ref, page_ref) if source_backed else ("source:human-assertion@v2",),
        allowed_purposes=("change_rebase",),
    )
    event = ChangeEvent(
        event_id="recovery-case", organization_id="org:test",
        slot_id="product_plan", owner_id="owner:product",
        base_version="v1", base_digest=sha256_digest({"base": 1}),
        proposal=proposal, occurred_at="2026-09-28T00:00:00Z",
    )
    workspace = SimpleNamespace(
        store=store, profile=SimpleNamespace(organization_id="org:test"),
        approval_identity_mode="VERIFIED_PRINCIPAL_IDENTITY",
        changes=_Changes(event), effective_workflow_run_id="run:case",
        clock=FrozenClock("2026-09-28T00:00:00Z"),
        private_records=PrivateRecordStore(store, FrozenClock("2026-09-28T00:00:00Z")),
    )
    request = _sealed({
        "event_id": event.event_id, "event_digest": event.digest, "round": 1,
        "previous_request_digest": None, "workspace_id": store.workspace_id,
        "run_id": workspace.effective_workflow_run_id,
        "required_evidence_refs": ["claim:product.enterprise_plan@v1"],
        "reason": "Private source review note",
    })
    resume = _sealed({
        "request_digest": request["digest"], "event_digest": event.digest,
        "executor": {"executor_id": "product-steward"},
        "evidence": [],
    })
    with store.transaction() as connection:
        if source_backed:
            store.save_artifact(connection, observation_ref, "application/json", {
                "source_ref": source_ref, "revision": "rev:1",
                "value_digest": sha256_digest("private business value"),
                "page_ref": page_ref, "observed_at": proposal.valid_from,
            })
        if persist_request:
            store.save_artifact(connection, _prefix(event.event_id) + "request:0001", REQUEST_MEDIA, request)
        store.save_artifact(connection, _prefix(event.event_id) + "resume:0001", RESUME_MEDIA, resume)
    return workspace, event, request, resume


def _principal(roles=frozenset({"reader"})):
    return Principal(
        issuer="local:test", subject="collector-service", tenant_id="org:test",
        actor_id="actor:collector", roles=roles, expires_at=4_102_444_800,
    )


def test_continuous_page_observes_partial_recovery_with_private_episode(tmp_path):
    workspace, event, request, resume = _workspace(tmp_path / "experience.sqlite")
    try:
        store = workspace.store
        store.record_event("UNRELATED", {"kind": "other"})
        store.record_event("WORKSPACE_CHANGE_EVIDENCE_REQUESTED", {
            "event_id": event.event_id, "round": 1,
            "recovery_digest": request["digest"], "actor_id": "owner:product",
        })
        store.record_event("WORKSPACE_CHANGE_EVIDENCE_SUPPLIED", {
            "event_id": event.event_id, "round": 1,
            "recovery_digest": resume["digest"], "actor_id": "owner:product",
        })
        # The retained event belongs to the original run, not the run active
        # when a later collector catches up.
        workspace.effective_workflow_run_id = "run:later"
        collector = ExperienceCollector(workspace, collector_actor_id="actor:collector", enabled=True)
        token = request_principal.set(_principal())
        try:
            assert collector.collect(worker_id="worker:one", limit=1).skipped == 1
            first = collector.collect(worker_id="worker:one", limit=1)
            second = collector.collect(worker_id="worker:one", limit=1)
            assert first.status == "PARTIAL" and first.observed == 1
            assert second.status == "COMPLETE" and second.observed == 1
            assert collector.collect(worker_id="worker:one").status == "IDLE"
            rows = store.list_artifacts(
                artifact_id_prefix="experience-collection:", expected_media_type=COLLECTION_MEDIA,
            )
            rows = sorted(rows, key=lambda item: item.payload["origin_sequence"])
            assert len(rows) == 2
            cases = [CaseObservationV2.model_validate(store.load_artifact(
                row.payload["case_ref"], OBSERVATION_MEDIA,
            ).payload) for row in rows]
            assert [case.execution_outcome for case in cases] == ["PENDING", "PENDING"]
            assert [case.resume_digest for case in cases] == [None, resume["digest"]]
            assert all(case.learning_assessment == "UNASSESSED" for case in cases)
            assert cases[0].independence_cluster_id == cases[1].independence_cluster_id
            assert all(case.cluster_status == "PROVISIONAL" for case in cases)
            assert "private business value" not in str([case.model_dump() for case in cases])
            private = workspace.private_records.read_owned(
                cases[0].private_episode_ref,
                owner_id="actor:collector", scope_ref=f"experience:{cases[0].case_id}",
            )
            assert private["request"]["reason"] == "Private source review note"
        finally:
            request_principal.reset(token)
    finally:
        workspace.store.close()


def test_case_quarantine_advances_only_verified_chain(tmp_path):
    workspace, event, request, _ = _workspace(tmp_path / "experience.sqlite")
    try:
        store = workspace.store
        store.record_event("WORKSPACE_CHANGE_EVIDENCE_REQUESTED", {
            "event_id": event.event_id, "round": 1,
            "recovery_digest": sha256_digest("wrong"), "actor_id": "owner:product",
        })
        store.record_event("WORKSPACE_CHANGE_EVIDENCE_REQUESTED", {
            "event_id": event.event_id, "round": 1,
            "recovery_digest": request["digest"], "actor_id": "owner:product",
        })
        collector = ExperienceCollector(workspace, collector_actor_id="actor:collector", enabled=True)
        token = request_principal.set(_principal())
        try:
            result = collector.collect(worker_id="worker:one", limit=10)
            assert (result.observed, result.quarantined, result.cursor_sequence) == (1, 1, 2)
            quarantines = collector.quarantined()
            assert len(quarantines) == 1
            assert quarantines[0]["reason_code"] == "EXPERIENCE_RECOVERY_REQUEST_BINDING_MISMATCH"
            assert "wrong" not in str(quarantines)
            retry = collector.revisit_quarantined(
                origin_event_digest=quarantines[0]["origin_event_digest"],
                attempt_id="review:one",
            )
            assert retry["status"] == "QUARANTINED"
            assert collector.revisit_quarantined(
                origin_event_digest=quarantines[0]["origin_event_digest"],
                attempt_id="review:one",
            ) == retry
            assert collector.collect(worker_id="worker:two").status == "IDLE"
        finally:
            request_principal.reset(token)
    finally:
        workspace.store.close()


def test_quarantine_revisit_after_source_receipt_arrives_keeps_original(tmp_path):
    workspace, event, request, _ = _workspace(
        tmp_path / "experience.sqlite", persist_request=False,
    )
    try:
        store = workspace.store
        origin = store.record_event("WORKSPACE_CHANGE_EVIDENCE_REQUESTED", {
            "event_id": event.event_id, "round": 1,
            "recovery_digest": request["digest"], "actor_id": "owner:product",
        })
        collector = ExperienceCollector(workspace, collector_actor_id="actor:collector", enabled=True)
        token = request_principal.set(_principal())
        try:
            first = collector.collect(worker_id="worker:one")
            assert first.quarantined == 1 and first.cursor_sequence == 1
            with store.transaction() as connection:
                store.save_artifact(
                    connection, _prefix(event.event_id) + "request:0001", REQUEST_MEDIA, request,
                )
            repaired = collector.revisit_quarantined(
                origin_event_digest=origin, attempt_id="review:after-source",
            )
            assert repaired["status"] == "OBSERVED"
            assert store.load_artifact(repaired["case_ref"], OBSERVATION_MEDIA).payload["execution_outcome"] == "PENDING"
            assert collector.quarantined()[0]["status"] == "QUARANTINED"
            assert collector.collect(worker_id="worker:one").cursor_sequence == 1
        finally:
            request_principal.reset(token)
    finally:
        workspace.store.close()


def test_collector_requires_a_distinct_reader_service_identity(tmp_path):
    workspace, _, _, _ = _workspace(tmp_path / "experience.sqlite")
    try:
        collector = ExperienceCollector(workspace, collector_actor_id="actor:collector", enabled=True)
        with pytest.raises(AuthenticationError, match="PRINCIPAL_REQUIRED"):
            collector.collect(worker_id="worker:one")
        token = request_principal.set(_principal(frozenset({"administrator"})))
        try:
            with pytest.raises(AuthorizationError, match="SCOPE_DENIED"):
                collector.collect(worker_id="worker:one")
        finally:
            request_principal.reset(token)
        assert workspace.store.get_source_checkpoint(collector.connector_id) is None
    finally:
        workspace.store.close()


def test_two_workers_share_fenced_cursor_and_failed_case_write_rolls_back(tmp_path, monkeypatch):
    path = tmp_path / "experience.sqlite"
    one, event, request, _ = _workspace(path)
    two, _, _, _ = _workspace(path)
    try:
        one.store.record_event("WORKSPACE_CHANGE_EVIDENCE_REQUESTED", {
            "event_id": event.event_id, "round": 1,
            "recovery_digest": request["digest"], "actor_id": "owner:product",
        })
        first = ExperienceCollector(one, collector_actor_id="actor:collector", enabled=True)
        second = ExperienceCollector(two, collector_actor_id="actor:collector", enabled=True)
        token = request_principal.set(_principal())
        try:
            claim = first._claim("worker:one", time.time())
            assert claim is not None
            assert second.collect(worker_id="worker:two").status == "BUSY"
            first._release("worker:one", claim["fence"])
            original_write = two.private_records.write

            def fail_before_commit(*args, **kwargs):
                raise RuntimeError("simulated private write failure")

            monkeypatch.setattr(two.private_records, "write", fail_before_commit)
            with pytest.raises(RuntimeError, match="simulated private write failure"):
                second.collect(worker_id="worker:two")
            assert two.store.get_source_checkpoint(second.connector_id)["cursor"] is None
            assert not two.store.list_artifacts(artifact_id_prefix="experience-collection:")
            monkeypatch.setattr(two.private_records, "write", original_write)
            assert second.collect(worker_id="worker:two").observed == 1
            assert first.collect(worker_id="worker:one").status == "IDLE"
        finally:
            request_principal.reset(token)
    finally:
        one.store.close()
        two.store.close()


def test_chain_gap_stops_page_without_admitting_case_or_advancing_cursor(tmp_path, monkeypatch):
    workspace, event, request, _ = _workspace(tmp_path / "experience.sqlite")
    try:
        workspace.store.record_event("UNRELATED", {"kind": "one"})
        workspace.store.record_event("WORKSPACE_CHANGE_EVIDENCE_REQUESTED", {
            "event_id": event.event_id, "round": 1,
            "recovery_digest": request["digest"], "actor_id": "owner:product",
        })
        collector = ExperienceCollector(workspace, collector_actor_id="actor:collector", enabled=True)
        original_page = workspace.store.event_page

        def gap(*args, **kwargs):
            page = original_page(*args, **kwargs)
            return {**page, "items": page["items"][1:]}

        token = request_principal.set(_principal())
        try:
            monkeypatch.setattr(workspace.store, "event_page", gap)
            with pytest.raises(IntegrityError, match="CHAIN_DISCONTINUITY"):
                collector.collect(worker_id="worker:one")
            assert workspace.store.get_source_checkpoint(collector.connector_id)["cursor"] is None
            assert not workspace.store.list_artifacts(artifact_id_prefix="experience-collection:")
        finally:
            request_principal.reset(token)
    finally:
        workspace.store.close()


def test_historical_outcome_uses_earlier_round_even_after_later_request(tmp_path):
    workspace, event, request, resume = _workspace(tmp_path / "experience.sqlite")
    try:
        store = workspace.store
        store.record_event("WORKSPACE_CHANGE_EVIDENCE_REQUESTED", {
            "event_id": event.event_id, "round": 1,
            "recovery_digest": request["digest"], "actor_id": "owner:product",
        })
        store.record_event("WORKSPACE_CHANGE_EVIDENCE_SUPPLIED", {
            "event_id": event.event_id, "round": 1,
            "recovery_digest": resume["digest"], "actor_id": "owner:product",
        })
        artifact_id = f"workspace-outcome:{event.event_id}@r1"
        outcome = {"kind": event.event_id, "approval_digest": sha256_digest("approval")}
        with store.transaction() as connection:
            outcome_digest = store.save_artifact(
                connection, artifact_id, WORKSPACE_OUTCOME_MEDIA_TYPE, outcome,
            )
            store.append_event(connection, "WORKSPACE_CHANGE_OUTCOME_RECORDED", {
                "kind": event.event_id, "artifact_id": artifact_id,
                "artifact_digest": outcome_digest,
                "approval_digest": outcome["approval_digest"],
            })
        second_request = _sealed({
            **{key: value for key, value in request.items() if key != "digest"},
            "round": 2, "previous_request_digest": request["digest"],
        })
        with store.transaction() as connection:
            store.save_artifact(
                connection, _prefix(event.event_id) + "request:0002", REQUEST_MEDIA,
                second_request,
            )
            store.append_event(connection, "WORKSPACE_CHANGE_EVIDENCE_REQUESTED", {
                "event_id": event.event_id, "round": 2,
                "recovery_digest": second_request["digest"], "actor_id": "owner:product",
            })
        collector = ExperienceCollector(workspace, collector_actor_id="actor:collector", enabled=True)
        token = request_principal.set(_principal())
        try:
            result = collector.collect(worker_id="worker:one")
            assert result.observed == 4 and result.quarantined == 0
            rows = sorted(store.list_artifacts(
                artifact_id_prefix="experience-collection:", expected_media_type=COLLECTION_MEDIA,
            ), key=lambda row: row.payload["origin_sequence"])
            applied = CaseObservationV2.model_validate(store.load_artifact(
                rows[2].payload["case_ref"], OBSERVATION_MEDIA,
            ).payload)
            assert applied.execution_outcome == "APPLIED"
            assert applied.request_digest == request["digest"]
            assert applied.resume_digest == resume["digest"]
            assert applied.outcome_artifact_digest == outcome_digest
            later = CaseObservationV2.model_validate(store.load_artifact(
                rows[3].payload["case_ref"], OBSERVATION_MEDIA,
            ).payload)
            assert later.request_digest == second_request["digest"]
            assert later.outcome_artifact_digest is None
        finally:
            request_principal.reset(token)
    finally:
        workspace.store.close()


def test_ordinary_rejection_does_not_become_a_learning_failure(tmp_path):
    workspace, event, _, _ = _workspace(tmp_path / "experience.sqlite")
    try:
        workspace.store.record_event("WORKSPACE_CHANGE_REJECTED", {
            "event_id": event.event_id, "event_digest": event.digest,
            "status": "REJECTED", "actor_id": "owner:product",
            "reason": "ordinary business rejection",
        })
        collector = ExperienceCollector(workspace, collector_actor_id="actor:collector", enabled=True)
        token = request_principal.set(_principal())
        try:
            result = collector.collect(worker_id="worker:one")
            assert result.skipped == 1 and result.quarantined == 0 and result.observed == 0
            assert not workspace.store.list_artifacts(artifact_id_prefix="experience-case:")
        finally:
            request_principal.reset(token)
    finally:
        workspace.store.close()
