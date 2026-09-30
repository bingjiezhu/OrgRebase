"""One durable, explicit Finance V4 static-baseline evaluation attempt.

The immutable intent is written before model I/O. A missing result after an
intent is an unknown dispatch, so another operation ID cannot silently issue
the same business case again. This first slice supports one static arm only;
it does not qualify or adopt a learned skill.
"""

from __future__ import annotations

import re
from typing import Any

from orgrebase.auth import AuthenticationError, authorize, current_authorization, request_principal
from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.domain import AuthorizationError, EvidenceClass, FreshnessError, IntegrityError, RunEnvelope
from orgrebase.private_records import PrivateRecordStore
from orgrebase.workspace.advisory import (
    AdvisoryGenerationError,
    DomainAdvisoryCandidate,
    WorkspaceApplyAdvisoryVerifier,
    WorkspaceChangeAdvisoryAdapter,
    finance_case_revision,
    finance_evaluation_advice_context,
)
from orgrebase.workspace.experience_contracts import (
    LessonSnapshotEntry,
    MemorySnapshot,
    RecallSelectionManifest,
    empty_memory_snapshot,
    empty_recall_selection_manifest,
    exact_bytes_digest,
)
from orgrebase.workspace.finance_experiment import TRIAL_MEDIA, FinanceExperimentService
from orgrebase.workspace.model_provider import VERTEX_CANDIDATE_MODEL_ID, VertexAIStructuredProvider
from orgrebase.workspace.models import ModelRequestV4, WorkspacePreviewBundle
from orgrebase.workspace.read_dependencies import validate_change_proposal_sources
from orgrebase.workspace.runtime_revision import require_preview_runtime
from orgrebase.workspace.skill_evolution_v2 import (
    FinanceSkillHeadService,
    FinanceSkillResolution,
    SkillContentBundleV2,
)
from orgrebase.workspace.vertex_candidate import FINANCE_V4_COMPILER_VERSION, build_vertex_advice_body

PROFILE_ID = "workspace-change-explanation-v1"
INTENT_MEDIA = "application/vnd.orgrebase.finance-evaluation-intent.v1+json"
RESULT_MEDIA = "application/vnd.orgrebase.finance-evaluation-result.v1+json"
SNAPSHOT_MEDIA = "application/vnd.orgrebase.memory-snapshot.v1+json"
MANIFEST_MEDIA = "application/vnd.orgrebase.recall-selection-manifest.v1+json"
STATIC_ARM = "HUMAN_REVIEWED_STATIC_NO_MEMORY"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")


def finance_evaluation_refs(workspace: Any, event_id: str) -> dict[str, str]:
    """One static-arm business guard, independent of caller operation/head IDs."""

    if not isinstance(event_id, str) or _IDENTIFIER.fullmatch(event_id) is None:
        raise ValueError("FINANCE_EVALUATION_EVENT_ID_INVALID")
    guard = sha256_digest({
        "tenant_id": workspace.profile.organization_id,
        "workspace_id": workspace.store.workspace_id,
        "profile_id": PROFILE_ID,
        "event_id": event_id,
    }).removeprefix("sha256:")
    return {
        "guard": guard,
        "intent_ref": f"finance-evaluation-intent:{guard}",
        "result_ref": f"finance-evaluation-result:{guard}",
        "private_ref": f"finance-evaluation-private:{guard}",
    }


def _authorized_actor(workspace: Any) -> str:
    principal = request_principal.get()
    if principal is None:
        raise AuthenticationError("FINANCE_EVALUATION_PRINCIPAL_REQUIRED")
    authorize(principal, "propose", workspace.profile.organization_id)
    if principal.tenant_id != workspace.profile.organization_id:
        raise IntegrityError("FINANCE_EVALUATION_TENANT_MISMATCH")
    check = current_authorization()
    if check is None:
        raise AuthenticationError("FINANCE_EVALUATION_CURRENT_AUTHORIZATION_REQUIRED")
    check()
    return principal.actor_id


def _current_static_head(workspace: Any, actor_id: str) -> FinanceSkillResolution:
    head_service = FinanceSkillHeadService(
        workspace.store, tenant_id=workspace.profile.organization_id
    )
    resolution = head_service.resolve()
    source = workspace.store.get_object(head_service.head_id)
    if (
        resolution.generation != 0
        or resolution.qualification_status != "UNQUALIFIED"
        or resolution.adoption_enabled is not False
        or source.payload["transition_kind"] != "GENESIS"
        or source.payload["actor_id"] == actor_id
        or resolution.bundle.digest != type(resolution.bundle).static_baseline(head_service.policy).digest
    ):
        raise IntegrityError("FINANCE_EVALUATION_STATIC_HEAD_REQUIRED")
    return resolution


