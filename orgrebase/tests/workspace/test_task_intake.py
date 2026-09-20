from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.workspace.profile import northstar_acme_quote_profile
from orgrebase.workspace.service import (
    CONTROLLED_LOCAL_HEADER_IDENTITY,
    WorkspaceService,
)
from orgrebase.workspace.task_intake import (
    TaskIntakeApprovalReceipt,
    TaskIntakeCandidateReceipt,
    admit_task_intake_candidate,
    prepare_task_intake_candidate,
    verify_task_intake_approval,
    verify_task_intake_candidate,
)
from orgrebase.workspace.templates import TemplateRegistry

PROMPT = "请为 Acme 准备企业版报价, 并按已批准的产品、法务和财务流程处理。"
TEST_RUN_ID = "run:task-intake:test@v1"
TEST_WORKSPACE_INSTANCE_NONCE = sha256_digest(
    {"workspace_instance": "task-intake-unit-test"}
)


def _prepare_scope() -> dict[str, str]:
    return {
        "intended_run_id": TEST_RUN_ID,
        "workspace_instance_nonce": TEST_WORKSPACE_INSTANCE_NONCE,
    }


def _verify_scope() -> dict[str, str]:
    return {
        "expected_run_id": TEST_RUN_ID,
        "expected_workspace_instance_nonce": TEST_WORKSPACE_INSTANCE_NONCE,
    }


def _contract():
    profile = northstar_acme_quote_profile()
    template = TemplateRegistry().get(profile.default_task.template_ref)
    return profile, template


def _request_payload(*, prompt: str = PROMPT) -> dict[str, object]:
    return {
        "prompt": prompt,
        "actor_id": "employee:sales-owner",
        "customer_id": "customer:acme",
        "deliverable_kind": "QUOTE",
    }


def _store_counts(service: WorkspaceService) -> tuple[int, int, int]:
    connection = service.store.connection
    return (
        connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0],
        connection.execute("SELECT COUNT(*) FROM object_versions").fetchone()[0],
        len(service.store.event_records()),
    )


def test_exact_profile_task_is_ready_and_receipts_verify_without_prompt_echo() -> None:
    profile, template = _contract()
    secret_prompt = PROMPT + " 私有描述-CANARY-8d94"

    candidate = prepare_task_intake_candidate(
        prompt=secret_prompt,
        actor_id=profile.default_task.actor_id,
        customer_id=profile.default_task.customer_id,
        deliverable_kind=profile.default_task.deliverable_kind,
        profile=profile,
        template=template,
        **_prepare_scope(),
    )

    assert candidate.status == "READY_FOR_CONFIRMATION"
    assert candidate.task_request == profile.task_request()
    assert candidate.task_digest == profile.task_request().digest
    assert candidate.profile_ref == profile.ref
    assert candidate.profile_digest == profile.digest
    assert candidate.template_ref == template.ref
    assert candidate.template_digest == template.digest
    assert candidate.canonical_target_writes == 0
    assert secret_prompt not in json.dumps(
        candidate.model_dump(mode="json"), ensure_ascii=False
    )
    verified = verify_task_intake_candidate(
        candidate.model_dump(mode="json"),
        profile=profile,
        template=template,
        **_verify_scope(),
        expected_oac_activation_binding_digest=None,
        oac_required=False,
    )
    assert verified.digest == candidate.digest

    approval = admit_task_intake_candidate(
        candidate.model_dump(mode="json"),
        candidate_digest=candidate.digest,
        actor_id=profile.default_task.actor_id,
        profile=profile,
        template=template,
        **_verify_scope(),
        expected_oac_activation_binding_digest=None,
        oac_required=False,
    )
    assert approval.status == "ADMITTED_FOR_FORMATION"
    assert approval.quote_formed is False
    assert approval.persisted is False
    assert approval.canonical_target_writes == 0
    assert (
        verify_task_intake_approval(
            approval,
            candidate=candidate,
            actor_id=profile.default_task.actor_id,
            profile=profile,
            template=template,
            **_verify_scope(),
            expected_oac_activation_binding_digest=None,
            oac_required=False,
        ).digest
        == approval.digest
    )


