"""Commit-boundary proof-graph closure for the Workspace Reference Runtime.

Content-addressed models prove that each node is internally stable.  This
module proves the stronger property required before canonical writes: the
compiled Task, Context, Trace, Deliverable, dependency graph, receipt and
Enterprise Seed binding all describe the same formation.

The verifier is deliberately runtime-specific.  A future OAC Runtime Binding
can select a different verifier; Quote-specific rules are not presented as a
universal OAC rule.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel

from orgrebase.clock import utc_datetime
from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.runtime_contracts import ArtifactWrite
from orgrebase.workspace.execution import (
    ExecutionReferenceMonitor,
    StaticClock,
    quote_output_lineage,
)
from orgrebase.workspace.graph import (
    RUNTIME_MANIFEST_MEDIA_TYPE,
    SNAPSHOT_MEDIA_TYPE,
    UNIVERSE_MEDIA_TYPE,
    graph_pointer_object,
)
from orgrebase.workspace.models import (
    ActorContextProjection,
    AdmissionDecision,
    ClaimCandidate,
    CoalitionPlan,
    DomainCandidateBundle,
    PreparedFormationBundle,
    QuoteTaskLiterals,
    RuntimeDependencyManifest,
    TaskContextManifest,
    TaskInterpretationReceipt,
    TaskReceipt,
    TaskRequest,
    TaskTemplateVersion,
    TraceCoverageReceipt,
    WorkspaceGraphSnapshot,
    WorkspaceUniverse,
    WorkTrace,
)
from orgrebase.workspace.templates import default_capability_cards

_ERROR = "PREPARED_FORMATION_PROOF_GRAPH_INVALID"


def _fail(reason: str) -> None:
    raise IntegrityError(f"{_ERROR}:{reason}")


def _same(actual: BaseModel, expected: BaseModel, reason: str) -> None:
    if actual.model_dump(mode="json") != expected.model_dump(mode="json"):
        _fail(reason)


def _same_model_set[ModelT: BaseModel](
    actual: tuple[ModelT, ...],
    expected: tuple[ModelT, ...],
    *,
    key: Callable[[ModelT], str],
    reason: str,
) -> None:
    actual_by_key = {key(item): item for item in actual}
    expected_by_key = {key(item): item for item in expected}
    if len(actual_by_key) != len(actual) or set(actual_by_key) != set(expected_by_key):
        _fail(reason)
    for item_key, expected_item in expected_by_key.items():
        _same(actual_by_key[item_key], expected_item, reason)


def _artifact_index(
    prepared: PreparedFormationBundle,
) -> dict[str, ArtifactWrite]:
    indexed: dict[str, ArtifactWrite] = {}
    for write in prepared.artifact_writes:
        if write.artifact_id in indexed:
            _fail(f"DUPLICATE_ARTIFACT_ID:{write.artifact_id}")
        if sha256_digest(write.payload) != write.payload_digest:
            _fail(f"ARTIFACT_DIGEST_MISMATCH:{write.artifact_id}")
        indexed[write.artifact_id] = write
    return indexed


def _parse[ModelT: BaseModel](
    indexed: Mapping[str, ArtifactWrite],
    artifact_id: str,
    media_type: str,
    model_type: type[ModelT],
) -> ModelT:
    write = indexed.get(artifact_id)
    if write is None:
        _fail(f"ARTIFACT_MISSING:{artifact_id}")
    if write.media_type != media_type:
        _fail(f"ARTIFACT_MEDIA_TYPE_MISMATCH:{artifact_id}")
    return model_type.model_validate(write.payload)


def _models_by_media[ModelT: BaseModel](
    indexed: Mapping[str, ArtifactWrite],
    media_type: str,
    model_type: type[ModelT],
) -> tuple[ModelT, ...]:
    return tuple(
        model_type.model_validate(write.payload)
        for write in indexed.values()
        if write.media_type == media_type
    )


def _verify_bundle_partition(
    *,
    request: TaskRequest,
    template: TaskTemplateVersion,
    coalition: CoalitionPlan,
    candidates: tuple[ClaimCandidate, ...],
    bundles: tuple[DomainCandidateBundle, ...],
) -> str:
    modes = {item.transport_mode for item in bundles}
    if len(modes) != 1:
        _fail("DOMAIN_TRANSPORT_MODE_SET_INVALID")
    mode = next(iter(modes))
    domains = {item.domain_id for item in coalition.coverage}
    if {item.domain_id for item in bundles} != domains or len(bundles) != len(domains):
        _fail("DOMAIN_BUNDLE_SET_INVALID")
    cards = {
        item.domain_id: item
        for item in default_capability_cards(template.ref)
        if item.ref in coalition.selected_card_refs
    }
    bound: set[str] = set()
    for bundle in bundles:
        domain_candidates = tuple(
            item for item in candidates if item.issuer_domain_id == bundle.domain_id
        )
        digests = tuple(item.digest for item in domain_candidates)
        card = cards.get(bundle.domain_id)
        if (
            bundle.task_ref != request.id
            or bundle.template_ref != template.ref
            or bundle.coalition_plan_ref != coalition.id
            or card is None
            or bundle.worker_id != card.worker_id
            or set(bundle.candidate_refs) != set(digests)
            or len(bundle.candidate_refs) != len(set(bundle.candidate_refs))
            or bundle.candidate_set_digest != sha256_digest(sorted(digests))
        ):
            _fail(f"DOMAIN_BUNDLE_BINDING_INVALID:{bundle.id}")
        bound.update(digests)
    if bound != {item.digest for item in candidates}:
        _fail("UNBOUND_CLAIM_CANDIDATE")
    return mode


def _verify_live_evidence(
    *,
    indexed: Mapping[str, ArtifactWrite],
    prepared: PreparedFormationBundle,
    request: TaskRequest,
    template: TaskTemplateVersion,
    interpretation: TaskInterpretationReceipt,
    coalition: CoalitionPlan,
    candidates: tuple[ClaimCandidate, ...],
    bundles: tuple[DomainCandidateBundle, ...],
) -> tuple[tuple[ActorContextProjection, ...], str, str, str]:
    # Local import avoids making the generic Formation module depend on the
    # optional live transport module while still validating its typed receipts.
    from orgrebase.workspace.live_formation import (
        LIVE_PREPARE_MEDIA_TYPE,
        LIVE_TRANSPORT_CANDIDATE_MEDIA_TYPE,
        LIVE_TRANSPORT_RECEIPT_MEDIA_TYPE,
        LIVE_VERIFICATION_MEDIA_TYPE,
        LiveFormationPrepareBundle,
        LiveFormationVerificationReceipt,
    )
    from orgrebase.workspace.models import (
        DomainTransportCandidate,
        DomainTransportReceipt,
    )

    live_prepares = _models_by_media(
        indexed, LIVE_PREPARE_MEDIA_TYPE, LiveFormationPrepareBundle
    )
    verifications = _models_by_media(
        indexed, LIVE_VERIFICATION_MEDIA_TYPE, LiveFormationVerificationReceipt
    )
    receipts = _models_by_media(
        indexed, LIVE_TRANSPORT_RECEIPT_MEDIA_TYPE, DomainTransportReceipt
    )
    transport_candidates = _models_by_media(
        indexed,
        LIVE_TRANSPORT_CANDIDATE_MEDIA_TYPE,
        DomainTransportCandidate,
    )
    if len(live_prepares) != 1 or len(verifications) != 1 or len(receipts) != 1:
        _fail("LIVE_EVIDENCE_CARDINALITY_INVALID")
    live_prepare = live_prepares[0]
    verification = verifications[0]
    transport_receipt = receipts[0]
    _same(live_prepare.request, request, "LIVE_REQUEST_MISMATCH")
    _same(live_prepare.template, template, "LIVE_TEMPLATE_MISMATCH")
    _same(live_prepare.interpretation, interpretation, "LIVE_INTERPRETATION_MISMATCH")
    _same(live_prepare.coalition, coalition, "LIVE_COALITION_MISMATCH")
    if (
        live_prepare.revision_lock != coalition.revision_lock
        or verification.prepare_digest != live_prepare.digest
        or set(verification.claim_candidate_digests)
        != {item.digest for item in candidates}
        or set(verification.domain_bundle_digests)
        != {item.digest for item in bundles}
        or verification.transport_receipt_digest != transport_receipt.digest
        or set(verification.transport_candidate_digests)
        != {item.digest for item in transport_candidates}
        or verification.candidate_target_writes != 0
    ):
        _fail("LIVE_VERIFICATION_BINDING_INVALID")
    event = prepared.event_payload
    if (
        event.get("execution_mode") != "LIVE_AGENTTEAMS"
        or event.get("live_prepare_digest") != live_prepare.digest
        or event.get("live_verification_receipt_digest") != verification.digest
        or event.get("candidate_target_writes") != 0
    ):
        _fail("LIVE_EVENT_BINDING_INVALID")
    return (
        live_prepare.source_projections,
        live_prepare.run_envelope.run_id,
        verification.verified_at,
        live_prepare.expires_at,
    )


def _verify_controlled_agentteams_evidence(
    *,
    indexed: Mapping[str, ArtifactWrite],
    prepared: PreparedFormationBundle,
    request: TaskRequest,
    candidates: tuple[ClaimCandidate, ...],
    bundles: tuple[DomainCandidateBundle, ...],
) -> tuple[tuple[ActorContextProjection, ...], str, str, None]:
    from orgrebase.workspace.controlled_agentteams_formation import (
        CONTROLLED_AGENTTEAMS_FORMATION_MEDIA_TYPE,
        ControlledAgentTeamsFormationReceipt,
    )

    receipts = _models_by_media(
        indexed,
        CONTROLLED_AGENTTEAMS_FORMATION_MEDIA_TYPE,
        ControlledAgentTeamsFormationReceipt,
    )
    if len(receipts) != 1:
        _fail("CONTROLLED_AGENTTEAMS_EVIDENCE_CARDINALITY_INVALID")
    receipt = receipts[0]
    event = prepared.event_payload
    if (
        event.get("execution_mode") != "CONTROLLED_LOCAL_AGENTTEAMS"
        or event.get("controlled_agentteams_formation_receipt_digest")
        != receipt.digest
        or event.get("golden_competition_run_id") != receipt.run_id
        or event.get("golden_competition_correlation_id")
        != receipt.correlation_id
        or event.get("reviewer_result_digest") != receipt.reviewer_result_digest
        or event.get("tool_receipt_digest") != receipt.tool_receipt_digest
        or event.get("skill_invocation_receipt_digest")
        != receipt.skill_invocation_receipt_digest
        or event.get("candidate_target_writes") != 0
        or receipt.project_terminal_state != "completed"
        or receipt.candidate_target_writes != 0
        or receipt.canonical_target_writes != 0
        or set(receipt.claim_candidate_digests)
        != {item.digest for item in candidates}
        or set(receipt.domain_bundle_digests) != {item.digest for item in bundles}
        or any(
            item.transport_mode != "CONTROLLED_LOCAL_AGENTTEAMS"
            or item.evidence_class != "CONTROLLED_LOCAL_AGENTTEAMS"
            for item in bundles
        )
    ):
        _fail("CONTROLLED_AGENTTEAMS_EVIDENCE_BINDING_INVALID")

    candidates_by_domain: dict[str, list[ClaimCandidate]] = {}
    for candidate in candidates:
        candidates_by_domain.setdefault(candidate.issuer_domain_id, []).append(candidate)
    if set(candidates_by_domain) != set(receipt.manager_source_bindings):
        _fail("CONTROLLED_AGENTTEAMS_DOMAIN_BINDINGS_INVALID")
    for domain, domain_candidates in candidates_by_domain.items():
        source_bindings = receipt.manager_source_bindings[domain]
        if set(source_bindings) != {item.predicate for item in domain_candidates}:
            _fail(f"CONTROLLED_AGENTTEAMS_SLOT_BINDINGS_INVALID:{domain}")
        for candidate in domain_candidates:
            binding = source_bindings[candidate.predicate]
            source_ref = (
                candidate.source_refs[0] if len(candidate.source_refs) == 1 else None
            )
            if (
                candidate.subject_ref != binding.get("object_ref")
                or sha256_digest(candidate.value) != binding.get("value_digest")
                or candidate.semantic_kind.value != binding.get("semantic_kind")
                or candidate.authority_ref != binding.get("authority_ref")
                or candidate.sensitivity != binding.get("sensitivity")
                or candidate.purpose != receipt.task_purpose
                or candidate.task_ref not in {None, request.id}
                or candidate.claim_scope.value != "ORGANIZATION"
                or set(candidate.recipients)
                != {"workspace-renderer", f"{domain}-steward"}
                or source_ref is None
                or source_ref.source_id != binding.get("source_id")
                or source_ref.source_version != binding.get("source_version")
                or source_ref.source_digest != binding.get("source_digest")
                or source_ref.locator_class != "SYNTHETIC_LOCAL_SOURCE"
            ):
                _fail(
                    f"CONTROLLED_AGENTTEAMS_CANDIDATE_BINDING_INVALID:"
                    f"{domain}:{candidate.predicate}"
                )
    return receipt.source_projections, receipt.run_id, receipt.verified_at, None


def _verify_prepared_formation(
    formation: Any,
    prepared: PreparedFormationBundle,
    *,
    media: Mapping[str, str],
    profile_binding_artifact_id: str,
    profile_binding_media_type: str,
    snapshot_target_ids: tuple[str, ...],
    snapshot_scope_roots: tuple[str, ...],
) -> tuple[str, str] | None:
    indexed = _artifact_index(prepared)
    expected_request = formation.default_request(formation.profile)
    request = _parse(indexed, expected_request.id, media["task"], TaskRequest)
    request = formation.require_profile_bound_request(
        request,
        error_code="PREPARED_FORMATION_REQUEST_MODEL_INVALID",
    )
    if (
        request.digest != prepared.request_digest
        or request.idempotency_key != prepared.idempotency_key
    ):
        _fail("REQUEST_BUNDLE_BINDING_INVALID")
    _same(request, expected_request, "REQUEST_PROFILE_PROJECTION_MISMATCH")

    template = _parse(
        indexed,
        request.template_ref,
        media["template"],
        TaskTemplateVersion,
    )
    authoritative_template = formation.registry.get(request.template_ref)
    _same(template, authoritative_template, "TEMPLATE_REGISTRY_MISMATCH")
    requirement_candidates, expected_interpretation = formation.interpreter.propose(
        task=request,
        template=template,
    )
    interpretation = _parse(
        indexed,
        expected_interpretation.id,
        media["interpretation"],
        TaskInterpretationReceipt,
    )
    _same(
        interpretation,
        expected_interpretation,
        "TASK_INTERPRETATION_REPLAY_MISMATCH",
    )
    expected_coalition = formation.planner.plan(
        task=request,
        template=template,
        candidates=requirement_candidates,
        cards=default_capability_cards(template.ref),
        revision_lock=formation._revision_lock(),
    )
    coalition = _parse(
        indexed,
        expected_coalition.id,
        media["coalition"],
        CoalitionPlan,
    )
    _same(coalition, expected_coalition, "COALITION_REPLAY_MISMATCH")

    receipt_artifact = _parse(
        indexed,
        prepared.task_receipt.id,
        media["receipt"],
        TaskReceipt,
    )
    _same(
        receipt_artifact,
        prepared.task_receipt,
        "TASK_RECEIPT_ARTIFACT_MISMATCH",
    )
    receipt = prepared.task_receipt
    context = _parse(
        indexed,
        receipt.context_manifest_ref,
        media["context"],
        TaskContextManifest,
    )
    candidates = _models_by_media(indexed, media["candidate"], ClaimCandidate)
    bundles = _models_by_media(indexed, media["bundle"], DomainCandidateBundle)
    if not candidates or not bundles:
        _fail("CANDIDATE_EVIDENCE_MISSING")
    if any(indexed[item.digest].artifact_id != item.digest for item in candidates):
        _fail("CANDIDATE_ARTIFACT_ID_MISMATCH")
    mode = _verify_bundle_partition(
        request=request,
        template=template,
        coalition=coalition,
        candidates=candidates,
        bundles=bundles,
    )

    if mode == "LOCAL_DETERMINISTIC":
        commit_time = utc_datetime(formation.clock.now())
        if not utc_datetime(context.created_at) <= commit_time < utc_datetime(context.expires_at):
            _fail("LOCAL_FORMATION_CLOCK_OUTSIDE_LEASE")
        source_projections = formation.domain_registry.source_projections(
            task=request,
            template=template,
            plan=coalition,
            now=context.created_at,
        )
        expected_candidates, expected_bundles = (
            formation.domain_registry.execute_selected(
                task=request,
                template=template,
                plan=coalition,
                projections=source_projections,
                now=context.created_at,
            )
        )
        _same_model_set(
            candidates,
            expected_candidates,
            key=lambda item: item.digest,
            reason="LOCAL_CANDIDATE_REPLAY_MISMATCH",
        )
        _same_model_set(
            bundles,
            expected_bundles,
            key=lambda item: item.id,
            reason="LOCAL_BUNDLE_REPLAY_MISMATCH",
        )
        expected_run_id = formation.workflow_run_id or formation.default_run_id
        declared_parent_run_id = prepared.event_payload.get(
            "parent_workflow_run_id"
        )
        if formation.workflow_run_id is None:
            if declared_parent_run_id is not None:
                _fail("UNEXPECTED_PARENT_WORKFLOW_RUN_ID")
        elif declared_parent_run_id != formation.workflow_run_id:
            _fail("PARENT_WORKFLOW_RUN_ID_MISMATCH")
        verified_at = context.created_at
        context_expires_at: str | None = None
    elif mode == "LIVE_AGENTTEAMS":
        (
            source_projections,
            expected_run_id,
            verified_at,
            context_expires_at,
        ) = _verify_live_evidence(
            indexed=indexed,
            prepared=prepared,
            request=request,
            template=template,
            interpretation=interpretation,
            coalition=coalition,
            candidates=candidates,
            bundles=bundles,
        )
        if context.created_at != verified_at:
            _fail("LIVE_FORMATION_CLOCK_MISMATCH")
    elif mode == "CONTROLLED_LOCAL_AGENTTEAMS":
        (
            source_projections,
            expected_run_id,
            verified_at,
            context_expires_at,
        ) = _verify_controlled_agentteams_evidence(
            indexed=indexed,
            prepared=prepared,
            request=request,
            candidates=candidates,
            bundles=bundles,
        )
        if context.created_at != verified_at:
            _fail("CONTROLLED_AGENTTEAMS_FORMATION_CLOCK_MISMATCH")
    else:  # pragma: no cover - model literal keeps this defensive branch closed
        _fail(f"UNSUPPORTED_TRANSPORT_MODE:{mode}")

    decisions = _models_by_media(indexed, media["admission"], AdmissionDecision)
    expected_decisions = formation.admission.admit(
        task=request,
        template=template,
        coalition=coalition,
        candidates=candidates,
        now=verified_at,
        policy_revision=coalition.revision_lock["policy"],
    )
    _same_model_set(
        decisions,
        expected_decisions,
        key=lambda item: item.id,
        reason="ADMISSION_REPLAY_MISMATCH",
    )
    expected_context, final_projections = formation.context_compiler.compile(
        task=request,
        template=template,
        coalition=coalition,
        decisions=expected_decisions,
        candidates=candidates,
        now=verified_at,
        revisions=coalition.revision_lock,
        expires_at=context_expires_at,
    )
    _same(context, expected_context, "TASK_CONTEXT_REPLAY_MISMATCH")
    projection_artifacts = _models_by_media(
        indexed,
        media["projection"],
        ActorContextProjection,
    )
    _same_model_set(
        projection_artifacts,
        (*source_projections, *final_projections),
        key=lambda item: item.ref,
        reason="ACTOR_PROJECTION_SET_MISMATCH",
    )

    trace = _parse(indexed, receipt.trace_ref, media["trace"], WorkTrace)
    if (
        trace.run_id != expected_run_id
        or trace.started_at != verified_at
        or trace.completed_at != verified_at
    ):
        _fail("TRACE_RUNTIME_BINDING_INVALID")
    monitor = ExecutionReferenceMonitor(
        task=request,
        template=template,
        manifest=context,
        artifact_reader=None,
        actor_id="workspace-renderer",
        run_id=expected_run_id,
        clock=StaticClock(verified_at),
        id_factory=formation._id_factory(request),
    )
    inputs = formation.assembler.assemble(monitor)
    expected_payload = formation.renderer.render(
        task_literals=QuoteTaskLiterals(
            owner=str(request.input_values.get("owner", request.actor_id)),
            customer_id=request.customer_id or "customer:unknown",
        ),
        inputs=inputs,
    )
    expected_quote = formation._quote_object(context, expected_payload)
    _same(prepared.deliverable, expected_quote, "DELIVERABLE_REPLAY_MISMATCH")
    expected_trace = monitor.finish(
        output_ref=expected_quote.ref,
        output_payload=expected_quote.payload,
        output_field_lineage=quote_output_lineage(priced=expected_payload.pricing is not None),
    )
    _same(trace, expected_trace, "TRACE_REPLAY_MISMATCH")
    coverage = _parse(
        indexed,
        receipt.coverage_receipt_ref,
        media["coverage"],
        TraceCoverageReceipt,
    )
    expected_coverage = formation.coverage_verifier.verify(
        template=template,
        trace=trace,
        output_payload=expected_quote.payload,
        observed_channels=("REFERENCE_MONITOR",),
    )
    _same(coverage, expected_coverage, "TRACE_COVERAGE_REPLAY_MISMATCH")
    runtime_manifest = _parse(
        indexed,
        receipt.dependency_manifest_ref,
        RUNTIME_MANIFEST_MEDIA_TYPE,
        RuntimeDependencyManifest,
    )
    expected_manifest = formation.dependency_compiler.compile(
        task=request,
        template=template,
        trace=trace,
        coverage=coverage,
        consumer_ref=expected_quote.ref,
        consumer_domain=expected_quote.domain,
        revision_lock=coalition.revision_lock,
        now=verified_at,
        version=runtime_manifest.version,
    )
    _same(
        runtime_manifest,
        expected_manifest,
        "RUNTIME_MANIFEST_REPLAY_MISMATCH",
    )

    snapshot = _parse(
        indexed,
        receipt.graph_snapshot_ref,
        SNAPSHOT_MEDIA_TYPE,
        WorkspaceGraphSnapshot,
    )
    universe = _parse(
        indexed,
        snapshot.universe_id,
        UNIVERSE_MEDIA_TYPE,
        WorkspaceUniverse,
    )
    stored_universe = formation.store.load_artifact(
        snapshot.universe_id,
        UNIVERSE_MEDIA_TYPE,
    )
    if stored_universe.payload != universe.model_dump(mode="json"):
        _fail("UNIVERSE_AUTHORITY_MISMATCH")
    expected_snapshot = formation.snapshot_builder.build(
        universe=universe,
        replacement_objects=(expected_quote,),
        replacement_manifests=(runtime_manifest,),
        targets=snapshot_target_ids,
        scope_roots=snapshot_scope_roots,
        now=verified_at,
        version=snapshot.version,
        snapshot_id=formation.graph_snapshot_id,
        graph_namespace=formation.graph_pointer_id,
    )
    _same(snapshot, expected_snapshot, "SNAPSHOT_REPLAY_MISMATCH")
    expected_pointer = graph_pointer_object(
        version=snapshot.version,
        snapshot=snapshot,
        promoted_at=verified_at,
        object_id=formation.graph_pointer_id,
    )
    _same(
        prepared.graph_pointer,
        expected_pointer,
        "GRAPH_POINTER_REPLAY_MISMATCH",
    )
    expected_receipt = TaskReceipt(
        id=formation.task_receipt_id,
        task_ref=request.id,
        template_ref=template.ref,
        coalition_plan_ref=coalition.id,
        admission_decision_refs=tuple(item.id for item in expected_decisions),
        context_manifest_ref=context.ref,
        trace_ref=trace.ref,
        coverage_receipt_ref=coverage.id,
        dependency_manifest_ref=runtime_manifest.ref,
        deliverable_ref=expected_quote.ref,
        graph_snapshot_ref=snapshot.ref,
        revision_lock=snapshot.revisions,
        committed_at=verified_at,
    )
    _same(receipt, expected_receipt, "TASK_RECEIPT_REPLAY_MISMATCH")

    profile_binding = indexed.get(profile_binding_artifact_id)
    if (
        profile_binding is None
        or profile_binding.media_type != profile_binding_media_type
        or profile_binding.payload != formation._profile_binding_payload(request)
    ):
        _fail("ENTERPRISE_SEED_BINDING_MISMATCH")
    event_facts = {
        "task_ref": request.id,
        "deliverable_ref": expected_quote.ref,
        "trace_ref": trace.ref,
        "manifest_ref": runtime_manifest.ref,
        "snapshot_ref": snapshot.ref,
        "snapshot_digest": snapshot.digest,
        "task_receipt_digest": receipt.digest,
        "enterprise_seed_profile_ref": formation.profile.ref,
        "enterprise_seed_profile_digest": formation.profile_digest,
        "enterprise_seed_source_admission_receipt_digest": (
            formation.source_admission.digest
        ),
        "enterprise_seed_runtime_projection_digest": (
            formation.runtime_projection.runtime_projection_digest
        ),
        "enterprise_seed_binding_artifact_id": profile_binding_artifact_id,
        "enterprise_seed_binding_artifact_digest": profile_binding.payload_digest,
    }
    if any(prepared.event_payload.get(key) != value for key, value in event_facts.items()):
        _fail("FORMATION_EVENT_FACT_MISMATCH")
    # Other transports use their own live or logical-event clocks.
    return (context.created_at, context.expires_at) if mode == "LOCAL_DETERMINISTIC" else None


def verify_prepared_formation(
    formation: Any,
    prepared: PreparedFormationBundle,
    *,
    media: Mapping[str, str],
    profile_binding_artifact_id: str,
    profile_binding_media_type: str,
    snapshot_target_ids: tuple[str, ...],
    snapshot_scope_roots: tuple[str, ...],
) -> tuple[str, str] | None:
    """Verify the proof graph and return its local-runtime commit lease, if any."""

    try:
        return _verify_prepared_formation(
            formation,
            prepared,
            media=media,
            profile_binding_artifact_id=profile_binding_artifact_id,
            profile_binding_media_type=profile_binding_media_type,
            snapshot_target_ids=snapshot_target_ids,
            snapshot_scope_roots=snapshot_scope_roots,
        )
    except IntegrityError:
        raise
    except (KeyError, RuntimeError, TypeError, ValueError) as exc:
        raise IntegrityError(f"{_ERROR}:{type(exc).__name__}:{exc}") from exc
