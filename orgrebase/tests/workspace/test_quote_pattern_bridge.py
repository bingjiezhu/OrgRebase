from __future__ import annotations

from types import SimpleNamespace

import pytest

from orgrebase.change_events import ChangeEvent
from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError, ObjectState, VersionedObject
from orgrebase.store import StateStore
from orgrebase.workspace import quote_pattern_bridge as bridge
from orgrebase.workspace.change_recovery import REQUEST_MEDIA, RESUME_MEDIA, _prefix, _sealed
from orgrebase.workspace.pattern_evolution import FeatureProfile, GovernedPatternService


class Changes:
    def __init__(self, event):
        self.event = event

    def get(self, event_id):
        if event_id != self.event.event_id:
            raise KeyError(event_id)
        return self.event

    def pending_ids(self):
        return ()


def workspace_case(tmp_path, monkeypatch):
    store = StateStore(tmp_path / "case.sqlite")
    proposal = VersionedObject(
        id="claim:product.enterprise_plan",
        version="v2",
        kind="ClaimVersion",
        label="Product plan",
        domain="product",
        state=ObjectState.PROPOSED,
        payload={"canonical_value": "revised"},
        source_refs=("source:owner-reviewed@v2",),
        allowed_purposes=("change_rebase",),
    )
    event = ChangeEvent(
        event_id="recovery-case",
        organization_id="org:test",
        slot_id="product_plan",
        owner_id="owner:product",
        base_version="v1",
        base_digest=sha256_digest({"base": 1}),
        proposal=proposal,
        occurred_at="2026-09-25T00:00:00Z",
    )
    workspace = SimpleNamespace(
        store=store,
        profile=SimpleNamespace(organization_id="org:test"),
        approval_identity_mode="BODY_ACTOR_COMPATIBILITY",
        changes=Changes(event),
        change_order=(event.event_id,),
        effective_workflow_run_id="run:case",
    )
    request = _sealed(
        {
            "event_id": event.event_id,
            "event_digest": event.digest,
            "round": 1,
            "previous_request_digest": None,
            "workspace_id": store.workspace_id,
            "run_id": workspace.effective_workflow_run_id,
            "required_evidence_refs": ["claim:product.enterprise_plan@v1"],
        }
    )
    resume = _sealed(
        {
            "request_digest": request["digest"],
            "event_digest": event.digest,
            "executor": {"executor_id": "product-steward"},
            "evidence": [],
        }
    )
    with store.transaction() as connection:
        store.save_artifact(
            connection,
            _prefix(event.event_id) + "request:0001",
            REQUEST_MEDIA,
            request,
        )
        store.save_artifact(
            connection,
            _prefix(event.event_id) + "resume:0001",
            RESUME_MEDIA,
            resume,
        )
    detail = {
        "status": "APPLIED",
        "preview": {"preview_digest": sha256_digest({"preview": 1})},
        "approval": {"approval_digest": sha256_digest({"approval": 1})},
        "outcome": {"artifact_digest": sha256_digest({"outcome": 1})},
    }
    monkeypatch.setattr(bridge, "change_detail", lambda _workspace, _event_id: detail)
    return workspace, detail