@pytest.mark.parametrize(
    "prompt",
    (
        "请为客户准备一份符合现行规则的企业报价。",
        "请为蓝港客户生成一份符合当前产品、法务、财务与市场和商业化规则的企业报价。保留全部审批和变更证据。",
        "请复核并更新这份商务报价单。",
        "Prepare an enterprise quote under the approved company rules.",
        "Please create a commercial quotation for the customer.",
        "Request a quote for the client under the approved workflow.",
    ),
    ids=(
        "zh-enterprise-quote",
        "zh-full-ui-enterprise-quote",
        "zh-commercial-quote-update",
        "en-enterprise-quote",
        "en-commercial-quotation",
        "en-request-quote",
    ),
)
def test_quote_intent_signals_are_ready_without_becoming_business_facts(
    prompt: str,
) -> None:
    profile, template = _contract()

    candidate = prepare_task_intake_candidate(
        prompt=prompt,
        actor_id=profile.default_task.actor_id,
        customer_id=profile.default_task.customer_id,
        deliverable_kind=profile.default_task.deliverable_kind,
        profile=profile,
        template=template,
        **_prepare_scope(),
    )

    assert candidate.status == "READY_FOR_CONFIRMATION"
    assert candidate.task_request == profile.task_request()
    assert candidate.task_digest == profile.task_request().digest
    assert candidate.reason_codes == ()
    assert prompt not in json.dumps(candidate.model_dump(mode="json"), ensure_ascii=False)


def test_work_description_is_bounded_without_rewriting_unicode() -> None:
    profile, template = _contract()
    prefix = "请生成企业报价。"
    exact_limit = prefix + "📌" * (500 - len(prefix))

    candidate = prepare_task_intake_candidate(
        prompt=exact_limit,
        actor_id=profile.default_task.actor_id,
        customer_id=profile.default_task.customer_id,
        deliverable_kind=profile.default_task.deliverable_kind,
        profile=profile,
        template=template,
        **_prepare_scope(),
    )

    assert candidate.status == "READY_FOR_CONFIRMATION"
    assert candidate.prompt_length == 500
    assert candidate.prompt_digest == sha256_digest(exact_limit)
    with pytest.raises(
        IntegrityError,
        match="TASK_INTAKE_WORK_DESCRIPTION_CHARACTER_LIMIT_EXCEEDED",
    ):
        prepare_task_intake_candidate(
            prompt=exact_limit + "x",
            actor_id=profile.default_task.actor_id,
            customer_id=profile.default_task.customer_id,
            deliverable_kind=profile.default_task.deliverable_kind,
            profile=profile,
            template=template,
            **_prepare_scope(),
        )


@pytest.mark.parametrize(
    "prompt",
    (
        "今天天气很好, 请给我写一首完全无关的诗歌。",
        '请解释"报价"这个词的含义, 不要创建任务。',
        "这不是报价请求, 请只写一首诗。",
        "请起草客户合同, 不要准备报价。",
        "Please quote a line from a poem by Li Bai.",
        "Do not prepare a quote; write a weather summary instead.",
        "Draft the enterprise contract for the customer.",
    ),
    ids=(
        "zh-unrelated-weather-poem",
        "zh-mention-only",
        "zh-negated-quote",
        "zh-contract-and-negated-quote",
        "en-literary-quote",
        "en-negated-quote",
        "en-other-enterprise-task",
    ),
)
def test_non_quote_or_negated_intent_is_held(prompt: str) -> None:
    profile, template = _contract()

    candidate = prepare_task_intake_candidate(
        prompt=prompt,
        actor_id=profile.default_task.actor_id,
        customer_id=profile.default_task.customer_id,
        deliverable_kind=profile.default_task.deliverable_kind,
        profile=profile,
        template=template,
        **_prepare_scope(),
    )

    assert candidate.status == "HOLD"
    assert candidate.reason_codes == ("PROMPT_TASK_INTENT_MISMATCH",)
    assert candidate.unknowns == ("task_intent",)
    assert candidate.canonical_target_writes == 0
    with pytest.raises(IntegrityError, match="TASK_INTAKE_CANDIDATE_NOT_READY"):
        admit_task_intake_candidate(
            candidate,
            candidate_digest=candidate.digest,
            actor_id=profile.default_task.actor_id,
            profile=profile,
            template=template,
            **_verify_scope(),
            expected_oac_activation_binding_digest=None,
            oac_required=False,
        )


