from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path

import pytest

from orgrebase.store import StateStore
from orgrebase.workspace.evolution_evidence import (
    _seal,
    _validate_evolution,
    build_execution_approval,
    build_initial_lifecycle_roots,
    build_portable_outcome_certificate,
)
from orgrebase.workspace.evolution_runtime import ZeroEffectOACExecutor
from orgrebase.workspace.models import OACRuntimeCapsule
from orgrebase.workspace.oac_bridge import OACBlackBoxCLI, OACRuntimeAdmissionBridge
from orgrebase.workspace.outcome_assurance import (
    ControlledOutcomeObservationProducer,
    ControlledProcessOutcomeAssurance,
    controlled_reference_actual_values,
)


@pytest.fixture(scope="module")
def cli() -> OACBlackBoxCLI:
    return OACBlackBoxCLI()


@pytest.fixture
def store(tmp_path: Path) -> Iterator[StateStore]:
    value = StateStore(tmp_path / "evolution-wire.sqlite")
    try:
        yield value
    finally:
        value.close()


def _run(
    cli: OACBlackBoxCLI,
    store: StateStore,
    capsules: dict[str, OACRuntimeCapsule],
    case: str,
    *,
    expired: bool = False,
) -> tuple[dict[str, object], object, object]:
    capsule = capsules[case]
    roots = build_initial_lifecycle_roots(cli, capsule.snapshot, capsule.change)
    bridge = OACRuntimeAdmissionBridge(store)
    preview = bridge.prepare(capsule)
    formation_approval = bridge.approve(
        preview,
        actor_id=preview.runtime_owner_id,
        preview_digest=preview.digest,
        command_id=f"wire-formation-{case.lower()}-{'expired' if expired else 'accepted'}",
        approved_at="2026-08-26T00:00:00Z",
    )
    admission = bridge.apply(capsule, preview, formation_approval)
    execution_approval = build_execution_approval(
        command_id=f"wire-execution-{case.lower()}-{'expired' if expired else 'accepted'}",
        capsule_digest=capsule.digest,
        runtime_admission_digest=admission.digest,
        source_admission=roots.oac_source_admission,
        demand=roots.demand,
        runtime_bundle_digest=capsule.runtime_bundle["digest"],
        runtime_owner_id=preview.runtime_owner_id,
    )
    receipt, evidence = ZeroEffectOACExecutor(store).execute(
        capsule,
        roots.demand,
        admission,
        execution_approval,
        source_admission=roots.oac_source_admission,
        run_id=f"wire-{case.lower()}-{'expired' if expired else 'accepted'}",
    )
    actual_values = controlled_reference_actual_values()
    if expired:
        actual_values["qualification-evidence-check"] = "qualification_record_expired"
    observation = ControlledOutcomeObservationProducer(store).record(
        receipt,
        observation_id=f"outcome-observation:{receipt.run_id}",
        actual_values=actual_values,
    )
    _, decision = ControlledProcessOutcomeAssurance(store).evaluate(
        receipt,
        evidence,
        observation,
        decision_id=f"outcome-decision:{receipt.run_id}",
    )
    certificate = build_portable_outcome_certificate(
        cli,
        capsule=capsule.model_dump(mode="json"),
        roots=roots,
        execution=receipt,
        evidence=evidence,
        observation=observation,
        decision=decision,
    )
    return certificate, receipt, roots


def test_source_profile_is_admission_policy_not_duplicate_business_subject(
    cli: OACBlackBoxCLI,
    current_oac_capsules: dict[str, OACRuntimeCapsule],
) -> None:
    capsule = current_oac_capsules["BASE"]
    roots = build_initial_lifecycle_roots(cli, capsule.snapshot, capsule.change)
    source = roots.oac_source_admission

    assert [item["resourceId"] for item in source["spec"]["subjectRefs"]] == [
        capsule.snapshot["metadata"]["id"]
    ]
    assert source["spec"]["intakeProfileRef"]["resourceId"] == ("profile:veracier-supplier-shadow")
    assert source["metadata"]["sourceRefs"] == [
        capsule.snapshot["metadata"]["id"],
        "profile:veracier-supplier-shadow",
        "human:veracier-shadow-owner",
    ]
    assert roots.demand["metadata"]["sourceRefs"] == [
        capsule.snapshot["metadata"]["id"],
        "alternative:acieries-savoie",
        capsule.change["metadata"]["id"],
    ]
    assert source["spec"]["decisionAuthorityRef"]["resourceId"] == ("human:veracier-shadow-owner")


@pytest.mark.parametrize(("case", "steps"), (("BASE", 3), ("SPLIT", 4)))
def test_public_wire_positive_lifecycle_reaches_independent_accept(
    cli: OACBlackBoxCLI,
    store: StateStore,
    case: str,
    steps: int,
    current_oac_capsules: dict[str, OACRuntimeCapsule],
) -> None:
    certificate, receipt, roots = _run(cli, store, current_oac_capsules, case)

    assert receipt.status.value == "COMPLETED"
    assert receipt.source_admission_digest == roots.oac_source_admission["digest"]
    assert len(receipt.steps) == steps
    assert certificate["spec"]["verdict"] == "ACCEPT"
    spec = certificate["spec"]
    assert "profileBinding" not in spec
    source_refs = certificate["metadata"]["sourceRefs"]
    assert source_refs == [
        spec["snapshotRef"]["resourceId"],
        *(ref["resourceId"] for ref in spec["sourceAdmissionReceiptRefs"]),
        spec["demandRef"]["resourceId"],
        *(ref["resourceId"] for ref in spec["changeRefs"]),
        spec["planRef"]["resourceId"],
        spec["planCertificateRef"]["resourceId"],
        spec["runtimeBindingRef"]["resourceId"],
        spec["runtimeBundleRef"]["resourceId"],
        spec["executionReceiptRef"]["resourceId"],
        *(ref["resourceId"] for ref in spec["executionEvidenceRefs"]),
        *(ref["resourceId"] for ref in spec["observationRefs"]),
        spec["oracleRef"]["resourceId"],
        spec["observationProfileRef"]["resourceId"],
        *(ref["resourceId"] for ref in spec["evidenceRefs"]),
        *(ref["resourceId"] for ref in spec["unresolvedRefs"]),
    ]
    observation = spec["observationRefs"][0]
    assert observation in spec["evidenceRefs"]
    assert source_refs.count(observation["resourceId"]) == 2
    deduplicated = deepcopy(certificate)
    deduplicated["metadata"]["sourceRefs"] = list(dict.fromkeys(source_refs))
    with pytest.raises(RuntimeError, match="OAC_CLI_FAILED:validate-evolution"):
        _validate_evolution(cli, _seal(deduplicated))
    assert certificate["metadata"]["ownerRef"] != receipt.runtime_owner_id
    assert store.verify_event_chain()["status"] == "PASS"


def test_public_wire_complete_execution_can_be_rejected(
    cli: OACBlackBoxCLI,
    store: StateStore,
    current_oac_capsules: dict[str, OACRuntimeCapsule],
) -> None:
    certificate, receipt, _ = _run(cli, store, current_oac_capsules, "SPLIT", expired=True)

    assert receipt.status.value == "COMPLETED"
    assert all(step.status.value == "COMPLETED" for step in receipt.steps)
    assert certificate["spec"]["verdict"] == "REJECT"
    assert any(item["verdict"] == "FAIL" for item in certificate["spec"]["dimensions"])
