from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import shutil
import sys
import zipfile
from importlib.metadata import version
from pathlib import Path
from types import ModuleType

import pytest

from orgrebase.digest import sha256_digest

ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


agentteams_preflight = _load_script("agentteams_preflight")
collect_agentteams_evidence = _load_script("collect_agentteams_evidence")
goai_gate = _load_script("goai_gate")
generate_release_facts = _load_script("generate_release_facts")
verify_evidence_manifest = _load_script("verify_evidence_manifest")


def test_product_path_release_event_count_is_bound_to_explicit_runtime_contract() -> None:
    historical = json.loads((ROOT / "evidence/workspace/latest/product-path-blackbox.json").read_bytes())
    assert goai_gate._current_product_path_release_failures(historical) == []
    current = copy.deepcopy(historical)
    current["runtime_contract"] = "orgrebase.product-path-runtime-contract.v2"
    primary = next(item for item in current["cases"] if item["id"] == "PP-001")
    primary["facts"]["event_count"] = 14
    assert goai_gate._current_product_path_release_failures(current) == []
    for marker in ("unsupported", None):
        current["runtime_contract"] = marker
        assert "CURRENT_RUNTIME_CONTRACT_INVALID" in goai_gate._current_product_path_release_failures(current)
    del current["runtime_contract"]
    assert "CURRENT_PRIMARY_INTAKE_AND_REVIEW_SCOPE_INVALID" in goai_gate._current_product_path_release_failures(current)
    current["runtime_contract"] = "orgrebase.product-path-runtime-contract.v2"
    primary["facts"]["event_count"] = 12
    assert "CURRENT_PRIMARY_INTAKE_AND_REVIEW_SCOPE_INVALID" in goai_gate._current_product_path_release_failures(current)


def test_retained_product_path_sources_are_verified_before_import(tmp_path: Path) -> None:
    relative = Path("benchmark/product-path-v0.3-task-intake-bound/retained-verifiers")
    shutil.copytree(ROOT / relative, tmp_path / relative)
    evaluator = tmp_path / relative / "workspace_product_path_evaluator.py"
    evaluator.chmod(0o600)
    evaluator.write_bytes(b"raise SystemExit('must not execute substituted historical code')\n")
    result = goai_gate.verify_product_path_blackbox(
        tmp_path, evidence_root=ROOT / "evidence/workspace/latest",
        rebuild_wheel=False, retained_verifiers=True,
    )
    assert result["status"] == "FAIL"
    assert result["failures"] == ["RETAINED_VERIFIER_MATERIAL_INVALID:ValueError"]
    assert result["current_release_qualified"] is False
    with pytest.raises(ValueError, match="only for historical"):
        goai_gate.verify_product_path_blackbox(ROOT, retained_verifiers=True)


def test_agentteams_preflight_never_discloses_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agentteams_preflight.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(agentteams_preflight, "_command", lambda command: (True, "ok"))
    monkeypatch.setenv("AGENTTEAMS_LLM_API_KEY", "must-not-appear")
    report = agentteams_preflight.preflight()
    assert report["status"] == "READY"
    assert report["secrets_disclosed"] is False
    assert "must-not-appear" not in json.dumps(report)


def test_agentteams_preflight_accepts_vertex_adc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agentteams_preflight.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(agentteams_preflight, "_command", lambda command: (True, "ok"))
    monkeypatch.setattr(
        agentteams_preflight,
        "_vertex_adc",
        lambda: (True, "location=global; model=test; credentials=adc-present"),
    )
    monkeypatch.delenv("AGENTTEAMS_LLM_API_KEY", raising=False)
    report = agentteams_preflight.preflight()
    assert report["status"] == "READY"
    assert report["checks"]["llm.authentication"]["mode"] == "vertex-adc"
    assert report["deployment_model"] == "google/gemini-3.1-flash-lite"


def test_agentteams_preflight_redacts_vertex_project_and_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agentteams_preflight.shutil, "which", lambda name: f"/bin/{name}")

    def command_result(command: list[str]) -> tuple[bool, str]:
        if command[:2] == ["docker", "info"]:
            return True, "24.0.0"
        if command[:3] == ["gcloud", "config", "get-value"]:
            return True, "must-not-leak-gcp-project"
        if command[:4] == ["gcloud", "auth", "application-default", "print-access-token"]:
            return True, "ya29.must-not-leak-adc-token"
        return True, "ok"

    monkeypatch.setattr(agentteams_preflight, "_command", command_result)
    monkeypatch.delenv("AGENTTEAMS_LLM_API_KEY", raising=False)
    report = agentteams_preflight.preflight()
    blob = json.dumps(report)
    assert report["status"] == "READY"
    assert report["checks"]["llm.authentication"]["mode"] == "vertex-adc"
    assert "must-not-leak-gcp-project" not in blob
    assert "ya29" not in blob
    assert "must-not-leak-adc-token" not in blob


def test_published_agentteams_evidence_does_not_disclose_vertex_api() -> None:
    evidence_root = ROOT / "evidence" / "agentteams"
    forbidden = (
        "gen-lang-client-",
        "ya29.",
        "AIza",
        "GOCSPX-",
        "BEGIN PRIVATE KEY",
        "Bearer ",
    )
    for path in evidence_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".json", ".jsonl", ".txt", ".md"}:
            continue
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text, f"{path} discloses Vertex/Gemini credential material"


