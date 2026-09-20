from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from orgrebase.api import create_app
from orgrebase.semifinal_view import (
    PUBLIC_REAL_PROCESS_SCHEMA_VERSION,
    public_real_process_validation_view,
    semifinal_evidence_view,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "orgrebase.semifinal-evidence-view.v2"
QUOTE_SKILL_PACKAGE = json.loads(
    (ROOT / "skills/enterprise-quote-compose/package.json").read_text(encoding="utf-8")
)


def _copy_retained_evidence(tmp_path: Path) -> Path:
    for relative in (
        "evidence/semifinal-closure/latest",
        "evidence/semifinal-governed/latest",
        "evidence/public-process/latest",
        "evidence/skill-predecessor-rollback/latest",
        "evidence/semifinal-mvp/latest",
    ):
        source = ROOT / relative
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination)
    for relative in (
        "evidence/latest/failure-receipt.json",
        "evidence/latest/rollback-evidence.json",
        "evidence/latest/git-tool-evidence.json",
    ):
        source = ROOT / relative
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    baseline = Path("benchmark/quote-value-v0.1/public/current-process-baseline.json")
    destination = tmp_path / baseline
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / baseline, destination)
    return tmp_path


def _copy_public_real_process_evidence(tmp_path: Path) -> Path:
    for relative in (
        "evidence/public-real-process/latest",
        "evidence/oac-public-real-process/latest",
        "benchmark/quote-value-v0.4-bpi-real-process",
    ):
        source = ROOT / relative
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination)
    return tmp_path


def _copy_formation_taskflow_evidence(tmp_path: Path) -> Path:
    relative = "evidence/formation-taskflow/latest"
    destination = tmp_path / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / relative, destination)
    return tmp_path


