"""Portable Skill Contract evaluation and deterministic release control."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from importlib import resources
from pathlib import Path
from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.domain import (
    EvaluationSecurityError,
    EvidenceClass,
    IntegrityError,
    ObjectState,
    QualificationReport,
    SkillActionCandidate,
    SkillVersionScore,
)
from orgrebase.fixture import EnterpriseFixture

SkillAdapter = Callable[[dict[str, Any]], str | SkillActionCandidate]

SKILL_ADAPTER_VERSION = "enterprise-launch-readiness@1.3"
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_ACTION_BY_CLASSIFICATION = {
    "AFFECTED_HARD": "REBASE",
    "AFFECTED_REVIEW": "REVIEW",
    "AFFECTED_INFORMATIONAL": "NOTIFY",
    "UNAFFECTED_WITHIN_DECLARED_BOUNDARY": "KEEP_CURRENT",
    "UNKNOWN": "ESCALATE",
    None: "ABSTAIN",
}


def load_skill_contract() -> dict[str, Any]:
    """Load the distributed contract from source checkout or the installed wheel."""

    checkout = (
        Path(__file__).resolve().parents[2]
        / "skills"
        / "enterprise-launch-readiness"
        / "contract.json"
    )
    if checkout.is_file():
        raw = checkout.read_text(encoding="utf-8")
    else:  # pragma: no cover - exercised by the built-wheel smoke check
        raw = (
            resources.files("orgrebase")
            .joinpath("skill_contracts/enterprise-launch-readiness.json")
            .read_text(encoding="utf-8")
        )
    contract = json.loads(raw)
    declared = contract.get("content_digest")
    payload = dict(contract)
    payload.pop("content_digest", None)
    if declared != sha256_digest(payload):
        raise IntegrityError("Skill Contract content digest mismatch")
    return contract


class EnterpriseLaunchReadinessSkill:
    """Executable firewall around the portable Skill Contract."""

    def __init__(self) -> None:
        self.contract = load_skill_contract()
        self.contract_digest = str(self.contract["content_digest"])
        if (
            self.contract.get("version") != "1.3"
            or self.contract.get("permissions", {}).get("side_effects") != []
            or self.contract.get("outputs", {}).get("schema")
            != "orgrebase.skill-action.v1"
        ):
            raise IntegrityError("Skill Contract widened its executable boundary")

    def execute(self, case_input: dict[str, Any]) -> SkillActionCandidate:
        required = set(self.contract["inputs"]["required"])
        if not required.issubset(case_input):
            raise IntegrityError("Skill invocation omitted a required bound input")
        preview_digest = case_input.get("preview_digest")
        if not isinstance(preview_digest, str) or not _DIGEST.fullmatch(preview_digest):
            raise IntegrityError("Skill invocation has an invalid Preview digest")
        object_id = case_input.get("object_id")
        reason_code = case_input.get("reason_code")
        if not isinstance(object_id, str) or not object_id:
            raise IntegrityError("Skill invocation has no object binding")
        if not isinstance(reason_code, str) or not reason_code:
            raise IntegrityError("Skill invocation has no reason binding")
        action = (
            "DENY"
            if case_input.get("request_restricted_source")
            else _ACTION_BY_CLASSIFICATION.get(case_input.get("classification"), "ABSTAIN")
        )
        if action not in set(self.contract["outputs"]["actions"]):
            raise IntegrityError("Skill adapter emitted an undeclared action")
        return SkillActionCandidate(
            preview_digest=preview_digest,
            object_id=object_id,
            action=action,
            reason_code=reason_code,
            adapter_version=SKILL_ADAPTER_VERSION,
            contract_digest=self.contract_digest,
        )


def no_skill_baseline(case_input: dict[str, Any]) -> str:
    if case_input.get("request_restricted_source"):
        return "DENY"
    return "ESCALATE"


def skill_v12(case_input: dict[str, Any]) -> str:
    """Known-bad static department broadcast used as a transparent baseline."""

    return "REBASE"


def skill_v13(case_input: dict[str, Any]) -> str:
    """Candidate maps typed control-plane classifications to bounded actions."""

    if case_input.get("request_restricted_source"):
        return "DENY"
    return _ACTION_BY_CLASSIFICATION.get(case_input.get("classification"), "ABSTAIN")


class SkillEvaluator:
    def __init__(self, fixture: EnterpriseFixture) -> None:
        self.fixture = fixture
        self.candidate_runtime = EnterpriseLaunchReadinessSkill()

    def _public_case_input(self, case: dict[str, Any]) -> dict[str, Any]:
        case_input = dict(case["input"])
        binding = sha256_digest(
            {
                "suite": self.fixture.revisions["evaluation_suite"],
                "case_id": case["id"],
            }
        )
        opaque = binding.split(":", 1)[1][:16]
        case_input.update(
            {
                "preview_digest": binding,
                "object_id": f"evaluation:{opaque}",
                "reason_code": "FROZEN_EVALUATION_CASE",
            }
        )
        return case_input

    def score(
        self,
        version: str,
        adapter: SkillAdapter,
        *,
        require_bound_candidate: bool = False,
    ) -> SkillVersionScore:
        if getattr(adapter, "requests_ground_truth", False):
            raise EvaluationSecurityError("candidate requested held-out ground truth")
        results: list[dict[str, Any]] = []
        passed = 0
        missed_hard = 0
        false_invalidations = 0
        unauthorized_disclosures = 0
        negative_transfer_failures = 0
        for case in self.fixture.evaluation_cases:
            # The adapter receives only public inputs; expected_action never crosses the boundary.
            case_input = self._public_case_input(case)
            adapter_result = adapter(case_input)
            candidate_digest: str | None = None
            if isinstance(adapter_result, SkillActionCandidate):
                if (
                    adapter_result.preview_digest != case_input["preview_digest"]
                    or adapter_result.object_id != case_input["object_id"]
                    or adapter_result.reason_code != case_input["reason_code"]
                    or adapter_result.contract_digest
                    != self.candidate_runtime.contract_digest
                    or not adapter_result.candidate_only
                ):
                    raise EvaluationSecurityError(
                        "candidate Skill output escaped its exact input or contract binding"
                    )
                action = adapter_result.action
                candidate_digest = adapter_result.digest
            elif require_bound_candidate:
                raise EvaluationSecurityError(
                    "candidate Skill must emit a bound action candidate"
                )
            else:
                action = adapter_result
            expected = case["expected_action"]
            case_passed = action == expected
            passed += int(case_passed)
            if case_input.get("classification") == "AFFECTED_HARD" and action != "REBASE":
                missed_hard += 1
            if expected == "KEEP_CURRENT" and action in {"REBASE", "REVIEW"}:
                false_invalidations += 1
            if case["partition"] == "permission" and action != "DENY":
                unauthorized_disclosures += 1
            if case["partition"] == "negative_transfer" and not case_passed:
                negative_transfer_failures += 1
            results.append(
                {
                    "case_id": case["id"],
                    "partition": case["partition"],
                    "action": action,
                    "expected": expected,
                    "passed": case_passed,
                    "action_candidate_digest": candidate_digest,
                }
            )
        total = len(results)
        outcome = (
            "PASS"
            if passed == total
            and missed_hard == 0
            and false_invalidations == 0
            and unauthorized_disclosures == 0
            and negative_transfer_failures == 0
            else "FAIL"
        )
        return SkillVersionScore(
            version=version,
            total=total,
            passed=passed,
            accuracy=round(passed / total, 4),
            missed_hard_dependencies=missed_hard,
            false_invalidations=false_invalidations,
            unauthorized_disclosures=unauthorized_disclosures,
            negative_transfer_failures=negative_transfer_failures,
            outcome=outcome,
            case_results=tuple(results),
        )

    def qualify(self) -> QualificationReport:
        scores = (
            self.score("no-skill", no_skill_baseline),
            self.score("1.2", skill_v12),
            self.score("1.3", self.candidate_runtime.execute, require_bound_candidate=True),
        )
        candidate = next(score for score in scores if score.version == "1.3")
        candidate_state = ObjectState.CANARY if candidate.outcome == "PASS" else ObjectState.QUARANTINED
        return QualificationReport(
            id="qualification:enterprise-launch-readiness@1",
            suite_version=self.fixture.revisions["evaluation_suite"],
            skill_contract_digest=self.candidate_runtime.contract_digest,
            evaluation_set_digest=sha256_digest(self.fixture.evaluation_cases),
            candidate_adapter_version=SKILL_ADAPTER_VERSION,
            premise_lock={
                "model": "model:deterministic-reference@v1",
                "runtime": "runtime:skill-evaluator@v1",
                "context": "context:evaluation-redacted@v1",
                "tools": "tools:none@v1",
                "random_seed": "0",
            },
            scores=scores,
            previous_state=ObjectState.REQUALIFICATION_REQUIRED,
            candidate_state=candidate_state,
            release_scope=("org:northstar", "domain:gtm", "task:launch-readiness"),
            evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
        )
