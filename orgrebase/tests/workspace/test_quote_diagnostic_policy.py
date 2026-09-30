"""Closed diagnostic-reason leaf cannot alter Quote handoff safety."""

from __future__ import annotations

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.store import StateStore
from orgrebase.workspace.quote_recovery_learning import (
    TARGET_SKILL,
    quote_recovery_content_bundle,
    recovery_handoff_input,
)
from orgrebase.workspace.skill_packages import (
    QUOTE_DIAGNOSTIC_REASON_CATALOG,
    SkillCandidateOverlayRegistry,
    SkillPackageRegistry,
)
from tests.workspace.test_quote_recovery_governed_learning import (
    AUTHOR,
    EVALUATOR,
    TENANT,
    WORKSPACE,
    _prepare,
    _service,
)


def _input(**evidence):
    return recovery_handoff_input(
        run_id="run:diagnostic", task_id="task:diagnostic",
        delegation_id="delegation:finance",
        delegation_task_digest=sha256_digest("task"),
        context_projection_digest=sha256_digest("context"),
        request_digest=evidence.get("request"),
        resume_digest=evidence.get("resume"),
        outcome_artifact_digest=evidence.get("outcome"),
    )


def _overlay(reason_map=None):
    registry = SkillPackageRegistry()
    return SkillCandidateOverlayRegistry(
        registry,
        candidate_ref="candidate:diagnostic",
        candidate_digest=sha256_digest("candidate:diagnostic"),
        proposed_version="pattern.diagnostic",
        source_run_id="run:diagnostic",
        target_skill=TARGET_SKILL,
        content_bundle=quote_recovery_content_bundle(registry).payload,
        diagnostic_reason_map=reason_map,
    )


def test_closed_diagnostic_map_only_changes_reason_for_exact_missing_receipt():
    generic = _overlay()
    refined = _overlay(QUOTE_DIAGNOSTIC_REASON_CATALOG)
    for missing, expected in (
        ("request", "QUOTE_RECOVERY_REQUEST_UNVERIFIED"),
        ("resume", "QUOTE_RECOVERY_RESUME_UNVERIFIED"),
        ("outcome", "QUOTE_RECOVERY_OUTCOME_PENDING"),
    ):
        evidence = {key: sha256_digest(key) for key in ("request", "resume", "outcome")}
        evidence[missing] = None
        prior = generic.interpret_candidate(generic.load(TARGET_SKILL), _input(**evidence))
        current = refined.interpret_candidate(refined.load(TARGET_SKILL), _input(**evidence))
        assert prior["action"] == current["action"] == "ABSTAIN"
        assert prior["reason"] == "QUOTE_RECOVERY_EVIDENCE_INCOMPLETE"
        assert current["reason"] == expected
        assert current["target_writes"] == 0
    complete = _input(**{key: sha256_digest(key) for key in ("request", "resume", "outcome")})
    assert refined.interpret_candidate(refined.load(TARGET_SKILL), complete)["action"] == "HANDOFF"


@pytest.mark.parametrize("mapping", [
    {"request": "HANDOFF"},
    {"owner": "QUOTE_RECOVERY_REQUEST_UNVERIFIED"},
    {"outcome": "QUOTE_RECOVERY_RESUME_UNVERIFIED"},
])
def test_diagnostic_policy_rejects_unknown_or_cross_mapped_codes(mapping):
    with pytest.raises(IntegrityError, match="DIAGNOSTIC_REASON_POLICY_DENIED"):
        _overlay(mapping)


def test_pattern_evaluation_keeps_diagnostic_leaf_unqualified_without_next_step_oracle(tmp_path):
    with StateStore(tmp_path / "p0.sqlite3", tenant_id=TENANT, workspace_id=WORKSPACE) as store:
        service = _service(store)
        proposal, boundary = _prepare(service)
        bundle = quote_recovery_content_bundle(service.registry)
        (candidate,) = service.propose(
            proposal,
            actor_id=AUTHOR,
            boundary=boundary,
            content_bundle=bundle.payload,
            diagnostic_reason_map=QUOTE_DIAGNOSTIC_REASON_CATALOG,
        )
        evaluation = service._load(service.evaluate(candidate, actor_id=EVALUATOR))
        assert evaluation["business_oracle"]["status"] == "IMPROVED"
        assert evaluation["verdict"] == "REJECTED"
        assert service._family("admission") == ()
