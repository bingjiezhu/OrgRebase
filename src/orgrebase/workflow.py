"""Freshness-locked selective Rebase and machine-verifiable receipt."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from orgrebase.agentteams_ingest import (
    assert_zero_write_ingestion,
    ingest_local_proposals,
)
from orgrebase.certificates import MinimalRebaseCertificateVerifier
from orgrebase.collaboration import OrchestrationCompiler
from orgrebase.context import ContextCompiler
from orgrebase.digest import sha256_digest
from orgrebase.domain import (
    AgentCandidateIngestionReceipt,
    AgentRun,
    Approval,
    ChangeSetRevision,
    CompilationReceipt,
    ContextManifest,
    CoordinationReceipt,
    CoverageBasis,
    EffectDisposition,
    EvidenceClass,
    FreshnessError,
    ImpactPreview,
    ImpactResult,
    IntegrityError,
    MinimalRebaseCertificate,
    ObjectState,
    OrchestrationPlan,
    RebaseReceipt,
    RunEnvelope,
    SemanticClassification,
    StructuredHandoff,
    VersionedObject,
)
from orgrebase.fixture import EnterpriseFixture
from orgrebase.skills import SkillEvaluator
from orgrebase.store import StateStore
from orgrebase.workspace.models import WorkflowIdentity

_EXECUTED_DISPOSITIONS = (
    EffectDisposition.REBUILD,
    EffectDisposition.PRESERVE_WITHIN_BOUNDARY,
    EffectDisposition.HOLD_FOR_REVIEW,
    EffectDisposition.REQUALIFY,
)


class WorkflowClock(Protocol):
    approved_at: str
    apply_at: str
    expires_at: str


class LegacyWorkflowClock:
    approved_at = "2026-08-14T00:03:00Z"
    apply_at = "2026-08-14T00:04:00Z"
    expires_at = "2026-08-14T01:00:00Z"


LEGACY_WORKFLOW_IDENTITY = WorkflowIdentity(
    namespace="legacy",
    approval_prefix="approval",
    receipt_prefix="rebase",
    coordination_prefix="coordination",
    idempotency_prefix="apply",
    extension_receipt_prefix="workspace-rebase",
)


class RebaseWorkflow:
    def __init__(
        self,
        fixture: EnterpriseFixture,
        store: StateStore,
        *,
        identity: WorkflowIdentity = LEGACY_WORKFLOW_IDENTITY,
        clock: WorkflowClock | None = None,
        context_provider: Any | None = None,
        rebuild_handlers: Mapping[str, Any] | None = None,
        apply_extension: Any | None = None,
        advisory_verifier: Any | None = None,
        skill_evaluator: SkillEvaluator | None = None,
    ) -> None:
        self.fixture = fixture
        self.store = store
        self.identity = identity
        self.clock = clock or LegacyWorkflowClock()
        self.context_provider = context_provider
        self.rebuild_handlers = dict(rebuild_handlers or {})
        self.apply_extension = apply_extension
        self.advisory_verifier = advisory_verifier
        self.last_extension_receipt = None
        self.context_compiler = ContextCompiler(fixture, store)
        self.skill_evaluator = skill_evaluator or SkillEvaluator(fixture)
        self.minimal_certificate_verifier = MinimalRebaseCertificateVerifier(fixture)

    def approve(
        self,
        change_set: ChangeSetRevision,
        preview: ImpactPreview,
        minimal_rebase_certificate: MinimalRebaseCertificate,
        actor_id: str = "human:change-approver",
    ) -> Approval:
        if preview.state != "READY":
            raise RuntimeError("PREVIEW_NOT_APPROVABLE")
        self.minimal_certificate_verifier.verify(
            minimal_rebase_certificate.model_dump(mode="json"), change_set
        )
        self._assert_preview_matches_certificate(preview, minimal_rebase_certificate)
        return Approval(
            id=self._approval_id(change_set),
            actor_id=actor_id,
            change_set_digest=change_set.digest,
            preview_digest=preview.digest,
            minimal_rebase_certificate_digest=minimal_rebase_certificate.digest,
            authorization_revision=self.fixture.revisions["authorization"],
            authority_scope=tuple(sorted((change_set.id, *change_set.scope))),
            approved_at=self.clock.approved_at,
            expires_at=self.clock.expires_at,
            method=(
                "LOCAL_EXPLICIT_DEMO_APPROVAL"
                if self.identity.namespace == "legacy"
                else "LOCAL_EXPLICIT_WORKSPACE_APPROVAL"
            ),
        )

    @staticmethod
    def assert_fresh(preview: ImpactPreview, current_revisions: dict[str, str]) -> None:
        lock = preview.revision_lock
        expected = {
            "graph": lock.graph_revision,
            "policy": lock.policy_revision,
            "skill_registry": lock.skill_registry_revision,
            "runtime_registry": lock.runtime_registry_revision,
        }
        drift = {
            key: {"expected": value, "actual": current_revisions.get(key)}
            for key, value in expected.items()
            if current_revisions.get(key) != value
        }
        if drift:
            raise FreshnessError(f"preview revision drift: {drift}")

    def apply(
        self,
        *,
        change_set: ChangeSetRevision,
        preview: ImpactPreview,
        minimal_rebase_certificate: MinimalRebaseCertificate,
        approval: Approval,
        collaboration: dict[str, Any],
        run_envelope: RunEnvelope,
        current_revisions: dict[str, str] | None = None,
        idempotency_key: str = "apply:launch-date@r1",
    ) -> RebaseReceipt:
        self.minimal_certificate_verifier.verify(
            minimal_rebase_certificate.model_dump(mode="json"), change_set
        )
        self._assert_preview_matches_certificate(preview, minimal_rebase_certificate)
        if preview.state != "READY":
            raise RuntimeError("PREVIEW_NOT_APPROVABLE")
        if len(change_set.deltas) != 1:
            raise ValueError("EXACTLY_ONE_ADMITTED_SEMANTIC_DELTA_REQUIRED")
        delta = change_set.deltas[0]
        if delta.semantic_classification != SemanticClassification.SEMANTIC_DELTA:
            raise IntegrityError("APPLY_REQUIRES_SEMANTIC_DELTA")
        proposed = self.store.get_object(delta.object_id, delta.proposed_version)
        if proposed.payload.get("canonical_value") != delta.proposed_value:
            raise IntegrityError("ADMITTED_DELTA_DOES_NOT_MATCH_STORED_PROPOSAL")
        if (
            approval.change_set_digest != change_set.digest
            or approval.preview_digest != preview.digest
            or approval.minimal_rebase_certificate_digest
            != minimal_rebase_certificate.digest
        ):
            raise FreshnessError(
                "approval is not bound to the exact ChangeSet, Preview, and minimal certificate"
            )
        expected_scope = tuple(sorted((change_set.id, *change_set.scope)))
        if (
            approval.authorization_revision != self.fixture.revisions["authorization"]
            or approval.authority_scope != expected_scope
        ):
            raise IntegrityError("approval authorization root or authority scope is invalid")
        if not (approval.approved_at < self.clock.apply_at < approval.expires_at):
            raise IntegrityError("approval is expired or temporally invalid")
        revisions = dict(current_revisions or self.fixture.revisions)
        self.assert_fresh(preview, revisions)
        if preview.revision_lock.digest != minimal_rebase_certificate.revision_lock_digest:
            raise IntegrityError("apply freshness lock is not the lock committed by the certificate")
        if self.advisory_verifier is None:
            agent_runs = self._bound_agent_runs(
                change_set=change_set,
                preview=preview,
                collaboration=collaboration,
                run_envelope=run_envelope,
            )
            ingestion = self._bound_candidate_ingestion(
                change_set=change_set,
                preview=preview,
                collaboration=collaboration,
                run_envelope=run_envelope,
            )
            compilation = self._bound_compilation_receipt(
                change_set=change_set,
                preview=preview,
                collaboration=collaboration,
            )
        else:
            advisory = self.advisory_verifier.verify(
                fixture=self.fixture,
                change_set=change_set,
                preview=preview,
                collaboration=collaboration,
                run_envelope=run_envelope,
            )
            agent_runs = advisory.agent_runs
            ingestion = advisory.ingestion_receipt
            compilation = advisory.compilation_receipt
        request_digest = sha256_digest(
            {
                "change_set": change_set.digest,
                "preview": preview.digest,
                "minimal_rebase_certificate": minimal_rebase_certificate.digest,
                "approval": approval.digest,
            }
        )
        existing = self.store.get_idempotent(idempotency_key, request_digest)
        if existing is not None:
            return RebaseReceipt.model_validate(existing)

        result_by_id = {result.object_id: result for result in preview.results}
        effects = self._effects_by_disposition(minimal_rebase_certificate, result_by_id)
        affected = effects[EffectDisposition.REBUILD]
        bounded = effects[EffectDisposition.PRESERVE_WITHIN_BOUNDARY]
        unknown = effects[EffectDisposition.HOLD_FOR_REVIEW]
        requalify = effects[EffectDisposition.REQUALIFY]
        before = {
            result.object_id: self.store.get_object(result.object_id)
            for result in (*affected, *bounded, *unknown, *requalify)
        }
        qualification = self.skill_evaluator.qualify()
        if requalify and qualification.candidate_state != ObjectState.CANARY:
            raise RuntimeError("SKILL_QUALIFICATION_FAILED")

        transitions: list[dict[str, Any]] = []
        runtime_dependencies: list[dict[str, Any]] = []
        acknowledgements: list[dict[str, Any]] = []
        rebuilt_objects: list[VersionedObject] = []

        with self.store.transaction() as connection:
            delta = change_set.deltas[0]
            self.store.promote_version(
                connection,
                delta.object_id,
                delta.base_version,
                delta.proposed_version,
            )
            manifests = (
                self.context_provider.prepare(
                    fixture=self.fixture,
                    store=self.store,
                    change_set=change_set,
                    preview=preview,
                    affected=affected,
                )
                if self.context_provider is not None
                else self._context_manifests({item.object_id for item in affected})
            )
            manifest_by_target = {item.target_object_id: item for item in manifests}

            for result in affected:
                old = before[result.object_id]
                old_ref = self.store.transition_current(connection, result.object_id, ObjectState.STALE)
                manifest = manifest_by_target[result.object_id]
                deliverable_kind = str(old.payload.get("deliverable_kind", ""))
                handler = self.rebuild_handlers.get(deliverable_kind)
                if handler is None:
                    next_payload = {
                        **old.payload,
                        "rebased_from": old_ref,
                        "rebase_change_set": change_set.digest,
                        "premise": f"{delta.object_id}@{delta.proposed_version}",
                        "canonical_premise": delta.proposed_value,
                        "context_manifest": manifest.digest,
                    }
                else:
                    next_payload = dict(
                        handler.rebuild_payload(old=old, delta=delta, context=manifest)
                    )
                    handler.verify_payload(
                        old=old,
                        new_payload=next_payload,
                        delta=delta,
                        context=manifest,
                    )
                new = VersionedObject(
                    id=old.id,
                    version=self._successor_version(old.version),
                    kind=old.kind,
                    label=old.label,
                    domain=old.domain,
                    state=ObjectState.CURRENT,
                    payload=next_payload,
                    source_refs=old.source_refs,
                    valid_from=self.clock.apply_at,
                    sensitivity=old.sensitivity,
                    allowed_purposes=old.allowed_purposes,
                    coverage_complete=True,
                    coverage_basis=(CoverageBasis.RUNTIME_OBSERVED,),
                )
                self.store.insert_version(connection, new, make_current=True)
                rebuilt_objects.append(new)
                transitions.append(
                    {
                        "object_id": old.id,
                        "from": old.ref,
                        "from_state": "STALE",
                        "to": new.ref,
                        "to_state": "CURRENT",
                        "reason": result.reason_code,
                        "context_manifest": manifest.digest,
                    }
                )
                acknowledgements.append(
                    {
                        "object_id": old.id,
                        "owner": old.payload["owner"],
                        "version": new.ref,
                        "state": "ACKNOWLEDGED_LOCAL_DEMO",
                    }
                )
                for context_item in manifest.included:
                    runtime_dependencies.append(
                        {
                            "source": context_item.object_ref,
                            "target": new.ref,
                            "relation": "CONSUMED_BY",
                            "coverage_basis": "RUNTIME_OBSERVED",
                            "context_manifest": manifest.digest,
                        }
                    )

            for result in unknown:
                self.store.transition_current(connection, result.object_id, ObjectState.REVIEW_REQUIRED)

            for result in requalify:
                current = before[result.object_id]
                self.store.activate_version(
                    connection,
                    result.object_id,
                    current.version,
                    self._requalify_candidate_version(result.object_id, current.version),
                    base_state=ObjectState.REQUALIFICATION_REQUIRED,
                    proposed_state=ObjectState.CANARY,
                )
                activated = self.store.get_object(result.object_id)
                transitions.append(
                    {
                        "object_id": result.object_id,
                        "from": current.ref,
                        "from_state": current.state.value,
                        "to": activated.ref,
                        "to_state": activated.state.value,
                        "reason": result.reason_code,
                    }
                )

            self._last_contexts = manifests
            self._assert_content_oracle(
                affected=affected,
                bounded=bounded,
                before=before,
                delta=delta,
                transitions=transitions,
            )

            receipt = RebaseReceipt(
                id=self._receipt_id(change_set),
                status="COMPLETED",
                workflow_run_id=run_envelope.run_id,
                run_nonce=run_envelope.nonce,
                change_set_ref=f"{change_set.id}@{change_set.revision}",
                preview_ref=preview.id,
                minimal_rebase_certificate_digest=minimal_rebase_certificate.digest,
                approval_ref=approval.id,
                approval_digest=approval.digest,
                approval_actor_id=approval.actor_id,
                applied_claims=(
                    {
                        "object_id": delta.object_id,
                        "from": delta.base_version,
                        "to": delta.proposed_version,
                        "state": "CURRENT",
                    },
                ),
                transitions=tuple(transitions),
                bounded_unaffected=tuple(
                    {
                        "object_id": result.object_id,
                        "state": before[result.object_id].state.value,
                        "proof_digest": result.digest,
                    }
                    for result in bounded
                ),
                unknown=tuple(
                    {
                        "object_id": result.object_id,
                        "state": "REVIEW_REQUIRED",
                        "reason": result.reason_code,
                    }
                    for result in unknown
                ),
                context_manifests=manifests,
                runtime_dependencies=tuple(runtime_dependencies),
                agent_runs=agent_runs,
                acknowledgements=tuple(acknowledgements),
                qualification_report=qualification,
                metrics={
                    "facts_changed": 1,
                    "work_items_rebased": len(affected),
                    "bounded_unaffected": len(bounded),
                    "unknown": len(unknown),
                    "skills_requalified": len(requalify),
                    "unauthorized_disclosures": 0,
                    "false_invalidations": 0,
                },
                revision_lock=preview.revision_lock,
                evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
                candidate_ingestion_digest=ingestion.digest,
                compilation_receipt_digest=compilation.digest,
            )
            extension_receipt = None
            if self.apply_extension is not None:
                extension_receipt = self.apply_extension.commit(
                    connection,
                    store=self.store,
                    change_set=change_set,
                    preview=preview,
                    delta=delta,
                    rebuilt_objects=tuple(rebuilt_objects),
                    contexts=manifests,
                    base_receipt=receipt,
                    committed_at=self.clock.apply_at,
                )
                self.last_extension_receipt = extension_receipt
                if extension_receipt is not None:
                    self.store.save_artifact(
                        connection,
                        extension_receipt.id,
                        "application/vnd.orgrebase.workspace-rebase-receipt+json",
                        extension_receipt.model_dump(mode="json"),
                    )
            self.store.save_artifact(
                connection,
                receipt.id,
                "application/vnd.orgrebase.rebase-receipt+json",
                receipt.model_dump(mode="json"),
            )
            self.store.append_event(
                connection,
                "REBASE_APPLIED",
                {
                    "change_set_digest": change_set.digest,
                    "preview_digest": preview.digest,
                    "minimal_rebase_certificate_digest": minimal_rebase_certificate.digest,
                    "approval_digest": approval.digest,
                    "receipt_digest": receipt.digest,
                    "candidate_ingestion_digest": ingestion.digest,
                    "compilation_receipt_digest": compilation.digest,
                    "transition_count": len(transitions),
                    "runtime_dependency_count": len(runtime_dependencies),
                    "extension_receipt_digest": (
                        extension_receipt.digest if extension_receipt is not None else None
                    ),
                },
            )
            self.store.save_idempotent(
                connection,
                idempotency_key,
                request_digest,
                receipt.model_dump(mode="json"),
            )
        return receipt

    def _approval_id(self, change_set: ChangeSetRevision) -> str:
        if self.identity.namespace == "legacy":
            return "approval:launch-date@1"
        slug = change_set.id.split(":", 1)[-1]
        return f"{self.identity.approval_prefix}:{slug}@{change_set.revision}"

    def _receipt_id(self, change_set: ChangeSetRevision) -> str:
        if self.identity.namespace == "legacy":
            return "rebase:launch-date@1"
        slug = change_set.id.split(":", 1)[-1]
        return f"{self.identity.receipt_prefix}:{slug}@{change_set.revision}"

    def _bound_agent_runs(
        self,
        *,
        change_set: ChangeSetRevision,
        preview: ImpactPreview,
        collaboration: dict[str, Any],
        run_envelope: RunEnvelope,
    ) -> tuple[AgentRun, ...]:
        plan = _model(collaboration["orchestration_plan"], OrchestrationPlan)
        handoffs = tuple(_model(item, StructuredHandoff) for item in collaboration["handoffs"])
        runs = tuple(_model(item, AgentRun) for item in collaboration["agent_runs"])
        if (
            plan.change_set_digest != change_set.digest
            or plan.preview_digest != preview.digest
            or plan.revision_lock_digest != preview.revision_lock.digest
        ):
            raise IntegrityError("orchestration plan is not bound to the Apply inputs")
        recomputed = OrchestrationCompiler(self.fixture).verify_execution(
            plan=plan,
            handoffs=handoffs,
            runs=runs,
            run_envelope=run_envelope,
        )
        submitted = _model(collaboration["coordination_receipt"], CoordinationReceipt)
        if submitted.digest != recomputed.digest:
            raise IntegrityError("coordination receipt does not match recomputation")
        return runs

    def _bound_candidate_ingestion(
        self,
        *,
        change_set: ChangeSetRevision,
        preview: ImpactPreview,
        collaboration: dict[str, Any],
        run_envelope: RunEnvelope,
    ) -> AgentCandidateIngestionReceipt:
        if "candidate_ingestion" not in collaboration:
            raise IntegrityError("apply requires bound candidate ingestion")
        plan = _model(collaboration["orchestration_plan"], OrchestrationPlan)
        handoffs = tuple(_model(item, StructuredHandoff) for item in collaboration["handoffs"])
        coordination = _model(collaboration["coordination_receipt"], CoordinationReceipt)
        recomputed = ingest_local_proposals(
            change_set=change_set,
            preview=preview,
            plan=plan,
            handoffs=handoffs,
            coordination_receipt=coordination,
            run_envelope=run_envelope,
        )
        submitted = _model(
            collaboration["candidate_ingestion"], AgentCandidateIngestionReceipt
        )
        if submitted.digest != recomputed.digest:
            raise IntegrityError("candidate ingestion does not match recomputation")
        assert_zero_write_ingestion(recomputed)
        if recomputed.rejected_candidate_digests:
            raise IntegrityError("candidate ingestion rejected a bound proposal")
        return recomputed

    def _bound_compilation_receipt(
        self,
        *,
        change_set: ChangeSetRevision,
        preview: ImpactPreview,
        collaboration: dict[str, Any],
    ) -> CompilationReceipt:
        if "compilation_receipt" not in collaboration:
            raise IntegrityError("apply requires bound compilation receipt")
        plan = _model(collaboration["orchestration_plan"], OrchestrationPlan)
        recomputed = OrchestrationCompiler(self.fixture).compilation_receipt(
            plan, change_set, preview
        )
        submitted = _model(collaboration["compilation_receipt"], CompilationReceipt)
        if submitted.digest != recomputed.digest:
            raise IntegrityError("compilation receipt does not match recomputation")
        if recomputed.orchestration_plan_digest != plan.digest:
            raise IntegrityError("compilation receipt is not bound to the orchestration plan")
        return recomputed

    def _assert_content_oracle(
        self,
        *,
        affected: tuple[ImpactResult, ...],
        bounded: tuple[ImpactResult, ...],
        before: dict[str, VersionedObject],
        delta: Any,
        transitions: list[dict[str, Any]],
    ) -> None:
        preserve_ids = {result.object_id for result in bounded}
        written_ids = {item["object_id"] for item in transitions}
        if written_ids & preserve_ids:
            raise IntegrityError("PRESERVE_TARGET_WAS_WRITTEN")
        for result in affected:
            rebuilt = self.store.get_object(result.object_id)
            old = before[result.object_id]
            handler = self.rebuild_handlers.get(str(old.payload.get("deliverable_kind", "")))
            if handler is None:
                if rebuilt.payload.get("canonical_premise") != delta.proposed_value:
                    raise IntegrityError("REBUILD_DID_NOT_WRITE_ADMITTED_VALUE")
            else:
                context_digest = next(
                    item["context_manifest"]
                    for item in transitions
                    if item["object_id"] == result.object_id
                )
                context = next(
                    manifest
                    for manifest in self._last_contexts
                    if manifest.digest == context_digest
                )
                handler.verify_committed(
                    old=old, rebuilt=rebuilt, delta=delta, context=context
                )
        for result in bounded:
            preserved = self.store.get_object(result.object_id)
            if preserved.payload != before[result.object_id].payload:
                raise IntegrityError("PRESERVE_MUTATED_BUSINESS_PAYLOAD")
            if preserved.version != before[result.object_id].version:
                raise IntegrityError("PRESERVE_MUTATED_OBJECT_VERSION")

    @staticmethod
    def _effects_by_disposition(
        certificate: MinimalRebaseCertificate,
        result_by_id: dict[str, ImpactResult],
    ) -> dict[EffectDisposition, tuple[ImpactResult, ...]]:
        buckets: dict[EffectDisposition, list[ImpactResult]] = {
            disposition: [] for disposition in _EXECUTED_DISPOSITIONS
        }
        for effect in certificate.effects:
            if effect.disposition not in buckets:
                raise IntegrityError(f"UNHANDLED_EFFECT_DISPOSITION:{effect.disposition.value}")
            buckets[effect.disposition].append(result_by_id[effect.target_id])
        return {disposition: tuple(items) for disposition, items in buckets.items()}

    @staticmethod
    def _assert_preview_matches_certificate(
        preview: ImpactPreview,
        certificate: MinimalRebaseCertificate,
    ) -> None:
        if preview.digest != certificate.preview_digest:
            raise IntegrityError(
                "preview is not the Preview committed by the minimal certificate"
            )
        if preview.revision_lock.digest != certificate.revision_lock_digest:
            raise IntegrityError(
                "preview revision lock is not the lock committed by the minimal certificate"
            )
        results = {item.object_id: item for item in preview.results}
        certificates = {item.subject_id: item for item in preview.certificates}
        for effect in certificate.effects:
            result = results.get(effect.target_id)
            impact_certificate = certificates.get(effect.target_id)
            if result is None or impact_certificate is None:
                raise IntegrityError("minimal certificate effect is missing from the Preview")
            if (
                result.digest != effect.impact_result_digest
                or impact_certificate.digest != effect.impact_certificate_digest
            ):
                raise IntegrityError(
                    "preview result is not bound to the minimal certificate"
                )

    def _context_manifests(self, rebuild_ids: set[str]) -> tuple[ContextManifest, ...]:
        manifests = tuple(
            self.context_compiler.compile(actor_id)
            for actor_id, profile in self.fixture.context_profiles.items()
            if profile["target"] in rebuild_ids
        )
        missing = rebuild_ids - {item.target_object_id for item in manifests}
        if missing:
            raise IntegrityError(
                "REBUILD_TARGET_MISSING_CONTEXT:" + ",".join(sorted(missing))
            )
        return manifests

    def _requalify_candidate_version(self, object_id: str, current_version: str) -> str:
        candidates = [
            item
            for item in self.fixture.object_versions(object_id)
            if item.version != current_version
            and item.state in {ObjectState.PROPOSED, ObjectState.SHADOW}
        ]
        if len(candidates) != 1:
            raise IntegrityError(f"REQUALIFY_CANDIDATE_AMBIGUOUS:{object_id}")
        return candidates[0].version

    @staticmethod
    def _successor_version(version: str) -> str:
        if version.startswith("v") and version[1:].isdigit():
            return f"v{int(version[1:]) + 1}"
        raise RuntimeError(f"UNSUPPORTED_VERSION_SCHEME:{version}")


def _model(value: Any, model_type: type[Any]) -> Any:
    return value if isinstance(value, model_type) else model_type.model_validate(value)