def test_frozen_semifinal_view_projects_complete_retained_evidence_lane() -> None:
    view = semifinal_evidence_view(ROOT)
    retained_index = json.loads(
        (ROOT / "evidence/semifinal-closure/latest/evidence-index.json").read_text(
            encoding="utf-8"
        )
    )

    assert view["schema_version"] == SCHEMA_VERSION
    assert view["status"] == "PASS"
    assert view["verification_status"] == "PASS"
    assert view["completion_status"] == "CONTROLLED_LOCAL_MVP_WITH_DECLARED_NOT_RUN"
    assert view["production_readiness"] is False
    assert view["evidence_lane"] == "RETAINED_SPEC_053_MECHANISM"
    assert view["title"] == "Independent mechanism evidence (separate run-scoped lane)"
    assert view["current_pack_pilot_run"] is False
    assert view["pack_pilot_binding"] == "NOT_SAME_RUN"
    assert view["read_model_target_writes"] == 0
    assert "canonical_target_writes" not in view

    collaboration = view["agent_collaboration"]
    assert collaboration["action_count"] == len(collaboration["actions"]) == 34
    assert collaboration["binding_count"] == len(collaboration["bindings"]) == 6
    assert [item["sequence"] for item in collaboration["actions"]] == list(range(1, 35))
    assert all(
        set(item) == {"sequence", "key", "tool", "action", "status", "ok", "digest"}
        for item in collaboration["actions"]
    )
    assert all(item["candidate_only"] and item["target_writes"] == 0 for item in collaboration["bindings"])
    assert [item["reason_codes"] for item in collaboration["control_exceptions"]] == [
        ["RESULT_DIGEST_MISMATCH"],
        ["STALE_ATTEMPT_FENCED"],
    ]
    assert [item["stage"] for item in collaboration["lifecycle"]] == [
        "CREATE",
        "DELEGATE",
        "ACK",
        "HANDOFF_BINDING",
        "SUBMIT",
        "CHECK",
        "ACCEPT",
        "COMPLETE",
    ]
    assert collaboration["authority"] == {
        "agentteams_task": "SCHEDULING_AND_CANDIDATE_FACTS_ONLY",
        "canonical_state": "STATESTORE_REBASE_WORKFLOW_ONLY",
        "candidate_target_writes": 0,
    }
    assert collaboration["exception_semantics"] == {
        "result_conflict": "REJECTED",
        "reassignment": "EXECUTED",
        "late_result_fencing": "FENCED",
        "canonical_transaction_atomic_rollback": ("VALIDATED_CONTROLLED_LOCAL_ATOMIC_ROLLBACK"),
        "downstream_state_compensation": ("VALIDATED_LOCAL_DETERMINISTIC_SEPARATE_RUN"),
        "reversible_external_git_compensation": ("VALIDATED_LOCAL_REAL_TOOL_SEPARATE_RUN"),
        "real_enterprise_connector_compensation": "NOT_RUN",
    }
    compensation = collaboration["compensation_evidence"]
    assert compensation["status"] == "PASS"
    assert compensation["git_residual_effect_count"] == 0
    assert compensation["read_model_target_writes"] == 0
    assert "NOT_SAME_GOLDEN_RUN" in compensation["claim_boundary"]
    assert collaboration["run_separation"] == {
        "active_pack_pilot": "DIFFERENT_RUN",
        "retained_governed_successor": "SAME_RETAINED_RUN",
    }
    formation = collaboration["formation_taskflow"]
    assert formation["status"] == "PASS"
    assert formation["source"] == "RETAINED_TWO_DOMAIN_FORMATION_TASKFLOW"
    assert formation["planned_domain_ids"] == ["legal", "product"]
    assert formation["actual_agentteams_domain_ids"] == ["legal", "product"]
    assert formation["planned_domain_count"] == 2
    assert formation["actual_agentteams_domain_count"] == 2
    assert formation["topology_match"] is True
    assert [item["domain_id"] for item in formation["domain_tasks"]] == [
        "legal",
        "product",
    ]
    assert formation["reviewer_barrier"]["depends_on"] == [
        item["task_id"] for item in formation["domain_tasks"]
    ]
    assert formation["agentteams_action_count"] == 20
    assert formation["task_binding_count"] == 3
    assert formation["candidate_only"] is True
    assert formation["canonical_target_writes"] == 0
    assert formation["read_model_target_writes"] == 0
    for digest_key in (
        "execution_plan_digest",
        "formation_receipt_digest",
        "context_envelope_digest",
        "coalition_plan_digest",
        "receipt_digest",
        "verification_digest",
    ):
        assert formation[digest_key].startswith("sha256:")

    value = view["value_and_responsibility"]
    assert value["primary_user"] == "Enterprise Quote Operator"
    assert value["process_status"] == "NOT_RUN"
    assert len(value["process_steps"]) == 8
    assert [step["responsible"] for step in value["process_steps"]] == [
        ["Quote Operator"],
        ["Product Owner"],
        ["Legal Owner"],
        ["Finance Owner"],
        ["Quote Operator"],
        ["Quote Operator"],
        ["Changed-source Owner"],
        ["Exact Approval Owner"],
    ]
    assert [step["to_be_responsible"] for step in value["process_steps"]] == [
        ["Quote Operator"],
        ["Product Agent"],
        ["Legal Agent"],
        ["Finance Agent"],
        ["GTM Agent"],
        ["Quote Operator"],
        ["Deterministic Control"],
        ["Bounded Runtime", "Canonical Writer"],
    ]
    assert all(step["named_connector_status"] == "NOT_RUN" for step in value["process_steps"])
    assert len(value["metrics"]) == 7
    assert [item["maturity"] for item in value["metrics"]] == [
        "NOT_RUN",
        "NOT_RUN",
        "VALIDATED_PROXY",
        "NOT_RUN",
        "VALIDATED_PROXY",
        "VALIDATED_PROXY",
        "MODELLED",
    ]
    assert value["cost_model"]["full_rebuild"] == 8.0
    assert value["cost_model"]["selective_rebase"] == 4.05
    assert value["cost_model"]["saving_rate"] == 0.49375
    assert value["cost_model"]["enterprise_roi"] == "NOT_RUN"
    assert "official_completion" not in value

    spine = view["evidence_spine"]
    assert [item["stage"] for item in spine] == [
        "SOURCE",
        "AGENTTEAMS",
        "TOOL",
        "SKILL",
        "OTLP",
        "CANDIDATE",
        "GOVERNED_APPLY",
    ]
    assert all(item["target_writes"] == 0 for item in spine[:-1])
    assert spine[-1]["target_writes"] == 6
    assert spine[-1]["authority"] == "STATESTORE_REBASE_WORKFLOW_ONLY"
    assert spine[4]["signals"] == ["traces", "logs", "metrics"]

    evidence_index = view["evidence_index"]
    assert evidence_index == {
        "schema_version": "orgrebase.semifinal-closure-evidence-index.v1",
        "status": "PASS",
        "archive_status": "FROZEN_HISTORICAL_VALIDATION",
        "run_id": view["run_id"],
        "current_task_run": False,
        "entry_count": 148,
        "index_digest": retained_index["digest"],
        "pack_digest": retained_index["pack_digest"],
        "evidence_classes": [
            "CONTROLLED_LOCAL_GOVERNED_CANONICAL_WRITE_WITH_SIGKILL_RECOVERY",
            "CONTROLLED_LOCAL_INSTALLED_WHEEL",
            "CONTROLLED_LOCAL_INTEGRATED_VERTICAL_SLICE",
            "CONTROLLED_LOCAL_NATIVE_TASKFLOW",
            "CONTROLLED_LOCAL_REAL_HTTP",
        ],
        "verifier_status": "PASS",
        "privacy": {
            "canary_status": "PASS",
            "canary_reason_code": "PRIVACY_PAYLOAD_REJECTED",
            "rejection_reason_code": "OTLP_RESTRICTED_FIELD:secret",
            "raw_payload_retained": False,
            "sensitive_values_disclosed": False,
        },
        "target_writes": 0,
    }

    assert view["operations"]["alert"]["status"] == "PASS"
    assert view["operations"]["alert"]["alerts"] == 0
    assert view["operations"]["alert"]["negative_probe"]["status"] == "ALERT"
    assert view["operations"]["alert"]["negative_probe"]["alerts"] == 5
    assert view["operations"]["alert"]["negative_probe"]["severity"] == "CRITICAL"
    retention = view["operations"]["retention"]
    assert retention["canonical_business_evidence_deleted"] is False
    assert retention["canonical_business_evidence_affected"] is False
    assert retention["indexed_telemetry_seconds"] == 30 * 24 * 60 * 60
    assert retention["rejected_payload_diagnostic_seconds"] == 7 * 24 * 60 * 60
    assert retention["rejected_raw_payload_persisted"] is False
    assert retention["retained_rejection_count"] == 1
    assert retention["deleted_rejection_count"] == 0
    assert view["operations"]["query"]["count"] == 3
    assert view["operations"]["capacity"]["successes"] == 25
    assert view["operations"]["capacity"]["production_ready_claimed"] is False
    assert view["operations"]["backup"]["digest_match"] is True
    assert view["operations"]["connectors"]["source"]["http_status"] == 200
    assert view["operations"]["connectors"]["tool"]["http_status"] == 200
    assert set(view["operations"]["connectors"]["enterprise"].values()) == {"NOT_RUN"}
    assert "orgrebase.workflow.run_id" in view["operations"]["otlp"]["key_fields"]
    assert view["operations"]["otlp"]["signals"] == ["traces", "logs", "metrics"]
    assert len(view["operations"]["otlp"]["key_fields"]) == 9
    assert view["operations"]["enterprise_readiness"] == {
        "deployment_profile": "controlled-local@v1",
        "deployment_evidence_class": "OBSERVED_CONTROLLED_LOCAL",
        "production_ready": False,
        "contractual_sla": None,
        "geographic_failover": "NOT_RUN",
        "sbom_format": "CycloneDX",
        "artifact_version": "0.4.0",
        "sbom_component_count": 33,
        "contributing_guide": "PRESENT_IN_REPOSITORY",
    }

    assert [item["name"] for item in view["skills"]] == sorted(
        [
            "enterprise-launch-readiness",
            "enterprise-quote-compose",
            "structured-domain-handoff",
        ]
    )
    assert all(item["case_count"] == 8 for item in view["skills"])
    assert all(len(item["evaluation_partitions"]) == 8 for item in view["skills"])
    assert all(item["gates_passed"] == item["gate_count"] == 11 for item in view["skills"])
    assert all(item["discovered"] is True for item in view["skills"])
    assert all(item["resource_mode"] == "INSTALLED_WHEEL" for item in view["skills"])
    assert all(item["lifecycle"][-1] == "CANARY" for item in view["skills"])
    rollback_modes = {item["name"]: item["rollback_mode"] for item in view["skills"]}
    assert rollback_modes == {
        "enterprise-launch-readiness": "DECISION_ONLY",
        "enterprise-quote-compose": "EXECUTED_AND_INVOKED",
        "structured-domain-handoff": "DECISION_ONLY",
    }
    quote_skill = next(item for item in view["skills"] if item["name"] == "enterprise-quote-compose")
    assert quote_skill["version"] == QUOTE_SKILL_PACKAGE["version"]
    assert quote_skill["package_id"] == QUOTE_SKILL_PACKAGE["package_id"]
    assert quote_skill["package_digest"] == QUOTE_SKILL_PACKAGE["manifest_digest"]
    assert quote_skill["invocation"]["status"] == "SUCCESS"
    assert quote_skill["invocation"]["installed_wheel_receipt_digest"].startswith("sha256:")

    assert view["recovery"]["sigkill_count"] == 2
    assert [item["phase"] for item in view["recovery"]["events"]] == [
        "prepare",
        "launch",
    ]
    assert [item["disposition"] for item in view["recovery"]["events"]] == [
        "RETRY_INTENT",
        "ADOPT_COMMITTED",
    ]
    assert "LIVE_DISTRIBUTED_AGENTTEAMS" in view["exceptions"]["not_run"]
    assert view["candidate_chain"]["terminal_state"] == "CANDIDATE_ACCEPTED"
    assert view["governed_apply"]["final_quote_ref"] == "work:quote_acme@v3"
    assert "public_mechanism" not in view


