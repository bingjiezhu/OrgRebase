from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.workspace.oac_quote_adaptation import (
    OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS,
    OACAdapterActivationBinding,
    OACAdapterCapsule,
    OACOwnerReviewGate,
    OACSourceAdmissionApproval,
)
from orgrebase.workspace.oac_shadow_execution import (
    OACBoundShadowExecutionReceipt,
    OACShadowExecutionError,
    run_oac_bound_shadow_execution,
)
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from scripts.verify_oac_bound_shadow_execution import (
    verify_oac_bound_shadow_execution,
)

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "examples/enterprise-quote-pilot/evergreen"
QUOTE_SKILL_PACKAGE = json.loads(
    (ROOT / "skills/enterprise-quote-compose/package.json").read_text(encoding="utf-8")
)


def _sealed(value: dict[str, Any]) -> dict[str, Any]:
    selected = copy.deepcopy(value)
    selected.pop("digest", None)
    selected["digest"] = sha256_digest(selected)
    return selected


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _fake_competition_runner(**kwargs: Any) -> dict[str, Any]:
    output = Path(kwargs["output_dir"])
    run_id = kwargs["execution_run_id"]
    context_digest = kwargs["context_envelope_digest"]
    pack = load_enterprise_quote_pilot_pack(kwargs["pack_path"])
    project_id = "project:oac-shadow-test"
    task_id = "task:oac-shadow-test-product-a1"
    actions = []
    for sequence, action in enumerate(
        (
            "create_project",
            "plan_dag",
            "delegate_task",
            "ack_task",
            "submit_task",
            "check_task",
            "accept_task_result",
            "complete_project",
        ),
        start=1,
    ):
        actions.append(
            _sealed(
                {
                    "schema_version": "test.agentteams-action.v1",
                    "sequence": sequence,
                    "key": f"{task_id}:{action}",
                    "tool": "projectflow"
                    if action in {"create_project", "plan_dag", "accept_task_result", "complete_project"}
                    else "taskflow",
                    "action": action,
                    "status": "completed" if action == "complete_project" else "ok",
                }
            )
        )
    action_by_name = {item["action"]: item for item in actions}
    worker_input = _sealed(
        {
            "schema_version": "test.worker-input.v1",
            "run_id": run_id,
            "task_id": task_id,
            "context_envelope_digest": context_digest,
            "candidate_only": True,
            "target_writes": 0,
        }
    )
    worker_output = _sealed(
        {
            "schema_version": "test.worker-output.v1",
            "run_id": run_id,
            "task_id": task_id,
            "status": "PASS",
            "candidate_only": True,
            "target_writes": 0,
        }
    )
    binding = _sealed(
        {
            "schema_version": "test.task-binding.v1",
            "run_id": run_id,
            "task_id": task_id,
            "context_envelope_digest": context_digest,
            "candidate_only": True,
            "target_writes": 0,
            "delegate_action_digest": action_by_name["delegate_task"]["digest"],
            "ack_action_digest": action_by_name["ack_task"]["digest"],
            "submit_action_digest": action_by_name["submit_task"]["digest"],
            "check_action_digest": action_by_name["check_task"]["digest"],
            "accept_action_digest": action_by_name["accept_task_result"]["digest"],
        }
    )
    process = _sealed(
        {
            "schema_version": "test.process-receipt.v1",
            "run_id": run_id,
            "task_id": task_id,
            "context_envelope_digest": context_digest,
            "input_digest": sha256_digest(worker_input),
            "output_digest": sha256_digest(worker_output),
            "started_after_ack_action_digest": action_by_name["ack_task"]["digest"],
            "canonical_target_writes": 0,
        }
    )
    tool_receipt = _sealed(
        {
            "schema_version": "orgrebase.controlled-http-receipt.v1",
            "id": f"tool:{run_id}",
            "connector_kind": "TOOL",
            "operation": "READ_DEPENDENCY_EVIDENCE",
            "run_id": run_id,
            "request_digest": sha256_digest({"run_id": run_id}),
            "response_digest": sha256_digest({"result": "ok"}),
            "status": "SUCCEEDED",
            "http_status": 200,
            "attempts": 1,
            "etag": None,
            "endpoint_class": "LOOPBACK_TCP",
            "evidence_class": "CONTROLLED_LOCAL_REAL_HTTP",
            "target_writes": 0,
        }
    )
    tool = {
        "schema_version": "test.tool-invocation.v1",
        "run_id": run_id,
        "result": {"dependencies": ["price"], "target_writes": 0},
        "receipt": tool_receipt,
        "candidate_only": True,
        "target_writes": 0,
    }
    skill = _sealed(
        {
            "schema_version": "test.skill-receipt.v1",
            "run_id": run_id,
            "task_id": "task:quote-test",
            "package_id": QUOTE_SKILL_PACKAGE["package_id"],
            "package_digest": QUOTE_SKILL_PACKAGE["manifest_digest"],
            "authorization_mode": "RELEASE",
            "target_writes": 0,
        }
    )
    formation_receipt = _sealed(
        {
            "schema_version": "orgrebase.controlled-agentteams-formation-receipt.v1",
            "run_id": run_id,
            "canonical_target_writes": 0,
        }
    )
    prepared = _sealed(
        {
            "schema_version": "test.prepared-formation.v1",
            "artifact_writes": [
                {
                    "artifact_id": "formation:test",
                    "media_type": "application/vnd.orgrebase.controlled-agentteams-formation-receipt+json",
                    "payload": formation_receipt,
                }
            ],
            "candidate_only": True,
        }
    )
    envelope = _sealed(
        {
            "schema_version": "orgrebase.golden-execution-envelope.v1",
            "run_id": run_id,
            "correlation_id": "correlation:test",
            "pack_digest": pack.pack_digest,
            "model_provider": kwargs["model_provider"],
            "started_at": "2026-08-28T00:00:00Z",
            "nonce": "nonce-test",
            "context_envelope_digest": context_digest,
        }
    )
    summary = _sealed(
        {
            "schema_version": "orgrebase.golden-competition-summary.v1",
            "status": "PASS",
            "run_id": run_id,
            "correlation_id": "correlation:test",
            "execution_envelope_digest": envelope["digest"],
            "nonce": "nonce-test",
            "project_id": project_id,
            "enterprise_pack": {
                "pack_id": pack.pack_id,
                "pack_revision": pack.pack_revision,
                "pack_digest": pack.pack_digest,
                "synthetic": True,
            },
            "context_envelope_digest": context_digest,
            "task_bindings": [binding],
            "agentteams_action_count": len(actions),
            "agentteams_action_digests": [item["digest"] for item in actions],
            "tool_receipt_digest": tool_receipt["digest"],
            "skill_invocation_receipt_digest": skill["digest"],
            "skill_release_state": "CANARY",
            "prepared_formation_digest": prepared["digest"],
            "controlled_agentteams_formation_receipt_digest": formation_receipt["digest"],
            "canonical_target_writes": 0,
            "external_promotion_status": "NOT_RUN",
            "project_terminal_state": "completed",
        }
    )
    _write(output / "execution-envelope.json", envelope)
    _write(output / "agentteams/action-journal.json", actions)
    _write(output / f"agentteams/bindings/{task_id}.json", binding)
    _write(output / "process-inputs" / f"{task_id}.json", worker_input)
    _write(output / "process-outputs" / f"{task_id}.json", worker_output)
    _write(output / "process-receipts.json", [process])
    _write(output / "tool/invocation.json", tool)
    _write(output / "skill/receipt.json", skill)
    _write(output / "prepared-formation-bundle.json", prepared)
    _write(output / "summary.json", summary)
    return summary