def test_agentteams_preflight_rejects_empty_docker_info(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agentteams_preflight.shutil, "which", lambda name: f"/bin/{name}")

    def command_result(command: list[str]) -> tuple[bool, str]:
        if command[:2] == ["docker", "info"]:
            return True, "exit=0"
        return True, "ok"

    monkeypatch.setattr(agentteams_preflight, "_command", command_result)
    monkeypatch.setattr(agentteams_preflight, "_vertex_adc", lambda: (True, "ok"))
    monkeypatch.delenv("AGENTTEAMS_LLM_API_KEY", raising=False)
    report = agentteams_preflight.preflight()
    assert report["status"] == "BLOCKED"
    assert report["checks"]["docker.daemon"]["status"] == "BLOCKED"


def test_goai_stage_gate_requires_publishable_fresh_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(goai_gate, "ROOT", tmp_path)
    (tmp_path / "submission").mkdir()
    (tmp_path / "submission" / "INTRO.md").write_text("# title\nshort", encoding="utf-8")
    (tmp_path / "submission" / "deck.pdf").write_bytes(b"%PDF")
    identities = {
        "change-coordinator",
        "product-steward",
        "legal-steward",
        "gtm-steward",
        "skill-curator",
    }
    (tmp_path / "agentteams" / "identities").mkdir(parents=True)
    for name in identities:
        (tmp_path / "agentteams" / "identities" / f"{name}.json").write_text("{}", encoding="utf-8")
    (tmp_path / "submission" / "AGENT-IDENTITIES.md").write_text(
        "\n".join(f"`{name}`" for name in identities), encoding="utf-8"
    )
    (tmp_path / "LICENSE").write_text("Apache-2.0", encoding="utf-8")
    (tmp_path / "README.md").write_text("# source package\n", encoding="utf-8")
    (tmp_path / "evidence" / "latest").mkdir(parents=True)
    (tmp_path / "evidence" / "latest" / "demo.json").write_text(
        json.dumps({"receipt": {"status": "COMPLETED"}}), encoding="utf-8"
    )
    assert goai_gate.evaluate("preliminary")["decision"] == "GO"
    semifinal = goai_gate.evaluate("semifinal")
    assert semifinal["decision"] == "NO_GO"
    assert (
        "semifinal.core_change_advisory_live_agentteams"
        in semifinal["blocking_failures"]
    )
    assert "semifinal.oac_local_runtime_admission" in semifinal["blocking_failures"]
    assert "semifinal.oac_governed_evolution" in semifinal["blocking_failures"]
    assert "semifinal.oac_enterprise_adaptation" in semifinal["blocking_failures"]
    assert "semifinal.quote_value_evidence" in semifinal["blocking_failures"]
    assert "semifinal.integrated_candidate_closure" in semifinal["blocking_failures"]
    by_id = {item["id"]: item for item in semifinal["checks"]}
    assert by_id["semifinal.oac_local_runtime_admission"]["evidence"] == (
        "evidence/oac-bridge/latest/evidence-index.json"
    )
    assert by_id["semifinal.oac_governed_evolution"]["evidence"] == (
        "evidence/oac-evolution/latest/evidence-index.json"
    )
    assert by_id["semifinal.oac_enterprise_adaptation"]["evidence"] == (
        "evidence/oac-quote-adaptation/latest/manifest.json"
    )
    assert by_id["semifinal.integrated_candidate_closure"]["evidence"] == (
        "evidence/semifinal-closure/latest/evidence-index.json"
    )
    assert by_id["semifinal.quote_value_evidence"]["evidence"] == (
        "evidence/quote-value/latest/evidence-index.json"
    )
    assert by_id["semifinal.historical_agentteams_transport"]["evidence"] == (
        "evidence/agentteams/public/historical-transport-v1.2.2.json"
    )
    assert by_id["semifinal.core_change_advisory_live_agentteams"]["evidence"] == (
        "evidence/agentteams/fresh-live/2026-08-25-561171ed039b/live-receipt.json"
    )
    assert by_id["semifinal.agent_candidate_control_plane_ingestion"]["evidence"] == (
        "evidence/agentteams/fresh-live/2026-08-25-561171ed039b/semantic-ingestion.json"
    )
    assert all(not item["evidence"].startswith(str(tmp_path)) for item in semifinal["checks"])


