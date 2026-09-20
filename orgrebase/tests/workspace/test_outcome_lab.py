from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.workspace.evolution_evidence import _seal
from orgrebase.workspace.outcome_lab import (
    DIMENSIONS,
    LabBudget,
    LabToolGrant,
    LabToolRequest,
    OutcomeLab,
    OutcomeOracle,
    OutcomeTaskMapping,
)


class Environment:
    build_digest = sha256_digest("test-environment")

    def __init__(self) -> None:
        self.reset_count = 0
        self.call_count = 0
        self.closed = False
        self.unknown = False
        self.forbidden = False
        self.reset()

    def reset(self) -> str:
        self.reset_count += 1
        self.state = {"order": {"state": "pending"}, "unrelated": 1}
        return str(self.reset_count)

    def snapshot(self) -> dict[str, Any]:
        return deepcopy(self.state)

    def tool_mutates_state(self, tool: str) -> bool:
        return tool == "cancel"

    def call(self, request: LabToolRequest, *, timeout: float) -> Any:
        assert timeout > 0
        self.call_count += 1
        if request.tool == "cancel":
            self.state["order"]["state"] = "cancelled"
        if self.forbidden:
            self.state["unrelated"] += 1
        if self.unknown:
            raise TimeoutError("response lost after dispatch")
        return deepcopy(self.state["order"])

    def close(self) -> None:
        self.closed = True


def unit_resource(kind: str) -> dict[str, Any]:
    return _seal(
        {
            "apiVersion": "oac.dev/v0alpha1",
            "kind": kind,
            "metadata": {
                "id": "test:" + kind,
                "revision": 1,
                "namespace": "test:disposable-unit",
                "ownerRef": "test:owner",
                "governanceRef": "test:governance",
                "createdAt": "2026-09-10T00:00:00Z",
                "effectiveFrom": None,
                "effectiveTo": None,
                "sourceRefs": [],
            },
            "spec": {},
        }
    )


def approved_run(lab: OutcomeLab, requests: Any, system: str = "oac") -> dict[str, Any]:
    approval = lab.approve(system, authority="test:controller")
    return lab.run(approval["token"], requests, runtime_bundle=approval["runtime_bundle"])