@pytest.fixture(scope="module")
def shadow_pack(tmp_path_factory: pytest.TempPathFactory):
    base = tmp_path_factory.mktemp("oac-shadow")
    frozen = base / "frozen"
    golden_summary = _sealed(
        {
            "schema_version": "orgrebase.golden-competition-summary.v1",
            "status": "PASS",
            "run_id": "run:frozen-golden:test",
            "canonical_target_writes": 0,
        }
    )
    _write(frozen / "golden-run/summary.json", golden_summary)
    _write(frozen / "manifest.json", {"files": []})
    _write(frozen / "verification.json", {"status": "PASS", "entry_count": 3})
    fencing = base / "fencing.json"
    fenced_decision = {
        "decision_digest": "sha256:" + "f" * 64,
        "verdict": "REJECT",
        "reason_codes": ["STALE_ATTEMPT_FENCED"],
        "target_writes": 0,
    }
    _write(
        fencing,
        {
            "schema_version": "orgrebase.workspace-agentteams-lifecycle-receipt.v1",
            "run_id": "run:fencing-reference:test",
            "control_decisions": [fenced_decision],
            "canonical_target_writes": 0,
        },
    )
    runtime = load_enterprise_quote_pilot_pack(PACK)
    adaptation_run_id = "run:oac-adaptation:test"
    shadow_run_id = "run:oac-bound-shadow:test"
    owner_review_summary_digest = "sha256:" + "9" * 64
    review_gate = OACOwnerReviewGate(
        adaptation_run_id=adaptation_run_id,
        mapping_set_digest="sha256:" + "4" * 64,
        organization_snapshot_digest="sha256:" + "5" * 64,
        organizational_demand_digest="sha256:" + "6" * 64,
        owner_review_summary_digest=owner_review_summary_digest,
        profile_digest=runtime.profile.digest,
        pack_digest=runtime.pack_digest,
        owner_ref="human:owner",
        review_duration_ms=4000,
        prepared_at_epoch_ms=0,
        not_before_epoch_ms=4000,
        prepared_at="1970-01-01T00:00:00.000Z",
        not_before="1970-01-01T00:00:04.000Z",
    )
    approval = OACSourceAdmissionApproval(
        adaptation_run_id=adaptation_run_id,
        command_id="command:approve:test",
        actor_id="human:owner",
        candidate_digest="sha256:" + "4" * 64,
        owner_review_summary_digest=owner_review_summary_digest,
        acknowledgements=OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS,
        review_gate_digest=review_gate.digest,
        review_gate=review_gate,
        approved_at_epoch_ms=4000,
        approved_at="1970-01-01T00:00:04.000Z",
        source_admission_receipt={"digest": "sha256:" + "3" * 64},
        public_validation={"status": "PASS"},
    )
    capsule = OACAdapterCapsule(
        adaptation_run_id=adaptation_run_id,
        profile_digest=runtime.profile.digest,
        pack_digest=runtime.pack_digest,
        mapping_set_digest="sha256:" + "4" * 64,
        owner_review_summary_digest=owner_review_summary_digest,
        organization_snapshot_digest="sha256:" + "5" * 64,
        organizational_demand_digest="sha256:" + "6" * 64,
        source_admission_receipt_digest="sha256:" + "7" * 64,
        approval_digest=approval.digest,
        quote_formation_parity_digest="sha256:" + "8" * 64,
    )
    activation = OACAdapterActivationBinding(
        adaptation_run_id=adaptation_run_id,
        adapter_capsule_digest=capsule.digest,
        profile_digest=runtime.profile.digest,
        pack_digest=runtime.pack_digest,
        execution_run_id=shadow_run_id,
    )
    context = _sealed(
        {
            "schema_version": "orgrebase.task-agent-context-envelope.v1",
            "id": "task-agent-context:quote-test",
            "version": "v1",
            "effect_ceiling": "ZERO_EXTERNAL_EFFECTS",
            "candidate_only": True,
            "canonical_target_writes": 0,
            "created_at": "1970-01-01T00:00:00.000Z",
            "expires_at": "1970-01-01T00:10:00.000Z",
        }
    )
    output = base / "output"
    receipt = run_oac_bound_shadow_execution(
        repo_root=ROOT,
        output_dir=output,
        checkout=base / "checkout-not-used",
        lock_path=base / "lock-not-used.json",
        pack_path=PACK,
        frozen_golden_root=frozen,
        adapter_capsule=capsule,
        activation_binding=activation,
        approval=approval,
        context_envelope=context,
        context_validation_time="1970-01-01T00:05:00.000Z",
        late_attempt_fencing_receipt=fencing,
        competition_runner=_fake_competition_runner,
    )
    return base, output, frozen, fencing, receipt


