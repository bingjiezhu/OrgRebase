from __future__ import annotations

from pathlib import Path

import pytest

from oac.canonical import parse_resource
from oac.compiler import compile_supplier_change
from oac.models import OrganizationSnapshot, SemanticChangeSet, Verdict
from oac.verifier import verify_plan

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / "profiles" / "supplier-change" / "inputs"


def _roots(case_id: str) -> tuple[OrganizationSnapshot, SemanticChangeSet]:
    snapshot_name = (
        "veracier-proc01-truncated-contextual.snapshot.json"
        if case_id == "SC-010"
        else "veracier-proc01-contextual.snapshot.json"
    )
    snapshot = parse_resource((INPUTS / snapshot_name).read_bytes(), verify_digest=True)
    change = parse_resource((INPUTS / f"{case_id}.change.json").read_bytes(), verify_digest=True)
    assert isinstance(snapshot, OrganizationSnapshot)
    assert isinstance(change, SemanticChangeSet)
    return snapshot, change


@pytest.mark.parametrize(
    ("case_id", "expected_roles", "required_types", "expected_verdict"),
    [
        (
            "SC-008",
            {
                "role:quality-qualification",
                "role:operations-continuity",
                "role:procurement-owner",
            },
            {
                "qualification-evidence-check",
                "continuity-option-selection",
                "commercial-switch-review",
            },
            Verdict.ACCEPT,
        ),
        (
            "SC-009",
            {
                "role:procurement-owner",
                "role:operations-continuity",
                "role:quality-qualification",
                "role:compliance-reviewer",
            },
            {
                "exposure-inventory",
                "continuity-assessment",
                "alternative-qualification",
                "compliance-discovery",
            },
            Verdict.PROVISIONAL,
        ),
        (
            "SC-010",
            {"role:procurement-owner", "role:evidence-discovery"},
            {"source-discovery", "unknown-preservation"},
            Verdict.UNKNOWN,
        ),
    ],
)
def test_contextual_cases_close_over_declared_contract_fields(
    case_id: str,
    expected_roles: set[str],
    required_types: set[str],
    expected_verdict: Verdict,
) -> None:
    snapshot, change = _roots(case_id)
    plan = compile_supplier_change(snapshot, change)
    certificate = verify_plan(snapshot, change, plan)

    assert {item.role_definition_ref for item in plan.spec.role_instances} == expected_roles
    assert required_types.issubset(
        {item.obligation_type for item in plan.spec.obligations}
    )
    assert certificate.spec.verdict is expected_verdict
    assert plan.spec.applicability_evaluations
    evaluation_ids = {
        item.evaluation_id for item in plan.spec.applicability_evaluations
    }
    assert {
        evaluation_ref
        for path in plan.spec.impact_paths
        for evaluation_ref in path.evaluation_refs
    }.issubset(evaluation_ids)


def _ordered_type_pairs(case_id: str) -> set[tuple[str, str]]:
    snapshot, change = _roots(case_id)
    plan = compile_supplier_change(snapshot, change)
    obligation_types = {
        item.obligation_id: item.obligation_type for item in plan.spec.obligations
    }
    work_types = {
        item.work_unit_id: {
            obligation_types[obligation_ref]
            for obligation_ref in item.obligation_refs
        }
        for item in plan.spec.work_units
    }
    return {
        (left, right)
        for order in plan.spec.happens_before
        for left in work_types[order.predecessor_ref]
        for right in work_types[order.successor_ref]
    }


def test_contextual_prerequisite_types_compile_to_role_work_order() -> None:
    assert {
        ("qualification-evidence-check", "continuity-option-selection"),
        ("continuity-option-selection", "commercial-switch-review"),
    }.issubset(_ordered_type_pairs("SC-008"))
    assert {
        ("compliance-discovery", "alternative-qualification"),
        ("alternative-qualification", "continuity-assessment"),
    }.issubset(_ordered_type_pairs("SC-009"))
    assert ("source-discovery", "unknown-preservation") in _ordered_type_pairs(
        "SC-010"
    )


def test_explicit_compliance_duty_suppresses_generic_discovery() -> None:
    snapshot, change = _roots("SC-009")
    plan = compile_supplier_change(snapshot, change)
    types = {item.obligation_type for item in plan.spec.obligations}
    roles = {item.role_definition_ref for item in plan.spec.role_instances}

    assert "compliance-discovery" in types
    assert "discover_dependency" not in types
    assert "role:evidence-discovery" not in roles
    assert "role:finance-exposure" not in roles
    assert "role:legal-reviewer" not in roles
    assert any(path.duty_refs for path in plan.spec.impact_paths)


def test_root_unknown_is_not_downgraded_to_bounded_provisional() -> None:
    snapshot, change = _roots("SC-010")
    plan = compile_supplier_change(snapshot, change)
    certificate = verify_plan(snapshot, change, plan)
    discovery = [
        item
        for item in plan.spec.obligations
        if item.obligation_type == "source-discovery"
    ]

    assert len(discovery) == 1
    assert len(discovery[0].path_refs) > 1
    assert certificate.spec.verdict is Verdict.UNKNOWN
    assert "ROOT_APPLICABILITY_UNKNOWN" in certificate.spec.reason_codes
    assert certificate.spec.restrictions == ("activation_prohibited",)
