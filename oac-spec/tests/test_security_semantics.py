from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_core import make_change, make_snapshot

from oac.canonical import OACValidationError, parse_resource, resource_ref, seal_resource
from oac.compiler import CompilationError, compile_supplier_change
from oac.models import ChangeDelta, ObservedValue, OrderConstraint, Verdict
from oac.registry import REASON_CODE_REGISTRY
from oac.verifier import verify_plan

ROOT = Path(__file__).resolve().parents[1]


def _codes(certificate: object) -> set[str]:
    return set(certificate.spec.reason_codes)  # type: ignore[attr-defined]


def _rebind_plan_to_change(plan: object, change: object) -> object:
    rebound_spec = plan.spec.model_copy(  # type: ignore[attr-defined]
        update={"change_ref": resource_ref(change)}
    )
    return seal_resource(
        plan.model_copy(update={"spec": rebound_spec, "digest": None})  # type: ignore[attr-defined]
    )


@pytest.mark.parametrize(
    ("metadata_update", "spec_update"),
    [
        ({"namespace": "urn:organization:attacker"}, {}),
        ({"governance_ref": "urn:governance:attacker"}, {}),
        ({"owner_ref": "urn:role:attacker"}, {}),
        ({"source_refs": ("urn:dataset:attacker",)}, {}),
        ({}, {"scope_refs": ("urn:organization:attacker",)}),
    ],
)
def test_cross_enterprise_or_incoherent_change_is_never_compiled_or_accepted(
    metadata_update: dict[str, object], spec_update: dict[str, object]
) -> None:
    snapshot, change = make_snapshot(), make_change()
    forged = seal_resource(
        change.model_copy(
            update={
                "metadata": change.metadata.model_copy(update=metadata_update),
                "spec": change.spec.model_copy(update=spec_update),
                "digest": None,
            }
        )
    )
    with pytest.raises(CompilationError) as error:
        compile_supplier_change(snapshot, forged)
    assert error.value.reason_code == "RESOURCE_COHERENCE_VIOLATION"

    plan = _rebind_plan_to_change(compile_supplier_change(snapshot, change), forged)
    certificate = verify_plan(snapshot, forged, plan)  # type: ignore[arg-type]
    assert certificate.spec.verdict is Verdict.REJECT
    assert "RESOURCE_COHERENCE_VIOLATION" in _codes(certificate)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("namespace", "urn:organization:attacker"),
        ("governance_ref", "urn:governance:attacker"),
        ("owner_ref", "urn:organization:attacker"),
        ("source_refs", ("urn:snapshot:attacker", "urn:change:attacker")),
    ],
)
def test_incoherent_plan_envelope_receives_bounded_rejection_certificate(
    field: str, value: object
) -> None:
    snapshot, change = make_snapshot(), make_change()
    plan = compile_supplier_change(snapshot, change)
    forged = seal_resource(
        plan.model_copy(
            update={
                "metadata": plan.metadata.model_copy(update={field: value}),
                "digest": None,
            }
        )
    )
    certificate = verify_plan(snapshot, change, forged)
    assert certificate.spec.verdict is Verdict.REJECT
    assert "RESOURCE_COHERENCE_VIOLATION" in _codes(certificate)
    assert certificate.metadata.namespace == snapshot.metadata.namespace
    assert certificate.metadata.governance_ref == snapshot.metadata.governance_ref
    assert certificate.metadata.owner_ref == snapshot.metadata.owner_ref
    assert certificate.metadata.source_refs == (
        forged.metadata.id,
        snapshot.metadata.id,
        change.metadata.id,
    )


def test_delta_operation_is_wire_validated_and_bypass_is_profile_rejected() -> None:
    with pytest.raises(ValidationError):
        ChangeDelta(
            path="/status",
            operation="remove",
            before=ObservedValue(state="known", value="operating"),
            after=ObservedValue(state="known", value="liquidation"),
        )

    snapshot, change = make_snapshot(), make_change()
    forged_delta = change.spec.deltas[0].model_copy(update={"operation": "remove"})
    forged = seal_resource(
        change.model_copy(
            update={
                "spec": change.spec.model_copy(update={"deltas": (forged_delta,)}),
                "digest": None,
            }
        )
    )
    with pytest.raises(OACValidationError) as wire_error:
        parse_resource(forged.model_dump_json(by_alias=True), verify_digest=True)
    assert wire_error.value.reason_code == "CORE_SCHEMA_INVALID"
    with pytest.raises(CompilationError) as compiler_error:
        compile_supplier_change(snapshot, forged)
    assert compiler_error.value.reason_code == "CHANGE_OPERATION_INCONSISTENT"

    plan = _rebind_plan_to_change(compile_supplier_change(snapshot, change), forged)
    certificate = verify_plan(snapshot, forged, plan)  # type: ignore[arg-type]
    assert certificate.spec.verdict is Verdict.REJECT
    assert "CHANGE_OPERATION_INCONSISTENT" in _codes(certificate)