def _verify(shadow_pack, *, output: Path | None = None):
    _base, original, frozen, fencing, _receipt = shadow_pack
    return verify_oac_bound_shadow_execution(
        root=output or original,
        pack=PACK,
        frozen_golden=frozen,
        fencing_reference=fencing,
    )


def test_shadow_execution_binds_full_candidate_chain_and_same_run_otlp(
    shadow_pack,
) -> None:
    _base, output, _frozen, _fencing, receipt = shadow_pack
    assert isinstance(receipt, OACBoundShadowExecutionReceipt)
    assert receipt.same_run_layers == (
        "SOURCE",
        "CONTEXT",
        "AGENTTEAMS",
        "TOOL",
        "SKILL",
        "OTLP",
        "CANDIDATE",
    )
    assert receipt.shadow_terminal_status == "CANDIDATE_PREPARED_FOR_CONTROL_ADMISSION"
    assert receipt.telemetry_ingestion_count == 3
    assert receipt.review_gate_digest == receipt.approval_review_gate_digest
    assert receipt.context_freshness_basis == "LOGICAL_EVENT_TIME"
    assert (output / "inputs/review-gate.json").is_file()
    assert receipt.canonical_target_writes == 0
    assert receipt.shadow_execution_run_id not in {
        receipt.adaptation_run_id,
        receipt.frozen_golden_run_id,
    }
    summary = json.loads((output / "shadow-run/summary.json").read_text(encoding="utf-8"))
    assert summary["context_envelope_digest"] == receipt.context_envelope_digest
    assert all(
        item["context_envelope_digest"] == receipt.context_envelope_digest
        for item in summary["task_bindings"]
    )
    verification = _verify(shadow_pack)
    assert verification["status"] == "PASS", verification