def test_goai_semifinal_gate_accepts_frozen_public_agentteams_evidence() -> None:
    semifinal = goai_gate.evaluate("semifinal")
    by_id = {item["id"]: item for item in semifinal["checks"]}

    assert by_id["semifinal.historical_agentteams_transport"]["status"] == "PASS"
    assert by_id["semifinal.core_change_advisory_live_agentteams"]["status"] == "PASS"
    assert by_id["semifinal.agent_candidate_control_plane_ingestion"]["status"] == "PASS"
    assert by_id["semifinal.oac_local_runtime_admission"]["status"] == "PASS"
    admission = by_id["semifinal.oac_local_runtime_admission"]
    assert admission["verification_scope"] == "RETAINED_ARTIFACT"
    assert admission["current_release_qualified"] is False
    assert admission["verification"]["policy_sha256"] == goai_gate.RETAINED_OAC_ADMISSION_POLICY_SHA256
    assert admission["verification"]["pack_digest"] == goai_gate.RETAINED_OAC_ADMISSION_PACK_DIGEST
    assert by_id["semifinal.oac_governed_evolution"]["status"] == "PASS"
    assert by_id["semifinal.oac_enterprise_adaptation"]["status"] == "PASS"
    assert by_id["semifinal.quote_value_evidence"]["status"] == "PASS"
    assert by_id["semifinal.integrated_candidate_closure"]["status"] == "PASS"
    retained_closure = goai_gate.verify_integrated_semifinal_closure(ROOT)
    assert retained_closure["verification_scope"] == "RETAINED_ARTIFACT"
    assert retained_closure["current_release_qualified"] is False
    assert goai_gate._quote_value_evidence_valid(ROOT) is True
    evolution = goai_gate.verify_oac_governed_evolution(ROOT)
    assert evolution["status"] == "PASS"
    assert evolution["artifact_count"] == 51
    assert evolution["event_count"] == 13
    assert evolution["independent_evaluator"]["product_imports"] == 0
    assert evolution["dual_wheel"]["modules_loaded_from_wheel"] == [
        "oac",
        "orgrebase",
    ]
    adaptation = goai_gate.verify_oac_enterprise_adaptation(ROOT)
    review_wait_ms = adaptation["real_review_wait_ms"]
    assert isinstance(review_wait_ms, int)
    assert review_wait_ms >= 4000
    assert adaptation == {
        "status": "PASS",
        "verification_scope": "RETAINED_ARTIFACT",
        "current_release_qualified": False,
        "oac_reader": {
            "digest_projection": "LEGACY_TYPED_PROJECTION",
            "wheel_sha256": "aead48d80e921ddd8ea9505e4723d716e35ac351bb4237b7a88ad38a83e7e13b",
            "python": sys.version.split()[0], "pydantic": version("pydantic"), "rfc8785": version("rfc8785"),
            "original_build_environment_reproduced": False,
        },
        "failures": [],
        "evidence_class": "VALIDATED_CONTROLLED_LOCAL",
        "claim_ceiling": "CONTROLLED_LOCAL_ADAPTATION_MECHANISM_ONLY",
        "entry_count": 21,
        "pack_digest": "sha256:d470bf87f4b37754ff7efa4ebdc9294492c38c506108e0012264f694181ab5e8",
        "evergreen_status": "READY_FOR_ORGREBASE",
        "veracier_status": "HOLD",
        "veracier_gap_count": 7,
        "real_review_wait_ms": review_wait_ms,
        "oac_public_cli_checks": 5,
        "mutation_rejections": 6,
        "canonical_target_writes": 0,
        "oac_plan_produced": False,
        "oac_plan_certificate_produced": False,
        "oac_runtime_invoked": False,
        "bound_execution_started": False,
        "real_enterprise_validated": "NOT_RUN",
        "production_ready": False,
        "evidence_path": "evidence/oac-quote-adaptation/latest",
    }
    product_path = goai_gate.verify_product_path_blackbox(ROOT, rebuild_wheel=False, retained_verifiers=True)
    # This test retains the historical artifact claim. Current-source behavior
    # and byte binding are exercised against a newly built HTTP evidence bundle
    # in test_product_path_release_binding.py; the default release gate stays strict.
    assert product_path["status"] == "PASS"
    assert product_path["verification_scope"] == "RETAINED_VERIFIER_BYTES"
    assert product_path["current_release_qualified"] is False
    assert product_path["wheel_rebuild"] == "NOT_RUN"
    assert product_path["evaluator_replay"] == "PASS"
    assert product_path["gate_attack_summary"] == {
        "total": 2,
        "rejected": 2,
        "accepted": 0,
    }
    assert by_id["semifinal.oac_local_runtime_admission"]["evidence"] == (
        "evidence/oac-bridge/latest/evidence-index.json"
    )
    serialized = json.dumps(by_id, ensure_ascii=False)
    assert "evidence/agentteams/live-receipt.json" not in serialized
    assert "evidence/agentteams/candidate-ingestion-receipt.json" not in serialized

    final_by_id = {item["id"]: item for item in goai_gate.evaluate("final")["checks"]}
    assert final_by_id["final.workspace_product_runbook"]["evidence"] == ("docs/WORKSPACE-DEMO-RUNBOOK.md")
    assert final_by_id["final.core_live_agentteams_runbook"]["evidence"] == ("agentteams/LIVE-RUNBOOK.md")


def test_goai_semifinal_gate_blocks_stale_independent_quote_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(goai_gate, "_quote_value_evidence_valid", lambda root: False)

    result = goai_gate.evaluate("semifinal")
    by_id = {item["id"]: item for item in result["checks"]}

    assert result["decision"] == "NO_GO"
    assert "semifinal.quote_value_evidence" in result["blocking_failures"]
    assert by_id["semifinal.quote_value_evidence"] == {
        "id": "semifinal.quote_value_evidence",
        "status": "FAIL",
        "evidence": "evidence/quote-value/latest/evidence-index.json",
    }


def test_goai_semifinal_gate_fails_closed_without_oac_enterprise_adaptation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        goai_gate,
        "_oac_enterprise_adaptation_valid",
        lambda root: False,
    )

    result = goai_gate.evaluate("semifinal")
    by_id = {item["id"]: item for item in result["checks"]}

    assert result["decision"] == "NO_GO"
    assert "semifinal.oac_enterprise_adaptation" in result["blocking_failures"]
    assert by_id["semifinal.oac_enterprise_adaptation"] == {
        "id": "semifinal.oac_enterprise_adaptation",
        "status": "FAIL",
        "evidence": "evidence/oac-quote-adaptation/latest/manifest.json",
    }