def test_semifinal_view_is_read_only_api_surface() -> None:
    client = TestClient(create_app())
    response = client.get("/api/platform/evidence")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["status"] == "PASS"
    assert payload["read_model_target_writes"] == 0
    assert payload["current_pack_pilot_run"] is False
    assert "NOT_CURRENT_PACK_PILOT" in payload["claim_boundary"]
    assert payload["evidence_index"]["current_task_run"] is False
    assert payload["evidence_index"]["verifier_status"] == "PASS"
    assert payload["evidence_index"]["target_writes"] == 0
    assert payload["agent_collaboration"]["formation_taskflow"]["status"] == "PASS"
    assert payload["agent_collaboration"]["formation_taskflow"]["topology_match"] is True
    assert client.get("/api/semifinal/evidence").json() == payload


def test_public_real_process_view_projects_independently_verified_bpi_lane() -> None:
    view = public_real_process_validation_view(ROOT)
    verification = json.loads(
        (ROOT / "evidence/public-real-process/latest/bpi2019-real-process-verification.json").read_text(
            encoding="utf-8"
        )
    )

    assert view["schema_version"] == PUBLIC_REAL_PROCESS_SCHEMA_VERSION
    assert view["status"] == "PASS"
    assert view["generated"] is True
    assert view["read_model_target_writes"] == 0
    assert view["archive_snapshot_at"] is None
    assert view["archive_time_basis"] == (
        "CONTENT_ADDRESSED_EVIDENCE_NO_TRUSTED_COMPLETION_TIME"
    )
    assert view["archive_identity_digest"] == verification["digest"]
    assert view["dataset"] == {
        "title": "BPI Challenge 2019",
        "source_nature_zh": "大型跨国涂料企业的匿名真实 SAP 采购到付款事件日志",
        "source_nature_en": (
            "anonymized real SAP purchase-to-pay event log from a large "
            "multinational coatings and paints company"
        ),
        "purchase_order_item_traces": 251734,
        "events": 1595923,
        "activity_types": 42,
        "anonymous_identities": 627,
    }
    assert view["scenario"]["query_count"] == 128
    assert view["scenario"]["eligible_query_count"] == 8318
    assert view["scenario"]["outcome_blind_ordering"] is True
    assert view["scenario"]["ground_truth_evidence_class"] == "TASK_DEFINED_QUERY_GROUND_TRUTH"
    assert view["scenario"]["change_event_type"] == "Change Price"
    assert view["scenario"]["unique_observed_event_count"] == 45227
    assert view["action_scopes"] == {
        "vendor_broadcast": {"action_scope": 66549, "safe_reduction": 0.0},
        "purchase_document": {
            "action_scope": 3415,
            "safe_reduction": 0.948684,
        },
        "oac_typed_item": {"action_scope": 201, "safe_reduction": 0.99698},
    }
    assert view["assurance"] == {
        "query_contract_recall": 1.0,
        "unsafe_false_unaffected": 0,
        "unsafe_false_unaffected_rate": 0.0,
        "lineage_closure_rate": 1.0,
    }
    assert view["source"]["license"] == "CC-BY-4.0"
    assert view["source"]["raw_bytes_in_repository"] is False
    assert view["source"]["raw_sha256"].startswith("sha256:")
    assert view["verification"]["status"] == "PASS"
    assert view["verification"]["queries_replayed"] == 128
    assert view["verification"]["strategies_replayed"] == 3
    assert view["verification"]["benchmark_receipt_digest"].startswith("sha256:")
    assert view["verification"]["replay_receipt_digest"].startswith("sha256:")
    assert "NOT_REAL_QUOTE_DATA" in view["claim_boundary"]
    adaptation = view["agentic_adaptation"]
    assert adaptation["status"] == "PASS"
    assert adaptation["claim_ceiling"] == ("PUBLIC_REAL_DATA_OAC_MAPPING_AND_TYPED_SCOPE_EXECUTION")
    assert adaptation["model"] == {
        "status": "VALID",
        "evidence_class": "LIVE_MODEL",
        "provider": "vertex-ai",
        "model_id": "gemini-3.8-flash",
        "provider_request_observed": True,
    }
    assert adaptation["agentteams"]["action_count"] == 9
    assert adaptation["agentteams"]["action_sequence"] == [
        "create_project",
        "plan_dag",
        "ready_nodes",
        "delegate_task",
        "ack_task",
        "submit_task",
        "check_task",
        "accept_task_result",
        "complete_project",
    ]
    assert adaptation["mapping"]["unknown_count"] == 4
    assert adaptation["mapping"]["unknown_dimensions"] == [
        "APPROVAL_AUTHORITIES",
        "ORGANIZATION_VALUES",
        "PERMISSIONS",
        "REAL_RESPONSIBLE_OWNERS",
    ]
    assert adaptation["admission"]["early_attempt"] == "REJECTED_TOO_EARLY"
    assert adaptation["admission"]["elapsed_ms"] >= 4000
    assert adaptation["admission"]["external_human_identity_proven"] is False
    assert adaptation["execution"] == {
        "query_count": 128,
        "recall": 1.0,
        "unsafe_false_unaffected_rate": 0.0,
        "canonical_target_writes": 0,
    }
    assert "NOT_ARBITRARY_ENTERPRISE_ADAPTATION" in adaptation["limitations"]


