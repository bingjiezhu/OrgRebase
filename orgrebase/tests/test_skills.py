from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import orgrebase.skills as skills_module
from orgrebase.digest import sha256_digest
from orgrebase.domain import EvaluationSecurityError, IntegrityError, ObjectState
from orgrebase.fixture import EnterpriseFixture
from orgrebase.skills import (
    SKILL_ADAPTER_VERSION,
    EnterpriseLaunchReadinessSkill,
    SkillEvaluator,
    no_skill_baseline,
    skill_v12,
    skill_v13,
)

ROOT = Path(__file__).resolve().parents[1]


def test_known_bad_skill_fails_and_candidate_passes(fixture: EnterpriseFixture) -> None:
    evaluator = SkillEvaluator(fixture)
    baseline = evaluator.score("no-skill", no_skill_baseline)
    old = evaluator.score("1.2", skill_v12)
    candidate = evaluator.score("1.3", skill_v13)
    assert baseline.outcome == "FAIL"
    assert old.outcome == "FAIL"
    assert old.false_invalidations == 2
    assert old.unauthorized_disclosures == 1
    assert candidate.outcome == "PASS"
    assert candidate.passed == candidate.total == 8


def test_skill_candidate_handles_boundaries() -> None:
    assert skill_v13({"request_restricted_source": True}) == "DENY"
    assert skill_v13({"classification": "UNKNOWN"}) == "ESCALATE"
    assert skill_v13({"classification": None}) == "ABSTAIN"
    assert skill_v13({"classification": "NEW_UNTRUSTED_TYPE"}) == "ABSTAIN"
    assert no_skill_baseline({"request_restricted_source": True}) == "DENY"
    assert no_skill_baseline({}) == "ESCALATE"


def test_qualification_only_releases_to_canary(fixture: EnterpriseFixture) -> None:
    report = SkillEvaluator(fixture).qualify()
    assert report.previous_state == ObjectState.REQUALIFICATION_REQUIRED
    assert report.candidate_state == ObjectState.CANARY
    assert report.release_scope == ("org:northstar", "domain:gtm", "task:launch-readiness")
    assert report.premise_lock["random_seed"] == "0"
    assert report.skill_contract_digest.startswith("sha256:")
    assert report.evaluation_set_digest.startswith("sha256:")
    assert report.candidate_adapter_version == SKILL_ADAPTER_VERSION
    candidate_score = next(score for score in report.scores if score.version == "1.3")
    assert all(result["action_candidate_digest"] for result in candidate_score.case_results)


def test_evaluation_inputs_hide_held_out_case_identity(fixture: EnterpriseFixture) -> None:
    case = fixture.evaluation_cases[0]
    public = SkillEvaluator(fixture)._public_case_input(case)
    assert public["reason_code"] == "FROZEN_EVALUATION_CASE"
    assert public["object_id"].startswith("evaluation:")
    assert str(case["id"]).lower() not in public["object_id"]
    assert str(case["id"]) not in public["reason_code"]


def test_candidate_cannot_request_ground_truth(fixture: EnterpriseFixture) -> None:
    def attacker(_: dict[str, object]) -> str:
        return "REBASE"

    attacker.requests_ground_truth = True  # type: ignore[attr-defined]
    with pytest.raises(EvaluationSecurityError, match="held-out ground truth"):
        SkillEvaluator(fixture).score("attacker", attacker)


def test_qualified_skill_must_emit_bound_action_candidate(fixture: EnterpriseFixture) -> None:
    with pytest.raises(EvaluationSecurityError, match="bound action candidate"):
        SkillEvaluator(fixture).score("1.3", skill_v13, require_bound_candidate=True)


def test_executable_skill_emits_exactly_bound_side_effect_free_candidate() -> None:
    runtime = EnterpriseLaunchReadinessSkill()
    preview_digest = sha256_digest({"preview": "frozen"})
    result = runtime.execute(
        {
            "classification": "UNKNOWN",
            "object_id": "work:partner_brief_e",
            "reason_code": "COVERAGE_GAP",
            "preview_digest": preview_digest,
        }
    )
    assert result.action == "ESCALATE"
    assert result.preview_digest == preview_digest
    assert result.contract_digest == runtime.contract_digest
    assert result.candidate_only is True


def test_legacy_runtime_contract_is_separate_from_v14_skill_package() -> None:
    runtime = EnterpriseLaunchReadinessSkill()
    package_contract = json.loads(
        (ROOT / "skills/enterprise-launch-readiness/contract.json").read_text(
            encoding="utf-8"
        )
    )
    assert runtime.contract["version"] == "1.3"
    assert runtime.contract["inputs"]["schema"] == "orgrebase.impact-result.v1"
    assert package_contract["version"] == "1.4"
    assert package_contract["inputs"]["schema"] == "orgrebase.launch-readiness-input.v1"
    assert runtime.contract_digest != package_contract["content_digest"]


def test_legacy_runtime_rejects_permission_widening(monkeypatch: pytest.MonkeyPatch) -> None:
    widened = copy.deepcopy(skills_module.load_skill_contract())
    widened["permissions"]["side_effects"] = ["canonical_write"]
    monkeypatch.setattr(skills_module, "load_skill_contract", lambda: widened)
    with pytest.raises(IntegrityError, match="widened its executable boundary"):
        EnterpriseLaunchReadinessSkill()


def test_executable_skill_fails_closed_without_preview_binding() -> None:
    with pytest.raises(IntegrityError, match="invalid Preview digest"):
        EnterpriseLaunchReadinessSkill().execute(
            {
                "classification": "AFFECTED_HARD",
                "object_id": "work:sales_quote_a",
                "reason_code": "HARD_PATH",
                "preview_digest": "latest",
            }
        )
