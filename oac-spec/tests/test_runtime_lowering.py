from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import rfc8785

from oac.canonical import OACValidationError, parse_resource, resource_ref, seal_resource
from oac.cli import main
from oac.lowering import lower_plan
from oac.models import (
    EffectCeiling,
    LoweringStatus,
    OrganizationPlan,
    OrganizationSnapshot,
    PlanCertificate,
    RuntimeBinding,
    RuntimeBindingSpec,
    RuntimeHandlerBinding,
    RuntimeRoleBinding,
    SemanticChangeSet,
    Verdict,
)
from oac.verifier import verify_plan

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / "profiles" / "supplier-change" / "inputs"
PLANS = (
    ROOT / "experiments" / "plan-verification-portability" / "v0.1-seed-1" / "artifacts" / "plans"
)


def _load(path: Path) -> object:
    return parse_resource(path.read_bytes(), verify_digest=True)


def _roots(case_id: str) -> tuple[OrganizationSnapshot, SemanticChangeSet]:
    snapshot_name = (
        "veracier-proc01-truncated-contextual.snapshot.json"
        if case_id == "SC-010"
        else "veracier-proc01-contextual.snapshot.json"
    )
    snapshot = _load(INPUTS / snapshot_name)
    change = _load(INPUTS / f"{case_id}.change.json")
    assert isinstance(snapshot, OrganizationSnapshot)
    assert isinstance(change, SemanticChangeSet)
    return snapshot, change


def _plan(name: str) -> OrganizationPlan:
    plan = _load(PLANS / f"{name}.plan.json")
    assert isinstance(plan, OrganizationPlan)
    return plan


def _handler_digest(obligation_type: str) -> str:
    return f"sha256:{hashlib.sha256(('handler:' + obligation_type).encode()).hexdigest()}"


def _binding(
    snapshot: OrganizationSnapshot,
    plan: OrganizationPlan,
    certificate: PlanCertificate,
) -> RuntimeBinding:
    obligation_by_ref = {item.obligation_id: item for item in plan.spec.obligations}
    evidence_by_type: dict[str, set[str]] = {}
    for obligation in plan.spec.obligations:
        evidence_by_type.setdefault(obligation.obligation_type, set()).update(
            obligation.required_evidence
        )

    role_bindings = []
    for role in plan.spec.role_instances:
        capabilities = {
            f"capability:{obligation_by_ref[ref].obligation_type}" for ref in role.obligation_refs
        }
        role_bindings.append(
            RuntimeRoleBinding(
                roleInstanceRef=role.role_instance_id,
                principalRef=role.principal_ref,
                runtimeSubject=f"runtime-subject:{role.principal_ref}",
                capabilityRefs=tuple(sorted(capabilities, key=str.encode)),
            )
        )
    handlers = tuple(
        RuntimeHandlerBinding(
            obligationType=obligation_type,
            handlerRef=f"urn:oac:zero-effect-handler:{obligation_type}",
            handlerDigest=_handler_digest(obligation_type),
            capabilityRef=f"capability:{obligation_type}",
            evidenceOutputRefs=tuple(sorted(evidence_by_type[obligation_type], key=str.encode)),
            effectCeiling=EffectCeiling.ZERO_EFFECT,
        )
        for obligation_type in sorted(evidence_by_type, key=str.encode)
    )
    binding = RuntimeBinding(
        metadata={
            "id": f"runtime-binding:{plan.digest}",
            "namespace": snapshot.metadata.namespace,
            "revision": 1,
            "ownerRef": snapshot.metadata.owner_ref,
            "governanceRef": snapshot.metadata.governance_ref,
            "createdAt": snapshot.metadata.created_at,
            "sourceRefs": (plan.metadata.id, certificate.metadata.id),
        },
        spec=RuntimeBindingSpec(
            subjectPlanRef=resource_ref(plan),
            subjectCertificateRef=resource_ref(certificate),
            roleBindings=tuple(role_bindings),
            handlerBindings=handlers,
            effectCeiling=EffectCeiling.ZERO_EFFECT,
            targetWrites=0,
        ),
    )
    return seal_resource(binding)


