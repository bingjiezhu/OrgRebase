from __future__ import annotations

import copy
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from orgrebase.agentteams_ingest import ingest_local_proposals
from orgrebase.certificates import build_minimal_rebase_certificate
from orgrebase.domain import (
    Approval,
    EffectDisposition,
    FreshnessError,
    IntegrityError,
    MinimalRebaseCertificate,
    ObjectState,
)
from orgrebase.service import OrgRebaseService


def test_preview_does_not_change_target_objects(service: OrgRebaseService) -> None:
    before = service.current_view()
    result = service.preview()
    assert result["current_state"] == before
    assert result["preview"].state == "READY"
    assert service.store.verify_event_chain()["events"] == 3


def test_apply_is_selective_atomic_and_receipted(service: OrgRebaseService) -> None:
    result = service.apply()
    state = result["current_state"]
    receipt = result["receipt"]
    assert state["claim:product.launch_date"]["version"] == "v8"
    assert state["work:sales_quote_a"]["version"] == "v2"
    assert state["work:support_doc_b"]["version"] == "v2"
    assert state["work:legal_review_c"]["version"] == "v1"
    assert state["work:finance_analysis_d"]["version"] == "v1"
    assert state["work:partner_brief_e"]["state"] == "REVIEW_REQUIRED"
    assert state["skill:enterprise-launch-readiness"] == {
        "version": "1.3",
        "state": "CANARY",
        "digest": state["skill:enterprise-launch-readiness"]["digest"],
    }
    assert service.store.get_object("work:sales_quote_a", "v1").state == ObjectState.STALE
    assert receipt.metrics["work_items_rebased"] == 2
    assert receipt.metrics["bounded_unaffected"] == 2
    assert receipt.metrics["unknown"] == 1
    assert len(receipt.runtime_dependencies) == 6
    assert len(receipt.agent_runs) == 5
    assert result["event_chain"]["status"] == "PASS"
    admitted = service.change_set.deltas[0].proposed_value
    assert service.store.get_object("work:sales_quote_a").payload["canonical_premise"] == admitted
    assert service.store.get_object("work:support_doc_b").payload["canonical_premise"] == admitted
    assert "canonical_premise" not in service.store.get_object("work:legal_review_c").payload
    assert receipt.candidate_ingestion_digest == service.collaboration["candidate_ingestion"].digest
    assert receipt.compilation_receipt_digest == service.collaboration["compilation_receipt"].digest


def test_apply_contexts_are_distinct_and_do_not_disclose_restricted_source(service: OrgRebaseService) -> None:
    receipt = service.apply()["receipt"]
    sales, support = receipt.context_manifests
    assert sales.digest != support.digest
    for manifest in receipt.context_manifests:
        assert all(not item.object_ref.startswith("source:legal") for item in manifest.included)
        assert any(item.object_ref.startswith("source:legal") for item in manifest.excluded)
    assert receipt.metrics["unauthorized_disclosures"] == 0


def test_canonical_store_rejects_nested_payload_mutation_with_cached_digest(
    service: OrgRebaseService,
) -> None:
    item = service.store.get_object("work:sales_quote_a", "v1")
    original_digest = item.digest
    item.payload["canonical_premise"] = "attacker-controlled"
    assert item.digest == original_digest

    with (
        service.store.transaction() as connection,
        pytest.raises(IntegrityError, match="VERSIONED_OBJECT_MODEL_INVALID"),
    ):
        service.store.insert_version(connection, item, make_current=False)


def test_freshness_drift_fails_before_state_writes(service: OrgRebaseService) -> None:
    service.preview()
    before = service.current_view()
    revisions = dict(service.fixture.revisions)
    revisions["graph"] = "graph:canonical@r2"
    with pytest.raises(FreshnessError, match="preview revision drift"):
        service.apply(current_revisions=revisions)
    assert service.current_view() == before


