from __future__ import annotations

from collections.abc import Callable, Iterator
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.store import StateStore
from orgrebase.workspace import evolution_runtime
from orgrebase.workspace.evolution_contracts import (
    ExecutionEvidence,
    ExecutionStatus,
    OACExecutionApproval,
    OACExecutionReceipt,
    OutcomeVerdict,
)
from orgrebase.workspace.evolution_runtime import (
    ZeroEffectOACExecutor,
    program_policy_digest,
)
from orgrebase.workspace.models import (
    OACRuntimeApproval,
    OACRuntimeCapsule,
    RuntimeAdmissionReceipt,
)
from orgrebase.workspace.oac_bridge import (
    OACRuntimeAdmissionBridge,
    _jcs_digest,
    _oac_projection,
    _resource_ref,
)
from orgrebase.workspace.outcome_assurance import (
    ControlledOutcomeObservationProducer,
    ControlledProcessOutcomeAssurance,
    controlled_reference_actual_values,
)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[StateStore]:
    value = StateStore(tmp_path / "oac-governed-evolution.sqlite")
    try:
        yield value
    finally:
        value.close()


def _reseal(resource: dict[str, Any]) -> dict[str, Any]:
    resource["digest"] = _jcs_digest(_oac_projection(resource))
    return resource


def _mutate_runtime_bundle(
    capsule: OACRuntimeCapsule,
    mutator: Callable[[dict[str, Any]], None],
) -> OACRuntimeCapsule:
    payload = capsule.model_dump(mode="json")
    payload.pop("digest")
    bundle = deepcopy(payload["runtime_bundle"])
    mutator(bundle)
    payload["runtime_bundle"] = _reseal(bundle)

    lowering_receipt = deepcopy(payload["runtime_lowering_receipt"])
    lowering_receipt["spec"]["bundleRef"] = _resource_ref(bundle)
    payload["runtime_lowering_receipt"] = _reseal(lowering_receipt)
    return OACRuntimeCapsule.model_validate(payload)


def _wire_ref(kind: str, resource_id: str) -> dict[str, Any]:
    return {
        "apiVersion": "oac.dev/v0alpha1",
        "kind": kind,
        "namespace": "oac.examples.supplier",
        "resourceId": resource_id,
        "revision": 1,
        "digest": sha256_digest({"kind": kind, "resource_id": resource_id}),
    }


def _demand_wire(capsule: OACRuntimeCapsule) -> dict[str, Any]:
    demand = {
        "apiVersion": "oac.dev/v0alpha1",
        "kind": "OrganizationalDemand",
        "metadata": {
            "id": "demand:SC-008",
            "namespace": "oac.examples.supplier",
            "revision": 1,
            "ownerRef": "role:procurement-owner",
            "governanceRef": "policy:oac-shadow-zero-effect",
            "createdAt": "2026-08-26T00:00:00Z",
            "sourceRefs": [
                "alternative:acieries-savoie",
                "change:SC-008",
                "snapshot:veracier-proc01-contextual",
            ],
        },
        "spec": {
            "snapshotRef": _resource_ref(capsule.snapshot),
            "requesterPrincipalRef": "principal:procurement-agent",
            "accountableRoleRef": "role:procurement-owner",
            "objective": "objective:review-supplier-status-with-zero-effects",
            "subjectRefs": [_wire_ref("OrganizationSubject", "alternative:acieries-savoie")],
            "triggerRefs": [_resource_ref(capsule.change)],
            "desiredOutcomeRefs": [_wire_ref("OutcomeCriterion", "criterion:controlled-review-complete")],
            "evidenceObligationRefs": [
                _wire_ref("EvidenceObligation", "obligation:controlled-review-evidence")
            ],
            "constraintRefs": [],
            "priority": 1,
            "effectCeiling": "zero_effect",
        },
    }
    demand["digest"] = _jcs_digest(_oac_projection(demand))
    return demand