def test_public_real_process_view_is_a_read_only_api_surface() -> None:
    response = TestClient(create_app()).get("/api/public-real-process/validation")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == PUBLIC_REAL_PROCESS_SCHEMA_VERSION
    assert payload["status"] == "PASS"
    assert payload["read_model_target_writes"] == 0
    assert payload["dataset"]["purchase_order_item_traces"] == 251734
    assert payload["action_scopes"]["oac_typed_item"]["action_scope"] == 201
    assert payload["agentic_adaptation"]["model"]["evidence_class"] == "LIVE_MODEL"
    assert payload["agentic_adaptation"]["read_model_target_writes"] == 0


def test_public_real_process_view_isolates_tampered_agentic_adaptation(
    tmp_path: Path,
) -> None:
    project_root = _copy_public_real_process_evidence(tmp_path)
    receipt_path = project_root / "evidence/oac-public-real-process/latest/adaptation-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["model_attempt"]["model_id"] = "unverified-model"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    view = public_real_process_validation_view(project_root)

    assert view["status"] == "PASS"
    assert view["agentic_adaptation"] == {
        "status": "FAIL",
        "read_model_target_writes": 0,
        "failures": [
            "ADAPTATION_SEMANTICS",
            "CLOSED_WORLD_MANIFEST",
            "DIGEST:receipt",
        ],
    }