def test_exact_approval_binding_is_enforced(service: OrgRebaseService) -> None:
    service.preview()
    approval = service.workflow.approve(
        service.change_set,
        service.preview_receipt,
        service.minimal_rebase_certificate,
    )
    altered = approval.model_copy(update={"preview_digest": "sha256:" + "0" * 64, "digest": ""})
    with pytest.raises(FreshnessError, match="not bound"):
        service.workflow.apply(
            change_set=service.change_set,
            preview=service.preview_receipt,
            minimal_rebase_certificate=service.minimal_rebase_certificate,
            approval=altered,
            collaboration=service.collaboration,
            run_envelope=service.run_envelope,
        )


def test_apply_rejects_unbound_agent_trace(service: OrgRebaseService) -> None:
    service.preview()
    escaped = service.collaboration["handoffs"][0].model_copy(update={"candidate_only": False, "digest": ""})
    collaboration = {
        **service.collaboration,
        "handoffs": (escaped, *service.collaboration["handoffs"][1:]),
    }
    approval = service.workflow.approve(
        service.change_set,
        service.preview_receipt,
        service.minimal_rebase_certificate,
    )

    with pytest.raises(IntegrityError, match="handoff escaped"):
        service.workflow.apply(
            change_set=service.change_set,
            preview=service.preview_receipt,
            minimal_rebase_certificate=service.minimal_rebase_certificate,
            approval=approval,
            collaboration=collaboration,
            run_envelope=service.run_envelope,
            idempotency_key="apply:unbound-collab@r1",
        )


def test_apply_rejects_forged_or_missing_candidate_ingestion(
    service: OrgRebaseService,
) -> None:
    service.preview()
    approval = service.workflow.approve(
        service.change_set,
        service.preview_receipt,
        service.minimal_rebase_certificate,
    )
    missing = {key: value for key, value in service.collaboration.items() if key != "candidate_ingestion"}
    with pytest.raises(IntegrityError, match="requires bound candidate ingestion"):
        service.workflow.apply(
            change_set=service.change_set,
            preview=service.preview_receipt,
            minimal_rebase_certificate=service.minimal_rebase_certificate,
            approval=approval,
            collaboration=missing,
            run_envelope=service.run_envelope,
            idempotency_key="apply:missing-ingestion@r1",
        )

    forged = service.collaboration["candidate_ingestion"].model_copy(
        update={"target_writes": 1, "digest": ""}
    )
    swapped = {**service.collaboration, "candidate_ingestion": forged}
    with pytest.raises(IntegrityError, match="does not match recomputation"):
        service.workflow.apply(
            change_set=service.change_set,
            preview=service.preview_receipt,
            minimal_rebase_certificate=service.minimal_rebase_certificate,
            approval=approval,
            collaboration=swapped,
            run_envelope=service.run_envelope,
            idempotency_key="apply:forged-ingestion@r1",
        )


def test_apply_rejects_forged_or_missing_compilation_receipt(
    service: OrgRebaseService,
) -> None:
    service.preview()
    approval = service.workflow.approve(
        service.change_set,
        service.preview_receipt,
        service.minimal_rebase_certificate,
    )
    missing = {key: value for key, value in service.collaboration.items() if key != "compilation_receipt"}
    with pytest.raises(IntegrityError, match="requires bound compilation receipt"):
        service.workflow.apply(
            change_set=service.change_set,
            preview=service.preview_receipt,
            minimal_rebase_certificate=service.minimal_rebase_certificate,
            approval=approval,
            collaboration=missing,
            run_envelope=service.run_envelope,
            idempotency_key="apply:missing-compilation@r1",
        )
    forged = service.collaboration["compilation_receipt"].model_copy(
        update={"claim_boundary": "forged", "digest": ""}
    )
    swapped = {**service.collaboration, "compilation_receipt": forged}
    with pytest.raises(IntegrityError, match="compilation receipt does not match"):
        service.workflow.apply(
            change_set=service.change_set,
            preview=service.preview_receipt,
            minimal_rebase_certificate=service.minimal_rebase_certificate,
            approval=approval,
            collaboration=swapped,
            run_envelope=service.run_envelope,
            idempotency_key="apply:forged-compilation@r1",
        )


