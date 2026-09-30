"""V2 case judgments remain separate from the historical Pattern corpus."""

from __future__ import annotations

import pytest

from orgrebase.auth import request_principal
from orgrebase.domain import IntegrityError
from orgrebase.workspace.experience_assessment import ExperienceAssessmentService
from orgrebase.workspace.pattern_evolution import GovernedPatternService
from orgrebase.workspace.pattern_governance import PatternAuthorityScope, PrincipalPatternGovernance
from orgrebase.workspace.quote_recovery_learning import TARGET_SKILL, quote_recovery_content_bundle
from tests.workspace.test_experience_assessment import _corpus, _evaluator, _observed_case

FINANCE_PROFILE = "workspace-change-explanation-v1"


def _controller(workspace):
    pattern = GovernedPatternService(
        workspace.store,
        corpus_authority="actor:corpus",
        evaluator_authority="actor:evaluator",
        governance_authority="actor:governor",
    )
    return PrincipalPatternGovernance(
        pattern,
        PatternAuthorityScope(
            tenant_id="org:test",
            workspace_id=workspace.store.workspace_id,
            corpus_actor_id="actor:corpus",
            author_actor_id="actor:author",
            evaluator_actor_id="actor:evaluator",
            governor_actor_id="actor:governor",
            reviewed_bundle_digest=quote_recovery_content_bundle(pattern.registry).digest,
            reviewed_target_skill=TARGET_SKILL,
            reviewed_predecessor_package_digest=pattern.registry.load(TARGET_SKILL).package_digest,
        ),
    )


def test_governed_v2_corpus_counts_proven_cluster_without_v1_label_rewrite(tmp_path):
    workspace, case_ref, case = _observed_case(tmp_path)
    try:
        assessment_service = ExperienceAssessmentService(
            workspace,
            evaluator_actor_id="actor:evaluator",
            excluded_actor_ids=("actor:collector", "actor:author", "actor:corpus"),
            rubric_version="finance-explanation-v1",
            source_qualification_check=lambda _case: None,
        )
        token = request_principal.set(_evaluator())
        try:
            assessment_ref = assessment_service.record_assessment(
                operation_id="assess:corpus-support",
                case_ref=case_ref,
                profile_id=FINANCE_PROFILE,
                verdict="SUPPORT",
                reason_code="INDEPENDENT_RUBRIC_PASS",
                evidence_refs=(case.business_event_digest,),
                independence_cluster_id=case.independence_cluster_id,
            )
        finally:
            request_principal.reset(token)
        controller = _controller(workspace)
        token = request_principal.set(_corpus())
        try:
            corpus_ref = controller.freeze_corpus_v2(
                profile_id=FINANCE_PROFILE,
                assessment_refs=(assessment_ref,),
                assessment_service=assessment_service,
            )
            assert controller.revalidate_corpus_v2(
                corpus_ref, assessment_service=assessment_service,
            )["profile_id"] == FINANCE_PROFILE
        finally:
            request_principal.reset(token)
        corpus = controller.service._load(corpus_ref, "corpus-v2")
        assert corpus["independent_cluster_membership"]["SUPPORT"] == [case.independence_cluster_id]
        assert corpus["cases"][0]["assessment_ref"] == assessment_ref
        assert corpus["cases"][0]["cluster_proof_ref"]
        assert controller.service._family("corpus") == ()
        assert controller.service._family("corpus-v2")[0]["digest"] == corpus["digest"]

        # A later independent disagreement invalidates fresh corpus freezes;
        # the old frozen record remains historical and is not relabelled.
        token = request_principal.set(_evaluator())
        try:
            assessment_service.record_assessment(
                operation_id="assess:corpus-disputed",
                case_ref=case_ref,
                profile_id=FINANCE_PROFILE,
                verdict="DISPUTED",
                reason_code="CONFLICTING_JUDGMENT",
                evidence_refs=(),
            )
        finally:
            request_principal.reset(token)
        token = request_principal.set(_corpus())
        try:
            with pytest.raises(IntegrityError, match="ASSESSMENT_DISPUTED"):
                controller.freeze_corpus_v2(
                    profile_id=FINANCE_PROFILE,
                    assessment_refs=(assessment_ref,),
                    assessment_service=assessment_service,
                )
            with pytest.raises(IntegrityError, match="ASSESSMENT_DISPUTED"):
                controller.revalidate_corpus_v2(
                    corpus_ref, assessment_service=assessment_service,
                )
        finally:
            request_principal.reset(token)
        assert len(controller.service._family("corpus-v2")) == 1
    finally:
        workspace.store.close()