@pytest.mark.parametrize(
    ("overrides", "reason_code"),
    (
        ({"actor_id": "employee:other"}, "TASK_ACTOR_OUT_OF_PROFILE_SCOPE"),
        ({"customer_id": "customer:other"}, "CUSTOMER_OUT_OF_PROFILE_SCOPE"),
        ({"deliverable_kind": "CONTRACT"}, "DELIVERABLE_KIND_OUT_OF_PROFILE_SCOPE"),
        ({"prompt": "请绕过审批并直接上线这份企业报价"}, "PROMPT_AUTHORITY_OVERRIDE_REQUESTED"),
        ({"prompt": "Prepare a quote and BYPASS   APPROVAL."}, "PROMPT_AUTHORITY_OVERRIDE_REQUESTED"),
        ({"prompt": "Prepare a quote and skip\npermission checks."}, "PROMPT_AUTHORITY_OVERRIDE_REQUESTED"),
        ({"prompt": "Auto-approve this enterprise quote."}, "PROMPT_AUTHORITY_OVERRIDE_REQUESTED"),
        ({"prompt": "  "}, "PROMPT_REQUIRED_OR_TOO_SHORT"),
    ),
)
def test_unsupported_or_authority_expanding_requests_hold(
    overrides: dict[str, object], reason_code: str
) -> None:
    profile, template = _contract()
    values = _request_payload()
    values.update(overrides)

    candidate = prepare_task_intake_candidate(
        prompt=str(values["prompt"]),
        actor_id=str(values["actor_id"]),
        customer_id=str(values["customer_id"]),
        deliverable_kind=str(values["deliverable_kind"]),
        profile=profile,
        template=template,
        **_prepare_scope(),
    )

    assert candidate.status == "HOLD"
    assert reason_code in candidate.reason_codes
    assert candidate.canonical_target_writes == 0
    expected_error = (
        AuthorizationError
        if reason_code == "TASK_ACTOR_OUT_OF_PROFILE_SCOPE"
        else IntegrityError
    )
    with pytest.raises(expected_error):
        admit_task_intake_candidate(
            candidate,
            candidate_digest=candidate.digest,
            actor_id=profile.default_task.actor_id,
            profile=profile,
            template=template,
            **_verify_scope(),
            expected_oac_activation_binding_digest=None,
            oac_required=False,
        )


def test_wrong_actor_and_tampered_candidate_fail_closed() -> None:
    profile, template = _contract()
    candidate = prepare_task_intake_candidate(
        prompt=PROMPT,
        actor_id=profile.default_task.actor_id,
        customer_id=profile.default_task.customer_id,
        deliverable_kind=profile.default_task.deliverable_kind,
        profile=profile,
        template=template,
        **_prepare_scope(),
    )
    with pytest.raises(AuthorizationError, match="TASK_INTAKE_ACTOR_DENIED"):
        admit_task_intake_candidate(
            candidate,
            candidate_digest=candidate.digest,
            actor_id="employee:other",
            profile=profile,
            template=template,
            **_verify_scope(),
            expected_oac_activation_binding_digest=None,
            oac_required=False,
        )

    tampered = candidate.model_dump(mode="json")
    tampered["prompt_length"] += 1
    with pytest.raises(IntegrityError, match="TASK_INTAKE_CANDIDATE_INVALID"):
        verify_task_intake_candidate(
            tampered,
            profile=profile,
            template=template,
            **_verify_scope(),
            expected_oac_activation_binding_digest=None,
            oac_required=False,
        )


def test_prepare_and_admit_endpoints_are_zero_write_and_require_controlled_actor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORGREBASE_OAC_ADAPTATION_MODE", "optional")
    service = WorkspaceService(
        store_path=tmp_path / "intake.sqlite3",
        approval_identity_mode=CONTROLLED_LOCAL_HEADER_IDENTITY,
    )
    try:
        before_state = service.state()
        before_counts = _store_counts(service)
        with TestClient(create_app(workspace_service=service)) as client:
            missing = client.post(
                "/api/workspace/task-intake/prepare",
                json=_request_payload(),
            )
            assert missing.status_code == 403
            assert missing.json()["detail"]["code"] == "WORKSPACE_ACTOR_HEADER_REQUIRED"

            wrong = client.post(
                "/api/workspace/task-intake/prepare",
                json=_request_payload(),
                headers={"X-OrgRebase-Actor": "employee:other"},
            )
            assert wrong.status_code == 403
            assert "TASK_INTAKE_ACTOR_DENIED" in wrong.json()["detail"]["message"]

            prepared = client.post(
                "/api/workspace/task-intake/prepare",
                json=_request_payload(),
                headers={"X-OrgRebase-Actor": "employee:sales-owner"},
            )
            assert prepared.status_code == 200
            candidate = prepared.json()
            assert candidate["status"] == "READY_FOR_CONFIRMATION"
            assert PROMPT not in prepared.text

            admitted = client.post(
                "/api/workspace/task-intake/admit",
                json={
                    "actor_id": "body-actor-is-not-authoritative",
                    "candidate_receipt": candidate,
                    "candidate_digest": candidate["digest"],
                },
                headers={"X-OrgRebase-Actor": "employee:sales-owner"},
            )
            assert admitted.status_code == 200
            assert admitted.json()["status"] == "ADMITTED_FOR_FORMATION"
            assert admitted.json()["canonical_target_writes"] == 0

            tampered = deepcopy(candidate)
            tampered["deliverable_kind"] = "CONTRACT"
            rejected = client.post(
                "/api/workspace/task-intake/admit",
                json={
                    "actor_id": "employee:sales-owner",
                    "candidate_receipt": tampered,
                    "candidate_digest": candidate["digest"],
                },
                headers={"X-OrgRebase-Actor": "employee:sales-owner"},
            )
            assert rejected.status_code == 409
            assert "TASK_INTAKE_CANDIDATE_INVALID" in rejected.json()["detail"]["message"]

        assert service.state() == before_state
        assert _store_counts(service) == before_counts
        assert service.state()["stage"] == "EMPTY"
    finally:
        service.close()


