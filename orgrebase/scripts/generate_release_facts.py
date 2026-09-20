"""Generate the single machine-readable fact surface used by docs and demos."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

from orgrebase import __version__
from orgrebase.goai_agentteams import load_agentteams_evidence
from orgrebase.workspace.profile_admission import admit_enterprise_seed_profile
from orgrebase.workspace.profile_contracts import (
    RuntimeCompatibilityMode,
    SeedComponentKind,
)
from orgrebase.workspace.reference_profiles import (
    northstar_acme_quote_profile,
    supplier_shadow_intake_profile,
)
from orgrebase.workspace.source_admission import (
    admit_enterprise_seed_sources,
    exact_locator_assets,
    verify_reference_runtime_projections,
)

ROOT = Path(__file__).resolve().parents[1]
SEMIFINAL_COMPLETION_MATRIX = {
    "enterprise_quote_operating_model": "VALIDATED_SYNTHETIC_AND_MODELLED",
    "enterprise_shadow_observation_contract": "VALIDATED_SYNTHETIC_CONTRACT",
    "agentteams_native_taskflow": "VALIDATED_CONTROLLED_LOCAL",
    "four_domain_coalition_binding": "VALIDATED_CONTROLLED_LOCAL",
    "skill_package_lifecycle": "VALIDATED_RUN_LOCAL",
    "source_tool_otlp_operations": "VALIDATED_CONTROLLED_LOCAL",
    "workspace_approval_apply_same_run": "NOT_RUN",
    "live_distributed_agentteams": "NOT_RUN",
    "real_enterprise_connectors": "NOT_RUN",
    "real_enterprise_value": "NOT_RUN",
    "production_ha_dr_sla": "NOT_RUN",
}


def _load_goai_gate() -> ModuleType:
    path = Path(__file__).with_name("goai_gate.py")
    spec = importlib.util.spec_from_file_location("orgrebase_release_goai_gate", path)
    if spec is None or spec.loader is None:  # pragma: no cover - source invariant
        raise RuntimeError("GOAI gate verifier is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _optional_machine_object(
    root: Path,
    relative_path: str,
) -> tuple[dict[str, Any] | None, str | None]:
    path = root / relative_path
    if not path.is_file():
        return None, f"MISSING_EVIDENCE:{relative_path}"
    try:
        return _object(path), None
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return None, f"INVALID_EVIDENCE:{relative_path}:{type(exc).__name__}"


def _derived_status(failures: list[str]) -> str:
    if not failures:
        return "PASS"
    if any(item.startswith("MISSING_EVIDENCE:") for item in failures):
        return "INCOMPLETE"
    return "FAIL"


def _build_current_golden_delivery(root: Path) -> dict[str, Any]:
    paths = {
        "verification": "evidence/golden-competition/latest/pilot/verification.json",
        "summary": "evidence/golden-competition/latest/pilot/golden-run/summary.json",
        "state": "evidence/golden-competition/latest/pilot/state.json",
        "evidence_export": "evidence/golden-competition/latest/pilot/evidence-export.json",
    }
    loaded = {name: _optional_machine_object(root, path) for name, path in paths.items()}
    failures = [error for _, error in loaded.values() if error is not None]
    verification = loaded["verification"][0] or {}
    summary = loaded["summary"][0] or {}
    state = loaded["state"][0] or {}
    evidence_export = loaded["evidence_export"][0] or {}

    quote = state.get("quote") if isinstance(state.get("quote"), dict) else {}
    quote_payload = quote.get("payload") if isinstance(quote.get("payload"), dict) else {}
    bindings = summary.get("task_bindings")
    bindings = bindings if isinstance(bindings, list) else []
    changes = state.get("changes")
    changes = changes if isinstance(changes, dict) else {}
    owner_gates: list[dict[str, Any]] = []
    change_order = [kind for kind in ("launch_date", "currency") if kind in changes]
    change_order.extend(sorted(set(changes) - set(change_order)))
    for change_kind in change_order:
        change = changes.get(change_kind)
        approval_record = change.get("approval") if isinstance(change, dict) else None
        if not isinstance(approval_record, dict):
            continue
        approval = approval_record.get("approval")
        binding = approval_record.get("binding")
        review = approval_record.get("approval_review_evidence")
        if not all(isinstance(item, dict) for item in (approval, binding, review)):
            continue
        owner_gates.append(
            {
                "change_kind": change_kind,
                "owner_id": approval.get("actor_id"),
                "run_id": binding.get("workflow_run_id"),
                "review_duration_ms": review.get("review_duration_ms"),
                "review_wait_satisfied": review.get("review_wait_satisfied"),
                "approval_digest": approval.get("digest"),
                "binding_digest": binding.get("digest"),
            }
        )

    experience = state.get("experience_governance")
    experience = experience if isinstance(experience, dict) else {}
    experience_evaluation = experience.get("evaluation")
    experience_evaluation = experience_evaluation if isinstance(experience_evaluation, dict) else {}
    experience_cases = experience_evaluation.get("case_results")
    experience_cases = experience_cases if isinstance(experience_cases, list) else []
    experience_decision = experience.get("decision")
    experience_decision = experience_decision if isinstance(experience_decision, dict) else {}
    experience_release = experience.get("release")
    experience_release = experience_release if isinstance(experience_release, dict) else {}
    experience_candidate = experience.get("candidate")
    experience_candidate = experience_candidate if isinstance(experience_candidate, dict) else {}

    exported_task_intake = evidence_export.get("task_intake")
    exported_task_intake = (
        exported_task_intake if isinstance(exported_task_intake, dict) else {}
    )
    exported_oac_activation = evidence_export.get("oac_activation_consumption")
    exported_oac_activation = (
        exported_oac_activation if isinstance(exported_oac_activation, dict) else {}
    )
    exported_competition = evidence_export.get("competition_evidence")
    exported_competition = (
        exported_competition if isinstance(exported_competition, dict) else {}
    )
    exported_collaboration = exported_competition.get("agent_collaboration")
    exported_collaboration = (
        exported_collaboration if isinstance(exported_collaboration, dict) else {}
    )
    exported_oac_formation = exported_collaboration.get("oac_task_formation")
    exported_oac_formation = (
        exported_oac_formation if isinstance(exported_oac_formation, dict) else {}
    )
    exported_oac_lineage = exported_competition.get("oac_agentteams_lineage")
    exported_oac_lineage = (
        exported_oac_lineage if isinstance(exported_oac_lineage, dict) else {}
    )

    required_run_ids = (
        verification.get("run_id"),
        summary.get("run_id"),
        (state.get("competition_evidence") or {}).get("run_id")
        if isinstance(state.get("competition_evidence"), dict)
        else None,
        experience.get("run_id"),
        exported_competition.get("run_id"),
        *(item.get("run_id") for item in bindings if isinstance(item, dict)),
        *(item.get("run_id") for item in owner_gates),
    )
    run_ids = {
        value for value in required_run_ids if isinstance(value, str) and value
    }
    tool_digest = summary.get("tool_receipt_digest")
    skill_digest = summary.get("skill_invocation_receipt_digest")
    same_run_pass = (
        all(isinstance(value, str) and value for value in required_run_ids)
        and len(run_ids) == 1
        and isinstance(tool_digest, str)
        and tool_digest.startswith("sha256:")
        and isinstance(skill_digest, str)
        and skill_digest.startswith("sha256:")
    )
    golden_run_id = next(iter(run_ids)) if len(run_ids) == 1 else None

    def is_digest(value: object) -> bool:
        return (
            isinstance(value, str)
            and value.startswith("sha256:")
            and len(value) == len("sha256:") + 64
        )

    task_intake_formation_digest = exported_task_intake.get("formation_receipt_digest")
    activation_formation_digest = exported_oac_activation.get("formation_receipt_digest")
    task_intake_binding_digest = exported_task_intake.get(
        "oac_activation_binding_digest"
    )
    activation_binding_digest = exported_oac_activation.get(
        "activation_binding_digest"
    )
    formation_decision_digest = exported_oac_formation.get(
        "task_formation_decision_receipt_digest"
    )
    context_envelope_digest = exported_oac_formation.get("context_envelope_digest")
    execution_plan_digest = exported_oac_formation.get(
        "agentteams_execution_plan_digest"
    )
    planned_domains = exported_oac_lineage.get("planned_domain_ids")
    actual_domains = exported_oac_lineage.get("actual_agentteams_domain_ids")
    selected_domains = exported_oac_formation.get("selected_domain_ids")
    domain_bindings = exported_oac_formation.get("domain_bindings")
    domain_bindings = domain_bindings if isinstance(domain_bindings, list) else []
    bound_domains = [
        item.get("domain_id") for item in domain_bindings if isinstance(item, dict)
    ]
    plan_tasks = exported_oac_formation.get("plan_tasks")
    plan_tasks = plan_tasks if isinstance(plan_tasks, list) else []
    planned_task_domains = [
        item.get("domain_id")
        for item in plan_tasks
        if isinstance(item, dict) and item.get("task_kind") == "DOMAIN"
    ]

    oac_checks = {
        "oac_task_intake": (
            exported_task_intake.get("status") == "FORMATION_COMPLETED"
            and exported_task_intake.get("intake_persisted") is True
            and exported_task_intake.get("intake_canonical_target_writes") == 0
        ),
        "oac_activation_consumption": (
            exported_oac_activation.get("status")
            == "CONSUMED_BY_QUOTE_FORMATION"
            and exported_oac_activation.get("canonical_target_writes") == 0
        ),
        "oac_same_run": (
            isinstance(golden_run_id, str)
            and exported_task_intake.get("run_id") == golden_run_id
            and exported_oac_activation.get("execution_run_id") == golden_run_id
        ),
        "oac_activation_binding": (
            is_digest(activation_binding_digest)
            and activation_binding_digest == task_intake_binding_digest
            and activation_binding_digest
            == exported_oac_lineage.get("activation_binding_digest")
            and is_digest(task_intake_formation_digest)
            and task_intake_formation_digest == activation_formation_digest
        ),
        "oac_formation_lineage": (
            is_digest(formation_decision_digest)
            and is_digest(context_envelope_digest)
            and is_digest(execution_plan_digest)
            and formation_decision_digest
            == summary.get("task_formation_decision_receipt_digest")
            == exported_competition.get("task_formation_decision_receipt_digest")
            == exported_oac_lineage.get("task_formation_decision_receipt_digest")
            and context_envelope_digest
            == summary.get("context_envelope_digest")
            == exported_competition.get("context_envelope_digest")
            == exported_oac_lineage.get("task_agent_context_envelope_digest")
            and execution_plan_digest
            == summary.get("agentteams_execution_plan_digest")
            == exported_competition.get("agentteams_execution_plan_digest")
            == exported_oac_lineage.get("agentteams_execution_plan_digest")
            and bool(bindings)
            and all(
                isinstance(item, dict)
                and item.get("formation_receipt_digest")
                == formation_decision_digest
                and item.get("context_envelope_digest") == context_envelope_digest
                and item.get("agentteams_execution_plan_digest")
                == execution_plan_digest
                for item in bindings
            )
        ),
        "oac_topology": (
            exported_oac_formation.get("status") == "BOUND_TO_CURRENT_RUN"
            and exported_oac_formation.get("candidate_only") is True
            and exported_oac_formation.get("canonical_target_writes") == 0
            and exported_oac_lineage.get("status")
            == "OAC_BOUND_EXECUTION_PLAN_REALIZED"
            and exported_oac_lineage.get("topology_match") is True
            and exported_oac_lineage.get("candidate_only") is True
            and exported_oac_lineage.get("canonical_target_writes") == 0
            and isinstance(planned_domains, list)
            and bool(planned_domains)
            and planned_domains
            == actual_domains
            == selected_domains
            == bound_domains
            == planned_task_domains
        ),
    }

    checks = {
        "independent_verifier": (
            verification.get("status") == "PASS"
            and verification.get("causal_verification") == "PASS"
            and verification.get("experience_verification") == "PASS"
            and verification.get("failures") == []
        ),
        "golden_summary": summary.get("status") == "PASS",
        "quote_v3": (
            state.get("stage") == "QUOTE_V3"
            and quote.get("version") == "v3"
            and isinstance(quote_payload.get("launch_date"), str)
            and isinstance(quote_payload.get("currency"), str)
        ),
        "agentteams_actions": summary.get("agentteams_action_count") == 40,
        "task_bindings": len(bindings) == 7,
        "independent_workers": summary.get("independent_domain_worker_processes") == 5,
        "independent_reviewers": summary.get("independent_reviewer_processes") == 2,
        "two_server_human_gates": (
            len(owner_gates) == 2
            and all(item.get("review_wait_satisfied") is True for item in owner_gates)
            and all(
                isinstance(item.get("review_duration_ms"), int)
                and item["review_duration_ms"] >= 4000
                for item in owner_gates
            )
        ),
        "same_run_agentteams_skill_tool": same_run_pass,
        "experience_approved_canary": (
            experience.get("status") == "APPROVED_CANARY"
            and experience_candidate.get("maturity") == "SINGLE_RUN_SEED"
            and experience_evaluation.get("verdict") == "CANARY"
            and len(experience_cases) == 8
            and experience_decision.get("decision") == "APPROVE"
            and experience_decision.get("review_wait_satisfied") is True
            and experience_release.get("release_state") == "CANARY"
            and experience.get("candidate_only") is True
            and experience.get("target_writes") == 0
        ),
        "candidate_layer_zero_canonical_writes": (
            summary.get("canonical_target_writes") == 0
            and all(
                isinstance(item, dict) and item.get("target_writes") == 0
                for item in bindings
            )
        ),
        **oac_checks,
    }
    if not failures:
        failures.extend(f"GOLDEN_{name.upper()}_INVALID" for name, passed in checks.items() if not passed)

    state_boundaries = state.get("boundaries")
    state_boundaries = state_boundaries if isinstance(state_boundaries, dict) else {}
    approval_control = state.get("approval_control")
    approval_control = approval_control if isinstance(approval_control, dict) else {}
    oac_source_error = loaded["evidence_export"][1]
    oac_failures = [oac_source_error] if oac_source_error is not None else [
        f"GOLDEN_{name.upper()}_INVALID"
        for name, passed in oac_checks.items()
        if not passed
    ]
    return {
        "status": _derived_status(failures),
        "evidence_role": "CURRENT_GOLDEN_DELIVERY",
        "evidence_paths": paths,
        "failures": failures,
        "run_id": golden_run_id,
        "quote": {
            "status": "PASS" if checks["quote_v3"] else "FAIL",
            "stage": state.get("stage"),
            "ref": (
                f"{quote.get('id')}@{quote.get('version')}"
                if quote.get("id") and quote.get("version")
                else None
            ),
            "version": quote.get("version"),
            "digest": quote.get("digest"),
            "launch_date": quote_payload.get("launch_date"),
            "currency": quote_payload.get("currency"),
        },
        "agentteams": {
            "status": "PASS"
            if all(
                checks[name]
                for name in (
                    "agentteams_actions",
                    "task_bindings",
                    "independent_workers",
                    "independent_reviewers",
                )
            )
            else "FAIL",
            "action_count": summary.get("agentteams_action_count"),
            "task_binding_count": len(bindings) if bindings else None,
            "independent_domain_worker_processes": summary.get(
                "independent_domain_worker_processes"
            ),
            "independent_reviewer_processes": summary.get("independent_reviewer_processes"),
        },
        "human_owner_gates": {
            "status": "PASS" if checks["two_server_human_gates"] else "FAIL",
            "count": len(owner_gates),
            "server_review_duration_ms": approval_control.get("review_duration_ms"),
            "identity_mode": approval_control.get("identity_mode"),
            "external_iam": approval_control.get("external_iam"),
            "gates": owner_gates,
        },
        "same_run_agentteams_skill_tool": {
            "status": "PASS" if same_run_pass else "FAIL",
            "run_id": golden_run_id,
            "agentteams_summary_digest": summary.get("digest"),
            "tool_receipt_digest": tool_digest,
            "skill_invocation_receipt_digest": skill_digest,
        },
        "oac_bound_task_formation": {
            "status": _derived_status(oac_failures),
            "evidence_role": "GOLDEN_SAME_RUN_OAC_BOUND_TASK_FORMATION",
            "run_id": golden_run_id,
            "failures": oac_failures,
            "task_intake_status": exported_task_intake.get("status"),
            "activation_status": exported_oac_activation.get("status"),
            "activation_binding_digest": activation_binding_digest,
            "task_formation_decision_receipt_digest": formation_decision_digest,
            "context_envelope_digest": context_envelope_digest,
            "agentteams_execution_plan_digest": execution_plan_digest,
            "planned_domain_ids": planned_domains,
            "actual_agentteams_domain_ids": actual_domains,
            "topology_match": exported_oac_lineage.get("topology_match"),
            "candidate_only": exported_oac_lineage.get("candidate_only"),
            "canonical_target_writes": exported_oac_lineage.get(
                "canonical_target_writes"
            ),
            "claim_boundary": exported_oac_activation.get("claim_ceiling"),
        },
        "experience_governance": {
            "status": experience.get("status") or "MISSING_EVIDENCE",
            "maturity": experience_candidate.get("maturity"),
            "evaluation_partition_count": len(experience_cases) if experience_cases else None,
            "release_state": experience_release.get("release_state"),
            "candidate_only": experience.get("candidate_only"),
            "canonical_target_writes": experience.get("target_writes"),
            "claim_boundary": experience_release.get("claim_boundary"),
        },
        "candidate_layer_canonical_target_writes": summary.get("canonical_target_writes"),
        "evidence_class": summary.get("evidence_class"),
        "claim_boundary": summary.get("claim_boundary"),
        "agents_are_candidate_only": state_boundaries.get("agents_are_candidate_only"),
        "external_enterprise_systems": state_boundaries.get("external_enterprise_systems"),
        "production_ready": False,
    }


def _build_current_dynamic_formation_validation(root: Path) -> dict[str, Any]:
    paths = {
        "verification": "evidence/formation-taskflow/latest/verification.json",
        "receipt": "evidence/formation-taskflow/latest/probe-receipt.json",
    }
    loaded = {name: _optional_machine_object(root, path) for name, path in paths.items()}
    failures = [error for _, error in loaded.values() if error is not None]
    verification = loaded["verification"][0] or {}
    receipt = loaded["receipt"][0] or {}
    passed = (
        verification.get("status") == "PASS"
        and verification.get("topology_match") is True
        and verification.get("candidate_only") is True
        and verification.get("canonical_target_writes") == 0
        and verification.get("project_terminal_state") == "completed"
        and verification.get("run_id") == receipt.get("run_id")
        and receipt.get("candidate_only") is True
        and receipt.get("canonical_target_writes") == 0
        and receipt.get("project_terminal_state") == "completed"
        and verification.get("selected_domain_ids") == verification.get("actual_domain_ids")
        and verification.get("domain_task_count", 0) >= 2
        and verification.get("reviewer_task_count", 0) >= 1
    )
    if not failures and not passed:
        failures.append("DYNAMIC_FORMATION_VERIFICATION_INVALID")
    return {
        "status": _derived_status(failures),
        "evidence_role": "INDEPENDENT_DYNAMIC_FORMATION_RUN",
        "evidence_paths": paths,
        "failures": failures,
        "run_id": verification.get("run_id"),
        "evidence_class": verification.get("evidence_class"),
        "selected_domain_ids": verification.get("selected_domain_ids"),
        "domain_task_count": verification.get("domain_task_count"),
        "reviewer_task_count": verification.get("reviewer_task_count"),
        "agentteams_action_count": verification.get("agentteams_actions"),
        "task_binding_count": verification.get("task_binding_count"),
        "topology_match": verification.get("topology_match"),
        "candidate_only": verification.get("candidate_only"),
        "canonical_target_writes": verification.get("canonical_target_writes"),
        "claim_boundary": verification.get("claim_boundary"),
        "production_ready": False,
    }


def build_current_semifinal_delivery(*, root: Path = ROOT) -> dict[str, Any]:
    """Derive the current semifinal facts from Golden plus one independent probe."""

    golden = _build_current_golden_delivery(root)
    formation = _build_current_dynamic_formation_validation(root)
    component_statuses = [golden["status"], formation["status"]]
    component_run_ids = (
        golden.get("run_id"),
        formation.get("run_id"),
    )
    independent_runs = (
        all(isinstance(run_id, str) and run_id for run_id in component_run_ids)
        and len(set(component_run_ids)) == len(component_run_ids)
    )
    failures: list[str] = []
    if all(status == "PASS" for status in component_statuses) and independent_runs:
        status = "PASS"
    elif any(status == "INCOMPLETE" for status in component_statuses):
        status = "INCOMPLETE"
    else:
        status = "FAIL"
        if not independent_runs:
            failures.append("CURRENT_COMPONENT_RUNS_NOT_INDEPENDENT")
    return {
        "status": status,
        "fact_role": "CURRENT_SEMIFINAL_DELIVERY",
        "evidence_generation": "CURRENT_MACHINE_EVIDENCE_DERIVED",
        "failures": failures,
        "golden_quote_delivery": golden,
        "dynamic_formation_validation": formation,
        "claim_boundary": {
            "evidence_maturity": "VALIDATED_CONTROLLED_LOCAL"
            if status == "PASS"
            else "NOT_ESTABLISHED",
            "agents_tools_skills_are_candidate_only": golden.get("agents_are_candidate_only"),
            "golden_and_dynamic_formation_are_independent_runs": independent_runs,
            "real_enterprise_connectors": "NOT_RUN",
            "external_human_iam": "NOT_RUN",
            "distributed_production_workers": "NOT_RUN",
            "production_ha_dr_sla": "NOT_RUN",
            "production_ready": False,
        },
    }


def _require_enterprise_fact(condition: bool, code: str) -> None:
    if not condition:
        raise RuntimeError(code)


def build_enterprise_seed_facts() -> dict[str, Any]:
    """Re-admit shipped Seed profiles and derive the bounded enterprise facts.

    This deliberately executes the same strict Profile, exact-byte Source, and
    Reference Runtime projection admission paths used by the product.  Counts
    and claims are derived from validated receipts; the generator does not
    infer evidence from fixture filenames or copy presentation constants.
    """

    profiles = (
        northstar_acme_quote_profile(),
        supplier_shadow_intake_profile(),
    )
    profile_refs = tuple(profile.ref for profile in profiles)
    _require_enterprise_fact(
        len(profile_refs) == len(set(profile_refs)),
        "RELEASE_FACTS_ENTERPRISE_PROFILE_REF_DUPLICATE",
    )

    locator_assets = exact_locator_assets()
    declared_locators = tuple(
        source_root.locator for profile in profiles for source_root in profile.source_roots
    )
    _require_enterprise_fact(
        len(declared_locators) == len(set(declared_locators)),
        "RELEASE_FACTS_ENTERPRISE_SOURCE_LOCATOR_DUPLICATE",
    )
    _require_enterprise_fact(
        set(declared_locators) == set(locator_assets),
        "RELEASE_FACTS_ENTERPRISE_SOURCE_LOCATOR_DRIFT",
    )

    profile_facts: list[dict[str, Any]] = []
    authority_assurances: set[str] = set()
    runtime_profile_refs: list[str] = []
    intake_only_profile_refs: list[str] = []
    runtime_projection_total = 0
    runtime_projection_matched = 0
    canonical_target_writes = 0

    for profile in profiles:
        source_receipt = admit_enterprise_seed_sources(profile)
        admission_receipt = admit_enterprise_seed_profile(
            profile,
            source_admission=source_receipt,
        )
        _require_enterprise_fact(
            source_receipt.profile_ref == profile.ref and source_receipt.profile_digest == profile.digest,
            "RELEASE_FACTS_ENTERPRISE_SOURCE_PROFILE_DRIFT",
        )
        _require_enterprise_fact(
            admission_receipt.profile_ref == profile.ref
            and admission_receipt.profile_digest == profile.digest,
            "RELEASE_FACTS_ENTERPRISE_ADMISSION_PROFILE_DRIFT",
        )
        _require_enterprise_fact(
            admission_receipt.source_admission_receipt_digest == source_receipt.digest,
            "RELEASE_FACTS_ENTERPRISE_PROFILE_SOURCE_RECEIPT_DRIFT",
        )

        declared_profile_locators = {item.locator for item in profile.source_roots}
        observed_profile_locators = {item.locator for item in source_receipt.root_observations}
        _require_enterprise_fact(
            declared_profile_locators == observed_profile_locators,
            "RELEASE_FACTS_ENTERPRISE_SOURCE_OBSERVATION_DRIFT",
        )
        _require_enterprise_fact(
            all(
                locator_assets[observation.locator] == observation.logical_asset_ref
                for observation in source_receipt.root_observations
            ),
            "RELEASE_FACTS_ENTERPRISE_SOURCE_ASSET_BINDING_DRIFT",
        )
        _require_enterprise_fact(
            tuple(item.observed_digest for item in source_receipt.root_observations)
            == admission_receipt.admitted_source_root_digests,
            "RELEASE_FACTS_ENTERPRISE_SOURCE_DIGEST_DRIFT",
        )
        _require_enterprise_fact(
            tuple(item.digest for item in source_receipt.component_admissions)
            == admission_receipt.component_admission_digests,
            "RELEASE_FACTS_ENTERPRISE_COMPONENT_DIGEST_DRIFT",
        )

        runtime_mode = profile.runtime_compatibility.mode
        intake_only = runtime_mode == RuntimeCompatibilityMode.INTAKE_ONLY
        if intake_only:
            intake_only_profile_refs.append(profile.ref)
            _require_enterprise_fact(
                not admission_receipt.reference_runtime_compatible,
                "RELEASE_FACTS_ENTERPRISE_INTAKE_ONLY_RUNTIME_DRIFT",
            )

        runtime_projection: dict[str, Any] | None = None
        runtime_writes = 0
        if admission_receipt.reference_runtime_compatible:
            runtime_receipt = verify_reference_runtime_projections(profile, source_receipt)
            runtime_profile_refs.append(profile.ref)
            matched = sum(
                observation.status == "MATCH"
                and observation.admitted_projection_digest == observation.runtime_projection_digest
                for observation in runtime_receipt.observations
            )
            total = len(runtime_receipt.observations)
            _require_enterprise_fact(
                total == len(SeedComponentKind) and matched == total,
                "RELEASE_FACTS_ENTERPRISE_RUNTIME_PROJECTION_DRIFT",
            )
            _require_enterprise_fact(
                runtime_receipt.source_admission_receipt_digest == source_receipt.digest,
                "RELEASE_FACTS_ENTERPRISE_RUNTIME_SOURCE_RECEIPT_DRIFT",
            )
            runtime_projection_total += total
            runtime_projection_matched += matched
            runtime_writes = runtime_receipt.canonical_target_writes
            runtime_projection = {
                "status": runtime_receipt.verdict,
                "receipt_digest": runtime_receipt.digest,
                "matched": matched,
                "total": total,
                "runtime_projection_digest": runtime_receipt.runtime_projection_digest,
                "canonical_target_writes": runtime_writes,
            }

        profile_writes = (
            source_receipt.canonical_target_writes
            + admission_receipt.canonical_target_writes
            + runtime_writes
        )
        canonical_target_writes += profile_writes
        authority_assurances.add(source_receipt.authority_assurance)
        profile_facts.append(
            {
                "profile_ref": profile.ref,
                "profile_digest": profile.digest,
                "profile_schema_version": profile.schema_version,
                "admission_receipt_digest": admission_receipt.digest,
                "organization_id": profile.organization_id,
                "scenario_id": profile.scenario_id,
                "synthetic": profile.synthetic,
                "data_class": profile.data_class.value,
                "runtime_mode": runtime_mode.value,
                "intake_only": intake_only,
                "seed_ready": admission_receipt.seed_ready,
                "shadow_intake_admissible": (admission_receipt.shadow_intake_admissible),
                "reference_runtime_compatible": (admission_receipt.reference_runtime_compatible),
                "admitted_claim_ceiling": (admission_receipt.admitted_claim_ceiling.value),
                "source_admission": {
                    "status": source_receipt.verdict,
                    "receipt_digest": source_receipt.digest,
                    "admission_policy_ref": source_receipt.admission_policy_ref,
                    "authority_assurance": source_receipt.authority_assurance,
                    "fixed_exact_source_component_roots": len(source_receipt.root_observations),
                    "component_admissions": len(source_receipt.component_admissions),
                    "source_projection_digest": source_receipt.source_projection_digest,
                    "component_projection_digest": (source_receipt.component_projection_digest),
                    "profile_projection_digest": source_receipt.profile_projection_digest,
                    "canonical_target_writes": (source_receipt.canonical_target_writes),
                    "root_observations": [
                        {
                            "source_root_ref": observation.source_root_ref,
                            "locator": observation.locator,
                            "logical_asset_ref": observation.logical_asset_ref,
                            "component_kind": observation.component_kind.value,
                            "observed_digest": observation.observed_digest,
                            "byte_length": observation.byte_length,
                        }
                        for observation in source_receipt.root_observations
                    ],
                },
                "runtime_projection": runtime_projection,
                "canonical_target_writes": profile_writes,
            }
        )

    authority_values = sorted(authority_assurances)
    all_synthetic = all(profile.synthetic for profile in profiles)
    all_sources_admitted = all(
        profile_fact["source_admission"]["status"] == "ADMITTED" for profile_fact in profile_facts
    )
    reference_profile_source_bound = (
        bool(runtime_profile_refs)
        and runtime_projection_total > 0
        and runtime_projection_matched == runtime_projection_total
        and all_synthetic
        and all_sources_admitted
        and authority_values == ["DECLARED_NOT_AUTHENTICATED"]
        and canonical_target_writes == 0
    )
    _require_enterprise_fact(
        reference_profile_source_bound,
        "RELEASE_FACTS_ENTERPRISE_CLAIM_CEILING_UNPROVEN",
    )

    return {
        "status": "PASS",
        "current_claim_ceiling": "REFERENCE_PROFILE_SOURCE_BOUND",
        "profile_count": len(profiles),
        "synthetic_profile_count": sum(profile.synthetic for profile in profiles),
        "fixed_exact_source_component_root_count": len(locator_assets),
        "admitted_source_component_root_count": sum(
            profile_fact["source_admission"]["fixed_exact_source_component_roots"]
            for profile_fact in profile_facts
        ),
        "authority_assurance": authority_values[0],
        "reference_runtime_profile_refs": runtime_profile_refs,
        "intake_only_profile_refs": intake_only_profile_refs,
        "runtime_projection_match": {
            "matched": runtime_projection_matched,
            "total": runtime_projection_total,
        },
        "canonical_target_writes": canonical_target_writes,
        "profiles": profile_facts,
        "limitations": [
            "SHIPPED_SYNTHETIC_REFERENCE_PROFILES_ONLY",
            "FIXED_EXACT_LOCATORS_ONLY",
            "SOURCE_AUTHORITY_DECLARED_NOT_AUTHENTICATED",
            "NO_REAL_ENTERPRISE_GENERALIZATION",
            *(["INTAKE_ONLY_PROFILES_HAVE_NO_HANDLER_EXECUTION"] if intake_only_profile_refs else []),
        ],
    }


def build_facts(*, root: Path = ROOT) -> dict[str, Any]:
    demo = _object(root / "evidence" / "latest" / "demo.json")
    manifest = _object(root / "evidence" / "latest" / "manifest.json")
    agentteams_config = _object(root / "configs" / "goai-agentteams-demo.json")
    historical_transport, fresh_core = load_agentteams_evidence(config=agentteams_config, project_root=root)
    fresh_ingestion = fresh_core["semantic_ingestion"]
    workspace_demo = _object(root / "evidence" / "workspace" / "latest" / "workspace-demo.json")
    workspace_agentteams = _object(root / "evidence" / "workspace" / "latest" / "agentteams-check.json")
    readiness = _object(root / "evidence" / "workspace" / "latest" / "review-readiness.json")
    product_path = _object(root / "evidence" / "workspace" / "latest" / "product-path-blackbox.json")
    goai_gate = _load_goai_gate()
    product_path_verification = goai_gate.verify_product_path_blackbox(
        root, rebuild_wheel=False, retained_verifiers=True,
    )
    oac_bridge = _object(root / "evidence" / "oac-bridge" / "latest" / "bridge-demo.json")
    oac_evolution_summary = _object(root / "evidence" / "oac-evolution" / "latest" / "summary.json")
    oac_evolution_verification = goai_gate.verify_oac_governed_evolution(root)
    _require_enterprise_fact(
        oac_evolution_verification["status"] == "PASS",
        "RELEASE_FACTS_OAC_GOVERNED_EVOLUTION_INVALID",
    )
    semifinal_closure = _object(
        root / "evidence" / "semifinal-closure" / "latest" / "summary.json"
    )
    semifinal_closure_verification = goai_gate.verify_integrated_semifinal_closure(root)
    semifinal_closure_pass = (
        semifinal_closure_verification is not None
        and semifinal_closure.get("status") == "PASS"
        and semifinal_closure.get("evidence_class")
        == "CONTROLLED_LOCAL_INTEGRATED_VERTICAL_SLICE"
        and semifinal_closure.get("terminal_state") == "CANDIDATE_ACCEPTED"
        and semifinal_closure.get("canonical_target_writes") == 0
        and semifinal_closure.get("business_value_same_run") is False
        and semifinal_closure.get("completion_matrix") == SEMIFINAL_COMPLETION_MATRIX
        and semifinal_closure.get("production_readiness") is False
    )
    _require_enterprise_fact(
        semifinal_closure_pass,
        "RELEASE_FACTS_SEMIFINAL_INTEGRATED_CLOSURE_INVALID",
    )
    preview = demo["preview"]
    vmrc = demo["minimal_rebase_certificate"]
    collaboration = demo["collaboration"]
    orchestration_plan = collaboration["orchestration_plan"]
    coordination_receipt = collaboration["coordination_receipt"]
    qualification = demo["receipt"]["qualification_report"]
    candidate_score = next(score for score in qualification["scores"] if score["version"] == "1.3")
    product_path_pass = product_path_verification["status"] == "PASS"
    product_case_facts = {
        item["id"]: item["facts"]
        for item in product_path["cases"]
        if isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and isinstance(item.get("facts"), dict)
    }
    product_path_pp001 = product_case_facts["PP-001"]
    product_path_pp010 = product_case_facts["PP-010"]
    workspace_local_pass = (
        workspace_demo["primary_evaluation_status"] == "PASS"
        and workspace_demo["skill_status"] == "CANARY"
        and workspace_demo["approval_input_mode"] == "CONTROLLED_LOCAL_SCRIPTED_COMMAND"
        and workspace_demo["tool_evidence"]["invocation_receipt_ref"].startswith("tool-call:")
        and workspace_agentteams["static"]["status"] == "PASS"
        and readiness["status"] == "PASS"
        and readiness["failure_count"] == 0
        and product_path_pass
    )
    oac_bridge_pass = (
        oac_bridge["status"] == "LOCAL_RUNTIME_ADMISSION_PASS"
        and oac_bridge["case_count"] == 2
        and oac_bridge["boundaries"]["target_writes"] == 0
        and oac_bridge["boundaries"]["handler_execution"] == "NOT_RUN"
        and oac_bridge["restart"]["state_digest_before_close"]
        == oac_bridge["restart"]["state_digest_after_reopen"]
    )
    current_semifinal_delivery = build_current_semifinal_delivery(root=root)
    return {
        "schema_version": "orgrebase.release-facts.v9",
        "project": "OrgRebase",
        "release": __version__,
        "system_thesis": (
            "contract-defined enterprise world model, task-conditioned organizational "
            "compilation, and proof-carrying governed evolution"
        ),
        "product_identity": {
            "category": "ENTERPRISE_AGENT_SUBSTRATE",
            "definition_zh": ("基于 OAC 的组织工作编译与持续演化基座"),
            "oac_role": "INDEPENDENT_CANDIDATE_STANDARD_AND_ACCEPTANCE_RELATION",
            "oac_is_agent": False,
            "oac_is_runtime": False,
            "reference_runtime_capability": "ENTERPRISE_WORK_CONTINUOUS_EVOLUTION_ENGINE",
            "authority_roots": ["SOURCE", "DEMAND", "PLAN", "OUTCOME", "EVOLUTION"],
            "execution_role": "EVIDENCED_RUNTIME_STATE_NOT_AUTHORITY_ROOT",
        },
        "innovation_hypothesis": {
            "claim_type": "COMBINATION_HYPOTHESIS_NOT_WORLD_FIRST_CLAIM",
            "dimensions": [
                {
                    "id": "CONTRACT_DEFINED_ENTERPRISE_WORLD_MODEL",
                    "status": "BOUNDED_IMPLEMENTED",
                },
                {
                    "id": "TASK_CONDITIONED_ORGANIZATIONAL_COMPILATION",
                    "status": "BOUNDED_SPLIT_EVIDENCE",
                },
                {
                    "id": "PROOF_CARRYING_GOVERNED_EVOLUTION",
                    "status": "BOUNDED_SYNTHETIC_CONTROLLED_REFERENCE_MVP",
                },
            ],
        },
        "deployment_readiness": {
            "competition_technical_mvp": "GO",
            "isolated_read_only_single_enterprise_shadow_pilot": "CONDITIONAL_GO",
            "arbitrary_enterprise_production": "NO_GO",
        },
        "release_scope": "CODE_ONLY",
        "current_semifinal_delivery": current_semifinal_delivery,
        "enterprise_seed_intake": build_enterprise_seed_facts(),
        "workspace": {
            "fact_role": "HISTORICAL_COMPATIBILITY_BASELINE",
            "current_semifinal_delivery": False,
            "baseline_note": (
                "Retained pre-Golden Workspace compatibility surface; current semifinal "
                "claims are authoritative only under current_semifinal_delivery."
            ),
            "local_status": "PASS" if workspace_local_pass else "FAIL",
            "final_quote_ref": (
                f"{workspace_demo['final_quote']['id']}@{workspace_demo['final_quote']['version']}"
            ),
            "final_quote_digest": workspace_demo["final_quote"]["digest"],
            "launch_date": workspace_demo["final_quote"]["payload"]["launch_date"],
            "currency": workspace_demo["final_quote"]["payload"]["currency"],
            "final_graph_ref": (
                f"{workspace_demo['final_graph_pointer']['id']}"
                f"@{workspace_demo['final_graph_pointer']['version']}"
            ),
            "evaluation_score": workspace_demo["primary_evaluation_score"],
            "skill_status": workspace_demo["skill_status"],
            "agentteams_static": workspace_agentteams["static"]["status"],
            "agentteams_live": workspace_agentteams["live"]["status"],
            "user_validation": workspace_demo["user_validation"]["status"],
            "approval_mode": workspace_demo["approval_mode"],
            "approval_input_mode": workspace_demo["approval_input_mode"],
            "external_human_approval": "NOT_RUN",
            "one_shot_routes": "RETIRED_FAIL_CLOSED_410_STAGED_COMMANDS_REQUIRED",
            "dependency_evidence_tool": {
                "status": "SUCCEEDED",
                "evidence_class": "LOCAL_REAL_TOOL",
                "target_writes": 0,
                **workspace_demo["tool_evidence"],
            },
            "evidence_index_digest": workspace_demo["evidence_index_digest"],
            "oac_runtime_bridge": workspace_demo["boundaries"]["oac_runtime_bridge"],
        },
        "oac_local_runtime_admission": {
            "status": oac_bridge["status"] if oac_bridge_pass else "FAIL",
            "profile": "oac.runtime-lowering/zero-effect/v0.1",
            "case_count": oac_bridge["case_count"],
            "cases": oac_bridge["cases"],
            "oac_source": oac_bridge["oac_source"],
            "policy_digest": oac_bridge["policy_digest"],
            "event_chain_head": oac_bridge["event_chain"]["head_digest"],
            "restart": oac_bridge["restart"],
            "target_writes": oac_bridge["boundaries"]["target_writes"],
            "runtime_owner_approval_input": oac_bridge["boundaries"]["runtime_owner_approval_input"],
            "external_human_approval": oac_bridge["boundaries"]["external_human_approval"],
            "handler_execution": oac_bridge["boundaries"]["handler_execution"],
            "agent_execution": oac_bridge["boundaries"]["agent_execution"],
            "outcome_certificate": oac_bridge["boundaries"]["outcome_certificate"],
            "real_enterprise": oac_bridge["boundaries"]["real_enterprise"],
            "evidence_path": "evidence/oac-bridge/latest",
        },
        "oac_governed_evolution": {
            "status": oac_evolution_verification["status"],
            "maturity": oac_evolution_verification["maturity"],
            "claim_ceiling": ("ONE_GOVERNED_EVOLUTION_REFERENCE_MVP_WITH_PRELIMINARY_REGRESSION"),
            "artifact_count": oac_evolution_verification["artifact_count"],
            "event_count": oac_evolution_verification["event_count"],
            "pack_digest": oac_evolution_verification["pack_digest"],
            "event_chain_head": oac_evolution_verification["event_chain_head"],
            "independent_evaluator": oac_evolution_verification["independent_evaluator"],
            "dual_wheel": oac_evolution_verification["dual_wheel"],
            "source": oac_evolution_summary["source"],
            "plan_count": len(oac_evolution_summary["plans"]),
            "plan_ids": sorted(oac_evolution_summary["plans"]),
            "distinct_topology_count": len(
                {plan["topology_digest"] for plan in oac_evolution_summary["plans"].values()}
            ),
            "runs": oac_evolution_summary["runs"],
            "evolution": oac_evolution_summary["evolution"],
            "restart": oac_evolution_summary["restart"],
            "preliminary_regression": oac_evolution_summary["preliminary_regression"],
            "boundaries": oac_evolution_summary["boundaries"],
            "evidence_path": "evidence/oac-evolution/latest",
        },
        "product_path_blackbox": {
            "fact_role": "HISTORICAL_COMPATIBILITY_BASELINE",
            "current_release_qualified": False,
            "status": product_path["status"] if product_path_pass else "FAIL",
            "benchmark_version": product_path["benchmark_version"],
            "evidence_class": product_path["evidence_class"],
            "execution_mode": product_path["execution"]["mode"],
            "uvicorn_processes_started": product_path["execution"][
                "uvicorn_processes_started"
            ],
            "real_process_restarts": product_path["execution"][
                "real_process_restarts"
            ],
            "case_count": product_path["case_summary"]["total"],
            "cases_passed": product_path["case_summary"]["passed"],
            "pp001_event_count": product_path_pp001["event_count"],
            "mutation_count": product_path["mutation_summary"]["total"],
            "mutations_killed": product_path["mutation_summary"]["killed"],
            "integrity_attacks": product_path["integrity_attack_summary"]["total"],
            "integrity_attacks_rejected": product_path["integrity_attack_summary"]["rejected"],
            "gate_attacks": product_path_verification["gate_attack_summary"]["total"],
            "gate_attacks_rejected": product_path_verification["gate_attack_summary"]["rejected"],
            "single_byte_tamper": product_path["single_byte_tamper"]["status"],
            "retained_evaluator_replay": product_path_verification["evaluator_replay"],
            "reconstructed_wheel_bytes": product_path_verification["wheel_rebuild"],
            "retained_raw_observations": product_path["source_bindings"]["retained_bundle"]["status"],
            "artifact_bindings": len(product_path["source_bindings"]["artifact_manifest"]["entries"]),
            "independent_evaluator": product_path["independent_evaluator"]["implementation"],
            "task_intake_binding_scope": product_path["independent_evaluator"][
                "task_intake_binding_scope"
            ],
            "approval_review_gate_scope": product_path["independent_evaluator"][
                "approval_review_gate_scope"
            ],
            "task_intake_status": product_path_pp001["task_intake_run_status"],
            "task_intake_same_run_id": product_path_pp001[
                "task_intake_same_run_id"
            ],
            "task_intake_same_workspace_nonce": product_path_pp001[
                "task_intake_same_workspace_nonce"
            ],
            "task_intake_natural_language_authority": product_path_pp001[
                "task_intake_natural_language_authority"
            ],
            "review_duration_ms": product_path_pp001["review_duration_ms"],
            "review_gate_store": product_path_pp001["review_gate_store"],
            "identity_mode": product_path_pp001["identity_mode"],
            "external_iam": product_path_pp001["external_iam"],
            "direct_form_status": product_path_pp010["direct_form_status"],
            "direct_form_code": product_path_pp010["direct_form_code"],
            "source_bound_oracle": product_path["independent_evaluator"]["source_bound_oracle"]["status"],
            "source_component_roots": product_path["independent_evaluator"]["source_bound_oracle"][
                "component_root_count"
            ],
            "source_runtime_projection": product_path["independent_evaluator"]["source_bound_oracle"][
                "runtime_projection"
            ],
            "source_canonical_target_writes": product_path["independent_evaluator"]["source_bound_oracle"][
                "canonical_target_writes"
            ],
            "benchmark_migration": product_path["source_bindings"]["benchmark_migration"]["status"],
            "rfc8785_quote": product_path["independent_evaluator"]["rfc8785_probe"]["quote_export"],
            "rfc8785_evidence": product_path["independent_evaluator"]["rfc8785_probe"]["evidence_export"],
            "data_profile": "SYNTHETIC_NORTHSTAR_ACME",
            "external_participants": product_path["boundaries"]["external_participants"],
            "real_enterprise_generalization": product_path["boundaries"]["real_enterprise_generalization"],
            "evidence_path": "evidence/workspace/latest/product-path-blackbox.json",
        },
        "semifinal_integrated_candidate_closure": {
            "status": "PASS",
            "fact_role": "HISTORICAL_COMPATIBILITY_BASELINE",
            "current_release_qualified": False,
            "current_build_binding": semifinal_closure_verification["current_build_binding"],
            "current_release_status": "NOT_QUALIFIED_BY_RETAINED_EVIDENCE",
            "evidence_class": semifinal_closure["evidence_class"],
            "run_id": semifinal_closure["run_id"],
            "task_id": semifinal_closure["task_id"],
            "terminal_state": semifinal_closure["terminal_state"],
            "canonical_target_writes": semifinal_closure["canonical_target_writes"],
            "business_value_same_run": semifinal_closure["business_value_same_run"],
            "completion_matrix": semifinal_closure["completion_matrix"],
            "production_readiness": semifinal_closure["production_readiness"],
            "verification_mode": "EXTERNAL_POST_WHEEL_PARENT_VERIFIER",
            "artifact_digests": "OMITTED_TO_AVOID_RELEASE_FACTS_WHEEL_SELF_REFERENCE",
            "evidence_path": "evidence/semifinal-closure/latest",
        },
        "local_control_plane": {
            "status": "PASS",
            "change_set_digest": demo["change_set"]["digest"],
            "preview_digest": preview["digest"],
            "impact_algorithm": preview["algorithm_version"],
            "impact_counts": preview["counts"],
            "impact_certificate_count": len(preview["certificates"]),
            "minimal_rebase_certificate_digest": vmrc["digest"],
            "rebase_receipt_digest": demo["receipt"]["digest"],
            "candidate_ingestion_digest": demo["receipt"]["candidate_ingestion_digest"],
            "evidence_manifest_artifacts": len(manifest["artifacts"]),
        },
        "agent_orchestration": {
            "status": coordination_receipt["status"],
            "compiler": "orgrebase.authority-aware-orchestration-compiler@1.0.0",
            "plan_digest": orchestration_plan["digest"],
            "coordination_receipt_digest": coordination_receipt["digest"],
            "compilation_receipt_digest": collaboration["compilation_receipt"]["digest"],
            "task_count": len(orchestration_plan["tasks"]),
            "authority_domains": sorted({task["authority_domain"] for task in orchestration_plan["tasks"]}),
            "input_commitments_per_task": sorted(
                {len(task["input_refs"]) for task in orchestration_plan["tasks"]}
            ),
            "checked_invariants": coordination_receipt["checked_invariants"],
        },
        "skill_governance": {
            "status": candidate_score["outcome"],
            "contract_digest": qualification["skill_contract_digest"],
            "evaluation_set_digest": qualification["evaluation_set_digest"],
            "candidate_adapter_version": qualification["candidate_adapter_version"],
            "bound_action_candidates": sum(
                bool(item["action_candidate_digest"]) for item in candidate_score["case_results"]
            ),
            "release_state": qualification["candidate_state"],
        },
        "historical_agentteams_transport": {
            "status": historical_transport["status"],
            "evidence_class": historical_transport["evidence_class"],
            "semantic_acceptance": historical_transport["semantic_acceptance"],
            "autonomous_collaboration": historical_transport["autonomous_collaboration"],
            "current_workspace_live": historical_transport["current_workspace_live"],
            "fixture_path": historical_transport["fixture_path"],
            "fixture_raw_sha256": historical_transport["fixture_raw_sha256"],
            "claim_boundary": historical_transport["claim_boundary"],
        },
        "core_change_advisory_live_agentteams": {
            "status": fresh_core["status"],
            "evidence_class": fresh_core["evidence_class"],
            "scope_evidence_class": fresh_core["scope_evidence_class"],
            "receipt_digest": fresh_core["live_receipt_digest"],
            "run_id": fresh_core["run_id"],
            "agentteams_version": fresh_core["agentteams_version"],
            "source_commit": fresh_core["source_commit"],
            "worker_count": fresh_core["worker_count"],
            "successful_model_calls": fresh_core["successful_provider_executions"],
            "model_ids": fresh_core["model_ids"],
            "execution_transport": fresh_core["execution_transport"],
            "matrix_role": fresh_core["matrix_role"],
            "bundle_path": fresh_core["bundle_path"],
            "run_summary_path": fresh_core["run_summary_path"],
            "runtime_skill": fresh_core["runtime_skill"],
            "current_workspace_live": fresh_core["current_workspace_live"],
            "oac_runtime_bridge": "NOT_USED_IN_THIS_RUN",
        },
        "candidate_control_plane": {
            "status": fresh_ingestion["status"],
            "receipt_digest": fresh_ingestion["receipt_digest"],
            "orchestration_plan_digest": fresh_ingestion["orchestration_plan_digest"],
            "evaluated": fresh_ingestion["evaluated"],
            "advisory_accepted": fresh_ingestion["advisory_accepted"],
            "rejected": fresh_ingestion["rejected"],
            "target_writes": fresh_ingestion["target_writes"],
            "admitted_effects": fresh_ingestion["admitted_effects"],
            "rejection_reason_counts": fresh_ingestion["rejection_reason_counts"],
            "receipt_path": fresh_core["semantic_ingestion_path"],
        },
        "code_release_gate": {
            "current_release_status": "NOT_QUALIFIED_BY_RETAINED_EVIDENCE",
            "status": (
                "HISTORICAL_PASS_CURRENT_BUILD_UNQUALIFIED"
                if workspace_local_pass and semifinal_closure_pass else "FAIL"
            ),
            "fact_role": "HISTORICAL_COMPATIBILITY_BASELINE",
            "current_release_qualified": False,
            "current_build_binding": semifinal_closure_verification["current_build_binding"],
            "checks": {
                "workspace_local_loop": workspace_demo["primary_evaluation_status"],
                "workspace_skill": workspace_demo["skill_status"],
                "workspace_agentteams_static": workspace_agentteams["static"]["status"],
                "review_readiness": readiness["status"],
                "core_evidence_manifest": "PASS",
                "oac_local_runtime_admission": (
                    "LOCAL_RUNTIME_ADMISSION_PASS" if oac_bridge_pass else "FAIL"
                ),
                "oac_governed_evolution": oac_evolution_verification["status"],
                "product_path_blackbox": "PASS" if product_path_pass else "FAIL",
                "semifinal_integrated_candidate_closure": (
                    "PASS" if semifinal_closure_pass else "FAIL"
                ),
            },
            "external_milestones": readiness["external_boundaries"],
        },
        "claim_boundaries": (
            "Business objects and benchmark outcomes use a synthetic fixture.",
            (
                "The frozen fresh Core bundle proves one observed LIVE_AGENTTEAMS proposal-plane "
                "run; Workspace formation live remains NOT_RUN and that run did not use the "
                "separately verified local OAC Runtime Admission bridge."
            ),
            (
                "The separate OAC bridge proves local zero-effect Runtime admission, exact "
                "Runtime Owner identity/digest binding through a controlled-local scripted "
                "command, Formation persistence, and evidence replay only; external human "
                "approval, "
                "handler and Agent execution remain NOT_RUN and OutcomeCertificate is NOT_IMPLEMENTED."
            ),
            (
                "All four observed live candidates carried the Leader-committed plan, "
                "exact task, and exact input bindings; the control plane admitted them "
                "as advisory only with zero target writes."
            ),
            (
                "Direct OpenClaw gateway executed the four provider calls. Matrix records "
                "identity, publication, and collection; Matrix-inbound delegation is not claimed."
            ),
            (
                "Workspace batch and OAC one-command acceptance use controlled-local scripted "
                "owner commands; no external human approval is claimed."
            ),
            (
                "The governed-evolution pack proves one synthetic controlled Veracier "
                "Source-to-Demand-to-plural-Plan-to-Execution-to-independent-Outcome-to-"
                "promotion-and-rollback reference loop plus exact Northstar Quote regression. "
                "Governance is scripted, human review and real-enterprise validation remain "
                "NOT_RUN, and production readiness remains false."
            ),
            (
                f"{product_path['benchmark_version']} runs "
                f"{product_path['case_summary']['passed']}/"
                f"{product_path['case_summary']['total']} black-box cases from a built wheel "
                f"over HTTP and kills {product_path['mutation_summary']['killed']}/"
                f"{product_path['mutation_summary']['total']} registered result mutations on "
                "the frozen synthetic Northstar/Acme profile. The same suite binds employee "
                "Task Intake to one run and workspace nonce, observes the persisted four-second "
                "review gate, and proves the retired direct Form returns 410 without state drift; "
                "this is not arbitrary-enterprise validation."
            ),
            (
                "The Quote export matches RFC 8785; the Evidence export retains a documented "
                "integral-float numeric-format difference and is a P1 migration debt."
            ),
            (
                "The Specs 045-049 parent gate proves one controlled-local Source, native "
                "AgentTeams, four-domain coalition, Tool, installed-wheel Quote Skill, and "
                "causal OTLP candidate chain under one run ID. "
                "It stops at CANDIDATE_ACCEPTED with zero canonical writes; same-run "
                "Workspace Approval/Apply, distributed workers, real enterprise connectors "
                "and value, HA, geographic DR, and contractual SLA remain NOT_RUN. Exact "
                "parent artifact digests stay outside packaged release facts to avoid a "
                "release-facts/wheel self-reference cycle."
            ),
            "No real enterprise connector or Matrix human approval is claimed.",
            "Submission documents and video are outside this code-only fact surface.",
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "evidence" / "release-facts.json")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = json.dumps(build_facts(), ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != expected:
            raise SystemExit("RELEASE_FACTS_DRIFT")
        print('{"status":"PASS","fact_surface":"CURRENT"}')
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(expected, encoding="utf-8")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