def test_quote_recovery_case_binds_real_recovery_and_outcome_without_business_values(
    tmp_path, monkeypatch
):
    workspace, _ = workspace_case(tmp_path, monkeypatch)
    try:
        case = bridge.build_quote_recovery_case(
            workspace,
            "recovery-case",
            corpus_authority="controller:corpus",
        )
        refs = bridge.make_quote_recovery_case_resolver(workspace)(case)
        assert case.outcome == "SUPPORT"
        assert case.public_input["profile_id"] == bridge.PROFILE_ID
        assert case.public_input["slot_id"] == "product_plan"
        assert case.public_input["domain_id"] == "product"
        assert case.public_input["failure_kind"] == "EVIDENCE_REQUIRED"
        assert case.public_input["recovery_round"] == 1
        assert case.public_input["required_evidence_count"] == 1
        assert case.public_input["candidate_only"] is False
        assert case.public_input["candidate_bundle"] == {
            "domain": "product",
            "profile_id": bridge.PROFILE_ID,
            "evidence": {
                "request_digest": case.certificate["evidence"]["request_digest"],
                "resume_digest": case.certificate["evidence"]["resume_digest"],
                "outcome_artifact_digest": case.certificate["evidence"][
                    "outcome_artifact_digest"
                ],
            },
        }
        assert len(refs) == 6
        consumer = bridge.quote_recovery_consumer_input(case)
        assert consumer["candidate_bundle"] == case.public_input["candidate_bundle"]
        assert set(consumer) == {
            "run_id",
            "task_id",
            "delegation_id",
            "delegation_task_digest",
            "context_projection_digest",
            "candidate_bundle",
        }
        assert "revised" not in str(case.model_dump(mode="json"))
    finally:
        workspace.store.close()


def test_case_resolver_rejects_a_later_business_state_as_the_old_case(tmp_path, monkeypatch):
    workspace, detail = workspace_case(tmp_path, monkeypatch)
    try:
        case = bridge.build_quote_recovery_case(
            workspace,
            "recovery-case",
            corpus_authority="controller:corpus",
        )
        detail["status"] = "PREVIEWED"
        detail["outcome"] = None
        with pytest.raises(IntegrityError, match="QUOTE_PATTERN_CASE_EVIDENCE_CHANGED"):
            bridge.make_quote_recovery_case_resolver(workspace)(case)
    finally:
        workspace.store.close()


def test_business_rejection_without_independent_skill_failure_stays_unknown(
    tmp_path, monkeypatch
):
    workspace, detail = workspace_case(tmp_path, monkeypatch)
    try:
        detail["status"] = "REJECTED"
        detail["outcome"] = None
        case = bridge.build_quote_recovery_case(
            workspace,
            "recovery-case",
            corpus_authority="controller:corpus",
        )
        assert case.outcome == "UNKNOWN"
        assert case.certificate["evidence"]["status"] == "REJECTED"
        assert case.certificate["claim_scope"] == (
            "WORKSPACE_CASE_CANDIDATE_NOT_SKILL_EFFECTIVENESS"
        )
    finally:
        workspace.store.close()


def test_quote_case_enters_the_existing_corpus_with_external_receipt_links(
    tmp_path, monkeypatch
):
    workspace, _ = workspace_case(tmp_path, monkeypatch)
    try:
        case = bridge.build_quote_recovery_case(
            workspace,
            "recovery-case",
            corpus_authority="controller:corpus",
        )
        service = GovernedPatternService(
            workspace.store,
            corpus_authority="controller:corpus",
            evaluator_authority="controller:evaluator",
            governance_authority="controller:governor",
            case_evidence_resolver=bridge.make_quote_recovery_case_resolver(workspace),
        )
        corpus_ref = service.freeze_corpus(
            FeatureProfile(
                profile_id=bridge.PROFILE_ID,
                revision="1",
                paths=("/candidate_bundle/domain",),
                transfer_scope="one workspace Quote evidence-recovery profile",
            ),
            (case,),
            actor_id="controller:corpus",
        )
        row = service._load(corpus_ref, "corpus")["cases"][0]
        assert row["features"] == {"/candidate_bundle/domain": "product"}
        assert case.certificate["digest"] in row["certificate_refs"]
        assert len(row["certificate_refs"]) == 7
        with pytest.raises(IntegrityError, match="CASE_SET_INVALID"):
            service.freeze_corpus(
                FeatureProfile(
                    profile_id=bridge.PROFILE_ID,
                    revision="1",
                    paths=("/candidate_bundle/domain",),
                    transfer_scope="one workspace Quote evidence-recovery profile",
                ),
                (case, case),
                actor_id="controller:corpus",
            )
    finally:
        workspace.store.close()