def test_preserve_targets_keep_pre_apply_payload(service: OrgRebaseService) -> None:
    legal_before = service.store.get_object("work:legal_review_c")
    finance_before = service.store.get_object("work:finance_analysis_d")
    service.apply()
    legal = service.store.get_object("work:legal_review_c")
    finance = service.store.get_object("work:finance_analysis_d")
    assert legal.payload == legal_before.payload
    assert finance.payload == finance_before.payload
    assert legal.version == legal_before.version
    assert finance.version == finance_before.version


def test_apply_rejects_ingestion_that_rejects_a_bound_proposal(
    service: OrgRebaseService,
) -> None:
    service.preview()
    gtm = next(item for item in service.collaboration["handoffs"] if item.from_agent == "gtm-steward")
    forged = gtm.model_copy(update={"payload": {**gtm.payload, "kind": "ApplyReceipt"}, "digest": ""})
    handoffs = tuple(
        forged if item.from_agent == "gtm-steward" else item for item in service.collaboration["handoffs"]
    )
    ingestion = ingest_local_proposals(
        change_set=service.change_set,
        preview=service.preview_receipt,
        plan=service.collaboration["orchestration_plan"],
        handoffs=handoffs,
        coordination_receipt=service.collaboration["coordination_receipt"],
        run_envelope=service.run_envelope,
    )
    with pytest.raises(IntegrityError, match="rejected a bound proposal"):
        service.workflow._bound_candidate_ingestion(
            change_set=service.change_set,
            preview=service.preview_receipt,
            collaboration={
                **service.collaboration,
                "handoffs": handoffs,
                "candidate_ingestion": ingestion,
            },
            run_envelope=service.run_envelope,
        )