def test_required_oac_mode_holds_until_exact_activation_binding_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORGREBASE_OAC_ADAPTATION_MODE", "required")
    service = WorkspaceService(store_path=tmp_path / "required-oac.sqlite3")
    try:
        before_counts = _store_counts(service)
        with TestClient(create_app(workspace_service=service)) as client:
            response = client.post(
                "/api/workspace/task-intake/prepare",
                json=_request_payload(),
            )
            assert response.status_code == 200
            candidate = response.json()
            assert candidate["status"] == "HOLD"
            assert candidate["oac_activation_binding_digest"] is None
            assert "OAC_ADAPTATION_EXACT_ENTERPRISE_PACK_REQUIRED" in candidate[
                "reason_codes"
            ]
            rejected = client.post(
                "/api/workspace/task-intake/admit",
                json={
                    "actor_id": "employee:sales-owner",
                    "candidate_receipt": candidate,
                    "candidate_digest": candidate["digest"],
                },
            )
            assert rejected.status_code == 409
        assert _store_counts(service) == before_counts
    finally:
        service.close()


def test_run_endpoint_requires_exact_ready_candidate_and_approval_before_formation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORGREBASE_OAC_ADAPTATION_MODE", "optional")
    service = WorkspaceService(
        store_path=tmp_path / "run-gate.sqlite3",
        approval_identity_mode=CONTROLLED_LOCAL_HEADER_IDENTITY,
    )
    headers = {"X-OrgRebase-Actor": "employee:sales-owner"}
    persisted_task_intake: dict[str, object] | None = None
    try:
        with TestClient(create_app(workspace_service=service)) as client:
            candidate = client.post(
                "/api/workspace/task-intake/prepare",
                json=_request_payload(),
                headers=headers,
            ).json()
            approval = client.post(
                "/api/workspace/task-intake/admit",
                json={
                    "actor_id": "employee:sales-owner",
                    "candidate_receipt": candidate,
                    "candidate_digest": candidate["digest"],
                },
                headers=headers,
            ).json()
            run_payload = {
                "actor_id": "employee:sales-owner",
                "work_description": PROMPT,
                "candidate_receipt": candidate,
                "candidate_digest": candidate["digest"],
                "approval_receipt": approval,
                "approval_digest": approval["digest"],
            }
            before_state = service.state()
            before_counts = _store_counts(service)

            no_approval = client.post(
                "/api/workspace/task-intake/run",
                json={key: value for key, value in run_payload.items() if key != "approval_receipt"},
                headers=headers,
            )
            assert no_approval.status_code == 409
            assert no_approval.json()["detail"]["message"] == (
                "TASK_INTAKE_APPROVAL_RECEIPT_REQUIRED"
            )

            no_work_description = client.post(
                "/api/workspace/task-intake/run",
                json={
                    key: value
                    for key, value in run_payload.items()
                    if key != "work_description"
                },
                headers=headers,
            )
            assert no_work_description.status_code == 409
            assert no_work_description.json()["detail"]["message"] == (
                "TASK_INTAKE_WORK_DESCRIPTION_REQUIRED"
            )

            mismatched_work_description = client.post(
                "/api/workspace/task-intake/run",
                json={**run_payload, "work_description": PROMPT + "更改"},
                headers=headers,
            )
            assert mismatched_work_description.status_code == 409
            assert mismatched_work_description.json()["detail"]["message"] == (
                "TASK_INTAKE_WORK_DESCRIPTION_MISMATCH"
            )

            tampered_approval = deepcopy(approval)
            tampered_approval["task_digest"] = "sha256:" + "0" * 64
            tampered_payload = {**run_payload, "approval_receipt": tampered_approval}
            tampered = client.post(
                "/api/workspace/task-intake/run",
                json=tampered_payload,
                headers=headers,
            )
            assert tampered.status_code == 409
            assert "TASK_INTAKE_APPROVAL_INVALID" in tampered.json()["detail"]["message"]

            tampered_candidate = deepcopy(candidate)
            tampered_candidate["prompt_length"] += 1
            candidate_tamper = client.post(
                "/api/workspace/task-intake/run",
                json={**run_payload, "candidate_receipt": tampered_candidate},
                headers=headers,
            )
            assert candidate_tamper.status_code == 409
            assert "TASK_INTAKE_CANDIDATE_INVALID" in candidate_tamper.json()[
                "detail"
            ]["message"]

            wrong_actor = client.post(
                "/api/workspace/task-intake/run",
                json=run_payload,
                headers={"X-OrgRebase-Actor": "employee:other"},
            )
            assert wrong_actor.status_code == 403

            hold_candidate = client.post(
                "/api/workspace/task-intake/prepare",
                json={**_request_payload(), "customer_id": "customer:other"},
                headers=headers,
            ).json()
            hold_attempt = client.post(
                "/api/workspace/task-intake/run",
                json={
                    **run_payload,
                    "candidate_receipt": hold_candidate,
                    "candidate_digest": hold_candidate["digest"],
                },
                headers=headers,
            )
            assert hold_attempt.status_code == 409

            assert service.state() == before_state
            assert _store_counts(service) == before_counts

            formed = client.post(
                "/api/workspace/task-intake/run",
                json=run_payload,
                headers=headers,
            )
            assert formed.status_code == 200
            result = formed.json()
            assert set(result) == {
                "receipt",
                "tool_invocation",
                "tool_called_event",
                "formation_run_id",
                "tool_evidence",
                "oac_activation",
                "state",
                "task_intake",
            }
            assert result["receipt"]["task_ref"] == "task:quote_acme"
            assert result["state"]["stage"] == "CURRENT"
            assert result["task_intake"] == {
                "digest": result["task_intake"]["digest"],
                "schema_version": "orgrebase.workspace-task-intake-run-receipt.v1",
                "status": "FORMATION_COMPLETED",
                "run_id": service.effective_workflow_run_id,
                "workspace_instance_nonce": candidate[
                    "workspace_instance_nonce"
                ],
                "actor_id": "employee:sales-owner",
                "prompt_digest": candidate["prompt_digest"],
                "prompt_length": candidate["prompt_length"],
                "candidate_digest": candidate["digest"],
                "approval_digest": approval["digest"],
                "task_digest": candidate["task_digest"],
                "formation_receipt_digest": result["receipt"]["digest"],
                "quote_ref": result["receipt"]["deliverable_ref"],
                "oac_activation_binding_digest": None,
                "candidate_receipt": candidate,
                "confirmation_receipt": approval,
                "intake_persisted": True,
                "intake_canonical_target_writes": 0,
                "formation_authority": "ORGREBASE_CONTROL_PLANE",
                "claim_boundary": (
                    "INTAKE_GATE_VERIFIED_THEN_EXISTING_CONTROL_PLANE_FORMED_QUOTE"
                ),
                "artifact_id": "task-intake:quote-v1@r1",
                "artifact_payload_digest": result["task_intake"][
                    "artifact_payload_digest"
                ],
                "event_digest": result["task_intake"]["event_digest"],
            }
            assert result["state"]["task_intake"] == result["task_intake"]
            assert service.export_evidence()["task_intake"] == result["task_intake"]
            assert any(
                row["action"] == "CONFIRM_EXACT_TASK_CANDIDATE"
                and row["receipt_digest"] == approval["digest"]
                and row["target_writes"] == 0
                for row in result["state"]["execution_activity"]["rows"]
            )
            persisted_task_intake = result["task_intake"]
            assert PROMPT not in formed.text
            assert PROMPT not in json.dumps(
                service.export_evidence(), ensure_ascii=False
            )
            assert PROMPT not in json.dumps(
                service.store.event_envelopes(), ensure_ascii=False
            )
            assert PROMPT in "\n".join(service.store.connection.iterdump())

            missing_identity = client.get(
                "/api/workspace/task-intake/work-description"
            )
            assert missing_identity.status_code == 403
            denied = client.get(
                "/api/workspace/task-intake/work-description",
                headers={"X-OrgRebase-Actor": "employee:other"},
            )
            assert denied.status_code == 403
            private_record = client.get(
                "/api/workspace/task-intake/work-description",
                headers=headers,
            )
            assert private_record.status_code == 200
            assert private_record.headers["cache-control"] == "private, no-store"
            assert private_record.headers["pragma"] == "no-cache"
            assert private_record.headers["vary"] == "X-OrgRebase-Actor"
            private_payload = private_record.json()
            assert private_payload["work_description"] == PROMPT
            assert private_payload["run_id"] == service.effective_workflow_run_id
            assert private_payload["actor_id"] == "employee:sales-owner"
            assert private_payload["prompt_digest"] == candidate["prompt_digest"]
            assert private_payload["prompt_length"] == len(PROMPT)
            assert private_payload["prompt_utf8_bytes"] == len(PROMPT.encode("utf-8"))
            assert private_payload["candidate_digest"] == candidate["digest"]
            assert private_payload["approval_digest"] == approval["digest"]
            assert (
                private_payload["formation_receipt_digest"]
                == result["receipt"]["digest"]
            )
            assert private_payload["semantic_use"] == "TASK_INTENT_ONLY"
            assert private_payload["contributes_business_facts"] is False
            assert private_payload["grants_authority"] is False
            assert private_payload["included_in_state"] is False
            assert private_payload["included_in_public_evidence"] is False
            assert private_payload["included_in_events_or_otlp"] is False

            before_replay_counts = _store_counts(service)
            replayed = client.post(
                "/api/workspace/task-intake/run",
                json=run_payload,
                headers=headers,
            )
            assert replayed.status_code == 200
            assert replayed.json()["task_intake"] == persisted_task_intake
            assert _store_counts(service) == before_replay_counts
    finally:
        service.close()

    assert persisted_task_intake is not None
    restarted = WorkspaceService(
        store_path=tmp_path / "run-gate.sqlite3",
        approval_identity_mode=CONTROLLED_LOCAL_HEADER_IDENTITY,
    )
    try:
        assert restarted.state()["task_intake"] == persisted_task_intake
        assert restarted.export_evidence()["task_intake"] == persisted_task_intake
        with TestClient(create_app(workspace_service=restarted)) as client:
            restored = client.get(
                "/api/workspace/task-intake/work-description",
                headers={"X-OrgRebase-Actor": "employee:sales-owner"},
            )
            assert restored.status_code == 200
            assert restored.json()["work_description"] == PROMPT
    finally:
        restarted.close()