def _source_admission_wire(capsule: OACRuntimeCapsule) -> dict[str, Any]:
    source = {
        "apiVersion": "oac.dev/v0alpha1",
        "kind": "SourceAdmissionReceipt",
        "metadata": {
            "id": "source-admission:test-sc008",
            "namespace": "oac.examples.supplier",
            "revision": 1,
            "ownerRef": "role:workspace-owner",
            "governanceRef": "policy:oac-shadow-zero-effect",
            "createdAt": "2026-08-26T00:00:00Z",
            "sourceRefs": [capsule.snapshot["metadata"]["id"]],
        },
        "spec": {
            "verdict": "ADMITTED",
            "unresolvedRefs": [],
            "admittedSubjectRefs": [_resource_ref(capsule.snapshot)],
        },
    }
    return _reseal(source)


def _admit(
    store: StateStore,
    capsule: OACRuntimeCapsule,
    *,
    command_suffix: str,
) -> tuple[OACRuntimeApproval, RuntimeAdmissionReceipt, str]:
    bridge = OACRuntimeAdmissionBridge(store)
    preview = bridge.prepare(capsule)
    formation_approval = bridge.approve(
        preview,
        actor_id=preview.runtime_owner_id,
        preview_digest=preview.digest,
        command_id=f"formation-{command_suffix}",
        approved_at="2026-08-26T00:00:00Z",
    )
    admission = bridge.apply(capsule, preview, formation_approval)
    return formation_approval, admission, preview.runtime_owner_id


def _execution_approval(
    capsule: OACRuntimeCapsule,
    source_admission: dict[str, Any],
    demand: dict[str, Any],
    admission: RuntimeAdmissionReceipt,
    runtime_owner_id: str,
    *,
    command_suffix: str,
) -> OACExecutionApproval:
    command_id = f"execute-{command_suffix}"
    return OACExecutionApproval(
        approval_id=f"oac-execution-approval:{command_id}",
        command_id=command_id,
        capsule_digest=capsule.digest,
        runtime_admission_digest=admission.digest,
        source_admission_ref=source_admission["metadata"]["id"],
        source_admission_digest=source_admission["digest"],
        demand_ref=demand["metadata"]["id"],
        demand_digest=demand["digest"],
        runtime_bundle_digest=capsule.runtime_bundle["digest"],
        program_policy_digest=program_policy_digest(),
        runtime_owner_id=runtime_owner_id,
        approved_at="2026-08-26T00:00:30Z",
    )


def _prepare_execution(
    store: StateStore,
    capsule: OACRuntimeCapsule,
    *,
    command_suffix: str,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    OACRuntimeApproval,
    RuntimeAdmissionReceipt,
    OACExecutionApproval,
]:
    formation_approval, admission, runtime_owner = _admit(store, capsule, command_suffix=command_suffix)
    source_admission = _source_admission_wire(capsule)
    demand = _demand_wire(capsule)
    execution_approval = _execution_approval(
        capsule,
        source_admission,
        demand,
        admission,
        runtime_owner,
        command_suffix=command_suffix,
    )
    return demand, source_admission, formation_approval, admission, execution_approval


def _execute(
    store: StateStore,
    capsule: OACRuntimeCapsule,
    *,
    run_id: str,
) -> tuple[OACExecutionReceipt, tuple[ExecutionEvidence, ...]]:
    demand, source, _, admission, approval = _prepare_execution(store, capsule, command_suffix=run_id)
    return ZeroEffectOACExecutor(store).execute(
        capsule,
        demand,
        admission,
        approval,
        source_admission=source,
        run_id=run_id,
    )