def test_enterprise_release_facts_are_recomputed_from_admission_receipts() -> None:
    facts = generate_release_facts.build_enterprise_seed_facts()

    assert facts["status"] == "PASS"
    assert facts["current_claim_ceiling"] == "REFERENCE_PROFILE_SOURCE_BOUND"
    assert facts["profile_count"] == 2
    assert facts["synthetic_profile_count"] == facts["profile_count"]
    assert facts["fixed_exact_source_component_root_count"] == 10
    assert facts["admitted_source_component_root_count"] == facts["fixed_exact_source_component_root_count"]
    assert facts["authority_assurance"] == "DECLARED_NOT_AUTHENTICATED"
    assert facts["canonical_target_writes"] == 0
    assert facts["runtime_projection_match"] == {"matched": 5, "total": 5}

    by_ref = {item["profile_ref"]: item for item in facts["profiles"]}
    northstar = by_ref["profile:northstar-acme-quote@r1"]
    veracier = by_ref["profile:veracier-supplier-shadow@r1"]
    assert northstar["reference_runtime_compatible"] is True
    assert northstar["runtime_projection"]["matched"] == northstar["runtime_projection"]["total"]
    assert veracier["intake_only"] is True
    assert veracier["reference_runtime_compatible"] is False
    assert veracier["runtime_projection"] is None
    assert facts["admitted_source_component_root_count"] == sum(
        item["source_admission"]["fixed_exact_source_component_roots"] for item in facts["profiles"]
    )
    assert facts["canonical_target_writes"] == sum(
        item["canonical_target_writes"] for item in facts["profiles"]
    )
    assert {
        observation["locator"]
        for item in facts["profiles"]
        for observation in item["source_admission"]["root_observations"]
    } == set(generate_release_facts.exact_locator_assets())


def test_enterprise_release_facts_v9_preserves_legacy_sections() -> None:
    facts = generate_release_facts.build_facts(root=ROOT)

    assert facts["schema_version"] == "orgrebase.release-facts.v9"
    assert facts["workspace"]["fact_role"] == "HISTORICAL_COMPATIBILITY_BASELINE"
    assert facts["workspace"]["current_semifinal_delivery"] is False
    assert facts["product_identity"] == {
        "category": "ENTERPRISE_AGENT_SUBSTRATE",
        "definition_zh": "基于 OAC 的组织工作编译与持续演化基座",
        "oac_role": "INDEPENDENT_CANDIDATE_STANDARD_AND_ACCEPTANCE_RELATION",
        "oac_is_agent": False,
        "oac_is_runtime": False,
        "reference_runtime_capability": "ENTERPRISE_WORK_CONTINUOUS_EVOLUTION_ENGINE",
        "authority_roots": ["SOURCE", "DEMAND", "PLAN", "OUTCOME", "EVOLUTION"],
        "execution_role": "EVIDENCED_RUNTIME_STATE_NOT_AUTHORITY_ROOT",
    }
    assert facts["innovation_hypothesis"]["claim_type"] == ("COMBINATION_HYPOTHESIS_NOT_WORLD_FIRST_CLAIM")
    innovation_by_id = {item["id"]: item["status"] for item in facts["innovation_hypothesis"]["dimensions"]}
    assert innovation_by_id["PROOF_CARRYING_GOVERNED_EVOLUTION"] == (
        "BOUNDED_SYNTHETIC_CONTROLLED_REFERENCE_MVP"
    )
    evolution = facts["oac_governed_evolution"]
    assert evolution["status"] == "PASS"
    assert evolution["claim_ceiling"] == ("ONE_GOVERNED_EVOLUTION_REFERENCE_MVP_WITH_PRELIMINARY_REGRESSION")
    assert evolution["artifact_count"] == 51
    assert evolution["event_count"] == 13
    assert evolution["plan_count"] == evolution["distinct_topology_count"] == 2
    assert evolution["runs"]["split-rejected"] == {
        "plan_status": "ACCEPT",
        "execution_status": "COMPLETED",
        "outcome_verdict": "REJECT",
    }
    assert evolution["preliminary_regression"]["status"] == "PASS"
    assert evolution["dual_wheel"]["status"] == "PASS"
    assert evolution["boundaries"]["human_review"] == "NOT_RUN"
    assert evolution["boundaries"]["real_enterprise"] == "NOT_RUN"
    assert evolution["boundaries"]["production_ready"] is False
    assert facts["deployment_readiness"] == {
        "competition_technical_mvp": "GO",
        "isolated_read_only_single_enterprise_shadow_pilot": "CONDITIONAL_GO",
        "arbitrary_enterprise_production": "NO_GO",
    }
    assert facts["enterprise_seed_intake"] == (generate_release_facts.build_enterprise_seed_facts())
    product_path = facts["product_path_blackbox"]
    assert product_path["benchmark_version"] == (
        "ProductPath-v0.3-task-intake-bound"
    )
    assert product_path["case_count"] == product_path["cases_passed"] == 12
    assert product_path["mutation_count"] == product_path["mutations_killed"] == 11
    assert product_path["integrity_attacks"] == product_path[
        "integrity_attacks_rejected"
    ] == 5
    assert product_path["gate_attacks"] == product_path["gate_attacks_rejected"] == 2
    assert product_path["uvicorn_processes_started"] == 15
    assert product_path["real_process_restarts"] == 3
    assert product_path["pp001_event_count"] == 12
    assert product_path["task_intake_status"] == "FORMATION_COMPLETED"
    assert product_path["task_intake_same_run_id"] is True
    assert product_path["task_intake_same_workspace_nonce"] is True
    assert product_path["task_intake_natural_language_authority"] is False
    assert product_path["review_duration_ms"] == 4000
    assert product_path["review_gate_store"] == "FILE_BACKED_SQLITE_ARTIFACT"
    assert product_path["identity_mode"] == "CONTROLLED_LOCAL_HEADER_IDENTITY"
    assert product_path["external_iam"] == "NOT_RUN"
    assert product_path["direct_form_status"] == 410
    assert product_path["direct_form_code"] == "WORKSPACE_TASK_INTAKE_REQUIRED"
    assert product_path["task_intake_binding_scope"]
    assert product_path["approval_review_gate_scope"]
    assert product_path["source_bound_oracle"] == "PASS"
    assert product_path["source_component_roots"] == 5
    assert product_path["source_runtime_projection"] == {
        "matched": 5,
        "total": 5,
    }
    assert product_path["source_canonical_target_writes"] == 0
    assert product_path["benchmark_migration"] == "PASS"
    closure = facts["semifinal_integrated_candidate_closure"]
    assert closure["status"] == "PASS"
    assert closure["fact_role"] == "HISTORICAL_COMPATIBILITY_BASELINE"
    assert closure["current_release_qualified"] is False
    assert closure["current_release_status"] == "NOT_QUALIFIED_BY_RETAINED_EVIDENCE"
    assert closure["current_build_binding"]["status"] == "MISMATCH"
    assert closure["evidence_class"] == (
        "CONTROLLED_LOCAL_INTEGRATED_VERTICAL_SLICE"
    )
    assert closure["terminal_state"] == "CANDIDATE_ACCEPTED"
    assert closure["canonical_target_writes"] == 0
    assert closure["business_value_same_run"] is False
    assert closure["production_readiness"] is False
    assert closure["verification_mode"] == "EXTERNAL_POST_WHEEL_PARENT_VERIFIER"
    assert closure["artifact_digests"] == (
        "OMITTED_TO_AVOID_RELEASE_FACTS_WHEEL_SELF_REFERENCE"
    )
    assert closure["completion_matrix"]["workspace_approval_apply_same_run"] == (
        "NOT_RUN"
    )
    assert facts["code_release_gate"]["checks"][
        "semifinal_integrated_candidate_closure"
    ] == "PASS"
    assert facts["code_release_gate"]["status"] == "HISTORICAL_PASS_CURRENT_BUILD_UNQUALIFIED"
    assert facts["code_release_gate"]["current_release_qualified"] is False
    assert facts["code_release_gate"]["current_release_status"] == "NOT_QUALIFIED_BY_RETAINED_EVIDENCE"
    assert {
        "current_semifinal_delivery",
        "workspace",
        "oac_local_runtime_admission",
        "oac_governed_evolution",
        "product_path_blackbox",
        "semifinal_integrated_candidate_closure",
        "local_control_plane",
        "agent_orchestration",
        "skill_governance",
        "historical_agentteams_transport",
        "core_change_advisory_live_agentteams",
        "candidate_control_plane",
        "code_release_gate",
        "claim_boundaries",
    }.issubset(facts)