def _current_case(workspace: Any, event_id: str) -> tuple[Any, WorkspacePreviewBundle]:
    workspace.changes.refresh()
    if workspace._change_status(event_id) != "PREVIEWED":
        raise IntegrityError("FINANCE_EVALUATION_CURRENT_PREVIEW_REQUIRED")
    record = workspace._preview_record(event_id)
    if record is None:
        raise IntegrityError("FINANCE_EVALUATION_CURRENT_PREVIEW_REQUIRED")
    bundle = WorkspacePreviewBundle.model_validate(record["bundle"])
    require_preview_runtime(workspace, event_id, bundle.preview.digest)
    if bundle.change_spec.digest != workspace.change_spec(event_id).digest:
        raise IntegrityError("FINANCE_EVALUATION_CHANGE_SPEC_STALE")
    fixture = workspace._fixture_for_change(bundle.change_spec)
    change_set = workspace.change_builder.build(fixture=fixture, spec=bundle.change_spec)
    from orgrebase.impact import ImpactEngine

    preview = ImpactEngine(fixture).preview(change_set)
    if change_set.digest != bundle.change_set.digest or preview.digest != bundle.preview.digest:
        raise IntegrityError("FINANCE_EVALUATION_BUSINESS_INPUT_STALE")
    event = workspace.changes.get(event_id)
    validate_change_proposal_sources(workspace, (event.proposal,), workspace.clock.now())
    if not any(item.domain == "finance" for item in fixture.objects if item.id in change_set.scope):
        raise IntegrityError("FINANCE_EVALUATION_FINANCE_DOMAIN_REQUIRED")
    return fixture, bundle


def _optional_artifact(workspace: Any, ref: str, media: str) -> dict[str, Any] | None:
    try:
        return workspace.store.load_artifact(ref, media).payload
    except KeyError:
        return None


def _current_static_evaluation_inputs(
    workspace: Any, *, event_id: str, actor_id: str, intent: dict[str, Any],
    private_status: str,
) -> tuple[str, str | None]:
    """Qualify today's inputs separately from the immutable historical result."""

    if private_status != "AVAILABLE":
        return "HOLD", "FINANCE_EVALUATION_PRIVATE_RESULT_UNAVAILABLE"
    try:
        head = _current_static_head(workspace, actor_id)
        _, bundle = _current_case(workspace, event_id)
        snapshot = MemorySnapshot.model_validate(
            workspace.store.load_artifact(intent["snapshot_ref"], SNAPSHOT_MEDIA).payload,
        ).revalidated()
        manifest = RecallSelectionManifest.model_validate(
            workspace.store.load_artifact(intent["manifest_ref"], MANIFEST_MEDIA).payload,
        ).revalidated()
        if (
            head.head_ref != intent["head_ref"]
            or head.head_digest != intent["head_digest"]
            or head.package_digest != intent["package_digest"]
            or bundle.change_set.digest != intent["change_set_digest"]
            or bundle.preview.digest != intent["preview_digest"]
            or finance_case_revision(bundle.change_set, bundle.preview)
            != intent["case_revision_digest"]
            or snapshot.digest != intent["snapshot_digest"]
            or manifest.digest != intent["manifest_digest"]
            or manifest.snapshot_digest != snapshot.digest
            or manifest.coverage != "EMPTY"
            or manifest.selected_lessons
        ):
            raise IntegrityError("FINANCE_EVALUATION_FROZEN_INPUT_STALE")
    except (IntegrityError, FreshnessError, KeyError, PermissionError, RuntimeError, ValueError):
        return "HOLD", "FINANCE_EVALUATION_CURRENT_INPUT_STALE"
    return "CURRENT_INPUTS", None


def read_static_finance_evaluation(workspace: Any, event_id: str) -> dict[str, Any]:
    """Authenticated low-sensitivity readback; never returns model/candidate text."""

    return _read_finance_evaluation(
        workspace, event_id=event_id, refs=finance_evaluation_refs(workspace, event_id),
        expected_arm=STATIC_ARM, expected_scope="STATIC_BASELINE_ONLY",
    )