def test_formation_approval_cannot_authorize_handler_execution(
    store: StateStore,
    current_oac_capsules: dict[str, OACRuntimeCapsule],
) -> None:
    capsule = current_oac_capsules["BASE"]
    demand, source, formation_approval, admission, execution_approval = _prepare_execution(
        store, capsule, command_suffix="approval-boundary"
    )

    assert formation_approval.scope == "LOCAL_FORMATION_CONTROL_PLANE_ONLY"
    assert execution_approval.scope == "ZERO_EFFECT_HANDLER_EXECUTION"
    assert formation_approval.digest != execution_approval.digest
    assert execution_approval.runtime_admission_digest == admission.digest

    with pytest.raises(IntegrityError, match="OAC_EXECUTION_APPROVAL_MODEL_INVALID"):
        ZeroEffectOACExecutor(store).execute(
            capsule,
            demand,
            admission,
            cast(OACExecutionApproval, formation_approval),
            source_admission=source,
            run_id="formation-approval-is-insufficient",
        )
    assert store.verify_event_chain()["events"] == 1


def test_base_and_split_use_one_executor_with_distinct_accepted_topologies(
    store: StateStore,
    current_oac_capsules: dict[str, OACRuntimeCapsule],
) -> None:
    executor = ZeroEffectOACExecutor(store)
    receipts: dict[str, OACExecutionReceipt] = {}
    evidence_by_case: dict[str, tuple[ExecutionEvidence, ...]] = {}
    demand_digests: dict[str, str] = {}

    for case in ("BASE", "SPLIT"):
        capsule = current_oac_capsules[case]
        demand, source, _, admission, approval = _prepare_execution(
            store, capsule, command_suffix=f"same-entry-{case.lower()}"
        )
        demand_digests[case] = demand["digest"]
        receipts[case], evidence_by_case[case] = executor.execute(
            capsule,
            demand,
            admission,
            approval,
            source_admission=source,
            run_id=f"same-entry-{case.lower()}",
        )

    assert demand_digests["BASE"] == demand_digests["SPLIT"]
    assert len(receipts["BASE"].steps) == 3
    assert len(receipts["SPLIT"].steps) == 4
    assert (
        current_oac_capsules["BASE"].runtime_bundle["spec"]["topologyDigest"]
        != current_oac_capsules["SPLIT"].runtime_bundle["spec"]["topologyDigest"]
    )
    assert receipts["BASE"].obligation_contract_digest == receipts["SPLIT"].obligation_contract_digest
    assert receipts["BASE"].executor_id == receipts["SPLIT"].executor_id

    def semantic_projection(
        evidence: tuple[ExecutionEvidence, ...],
    ) -> tuple[tuple[str, str, str], ...]:
        return tuple(
            sorted(
                (
                    item.obligation_type,
                    item.evidence_type,
                    item.observed_value,
                )
                for item in evidence
            )
        )

    assert semantic_projection(evidence_by_case["BASE"]) == semantic_projection(evidence_by_case["SPLIT"])


def test_execution_exact_replay_is_idempotent(
    store: StateStore,
    current_oac_capsules: dict[str, OACRuntimeCapsule],
) -> None:
    capsule = current_oac_capsules["BASE"]
    demand, source, _, admission, approval = _prepare_execution(store, capsule, command_suffix="idempotent")
    executor = ZeroEffectOACExecutor(store)

    first = executor.execute(
        capsule,
        demand,
        admission,
        approval,
        source_admission=source,
        run_id="idempotent-run",
    )
    repeated = executor.execute(
        capsule,
        demand,
        admission,
        approval,
        source_admission=source,
        run_id="idempotent-run",
    )

    assert repeated[0].digest == first[0].digest
    assert tuple(item.digest for item in repeated[1]) == tuple(item.digest for item in first[1])
    assert store.verify_event_chain()["events"] == 2

    with pytest.raises(RuntimeError, match="IDEMPOTENCY_CONFLICT"):
        executor.execute(
            capsule,
            demand,
            admission,
            approval,
            source_admission=source,
            run_id="idempotent-run",
            completed_at="2026-08-26T00:01:02Z",
        )
    assert store.verify_event_chain()["events"] == 2