def _lower(
    snapshot: OrganizationSnapshot,
    change: SemanticChangeSet,
    plan: OrganizationPlan,
    *,
    certificate: PlanCertificate | None = None,
    binding: RuntimeBinding | None = None,
    admitted: bool = True,
):
    recomputed_certificate = verify_plan(snapshot, change, plan)
    certificate = certificate or recomputed_certificate
    binding = binding or _binding(snapshot, plan, recomputed_certificate)
    allowlist = {binding.digest} if admitted else set()
    return lower_plan(
        snapshot,
        change,
        plan,
        certificate,
        binding,
        admitted_binding_digests=allowlist,
    )


def _codes(result: object) -> set[str]:
    return set(result.receipt.spec.reason_codes)  # type: ignore[attr-defined]


def test_plural_accept_plans_produce_distinct_deterministic_serial_bundles() -> None:
    snapshot, change = _roots("SC-008")
    base = _plan("PV-POS-SC008-BASE")
    split = _plan("PV-POS-SC008-SPLIT")

    base_result = _lower(snapshot, change, base)
    split_result = _lower(snapshot, change, split)
    repeated = _lower(snapshot, change, base)

    assert base_result.receipt.spec.status is LoweringStatus.PRODUCED
    assert split_result.receipt.spec.status is LoweringStatus.PRODUCED
    assert base_result.bundle is not None
    assert split_result.bundle is not None
    assert repeated.bundle is not None
    assert len(base_result.bundle.spec.steps) == 3
    assert len(split_result.bundle.spec.steps) == 4
    assert (
        base_result.bundle.spec.obligation_contract_digest
        == split_result.bundle.spec.obligation_contract_digest
    )
    assert base_result.bundle.spec.topology_digest != split_result.bundle.spec.topology_digest
    assert base_result.bundle.digest == repeated.bundle.digest
    assert base_result.receipt.digest == repeated.receipt.digest
    assert rfc8785.dumps(base_result.as_json()) == rfc8785.dumps(repeated.as_json())
    assert base_result.bundle.spec.target_writes == 0
    assert base_result.bundle.spec.requires_runtime_admission is True
    assert all(step.target_writes == 0 for step in base_result.bundle.spec.steps)
    assert base_result.receipt.spec.runtime_invoked is False
    work_by_ref = {work.work_unit_id: work for work in base.spec.work_units}
    role_by_ref = {role.role_instance_id: role for role in base.spec.role_instances}
    obligation_by_ref = {
        obligation.obligation_id: obligation for obligation in base.spec.obligations
    }
    for step in base_result.bundle.spec.steps:
        work = work_by_ref[step.work_unit_ref]
        assert tuple(item.role_instance_ref for item in step.role_bindings) == tuple(
            sorted(work.role_instance_refs, key=str.encode)
        )
        assert step.accountable.role_instance_ref == work.accountable_role_instance_ref
        assert (
            step.accountable.principal_ref
            == role_by_ref[work.accountable_role_instance_ref].principal_ref
        )
        assert step.obligation_refs == tuple(sorted(work.obligation_refs, key=str.encode))
        assert step.evidence_output_refs == tuple(sorted(work.evidence_outputs, key=str.encode))
        assert tuple(item.obligation_ref for item in step.handler_bindings) == tuple(
            sorted(work.obligation_refs, key=str.encode)
        )
        for handler in step.handler_bindings:
            assert (
                handler.obligation_type == obligation_by_ref[handler.obligation_ref].obligation_type
            )


@pytest.mark.parametrize(
    ("case_id", "plan_name", "verdict"),
    [
        ("SC-009", "PV-POS-SC009-PROVISIONAL", Verdict.PROVISIONAL),
        ("SC-010", "PV-POS-SC010-UNKNOWN", Verdict.UNKNOWN),
        ("SC-008", "PV-NEG-MISSING-EVIDENCE", Verdict.REJECT),
    ],
)
def test_non_accept_verdicts_are_blocked_without_bundle(
    case_id: str, plan_name: str, verdict: Verdict
) -> None:
    snapshot, change = _roots(case_id)
    plan = _plan(plan_name)
    certificate = verify_plan(snapshot, change, plan)
    assert certificate.spec.verdict is verdict
    result = _lower(snapshot, change, plan, certificate=certificate)
    assert result.receipt.spec.status is LoweringStatus.BLOCKED
    assert result.bundle is None
    assert result.receipt.spec.bundle_ref is None
    assert "LOWERING_VERDICT_NOT_ACCEPT" in _codes(result)
    assert result.receipt.spec.runtime_invoked is False