class AcceptedGate:
    """Unit-test boundary: live public CLI coverage belongs to the installed experiment."""

    def verify(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return unit_resource("PlanCertificate")


@pytest.fixture
def setup() -> tuple[OutcomeLab, Environment, list[LabToolRequest]]:
    environment = Environment()
    snapshot, change, plan = (
        unit_resource(kind) for kind in ("OrganizationSnapshot", "ChangeSet", "OrganizationPlan")
    )
    requests = [LabToolRequest(tool=name, arguments={"order": "one"}) for name in ("read", "cancel")]
    oracle = OutcomeOracle(
        authority="test:oracle",
        policy_source_digest=sha256_digest("policy"),
        expected={"/order/state": "cancelled"},
    )
    mapping = OutcomeTaskMapping(
        upstream_repository="https://example.org/test-only",
        upstream_revision="0" * 40,
        task_id="unit-test",
        oac_profile="unit-test-profile",
        task_digest=sha256_digest("task"),
        environment_build_digest=environment.build_digest,
        seed_root=sha256_digest(environment.snapshot()),
        snapshot_digest=snapshot["digest"],
        change_digest=change["digest"],
        plan_digest=plan["digest"],
        subjects={"order:one": "/order"},
        grants=tuple(
            LabToolGrant(
                request=request,
                role=role,
                obligation_ref=role,
                work_unit_ref="wu:" + role,
                evidence_ref=role,
                mutates_state=request.tool == "cancel",
            )
            for request, role in zip(requests, ("review", "execute"), strict=True)
        ),
        work_unit_predecessors={"wu:review": (), "wu:execute": ()},
        system_roles={
            "fixed-team": ("review", "execute"),
            "initiator-only": ("review",),
            "graph-only": ("review", "execute"),
            "oac": ("review", "execute"),
        },
        writable_paths=("/order/state",),
        required_evidence=("review", "execute"),
        oracle_digest=oracle.digest,
        unsupported=("full-public-benchmark",),
        budget=LabBudget(tool_calls=3, write_calls=1, elapsed_seconds=10),
    )
    lab = OutcomeLab(
        mapping,
        oracle,
        environment,
        AcceptedGate(),
        snapshot=snapshot,
        change=change,
        plan=plan,
        controller_authority="test:controller",
        acting_authority="test:actor",
    )
    return lab, environment, requests


def test_receipt_observes_real_mutation_and_resets(setup: Any) -> None:
    lab, environment, requests = setup
    approval = lab.approve("oac", authority="test:controller")
    receipt = lab.run(approval["token"], requests, runtime_bundle=approval["runtime_bundle"])
    lab.verify_receipt(receipt)
    assert receipt["verdict"] == "ACCEPT"
    assert set(receipt["dimensions"]) == set(DIMENSIONS)
    assert receipt["initial_root"] != receipt["final_root"]
    assert environment.snapshot()["order"]["state"] == "pending"
    assert receipt["write_calls"] == 1


def test_plan_certificate_does_not_grant_execution(setup: Any) -> None:
    lab, environment, requests = setup
    approval = lab.approve("oac", authority="test:controller")
    with pytest.raises(IntegrityError, match="EXPLICIT_ONE_USE_APPROVAL"):
        lab.run(lab._certificate["digest"], requests, runtime_bundle=approval["runtime_bundle"])
    with pytest.raises(IntegrityError, match="APPROVAL_AUTHORITY"):
        lab.approve("oac", authority="test:actor")
    assert environment.call_count == 0


def test_superseded_and_reused_capabilities_are_denied(setup: Any) -> None:
    lab, environment, requests = setup
    old = lab.approve("oac", authority="test:controller")
    current = lab.approve("oac", authority="test:controller")
    with pytest.raises(IntegrityError, match="ONE_USE"):
        lab.run(old["token"], requests, runtime_bundle=old["runtime_bundle"])
    lab.run(current["token"], requests, runtime_bundle=current["runtime_bundle"])
    with pytest.raises(IntegrityError, match="ONE_USE"):
        lab.run(current["token"], requests, runtime_bundle=current["runtime_bundle"])
    assert environment.call_count == 2


@pytest.mark.parametrize(
    "tool_request",
    [
        LabToolRequest(tool="cancel", arguments={"order": "someone-else"}),
        LabToolRequest(tool="oracle.set", arguments={"expected": "pending"}),
        LabToolRequest(tool="reset", arguments={}),
        LabToolRequest(tool="__import__", arguments={"name": "os"}),
    ],
)
def test_actor_cannot_modify_oracle_environment_or_other_subject(
    setup: Any, tool_request: LabToolRequest
) -> None:
    lab, environment, _ = setup
    approved = lab.approve("oac", authority="test:controller")
    result = lab.run(approved["token"], [tool_request], runtime_bundle=approved["runtime_bundle"])
    assert result["trace"][0]["status"] == "DENIED"
    assert result["verdict"] == "REJECT"
    assert environment.call_count == 0


def test_lost_response_is_retained_without_retry(setup: Any) -> None:
    lab, environment, requests = setup
    environment.unknown = True
    approved = lab.approve("oac", authority="test:controller")
    result = lab.run(approved["token"], requests[1:] * 2, runtime_bundle=approved["runtime_bundle"])
    assert result["trace"][0]["status"] == "UNKNOWN"
    assert result["initial_root"] != result["final_root"]
    assert environment.call_count == 1
    assert result["dimensions"]["unresolved_observations"] == "UNKNOWN"


def test_forbidden_state_change_rejects_even_when_task_goal_passes(setup: Any) -> None:
    lab, environment, requests = setup
    environment.forbidden = True
    result = approved_run(lab, requests)
    assert result["dimensions"]["task_goal"] == "PASS"
    assert result["dimensions"]["forbidden_effects"] == "FAIL"
    assert result["verdict"] == "REJECT"


def test_transient_forbidden_write_cannot_hide_behind_final_state(setup: Any) -> None:
    lab, environment, requests = setup
    original = environment.call

    def transient(request: LabToolRequest, *, timeout: float) -> Any:
        result = original(request, timeout=timeout)
        environment.state["unrelated"] = 2 if environment.call_count == 1 else 1
        return result

    environment.call = transient
    result = approved_run(lab, requests)
    assert result["dimensions"]["task_goal"] == "PASS"
    assert result["dimensions"]["forbidden_effects"] == "FAIL"
    assert result["verdict"] == "REJECT"


def test_write_budget_stops_dispatch(setup: Any) -> None:
    lab, environment, requests = setup
    result = approved_run(lab, requests[1:] * 3)
    assert environment.call_count == 1
    assert result["trace"][1]["reason"] == "LAB_WRITE_BUDGET_EXHAUSTED"
    assert result["verdict"] == "REJECT"


def test_all_systems_keep_failures_in_the_matched_denominator(setup: Any) -> None:
    lab, _environment, requests = setup
    results = [approved_run(lab, requests, system) for system in lab.mapping.system_roles]
    assert len(results) == 4
    assert len({result["initial_root"] for result in results}) == 1
    assert len({result["approval_receipt"]["budget_digest"] for result in results}) == 1
    assert [result["verdict"] for result in results].count("REJECT") == 1


def test_six_dimension_profile_cannot_be_shortened_or_relabelled(setup: Any) -> None:
    lab, _, requests = setup
    result = approved_run(lab, requests)
    broken = json.loads(json.dumps(result))
    del broken["dimensions"]["scope"]
    broken.pop("digest")
    broken["digest"] = sha256_digest(broken)
    with pytest.raises(IntegrityError, match="SIX_OUTCOME_DIMENSIONS"):
        lab.verify_receipt(broken)


def test_changed_mapping_after_approval_is_rejected(setup: Any) -> None:
    lab, environment, requests = setup
    approval = lab.approve("oac", authority="test:controller")
    lab.mapping.system_roles["initiator-only"] = ("review", "execute")
    with pytest.raises(ValueError, match="digest"):
        lab.run(approval["token"], requests, runtime_bundle=approval["runtime_bundle"])
    assert environment.call_count == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("acting_authority", "another:actor"),
        ("oracle_authority", "test:actor"),
        ("plan_certificate_digest", sha256_digest("different-plan")),
        ("write_calls", 0),
        ("tool_calls", True),
        ("observed_evidence", []),
        ("goals", []),
        ("final_root", sha256_digest("unobserved-state")),
        ("claim_scope", "PRODUCTION_SAFE"),
    ],
)
def test_rehashed_receipt_cannot_replace_trusted_context_or_observations(
    setup: Any, field: str, value: Any
) -> None:
    lab, _, requests = setup
    receipt = approved_run(lab, requests)
    receipt[field] = value
    receipt.pop("digest")
    receipt["digest"] = sha256_digest(receipt)
    with pytest.raises(IntegrityError):
        lab.verify_receipt(receipt)


