from __future__ import annotations

import json
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.workspace.competition_run import _quote_skill_evaluation_cases
from orgrebase.workspace.quote_skill_qualification import qualification_premise
from orgrebase.workspace.service import WorkspaceService
from orgrebase.workspace.skill_packages import (
    SKILL_REGISTRY_AUTHORITY,
    InvocationContext,
    SkillPackageEvaluator,
    SkillPackageRegistry,
    SkillReleaseLedger,
)

ROOT = Path(__file__).resolve().parents[2]


def _record(body: dict[str, Any]) -> dict[str, Any]:
    return {**body, "digest": sha256_digest(body)}


def _released_skill_chain(
    *, legacy: bool = False, run_id: str = "run:test:golden-skill-release",
    public_input: dict[str, Any] | None = None, context: InvocationContext | None = None,
) -> dict[str, Any]:
    registry = SkillPackageRegistry(ROOT)
    package = registry.load("enterprise-quote-compose")
    evidence_digest = sha256_digest({"evidence": "golden-skill-release"})
    public_input = public_input or {
        "skill_partition": "replay",
        "candidate_program_digest_required": package.manifest["program_content_digest"],
        "dependency_tool_receipt_digest": evidence_digest,
        "dependency_result_digest": evidence_digest,
        "coalition_result_binding_digest": evidence_digest,
        "domain_result_digests": {
            domain: sha256_digest({"domain": domain}) for domain in ("product", "legal", "finance", "gtm")
        },
    }
    evaluator = SkillPackageEvaluator(registry)
    cases = _quote_skill_evaluation_cases(run_id=run_id, public_input=public_input)
    evaluation = evaluator.evaluate(
        "enterprise-quote-compose",
        cases[:-1] if legacy else cases,
        evaluated_at="2026-08-27T00:01:00Z",
        premise_lock=None if legacy else qualification_premise(package),
    )
    ledger = SkillReleaseLedger(registry, evaluator)
    for index, state in enumerate(("EVALUATED", "SHADOW", "CANARY"), start=1):
        ledger.transition(
            "enterprise-quote-compose",
            evaluation,
            to_state=state,
            actor_id=SKILL_REGISTRY_AUTHORITY,
            reason_codes=(f"GOLDEN_RUN_QUALIFIED_FOR_{state}",),
            created_at=f"2026-08-27T00:01:{index}0Z",
        )
    invocation = ledger.invoke(
        "enterprise-quote-compose",
        public_input,
        context=context or InvocationContext(
            run_id=run_id,
            task_id="task:test:quote-compose",
            delegation_id="delegation:test:quote-compose",
            actor_id="worker:gtm-steward",
        ),
        observed_dependencies=package.manifest["dependencies"],
        created_at="2026-08-27T00:02:00Z",
    )
    release_history = list(ledger.history)
    summary = {
        "skill_authorization_mode": "RELEASE",
        "skill_invocation_receipt_digest": invocation.receipt["digest"],
        "skill_evaluation_receipt_digest": evaluation["digest"],
        "skill_evaluation_verdict": "CANARY",
        "skill_evaluation_partition_count": 8,
        **({"skill_evaluation_case_count": len(cases)} if not legacy else {}),
        "skill_release_receipt_digest": release_history[-1]["digest"],
        "skill_release_state": "CANARY",
        "skill_release_transition_count": 3,
    }
    return {
        "run_id": run_id,
        "package": package.manifest,
        "evaluation": evaluation,
        "release_history": release_history,
        "invocation": invocation.receipt,
        "result": invocation.result,
        "summary": summary,
    }


def _verify(chain: dict[str, Any]) -> dict[str, Any]:
    return WorkspaceService._verify_golden_skill_release_evidence(
        package=chain["package"],
        evaluation=chain["evaluation"],
        release_history=chain["release_history"],
        invocation=chain["invocation"],
        result=chain["result"],
        summary=chain["summary"],
        execution_run_id=chain["run_id"],
    )


@pytest.mark.parametrize("legacy", [False, True])
def test_golden_skill_projection_requires_exact_release_authority_chain(legacy: bool) -> None:
    projection = _verify(_released_skill_chain(legacy=legacy))

    assert projection["authorization_mode"] == "RELEASE"
    assert projection["release_state"] == "CANARY"
    assert projection["evaluation_partition_count"] == 8
    if legacy:
        assert "evaluation_case_count" not in projection
        assert "qualification_suite_revision" not in projection
    else:
        assert projection["evaluation_case_count"] == 9
        assert projection["qualification_suite_revision"] == "orgrebase.quote-skill-qualification.v2"
    assert projection["release_transition_count"] == 3


@pytest.mark.parametrize("mutation", ["partition_is_case_count", "case_is_partition_count", "missing_case_count"])
def test_golden_skill_projection_rejects_mixed_partition_and_case_counts(mutation):
    chain = _released_skill_chain()
    if mutation == "partition_is_case_count":
        chain["summary"]["skill_evaluation_partition_count"] = 9
    elif mutation == "case_is_partition_count":
        chain["summary"]["skill_evaluation_case_count"] = 8
    else:
        chain["summary"].pop("skill_evaluation_case_count")
    with pytest.raises(RuntimeError, match="WORKSPACE_GOLDEN_SKILL_EVALUATION_INVALID"):
        _verify(chain)