def test_resealed_accept_certificate_is_recomputed_and_blocked() -> None:
    snapshot, change = _roots("SC-008")
    plan = _plan("PV-POS-SC008-BASE")
    valid = verify_plan(snapshot, change, plan)
    forged_spec = valid.spec.model_copy(update={"certifier_id": "attacker.verifier"})
    forged = seal_resource(valid.model_copy(update={"spec": forged_spec, "digest": None}))
    assert forged.spec.verdict is Verdict.ACCEPT

    result = _lower(snapshot, change, plan, certificate=forged)
    assert result.bundle is None
    assert _codes(result) == {"LOWERING_CERTIFICATE_MISMATCH"}
    assert result.receipt.spec.supplied_certificate_ref.digest == forged.digest
    assert result.receipt.spec.recomputed_certificate_ref.digest == valid.digest


def test_certificate_for_another_accepted_plan_cannot_be_reused() -> None:
    snapshot, change = _roots("SC-008")
    base = _plan("PV-POS-SC008-BASE")
    split = _plan("PV-POS-SC008-SPLIT")
    result = _lower(
        snapshot,
        change,
        split,
        certificate=verify_plan(snapshot, change, base),
    )
    assert result.bundle is None
    assert "LOWERING_CERTIFICATE_MISMATCH" in _codes(result)


def test_runtime_binding_pins_the_exact_recomputed_certificate() -> None:
    snapshot, change = _roots("SC-008")
    base = _plan("PV-POS-SC008-BASE")
    split = _plan("PV-POS-SC008-SPLIT")
    base_certificate = verify_plan(snapshot, change, base)
    split_certificate = verify_plan(snapshot, change, split)
    binding = _binding(snapshot, base, base_certificate)
    assert binding.spec.subject_certificate_ref == resource_ref(base_certificate)

    stale_spec = binding.spec.model_copy(
        update={"subject_certificate_ref": resource_ref(split_certificate)}
    )
    stale_metadata = binding.metadata.model_copy(
        update={"source_refs": (base.metadata.id, split_certificate.metadata.id)}
    )
    stale_binding = seal_resource(
        binding.model_copy(update={"metadata": stale_metadata, "spec": stale_spec, "digest": None})
    )
    result = _lower(snapshot, change, base, binding=stale_binding)
    assert result.bundle is None
    assert _codes(result) == {"LOWERING_BINDING_ROOT_MISMATCH"}


def test_runner_owned_binding_admission_is_mandatory() -> None:
    snapshot, change = _roots("SC-008")
    plan = _plan("PV-POS-SC008-BASE")
    result = _lower(snapshot, change, plan, admitted=False)
    assert result.bundle is None
    assert _codes(result) == {"LOWERING_BINDING_NOT_ADMITTED"}


@pytest.mark.parametrize(
    ("input_index", "expected_reason"),
    (
        (0, "LOWERING_INPUT_DIGEST_INVALID"),
        (1, "LOWERING_INPUT_DIGEST_INVALID"),
        (2, "LOWERING_INPUT_DIGEST_INVALID"),
        (3, "LOWERING_INPUT_DIGEST_INVALID"),
        (4, "LOWERING_BINDING_DIGEST_INVALID"),
    ),
)
@pytest.mark.parametrize("failure_mode", ("claimed-alias", "missing"))
def test_all_sealed_input_digests_are_admitted_before_receipt(
    input_index: int,
    expected_reason: str,
    failure_mode: str,
) -> None:
    snapshot, change = _roots("SC-008")
    plan = _plan("PV-POS-SC008-BASE")
    certificate = verify_plan(snapshot, change, plan)
    binding = _binding(snapshot, plan, certificate)
    resources = [snapshot, change, plan, certificate, binding]
    original = resources[input_index]
    if failure_mode == "claimed-alias":
        changed_metadata = original.metadata.model_copy(
            update={"revision": original.metadata.revision + 1000}
        )
        mutated = original.model_copy(update={"metadata": changed_metadata})
        assert mutated.digest == original.digest
        assert rfc8785.dumps(mutated.model_dump(mode="json", by_alias=True)) != rfc8785.dumps(
            original.model_dump(mode="json", by_alias=True)
        )
    else:
        mutated = original.model_copy(update={"digest": None})
    resources[input_index] = mutated

    with pytest.raises(OACValidationError) as exc_info:
        lower_plan(
            *resources,
            admitted_binding_digests={binding.digest},
        )
    assert exc_info.value.reason_code == expected_reason
    assert str(exc_info.value) == (
        f"{original.kind} detached digest is missing or does not match its canonical projection"
    )