def test_extra_valid_delta_is_rejected_by_compiler_and_verifier() -> None:
    snapshot, change = make_snapshot(), make_change()
    extra_delta = ChangeDelta(
        path="/renewalTerms",
        operation="replace",
        before=ObservedValue(state="known", value="net-30"),
        after=ObservedValue(state="known", value="net-15"),
    )
    forged = seal_resource(
        change.model_copy(
            update={
                "spec": change.spec.model_copy(
                    update={"deltas": (*change.spec.deltas, extra_delta)}
                ),
                "digest": None,
            }
        )
    )
    parsed = parse_resource(forged.model_dump_json(by_alias=True), verify_digest=True)
    assert len(parsed.spec.deltas) == 2  # type: ignore[union-attr]

    with pytest.raises(CompilationError) as compiler_error:
        compile_supplier_change(snapshot, forged)
    assert compiler_error.value.reason_code == "SUPPLIER_STATUS_DELTA_SET_INVALID"

    plan = _rebind_plan_to_change(compile_supplier_change(snapshot, change), forged)
    certificate = verify_plan(snapshot, forged, plan)  # type: ignore[arg-type]
    assert certificate.spec.verdict is Verdict.REJECT
    assert "SUPPLIER_STATUS_DELTA_SET_INVALID" in _codes(certificate)


@pytest.mark.parametrize(
    ("side", "value", "expected_code"),
    [
        ("before", 42, "UNSUPPORTED_SEMANTICS"),
        ("before", True, "UNSUPPORTED_SEMANTICS"),
        ("before", "unknown", "SYNTHETIC_UNKNOWN_VALUE"),
        ("after", 42, "UNSUPPORTED_SEMANTICS"),
        ("after", True, "UNSUPPORTED_SEMANTICS"),
        ("after", "unknown", "SYNTHETIC_UNKNOWN_VALUE"),
    ],
)
def test_known_supplier_status_is_typed_and_never_synthetic_unknown(
    side: str, value: object, expected_code: str
) -> None:
    snapshot, change = make_snapshot(), make_change()
    delta = change.spec.deltas[0].model_copy(
        update={
            side: ObservedValue(state="known", value=value),
        }
    )
    forged = seal_resource(
        change.model_copy(
            update={
                "spec": change.spec.model_copy(update={"deltas": (delta,)}),
                "digest": None,
            }
        )
    )
    with pytest.raises(CompilationError) as compiler_error:
        compile_supplier_change(snapshot, forged)
    assert compiler_error.value.reason_code == expected_code

    plan = _rebind_plan_to_change(compile_supplier_change(snapshot, change), forged)
    certificate = verify_plan(snapshot, forged, plan)  # type: ignore[arg-type]
    assert certificate.spec.verdict is Verdict.REJECT
    assert expected_code in _codes(certificate)


def test_duplicate_order_constraints_and_forged_reason_refs_are_rejected() -> None:
    snapshot, change = make_snapshot(), make_change()
    plan = compile_supplier_change(snapshot, change)
    edge = plan.spec.happens_before[0]

    duplicate = seal_resource(
        plan.model_copy(
            update={
                "spec": plan.spec.model_copy(
                    update={"happens_before": (*plan.spec.happens_before, edge)}
                ),
                "digest": None,
            }
        )
    )
    duplicate_certificate = verify_plan(snapshot, change, duplicate)
    assert duplicate_certificate.spec.verdict is Verdict.REJECT
    assert "ORDER_CONSTRAINT_INVALID" in _codes(duplicate_certificate)

    forged_edge = edge.model_copy(update={"reason_refs": ("urn:attacker:claim",)})
    forged_order = seal_resource(
        plan.model_copy(
            update={
                "spec": plan.spec.model_copy(
                    update={
                        "happens_before": tuple(
                            forged_edge if item == edge else item
                            for item in plan.spec.happens_before
                        )
                    }
                ),
                "digest": None,
            }
        )
    )
    reason_certificate = verify_plan(snapshot, change, forged_order)
    assert reason_certificate.spec.verdict is Verdict.REJECT
    assert "ORDER_CONSTRAINT_INVALID" in _codes(reason_certificate)


