from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from orgrebase.agentteams_source import default_agentteams_checkout
from orgrebase.digest import sha256_digest
from orgrebase.workspace.agentteams_execution_plan import (
    AgentTeamsExecutionPlan,
    AgentTeamsExecutionTask,
)
from orgrebase.workspace.formation_taskflow_probe import (
    EVIDENCE_CLASS,
    FormationTaskflowProbeError,
    run_formation_taskflow_probe,
)

ROOT = Path(__file__).resolve().parents[2]
CHECKOUT = default_agentteams_checkout(ROOT)
LOCK = ROOT / "agentteams/teamharness-lock.json"
VERIFIER = ROOT / "scripts/verify_formation_taskflow_probe.py"
DIGESTS = {
    name: sha256_digest({"fixture": name})
    for name in (
        "task",
        "formation",
        "context",
        "coalition",
        "legal-card",
        "legal-projection",
        "product-card",
        "product-projection",
        "input-schema",
        "output-schema",
        "review-input",
        "review-output",
    )
}


def _domain_task(domain: str) -> AgentTeamsExecutionTask:
    return AgentTeamsExecutionTask(
        task_id=f"at-domain-{domain}-probe",
        task_kind="DOMAIN",
        domain_id=domain,
        assignee_actor_id=f"{domain}-steward",
        depends_on=(),
        formation_receipt_id="formation-receipt:two-domain-probe",
        formation_receipt_digest=DIGESTS["formation"],
        context_envelope_ref="task-agent-context:two-domain-probe@v1",
        context_envelope_digest=DIGESTS["context"],
        capability_card_ref=f"capability:{domain}@v1",
        capability_card_digest=DIGESTS[f"{domain}-card"],
        actor_projection_ref=f"projection:{domain}@v1",
        actor_projection_digest=DIGESTS[f"{domain}-projection"],
        input_schema_refs=("schema:workspace.domain-delegation@v1",),
        input_schema_digest=DIGESTS["input-schema"],
        output_schema_refs=("schema:workspace.domain-candidate-bundle@v1",),
        output_schema_digest=DIGESTS["output-schema"],
    )


def _two_domain_plan() -> AgentTeamsExecutionPlan:
    legal = _domain_task("legal")
    product = _domain_task("product")
    reviewer = AgentTeamsExecutionTask(
        task_id="at-reviewer-barrier-probe",
        task_kind="REVIEWER_BARRIER",
        domain_id="reviewer",
        assignee_actor_id="independent-reviewer",
        depends_on=(legal.task_id, product.task_id),
        formation_receipt_id="formation-receipt:two-domain-probe",
        formation_receipt_digest=DIGESTS["formation"],
        context_envelope_ref="task-agent-context:two-domain-probe@v1",
        context_envelope_digest=DIGESTS["context"],
        input_schema_refs=(
            f"agentteams-output:{legal.task_id}",
            f"agentteams-output:{product.task_id}",
        ),
        input_schema_digest=DIGESTS["review-input"],
        output_schema_refs=("schema:orgrebase.agentteams-review-candidate@v1",),
        output_schema_digest=DIGESTS["review-output"],
    )
    return AgentTeamsExecutionPlan(
        id="agentteams-execution-plan:two-domain-probe",
        task_ref="task:two-domain-probe",
        task_digest=DIGESTS["task"],
        formation_receipt_id="formation-receipt:two-domain-probe",
        formation_receipt_digest=DIGESTS["formation"],
        context_envelope_ref="task-agent-context:two-domain-probe@v1",
        context_envelope_digest=DIGESTS["context"],
        coalition_plan_ref="coalition:two-domain-probe",
        coalition_plan_digest=DIGESTS["coalition"],
        selected_domain_ids=("legal", "product"),
        tasks=(legal, product, reviewer),
        revision_lock={
            "capability_catalog": "catalog@two-domain-probe",
            "policy": "policy@two-domain-probe",
        },
    )