def test_public_real_process_view_fails_closed_on_tampered_summary(
    tmp_path: Path,
) -> None:
    project_root = _copy_public_real_process_evidence(tmp_path)
    summary_path = project_root / "evidence/public-real-process/latest/summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["query_count"] = 127
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    view = public_real_process_validation_view(project_root)

    assert view == {
        "schema_version": PUBLIC_REAL_PROCESS_SCHEMA_VERSION,
        "status": "FAIL",
        "read_model_target_writes": 0,
        "generated": False,
        "failures": ["DIGEST:summary", "SCENARIO_CONTRACT"],
    }


def test_public_real_process_view_reports_not_produced_when_receipts_are_missing(
    tmp_path: Path,
) -> None:
    view = public_real_process_validation_view(tmp_path)

    assert view["schema_version"] == PUBLIC_REAL_PROCESS_SCHEMA_VERSION
    assert view["status"] == "UNAVAILABLE"
    assert view["generated"] is False
    assert view["read_model_target_writes"] == 0
    assert set(view["missing"]) == {
        "dataset_manifest",
        "license_manifest",
        "projection",
        "receipt",
        "summary",
        "verification",
    }
    for prohibited in ("dataset", "scenario", "action_scopes", "assurance"):
        assert prohibited not in view


def test_semifinal_view_performs_no_filesystem_or_canonical_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("READ_MODEL_MUST_NOT_WRITE")

    for method_name in (
        "write_text",
        "write_bytes",
        "mkdir",
        "touch",
        "unlink",
        "rename",
        "replace",
    ):
        monkeypatch.setattr(Path, method_name, forbidden)

    view = semifinal_evidence_view(ROOT)

    assert view["status"] == "PASS"
    assert view["read_model_target_writes"] == 0
    assert view["agent_collaboration"]["bindings"]
    assert all(binding["target_writes"] == 0 for binding in view["agent_collaboration"]["bindings"])