def test_missing_role_principal_or_capability_blocks_lowering() -> None:
    snapshot, change = _roots("SC-008")
    plan = _plan("PV-POS-SC008-BASE")
    binding = _binding(snapshot, plan, verify_plan(snapshot, change, plan))

    missing_role_spec = binding.spec.model_copy(
        update={"role_bindings": binding.spec.role_bindings[1:]}
    )
    missing_role = seal_resource(
        binding.model_copy(update={"spec": missing_role_spec, "digest": None})
    )
    assert "LOWERING_BINDING_INCOMPLETE" in _codes(
        _lower(snapshot, change, plan, binding=missing_role)
    )

    wrong_principal_role = binding.spec.role_bindings[0].model_copy(
        update={"principal_ref": "principal:not-selected"}
    )
    wrong_principal_spec = binding.spec.model_copy(
        update={"role_bindings": (wrong_principal_role, *binding.spec.role_bindings[1:])}
    )
    wrong_principal = seal_resource(
        binding.model_copy(update={"spec": wrong_principal_spec, "digest": None})
    )
    assert "LOWERING_PRINCIPAL_MISMATCH" in _codes(
        _lower(snapshot, change, plan, binding=wrong_principal)
    )

    target_index = next(
        index for index, item in enumerate(binding.spec.role_bindings) if item.capability_refs
    )
    target = binding.spec.role_bindings[target_index].model_copy(update={"capability_refs": ()})
    missing_capability_spec = binding.spec.model_copy(
        update={
            "role_bindings": tuple(
                target if index == target_index else item
                for index, item in enumerate(binding.spec.role_bindings)
            )
        }
    )
    missing_capability = seal_resource(
        binding.model_copy(update={"spec": missing_capability_spec, "digest": None})
    )
    assert "LOWERING_CAPABILITY_MISSING" in _codes(
        _lower(snapshot, change, plan, binding=missing_capability)
    )


def test_extra_admin_capability_is_blocked_by_exact_role_capability_set() -> None:
    snapshot, change = _roots("SC-008")
    plan = _plan("PV-POS-SC008-BASE")
    binding = _binding(snapshot, plan, verify_plan(snapshot, change, plan))
    target = binding.spec.role_bindings[0].model_copy(
        update={
            "capability_refs": (
                *binding.spec.role_bindings[0].capability_refs,
                "capability:admin",
            )
        }
    )
    forged_spec = binding.spec.model_copy(
        update={"role_bindings": (target, *binding.spec.role_bindings[1:])}
    )
    forged = seal_resource(binding.model_copy(update={"spec": forged_spec, "digest": None}))
    result = _lower(snapshot, change, plan, binding=forged)
    assert result.bundle is None
    assert _codes(result) == {"LOWERING_CAPABILITY_SET_MISMATCH"}


def test_different_principals_cannot_collapse_to_one_runtime_subject() -> None:
    snapshot, change = _roots("SC-008")
    plan = _plan("PV-POS-SC008-BASE")
    binding = _binding(snapshot, plan, verify_plan(snapshot, change, plan))
    first = binding.spec.role_bindings[0]
    target_index = next(
        index
        for index, item in enumerate(binding.spec.role_bindings[1:], start=1)
        if item.principal_ref != first.principal_ref
    )
    collapsed = binding.spec.role_bindings[target_index].model_copy(
        update={"runtime_subject": first.runtime_subject}
    )
    collapsed_spec = binding.spec.model_copy(
        update={
            "role_bindings": tuple(
                collapsed if index == target_index else item
                for index, item in enumerate(binding.spec.role_bindings)
            )
        }
    )
    collapsed_binding = seal_resource(
        binding.model_copy(update={"spec": collapsed_spec, "digest": None})
    )
    result = _lower(snapshot, change, plan, binding=collapsed_binding)
    assert result.bundle is None
    assert "LOWERING_RUNTIME_SUBJECT_COLLISION" in _codes(result)