def test_rehashed_pass_cannot_hide_failed_oracle(setup: Any) -> None:
    lab, _, requests = setup
    receipt = approved_run(lab, requests[:1])
    assert receipt["verdict"] == "REJECT"
    receipt["dimensions"] = {key: "PASS" for key in DIMENSIONS}
    receipt["verdict"] = "ACCEPT"
    receipt.pop("digest")
    receipt["digest"] = sha256_digest(receipt)
    with pytest.raises(IntegrityError, match="ASSESSMENT_MISMATCH"):
        lab.verify_receipt(receipt)


def test_work_unit_order_is_enforced_at_dispatch(setup: Any) -> None:
    lab, environment, requests = setup
    value = lab.mapping.model_dump(mode="json")
    value["work_unit_predecessors"]["wu:execute"] = ["wu:review"]
    value.pop("digest")
    lab.mapping = OutcomeTaskMapping.model_validate(value)
    denied = approved_run(lab, requests[::-1])
    assert environment.call_count == 1
    assert denied["trace"][0]["reason"] == "LAB_WORK_UNIT_ORDER_DENIED"
    assert denied["dimensions"]["task_goal"] == "FAIL"
    lab.verify_receipt(denied)
    accepted = approved_run(lab, requests)
    assert accepted["verdict"] == "ACCEPT"
    lab.verify_receipt(accepted)


def test_partial_evidence_does_not_complete_a_work_unit(setup: Any) -> None:
    lab, _, requests = setup
    value = lab.mapping.model_dump(mode="json")
    extra = LabToolGrant(
        request=LabToolRequest(tool="read", arguments={"order": "two"}),
        role="review",
        obligation_ref="review",
        work_unit_ref="wu:review",
        evidence_ref="review",
        mutates_state=False,
    )
    value["grants"].append(extra.model_dump(mode="json"))
    value.pop("digest")
    lab.mapping = OutcomeTaskMapping.model_validate(value)
    receipt = approved_run(lab, requests)
    assert receipt["dimensions"]["task_goal"] == "PASS"
    assert receipt["dimensions"]["evidence_completion"] == "FAIL"
    lab.verify_receipt(receipt)


def test_late_return_is_unknown_even_with_observable_success(setup: Any) -> None:
    from threading import Event

    lab, environment, requests = setup
    value = lab.mapping.model_dump(mode="json")
    value["budget"] = LabBudget(tool_calls=3, write_calls=1, elapsed_seconds=0.02).model_dump(mode="json")
    value.pop("digest")
    lab.mapping = OutcomeTaskMapping.model_validate(value)
    original = environment.call

    def slow(request: LabToolRequest, *, timeout: float) -> Any:
        result = original(request, timeout=timeout)
        Event().wait(0.04)
        return result

    environment.call = slow
    receipt = approved_run(lab, requests[1:] * 2)
    assert environment.call_count == 1
    assert receipt["trace"][0]["status"] == "UNKNOWN"
    assert receipt["dimensions"]["task_goal"] == "PASS"
    assert receipt["dimensions"]["unresolved_observations"] == "UNKNOWN"
    lab.verify_receipt(receipt)


def test_lost_final_observation_keeps_unknown_instead_of_false_failure(setup: Any) -> None:
    lab, environment, requests = setup
    original = environment.snapshot
    reads = 0

    def missing_final() -> dict[str, Any]:
        nonlocal reads
        reads += 1
        # approve, initial, before/after twice, final, then reset observation.
        if reads == 7:
            raise EOFError("final state read unavailable")
        return original()

    environment.snapshot = missing_final
    receipt = approved_run(lab, requests)
    assert receipt["verdict"] == "UNKNOWN"
    assert receipt["final_root"] is None
    assert receipt["dimensions"]["task_goal"] == "UNKNOWN"
    assert receipt["dimensions"]["evidence_completion"] == "PASS"
    lab.verify_receipt(receipt)