def test_semifinal_view_keeps_core_lane_but_hides_missing_formation_topology(
    tmp_path: Path,
) -> None:
    project_root = _copy_retained_evidence(tmp_path)

    view = semifinal_evidence_view(project_root)

    assert view["status"] == "PASS"
    formation = view["agent_collaboration"]["formation_taskflow"]
    assert formation["status"] == "NOT_PRODUCED"
    assert formation["read_model_target_writes"] == 0
    assert set(formation["missing"]) == {
        "actions",
        "coalition",
        "context",
        "formation",
        "plan",
        "receipt",
        "verification",
    }
    for prohibited in (
        "planned_domain_ids",
        "actual_agentteams_domain_ids",
        "domain_tasks",
        "reviewer_barrier",
    ):
        assert prohibited not in formation


def test_semifinal_view_isolates_tampered_formation_topology(
    tmp_path: Path,
) -> None:
    project_root = _copy_retained_evidence(tmp_path)
    _copy_formation_taskflow_evidence(project_root)
    verification_path = project_root / "evidence/formation-taskflow/latest/verification.json"
    verification = json.loads(verification_path.read_text(encoding="utf-8"))
    verification["actual_domain_ids"] = ["legal"]
    verification_path.write_text(json.dumps(verification), encoding="utf-8")

    view = semifinal_evidence_view(project_root)

    assert view["status"] == "PASS"
    formation = view["agent_collaboration"]["formation_taskflow"]
    assert formation["status"] == "FAIL"
    assert formation["read_model_target_writes"] == 0
    assert formation["failures"] == [
        "DIGEST:verification",
        "INDEPENDENT_VERIFICATION_SEMANTICS",
    ]
    for prohibited in (
        "planned_domain_ids",
        "actual_agentteams_domain_ids",
        "domain_tasks",
        "reviewer_barrier",
    ):
        assert prohibited not in formation