@pytest.mark.parametrize(
    ("mutation", "failure"),
    (
        ("run", "SHADOW_RUN_SUBSTITUTION"),
        ("capsule", "CAPSULE_BINDING_MISMATCH"),
        ("profile", "PROFILE_SUBSTITUTION"),
        ("pack", "PACK_SUBSTITUTION"),
        ("context", "CONTEXT_SUBSTITUTION"),
        ("review-gate", "REVIEW_GATE_BINDING_INVALID"),
        ("canonical-write", "CLAIM_BOUNDARY_INVALID"),
        ("late-fencing", "LATE_ATTEMPT_FENCING_REFERENCE_INVALID"),
    ),
)
def test_independent_verifier_rejects_resealed_boundary_substitutions(
    shadow_pack,
    tmp_path: Path,
    mutation: str,
    failure: str,
) -> None:
    _base, output, _frozen, _fencing, _receipt = shadow_pack
    attacked = tmp_path / mutation
    shutil.copytree(output, attacked)
    receipt_path = attacked / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if mutation == "run":
        receipt["shadow_execution_run_id"] = "run:substituted"
    elif mutation == "capsule":
        receipt["adapter_capsule_digest"] = "sha256:" + "0" * 64
    elif mutation == "profile":
        receipt["profile_digest"] = "sha256:" + "0" * 64
    elif mutation == "pack":
        receipt["pack_digest"] = "sha256:" + "0" * 64
    elif mutation == "context":
        receipt["context_envelope_digest"] = "sha256:" + "0" * 64
    elif mutation == "review-gate":
        gate_path = attacked / "inputs/review-gate.json"
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
        gate["owner_ref"] = "human:substituted-owner"
        _write(gate_path, _sealed(gate))
    elif mutation == "canonical-write":
        receipt["canonical_target_writes"] = 1
    elif mutation == "late-fencing":
        receipt["late_attempt_fencing_reference"]["mode"] = "SAME_RUN"
    _write(receipt_path, _sealed(receipt))

    verification = _verify(shadow_pack, output=attacked)
    assert verification["status"] == "FAIL"
    assert failure in verification["failures"]