def test_task_intake_receipt_rolls_back_with_failed_formation_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORGREBASE_OAC_ADAPTATION_MODE", "optional")
    service = WorkspaceService(store_path=tmp_path / "atomic-intake.sqlite3")
    try:
        with TestClient(create_app(workspace_service=service)) as client:
            candidate = client.post(
                "/api/workspace/task-intake/prepare",
                json=_request_payload(),
            ).json()
            approval = client.post(
                "/api/workspace/task-intake/admit",
                json={
                    "actor_id": "employee:sales-owner",
                    "candidate_receipt": candidate,
                    "candidate_digest": candidate["digest"],
                },
            ).json()
            before_counts = _store_counts(service)

            def fail_tool(*_args: object, **_kwargs: object) -> object:
                raise RuntimeError("CONTROLLED_TOOL_FAILURE")

            monkeypatch.setattr(service, "_invoke_dependency_evidence_tool", fail_tool)
            response = client.post(
                "/api/workspace/task-intake/run",
                json={
                    "actor_id": "employee:sales-owner",
                    "candidate_receipt": candidate,
                    "candidate_digest": candidate["digest"],
                    "approval_receipt": approval,
                    "approval_digest": approval["digest"],
                    "work_description": PROMPT,
                },
            )

            assert response.status_code == 409
            assert response.json()["detail"]["message"] == "CONTROLLED_TOOL_FAILURE"
            assert service.state()["stage"] == "EMPTY"
            assert service.state()["task_intake"] is None
            assert _store_counts(service) == before_counts
            assert not service.store.artifact_exists("task-intake:quote-v1@r1")
            assert not service.store.artifact_exists(
                "task-intake-private-work-description:quote-v1@r1"
            )
            assert all(
                event["event_type"] != "WORKSPACE_TASK_INTAKE_BOUND"
                for event in service.store.event_records()
            )
    finally:
        service.close()