def test_content_oracle_fails_closed_on_rebuild_and_preserve_drift(
    service: OrgRebaseService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admitted_sales = service.store.get_object("work:sales_quote_a")
    service.apply()
    sales = service.store.get_object("work:sales_quote_a")
    legal = service.store.get_object("work:legal_review_c")
    affected = (SimpleNamespace(object_id="work:sales_quote_a"),)
    bounded = (SimpleNamespace(object_id="work:legal_review_c"),)
    delta = service.change_set.deltas[0]
    before = {"work:sales_quote_a": admitted_sales, "work:legal_review_c": legal}
    with pytest.raises(IntegrityError, match="PRESERVE_TARGET_WAS_WRITTEN"):
        service.workflow._assert_content_oracle(
            affected=affected,
            bounded=bounded,
            before=before,
            deltas=(delta,),
            transitions=[{"object_id": "work:legal_review_c"}],
        )
    get_object = service.store.get_object
    corrupt_rebuild = sales.model_copy(update={
        "payload": {**sales.payload, "canonical_premise": "not-the-admitted-value"}, "digest": ""})
    with monkeypatch.context() as patch:
        patch.setattr(service.store, "get_object", lambda object_id, version=None:
                      corrupt_rebuild if object_id == sales.id and version is None else get_object(object_id, version))
        with pytest.raises(IntegrityError, match="REBUILD_DID_NOT_WRITE_ADMITTED_VALUE"):
            service.workflow._assert_content_oracle(
                affected=affected,
                bounded=bounded,
                before=before,
                deltas=(delta,),
                transitions=[{"object_id": "work:sales_quote_a"}],
            )
    drifted_payload = legal.model_copy(
        update={"payload": {**legal.payload, "owner": "forged-owner"}, "digest": ""}
    )
    with pytest.raises(IntegrityError, match="PRESERVE_MUTATED_BUSINESS_PAYLOAD"):
        service.workflow._assert_content_oracle(
            affected=affected,
            bounded=bounded,
            before={"work:sales_quote_a": admitted_sales, "work:legal_review_c": drifted_payload},
            deltas=(delta,),
            transitions=[{"object_id": "work:sales_quote_a"}],
        )
    drifted_version = legal.model_copy(update={"version": "v9", "digest": ""})
    with pytest.raises(IntegrityError, match="PRESERVE_MUTATED_OBJECT_VERSION"):
        service.workflow._assert_content_oracle(
            affected=affected,
            bounded=bounded,
            before={"work:sales_quote_a": admitted_sales, "work:legal_review_c": drifted_version},
            deltas=(delta,),
            transitions=[{"object_id": "work:sales_quote_a"}],
        )


def test_apply_executes_every_vmrc_disposition(service: OrgRebaseService) -> None:
    result = service.apply()
    certificate = result["minimal_rebase_certificate"]
    receipt = result["receipt"]
    rebuilds = {
        item.target_id for item in certificate.effects if item.disposition == EffectDisposition.REBUILD
    }
    preserved = {
        item.target_id
        for item in certificate.effects
        if item.disposition == EffectDisposition.PRESERVE_WITHIN_BOUNDARY
    }
    held = {
        item.target_id
        for item in certificate.effects
        if item.disposition == EffectDisposition.HOLD_FOR_REVIEW
    }
    requalified = {
        item.target_id for item in certificate.effects if item.disposition == EffectDisposition.REQUALIFY
    }

    assert {item["object_id"] for item in receipt.transitions if item["to_state"] == "CURRENT"} == rebuilds
    assert {item["object_id"] for item in receipt.transitions if item["to_state"] == "CANARY"} == requalified
    assert {item["object_id"] for item in receipt.bounded_unaffected} == preserved
    assert {item["object_id"] for item in receipt.unknown} == held
    assert requalified == {"skill:enterprise-launch-readiness"}
    assert receipt.metrics["skills_requalified"] == 1


def test_apply_rejects_preview_not_committed_by_certificate(service: OrgRebaseService) -> None:
    service.preview()
    forged_preview = service.preview_receipt.model_copy(
        update={"algorithm_version": "forged-preview", "digest": ""}
    )
    approval = service.workflow.approve(
        service.change_set,
        service.preview_receipt,
        service.minimal_rebase_certificate,
    )
    with pytest.raises(IntegrityError, match="not the Preview committed"):
        service.workflow.apply(
            change_set=service.change_set,
            preview=forged_preview,
            minimal_rebase_certificate=service.minimal_rebase_certificate,
            approval=approval,
            collaboration=service.collaboration,
            run_envelope=service.run_envelope,
            idempotency_key="apply:forged-preview@r1",
        )


def test_apply_rejects_no_semantic_delta(service: OrgRebaseService) -> None:
    service.preview()
    result = service.no_semantic_delta()
    certificate = build_minimal_rebase_certificate(result["change_set"], result["preview"])
    approval = Approval(
        id="approval:no-semantic@1",
        actor_id="human:change-approver",
        change_set_digest=result["change_set"].digest,
        preview_digest=result["preview"].digest,
        minimal_rebase_certificate_digest=certificate.digest,
        authorization_revision=service.fixture.revisions["authorization"],
        authority_scope=tuple(sorted((result["change_set"].id, *result["change_set"].scope))),
        approved_at="2026-08-14T00:03:00Z",
        expires_at="2026-08-14T01:00:00Z",
        method="LOCAL_EXPLICIT_DEMO_APPROVAL",
    )
    with pytest.raises(RuntimeError, match="PREVIEW_NOT_APPROVABLE"):
        service.workflow.apply(
            change_set=result["change_set"],
            preview=result["preview"],
            minimal_rebase_certificate=certificate,
            approval=approval,
            collaboration=service.collaboration,
            run_envelope=service.run_envelope,
            idempotency_key="apply:no-semantic@r1",
        )


def test_apply_is_idempotent_at_workflow_boundary(service: OrgRebaseService) -> None:
    first = service.apply()["receipt"]
    second = service.workflow.apply(
        change_set=service.change_set,
        preview=service.preview_receipt,
        minimal_rebase_certificate=service.minimal_rebase_certificate,
        approval=service.approval,
        collaboration=service.collaboration,
        run_envelope=service.run_envelope,
    )
    assert second == first
    assert service.store.verify_event_chain()["events"] == 4


def _counterfactual_certificate(
    service: OrgRebaseService,
    target_id: str,
    disposition: EffectDisposition,
) -> MinimalRebaseCertificate:
    payload = service.minimal_rebase_certificate.model_dump(mode="json")
    for effect in payload["effects"]:
        if effect["target_id"] == target_id:
            effect["disposition"] = disposition.value
    payload["digest"] = ""
    return MinimalRebaseCertificate.model_validate(payload)


def test_minimality_rejects_missing_required_rebuild(service: OrgRebaseService) -> None:
    service.preview()
    counterfeit = _counterfactual_certificate(
        service,
        "work:sales_quote_a",
        EffectDisposition.PRESERVE_WITHIN_BOUNDARY,
    )

    with pytest.raises(IntegrityError, match="MINIMALITY_MISSING_REBUILD"):
        service.verify_minimal_rebase_certificate(counterfeit.model_dump(mode="json"))


def test_minimality_rejects_extra_unrelated_rebuild(service: OrgRebaseService) -> None:
    service.preview()
    counterfeit = _counterfactual_certificate(
        service,
        "work:legal_review_c",
        EffectDisposition.REBUILD,
    )

    with pytest.raises(IntegrityError, match="MINIMALITY_EXTRA_REBUILD"):
        service.verify_minimal_rebase_certificate(counterfeit.model_dump(mode="json"))


def test_receipt_digest_detects_tampering(service: OrgRebaseService) -> None:
    receipt = service.apply()["receipt"].model_dump(mode="json")
    assert service.verify_receipt(receipt)["status"] == "PASS"
    tampered = copy.deepcopy(receipt)
    tampered["metrics"]["work_items_rebased"] = 999
    with pytest.raises(IntegrityError, match="digest"):
        service.verify_receipt(tampered)


def test_event_chain_detects_tampering(service: OrgRebaseService) -> None:
    service.apply()
    service.store.connection.execute(
        "UPDATE domain_events SET payload_json=? WHERE sequence_no=2", ('{"tampered":true}',)
    )
    service.store.connection.commit()
    with pytest.raises(IntegrityError, match="sequence 2"):
        service.store.verify_event_chain()


def test_idempotency_key_cannot_be_reused_for_another_request(service: OrgRebaseService) -> None:
    service.apply()
    with pytest.raises(RuntimeError, match="IDEMPOTENCY_CONFLICT"):
        service.store.get_idempotent("apply:launch-date@r1", "sha256:" + "f" * 64)


def test_state_overlay_does_not_change_immutable_object_digest(
    service: OrgRebaseService,
) -> None:
    before = service.store.get_object("work:partner_brief_e")
    with service.store.transaction() as connection:
        service.store.transition_current(
            connection,
            "work:partner_brief_e",
            ObjectState.REVIEW_REQUIRED,
        )
    after = service.store.get_object("work:partner_brief_e")

    assert before.digest == after.digest
    assert before.state == ObjectState.CURRENT
    assert after.state == ObjectState.REVIEW_REQUIRED


def test_artifact_id_conflict_is_never_silently_ignored(
    service: OrgRebaseService,
) -> None:
    with service.store.transaction() as connection:
        first = service.store.save_artifact(
            connection,
            "artifact:test",
            "application/json",
            {"value": "canonical"},
        )
    with service.store.transaction() as connection:
        assert (
            service.store.save_artifact(
                connection,
                "artifact:test",
                "application/json",
                {"value": "canonical"},
            )
            == first
        )
    with (
        pytest.raises(IntegrityError, match="ARTIFACT_ID_CONFLICT"),
        service.store.transaction() as connection,
    ):
        service.store.save_artifact(
            connection,
            "artifact:test",
            "application/json",
            {"value": "forked"},
        )


def test_readers_never_observe_an_uncommitted_artifact_type(
    service: OrgRebaseService,
) -> None:
    """Concurrent API reads must see the state before or after a transaction."""

    writer_entered = Event()
    release_writer = Event()
    reader_finished = Event()
    reader_outcomes: list[str] = []

    def write_then_rollback() -> None:
        try:
            with service.store.transaction() as connection:
                service.store.save_artifact(
                    connection,
                    "artifact:transaction-isolation",
                    "application/vnd.orgrebase.uncommitted+json",
                    {"status": "UNCOMMITTED"},
                )
                writer_entered.set()
                assert release_writer.wait(timeout=2)
                raise RuntimeError("ROLLBACK_TEST_WRITE")
        except RuntimeError as exc:
            assert str(exc) == "ROLLBACK_TEST_WRITE"

    def read_expected_type() -> None:
        try:
            service.store.load_artifact(
                "artifact:transaction-isolation",
                "application/vnd.orgrebase.committed+json",
            )
        except KeyError:
            reader_outcomes.append("ROLLED_BACK_NOT_FOUND")
        except Exception as exc:  # pragma: no cover - assertion reports the leaked state
            reader_outcomes.append(f"LEAKED:{type(exc).__name__}:{exc}")
        else:  # pragma: no cover - assertion reports the leaked state
            reader_outcomes.append("LEAKED:ARTIFACT_VISIBLE")
        finally:
            reader_finished.set()

    writer = Thread(target=write_then_rollback)
    reader = Thread(target=read_expected_type)
    writer.start()
    assert writer_entered.wait(timeout=2)
    reader.start()
    assert not reader_finished.wait(timeout=0.05)
    release_writer.set()
    writer.join(timeout=2)
    reader.join(timeout=2)

    assert not writer.is_alive()
    assert not reader.is_alive()
    assert reader_outcomes == ["ROLLED_BACK_NOT_FOUND"]


def test_approval_authority_scope_and_expiry_are_enforced(
    service: OrgRebaseService,
) -> None:
    service.preview()
    approval = service.workflow.approve(
        service.change_set,
        service.preview_receipt,
        service.minimal_rebase_certificate,
    )
    wrong_root = approval.model_copy(update={"authorization_revision": "authz:forged", "digest": ""})
    with pytest.raises(IntegrityError, match="authorization root"):
        service.workflow.apply(
            change_set=service.change_set,
            preview=service.preview_receipt,
            minimal_rebase_certificate=service.minimal_rebase_certificate,
            approval=wrong_root,
            collaboration=service.collaboration,
            run_envelope=service.run_envelope,
        )

    expired = approval.model_copy(update={"expires_at": "2026-08-14T00:03:30Z", "digest": ""})
    with pytest.raises(IntegrityError, match="APPROVAL_TIME_WINDOW_INVALID"):
        service.workflow.apply(
            change_set=service.change_set,
            preview=service.preview_receipt,
            minimal_rebase_certificate=service.minimal_rebase_certificate,
            approval=expired,
            collaboration=service.collaboration,
            run_envelope=service.run_envelope,
        )


def test_no_semantic_delta_preview_is_not_approvable(
    service: OrgRebaseService,
) -> None:
    service.preview()
    no_delta = service.no_semantic_delta()

    with pytest.raises(RuntimeError, match="PREVIEW_NOT_APPROVABLE"):
        service.workflow.approve(
            no_delta["change_set"],
            no_delta["preview"],
            service.minimal_rebase_certificate,
        )