def _read_finance_evaluation(
    workspace: Any, *, event_id: str, refs: dict[str, str],
    expected_arm: str, expected_scope: str,
) -> dict[str, Any]:

    actor_id = _authorized_actor(workspace)
    intent = _optional_artifact(workspace, refs["intent_ref"], INTENT_MEDIA)
    if intent is None:
        return {"status": "NOT_STARTED", "event_id": event_id, "target_writes": 0}
    if (
        intent.get("schema_version") != "orgrebase.finance-evaluation-intent.v1"
        or intent.get("event_id") != event_id
        or intent.get("execution_mode") != "EVALUATION_ONLY"
        or intent.get("evaluation_arm") != expected_arm
        or intent.get("scope") != expected_scope
        or intent.get("target_writes") != 0
    ):
        raise IntegrityError("FINANCE_EVALUATION_INTENT_INVALID")
    if intent.get("actor_id") != actor_id:
        raise AuthorizationError("FINANCE_EVALUATION_OWNER_REQUIRED")
    intent_digest = sha256_digest(intent)
    result = _optional_artifact(workspace, refs["result_ref"], RESULT_MEDIA)
    if result is None:
        return {
            "status": "RESULT_UNKNOWN",
            "reason_code": "DURABLE_INTENT_WITHOUT_RESULT",
            "event_id": event_id,
            "operation_id": intent["operation_id"],
            "intent_ref": refs["intent_ref"],
            "intent_digest": intent_digest,
            "reserved_microusd": intent["reserved_microusd"],
            "target_writes": 0,
            "scope": expected_scope,
        }
    if (
        result.get("schema_version") != "orgrebase.finance-evaluation-result.v1"
        or result.get("intent_ref") != refs["intent_ref"]
        or result.get("intent_digest") != intent_digest
        or result.get("event_id") != event_id
        or result.get("operation_id") != intent["operation_id"]
        or result.get("execution_mode") != "EVALUATION_ONLY"
        or result.get("evaluation_arm") != expected_arm
        or result.get("scope") != expected_scope
        or result.get("target_writes") != 0
        or result.get("private_record_ref") != refs["private_ref"]
        or result.get("candidate_bundle_digest") != intent["candidate_bundle_digest"]
        or result.get("package_digest") != intent["package_digest"]
        or result.get("budget_contract_digest") != intent["budget_contract_digest"]
    ):
        raise IntegrityError("FINANCE_EVALUATION_RESULT_INVALID")
    private_store = PrivateRecordStore(
        workspace.store, workspace.clock, retention_seconds=workspace.private_retention_seconds
    )
    private_status = private_store.record_status(refs["private_ref"])
    if private_status == "AVAILABLE":
        private_payload = private_store.read_owned(
            refs["private_ref"], owner_id=actor_id, scope_ref=refs["intent_ref"]
        )
        if private_payload is None or sha256_digest(private_payload) != result["private_record_digest"]:
            raise IntegrityError("FINANCE_EVALUATION_PRIVATE_RESULT_MISMATCH")
    current_qualification, current_reason_code = _current_static_evaluation_inputs(
        workspace, event_id=event_id, actor_id=actor_id, intent=intent,
        private_status=private_status,
    )
    return {
        "schema_version": result["schema_version"],
        "status": result["status"],
        "reason_code": result["reason_code"],
        "event_id": event_id,
        "operation_id": result["operation_id"],
        "intent_ref": refs["intent_ref"],
        "intent_digest": intent_digest,
        "result_ref": refs["result_ref"],
        "result_digest": sha256_digest(result),
        "head_ref": intent["head_ref"],
        "head_digest": intent["head_digest"],
        "candidate_bundle_digest": intent["candidate_bundle_digest"],
        "finance_request_digest": result["finance_request_digest"],
        "finance_receipt_digest": result["finance_receipt_digest"],
        "finance_wire_body_digest": result["finance_wire_body_digest"],
        "private_record_ref": refs["private_ref"],
        "private_record_status": private_status,
        "current_qualification": current_qualification,
        "current_reason_code": current_reason_code,
        "reserved_microusd": intent["reserved_microusd"],
        "physical_attempts_observed": result["physical_attempts_observed"],
        "attempt_observation_coverage": result["attempt_observation_coverage"],
        "target_writes": 0,
        "quality_status": "NOT_EVALUATED",
        "scope": expected_scope,
    }