def test_execution_rejects_program_registry_drift_after_exact_approval(
    store: StateStore,
    current_oac_capsules: dict[str, OACRuntimeCapsule],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capsule = current_oac_capsules["BASE"]
    demand, source, _, admission, approval = _prepare_execution(
        store, capsule, command_suffix="program-digest"
    )
    handler_ref = "urn:oac:zero-effect-handler:qualification-evidence-check"
    admitted_definition = evolution_runtime.HANDLER_REGISTRY[handler_ref]
    monkeypatch.setitem(
        evolution_runtime.HANDLER_REGISTRY,
        handler_ref,
        replace(admitted_definition, program_version="attacker.forged-handler/v1"),
    )

    with pytest.raises(
        IntegrityError,
        match="OAC_EXECUTION_APPROVAL_MISMATCH:program_policy_digest",
    ):
        ZeroEffectOACExecutor(store).execute(
            capsule,
            demand,
            admission,
            approval,
            source_admission=source,
            run_id="forged-program-digest",
        )
    assert store.verify_event_chain()["events"] == 1


@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        ("predecessor", "OAC_RUNTIME_STEP_PROJECTION_MISMATCH"),
        ("evidence", "OAC_RUNTIME_STEP_HANDLER_MISMATCH"),
    ),
)
def test_execution_rejects_invalid_predecessor_or_missing_evidence_contract(
    store: StateStore,
    current_oac_capsules: dict[str, OACRuntimeCapsule],
    mutation: str,
    expected: str,
) -> None:
    capsule = current_oac_capsules["BASE"]
    demand, source, _, admission, approval = _prepare_execution(
        store, capsule, command_suffix=f"invalid-{mutation}"
    )

    def mutate(bundle: dict[str, Any]) -> None:
        if mutation == "predecessor":
            bundle["spec"]["steps"][0]["predecessorRefs"] = [bundle["spec"]["steps"][-1]["workUnitRef"]]
        else:
            bundle["spec"]["steps"][0]["handlerBindings"][0]["evidenceOutputRefs"] = []

    invalid = _mutate_runtime_bundle(capsule, mutate)
    with pytest.raises(IntegrityError, match=expected):
        ZeroEffectOACExecutor(store).execute(
            invalid,
            demand,
            admission,
            approval,
            source_admission=source,
            run_id=f"invalid-{mutation}",
        )
    assert store.verify_event_chain()["events"] == 1


def test_completed_execution_with_complete_controlled_evidence_is_accepted(
    store: StateStore,
    current_oac_capsules: dict[str, OACRuntimeCapsule],
) -> None:
    receipt, evidence = _execute(store, current_oac_capsules["BASE"], run_id="accepted")
    observation = ControlledOutcomeObservationProducer(store).record(
        receipt,
        observation_id="outcome-observation:accepted",
        actual_values=controlled_reference_actual_values(),
    )
    _, decision = ControlledProcessOutcomeAssurance(store).evaluate(
        receipt,
        evidence,
        observation,
        decision_id="outcome-decision:accepted",
    )

    assert receipt.status is ExecutionStatus.COMPLETED
    assert decision.verdict is OutcomeVerdict.ACCEPT
    assert {dimension.verdict for dimension in decision.dimensions} == {"PASS"}
    assert observation.execution_receipt_digest == receipt.digest
    assert observation.evidence_class == "SYNTHETIC_CONTROLLED_OBSERVATION"
    business_dimensions = {
        "commercial_switch_satisfied",
        "continuity_option_satisfied",
        "dependency_impact_satisfied",
        "qualification_evidence_satisfied",
    }
    assert all(
        dimension.evidence_refs == (observation.observation_id,)
        for dimension in decision.dimensions
        if dimension.name in business_dimensions
    )
    assert decision.oracle_id not in {receipt.executor_id, receipt.runtime_owner_id}
    assert store.verify_event_chain()["events"] == 4


