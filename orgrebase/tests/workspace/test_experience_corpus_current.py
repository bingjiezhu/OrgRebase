"""A frozen V2 corpus remains bound to current controlled source evidence."""

from __future__ import annotations

from contextlib import closing
from functools import partial

import pytest

from orgrebase.domain import IntegrityError
from orgrebase.workspace.change_recovery import resume_change, return_for_evidence
from orgrebase.workspace.experience_assessment import (
    ExperienceAssessmentService,
    require_quote_case_current,
)
from orgrebase.workspace.experience_collection import (
    COLLECTION_MEDIA,
    OBSERVATION_MEDIA,
    ExperienceCollector,
)
from orgrebase.workspace.experience_contracts import CaseObservationV2
from orgrebase.workspace.pattern_evolution import GovernedPatternService
from orgrebase.workspace.pattern_governance import PatternAuthorityScope, PrincipalPatternGovernance
from orgrebase.workspace.quote_recovery_learning import TARGET_SKILL, quote_recovery_content_bundle
from orgrebase.workspace.service import WorkspaceService
from tests.workspace.test_change_recovery import resume_command, return_command
from tests.workspace.test_experience_source_qualification import (
    SLOT,
    _actor,
    _sync_qualified_source,
)
from tests.workspace.test_pattern_v2_corpus import FINANCE_PROFILE

pytest_plugins = ("tests.workspace.test_experience_source_qualification",)


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
            tenant_id=workspace.store.tenant_id,
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


def test_source_qualified_corpus_rechecks_after_workspace_restart_and_revision(
    source_workspace, tmp_path,
):
    workspace, principal, config, mapping = source_workspace
    event, synchronizer, reader, row = _sync_qualified_source(
        workspace, principal, config, mapping, tmp_path,
    )
    with principal("operator"):
        workspace.preview_change(event.event_id)
    with principal(SLOT):
        return_for_evidence(workspace, event.event_id, return_command(workspace, event.event_id))
        ready = resume_change(workspace, event.event_id, resume_command(workspace, event.event_id))
        preview = workspace._preview_record(event.event_id)
        approval = workspace.approve_change(
            event.event_id, actor_id=event.owner_id,
            preview_digest=preview["preview_digest"],
            recovery_digest=ready["recovery_digest"],
        )
        workspace.apply_approved_change(event.event_id, approval_digest=approval["approval_digest"])

    with _actor(workspace, "collector", "reader"):
        collector = ExperienceCollector(workspace, collector_actor_id="actor:collector", enabled=True)
        for _ in range(20):
            if collector.collect(worker_id="corpus-case-reader", limit=500).status == "IDLE":
                break
        else:
            pytest.fail("bounded collector did not reach the event head")
    observed = []
    for record in workspace.store.list_artifacts(
        artifact_id_prefix="experience-collection:", expected_media_type=COLLECTION_MEDIA,
    ):
        case_ref = record.payload.get("case_ref")
        if case_ref is None:
            continue
        case = CaseObservationV2.model_validate(
            workspace.store.load_artifact(case_ref, OBSERVATION_MEDIA).payload,
        )
        if case.business_event_id == event.event_id and case.resume_digest is not None:
            observed.append((case_ref, case))
    applied = [(ref, case) for ref, case in observed if case.execution_outcome == "APPLIED"]
    assert applied
    # The collector also records intermediate observations for this event.
    # Corpus qualification must assess the latest terminal revision, rather
    # than whichever artifact sorts first by opaque ID.
    case_ref, case = max(applied, key=lambda item: workspace.store.event_by_digest(
        item[1].origin_event_refs[-1],
    )["sequence_no"])
    assert case.cluster_status == "PROVISIONAL" and case.independence_cluster_id

    assessment = ExperienceAssessmentService(
        workspace, evaluator_actor_id="actor:evaluator",
        excluded_actor_ids=("actor:collector", "actor:author", "actor:corpus"),
        rubric_version="finance-explanation-controlled-v1",
        source_qualification_check=partial(require_quote_case_current, workspace),
    )
    with _actor(workspace, "evaluator", "governor"):
        assessment_ref = assessment.record_assessment(
            operation_id="assess:corpus-current", case_ref=case_ref,
            profile_id=FINANCE_PROFILE, verdict="SUPPORT",
            reason_code="INDEPENDENT_SOURCE_RUBRIC_PASS",
            evidence_refs=(case.business_event_digest,),
            independence_cluster_id=case.independence_cluster_id,
        )
    controller = _controller(workspace)
    with _actor(workspace, "corpus", "governor"):
        corpus_ref = controller.freeze_corpus_v2(
            profile_id=FINANCE_PROFILE, assessment_refs=(assessment_ref,),
            assessment_service=assessment,
        )
        frozen = controller.revalidate_corpus_v2(
            corpus_ref, assessment_service=assessment,
        )
        assert frozen["independent_cluster_membership"]["SUPPORT"] == [
            case.independence_cluster_id,
        ]
        assert frozen["independent_cluster_membership"]["COUNTEREXAMPLE"] == []
        assert "Renewed source service plan" not in str(frozen)

    with closing(WorkspaceService.reopen(
        workspace.store.path,
        runtime_configuration=workspace.runtime_configuration,
        store_tenant_id=workspace.store.tenant_id,
        store_migrate=False,
    )) as restarted:
        restarted.identity_issuer = workspace.identity_issuer
        restarted.verify_membership = workspace.verify_membership
        restarted.authorize_workspace_subject = workspace.authorize_workspace_subject
        restarted.source_config_path = workspace.source_config_path
        reopened_assessment = ExperienceAssessmentService(
            restarted, evaluator_actor_id="actor:evaluator",
            excluded_actor_ids=("actor:collector", "actor:author", "actor:corpus"),
            rubric_version="finance-explanation-controlled-v1",
            source_qualification_check=partial(require_quote_case_current, restarted),
        )
        with _actor(restarted, "corpus", "governor"):
            assert _controller(restarted).revalidate_corpus_v2(
                corpus_ref, assessment_service=reopened_assessment,
            )["independent_cluster_membership"] == frozen["independent_cluster_membership"]

    # The source value is unchanged, but its opaque revision is new. The old
    # case/corpus must stop qualifying without rewriting its historical bytes.
    replacement = {**row, "@odata.etag": 'W/"source-b"'}
    reader.fetch = lambda _cursor: reader.parse_page({
        "@odata.deltaLink": reader.settings.endpoint + "?$deltatoken=source-b",
        "value": [replacement],
    })
    reader.read_record = lambda _identity: reader._live_record(replacement)
    with principal("operator"):
        assert synchronizer.sync_page()["records_admitted"] == 1
    with _actor(workspace, "corpus", "governor"), pytest.raises(IntegrityError):
        controller.revalidate_corpus_v2(corpus_ref, assessment_service=assessment)
    assert controller.service._load(corpus_ref, "corpus-v2")["cases"][0]["assessment_ref"] == assessment_ref