def test_legacy_run_without_private_text_is_reported_without_reconstruction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORGREBASE_OAC_ADAPTATION_MODE", "optional")
    service = WorkspaceService(store_path=tmp_path / "legacy-private-text.sqlite3")
    try:
        with TestClient(create_app(workspace_service=service)) as client:
            candidate = client.post(
                "/api/workspace/task-intake/prepare",
                json=_request_payload(),
            ).json()
            approval = client.post(
                "/api/workspace/task-intake/admit",
                json={
                    "actor_id": "employee:sales-owner",
                    "candidate_receipt": candidate,
                    "candidate_digest": candidate["digest"],
                },
            ).json()
            formed = client.post(
                "/api/workspace/task-intake/run",
                json={
                    "actor_id": "employee:sales-owner",
                    "candidate_receipt": candidate,
                    "candidate_digest": candidate["digest"],
                    "approval_receipt": approval,
                    "approval_digest": approval["digest"],
                    "work_description": PROMPT,
                },
            )
            assert formed.status_code == 200

            with service.store.transaction() as connection:
                connection.execute(
                    "DELETE FROM private_records WHERE record_id=?",
                    ("task-intake-private-work-description:quote-v1@r1",),
                )

            legacy = client.get(
                "/api/workspace/task-intake/work-description",
                headers={"X-OrgRebase-Actor": "employee:sales-owner"},
            )
            assert legacy.status_code == 404
            assert legacy.json()["detail"]["code"] == (
                "TASK_INTAKE_WORK_DESCRIPTION_NOT_RETAINED"
            )
            assert PROMPT not in legacy.text
            assert service.state()["task_intake"] is not None
            assert PROMPT not in json.dumps(
                service.export_evidence(), ensure_ascii=False
            )
    finally:
        service.close()