def test_structurally_complete_execution_can_still_be_rejected_by_outcome_oracle(
    store: StateStore,
    current_oac_capsules: dict[str, OACRuntimeCapsule],
) -> None:
    receipt, evidence = _execute(store, current_oac_capsules["SPLIT"], run_id="expired-qualification")
    actual_values = controlled_reference_actual_values()
    actual_values["qualification-evidence-check"] = "qualification_record_expired"
    observation = ControlledOutcomeObservationProducer(store).record(
        receipt,
        observation_id="outcome-observation:expired-qualification",
        actual_values=actual_values,
    )
    _, decision = ControlledProcessOutcomeAssurance(store).evaluate(
        receipt,
        evidence,
        observation,
        decision_id="outcome-decision:expired-qualification",
    )

    dimensions = {item.name: item for item in decision.dimensions}
    assert receipt.status is ExecutionStatus.COMPLETED
    assert all(step.status is ExecutionStatus.COMPLETED for step in receipt.steps)
    assert len(receipt.evidence_refs) == len(evidence)
    assert {item.observed_value for item in evidence} == {"zero_effect_handler_completed"}
    assert decision.verdict is OutcomeVerdict.REJECT
    assert dimensions["execution_completeness"].verdict == "PASS"
    assert dimensions["evidence_provenance"].verdict == "PASS"
    assert dimensions["effect_reconciliation"].verdict == "PASS"
    assert dimensions["qualification_evidence_satisfied"].verdict == "FAIL"
    assert decision.reason_codes == ("SYNTHETIC_CONTROLLED_PROCESS_ASSURANCE_REJECT",)


@pytest.mark.parametrize("collision", ("executor", "runtime_owner"))
def test_runtime_cannot_issue_its_own_outcome_authority(
    store: StateStore,
    current_oac_capsules: dict[str, OACRuntimeCapsule],
    collision: str,
) -> None:
    receipt, evidence = _execute(
        store, current_oac_capsules["BASE"], run_id=f"authority-collision-{collision}"
    )
    observation = ControlledOutcomeObservationProducer(store).record(
        receipt,
        observation_id=f"outcome-observation:authority-collision-{collision}",
        actual_values=controlled_reference_actual_values(),
    )
    kwargs = (
        {"oracle_id": receipt.executor_id}
        if collision == "executor"
        else {"issuer_authority_ref": receipt.runtime_owner_id}
    )

    with pytest.raises(
        IntegrityError,
        match="OUTCOME_AUTHORITY_COLLIDES_WITH_RUNTIME",
    ):
        ControlledProcessOutcomeAssurance(store).evaluate(
            receipt,
            evidence,
            observation,
            decision_id=f"outcome-decision:authority-collision-{collision}",
            **kwargs,
        )
    assert not store.artifact_exists(f"outcome-decision:authority-collision-{collision}")
    assert store.verify_event_chain()["events"] == 3


def test_runtime_api_cannot_inject_business_observation(
    store: StateStore,
    current_oac_capsules: dict[str, OACRuntimeCapsule],
) -> None:
    capsule = current_oac_capsules["BASE"]
    demand, source, _, admission, approval = _prepare_execution(
        store, capsule, command_suffix="no-runtime-observation"
    )

    with pytest.raises(TypeError, match="observation_overrides"):
        ZeroEffectOACExecutor(store).execute(
            capsule,
            demand,
            admission,
            approval,
            source_admission=source,
            run_id="no-runtime-observation",
            observation_overrides={"qualification-evidence-check": "forged"},  # type: ignore[call-arg]
        )
    assert store.verify_event_chain()["events"] == 1