def _verifier() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_formation_taskflow_independent_verifier",
        VERIFIER,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def probe_pack(tmp_path_factory: pytest.TempPathFactory):
    if not CHECKOUT.is_dir():
        pytest.skip("pinned AgentTeams checkout is unavailable")
    output = tmp_path_factory.mktemp("formation-taskflow-probe")
    plan = _two_domain_plan()
    receipt = run_formation_taskflow_probe(
        plan=plan,
        checkout=CHECKOUT,
        lock_path=LOCK,
        output_dir=output,
        run_id="run:orgrebase:formation-taskflow:test-two-domain",
    )
    return output, plan, receipt


def test_probe_materializes_exact_two_domain_topology(probe_pack) -> None:
    output, plan, receipt = probe_pack
    assert receipt.evidence_class == EVIDENCE_CLASS
    assert receipt.execution_plan_digest == plan.digest
    assert receipt.selected_domain_ids == ("legal", "product")
    assert receipt.expected_task_ids == tuple(item.task_id for item in plan.tasks)
    assert receipt.planned_task_ids == receipt.expected_task_ids
    assert receipt.actual_terminal_task_ids == receipt.expected_task_ids
    assert receipt.project_terminal_state == "completed"
    assert receipt.candidate_only is True
    assert receipt.canonical_target_writes == 0
    assert len(receipt.task_receipts) == 3
    assert receipt.task_receipts[-1].depends_on == receipt.expected_task_ids[:-1]
    assert receipt.agentteams_action_count == 20

    public = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in sorted(item for item in output.rglob("*") if item.is_file())
    )
    assert "finance-steward" not in public
    assert "gtm-steward" not in public


def test_independent_verifier_recomputes_plan_native_and_terminal_sets(probe_pack) -> None:
    output, plan, receipt = probe_pack
    result = _verifier().verify_formation_taskflow_probe(
        evidence_dir=output,
        lock_path=LOCK,
        checkout=CHECKOUT,
    )
    assert result["status"] == "PASS"
    assert result["verification_strength"] == "PINNED_CHECKOUT_REPLAY"
    assert result["execution_plan_digest"] == plan.digest
    assert result["actual_task_ids"] == list(receipt.actual_terminal_task_ids)
    assert result["selected_domain_ids"] == ["legal", "product"]
    assert result["planned_domain_ids"] == ["legal", "product"]
    assert result["actual_domain_ids"] == ["legal", "product"]
    assert result["topology_match"] is True
    assert result["task_binding_count"] == 3
    assert result["agentteams_actions"] == 20
    assert result["canonical_target_writes"] == 0


@pytest.mark.parametrize("mutation", ("add", "remove", "substitute"))
def test_independent_verifier_rejects_native_task_set_mutation(
    probe_pack,
    tmp_path: Path,
    mutation: str,
) -> None:
    source, _plan, _receipt = probe_pack
    attacked = tmp_path / mutation
    shutil.copytree(source, attacked)
    journal = json.loads((attacked / "action-journal.json").read_text(encoding="utf-8"))
    plan_action = next(item for item in journal if item["action"] == "plan_dag")
    raw_path = attacked / plan_action["raw_ref"]
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    tasks: list[dict[str, Any]] = raw["request"]["payload"]["tasks"]
    if mutation == "add":
        tasks.append(
            {
                "taskId": "at-domain-finance-injected",
                "title": "Finance injected task",
                "assignedTo": "@finance-steward:controlled.local",
                "dependsOn": [],
            }
        )
    elif mutation == "remove":
        tasks.pop(0)
    else:
        tasks[0]["taskId"] = "at-domain-legal-substituted"
    raw_path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    verifier = _verifier()
    with pytest.raises(
        verifier.FormationTaskflowVerificationError,
        match="FORMATION_TASKFLOW_ACTUAL_TASK_SET_MISMATCH",
    ):
        verifier.verify_formation_taskflow_probe(
            evidence_dir=attacked,
            lock_path=LOCK,
        )


def test_probe_rejects_non_two_domain_execution_plan(tmp_path: Path) -> None:
    plan = _two_domain_plan()
    malformed = plan.model_copy(update={"selected_domain_ids": ("legal",)})
    with pytest.raises(
        FormationTaskflowProbeError,
        match="FORMATION_TASKFLOW_PLAN_INVALID",
    ):
        run_formation_taskflow_probe(
            plan=malformed,
            checkout=CHECKOUT,
            lock_path=LOCK,
            output_dir=tmp_path / "invalid",
        )