def test_task_intake_cannot_be_attached_after_quote_already_exists(
    tmp_path: Path,
) -> None:
    profile, template = _contract()
    service = WorkspaceService(store_path=tmp_path / "retroactive-intake.sqlite3")
    try:
        candidate = prepare_task_intake_candidate(
            prompt=PROMPT,
            actor_id=profile.default_task.actor_id,
            customer_id=profile.default_task.customer_id,
            deliverable_kind=profile.default_task.deliverable_kind,
            profile=profile,
            template=template,
            intended_run_id=service.effective_workflow_run_id,
            workspace_instance_nonce=(
                service.task_intake_workspace_instance_nonce()
            ),
        )
        approval = admit_task_intake_candidate(
            candidate,
            candidate_digest=candidate.digest,
            actor_id=profile.default_task.actor_id,
            profile=profile,
            template=template,
            expected_run_id=service.effective_workflow_run_id,
            expected_workspace_instance_nonce=(
                service.task_intake_workspace_instance_nonce()
            ),
            expected_oac_activation_binding_digest=None,
            oac_required=False,
        )
        service.form_quote_with_dependency_evidence(candidate.task_request)
        assert service.state()["stage"] == "CURRENT"
        assert service.state()["task_intake"] is None

        with pytest.raises(
            IntegrityError,
            match="WORKSPACE_TASK_INTAKE_RETROACTIVE_BINDING_DENIED",
        ):
            service.form_quote_with_dependency_evidence(
                candidate.task_request,
                task_intake_candidate=candidate.model_dump(mode="json"),
                task_intake_approval=approval.model_dump(mode="json"),
                task_intake_work_description=PROMPT,
            )

        assert service.state()["task_intake"] is None
        assert not service.store.artifact_exists("task-intake:quote-v1@r1")
    finally:
        service.close()