def test_handler_evidence_and_capability_contract_is_exact() -> None:
    snapshot, change = _roots("SC-008")
    plan = _plan("PV-POS-SC008-BASE")
    binding = _binding(snapshot, plan, verify_plan(snapshot, change, plan))
    handler = binding.spec.handler_bindings[0]
    forged_handler = handler.model_copy(
        update={"evidence_output_refs": (*handler.evidence_output_refs, "evidence:extra")}
    )
    forged_spec = binding.spec.model_copy(
        update={"handler_bindings": (forged_handler, *binding.spec.handler_bindings[1:])}
    )
    forged = seal_resource(binding.model_copy(update={"spec": forged_spec, "digest": None}))
    result = _lower(snapshot, change, plan, binding=forged)
    assert result.bundle is None
    assert "LOWERING_HANDLER_INCOMPLETE" in _codes(result)


def test_effect_expansion_is_rejected_at_runtime_binding_admission() -> None:
    snapshot, change = _roots("SC-008")
    plan = _plan("PV-POS-SC008-BASE")
    value = _binding(snapshot, plan, verify_plan(snapshot, change, plan)).model_dump(
        mode="json", by_alias=True
    )
    value["spec"]["effectCeiling"] = "read_write"
    value["digest"] = None
    with pytest.raises(OACValidationError) as exc_info:
        parse_resource(json.dumps(value))
    assert exc_info.value.reason_code == "CORE_SCHEMA_INVALID"


@pytest.mark.parametrize("forbidden_field", ["prompt", "command", "credential"])
def test_runtime_binding_has_no_executable_or_secret_payload_slot(
    forbidden_field: str,
) -> None:
    snapshot, change = _roots("SC-008")
    plan = _plan("PV-POS-SC008-BASE")
    value = _binding(snapshot, plan, verify_plan(snapshot, change, plan)).model_dump(
        mode="json", by_alias=True
    )
    value["spec"]["handlerBindings"][0][forbidden_field] = "forbidden"
    value["digest"] = None
    with pytest.raises(OACValidationError) as exc_info:
        parse_resource(json.dumps(value))
    assert exc_info.value.reason_code == "CORE_SCHEMA_INVALID"


def test_reordered_bundle_steps_fail_semantic_admission() -> None:
    snapshot, change = _roots("SC-008")
    plan = _plan("PV-POS-SC008-BASE")
    result = _lower(snapshot, change, plan)
    assert result.bundle is not None
    value = result.bundle.model_dump(mode="json", by_alias=True)
    value["spec"]["steps"][0], value["spec"]["steps"][1] = (
        value["spec"]["steps"][1],
        value["spec"]["steps"][0],
    )
    value["digest"] = None
    with pytest.raises(OACValidationError) as exc_info:
        parse_resource(json.dumps(value))
    assert exc_info.value.reason_code == "CORE_SCHEMA_INVALID"


def test_topologically_valid_ready_tie_reordering_is_still_noncanonical() -> None:
    snapshot, change = _roots("SC-008")
    plan = _plan("PV-POS-SC008-SPLIT")
    result = _lower(snapshot, change, plan)
    assert result.bundle is not None
    value = result.bundle.model_dump(mode="json", by_alias=True)
    first_ready, second_ready = value["spec"]["steps"][1:3]
    assert first_ready["predecessorRefs"] == second_ready["predecessorRefs"]
    first_ready["stepIndex"], second_ready["stepIndex"] = 2, 1
    value["spec"]["steps"][1:3] = second_ready, first_ready
    value["digest"] = None
    with pytest.raises(OACValidationError) as exc_info:
        parse_resource(json.dumps(value))
    assert exc_info.value.reason_code == "CORE_SCHEMA_INVALID"


def test_accepted_plan_projection_holes_block_without_bundle_or_exception() -> None:
    snapshot, change = _roots("SC-008")
    plan = _plan("PV-POS-SC008-BASE")
    first_work = plan.spec.work_units[0]
    mutations = (
        {
            "evidence_outputs": (
                *first_work.evidence_outputs,
                "evidence:uncontracted-extra",
            )
        },
        {
            "role_instance_refs": (
                *first_work.role_instance_refs,
                first_work.role_instance_refs[0],
            )
        },
    )
    for update in mutations:
        mutated_work = first_work.model_copy(update=update)
        mutated_spec = plan.spec.model_copy(
            update={"work_units": (mutated_work, *plan.spec.work_units[1:])}
        )
        mutated_plan = seal_resource(plan.model_copy(update={"spec": mutated_spec, "digest": None}))
        certificate = verify_plan(snapshot, change, mutated_plan)
        assert certificate.spec.verdict is Verdict.ACCEPT
        result = _lower(
            snapshot,
            change,
            mutated_plan,
            certificate=certificate,
        )
        assert result.bundle is None
        assert _codes(result) == {"LOWERING_PLAN_PROJECTION_UNSUPPORTED"}