def test_shadow_receipt_and_pre_execution_binding_schemas_match_models() -> None:
    for model, name in (
        (
            OACBoundShadowExecutionReceipt,
            "workspace-o-a-c-bound-shadow-execution-receipt.schema.json",
        ),
        (
            __import__(
                "orgrebase.workspace.oac_shadow_execution",
                fromlist=["OACShadowPreExecutionBinding"],
            ).OACShadowPreExecutionBinding,
            "workspace-o-a-c-shadow-pre-execution-binding.schema.json",
        ),
    ):
        checked_in = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
        generated = model.model_json_schema(mode="validation")
        generated["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        generated["$id"] = f"https://orgrebase.local/schemas/{name}"
        assert checked_in == generated


def test_shadow_rejects_live_mapping_candidate_not_covered_by_old_approval(
    shadow_pack,
    tmp_path: Path,
) -> None:
    _base, retained, frozen, fencing, _receipt = shadow_pack
    mapping = ROOT / "evidence/oac-agentic-adaptation/latest/mapping-receipt.json"
    if not mapping.is_file():
        pytest.skip("live mapper receipt is not present in this checkout")
    context = _sealed(
        {
            "schema_version": "orgrebase.task-agent-context-envelope.v1",
            "id": "task-agent-context:mapping-substitution-test",
            "version": "v1",
            "effect_ceiling": "ZERO_EXTERNAL_EFFECTS",
            "candidate_only": True,
            "canonical_target_writes": 0,
            "created_at": "1970-01-01T00:00:00.000Z",
            "expires_at": "1970-01-01T00:10:00.000Z",
        }
    )

    with pytest.raises(
        OACShadowExecutionError,
        match="OAC_SHADOW_AGENT_MAPPING_RECEIPT_INVALID",
    ):
        run_oac_bound_shadow_execution(
            repo_root=ROOT,
            output_dir=tmp_path / "output",
            checkout=tmp_path / "unused-checkout",
            lock_path=tmp_path / "unused-lock.json",
            pack_path=PACK,
            frozen_golden_root=frozen,
            adapter_capsule=retained / "inputs/adapter-capsule.json",
            activation_binding=retained / "inputs/activation-binding.json",
            approval=retained / "inputs/approval.json",
            context_envelope=context,
            context_validation_time="1970-01-01T00:05:00.000Z",
            agent_mapping_receipt=mapping,
            late_attempt_fencing_receipt=fencing,
            competition_runner=lambda **_kwargs: pytest.fail(
                "runner must not start across an unapproved mapping digest"
            ),
        )


def test_shadow_rejects_expired_context_before_starting_runner(
    shadow_pack,
    tmp_path: Path,
) -> None:
    _base, output, frozen, fencing, _receipt = shadow_pack
    context = json.loads((output / "inputs/context-envelope.json").read_text(encoding="utf-8"))
    context["expires_at"] = "1970-01-01T00:01:00.000Z"
    context = _sealed(context)

    with pytest.raises(OACShadowExecutionError, match="OAC_SHADOW_CONTEXT_EXPIRED"):
        run_oac_bound_shadow_execution(
            repo_root=ROOT,
            output_dir=tmp_path / "expired-context",
            checkout=tmp_path / "unused-checkout",
            lock_path=tmp_path / "unused-lock.json",
            pack_path=PACK,
            frozen_golden_root=frozen,
            adapter_capsule=output / "inputs/adapter-capsule.json",
            activation_binding=output / "inputs/activation-binding.json",
            approval=output / "inputs/approval.json",
            context_envelope=context,
            context_validation_time="1970-01-01T00:05:00.000Z",
            late_attempt_fencing_receipt=fencing,
            competition_runner=lambda **_kwargs: pytest.fail("runner must not start with an expired context"),
        )