@pytest.mark.parametrize(
    "same_run_id",
    (False, True),
    ids=("different-run", "same-run-different-store"),
)
def test_task_intake_receipts_cannot_cross_run_or_store(
    tmp_path: Path,
    same_run_id: bool,
) -> None:
    profile, template = _contract()
    run_a = "run:task-intake:A"
    run_b = run_a if same_run_id else "run:task-intake:B"
    first = WorkspaceService(
        store_path=tmp_path / "workspace-a.sqlite3",
        workflow_run_id=run_a,
        task_intake_required=True,
    )
    second = WorkspaceService(
        store_path=tmp_path / "workspace-b.sqlite3",
        workflow_run_id=run_b,
        task_intake_required=True,
    )
    try:
        candidate = prepare_task_intake_candidate(
            prompt=PROMPT,
            actor_id=profile.default_task.actor_id,
            customer_id=profile.default_task.customer_id,
            deliverable_kind=profile.default_task.deliverable_kind,
            profile=profile,
            template=template,
            intended_run_id=first.effective_workflow_run_id,
            workspace_instance_nonce=first.task_intake_workspace_instance_nonce(),
        )
        confirmation = admit_task_intake_candidate(
            candidate,
            candidate_digest=candidate.digest,
            actor_id=profile.default_task.actor_id,
            profile=profile,
            template=template,
            expected_run_id=first.effective_workflow_run_id,
            expected_workspace_instance_nonce=(
                first.task_intake_workspace_instance_nonce()
            ),
            expected_oac_activation_binding_digest=None,
            oac_required=False,
        )
        expected_error = (
            "TASK_INTAKE_CANDIDATE_WORKSPACE_BINDING_MISMATCH"
            if same_run_id
            else "TASK_INTAKE_CANDIDATE_RUN_BINDING_MISMATCH"
        )
        # Both workspaces must have their own identity before testing receipt transfer.
        assert second.task_intake_workspace_instance_nonce() != candidate.workspace_instance_nonce
        with pytest.raises(IntegrityError, match=expected_error):
            second.form_quote_with_dependency_evidence(
                candidate.task_request,
                task_intake_candidate=candidate.model_dump(mode="json"),
                task_intake_approval=confirmation.model_dump(mode="json"),
                task_intake_work_description=PROMPT,
            )
        assert second.state()["stage"] == "EMPTY"
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("actor_id", "employee:attacker"),
        ("customer_id", "customer:other"),
        ("deliverable_kind", "CONTRACT"),
    ),
)
def test_service_authority_revalidates_candidate_scope_without_trusting_api(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    profile, template = _contract()
    service = WorkspaceService(
        store_path=tmp_path / f"forged-{field}.sqlite3",
        task_intake_required=True,
    )
    try:
        candidate = prepare_task_intake_candidate(
            prompt=PROMPT,
            actor_id=profile.default_task.actor_id,
            customer_id=profile.default_task.customer_id,
            deliverable_kind=profile.default_task.deliverable_kind,
            profile=profile,
            template=template,
            intended_run_id=service.effective_workflow_run_id,
            workspace_instance_nonce=service.task_intake_workspace_instance_nonce(),
        )
        forged_payload = candidate.model_dump(mode="json")
        forged_payload.pop("digest")
        forged_payload[field] = value
        forged = TaskIntakeCandidateReceipt.model_validate(forged_payload)
        confirmation = TaskIntakeApprovalReceipt(
            actor_id=forged.actor_id,
            intended_run_id=forged.intended_run_id,
            workspace_instance_nonce=forged.workspace_instance_nonce,
            candidate_digest=forged.digest,
            task_digest=forged.task_digest,
            profile_digest=forged.profile_digest,
            template_digest=forged.template_digest,
            oac_activation_binding_digest=forged.oac_activation_binding_digest,
        )
        with pytest.raises(
            (AuthorizationError, IntegrityError),
            match=r"TASK_INTAKE_(READY_SCOPE|APPROVAL_BINDING)",
        ):
            service.form_quote_with_dependency_evidence(
                forged.task_request,
                task_intake_candidate=forged.model_dump(mode="json"),
                task_intake_approval=confirmation.model_dump(mode="json"),
                task_intake_work_description=PROMPT,
            )
        assert service.state()["stage"] == "EMPTY"
    finally:
        service.close()


def test_required_product_runtime_retires_direct_form_and_fails_closed_on_missing_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORGREBASE_OAC_ADAPTATION_MODE", "optional")
    service = WorkspaceService(
        store_path=tmp_path / "strict-intake.sqlite3",
        task_intake_required=True,
    )
    try:
        with TestClient(create_app(workspace_service=service)) as client:
            direct = client.post("/api/workspace/form")
            assert direct.status_code == 410
            assert direct.json()["detail"]["code"] == (
                "WORKSPACE_TASK_INTAKE_REQUIRED"
            )
            assert service.state()["stage"] == "EMPTY"

            candidate = client.post(
                "/api/workspace/task-intake/prepare",
                json=_request_payload(),
            ).json()
            confirmation = client.post(
                "/api/workspace/task-intake/admit",
                json={
                    "actor_id": "employee:sales-owner",
                    "candidate_receipt": candidate,
                    "candidate_digest": candidate["digest"],
                },
            ).json()
            formed = client.post(
                "/api/workspace/task-intake/run",
                json={
                    "actor_id": "employee:sales-owner",
                    "candidate_receipt": candidate,
                    "candidate_digest": candidate["digest"],
                    "approval_receipt": confirmation,
                    "approval_digest": confirmation["digest"],
                    "work_description": PROMPT,
                },
            )
            assert formed.status_code == 200
            assert formed.json()["state"]["stage"] == "CURRENT"

        with service.store.transaction() as connection:
            connection.execute(
                "DELETE FROM artifacts WHERE artifact_id=?",
                ("task-intake:quote-v1@r1",),
            )
        with pytest.raises(
            IntegrityError,
            match="WORKSPACE_TASK_INTAKE_RECEIPT_REQUIRED",
        ):
            service.state()
        with pytest.raises(
            IntegrityError,
            match="WORKSPACE_TASK_INTAKE_RECEIPT_REQUIRED",
        ):
            service.export_evidence()
    finally:
        service.close()
