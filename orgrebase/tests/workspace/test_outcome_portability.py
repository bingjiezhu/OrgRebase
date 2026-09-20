from __future__ import annotations

import hashlib
import json
import os
import sys
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.workspace import outcome_portability
from orgrebase.workspace.evolution_evidence import _seal
from orgrebase.workspace.outcome_lab import OACPlanGate, OutcomeLab, OutcomeOracle, OutcomeTaskMapping
from orgrebase.workspace.outcome_portability import (
    LabOutcomeTrust,
    build_lab_lifecycle,
    build_portable_lab_outcome,
    verify_portable_lab_outcome,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "outcome-lab-retail"


class LocalEnvironment:
    """Small deterministic state experiment; not an upstream benchmark runner."""

    build_digest = sha256_digest("controlled-local-portable-test-environment")

    def __init__(self, mode: str = "accept") -> None:
        self.mode = mode
        self.lost = False
        self.calls = 0
        self.resets = 0
        self.reset()

    def reset(self) -> str:
        if self.lost or (self.mode == "unknown-reset" and self.resets >= 2):
            raise BrokenPipeError("worker cannot reset")
        self.resets += 1
        self.state = {
            "orders": {"#W5056519": {"status": "pending"}, "#W5995614": {"status": "pending"}},
            "unrelated": 1,
        }
        return str(self.resets)

    def snapshot(self) -> dict[str, Any]:
        if self.lost:
            raise EOFError("worker state unavailable")
        return deepcopy(self.state)

    def tool_mutates_state(self, tool: str) -> bool:
        return tool == "cancel_pending_order"

    def call(self, request: Any, *, timeout: float) -> Any:
        assert timeout > 0
        self.calls += 1
        if self.mode == "unknown":
            self.lost = True
            raise TimeoutError("dispatch result lost")
        if request.tool == "cancel_pending_order":
            self.state["orders"][request.arguments["order_id"]]["status"] = "cancelled"
            if self.mode == "reject":
                self.state["unrelated"] += 1
        return deepcopy(self.state["orders"])

    def close(self) -> None:
        pass


class RecordingGate(OACPlanGate):
    def __init__(self) -> None:
        environment = dict(os.environ)
        configured = os.environ.get("ORGREBASE_LAB_OAC_COMMAND_JSON")
        if configured:
            command = json.loads(configured)
            environment.pop("PYTHONPATH", None)
        else:
            root = Path(
                os.environ.get("ORGREBASE_OAC_ROOT", Path(__file__).resolve().parents[3] / "oac-spec")
            )
            command = [sys.executable, "-m", "oac"]
            environment["PYTHONPATH"] = str(root / "src")
        super().__init__(command, environment=environment)
        self.validated: list[str] = []

    def validate_lifecycle(self, resource: Any, *, snapshot: Any = None) -> None:
        super().validate_lifecycle(resource, snapshot=snapshot)
        self.validated.append(resource["kind"])


def make_case(mode: str) -> dict[str, Any]:
    values = {
        name: json.loads((FIXTURE / f"{name}.json").read_text())
        for name in ("snapshot", "change", "plan", "mapping")
    }
    environment = LocalEnvironment(mode)
    oracle = OutcomeOracle(
        authority="test:portable-oracle",
        policy_source_digest=sha256_digest("local-test-state-policy"),
        expected={"/orders/#W5056519/status": "cancelled", "/orders/#W5995614/status": "cancelled"},
    )
    raw = values["mapping"]
    raw.pop("digest")
    raw.update(
        task_id="controlled-local-portability-test",
        task_digest=sha256_digest("local-test-task"),
        upstream_repository="https://example.org/controlled-local-fixture",
        environment_build_digest=environment.build_digest,
        seed_root=sha256_digest(environment.snapshot()),
        oracle_digest=oracle.digest,
        budget={"tool_calls": 12, "write_calls": 2, "elapsed_seconds": 30},
    )
    spec = values["plan"]["spec"]
    raw["work_unit_predecessors"] = {unit["workUnitId"]: [] for unit in spec["workUnits"]}
    for constraint in spec["happensBefore"]:
        raw["work_unit_predecessors"][constraint["successorRef"]].append(constraint["predecessorRef"])
    for grant in raw["grants"]:
        grant.pop("digest")
        grant["work_unit_ref"] = next(
            unit["workUnitId"]
            for unit in spec["workUnits"]
            if grant["obligation_ref"] in unit["obligationRefs"]
        )
    mapping = OutcomeTaskMapping.model_validate(raw)
    gate = RecordingGate()
    lifecycle = build_lab_lifecycle(
        gate,
        mapping,
        oracle,
        values["snapshot"],
        values["change"],
        values["plan"],
        "test:portable-controller",
        "test:portable-actor",
    )
    assert environment.calls == 0
    lab = OutcomeLab(
        mapping,
        oracle,
        environment,
        gate,
        snapshot=values["snapshot"],
        change=values["change"],
        plan=values["plan"],
        controller_authority="test:portable-controller",
        acting_authority="test:portable-actor",
    )
    approval = lab.approve("oac", authority="test:portable-controller")
    receipt = lab.run(
        approval["token"],
        [grant.request for grant in mapping.grants],
        runtime_bundle=approval["runtime_bundle"],
    )
    trust = LabOutcomeTrust(
        mapping=mapping,
        oracle=oracle,
        controller_authority="test:portable-controller",
        acting_authority="test:portable-actor",
        lifecycle_digest=lifecycle["digest"],
        receipt_digest=receipt["digest"],
        approval_digest=approval["receipt"]["digest"],
        oracle_build_digest=sha256_digest(
            {
                name: hashlib.sha256(
                    (Path(__file__).resolve().parents[2] / "src/orgrebase/workspace" / name).read_bytes()
                ).hexdigest()
                for name in ("outcome_lab.py", "outcome_verification.py", "outcome_contracts.py")
            }
        ),
    )
    bundle = build_portable_lab_outcome(gate, lifecycle, receipt, trust)
    return {
        **values,
        "gate": gate,
        "mapping": mapping,
        "oracle": oracle,
        "environment": environment,
        "lab": lab,
        "approval": approval,
        "receipt": receipt,
        "lifecycle": lifecycle,
        "trust": trust,
        "bundle": bundle,
    }


@pytest.fixture(scope="module")
def accepted_case() -> dict[str, Any]:
    return make_case("accept")


@pytest.mark.parametrize(
    "recorded_at",
    [
        "2026-09-10T12:00:00.123456Z",
        "2026-09-10T12:00:00+00:00",
        "2026-02-30T12:00:00Z",
        "2026-9-10T12:00:00Z",
        "invalidZ",
        None,
    ],
)
def test_trusted_recording_time_requires_exact_whole_second_utc(accepted_case, recorded_at):
    original = accepted_case["trust"]
    with pytest.raises(IntegrityError, match=r"^LAB_PORTABLE_RECORDING_TIME_INVALID$"):
        replace(original, recorded_at=recorded_at)
    assert accepted_case["bundle"]["recorded_at"] == original.recorded_at


def test_actual_controller_grant_and_real_cli_form_one_portable_closure(accepted_case: Any) -> None:
    value = accepted_case
    assert value["receipt"]["verdict"] == "ACCEPT"
    assert value["receipt"]["initial_root"] != value["receipt"]["final_root"]
    assert value["environment"].snapshot()["orders"]["#W5056519"]["status"] == "pending"
    assert set(value["gate"].validated) == {
        "SourceAdmissionReceipt",
        "OrganizationalDemand",
        "OutcomeCertificate",
    }
    verify_portable_lab_outcome(value["gate"], value["bundle"], value["trust"])
    cert = value["bundle"]["outcome_certificate"]["spec"]
    assert cert["verdict"] == "ACCEPT"
    assert len(cert["dimensions"]) == 6
    assert cert["runtimeBundleRef"]["kind"] == "DisposableRuntimeBundle"
    assert cert["executionAuthorizationRef"]["kind"] == "DisposableExecutionGrant"
    assert value["lifecycle"]["plan"]["spec"]["effectCeiling"] == "zero_effect"
    with pytest.raises(IntegrityError, match="ONE_USE"):
        value["lab"].run(value["approval"]["token"], [], runtime_bundle=value["approval"]["runtime_bundle"])
    assert (
        "CONTROLLED_LOCAL_SCRIPT_ADMISSION" in value["lifecycle"]["source_admission"]["spec"]["reasonCodes"]
    )


@pytest.mark.parametrize(
    "mode,expected", [("reject", "REJECT"), ("unknown", "REJECT"), ("unknown-reset", "UNKNOWN")]
)
def test_observed_failure_and_worker_loss_survive_portable_conversion(mode: str, expected: str) -> None:
    value = make_case(mode)
    assert value["receipt"]["verdict"] == expected
    assert value["bundle"]["outcome_certificate"]["spec"]["verdict"] == expected
    verify_portable_lab_outcome(value["gate"], value["bundle"], value["trust"])
    if mode in {"unknown", "unknown-reset"}:
        assert value["bundle"]["outcome_certificate"]["spec"]["unresolvedRefs"]


def reseal_bundle(value: dict[str, Any]) -> None:
    value["digest"] = sha256_digest({key: item for key, item in value.items() if key != "digest"})


@pytest.mark.parametrize(
    "kind",
    [
        "DisposableExecutionGrant",
        "DisposableRuntimeBinding",
        "DisposableRuntimeBundle",
        "ExecutionReceipt",
        "ExecutionEvidence",
        "OutcomeOracle",
        "ObservationProfile",
        "OutcomeObservation",
        "OutcomeEvidence",
        "Principal",
    ],
)
def test_rehashed_payload_substitution_cannot_pass_reference_only_checks(
    accepted_case: Any, kind: str
) -> None:
    bundle = json.loads(json.dumps(accepted_case["bundle"]))
    index = next(i for i, resource in enumerate(bundle["payloads"]) if resource["kind"] == kind)
    payload = bundle["payloads"][index]
    payload["spec"]["unexpectedReplacement"] = True
    bundle["payloads"][index] = _seal(payload)
    reseal_bundle(bundle)
    with pytest.raises(IntegrityError, match="PAYLOAD_CLOSURE"):
        verify_portable_lab_outcome(accepted_case["gate"], bundle, accepted_case["trust"])


@pytest.mark.parametrize(
    "mutation",
    [
        "omit",
        "extra",
        "certificate-root",
        "certificate-grant",
        "receipt-trace",
        "receipt-approval",
        "lifecycle-manifest",
    ],
)
def test_whole_bundle_closure_is_bound_to_separately_trusted_pins(accepted_case: Any, mutation: str) -> None:
    bundle = deepcopy(accepted_case["bundle"])
    if mutation == "omit":
        bundle["payloads"].pop()
    elif mutation == "extra":
        bundle["payloads"].append(deepcopy(bundle["payloads"][0]))
    elif mutation.startswith("certificate"):
        spec = bundle["outcome_certificate"]["spec"]
        if mutation == "certificate-root":
            spec["executionRoot"] = sha256_digest("forged")
        else:
            spec["executionAuthorizationRef"]["digest"] = sha256_digest("forged")
        bundle["outcome_certificate"] = _seal(bundle["outcome_certificate"])
    elif mutation == "receipt-trace":
        bundle["receipt"]["trace"].clear()
        reseal_bundle(bundle["receipt"])
    elif mutation == "receipt-approval":
        bundle["receipt"]["approval_receipt"]["controller"] = "attacker"
        reseal_bundle(bundle["receipt"]["approval_receipt"])
        reseal_bundle(bundle["receipt"])
    else:
        bundle["lifecycle"]["payloads"][0]["spec"]["authority"] = "attacker"
        reseal_bundle(bundle["lifecycle"])
    reseal_bundle(bundle)
    with pytest.raises(IntegrityError, match=r"CLOSURE|PIN_MISMATCH"):
        verify_portable_lab_outcome(accepted_case["gate"], bundle, accepted_case["trust"])


@pytest.mark.parametrize("field", ["lifecycle_digest", "receipt_digest", "approval_digest"])
def test_trusted_host_pin_must_match_exact_run(accepted_case: Any, field: str) -> None:
    trust = replace(accepted_case["trust"], **{field: sha256_digest("another-run")})
    with pytest.raises(IntegrityError, match="PIN_MISMATCH"):
        verify_portable_lab_outcome(accepted_case["gate"], accepted_case["bundle"], trust)


def test_lifecycle_uses_snapshot_requester_and_never_fabricates_human_qualification(
    accepted_case: Any,
) -> None:
    lifecycle = accepted_case["lifecycle"]
    demand = lifecycle["demand"]["spec"]
    principal = next(
        item
        for item in lifecycle["snapshot"]["spec"]["principals"]
        if item["principalId"] == demand["requesterPrincipalRef"]
    )
    assert principal["principalType"] == "agent"
    assert demand["accountableRoleRef"] in principal["eligibleRoleRefs"]
    profile = next(item for item in lifecycle["payloads"] if item["kind"] == "IntakeProfile")
    assert profile["spec"]["humanQualificationClaimed"] is False


def test_oracle_build_pin_is_distinct_from_policy_configuration(accepted_case: Any) -> None:
    value = accepted_case
    assert (
        value["bundle"]["outcome_certificate"]["spec"]["oracleBuildDigest"]
        == value["trust"].oracle_build_digest
    )
    assert value["trust"].oracle_build_digest != value["oracle"].digest
    changed = replace(value["trust"], oracle_build_digest=sha256_digest("another-observer-build"))
    with pytest.raises(IntegrityError, match="PAYLOAD_CLOSURE"):
        verify_portable_lab_outcome(value["gate"], value["bundle"], changed)


def test_false_is_not_interchangeable_with_zero_in_a_resealed_grant(accepted_case: Any) -> None:
    bundle = json.loads(json.dumps(accepted_case["bundle"]))
    index = next(
        i for i, value in enumerate(bundle["payloads"]) if value["kind"] == "DisposableExecutionGrant"
    )
    bundle["payloads"][index]["spec"]["productionAuthority"] = 0
    bundle["payloads"][index] = _seal(bundle["payloads"][index])
    reseal_bundle(bundle)
    with pytest.raises(IntegrityError, match="PAYLOAD_CLOSURE"):
        verify_portable_lab_outcome(accepted_case["gate"], bundle, accepted_case["trust"])


@pytest.mark.parametrize("target", ["execution-receipt", "observed-state-root", "state-receipt"])
def test_resealed_state_reference_substitution_is_rejected(accepted_case: Any, target: str) -> None:
    bundle = deepcopy(accepted_case["bundle"])
    if target == "execution-receipt":
        item = next(row for row in bundle["payloads"] if row["kind"] == "ExecutionReceipt")
        item["spec"]["labReceiptDigest"] = sha256_digest("different-controller-run")
    else:
        item = next(row for row in bundle["payloads"] if "stateObservationRoots" in row["spec"])
        if target == "observed-state-root":
            item["spec"]["stateObservationRoots"][0] = sha256_digest("different-observation")
        else:
            item["spec"]["labReceiptDigest"] = sha256_digest("different-controller-run")
    item.update(_seal(item))
    reseal_bundle(bundle)
    with pytest.raises(IntegrityError, match="PAYLOAD_CLOSURE"):
        verify_portable_lab_outcome(accepted_case["gate"], bundle, accepted_case["trust"])


@pytest.mark.parametrize("target", ("owner", "plan-reference"))
def test_portable_binding_cannot_replace_original_execution_identity(accepted_case: Any, target: str) -> None:
    bundle = json.loads(json.dumps(accepted_case["bundle"]))
    binding = next(item for item in bundle["payloads"] if item["kind"] == "DisposableRuntimeBinding")
    if target == "owner":
        binding["metadata"]["ownerRef"] = "test:attacker"
    else:
        binding["spec"]["planRef"]["resourceId"] = "test:another-plan"
    binding.update(_seal(binding))
    reseal_bundle(bundle)
    with pytest.raises(IntegrityError, match="PAYLOAD_CLOSURE"):
        verify_portable_lab_outcome(accepted_case["gate"], bundle, accepted_case["trust"])


def test_portable_builder_retains_receipt_snapshot_before_verification_callbacks(
    accepted_case: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt = json.loads(json.dumps(accepted_case["receipt"]))
    original_receipt = json.loads(json.dumps(receipt))
    original_verify = outcome_portability.verify_outcome_receipt

    def verify_then_change_caller_receipt(*args: Any, **kwargs: Any) -> None:
        original_verify(*args, **kwargs)
        receipt["trace"][0]["result"] = {"replaced_after_verification": True}
        receipt["trace_digest"] = sha256_digest(receipt["trace"])
        reseal_bundle(receipt)

    with monkeypatch.context() as patch:
        patch.setattr(outcome_portability, "verify_outcome_receipt", verify_then_change_caller_receipt)
        bundle = build_portable_lab_outcome(
            accepted_case["gate"], accepted_case["lifecycle"], receipt, accepted_case["trust"]
        )

    assert receipt != original_receipt
    assert bundle["receipt"] == original_receipt
    assert bundle["receipt"]["digest"] == accepted_case["trust"].receipt_digest
    verify_portable_lab_outcome(accepted_case["gate"], bundle, accepted_case["trust"])
    with pytest.raises(IntegrityError, match="RECEIPT_PIN_MISMATCH"):
        build_portable_lab_outcome(
            accepted_case["gate"], accepted_case["lifecycle"], receipt, accepted_case["trust"]
        )


def test_portable_verifier_uses_entry_snapshot_when_caller_changes_bundle(
    accepted_case: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = json.loads(json.dumps(accepted_case["bundle"]))
    original_build = outcome_portability.build_portable_lab_outcome
    calls = 0

    def build_then_change_caller_bundle(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        expected = original_build(*args, **kwargs)
        calls += 1
        bundle["outcome_certificate"]["spec"]["executionRoot"] = sha256_digest("replacement")
        bundle["outcome_certificate"] = _seal(bundle["outcome_certificate"])
        reseal_bundle(bundle)
        return expected

    with monkeypatch.context() as patch:
        patch.setattr(outcome_portability, "build_portable_lab_outcome", build_then_change_caller_bundle)
        verify_portable_lab_outcome(accepted_case["gate"], bundle, accepted_case["trust"])

    assert calls == 1
    assert bundle["digest"] != accepted_case["bundle"]["digest"]
    with pytest.raises(IntegrityError, match="PAYLOAD_CLOSURE_MISMATCH"):
        verify_portable_lab_outcome(accepted_case["gate"], bundle, accepted_case["trust"])