@pytest.mark.parametrize(
    ("relative_path", "mutate", "failure"),
    [
        (
            "evidence/semifinal-closure/latest/agentteams/lifecycle-receipt.json",
            lambda value: value["actions"][0].update({"status": "tampered"}),
            "LIFECYCLE_DIGEST",
        ),
        (
            "evidence/semifinal-closure/latest/operations/observability/alert-receipt.json",
            lambda value: value.update({"status": "FAIL"}),
            "ALERT_DIGEST",
        ),
        (
            "evidence/semifinal-closure/latest/operations/observability/privacy-rejection.json",
            lambda value: value.update({"raw_payload_retained": True}),
            (
                "CLOSURE_INDEX_FILE_BINDING:"
                "operations/observability/privacy-rejection.json"
            ),
        ),
        (
            "evidence/semifinal-closure/latest/skills/evaluations/enterprise-quote-compose.json",
            lambda value: value.update({"verdict": "REJECT"}),
            "SKILL_EVALUATION_DIGEST:enterprise-quote-compose",
        ),
        (
            "evidence/semifinal-governed/latest/runtime/process-recovery.json",
            lambda value: value.update({"sigkill_count": 1}),
            "RECOVERY_DIGEST",
        ),
        (
            "evidence/semifinal-mvp/latest/summary.json",
            lambda value: value["completion_totals"].update({"validated_count": 20}),
            "SEMIFINAL_MVP_DIGEST",
        ),
    ],
)
def test_semifinal_view_fails_closed_on_tamper(
    tmp_path: Path,
    relative_path: str,
    mutate: Any,
    failure: str,
) -> None:
    project_root = _copy_retained_evidence(tmp_path)
    path = project_root / relative_path
    value = json.loads(path.read_text(encoding="utf-8"))
    mutate(value)
    path.write_text(json.dumps(value), encoding="utf-8")

    view = semifinal_evidence_view(project_root)

    assert view["status"] == "FAIL"
    assert view["verification_status"] == "FAIL"
    assert view["completion_status"] == "NOT_ASSESSED"
    assert view["production_readiness"] is False
    assert failure in view["failures"]
    assert view["read_model_target_writes"] == 0
    assert view["current_pack_pilot_run"] is False
    assert "NO_EVIDENCE_PROJECTION" in view["claim_boundary"]
    for prohibited in (
        "run_id",
        "agent_collaboration",
        "evidence_spine",
        "evidence_index",
        "operations",
        "skills",
        "recovery",
        "governed_apply",
        "value_and_responsibility",
    ):
        assert prohibited not in view


def test_semifinal_view_reports_unavailable_without_retained_pack(
    tmp_path: Path,
) -> None:
    view = semifinal_evidence_view(tmp_path)

    assert view["schema_version"] == SCHEMA_VERSION
    assert view["status"] == "UNAVAILABLE"
    assert view["verification_status"] == "UNAVAILABLE"
    assert view["completion_status"] == "NOT_ASSESSED"
    assert view["production_readiness"] is False
    assert view["evidence_lane"] == "RETAINED_SPEC_053_MECHANISM"
    assert view["read_model_target_writes"] == 0
    assert view["current_pack_pilot_run"] is False
    assert "candidate" in view["missing"]
    assert "lifecycle" in view["missing"]
    assert "skill_evaluation:enterprise-quote-compose" in view["missing"]
    assert "NO_EVIDENCE_PROJECTION" in view["claim_boundary"]
    assert "run_id" not in view
    assert "agent_collaboration" not in view
    assert "evidence_index" not in view


def test_semifinal_view_fails_closed_when_any_indexed_file_binding_drifts(
    tmp_path: Path,
) -> None:
    project_root = _copy_retained_evidence(tmp_path)
    path = project_root / "evidence/semifinal-closure/latest/quote-shadow/verification.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["status"] = "TAMPERED"
    path.write_text(json.dumps(value), encoding="utf-8")

    view = semifinal_evidence_view(project_root)

    assert view["status"] == "FAIL"
    assert (
        "CLOSURE_INDEX_FILE_BINDING:quote-shadow/verification.json"
        in view["failures"]
    )
    assert "evidence_index" not in view
