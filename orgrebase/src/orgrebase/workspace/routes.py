"""HTTP translations for business editing and operational detail views."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from orgrebase.auth import AuthenticationError
from orgrebase.commit_gateway import EffectError
from orgrebase.domain import AuthorizationError, FreshnessError, IntegrityError
from orgrebase.http_errors import public_http_error as public_error
from orgrebase.workspace.approval_authority import (
    CoordinationInput,
    DelegationInput,
    escalate_change,
    grant_delegation,
    revoke_delegation,
)
from orgrebase.workspace.change_operations import change_work_items
from orgrebase.workspace.change_proposals import (
    ChangeProposalInput,
    ReviewObservationInput,
    change_detail,
    change_options,
    record_review,
    require_action,
    submit_change,
)
from orgrebase.workspace.change_recovery import (
    ResumeChangeInput,
    ReturnForEvidenceInput,
    recovery_detail,
    resume_change,
    return_for_evidence,
)
from orgrebase.workspace.effects import (
    EffectActionInput,
    EffectApprovalInput,
    EffectProposalInput,
    approve_effect,
    effect_detail,
    effect_options,
    list_effect_proposals,
    load_effect_config,
    propose_effect,
    reject_effect,
    request_effect_action,
)
from orgrebase.workspace.experience_governance_operations import (
    AssessExperienceInput,
    DecideExperienceLessonInput,
    DecidePreparedExperienceDeltaInput,
    PrepareExperienceDeltaInput,
    ProposeExperienceLessonInput,
    assess_experience,
    candidate_experience_lessons,
    current_experience_lessons,
    decide_experience_lesson,
    decide_prepared_experience_delta,
    experience_lesson_detail,
    prepare_experience_delta,
    propose_experience_lesson,
    read_experience_delta_diff,
    review_experience_lesson,
    review_prepared_experience_delta,
)
from orgrebase.workspace.experience_operations import (
    CollectExperienceInput,
    RevisitExperienceInput,
    collect_experience,
    experience_case_detail,
    list_experience_cases,
    revisit_experience,
)
from orgrebase.workspace.finance_explanation_operations import (
    evaluate_static_finance,
    read_static_finance_evaluation,
)
from orgrebase.workspace.finance_learning_operations import (
    bootstrap_finance_skill,
    finance_skill_head,
)
from orgrebase.workspace.finance_use_projection import read_finance_use_projection
from orgrebase.workspace.onboarding import onboarding_status
from orgrebase.workspace.onboarding_drafts import (
    CreateOnboardingDraft,
    PreflightOnboardingDraft,
    SealOnboardingDraft,
    UpdateOnboardingDraft,
    create_draft,
    export_sealed_draft,
    preflight_draft,
    read_draft,
    read_operation,
    seal_draft,
    update_draft,
)
from orgrebase.workspace.owner_change import (
    OwnerChangeCommand,
    OwnerChangePreviewInput,
    OwnerChangeProposalInput,
    activate_owner_change,
    confirm_owner_change,
    list_owner_changes,
    owner_change_detail,
    owner_change_options,
    preview_owner_change,
    propose_owner_change,
)
from orgrebase.workspace.quote_recovery_operations import (
    QuoteRecoveryNewRun,
    execute_quote_recovery_new_run,
    read_quote_recovery_operation,
)
from orgrebase.workspace.quote_recovery_use import (
    QuoteRecoveryEvaluationCommand,
    evaluate_quote_recovery_candidate,
    read_quote_recovery_evaluation,
)
from orgrebase.workspace.read_dependencies import ReadQuery, capture_read_witness, dependency_payload
from orgrebase.workspace.source_readmission import (
    SourceReadmissionCommand,
    SourceReadmissionInput,
    apply_group,
    approve_group,
    create_group,
    group_attempt_detail,
    group_detail,
    group_options,
    list_groups,
    reject_group,
)


class DeliverableSetDecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    operation_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    preview_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    decision: Literal["APPROVED", "REJECTED"] = "APPROVED"


class StaticFinanceEvaluationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    operation_id: str = Field(
        min_length=1, max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )


def _respond[T](operation: Callable[[], T]) -> T:
    try:
        return operation()
    except AuthenticationError as error:
        raise HTTPException(error.status_code, detail={"code": error.code}) from None
    except AuthorizationError as error:
        raise HTTPException(403, detail=public_error(error, message_as_code=True)) from None
    except KeyError:
        raise HTTPException(404, detail={"code": "WORKSPACE_RECORD_NOT_FOUND"}) from None
    except (IntegrityError, FreshnessError, ValueError, RuntimeError) as error:
        raise HTTPException(409, detail=public_error(error, message_as_code=True)) from None


def change_router(get_workspace: Callable[[], Any]) -> APIRouter:
    router = APIRouter(prefix="/api/workspace")

    @router.post("/experience-assessments")
    def experience_assess(payload: AssessExperienceInput) -> dict[str, Any]:
        return _respond(lambda: assess_experience(get_workspace(), payload))

    @router.post("/experience-lessons/candidates")
    def experience_lesson_propose(payload: ProposeExperienceLessonInput) -> dict[str, Any]:
        return _respond(lambda: propose_experience_lesson(get_workspace(), payload))

    @router.get("/experience-lessons/candidates")
    def experience_lesson_candidates(
        response: Response,
        after: str | None = None, limit: int = Query(default=50, ge=1, le=100),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        return _respond(lambda: candidate_experience_lessons(
            get_workspace(), after=after, limit=limit,
        ))

    @router.get("/experience-lessons/candidates/{candidate_ref}")
    def experience_lesson_review(candidate_ref: str, response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        return _respond(lambda: review_experience_lesson(get_workspace(), candidate_ref))

    @router.post("/experience-lessons/decisions")
    def experience_lesson_decide(payload: DecideExperienceLessonInput) -> dict[str, Any]:
        return _respond(lambda: decide_experience_lesson(get_workspace(), payload))

    @router.post("/experience-lessons/deltas/prepare")
    def experience_delta_prepare(payload: PrepareExperienceDeltaInput) -> dict[str, Any]:
        return _respond(lambda: prepare_experience_delta(get_workspace(), payload))

    @router.get("/experience-lessons/deltas/preparations/{preparation_ref}")
    def experience_delta_review(preparation_ref: str, response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        return _respond(lambda: review_prepared_experience_delta(get_workspace(), preparation_ref))

    @router.post("/experience-lessons/deltas/decisions")
    def experience_delta_decide(payload: DecidePreparedExperienceDeltaInput) -> dict[str, Any]:
        return _respond(lambda: decide_prepared_experience_delta(get_workspace(), payload))

    @router.get("/experience-lessons/deltas/{delta_ref}")
    def experience_delta_read(delta_ref: str, response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        return _respond(lambda: read_experience_delta_diff(get_workspace(), delta_ref))

    @router.get("/experience-lessons")
    def experience_lesson_page(
        response: Response,
        after: str | None = None, limit: int = Query(default=50, ge=1, le=100),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        return _respond(lambda: current_experience_lessons(
            get_workspace(), after=after, limit=limit,
        ))

    @router.get("/experience-lessons/heads/{lesson_id}")
    def experience_lesson_head_detail(lesson_id: str, response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        return _respond(lambda: experience_lesson_detail(get_workspace(), lesson_id))

    @router.post("/finance-explanation/evaluations/{event_id}")
    def static_finance_evaluation(
        event_id: str, payload: StaticFinanceEvaluationInput,
    ) -> dict[str, Any]:
        return _respond(lambda: evaluate_static_finance(
            get_workspace(), event_id=event_id, operation_id=payload.operation_id,
        ))

    @router.get("/finance-explanation/evaluations/{event_id}")
    def static_finance_evaluation_read(event_id: str) -> dict[str, Any]:
        return _respond(lambda: read_static_finance_evaluation(get_workspace(), event_id))

    @router.post("/experience-collector/collect")
    def experience_collect(payload: CollectExperienceInput) -> dict[str, Any]:
        return _respond(lambda: collect_experience(get_workspace(), payload))

    @router.get("/experience-cases")
    def experience_cases(
        response: Response,
        after: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        return _respond(lambda: list_experience_cases(get_workspace(), after=after, limit=limit))

    @router.get("/experience-cases/{case_ref}")
    def experience_case_read(case_ref: str, response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        return _respond(lambda: experience_case_detail(get_workspace(), case_ref))

    @router.post("/experience-collector/revisit")
    def experience_revisit(payload: RevisitExperienceInput) -> dict[str, Any]:
        return _respond(lambda: revisit_experience(get_workspace(), payload))

    @router.get("/skills/finance-change-explanation/head")
    def finance_skill_head_read() -> dict[str, Any]:
        return _respond(lambda: finance_skill_head(get_workspace()))

    @router.post("/skills/finance-change-explanation/bootstrap")
    def finance_skill_bootstrap() -> dict[str, Any]:
        return _respond(lambda: bootstrap_finance_skill(get_workspace()))

    @router.post("/read-witnesses")
    def read_witness(payload: ReadQuery) -> dict[str, Any]:
        def capture():
            workspace = get_workspace()
            with workspace._command_lock, workspace.store.transaction() as connection:
                require_action(workspace, "propose")
                witness = capture_read_witness(workspace, payload, connection=connection)
                return {"witness": witness.model_dump(mode="json"), "read_dependencies": dependency_payload(witness)}
        return _respond(capture)

    def configured_target():
        path = getattr(get_workspace(), "effect_config_path", None)
        if path is None:
            raise EffectError("EFFECT_CONFIGURATION_UNAVAILABLE")
        return load_effect_config(path)

    @router.get("/source-readmission-options")
    def source_group_options() -> dict[str, Any]:
        return _respond(lambda: group_options(get_workspace()))

    @router.get("/source-readmission-groups")
    def source_groups(after: str | None = None, limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
        return _respond(lambda: list_groups(get_workspace(), after=after, limit=limit))

    @router.post("/source-readmission-groups")
    def source_group_create(payload: SourceReadmissionInput) -> dict[str, Any]:
        return _respond(lambda: create_group(get_workspace(), payload))

    @router.get("/source-readmission-groups/{group_id}")
    def source_group_read(group_id: str) -> dict[str, Any]:
        return _respond(lambda: group_detail(get_workspace(), group_id))

    @router.get("/source-readmission-groups/{group_id}/attempt")
    def source_group_attempt_read(group_id: str) -> dict[str, Any]:
        return _respond(lambda: group_attempt_detail(get_workspace(), group_id))

    @router.post("/source-readmission-groups/{group_id}/approve")
    def source_group_approve(group_id: str, payload: SourceReadmissionCommand) -> dict[str, Any]:
        return _respond(lambda: approve_group(get_workspace(), group_id, payload))

    @router.post("/source-readmission-groups/{group_id}/reject")
    def source_group_reject(group_id: str, payload: SourceReadmissionCommand) -> dict[str, Any]:
        return _respond(lambda: reject_group(get_workspace(), group_id, payload))

    @router.post("/source-readmission-groups/{group_id}/apply")
    def source_group_apply(group_id: str, payload: SourceReadmissionCommand) -> dict[str, Any]:
        return _respond(lambda: apply_group(get_workspace(), group_id, payload))

    @router.get("/change-options")
    def options() -> dict[str, Any]:
        return _respond(lambda: change_options(get_workspace()))

    @router.get("/onboarding-status")
    def onboarding() -> dict[str, Any]:
        return _respond(lambda: onboarding_status(get_workspace()))

    @router.post("/onboarding-drafts")
    def onboarding_draft_create(payload: CreateOnboardingDraft) -> dict[str, Any]:
        return _respond(lambda: create_draft(get_workspace(), payload))

    @router.post("/onboarding-drafts/update")
    def onboarding_draft_update(payload: UpdateOnboardingDraft) -> dict[str, Any]:
        return _respond(lambda: update_draft(get_workspace(), payload))

    @router.post("/onboarding-drafts/preflight")
    def onboarding_draft_preflight(payload: PreflightOnboardingDraft) -> dict[str, Any]:
        return _respond(lambda: preflight_draft(get_workspace(), payload))

    @router.post("/onboarding-drafts/seal")
    def onboarding_draft_seal(payload: SealOnboardingDraft) -> dict[str, Any]:
        return _respond(lambda: seal_draft(get_workspace(), payload))

    @router.get("/onboarding-draft-operations/{operation}/{operation_key}")
    def onboarding_operation_read(
        operation: Literal["CREATE", "UPDATE", "PREFLIGHT", "SEAL"],
        operation_key: str,
    ) -> dict[str, Any]:
        return _respond(
            lambda: read_operation(
                get_workspace(),
                operation=operation,
                operation_key=operation_key,
            )
        )

    @router.get("/onboarding-drafts/{draft_id}/revisions/{revision}")
    def onboarding_draft_read(
        draft_id: str,
        revision: int,
        receipt_digest: str = Query(pattern=r"^sha256:[0-9a-f]{64}$"),
    ) -> dict[str, Any]:
        return _respond(
            lambda: read_draft(
                get_workspace(),
                draft_id=draft_id,
                revision=revision,
                receipt_digest=receipt_digest,
            )
        )

    @router.get("/onboarding-drafts/{draft_id}/revisions/{revision}/export")
    def onboarding_draft_export(
        draft_id: str,
        revision: int,
        receipt_digest: str = Query(pattern=r"^sha256:[0-9a-f]{64}$"),
    ) -> Response:
        archive = _respond(lambda: export_sealed_draft(
            get_workspace(), draft_id=draft_id, revision=revision,
            receipt_digest=receipt_digest,
        ))
        return Response(archive, media_type="application/zip", headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'attachment; filename="enterprise-sealed-pack.zip"',
            "X-Content-Type-Options": "nosniff",
        })

    @router.get("/change-work-items")
    def work_items(
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> dict[str, Any]:
        return _respond(lambda: change_work_items(get_workspace(), after=after, limit=limit))

    @router.get("/deliverable-set")
    def deliverable_set_read() -> dict[str, Any]:
        return _respond(lambda: get_workspace().deliverable_set_view())

    @router.get("/deliverable-set/changes/{event_id}")
    def deliverable_set_change_read(event_id: str) -> dict[str, Any]:
        return _respond(lambda: get_workspace().deliverable_set_change_view(event_id))

    @router.post("/approve/{event_id}/deliverable-set")
    def deliverable_set_decide(
        event_id: str,
        payload: DeliverableSetDecisionInput,
    ) -> dict[str, Any]:
        return _respond(
            lambda: get_workspace().approve_deliverable_set_change(
                event_id,
                owner_id=None,
                actor_id=None,
                preview_digest=payload.preview_digest,
                decision=payload.decision,
                operation_id=payload.operation_id,
            )
        )

    @router.get("/export/deliverable-set")
    def deliverable_set_export(response: Response) -> dict[str, Any]:
        response.headers["Content-Disposition"] = (
            'attachment; filename="orgrebase-deliverable-set.json"'
        )
        return _respond(lambda: get_workspace().export_deliverable_set())

    @router.post("/governed-learning/quote-recovery-runs")
    def quote_recovery_new_run(payload: QuoteRecoveryNewRun) -> dict[str, Any]:
        return _respond(
            lambda: execute_quote_recovery_new_run(get_workspace(), payload)
        )

    @router.get("/governed-learning/quote-recovery-operations/{operation_key}")
    def quote_recovery_operation_read(operation_key: str) -> dict[str, Any]:
        return _respond(
            lambda: read_quote_recovery_operation(get_workspace(), operation_key)
        )

    @router.post("/governed-learning/quote-recovery-evaluations")
    def quote_recovery_evaluation(
        payload: QuoteRecoveryEvaluationCommand, response: Response,
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        return _respond(
            lambda: evaluate_quote_recovery_candidate(get_workspace(), payload)
        )

    @router.get("/governed-learning/quote-recovery-evaluations/{selection_ref}")
    def quote_recovery_evaluation_read(selection_ref: str, response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        return _respond(
            lambda: read_quote_recovery_evaluation(get_workspace(), selection_ref)
        )

    @router.post("/change-proposals")
    def propose(payload: ChangeProposalInput) -> dict[str, Any]:
        return _respond(lambda: submit_change(get_workspace(), payload))

    @router.get("/organization/owner-change-options")
    def owner_options() -> dict[str, Any]:
        return _respond(lambda: owner_change_options(get_workspace()))

    @router.get("/organization/owner-changes")
    def owner_list(after: str | None = None, limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
        return _respond(lambda: list_owner_changes(get_workspace(), after=after, limit=limit))

    @router.post("/organization/owner-changes")
    def owner_proposal(payload: OwnerChangeProposalInput) -> dict[str, Any]:
        return _respond(lambda: propose_owner_change(get_workspace(), payload))

    @router.get("/organization/owner-changes/{proposal_id}")
    def owner_detail(proposal_id: str) -> dict[str, Any]:
        return _respond(lambda: owner_change_detail(get_workspace(), proposal_id))

    @router.post("/organization/owner-changes/{proposal_id}/confirm")
    def owner_confirm(proposal_id: str, payload: OwnerChangeCommand) -> dict[str, Any]:
        return _respond(lambda: confirm_owner_change(get_workspace(), proposal_id, payload))

    @router.post("/organization/owner-changes/{proposal_id}/activate")
    def owner_activate(proposal_id: str, payload: OwnerChangeCommand) -> dict[str, Any]:
        return _respond(lambda: activate_owner_change(get_workspace(), proposal_id, payload))

    @router.post("/organization/owner-change-preview")
    def owner_change_preview(payload: OwnerChangePreviewInput) -> dict[str, Any]:
        return _respond(lambda: preview_owner_change(get_workspace(), payload).model_dump(mode="json"))

    @router.get("/changes/{event_id}")
    def detail(event_id: str) -> dict[str, Any]:
        return _respond(lambda: change_detail(get_workspace(), event_id))

    @router.get("/changes/{event_id}/finance-use")
    def finance_use(event_id: str, attempt_key: str | None = Query(default=None, max_length=160)) -> dict[str, Any]:
        return _respond(lambda: read_finance_use_projection(
            get_workspace(), event_id, attempt_key=attempt_key,
        ))

    @router.post("/changes/{event_id}/review-observation")
    def observe(event_id: str, payload: ReviewObservationInput) -> dict[str, Any]:
        return _respond(lambda: record_review(get_workspace(), event_id, payload))

    @router.get("/changes/{event_id}/recovery")
    def recovery(event_id: str) -> dict[str, Any]:
        return _respond(lambda: recovery_detail(get_workspace(), event_id))

    @router.post("/changes/{event_id}/return-for-evidence")
    def return_evidence(event_id: str, payload: ReturnForEvidenceInput) -> dict[str, Any]:
        return _respond(lambda: return_for_evidence(get_workspace(), event_id, payload))

    @router.post("/changes/{event_id}/resume")
    def resume(event_id: str, payload: ResumeChangeInput) -> dict[str, Any]:
        return _respond(lambda: resume_change(get_workspace(), event_id, payload))

    @router.post("/changes/{event_id}/delegate")
    def delegate(event_id: str, payload: DelegationInput) -> dict[str, Any]:
        return _respond(lambda: grant_delegation(get_workspace(), event_id, payload))

    @router.post("/changes/{event_id}/revoke-delegation")
    def revoke(event_id: str, payload: CoordinationInput) -> dict[str, Any]:
        return _respond(lambda: revoke_delegation(get_workspace(), event_id, payload))

    @router.post("/changes/{event_id}/escalate")
    def escalate(event_id: str, payload: CoordinationInput) -> dict[str, Any]:
        return _respond(lambda: escalate_change(get_workspace(), event_id, payload))

    @router.get("/effect-options")
    def target_options(after: str | None = None, limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
        return _respond(lambda: effect_options(get_workspace(), configured_target(), after=after, limit=limit))

    @router.get("/effect-proposals")
    def effects(after: str | None = None, limit: int = Query(default=30, ge=1, le=100)) -> dict[str, Any]:
        return _respond(lambda: list_effect_proposals(get_workspace(), configured_target(), after=after, limit=limit))

    @router.post("/effect-proposals")
    def effect_propose(payload: EffectProposalInput) -> dict[str, Any]:
        return _respond(lambda: propose_effect(get_workspace(), configured_target(), payload))

    @router.get("/effect-proposals/{proposal_id}")
    def effect_read(proposal_id: str) -> dict[str, Any]:
        return _respond(lambda: effect_detail(get_workspace(), configured_target(), proposal_id))

    @router.post("/effect-proposals/{proposal_id}/approve")
    def effect_approve(proposal_id: str, payload: EffectApprovalInput) -> dict[str, Any]:
        return _respond(lambda: approve_effect(get_workspace(), configured_target(), proposal_id, payload))

    @router.post("/effect-proposals/{proposal_id}/reject")
    def effect_reject(proposal_id: str, payload: EffectApprovalInput) -> dict[str, Any]:
        return _respond(lambda: reject_effect(get_workspace(), configured_target(), proposal_id, payload))

    @router.post("/effect-proposals/{proposal_id}/actions")
    def effect_action(proposal_id: str, payload: EffectActionInput) -> dict[str, Any]:
        return _respond(lambda: request_effect_action(get_workspace(), configured_target(), proposal_id, payload))

    return router
