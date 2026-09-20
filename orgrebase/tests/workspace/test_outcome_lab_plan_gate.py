"""Binding checks with a frozen public CLI certificate, isolated from CLI execution."""

from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from orgrebase.domain import IntegrityError
from orgrebase.workspace.evolution_evidence import _seal
from orgrebase.workspace.oac_wire import _resource_ref
from orgrebase.workspace.outcome_lab import OACPlanGate, OutcomeTaskMapping

FIXTURE = Path(__file__).parents[1] / "fixtures" / "outcome-lab-retail"


@pytest.fixture
def plan_case(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    values = {
        name: json.loads((FIXTURE / f"{name}.json").read_text())
        for name in ("snapshot", "change", "plan", "plan-certificate", "mapping")
    }
    mapping = values["mapping"]
    mapping.pop("digest")
    plan = values["plan"]["spec"]
    mapping["work_unit_predecessors"] = {unit["workUnitId"]: [] for unit in plan["workUnits"]}
    for constraint in plan["happensBefore"]:
        mapping["work_unit_predecessors"][constraint["successorRef"]].append(constraint["predecessorRef"])
    for grant in mapping["grants"]:
        grant.pop("digest")
        grant["work_unit_ref"] = next(
            unit["workUnitId"]
            for unit in plan["workUnits"]
            if grant["obligation_ref"] in unit["obligationRefs"]
        )
    values["mapping"] = OutcomeTaskMapping.model_validate(mapping)
    values["gate"] = OACPlanGate(("unit-certificate-transport",), environment={})
    values["calls"] = []

    def transport(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        values["calls"].append(command)
        assert command[1] == "verify"
        for name, filename in zip(("snapshot", "change", "plan"), command[2:5], strict=True):
            assert json.loads(Path(filename).read_text()) == values[name]
        assert command[5:7] == ["--profile", values["mapping"].oac_profile]
        Path(command[-1]).write_text(json.dumps(values["plan-certificate"]))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", transport)
    return values


def verify(values: dict[str, Any]) -> dict[str, Any]:
    return values["gate"].verify(
        values["mapping"], snapshot=values["snapshot"], change=values["change"], plan=values["plan"]
    )


def test_exact_work_unit_mapping_binds_the_accepted_public_certificate(plan_case: Any) -> None:
    assert verify(plan_case) == plan_case["plan-certificate"]
    assert len(plan_case["calls"]) == 1


@pytest.mark.parametrize("scope", ("plan", "roleInstances", "workUnits"))
def test_disposable_grant_cannot_relabel_a_nonzero_plan(plan_case: Any, scope: str) -> None:
    plan = deepcopy(plan_case["plan"])
    target = plan["spec"] if scope == "plan" else plan["spec"][scope][0]
    target["effectCeiling"] = "controlled_effect"
    plan_case["plan"] = _seal(plan)
    mapping = plan_case["mapping"].model_dump(mode="json")
    mapping.pop("digest")
    mapping["plan_digest"] = plan_case["plan"]["digest"]
    plan_case["mapping"] = OutcomeTaskMapping.model_validate(mapping)
    certificate = deepcopy(plan_case["plan-certificate"])
    certificate["spec"]["subjectPlanRef"] = _resource_ref(plan_case["plan"])
    plan_case["plan-certificate"] = _seal(certificate)
    with pytest.raises(IntegrityError, match="LAB_PLAN_MUST_REMAIN_ZERO_EFFECT"):
        verify(plan_case)


@pytest.mark.parametrize(
    "mutation", ("root", "role", "evidence", "obligation", "unit", "order", "subject", "oac-roles")
)
def test_mapping_cannot_reinterpret_an_accepted_plan(plan_case: Any, mutation: str) -> None:
    values = plan_case["mapping"].model_dump(mode="json")
    values.pop("digest")
    grants = values["grants"]
    units = list(values["work_unit_predecessors"])
    read = next(grant for grant in grants if not grant["mutates_state"])
    write = next(grant for grant in grants if grant["mutates_state"])
    if mutation == "root":
        values["plan_digest"] = "sha256:" + "0" * 64
    elif mutation == "role":
        write["role"] = read["role"]
        write.pop("digest")
    elif mutation == "evidence":
        read["evidence_ref"] = write["evidence_ref"]
        read.pop("digest")
    elif mutation == "obligation":
        read["obligation_ref"] = "absent:obligation"
        read.pop("digest")
    elif mutation == "unit":
        write["work_unit_ref"] = read["work_unit_ref"]
        write.pop("digest")
    elif mutation == "order":
        values["work_unit_predecessors"][units[0]] = [units[1]]
    elif mutation == "subject":
        values["subjects"].pop("order:5056519")
    else:
        values["system_roles"]["oac"] = [read["role"]]
    plan_case["mapping"] = OutcomeTaskMapping.model_validate(values)
    with pytest.raises(IntegrityError):
        verify(plan_case)
    if mutation == "root":
        assert not plan_case["calls"]


@pytest.mark.parametrize("mutation", ("rejected", "unresolved", "dimensions", "root"))
def test_certificate_cannot_downgrade_its_own_verification_scope(plan_case: Any, mutation: str) -> None:
    certificate = deepcopy(plan_case["plan-certificate"])
    if mutation == "rejected":
        certificate["spec"]["verdict"] = "REJECT"
    elif mutation == "unresolved":
        certificate["spec"]["unresolvedRefs"] = ["unknown:fact"]
    elif mutation == "dimensions":
        certificate["spec"]["dimensions"][0]["verdict"] = "UNKNOWN"
    else:
        certificate["spec"]["subjectPlanRef"]["digest"] = "sha256:" + "0" * 64
    plan_case["plan-certificate"] = _seal(certificate)
    with pytest.raises(IntegrityError):
        verify(plan_case)


def test_cli_failure_cannot_leave_a_usable_plan_admission(
    plan_case: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, 2, "", "denied")
    )
    with pytest.raises(IntegrityError, match="PLAN_VERIFICATION_FAILED"):
        verify(plan_case)
