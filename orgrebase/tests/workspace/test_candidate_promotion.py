from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from orgrebase.domain import IntegrityError
from orgrebase.workspace.candidate_promotion import (
    apply_promoted_change,
    approve_promoted_change,
    build_candidate_promotion,
    persist_candidate_promotion,
    prepare_promoted_change,
)
from orgrebase.workspace.service import WorkspaceService

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "evidence/semifinal-closure/latest"
RUN_ID = "run:orgrebase:semifinal-closure:quote-001"


def _formed_service(database: Path) -> tuple[WorkspaceService, object]:
    service = WorkspaceService(store_path=database, workflow_run_id=RUN_ID)
    formed = service.form_quote_with_dependency_evidence()
    assert formed["formation_run_id"] == RUN_ID
    assert formed["tool_invocation"]["receipt"]["workflow_run_id"] == RUN_ID
    bundle = build_candidate_promotion(
        service,
        evidence_pack=PACK,
        run_id=RUN_ID,
    )
    persist_candidate_promotion(service, bundle)
    return service, bundle


def test_candidate_chain_requires_two_owner_approvals_before_same_run_apply(
    tmp_path: Path,
) -> None:
    database = tmp_path / "promotion.sqlite"
    service, bundle = _formed_service(database)
    launch_preview = prepare_promoted_change(
        service,
        "launch_date",
        promotion_digest=bundle.digest,
    )
    with pytest.raises(IntegrityError, match="PROMOTION_APPROVAL_BINDING_INVALID"):
        approve_promoted_change(
            service,
            "launch_date",
            actor_id="human:finance-owner",
            preview_binding_digest=launch_preview.digest,
        )
    launch_approval = approve_promoted_change(
        service,
        "launch_date",
        actor_id="human:product-owner",
        preview_binding_digest=launch_preview.digest,
    )
    launch = apply_promoted_change(
        service,
        "launch_date",
        promotion_approval_digest=launch_approval.digest,
    )
    assert launch.run_id == RUN_ID
    assert launch.before_quote_ref.endswith("@v1")
    assert launch.after_quote_ref.endswith("@v2")
    assert launch.proposal_plane_target_writes == 0
    assert launch.canonical_target_writes == 2
    service.close()

    reopened = WorkspaceService.reopen(database, workflow_run_id=RUN_ID)
    currency_preview = prepare_promoted_change(
        reopened,
        "currency",
        promotion_digest=bundle.digest,
    )
    currency_approval = approve_promoted_change(
        reopened,
        "currency",
        actor_id="human:finance-owner",
        preview_binding_digest=currency_preview.digest,
    )
    currency = apply_promoted_change(
        reopened,
        "currency",
        promotion_approval_digest=currency_approval.digest,
    )
    assert currency.run_id == RUN_ID
    assert currency.before_quote_ref.endswith("@v2")
    assert currency.after_quote_ref.endswith("@v3")
    assert reopened.state()["stage"] == "CURRENT"
    assert reopened.store.verify_event_chain()["status"] == "PASS"
    reopened.close()


def test_promoted_apply_is_idempotent_and_stale_approval_fails_closed(
    tmp_path: Path,
) -> None:
    service, bundle = _formed_service(tmp_path / "idempotent.sqlite")
    preview = prepare_promoted_change(
        service,
        "launch_date",
        promotion_digest=bundle.digest,
    )
    approval = approve_promoted_change(
        service,
        "launch_date",
        actor_id="human:product-owner",
        preview_binding_digest=preview.digest,
    )
    before = service.store.verify_event_chain()["events"]
    with pytest.raises(IntegrityError, match="PROMOTION_APPLY_BINDING_INVALID"):
        apply_promoted_change(
            service,
            "launch_date",
            promotion_approval_digest="sha256:" + "0" * 64,
        )
    assert service.store.verify_event_chain()["events"] == before
    first = apply_promoted_change(
        service,
        "launch_date",
        promotion_approval_digest=approval.digest,
    )
    events = service.store.verify_event_chain()["events"]
    repeated = apply_promoted_change(
        service,
        "launch_date",
        promotion_approval_digest=approval.digest,
    )
    assert repeated.digest == first.digest
    assert service.store.verify_event_chain()["events"] == events
    service.close()