def test_work_unit_cannot_bind_obligations_to_an_unrelated_role() -> None:
    snapshot, change = make_snapshot(), make_change()
    plan = compile_supplier_change(snapshot, change)
    work = plan.spec.work_units[0]
    unrelated = next(
        role
        for role in plan.spec.role_instances
        if role.role_instance_id not in work.role_instance_refs
    )
    rebound_work = work.model_copy(
        update={
            "role_instance_refs": (unrelated.role_instance_id,),
            "accountable_role_instance_ref": unrelated.role_instance_id,
        }
    )
    forged = seal_resource(
        plan.model_copy(
            update={
                "spec": plan.spec.model_copy(
                    update={
                        "work_units": tuple(
                            rebound_work if item.work_unit_id == work.work_unit_id else item
                            for item in plan.spec.work_units
                        )
                    }
                ),
                "digest": None,
            }
        )
    )
    certificate = verify_plan(snapshot, change, forged)
    assert certificate.spec.verdict is Verdict.REJECT
    assert "RESPONSIBILITY_BINDING_INVALID" in _codes(certificate)


def test_dimension_witnesses_are_sorted_bounded_and_count_omissions() -> None:
    snapshot, change = make_snapshot(), make_change()
    plan = compile_supplier_change(snapshot, change)
    successor = plan.spec.work_units[0].work_unit_id
    dangling = tuple(
        OrderConstraint(
            predecessorRef=f"urn:attacker:work:{number:02}",
            successorRef=successor,
            reasonRefs=(f"urn:attacker:reason:{number:02}",),
        )
        for number in range(40)
    )
    forged = seal_resource(
        plan.model_copy(
            update={
                "spec": plan.spec.model_copy(
                    update={"happens_before": (*plan.spec.happens_before, *dangling)}
                ),
                "digest": None,
            }
        )
    )
    certificate = verify_plan(snapshot, change, forged)
    dimension = next(
        item for item in certificate.spec.dimensions if item.name == "order_constraints"
    )
    assert dimension.verdict.value == "FAIL"
    assert len(dimension.witnesses) == 32
    assert dimension.witnesses == tuple(sorted(dimension.witnesses))
    assert dimension.witnesses_omitted == 8


def test_certificate_envelope_and_annotation_status_refusal_are_wire_enforced() -> None:
    snapshot, change = make_snapshot(), make_change()
    plan = compile_supplier_change(snapshot, change)
    certificate = verify_plan(snapshot, change, plan)
    assert certificate.metadata.owner_ref == snapshot.metadata.owner_ref
    assert certificate.metadata.governance_ref == snapshot.metadata.governance_ref
    assert certificate.metadata.source_refs == (
        plan.metadata.id,
        snapshot.metadata.id,
        change.metadata.id,
    )

    broken_certificate = certificate.model_dump(mode="json", by_alias=True)
    broken_certificate["metadata"]["ownerRef"] = None
    with pytest.raises(OACValidationError) as certificate_error:
        parse_resource(broken_certificate)
    assert certificate_error.value.reason_code == "CORE_SCHEMA_INVALID"

    case_path = ROOT / "profiles/supplier-change/cases/SC-001-restructuring.json"
    case = parse_resource(case_path.read_bytes(), verify_digest=True)
    original_digest = case.digest
    tampered = json.loads(case_path.read_text(encoding="utf-8"))
    tampered["spec"]["annotationStatus"] = "plural"
    with pytest.raises(OACValidationError) as annotation_error:
        parse_resource(tampered)
    assert annotation_error.value.reason_code == "CORE_SCHEMA_INVALID"
    assert parse_resource(case_path.read_bytes(), verify_digest=True).digest == original_digest


def test_security_reason_codes_are_registered() -> None:
    assert {
        "RESOURCE_COHERENCE_VIOLATION",
        "CHANGE_OPERATION_INCONSISTENT",
        "SUPPLIER_STATUS_DELTA_SET_INVALID",
        "ORDER_CONSTRAINT_INVALID",
        "RESPONSIBILITY_BINDING_INVALID",
    }.issubset(REASON_CODE_REGISTRY)