def evaluate_static_finance(
    workspace: Any,
    *,
    event_id: str,
    operation_id: str,
    provider_override: VertexAIStructuredProvider | None = None,
    _experiment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one bounded static Finance explanation evaluation, with no Apply."""

    actor_id = _authorized_actor(workspace)
    if not isinstance(operation_id, str) or _IDENTIFIER.fullmatch(operation_id) is None:
        raise ValueError("FINANCE_EVALUATION_OPERATION_ID_INVALID")
    if _experiment is not None:
        ledger = _experiment.get("ledger")
        if (
            not isinstance(ledger, FinanceExperimentService)
            or ledger.store is not workspace.store
            or ledger.workspace is not workspace
            or ledger.tenant_id != workspace.profile.organization_id
            or ledger.operator_actor_id != actor_id
            or _experiment.get("arm") not in {
                "STATIC_CURRENT", "NO_MEMORY", "HUMAN_REVIEWED", "SPARSE_RECALL"
            }
            or _experiment.get("trial_ref") != ledger._trial_ref(
                _experiment["family_ref"], case_ref=event_id,
                seed=_experiment["seed"], order=_experiment["order"], arm=_experiment["arm"],
            )
            or operation_id != "finance-experiment:" + _experiment["trial_ref"].rsplit(":", 1)[1]
        ):
            raise IntegrityError("FINANCE_EVALUATION_EXPERIMENT_SCOPE_INVALID")
        family = ledger._load_family(_experiment["family_ref"])
        if (
            family["execution_scope"] != "CONTROLLED_FIXTURE"
            or family["family_id"] != _experiment["family_id"]
            or event_id not in family["cases"]
        ):
            raise IntegrityError("FINANCE_EVALUATION_EXPERIMENT_SCOPE_INVALID")
        guard = _experiment["trial_ref"].rsplit(":", 1)[1]
        expected_refs = {
            "guard": guard,
            "intent_ref": f"finance-evaluation-intent:{guard}",
            "result_ref": f"finance-evaluation-result:{guard}",
            "private_ref": f"finance-evaluation-private:{guard}",
        }
        if _experiment.get("refs") != expected_refs:
            raise IntegrityError("FINANCE_EVALUATION_EXPERIMENT_REFS_INVALID")
    refs = (
        _experiment["refs"] if _experiment is not None
        else finance_evaluation_refs(workspace, event_id)
    )
    arm = _experiment["arm"] if _experiment is not None else STATIC_ARM
    scope = "CONTROLLED_FAMILY_ARM" if _experiment is not None else "STATIC_BASELINE_ONLY"
    def readback() -> dict[str, Any]:
        return _read_finance_evaluation(
            workspace, event_id=event_id, refs=refs,
            expected_arm=arm, expected_scope=scope,
        )
    existing = _optional_artifact(workspace, refs["intent_ref"], INTENT_MEDIA)
    if existing is not None:
        if existing["actor_id"] != actor_id:
            raise AuthorizationError("FINANCE_EVALUATION_OWNER_REQUIRED")
        if existing["operation_id"] != operation_id:
            raise IntegrityError("FINANCE_EVALUATION_CASE_ALREADY_BOUND")
        if _experiment is not None:
            prior_trial = ledger.store.load_artifact(_experiment["trial_ref"], TRIAL_MEDIA).payload
            if (
                existing.get("trial_ref") != _experiment["trial_ref"]
                or existing.get("scope") != "CONTROLLED_FAMILY_ARM"
                or existing.get("evaluation_arm") != _experiment["arm"]
                or existing.get("candidate_bundle_digest") != prior_trial["candidate_bundle_digest"]
                or existing.get("snapshot_digest") != prior_trial["snapshot_digest"]
                or existing.get("manifest_digest") != prior_trial["manifest_digest"]
            ):
                raise IntegrityError("FINANCE_EVALUATION_TRIAL_BINDING_INVALID")
        return readback()
    if workspace.private_retention_seconds <= 0:
        raise IntegrityError("FINANCE_EVALUATION_PRIVATE_RETENTION_REQUIRED")
    provider = provider_override or workspace.advisory_factory.provider
    if not isinstance(provider, VertexAIStructuredProvider):
        raise IntegrityError("FINANCE_EVALUATION_VERTEX_PROVIDER_REQUIRED")
    if provider.model_id != VERTEX_CANDIDATE_MODEL_ID or provider.model_budget is None:
        raise IntegrityError("FINANCE_EVALUATION_PRICE_OR_MODEL_REQUIRED")
    provider.require_available()
    actor_id = _authorized_actor(workspace)
    resolution = _current_static_head(workspace, actor_id)
    fixture, bundle = _current_case(workspace, event_id)
    snapshot = (
        MemorySnapshot.model_validate(_experiment["snapshot"]).revalidated()
        if _experiment is not None and _experiment.get("snapshot") is not None
        else empty_memory_snapshot(
            tenant_id=workspace.profile.organization_id,
            workspace_id=workspace.store.workspace_id,
            profile_id=PROFILE_ID,
            retrieval_version="finance-static-no-retrieval.v1",
            compiler_version=FINANCE_V4_COMPILER_VERSION,
            budget_version="finance-static-budget.v1",
        )
    )
    candidate_bundle = (
        SkillContentBundleV2.from_payload(_experiment["candidate_bundle"].payload)
        if _experiment is not None and _experiment.get("candidate_bundle") is not None
        else resolution.bundle
    )
    advice_text = _experiment.get("advice_text", "") if _experiment is not None else ""
    selected_lessons = (
        tuple(LessonSnapshotEntry.model_validate(item) for item in _experiment.get("selected_lessons", ()))
        if _experiment is not None else ()
    )
    if (
        snapshot.tenant_id != workspace.profile.organization_id
        or snapshot.workspace_id != workspace.store.workspace_id
        or snapshot.profile_id != PROFILE_ID
        or snapshot.compiler_version != FINANCE_V4_COMPILER_VERSION
        or len(candidate_bundle.instruction_bytes) + len(candidate_bundle.reference_bytes) > 4096
        or len(advice_text.encode("utf-8")) > 4096
        or (_experiment is None and (selected_lessons or advice_text))
        or (_experiment is not None and arm != "SPARSE_RECALL" and (selected_lessons or advice_text))
        or (_experiment is not None and arm == "SPARSE_RECALL" and not selected_lessons)
        or any(item not in snapshot.lesson_entries for item in selected_lessons)
    ):
        raise IntegrityError("FINANCE_EVALUATION_RESOURCE_BUDGET_OR_SCOPE_INVALID")
    case_revision = finance_case_revision(bundle.change_set, bundle.preview)
    query_payload = {
        "schema_version": "orgrebase.finance-static-query.v1" if _experiment is None
        else "orgrebase.finance-controlled-query.v1",
        "case_id": bundle.change_set.id,
        "case_revision": case_revision,
        "change_set_digest": bundle.change_set.digest,
        "preview_digest": bundle.preview.digest,
        **({"trial_ref": _experiment["trial_ref"], "arm": arm} if _experiment is not None else {}),
    }
    query_digest = sha256_digest(query_payload)
    query_ref = f"finance-evaluation-query:{refs['guard']}"
    query_scope = f"experience-recall:{bundle.change_set.id}:{case_revision}"
    current_time = workspace.clock.now()
    envelope = RunEnvelope(
        run_id=f"finance-evaluation:{refs['guard']}",
        nonce=sha256_digest({"operation_id": operation_id, "guard": refs["guard"]})[7:39],
        issued_at=current_time,
        expires_at=bundle.run_envelope.expires_at,
        mode="EVALUATION_ONLY",
        evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
    )
    static_plan, _ = WorkspaceChangeAdvisoryAdapter().compile(
        fixture=fixture, change_set=bundle.change_set, preview=bundle.preview
    )
    finance_task = next(
        (task for task in static_plan.tasks if task.authority_domain == "finance"), None
    )
    if finance_task is None:
        raise IntegrityError("FINANCE_EVALUATION_FINANCE_TASK_REQUIRED")
    context_digest = sha256_digest({
        "change_set_digest": bundle.change_set.digest,
        "preview_digest": bundle.preview.digest,
        "run_envelope_digest": envelope.digest,
        "operation_id": operation_id,
    })
    if selected_lessons:
        memory_wire = canonical_json({
            "lessons": [{
                "ref": item.lesson_ref, "revision": item.lesson_revision,
                "content_digest": item.content_digest,
            } for item in selected_lessons],
            "advice_text": advice_text,
        }).encode("utf-8")
        if len(memory_wire) > 4096:
            raise IntegrityError("FINANCE_EVALUATION_MEMORY_BUDGET_EXCEEDED")
        manifest_draft = RecallSelectionManifest(
            tenant_id=snapshot.tenant_id, workspace_id=snapshot.workspace_id,
            profile_id=snapshot.profile_id,
            case_id=bundle.change_set.id, case_revision=case_revision,
            evaluation_arm=arm, task_id=finance_task.id,
            attempt_id=operation_id, context_digest=context_digest,
            snapshot_digest=snapshot.digest,
            snapshot_lesson_count=len(snapshot.lesson_entries),
            query_ref=query_ref, query_digest=query_digest,
            selected_lessons=selected_lessons,
            advice_bytes_digest=exact_bytes_digest(advice_text.encode("utf-8")),
            advice_byte_count=len(advice_text.encode("utf-8")),
            coverage=snapshot.coverage,
            candidate_count=len(snapshot.lesson_entries),
            eligible_count=len(selected_lessons),
            retrieval_version=snapshot.retrieval_version,
            index_revision=snapshot.index_revision,
            tokenizer_version=snapshot.tokenizer_version,
            ranker_version=snapshot.ranker_version,
            corpus_stats_digest=sha256_digest({
                "controlled_fixture_snapshot_digest": snapshot.digest,
                "document_count": len(snapshot.lesson_entries),
            }),
            compiler_version=snapshot.compiler_version,
            budget_version=snapshot.budget_version,
            memory_reserved_bytes=4096,
        )
    else:
        manifest_draft = empty_recall_selection_manifest(
            snapshot,
            case_id=bundle.change_set.id,
            case_revision=case_revision,
            evaluation_arm=arm,
            query_ref=query_ref,
            query_digest=query_digest,
            task_id=finance_task.id,
            attempt_id=operation_id,
            context_digest=context_digest,
        )
    manifest = RecallSelectionManifest.model_validate({
        **manifest_draft.model_dump(mode="json", exclude={"digest"}),
        "query_owner_id": actor_id,
    })
    snapshot_ref = f"memory-snapshot:{snapshot.digest[7:]}"
    manifest_ref = f"recall-selection:{manifest.digest[7:]}"
    advice = finance_evaluation_advice_context(
        resolution=resolution, snapshot=snapshot, manifest=manifest,
        change_set=bundle.change_set, preview=bundle.preview, run_envelope=envelope,
        operation_id=operation_id, snapshot_ref=snapshot_ref, manifest_ref=manifest_ref,
        advice_text=advice_text,
        candidate_bundle=candidate_bundle if candidate_bundle.digest != resolution.bundle.digest else None,
    )

    def require_current(context: Any) -> None:
        _authorized_actor(workspace)
        current_head = _current_static_head(workspace, actor_id)
        if (
            current_head.head_ref != context.head_ref
            or current_head.head_digest != context.head_digest
            or current_head.package_digest != resolution.package_digest
            or context.package_digest != candidate_bundle.package_digest
            or context.memory_snapshot_digest != snapshot.digest
            or context.recall_manifest_digest != manifest.digest
            or manifest.task_id != finance_task.id
            or manifest.attempt_id != operation_id
            or manifest.context_digest != context_digest
            or manifest.coverage != snapshot.coverage
            or manifest.selected_lessons != selected_lessons
            or manifest.advice_byte_count != len(advice_text.encode("utf-8"))
            or manifest.advice_bytes_digest != exact_bytes_digest(advice_text.encode("utf-8"))
            or (not selected_lessons and manifest.empty_reason != "NO_MEMORY_BASELINE")
            or (selected_lessons and manifest.empty_reason is not None)
            or manifest.query_ref != query_ref
            or manifest.query_owner_id != actor_id
            or manifest.query_digest != query_digest
            or PrivateRecordStore(
                workspace.store, workspace.clock,
                retention_seconds=workspace.private_retention_seconds,
            ).read_owned(query_ref, owner_id=actor_id, scope_ref=query_scope) != query_payload
            or MemorySnapshot.model_validate(workspace.store.load_artifact(snapshot_ref, SNAPSHOT_MEDIA).payload).digest
            != snapshot.digest
            or RecallSelectionManifest.model_validate(
                workspace.store.load_artifact(manifest_ref, MANIFEST_MEDIA).payload
            ).digest != manifest.digest
        ):
            raise IntegrityError("FINANCE_EVALUATION_FROZEN_INPUT_STALE")
        _, current_bundle = _current_case(workspace, event_id)
        if (
            current_bundle.change_set.digest != bundle.change_set.digest
            or current_bundle.preview.digest != bundle.preview.digest
        ):
            raise IntegrityError("FINANCE_EVALUATION_BUSINESS_INPUT_STALE")

    adapter = WorkspaceChangeAdvisoryAdapter(
        provider=provider, tenant_id=workspace.profile.organization_id,
        workspace_id=workspace.store.workspace_id, model_id=VERTEX_CANDIDATE_MODEL_ID,
        finance_advice=advice, validate_finance_advice=require_current,
    )
    reservation = adapter.cost_reservation(
        fixture=fixture, change_set=bundle.change_set, preview=bundle.preview
    )
    if reservation is None:
        raise IntegrityError("FINANCE_EVALUATION_PRICE_RESERVATION_REQUIRED")
    if _experiment is not None:
        ledger = _experiment["ledger"]
        trial = ledger.reserve_trial(
            family_ref=_experiment["family_ref"], case_ref=event_id,
            seed=_experiment["seed"], order=_experiment["order"], arm=arm,
            bundle_digest=candidate_bundle.digest, snapshot_digest=snapshot.digest,
            manifest_digest=manifest.digest,
            budget_contract_digest=reservation["contract_digest"],
            reserved_calls=reservation["calls"],
            reserved_microusd=reservation["reserved_microusd"],
        )
        if trial["trial_ref"] != _experiment["trial_ref"] or trial["operation_id"] != operation_id:
            raise IntegrityError("FINANCE_EVALUATION_TRIAL_IDENTITY_MISMATCH")
    intent = {
        "schema_version": "orgrebase.finance-evaluation-intent.v1",
        "scope": scope,
        "tenant_id": workspace.profile.organization_id,
        "workspace_id": workspace.store.workspace_id,
        "profile_id": PROFILE_ID,
        "event_id": event_id,
        "operation_id": operation_id,
        "experiment_family_id": (
            _experiment["family_id"] if _experiment is not None
            else f"finance-static-family:{refs['guard']}"
        ),
        "evaluation_arm": arm,
        "execution_mode": "EVALUATION_ONLY",
        "actor_id": actor_id,
        "case_revision_digest": case_revision,
        "change_set_digest": bundle.change_set.digest,
        "preview_digest": bundle.preview.digest,
        "run_envelope_digest": envelope.digest,
        "head_ref": resolution.head_ref,
        "head_digest": resolution.head_digest,
        "head_generation": resolution.generation,
        "candidate_bundle_digest": candidate_bundle.digest,
        "package_digest": candidate_bundle.package_digest,
        "snapshot_ref": snapshot_ref,
        "snapshot_digest": snapshot.digest,
        "manifest_ref": manifest_ref,
        "manifest_digest": manifest.digest,
        "budget_contract_digest": reservation["contract_digest"],
        "reserved_microusd": reservation["reserved_microusd"],
        "reserved_calls": reservation["calls"],
        "model_id": VERTEX_CANDIDATE_MODEL_ID,
        "consumer_version": adapter.version,
        "created_at": workspace.clock.now(),
        "target_writes": 0,
        **({"trial_ref": _experiment["trial_ref"]} if _experiment is not None else {}),
    }
    intent_digest = sha256_digest(intent)
    already_reserved = False
    try:
        with workspace.store.transaction() as connection:
            _authorized_actor(workspace)
            previous = _optional_artifact(workspace, refs["intent_ref"], INTENT_MEDIA)
            if previous is not None:
                if previous["actor_id"] != actor_id:
                    raise AuthorizationError("FINANCE_EVALUATION_OWNER_REQUIRED")
                if previous["operation_id"] != operation_id:
                    raise IntegrityError("FINANCE_EVALUATION_CASE_ALREADY_BOUND")
                already_reserved = True
            else:
                workspace.store.require_before_commit(connection, lambda: _authorized_actor(workspace))
                workspace.store.require_before_commit(connection, lambda: require_current(advice))
                PrivateRecordStore(
                    workspace.store, workspace.clock,
                    retention_seconds=workspace.private_retention_seconds,
                ).write(
                    connection, record_id=query_ref, scope_ref=query_scope,
                    owner_id=actor_id, payload=query_payload,
                )
                workspace.store.save_artifact(connection, snapshot_ref, SNAPSHOT_MEDIA, snapshot.model_dump(mode="json"))
                workspace.store.save_artifact(connection, manifest_ref, MANIFEST_MEDIA, manifest.model_dump(mode="json"))
                workspace.store.save_artifact(connection, refs["intent_ref"], INTENT_MEDIA, intent)
                workspace.store.append_event(connection, "FINANCE_EVALUATION_INTENT_RESERVED", {
                    "event_id": event_id, "operation_id": operation_id,
                    "intent_ref": refs["intent_ref"], "intent_digest": intent_digest,
                    "head_digest": resolution.head_digest,
                    "reserved_microusd": reservation["reserved_microusd"],
                })
    except Exception:
        # If another process won the immutable-ID race, observe its intent;
        # never dispatch a second request merely because this insert failed.
        winner = _optional_artifact(workspace, refs["intent_ref"], INTENT_MEDIA)
        if winner is None:
            raise
        if winner["actor_id"] != actor_id:
            raise AuthorizationError("FINANCE_EVALUATION_OWNER_REQUIRED") from None
        if winner["operation_id"] != operation_id:
            raise IntegrityError("FINANCE_EVALUATION_CASE_ALREADY_BOUND") from None
        return readback()
    if already_reserved:
        return readback()

    collaboration = None
    verified = None
    receipts = ()
    failure: str | None = None
    try:
        collaboration = adapter.run(
            fixture=fixture, change_set=bundle.change_set, preview=bundle.preview,
            run_envelope=envelope, now=workspace.clock.now(), evaluation_only=True,
        )
        receipts = tuple(
            item.payload["model_advisory"]["receipt"]
            for item in collaboration["handoffs"] if "model_advisory" in item.payload
        )
        verified = WorkspaceApplyAdvisoryVerifier(adapter).verify(
            fixture=fixture, change_set=bundle.change_set, preview=bundle.preview,
            run_envelope=envelope, collaboration=collaboration, now=workspace.clock.now(),
            evaluation_only=True, require_native=False,
        )
    except AdvisoryGenerationError as exc:
        receipts = tuple(item.model_dump(mode="json") for item in exc.receipts)
        failure = exc.reason_code
    except (IntegrityError, RuntimeError, ValueError) as exc:
        failure = type(exc).__name__
    finance = next(
        (item.payload["model_advisory"] for item in collaboration["handoffs"]
         if item.from_agent == "finance-steward" and "model_advisory" in item.payload),
        None,
    ) if collaboration is not None else None
    finance_receipt = next(
        (item for item in receipts if item.get("contract_version") == "4"), None
    )
    unknown_dispatch = any(item.get("dispatch_state") == "SENT_UNKNOWN" for item in receipts)
    current_input_qualified = True
    try:
        require_current(advice)
    except (IntegrityError, RuntimeError, ValueError):
        current_input_qualified = False
        verified = None
        failure = "FINANCE_EVALUATION_INPUT_CHANGED_AFTER_DISPATCH"
    status = (
        "PROTOCOL_VALID" if verified is not None and finance is not None
        else "HOLD" if not current_input_qualified and not unknown_dispatch
        else "HOLD" if collaboration is not None
        else "RESULT_UNKNOWN" if unknown_dispatch or (collaboration is None and not receipts)
        else "HOLD" if any(item.get("dispatch_state") == "RESPONSE_RECEIVED" for item in receipts)
        else "NOT_SENT"
    )
    if failure is None and status != "PROTOCOL_VALID":
        failure = "FINANCE_EVALUATION_INCOMPLETE"
    finance_wire = (
        build_vertex_advice_body(
            ModelRequestV4.model_validate(finance["request"]), DomainAdvisoryCandidate
        )
        if finance is not None else None
    )
    model_attempts = provider.observer.summary(run_ref=envelope.run_id)
    physical_attempts_observed = sum(
        item["dispatch_state"] != "NOT_DISPATCHED" for item in model_attempts["records"]
    )
    private_payload = {
        "schema_version": "orgrebase.finance-evaluation-private.v1",
        "intent_ref": refs["intent_ref"],
        "intent_digest": intent_digest,
        "collaboration": (
            {key: (
                [item.model_dump(mode="json") for item in value]
                if isinstance(value, tuple) else value.model_dump(mode="json")
            ) for key, value in collaboration.items()}
            if collaboration is not None and current_input_qualified else None
        ),
        "receipts": (
            list(receipts) if current_input_qualified else [
                {key: item.get(key) for key in (
                    "contract_version", "id", "request_ref", "request_digest", "status",
                    "dispatch_state", "provider_request_id", "body_digest", "error_code", "digest",
                )}
                for item in receipts
            ]
        ),
        "finance_wire_body": finance_wire if current_input_qualified else None,
        "model_attempts": model_attempts,
        "failure_class": failure,
        "provider_runtime_binding": dict(provider.runtime_binding),
    }
    private_digest = sha256_digest(private_payload)
    result = {
        "schema_version": "orgrebase.finance-evaluation-result.v1",
        "scope": scope,
        "intent_ref": refs["intent_ref"], "intent_digest": intent_digest,
        "event_id": event_id, "operation_id": operation_id,
        "experiment_family_id": intent["experiment_family_id"],
        "evaluation_arm": arm, "execution_mode": "EVALUATION_ONLY",
        "status": status, "reason_code": failure,
        "candidate_bundle_digest": candidate_bundle.digest,
        "package_digest": candidate_bundle.package_digest,
        "finance_request_digest": (
            finance["request"]["digest"] if finance else
            finance_receipt.get("request_digest") if finance_receipt else None
        ),
        "finance_receipt_digest": (
            finance["receipt"]["digest"] if finance else
            finance_receipt.get("digest") if finance_receipt else None
        ),
        "finance_wire_body_digest": (
            finance["receipt"].get("body_digest") if finance else
            finance_receipt.get("body_digest") if finance_receipt else None
        ),
        "finance_provider_request_id": (
            finance["receipt"].get("provider_request_id") if finance else
            finance_receipt.get("provider_request_id") if finance_receipt else None
        ),
        "private_record_ref": refs["private_ref"],
        "private_record_digest": private_digest,
        "budget_contract_digest": reservation["contract_digest"],
        "reserved_microusd": reservation["reserved_microusd"],
        "physical_attempts_observed": physical_attempts_observed,
        "attempt_observation_coverage": model_attempts["coverage"],
        "observed_model_id": finance["receipt"].get("observed_model_id") if finance else None,
        "quality_status": "NOT_EVALUATED",
        "target_writes": 0,
        "recorded_at": workspace.clock.now(),
    }
    with workspace.store.transaction() as connection:
        _authorized_actor(workspace)
        workspace.store.require_before_commit(connection, lambda: _authorized_actor(workspace))
        if status == "PROTOCOL_VALID":
            workspace.store.require_before_commit(connection, lambda: require_current(advice))
        PrivateRecordStore(
            workspace.store, workspace.clock, retention_seconds=workspace.private_retention_seconds
        ).write(
            connection, record_id=refs["private_ref"], scope_ref=refs["intent_ref"],
            owner_id=actor_id, payload=private_payload,
        )
        workspace.store.save_artifact(connection, refs["result_ref"], RESULT_MEDIA, result)
        workspace.store.append_event(connection, "FINANCE_EVALUATION_RESULT_RECORDED", {
            "event_id": event_id, "operation_id": operation_id,
            "result_ref": refs["result_ref"], "result_digest": sha256_digest(result),
            "status": status, "target_writes": 0,
        })
    return readback()


def evaluate_registered_finance_arm(
    workspace: Any, *, ledger: FinanceExperimentService,
    family_ref: str, event_id: str, seed: int, order: str, arm: str,
    provider_override: VertexAIStructuredProvider | None = None,
    candidate_bundle: SkillContentBundleV2 | None = None,
    snapshot: MemorySnapshot | None = None,
    selected_lessons: tuple[LessonSnapshotEntry, ...] = (),
    advice_text: str = "",
) -> dict[str, Any]:
    """Use the same V4 consumer for one pre-registered controlled arm.

    This path accepts a model-stub fixture family only.  Four comparator arms
    can exercise real compilation, wire, provider and verifier code with a
    controlled transport; Reflection/GEPA remain explicitly NOT_RUN until a
    durable optimizer generation and a real budget are available.  It does not
    issue a qualification or enable normal-business adoption.
    """
    actor_id = _authorized_actor(workspace)
    if (
        ledger.store is not workspace.store or ledger.workspace is not workspace
        or ledger.operator_actor_id != actor_id
        or ledger.tenant_id != workspace.profile.organization_id
    ):
        raise IntegrityError("FINANCE_EXPERIMENT_RUNNER_SCOPE_INVALID")
    family = ledger._load_family(family_ref)
    if family["execution_scope"] != "CONTROLLED_FIXTURE":
        raise IntegrityError("FINANCE_EXPERIMENT_REAL_RUNNER_NOT_QUALIFIED")
    if arm in {"SINGLE_REFLECTION", "BOUNDED_GEPA"}:
        return {
            "status": "NOT_RUN", "reason_code": "OPTIMIZER_AND_REAL_BUDGET_NOT_QUALIFIED",
            "arm": arm, "event_id": event_id, "target_writes": 0,
        }
    if arm not in {"STATIC_CURRENT", "NO_MEMORY", "HUMAN_REVIEWED", "SPARSE_RECALL"}:
        raise IntegrityError("FINANCE_EXPERIMENT_ARM_INVALID")
    if event_id not in family["cases"] or seed not in family["seeds"] or order not in family["orders"]:
        raise IntegrityError("FINANCE_EXPERIMENT_TRIAL_NOT_REGISTERED")
    _, current = _current_case(workspace, event_id)
    if finance_case_revision(current.change_set, current.preview) != family["cases"][event_id]["case_revision_digest"]:
        raise IntegrityError("FINANCE_EXPERIMENT_CASE_CHANGED")
    resolution = _current_static_head(workspace, actor_id)
    if (
        resolution.head_ref != family["parent_head_ref"]
        or resolution.head_digest != family["parent_head_digest"]
    ):
        raise IntegrityError("FINANCE_EXPERIMENT_PARENT_HEAD_CHANGED")
    if arm in {"STATIC_CURRENT", "NO_MEMORY"} and candidate_bundle is not None:
        raise IntegrityError("FINANCE_EXPERIMENT_BASELINE_BUNDLE_FIXED")
    if arm != "SPARSE_RECALL" and (snapshot is not None or selected_lessons or advice_text):
        raise IntegrityError("FINANCE_EXPERIMENT_BASELINE_MEMORY_FIXED")
    if arm == "SPARSE_RECALL" and (
        snapshot is None or not selected_lessons or not advice_text
    ):
        raise IntegrityError("FINANCE_EXPERIMENT_SPARSE_MEMORY_REQUIRED")
    trial_ref = ledger._trial_ref(
        family_ref, case_ref=event_id, seed=seed, order=order, arm=arm,
    )
    guard = trial_ref.rsplit(":", 1)[1]
    refs = {
        "guard": guard,
        "intent_ref": f"finance-evaluation-intent:{guard}",
        "result_ref": f"finance-evaluation-result:{guard}",
        "private_ref": f"finance-evaluation-private:{guard}",
    }
    operation_id = f"finance-experiment:{guard}"
    experiment = {
        "ledger": ledger, "family_ref": family_ref,
        "family_id": family["family_id"], "trial_ref": trial_ref,
        "seed": seed, "order": order, "arm": arm, "refs": refs,
        "candidate_bundle": candidate_bundle,
        "snapshot": snapshot, "selected_lessons": selected_lessons,
        "advice_text": advice_text,
    }
    view = evaluate_static_finance(
        workspace, event_id=event_id, operation_id=operation_id,
        provider_override=provider_override, _experiment=experiment,
    )
    if view["status"] == "PROTOCOL_VALID":
        ledger.record_controlled_protocol_result(
            trial_ref=trial_ref, result_ref=refs["result_ref"],
        )
        view = {**view, "experiment_trial_ref": trial_ref,
                "experiment_trial_status": ledger.trial_status(trial_ref),
                "evidence_scope": "CONTROLLED_MODEL_STUB_PROTOCOL_ONLY"}
    else:
        view = {**view, "experiment_trial_ref": trial_ref,
                "experiment_trial_status": ledger.trial_status(trial_ref),
                "evidence_scope": "CONTROLLED_MODEL_STUB_PROTOCOL_ONLY"}
    return view


__all__ = [
    "INTENT_MEDIA",
    "RESULT_MEDIA",
    "STATIC_ARM",
    "evaluate_registered_finance_arm",
    "evaluate_static_finance",
    "finance_evaluation_refs",
    "read_static_finance_evaluation",
]