def test_current_semifinal_delivery_is_derived_from_current_machine_evidence() -> None:
    current = generate_release_facts.build_current_semifinal_delivery(root=ROOT)

    assert current["status"] == "PASS"
    assert current["fact_role"] == "CURRENT_SEMIFINAL_DELIVERY"
    golden = current["golden_quote_delivery"]
    assert golden["quote"]["version"] == "v3"
    assert golden["quote"]["launch_date"] == "2026-10-15"
    assert golden["quote"]["currency"] == "EUR"
    assert golden["agentteams"] == {
        "status": "PASS",
        "action_count": 40,
        "task_binding_count": 7,
        "independent_domain_worker_processes": 5,
        "independent_reviewer_processes": 2,
    }
    assert golden["human_owner_gates"]["status"] == "PASS"
    assert golden["human_owner_gates"]["count"] == 2
    assert [gate["change_kind"] for gate in golden["human_owner_gates"]["gates"]] == [
        "launch_date",
        "currency",
    ]
    assert golden["same_run_agentteams_skill_tool"]["status"] == "PASS"
    assert golden["oac_bound_task_formation"] == {
        "status": "PASS",
        "evidence_role": "GOLDEN_SAME_RUN_OAC_BOUND_TASK_FORMATION",
        "run_id": golden["run_id"],
        "failures": [],
        "task_intake_status": "FORMATION_COMPLETED",
        "activation_status": "CONSUMED_BY_QUOTE_FORMATION",
        "activation_binding_digest": (
            "sha256:f0152b954b431d5a82c83b324f9aba7689f12bd04cad4763db453a21d43baf57"
        ),
        "task_formation_decision_receipt_digest": (
            "sha256:5d3dd72941aa39b26a226c992f56bf3c5f8543aa5a43ed38b9879ef85bdbf488"
        ),
        "context_envelope_digest": (
            "sha256:a9815a00bb5e32fd776f79bc06afffc1c29c8a4f6ee686bf0a96384cf158e5fa"
        ),
        "agentteams_execution_plan_digest": (
            "sha256:e424a4d369c48c2bec8a305830f8e1050a878548cfa30efaea064672bcb1eec3"
        ),
        "planned_domain_ids": ["finance", "gtm", "legal", "product"],
        "actual_agentteams_domain_ids": ["finance", "gtm", "legal", "product"],
        "topology_match": True,
        "candidate_only": True,
        "canonical_target_writes": 0,
        "claim_boundary": (
            "OAC_BINDING_CONSUMED_NOT_BUSINESS_APPROVAL_OR_PRODUCTION_PROOF"
        ),
    }
    assert golden["experience_governance"]["status"] == "APPROVED_CANARY"
    assert golden["experience_governance"]["maturity"] == "SINGLE_RUN_SEED"
    assert golden["candidate_layer_canonical_target_writes"] == 0
    assert "oac_bound_shadow_validation" not in current
    assert current["dynamic_formation_validation"]["status"] == "PASS"
    assert current["dynamic_formation_validation"]["canonical_target_writes"] == 0
    assert current["claim_boundary"]["production_ready"] is False
    assert current["claim_boundary"][
        "golden_and_dynamic_formation_are_independent_runs"
    ] is True
    release_facts_source = (ROOT / "scripts/generate_release_facts.py").read_text(
        encoding="utf-8"
    )
    assert "evidence/oac-bound-shadow" not in release_facts_source