def test_topology_digest_canonicalizes_nested_set_like_evidence_order() -> None:
    snapshot, change = _roots("SC-008")
    plan = _plan("PV-POS-SC008-BASE")
    baseline = _lower(snapshot, change, plan)
    assert baseline.bundle is not None
    work_index = next(
        index for index, work in enumerate(plan.spec.work_units) if len(work.evidence_outputs) > 1
    )
    work = plan.spec.work_units[work_index]
    reordered_work = work.model_copy(
        update={"evidence_outputs": tuple(reversed(work.evidence_outputs))}
    )
    reordered_spec = plan.spec.model_copy(
        update={
            "work_units": tuple(
                reordered_work if index == work_index else item
                for index, item in enumerate(plan.spec.work_units)
            )
        }
    )
    reordered_plan = seal_resource(plan.model_copy(update={"spec": reordered_spec, "digest": None}))
    certificate = verify_plan(snapshot, change, reordered_plan)
    assert certificate.spec.verdict is Verdict.ACCEPT
    reordered = _lower(
        snapshot,
        change,
        reordered_plan,
        certificate=certificate,
    )
    assert reordered.bundle is not None
    assert reordered_plan.digest != plan.digest
    assert reordered.bundle.spec.topology_digest == baseline.bundle.spec.topology_digest
    assert (
        reordered.bundle.spec.obligation_contract_digest
        == baseline.bundle.spec.obligation_contract_digest
    )


def test_runtime_resource_envelopes_reject_wrong_refs_namespaces_and_sources() -> None:
    snapshot, change = _roots("SC-008")
    plan = _plan("PV-POS-SC008-BASE")
    certificate = verify_plan(snapshot, change, plan)
    binding = _binding(snapshot, plan, certificate)
    result = _lower(
        snapshot,
        change,
        plan,
        certificate=certificate,
        binding=binding,
    )
    assert result.bundle is not None

    invalid_resources: list[dict[str, object]] = []
    wrong_binding_kind = binding.model_dump(mode="json", by_alias=True)
    wrong_binding_kind["spec"]["subjectCertificateRef"]["kind"] = "OrganizationPlan"
    invalid_resources.append(wrong_binding_kind)
    binding_sources = binding.model_dump(mode="json", by_alias=True)
    binding_sources["metadata"]["sourceRefs"].reverse()
    invalid_resources.append(binding_sources)

    wrong_bundle_kind = result.bundle.model_dump(mode="json", by_alias=True)
    wrong_bundle_kind["spec"]["certificateRef"]["kind"] = "OrganizationPlan"
    invalid_resources.append(wrong_bundle_kind)
    bundle_namespace = result.bundle.model_dump(mode="json", by_alias=True)
    bundle_namespace["spec"]["runtimeBindingRef"]["namespace"] = "tenant:other"
    invalid_resources.append(bundle_namespace)
    bundle_sources = result.bundle.model_dump(mode="json", by_alias=True)
    bundle_sources["metadata"]["sourceRefs"].reverse()
    invalid_resources.append(bundle_sources)

    wrong_receipt_bundle_kind = result.receipt.model_dump(mode="json", by_alias=True)
    wrong_receipt_bundle_kind["spec"]["bundleRef"]["kind"] = "OrganizationPlan"
    invalid_resources.append(wrong_receipt_bundle_kind)
    receipt_sources = result.receipt.model_dump(mode="json", by_alias=True)
    receipt_sources["metadata"]["sourceRefs"].reverse()
    invalid_resources.append(receipt_sources)

    for value in invalid_resources:
        value["digest"] = None
        with pytest.raises(OACValidationError) as exc_info:
            parse_resource(json.dumps(value))
        assert exc_info.value.reason_code == "CORE_SCHEMA_INVALID"