@pytest.mark.parametrize("legacy", [False, True])
def test_actual_golden_collaboration_preserves_qualification_for_console(tmp_path, legacy):
    """Replay retained process evidence; execute only the bounded Skill locally."""
    from tests.workspace.test_golden_pilot_integration import _read_json, _service, _write_json
    from tests.workspace.test_workspace_client import run_node

    frozen = ROOT / "evidence/golden-competition/latest/pilot/golden-run"
    retained_summary = _read_json(frozen / "summary.json")

    def runner(**kwargs):
        output = Path(kwargs["output_dir"])
        shutil.copytree(frozen, output, dirs_exist_ok=True)
        summary = _read_json(output / "summary.json")
        if not legacy:
            receipt = _read_json(output / "skill/receipt.json")
            chain = _released_skill_chain(
                run_id=summary["run_id"], public_input=_read_json(output / "skill/input.json"),
                context=InvocationContext(**{key: receipt[key] for key in
                    ("run_id", "task_id", "delegation_id", "actor_id")}),
            )
            for name, value in (("package", chain["package"]), ("evaluation", chain["evaluation"]),
                                ("release-ledger", chain["release_history"]),
                                ("receipt", chain["invocation"]), ("result", chain["result"])):
                _write_json(output / "skill" / f"{name}.json", value)
            summary.update(chain["summary"])
            prepared = _read_json(output / "prepared-formation-bundle.json")
            for key in ("skill_evaluation_receipt_digest", "skill_release_receipt_digest", "skill_invocation_receipt_digest"):
                prepared["event_payload"][key] = summary[key]
            prepared = _record({key: value for key, value in prepared.items() if key != "digest"})
            _write_json(output / "prepared-formation-bundle.json", prepared)
            summary["prepared_formation_digest"] = prepared["digest"]
            summary = _record({key: value for key, value in summary.items() if key != "digest"})
            _write_json(output / "summary.json", summary)
        return summary

    service = _service(tmp_path, mode="golden", runner=runner)
    try:
        service._activate_competition_execution_binding(retained_summary["run_id"])
        _, evidence = service._prepare_golden_competition(service.profile.task_request())
        skill = evidence["agent_collaboration"]["skill"]
        assert skill["evaluation_partition_count"] == 8
        assert skill.get("evaluation_case_count") == (None if legacy else 9)
        assert skill.get("qualification_suite_revision") == (None if legacy else "orgrebase.quote-skill-qualification.v2")
        if not legacy:
            assert skill["qualification_suite_digest"] == qualification_premise(
                SkillPackageRegistry(ROOT).load("enterprise-quote-compose"))["qualification_suite_digest"]
        # Exercise the shipped console's exact gate with the actual service output.
        run_node("const skill=" + json.dumps(skill) + r""";
const assert=require('node:assert/strict');
const source=fs.readFileSync('demo/console/app.js','utf8');
const expression=source.split('const skillQualificationReady = ')[1].split(';')[0];
assert.equal(new Function('skill','return '+expression)(skill),true);
""")
    finally:
        service.close()


@pytest.mark.parametrize("mutation", ["evaluation_mode", "foreign_release_head"])
def test_golden_skill_projection_rejects_non_authoritative_invocation(
    mutation: str,
) -> None:
    chain = deepcopy(_released_skill_chain())
    body = dict(chain["invocation"])
    body.pop("digest")
    if mutation == "evaluation_mode":
        body["authorization_mode"] = "EVALUATION"
        body["release_receipt_digest"] = None
        chain["summary"]["skill_authorization_mode"] = "EVALUATION"
    else:
        body["release_receipt_digest"] = sha256_digest({"foreign": "release-head"})
    chain["invocation"] = _record(body)
    chain["summary"]["skill_invocation_receipt_digest"] = chain["invocation"]["digest"]

    with pytest.raises(
        RuntimeError,
        match="WORKSPACE_GOLDEN_SKILL_RELEASE_AUTHORITY_INVALID",
    ):
        _verify(chain)


def test_golden_skill_projection_rejects_incomplete_evaluation_partition() -> None:
    chain = deepcopy(_released_skill_chain())
    evaluation_body = dict(chain["evaluation"])
    evaluation_body.pop("digest")
    evaluation_body["case_results"] = evaluation_body["case_results"][:-1]
    chain["evaluation"] = _record(evaluation_body)
    chain["summary"]["skill_evaluation_receipt_digest"] = chain["evaluation"]["digest"]
    chain["summary"]["skill_evaluation_partition_count"] = 7

    with pytest.raises(
        RuntimeError,
        match="WORKSPACE_GOLDEN_SKILL_EVALUATION_INVALID",
    ):
        _verify(chain)


def test_golden_skill_projection_rejects_self_consistent_foreign_release_head() -> None:
    chain = deepcopy(_released_skill_chain())
    head_body = dict(chain["release_history"][-1])
    head_body.pop("digest")
    head_body["reason_codes"] = ["FOREIGN_CANARY_AUTHORIZATION"]
    foreign_head = _record(head_body)
    chain["release_history"][-1] = foreign_head

    invocation_body = dict(chain["invocation"])
    invocation_body.pop("digest")
    invocation_body["release_receipt_digest"] = foreign_head["digest"]
    chain["invocation"] = _record(invocation_body)
    chain["summary"]["skill_release_receipt_digest"] = foreign_head["digest"]
    chain["summary"]["skill_invocation_receipt_digest"] = chain["invocation"]["digest"]

    with pytest.raises(
        RuntimeError,
        match="WORKSPACE_GOLDEN_SKILL_RELEASE_LEDGER_INVALID",
    ):
        _verify(chain)