def test_current_semifinal_delivery_downgrades_when_evidence_is_missing(tmp_path: Path) -> None:
    current = generate_release_facts.build_current_semifinal_delivery(root=tmp_path)

    assert current["status"] == "INCOMPLETE"
    assert current["golden_quote_delivery"]["status"] == "INCOMPLETE"
    assert current["golden_quote_delivery"]["oac_bound_task_formation"]["status"] == (
        "INCOMPLETE"
    )
    assert current["dynamic_formation_validation"]["status"] == "INCOMPLETE"
    assert all(
        failure.startswith("MISSING_EVIDENCE:")
        for failure in current["golden_quote_delivery"]["failures"]
    )


@pytest.mark.parametrize(
    ("mutation", "expected_failure"),
    (
        ("run", "GOLDEN_OAC_SAME_RUN_INVALID"),
        ("binding", "GOLDEN_OAC_ACTIVATION_BINDING_INVALID"),
        ("topology", "GOLDEN_OAC_TOPOLOGY_INVALID"),
        ("task_binding", "GOLDEN_OAC_FORMATION_LINEAGE_INVALID"),
    ),
)
def test_current_semifinal_delivery_fails_closed_on_oac_lineage_tamper(
    tmp_path: Path,
    mutation: str,
    expected_failure: str,
) -> None:
    relative_paths = (
        "evidence/golden-competition/latest/pilot/verification.json",
        "evidence/golden-competition/latest/pilot/golden-run/summary.json",
        "evidence/golden-competition/latest/pilot/state.json",
        "evidence/golden-competition/latest/pilot/evidence-export.json",
        "evidence/formation-taskflow/latest/verification.json",
        "evidence/formation-taskflow/latest/probe-receipt.json",
    )
    for relative in relative_paths:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    baseline = generate_release_facts.build_current_semifinal_delivery(root=tmp_path)
    assert baseline["status"] == "PASS"
    assert "oac_bound_shadow_validation" not in baseline

    export_path = tmp_path / "evidence/golden-competition/latest/pilot/evidence-export.json"
    evidence_export = json.loads(export_path.read_text(encoding="utf-8"))
    if mutation == "run":
        evidence_export["oac_activation_consumption"]["execution_run_id"] = (
            "run:tampered"
        )
    elif mutation == "binding":
        evidence_export["task_intake"]["oac_activation_binding_digest"] = (
            f"sha256:{'0' * 64}"
        )
    elif mutation == "topology":
        evidence_export["competition_evidence"]["oac_agentteams_lineage"][
            "actual_agentteams_domain_ids"
        ] = ["finance", "legal", "product"]
    else:
        summary_path = (
            tmp_path
            / "evidence/golden-competition/latest/pilot/golden-run/summary.json"
        )
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["task_bindings"][0]["context_envelope_digest"] = (
            f"sha256:{'0' * 64}"
        )
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
    export_path.write_text(json.dumps(evidence_export), encoding="utf-8")

    current = generate_release_facts.build_current_semifinal_delivery(root=tmp_path)

    assert current["status"] == "FAIL"
    oac = current["golden_quote_delivery"]["oac_bound_task_formation"]
    assert oac["status"] == "FAIL"
    assert oac["failures"] == [expected_failure]
    assert "oac_bound_shadow_validation" not in current