def test_execution_rejects_missing_or_wrong_source_admission_binding(
    store: StateStore,
    current_oac_capsules: dict[str, OACRuntimeCapsule],
) -> None:
    capsule = current_oac_capsules["BASE"]
    demand, source, _, admission, approval = _prepare_execution(
        store, capsule, command_suffix="source-binding"
    )
    executor = ZeroEffectOACExecutor(store)

    with pytest.raises(TypeError, match="source_admission"):
        executor.execute(  # type: ignore[call-arg]
            capsule,
            demand,
            admission,
            approval,
            run_id="missing-source-binding",
        )
    wrong_source = deepcopy(source)
    wrong_source["metadata"]["id"] = "source-admission:wrong"
    _reseal(wrong_source)
    with pytest.raises(
        IntegrityError,
        match="OAC_EXECUTION_APPROVAL_MISMATCH:source_admission_ref",
    ):
        executor.execute(
            capsule,
            demand,
            admission,
            approval,
            source_admission=wrong_source,
            run_id="wrong-source-binding",
        )
    assert store.verify_event_chain()["events"] == 1


def test_observation_producer_cannot_be_runtime(
    store: StateStore,
    current_oac_capsules: dict[str, OACRuntimeCapsule],
) -> None:
    receipt, _ = _execute(store, current_oac_capsules["BASE"], run_id="forged-runtime-observation")

    with pytest.raises(IntegrityError, match="OUTCOME_OBSERVATION_AUTHORITY_COLLISION"):
        ControlledOutcomeObservationProducer(store).record(
            receipt,
            observation_id="outcome-observation:forged-runtime",
            actual_values=controlled_reference_actual_values(),
            producer_id=receipt.executor_id,
        )
    assert not store.artifact_exists("outcome-observation:forged-runtime")
    assert store.verify_event_chain()["events"] == 2


def test_tampered_observation_digest_is_rejected_before_decision(
    store: StateStore,
    current_oac_capsules: dict[str, OACRuntimeCapsule],
) -> None:
    receipt, evidence = _execute(store, current_oac_capsules["BASE"], run_id="tampered-observation")
    observation = ControlledOutcomeObservationProducer(store).record(
        receipt,
        observation_id="outcome-observation:tampered",
        actual_values=controlled_reference_actual_values(),
    )
    tampered = observation.model_copy(update={"facts": observation.facts[:-1]})

    with pytest.raises(IntegrityError, match="OUTCOME_OBSERVATION_MODEL_INVALID"):
        ControlledProcessOutcomeAssurance(store).evaluate(
            receipt,
            evidence,
            tampered,
            decision_id="outcome-decision:tampered",
        )
    assert not store.artifact_exists("outcome-decision:tampered")
    assert store.verify_event_chain()["events"] == 3


def test_same_completed_execution_accepts_or_rejects_only_from_independent_observation(
    store: StateStore,
    current_oac_capsules: dict[str, OACRuntimeCapsule],
) -> None:
    receipt, evidence = _execute(store, current_oac_capsules["SPLIT"], run_id="same-execution-two-outcomes")
    producer = ControlledOutcomeObservationProducer(store)
    accepted_observation = producer.record(
        receipt,
        observation_id="outcome-observation:same-execution-accepted",
        actual_values=controlled_reference_actual_values(),
    )
    expired = controlled_reference_actual_values()
    expired["qualification-evidence-check"] = "qualification_record_expired"
    rejected_observation = producer.record(
        receipt,
        observation_id="outcome-observation:same-execution-rejected",
        actual_values=expired,
    )
    assurance = ControlledProcessOutcomeAssurance(store)
    _, accepted = assurance.evaluate(
        receipt,
        evidence,
        accepted_observation,
        decision_id="outcome-decision:same-execution-accepted",
    )
    _, rejected = assurance.evaluate(
        receipt,
        evidence,
        rejected_observation,
        decision_id="outcome-decision:same-execution-rejected",
    )

    assert receipt.status is ExecutionStatus.COMPLETED
    assert {item.observed_value for item in evidence} == {"zero_effect_handler_completed"}
    assert accepted.verdict is OutcomeVerdict.ACCEPT
    assert rejected.verdict is OutcomeVerdict.REJECT