def test_kahn_order_uses_utf8_byte_order_and_projects_exact_predecessors() -> None:
    snapshot, change = _roots("SC-008")
    plan = _plan("PV-POS-SC008-BASE")
    result = _lower(snapshot, change, plan)
    assert result.bundle is not None
    steps = result.bundle.spec.steps
    work_refs = {item.work_unit_id for item in plan.spec.work_units}
    predecessors = {
        ref: {
            edge.predecessor_ref for edge in plan.spec.happens_before if edge.successor_ref == ref
        }
        for ref in work_refs
    }
    emitted: set[str] = set()
    for index, step in enumerate(steps):
        ready = sorted(
            (ref for ref in work_refs - emitted if predecessors[ref] <= emitted),
            key=str.encode,
        )
        assert step.step_index == index
        assert step.work_unit_ref == ready[0]
        assert step.predecessor_refs == tuple(
            sorted(predecessors[step.work_unit_ref], key=str.encode)
        )
        emitted.add(step.work_unit_ref)


def test_lower_cli_emits_one_auditable_json_result(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    snapshot, change = _roots("SC-008")
    plan = _plan("PV-POS-SC008-BASE")
    certificate = verify_plan(snapshot, change, plan)
    binding = _binding(snapshot, plan, certificate)
    resources = {
        "snapshot": snapshot,
        "change": change,
        "plan": plan,
        "certificate": certificate,
        "binding": binding,
    }
    paths: dict[str, Path] = {}
    for name, resource in resources.items():
        path = tmp_path / f"{name}.json"
        path.write_bytes(rfc8785.dumps(resource.model_dump(mode="json", by_alias=True)) + b"\n")
        paths[name] = path
    exit_code = main(
        [
            "lower",
            str(paths["snapshot"]),
            str(paths["change"]),
            str(paths["plan"]),
            str(paths["certificate"]),
            str(paths["binding"]),
            "--admit-binding-digest",
            str(binding.digest),
        ]
    )
    assert exit_code == 0
    output = json.loads(capfd.readouterr().out)
    assert output["receipt"]["spec"]["status"] == "PRODUCED"
    assert output["bundle"]["spec"]["requiresRuntimeAdmission"] is True


def test_blocked_lower_cli_is_exit_zero_and_contains_no_bundle(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    snapshot, change = _roots("SC-009")
    plan = _plan("PV-POS-SC009-PROVISIONAL")
    certificate = verify_plan(snapshot, change, plan)
    binding = _binding(snapshot, plan, certificate)
    resources = (snapshot, change, plan, certificate, binding)
    paths = []
    for index, resource in enumerate(resources):
        path = tmp_path / f"resource-{index}.json"
        path.write_bytes(rfc8785.dumps(resource.model_dump(mode="json", by_alias=True)) + b"\n")
        paths.append(path)
    assert (
        main(
            [
                "lower",
                *(str(path) for path in paths),
                "--admit-binding-digest",
                str(binding.digest),
            ]
        )
        == 0
    )
    output = json.loads(capfd.readouterr().out)
    assert output["receipt"]["spec"]["status"] == "BLOCKED"
    assert "bundle" not in output
    assert output["receipt"]["spec"]["runtimeInvoked"] is False


def test_accepted_but_unprojectable_plan_is_cli_blocked_not_crashed(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    snapshot, change = _roots("SC-008")
    plan = _plan("PV-POS-SC008-BASE")
    first_work = plan.spec.work_units[0]
    mutated_work = first_work.model_copy(
        update={
            "evidence_outputs": (
                *first_work.evidence_outputs,
                "evidence:uncontracted-extra",
            )
        }
    )
    mutated_plan = seal_resource(
        plan.model_copy(
            update={
                "spec": plan.spec.model_copy(
                    update={"work_units": (mutated_work, *plan.spec.work_units[1:])}
                ),
                "digest": None,
            }
        )
    )
    certificate = verify_plan(snapshot, change, mutated_plan)
    assert certificate.spec.verdict is Verdict.ACCEPT
    binding = _binding(snapshot, mutated_plan, certificate)
    resources = (snapshot, change, mutated_plan, certificate, binding)
    paths = []
    for index, resource in enumerate(resources):
        path = tmp_path / f"projection-resource-{index}.json"
        path.write_bytes(rfc8785.dumps(resource.model_dump(mode="json", by_alias=True)) + b"\n")
        paths.append(path)
    assert (
        main(
            [
                "lower",
                *(str(path) for path in paths),
                "--admit-binding-digest",
                str(binding.digest),
            ]
        )
        == 0
    )
    output = json.loads(capfd.readouterr().out)
    assert output["receipt"]["spec"]["status"] == "BLOCKED"
    assert output["receipt"]["spec"]["reasonCodes"] == ["LOWERING_PLAN_PROJECTION_UNSUPPORTED"]
    assert "bundle" not in output


@pytest.mark.parametrize(
    ("input_index", "expected_reason"),
    (
        (0, "LOWERING_INPUT_DIGEST_INVALID"),
        (1, "LOWERING_INPUT_DIGEST_INVALID"),
        (2, "LOWERING_INPUT_DIGEST_INVALID"),
        (3, "LOWERING_INPUT_DIGEST_INVALID"),
        (4, "LOWERING_BINDING_DIGEST_INVALID"),
    ),
)
@pytest.mark.parametrize("failure_mode", ("claimed-alias", "missing"))
def test_all_sealed_input_digest_failures_are_cli_admission_errors(
    input_index: int,
    expected_reason: str,
    failure_mode: str,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    snapshot, change = _roots("SC-008")
    plan = _plan("PV-POS-SC008-BASE")
    certificate = verify_plan(snapshot, change, plan)
    binding = _binding(snapshot, plan, certificate)
    resources = [
        resource.model_dump(mode="json", by_alias=True)
        for resource in (snapshot, change, plan, certificate, binding)
    ]
    original_bytes = rfc8785.dumps(resources[input_index])
    if failure_mode == "claimed-alias":
        resources[input_index]["metadata"]["revision"] += 1000
        assert resources[input_index]["digest"] is not None
        assert rfc8785.dumps(resources[input_index]) != original_bytes
    else:
        resources[input_index]["digest"] = None
    paths = []
    for index, resource in enumerate(resources):
        path = tmp_path / f"invalid-digest-resource-{index}.json"
        path.write_bytes(rfc8785.dumps(resource) + b"\n")
        paths.append(path)
    assert (
        main(
            [
                "lower",
                *(str(path) for path in paths),
                "--admit-binding-digest",
                str(binding.digest),
            ]
        )
        == 2
    )
    captured = capfd.readouterr()
    assert captured.out == ""
    error = json.loads(captured.err)
    assert error["reasonCode"] == expected_reason
    assert error["message"] == (
        f"{resources[input_index]['kind']} detached digest is missing or does not match its canonical projection"
    )


def test_g1_machine_summary_is_frozen() -> None:
    cases = (
        ("BASE", "SC-008", "PV-POS-SC008-BASE"),
        ("SPLIT", "SC-008", "PV-POS-SC008-SPLIT"),
        ("PROVISIONAL", "SC-009", "PV-POS-SC009-PROVISIONAL"),
        ("UNKNOWN", "SC-010", "PV-POS-SC010-UNKNOWN"),
        ("REJECT", "SC-008", "PV-NEG-MISSING-EVIDENCE"),
    )
    actual: dict[str, object] = {
        "apiVersion": "oac.runtime-lowering.summary/v0.1",
        "cases": [],
        "scope": "G1a-local-zero-effect-lowering-only",
    }
    output_cases = actual["cases"]
    assert isinstance(output_cases, list)
    for label, case_id, plan_name in cases:
        snapshot, change = _roots(case_id)
        plan = _plan(plan_name)
        certificate = verify_plan(snapshot, change, plan)
        binding = _binding(snapshot, plan, certificate)
        result = _lower(
            snapshot,
            change,
            plan,
            certificate=certificate,
            binding=binding,
        )
        item = {
            "id": label,
            "planDigest": plan.digest,
            "certificateDigest": certificate.digest,
            "runtimeBindingDigest": binding.digest,
            "receiptDigest": result.receipt.digest,
            "status": result.receipt.spec.status.value,
            "reasonCodes": list(result.receipt.spec.reason_codes),
            "stepCount": len(result.bundle.spec.steps) if result.bundle else 0,
        }
        if result.bundle is not None:
            item.update(
                bundleDigest=result.bundle.digest,
                obligationContractDigest=(result.bundle.spec.obligation_contract_digest),
                topologyDigest=result.bundle.spec.topology_digest,
            )
        output_cases.append(item)
    expected = json.loads(
        (ROOT / "tests" / "fixtures" / "runtime-lowering" / "g1-summary.json").read_bytes()
    )
    assert actual == expected