def test_oac_evolution_gate_rejects_rehashed_outcome_tamper(tmp_path: Path) -> None:
    shutil.copytree(
        ROOT / "evidence" / "oac-evolution",
        tmp_path / "evidence" / "oac-evolution",
    )
    (tmp_path / "scripts").mkdir()
    shutil.copy2(
        ROOT / "scripts" / "verify_oac_evolution_evidence.py",
        tmp_path / "scripts" / "verify_oac_evolution_evidence.py",
    )
    evidence = tmp_path / "evidence" / "oac-evolution" / "latest"
    decision_path = evidence / "runs" / "split-accepted" / "outcome-decision.json"
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    decision["verdict"] = "REJECT"
    projection = {key: value for key, value in decision.items() if key != "digest"}
    encoded_projection = json.dumps(
        projection,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    decision["digest"] = "sha256:" + hashlib.sha256(encoded_projection).hexdigest()
    decision_path.write_text(
        json.dumps(decision, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    index_path = evidence / "evidence-index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    artifact_ref = "runs/split-accepted/outcome-decision.json"
    entry = next(item for item in index["entries"] if item["artifact_ref"] == artifact_ref)
    entry["sha256"] = "sha256:" + hashlib.sha256(decision_path.read_bytes()).hexdigest()
    encoded_entries = json.dumps(
        index["entries"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    index["pack_digest"] = "sha256:" + hashlib.sha256(encoded_entries).hexdigest()
    index_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    result = goai_gate.verify_oac_governed_evolution(tmp_path)
    assert result["status"] == "FAIL"
    assert "CANONICAL_EVALUATION_FAILED" in result["failures"]


def test_enterprise_release_facts_fail_closed_on_locator_registry_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = generate_release_facts.exact_locator_assets()
    monkeypatch.setattr(
        generate_release_facts,
        "exact_locator_assets",
        lambda: {**current, "fixture://unexpected/domain@r1": "unexpected.json"},
    )

    with pytest.raises(
        RuntimeError,
        match="RELEASE_FACTS_ENTERPRISE_SOURCE_LOCATOR_DRIFT",
    ):
        generate_release_facts.build_enterprise_seed_facts()


def test_enterprise_release_facts_fail_closed_on_receipt_binding_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admit = generate_release_facts.admit_enterprise_seed_profile

    def tampered_admission(profile: object, *, source_admission: object) -> object:
        receipt = admit(profile, source_admission=source_admission)
        return receipt.model_copy(update={"source_admission_receipt_digest": f"sha256:{'0' * 64}"})

    monkeypatch.setattr(
        generate_release_facts,
        "admit_enterprise_seed_profile",
        tampered_admission,
    )

    with pytest.raises(
        RuntimeError,
        match="RELEASE_FACTS_ENTERPRISE_PROFILE_SOURCE_RECEIPT_DRIFT",
    ):
        generate_release_facts.build_enterprise_seed_facts()


def test_evidence_manifest_recomputes_artifact_digest(tmp_path: Path) -> None:
    payload = {"result": "original"}
    artifact = tmp_path / "result.json"
    artifact.write_text(json.dumps(payload), encoding="utf-8")
    manifest = {
        "schema_version": "orgrebase.evidence-manifest.v2",
        "workflow_run_id": "run:test@1",
        "run_nonce": "a" * 64,
        "digest_algorithm": "orgrebase-canonical-json-sha256-v1",
        "artifacts": {
            "result.json": {
                "path": "result.json",
                "digest": sha256_digest(payload),
                "status": "IMPLEMENTED",
                "evidence_class": "LOCAL_DETERMINISTIC",
            }
        },
        "capabilities": {
            "result": {
                "status": "IMPLEMENTED",
                "evidence_class": "LOCAL_DETERMINISTIC",
                "artifact": "result.json",
            }
        },
        "verifier": "test",
        "limitations": ["fixture"],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert verify_evidence_manifest.verify(manifest_path)["status"] == "PASS"
    artifact.write_text(json.dumps({"result": "tampered"}), encoding="utf-8")
    with pytest.raises(verify_evidence_manifest.ManifestError, match="DIGEST_MISMATCH"):
        verify_evidence_manifest.verify(manifest_path)


@pytest.mark.parametrize("change", ["missing", "changed", "production", "symlink"])
def test_retained_oac_admission_requires_exact_fixed_policy(tmp_path: Path, change: str) -> None:
    evidence = tmp_path / "evidence/oac-bridge/latest"
    shutil.copytree(ROOT / "evidence/oac-bridge/latest", evidence)
    policy = evidence / "policy.json"
    if change == "missing":
        policy.unlink()
    elif change == "changed":
        policy.write_bytes(policy.read_bytes() + b"\n")
    elif change == "production":
        shutil.copyfile(ROOT / "configs/oac/runtime-admission-policy.json", policy)
    else:
        policy.unlink()
        policy.symlink_to(ROOT / "evidence/oac-bridge/latest/policy.json")
    result = goai_gate.verify_retained_oac_runtime_admission(tmp_path)
    assert result["status"] == "FAIL"
    assert result["verification_scope"] == "RETAINED_ARTIFACT"
    assert result["current_release_qualified"] is False
    assert result["failures"] == ["OAC_RETAINED_POLICY_UNAVAILABLE" if change in {"missing", "symlink"}
                                  else "OAC_RETAINED_POLICY_IDENTITY_MISMATCH"]


@pytest.mark.parametrize("artifact", ["cases/BASE/capsule.json", "cases/BASE/approval.json", "event-chain.json"])
def test_retained_oac_admission_still_replays_exact_artifacts(tmp_path: Path, artifact: str) -> None:
    evidence = tmp_path / "evidence/oac-bridge/latest"
    shutil.copytree(ROOT / "evidence/oac-bridge/latest", evidence)
    wheel = Path("evidence/oac-evolution/wheel-check/wheels") / goai_gate.RETAINED_OAC_ADMISSION_WHEEL
    (tmp_path / wheel).parent.mkdir(parents=True)
    shutil.copyfile(ROOT / wheel, tmp_path / wheel)
    path = evidence / artifact
    path.write_bytes(path.read_bytes() + b"\n")
    result = goai_gate.verify_retained_oac_runtime_admission(tmp_path)
    assert result["status"] == "FAIL"
    assert result["current_release_qualified"] is False
    assert result["failures"] == [f"RETAINED_OAC_ADMISSION_REPLAY_FAILED:OAC_BRIDGE_EVIDENCE_FILE_INVALID:{artifact}"]
    index_path = evidence / "evidence-index.json"
    index = json.loads(index_path.read_text())
    for entry in index["entries"]:
        if entry["artifact_ref"] == artifact:
            entry["sha256"] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    index["pack_digest"] = sha256_digest(index["entries"])
    index_path.write_text(json.dumps(index))
    resealed = goai_gate.verify_retained_oac_runtime_admission(tmp_path)
    assert resealed["status"] == "FAIL"
    assert resealed["failures"] == ["OAC_RETAINED_PACK_IDENTITY_MISMATCH"]


@pytest.mark.parametrize("case", ["BASE", "SPLIT"])
def test_current_production_oac_policy_rejects_retained_capsule(case: str) -> None:
    from orgrebase.domain import IntegrityError
    from orgrebase.workspace.models import OACRuntimeCapsule
    from orgrebase.workspace.oac_bridge import (
        OACRuntimeAdmissionVerifier,
        load_runtime_policy,
        verify_oac_bridge_evidence,
    )

    evidence = ROOT / "evidence/oac-bridge/latest"
    current_path = ROOT / "configs/oac/runtime-admission-policy.json"
    policy_bytes = current_path.read_bytes()
    policy, digest = load_runtime_policy(current_path)
    capsule = OACRuntimeCapsule.model_validate_json((evidence / "cases" / case / "capsule.json").read_bytes())
    # This legacy change was sealed after filling defaults; raw admission rejects it first.
    with pytest.raises(IntegrityError, match=r"^OAC_DIGEST_MISMATCH:SemanticChangeSet$"):
        OACRuntimeAdmissionVerifier(policy, digest).verify(capsule)
    with pytest.raises(IntegrityError, match="OAC_BRIDGE_EXPORTED_POLICY_MISMATCH"):
        verify_oac_bridge_evidence(evidence, policy_path=current_path)
    retained = goai_gate.verify_retained_oac_runtime_admission(ROOT)
    assert retained["status"] == "PASS" and retained["current_release_qualified"] is False
    assert retained["reader"]["wheel_sha256"] == goai_gate.RETAINED_OAC_ADMISSION_WHEEL_SHA256
    assert retained["reader"]["python"] == sys.version.split()[0]
    assert retained["reader"]["original_environment_reproduced"] is False
    assert retained["reader"]["environment_dependencies"]["pydantic"] == version("pydantic")
    assert retained["reader"]["module_origins"]["orgrebase.workspace.oac_bridge"] == "orgrebase/workspace/oac_bridge.py"
    assert current_path.read_bytes() == policy_bytes


@pytest.mark.parametrize("change", ["missing", "changed"])
def test_retained_oac_admission_never_falls_back_when_original_reader_unavailable(tmp_path: Path, change: str) -> None:
    evidence = tmp_path / "evidence/oac-bridge/latest"
    shutil.copytree(ROOT / "evidence/oac-bridge/latest", evidence)
    if change == "changed":
        wheel = tmp_path / "evidence/oac-evolution/wheel-check/wheels" / goai_gate.RETAINED_OAC_ADMISSION_WHEEL
        wheel.parent.mkdir(parents=True)
        wheel.write_bytes(b"not-the-retained-wheel")
    result = goai_gate.verify_retained_oac_runtime_admission(tmp_path)
    assert result["status"] == "FAIL" and result["current_release_qualified"] is False
    assert result["failures"] == ["RETAINED_OAC_ADMISSION_WHEEL_UNAVAILABLE_OR_CHANGED"]


def test_retained_oac_admission_rechecks_the_exact_extracted_wheel_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    shutil.copytree(ROOT / "evidence/oac-bridge/latest", tmp_path / "evidence/oac-bridge/latest")
    wheel = tmp_path / "evidence/oac-evolution/wheel-check/wheels" / goai_gate.RETAINED_OAC_ADMISSION_WHEEL
    wheel.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "evidence/oac-evolution/wheel-check/wheels" / wheel.name, wheel)
    marker = tmp_path / "unexpected-reader-executed"
    replacement = io.BytesIO()
    with zipfile.ZipFile(wheel) as original, zipfile.ZipFile(replacement, "w") as changed:
        for member in original.infolist():
            content = original.read(member)
            if member.filename == "orgrebase/__init__.py":
                content += f"\nfrom pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n".encode()
            changed.writestr(member, content)
    validate = goai_gate._retained_wheel_valid

    def replace_after_validation(root: Path, record: dict[str, str]) -> bool:
        assert validate(root, record) is True
        wheel.write_bytes(replacement.getvalue())
        return True

    monkeypatch.setattr(goai_gate, "_retained_wheel_valid", replace_after_validation)
    result = goai_gate.verify_retained_oac_runtime_admission(tmp_path)
    assert not marker.exists()
    assert result["status"] == "FAIL"
    assert result["current_release_qualified"] is False
    assert result["failures"] == ["RETAINED_OAC_ADMISSION_WHEEL_UNAVAILABLE_OR_CHANGED"]


@pytest.mark.parametrize("artifact", ["policy.json", "evidence-index.json"])
def test_retained_oac_admission_verifies_snapshot_identity_after_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, artifact: str,
) -> None:
    evidence = tmp_path / "evidence/oac-bridge/latest"
    shutil.copytree(ROOT / "evidence/oac-bridge/latest", evidence)
    wheel = tmp_path / "evidence/oac-evolution/wheel-check/wheels" / goai_gate.RETAINED_OAC_ADMISSION_WHEEL
    wheel.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "evidence/oac-evolution/wheel-check/wheels" / wheel.name, wheel)
    validate = goai_gate._retained_wheel_valid

    def change_after_identity_check(root: Path, record: dict[str, str]) -> bool:
        assert validate(root, record) is True
        path = evidence / artifact
        if artifact == "policy.json":
            path.write_bytes(path.read_bytes() + b"\n")
        else:
            index = json.loads(path.read_bytes())
            index["pack_digest"] = "sha256:" + "0" * 64
            path.write_text(json.dumps(index))
        return True

    monkeypatch.setattr(goai_gate, "_retained_wheel_valid", change_after_identity_check)
    result = goai_gate.verify_retained_oac_runtime_admission(tmp_path)
    assert result["status"] == "FAIL"
    assert result["current_release_qualified"] is False
    assert result["failures"] == ["OAC_RETAINED_POLICY_IDENTITY_MISMATCH" if artifact == "policy.json"
                                  else "OAC_RETAINED_PACK_IDENTITY_MISMATCH"]


def test_retained_oac_admission_snapshot_preserves_original_extra_json_rejection(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence/oac-bridge/latest"
    shutil.copytree(ROOT / "evidence/oac-bridge/latest", evidence)
    wheel = tmp_path / "evidence/oac-evolution/wheel-check/wheels" / goai_gate.RETAINED_OAC_ADMISSION_WHEEL
    wheel.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "evidence/oac-evolution/wheel-check/wheels" / wheel.name, wheel)
    (evidence / "unexpected.json").write_text("{}")
    result = goai_gate.verify_retained_oac_runtime_admission(tmp_path)
    assert result["status"] == "FAIL"
    assert result["failures"] == ["RETAINED_OAC_ADMISSION_REPLAY_FAILED:OAC_BRIDGE_EVIDENCE_PATH_SET_INVALID"]