def test_promoted_apply_reconstructs_receipt_after_post_commit_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "post-commit-recovery.sqlite"
    service, bundle = _formed_service(database)
    preview = prepare_promoted_change(
        service,
        "launch_date",
        promotion_digest=bundle.digest,
    )
    approval = approve_promoted_change(
        service,
        "launch_date",
        actor_id="human:product-owner",
        preview_binding_digest=preview.digest,
    )
    original_save_artifact = service.store.save_artifact

    def fail_receipt_save(connection, artifact_id, media_type, payload):
        if artifact_id == "promotion-apply:launch-date@r1":
            raise RuntimeError("INJECTED_AFTER_CANONICAL_APPLY")
        return original_save_artifact(connection, artifact_id, media_type, payload)

    monkeypatch.setattr(service.store, "save_artifact", fail_receipt_save)
    with pytest.raises(RuntimeError, match="INJECTED_AFTER_CANONICAL_APPLY"):
        apply_promoted_change(
            service,
            "launch_date",
            promotion_approval_digest=approval.digest,
        )

    # Workspace's canonical transaction and durable outcome committed, while
    # the projection receipt transaction rolled back in full.
    assert service.current_quote().ref.endswith("@v2")
    assert service.current_graph_pointer().ref.endswith("@v2")
    assert service.store.artifact_exists("workspace-outcome:launch_date@r1")
    assert not service.store.artifact_exists("promotion-apply:launch-date@r1")
    assert not any(
        event["event_type"] == "PROMOTION_CHANGE_APPLIED"
        for event in service.store.event_envelopes()
    )
    service.close()

    reopened = WorkspaceService.reopen(database, workflow_run_id=RUN_ID)
    recovered = apply_promoted_change(
        reopened,
        "launch_date",
        promotion_approval_digest=approval.digest,
    )
    assert recovered.before_quote_ref == bundle.base_quote_ref
    assert recovered.before_quote_digest == bundle.base_quote_digest
    assert recovered.before_graph_ref == bundle.base_graph_ref
    assert recovered.before_graph_digest == bundle.base_graph_digest
    assert recovered.after_quote_ref.endswith("@v2")
    assert recovered.after_graph_ref.endswith("@v2")
    assert recovered.canonical_target_writes == 2
    outcome_event = next(
        event
        for event in reopened.store.event_envelopes()
        if event["event_type"] == "WORKSPACE_CHANGE_OUTCOME_RECORDED"
        and event["payload"].get("kind") == "launch_date"
    )
    assert recovered.event_chain_head_digest == outcome_event["event_digest"]

    applied_events = [
        event
        for event in reopened.store.event_envelopes()
        if event["event_type"] == "PROMOTION_CHANGE_APPLIED"
    ]
    assert len(applied_events) == 1
    events_after_recovery = reopened.store.verify_event_chain()["events"]
    repeated = apply_promoted_change(
        reopened,
        "launch_date",
        promotion_approval_digest=approval.digest,
    )
    assert repeated.digest == recovered.digest
    assert reopened.store.verify_event_chain()["events"] == events_after_recovery
    reopened.close()


def test_skill_result_substitution_cannot_enter_promotion(tmp_path: Path) -> None:
    attacked = tmp_path / "attacked"
    shutil.copytree(PACK, attacked)
    result_path = attacked / "skills/quote-compose/result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["action"] = "KEEP_CURRENT"
    result_path.write_text(json.dumps(result), encoding="utf-8")
    service = WorkspaceService(
        store_path=tmp_path / "attacked.sqlite",
        workflow_run_id=RUN_ID,
    )
    service.form_quote_with_dependency_evidence()
    with pytest.raises(IntegrityError, match="PROMOTION_CANDIDATE_CHAIN_NOT_ADMISSIBLE"):
        build_candidate_promotion(
            service,
            evidence_pack=attacked,
            run_id=RUN_ID,
        )
    assert service.state()["stage"] == "CURRENT"
    service.close()


def test_explicit_service_run_id_is_bound_into_rebase_and_successor_trace(
    tmp_path: Path,
) -> None:
    service, bundle = _formed_service(tmp_path / "same-run.sqlite")
    preview = prepare_promoted_change(
        service,
        "launch_date",
        promotion_digest=bundle.digest,
    )
    approval = approve_promoted_change(
        service,
        "launch_date",
        actor_id="human:product-owner",
        preview_binding_digest=preview.digest,
    )
    apply_promoted_change(
        service,
        "launch_date",
        promotion_approval_digest=approval.digest,
    )
    outcome = service.state()["latest_outcome"]["outcome"]
    assert outcome["rebase_receipt"]["workflow_run_id"] == RUN_ID
    trace_ref = outcome["workspace_rebase_receipt"]["successor_trace_refs"][0]
    successor_trace = service.store.load_artifact(trace_ref).payload
    assert successor_trace["run_id"] == RUN_ID
    service.close()
