"""One immutable development input for Finance Skill candidates.

This is an input commitment, not a second head, experiment budget ledger,
model dispatch authority, sealed holdout, or qualification result.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field

from orgrebase.auth import AuthenticationError, authorize, current_authorization, request_principal
from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, ContentAddressedModel, IntegrityError
from orgrebase.workspace.advisory import WorkspaceChangeAdvisoryAdapter, finance_case_revision
from orgrebase.workspace.experience_contracts import MemorySnapshot
from orgrebase.workspace.experience_recall import ExperienceRecallService
from orgrebase.workspace.finance_adoption import _finance_recall_context_tags
from orgrebase.workspace.finance_experiment import (
    REVIEWED_FINANCE_RUBRIC_DIGEST,
    FinanceExperimentService,
    derive_finance_case_cluster,
)
from orgrebase.workspace.finance_explanation_operations import _current_case
from orgrebase.workspace.skill_evolution_v2 import (
    PROFILE_ID,
    REFERENCE_PATH,
    FinanceSkillHeadService,
)

INPUT_MEDIA = "application/vnd.orgrebase.finance-experiment-input.v1+json"
INTENT_MEDIA = "application/vnd.orgrebase.finance-experiment-input-intent.v1+json"
_OPERATION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
_GUIDANCE_BYTES = 4096
_MEMORY_BYTES = 4096
_WRAPPER_RESERVE_BYTES = 1024


class ExperimentInputSnapshot(ContentAddressedModel):
    """Low-sensitivity, exact input binding for one development case."""

    schema_version: Literal["orgrebase.finance-experiment-input.v1"] = (
        "orgrebase.finance-experiment-input.v1"
    )
    operation_id: str
    actor_id: str
    tenant_id: str
    workspace_id: str
    scope_digest: str
    family_ref: str
    family_digest: str
    event_id: str
    case_id: str
    case_revision_digest: str
    cluster_id: str
    source_change_key: str
    dependency_keys_digest: str
    context_tags: tuple[str, ...]
    split: Literal["DEVELOPMENT"] = "DEVELOPMENT"
    head_ref: str
    head_digest: str
    head_generation: int = Field(ge=0)
    package_digest: str
    bundle_digest: str
    reference_revision: str
    reference_digest: str
    policy_digest: str
    protected_kernel_digest: str
    snapshot_ref: str
    snapshot_digest: str
    manifest_ref: str
    manifest_digest: str
    query_ref: str
    query_digest: str
    task_id: str
    context_digest: str
    consumer_version: str
    compiler_version: str
    model_id: str
    rubric_digest: str
    budget_contract_digest: str
    memory_reserved_bytes: Literal[4096] = _MEMORY_BYTES
    guidance_budget_bytes: Literal[4096] = _GUIDANCE_BYTES
    guidance_wrapper_reserve_bytes: Literal[1024] = _WRAPPER_RESERVE_BYTES
    instruction_available_bytes: int = Field(ge=1, le=3072)
    evidence_scope: Literal["CONTROLLED_DEVELOPMENT_INPUT_ONLY"] = (
        "CONTROLLED_DEVELOPMENT_INPUT_ONLY"
    )
    qualification_status: Literal["NOT_QUALIFIED"] = "NOT_QUALIFIED"

    @property
    def ref(self) -> str:
        return "finance-experiment-input:" + sha256_digest({
            "scope_digest": self.scope_digest, "operation_id": self.operation_id,
        })[7:]


class FinanceExperimentInputService:
    def __init__(
        self, workspace: Any, *, head: FinanceSkillHeadService,
        experiment: FinanceExperimentService, recall: ExperienceRecallService,
    ) -> None:
        # Finance head and experiment family intentionally use different
        # hash projections. Their concrete tenant/workspace/profile must agree.
        if not (
            workspace.store is head.store is experiment.store
            and experiment.workspace is workspace and recall.workspace is workspace
            and recall.profile_id == PROFILE_ID
            and workspace.profile.organization_id == head.tenant_id == experiment.tenant_id
        ):
            raise ValueError("FINANCE_INPUT_SERVICE_SCOPE_INVALID")
        self.workspace = workspace
        self.head = head
        self.experiment = experiment
        self.recall = recall

    def _actor(self, *, evaluator: bool) -> str:
        principal = request_principal.get()
        if principal is None:
            raise AuthenticationError("FINANCE_INPUT_PRINCIPAL_REQUIRED")
        expected = (
            self.experiment.evaluator_actor_id if evaluator
            else self.experiment.operator_actor_id
        )
        authorize(principal, "govern" if evaluator else "propose", self.head.tenant_id)
        if principal.actor_id != expected:
            raise AuthorizationError("FINANCE_INPUT_PHASE_ACTOR_REQUIRED")
        check = current_authorization()
        if check is None:
            raise AuthenticationError("FINANCE_INPUT_CURRENT_AUTHORIZATION_REQUIRED")
        check()
        return principal.actor_id

    def _governor(self) -> str:
        principal = self.head._governor()
        check = current_authorization()
        if check is None:
            raise AuthenticationError("FINANCE_INPUT_CURRENT_AUTHORIZATION_REQUIRED")
        check()
        expected = self.head._current_source().payload["actor_id"]
        if (
            principal.actor_id != expected
            or principal.actor_id != self.experiment.release_governor_actor_id
            or principal.actor_id in {
                self.experiment.operator_actor_id, self.experiment.evaluator_actor_id,
            }
        ):
            raise AuthorizationError("FINANCE_INPUT_GOVERNOR_REQUIRED")
        return principal.actor_id

    def _family_case(self, family_ref: str, event_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        family = self.experiment._load_family(family_ref)
        case = family["cases"].get(event_id)
        head = self.head.resolve()
        if (
            family["execution_scope"] != "CONTROLLED_FIXTURE"
            or case is None or case["split"] != "DEVELOPMENT"
            or family["rubric_digest"] != REVIEWED_FINANCE_RUBRIC_DIGEST
            or family["parent_head_ref"] != head.head_ref
            or family["parent_head_digest"] != head.head_digest
            or family["consumer_version"] != head.bundle.payload["consumer_id"]
            or family.get("release_governor_actor_id")
            != self.head._current_source().payload["actor_id"]
        ):
            raise IntegrityError("FINANCE_INPUT_FAMILY_OR_HEAD_INVALID")
        derived = derive_finance_case_cluster(self.workspace, event_id)
        if (
            case["case_revision_digest"] != derived["case_revision_digest"]
            or case["independence_cluster_id"] != derived["cluster_id"]
            or case["source_change_key"] != derived["source_change_key"]
        ):
            raise IntegrityError("FINANCE_INPUT_CASE_OR_CLUSTER_CHANGED")
        return family, case, derived

    @staticmethod
    def _budget_digest(family: dict[str, Any], reference_size: int) -> str:
        return sha256_digest({
            "schema_version": "orgrebase.finance-dev-input-budget.v1",
            "family_ref": family["family_id"],
            "max_queries": family["max_queries"],
            "max_reserved_calls": family["max_reserved_calls"],
            "max_reserved_microusd": family["max_reserved_microusd"],
            "model_id": family["model_id"],
            "consumer_version": family["consumer_version"],
            "reference_size": reference_size,
            "memory_reserved_bytes": _MEMORY_BYTES,
            "guidance_budget_bytes": _GUIDANCE_BYTES,
            "wrapper_reserve_bytes": _WRAPPER_RESERVE_BYTES,
            "scope": "CONTROLLED_PRECHECK_NOT_VERTEX_PRICE_CONTRACT",
        })

    def freeze_development_case(
        self, *, operation_id: str, family_ref: str, event_id: str,
    ) -> str:
        actor_id = self._actor(evaluator=False)
        if not isinstance(operation_id, str) or _OPERATION.fullmatch(operation_id) is None:
            raise ValueError("FINANCE_INPUT_OPERATION_INVALID")
        family, _, derived = self._family_case(family_ref, event_id)
        head = self.head.resolve()
        input_ref = "finance-experiment-input:" + sha256_digest({
            "scope_digest": self.head.scope_digest, "operation_id": operation_id,
        })[7:]
        request = {
            "scope_digest": self.head.scope_digest, "operation_id": operation_id,
            "family_ref": family_ref, "family_digest": sha256_digest(family),
            "event_id": event_id, "actor_id": actor_id,
            "head_ref": head.head_ref, "head_digest": head.head_digest,
        }
        request_digest = sha256_digest(request)
        try:
            existing = self.workspace.store.load_artifact(input_ref, INPUT_MEDIA).payload
        except KeyError:
            existing = None
        if existing is not None:
            snapshot = ExperimentInputSnapshot.model_validate(existing).revalidated()
            if snapshot.ref != input_ref or (
                snapshot.family_ref, snapshot.family_digest, snapshot.event_id,
                snapshot.actor_id, snapshot.head_ref, snapshot.head_digest,
            ) != (
                family_ref, request["family_digest"], event_id,
                actor_id, head.head_ref, head.head_digest,
            ):
                raise IntegrityError("FINANCE_INPUT_OPERATION_CONFLICT")
            self.require_current(input_ref)
            return input_ref
        intent_ref = "finance-experiment-input-intent:" + input_ref.rsplit(":", 1)[1]
        case_key = "finance-experiment-input-cluster:" + sha256_digest({
            "scope_digest": self.head.scope_digest, "family_ref": family_ref,
            "cluster_id": derived["cluster_id"],
        })[7:]
        case_digest = sha256_digest({
            "family_ref": family_ref, "event_id": event_id,
            "cluster_id": derived["cluster_id"], "operation_id": operation_id,
        })
        with self.workspace.store.transaction() as connection:
            self._actor(evaluator=False)
            self.experiment._family_lock(connection, family_ref)
            self.workspace.store.require_before_commit(
                connection, lambda: self._actor(evaluator=False)
            )
            try:
                case_binding = self.workspace.store.get_idempotent(
                    case_key, case_digest, connection=connection,
                )
            except RuntimeError as exc:
                raise IntegrityError("FINANCE_INPUT_CLUSTER_ALREADY_FROZEN_OR_UNKNOWN") from exc
            if case_binding is not None:
                raise IntegrityError("FINANCE_INPUT_INCOMPLETE_OR_ALREADY_FROZEN")
            binding = self.workspace.store.get_idempotent(
                intent_ref, request_digest, connection=connection,
            )
            if binding is not None:
                raise IntegrityError("FINANCE_INPUT_INCOMPLETE_OR_ALREADY_FROZEN")
            self.workspace.store.save_idempotent(
                connection, case_key, case_digest, {"input_ref": input_ref},
            )
            self.workspace.store.save_idempotent(
                connection, intent_ref, request_digest, {"input_ref": input_ref},
            )
            self.workspace.store.save_artifact(
                connection, intent_ref, INTENT_MEDIA, request,
            )
            self.workspace.store.append_event(connection, "FINANCE_EXPERIMENT_INPUT_INTENT", {
                "intent_ref": intent_ref, "input_ref": input_ref,
                "family_ref": family_ref, "event_id": event_id,
                "request_digest": request_digest, "target_writes": 0,
            })
        fixture, bundle = _current_case(self.workspace, event_id)
        context_tags = _finance_recall_context_tags(self.workspace, event_id)
        plan, _ = WorkspaceChangeAdvisoryAdapter().compile(
            fixture=fixture, change_set=bundle.change_set, preview=bundle.preview,
        )
        task = next(
            (item for item in plan.tasks if item.authority_domain == "finance"), None
        )
        if task is None:
            raise IntegrityError("FINANCE_INPUT_FINANCE_TASK_REQUIRED")
        context_digest = sha256_digest({
            "family_ref": family_ref, "family_digest": sha256_digest(family),
            "event_id": event_id, "case_revision": derived["case_revision_digest"],
            "task_id": task.id, "head_digest": head.head_digest,
            "budget_digest": self._budget_digest(family, len(head.bundle.reference_bytes)),
        })
        snapshot_ref = self.recall.build_snapshot(
            purpose=PROFILE_ID, recipient="workspace-advisory",
        )
        snapshot = self.recall._snapshot(snapshot_ref)
        if snapshot.coverage == "PARTIAL_COVERAGE" or len(snapshot.lesson_entries) > 1:
            raise IntegrityError("FINANCE_INPUT_LESSON_SCOPE_UNSUPPORTED")
        manifest_ref, _ = self.recall.select_for_case(
            operation_id="finance-input:" + input_ref.rsplit(":", 1)[1],
            snapshot_ref=snapshot_ref, task_id=task.id, attempt_id=operation_id,
            context_digest=context_digest, case_id=bundle.change_set.id,
            case_revision=finance_case_revision(bundle.change_set, bundle.preview),
            evaluation_arm="SKILL_PAIR_FIXED_MEMORY",
            query_text="finance " + " ".join(delta.object_id for delta in bundle.change_set.deltas),
            context_tags=context_tags,
            object_kinds=tuple(sorted({
                fixture.object(delta.object_id, delta.base_version).kind
                for delta in bundle.change_set.deltas
            })),
            memory_reserved_bytes=_MEMORY_BYTES,
        )
        manifest, _ = self.recall.validate_manifest_for_consumption(
            manifest_ref, snapshot_ref=snapshot_ref,
            purpose=PROFILE_ID, recipient="workspace-advisory",
        )
        available = min(3072, _GUIDANCE_BYTES - _WRAPPER_RESERVE_BYTES - len(head.bundle.reference_bytes))
        if available <= 0 or len(head.bundle.instruction_bytes) > available:
            raise IntegrityError("FINANCE_INPUT_GUIDANCE_BUDGET_EXCEEDED")
        frozen = ExperimentInputSnapshot(
            operation_id=operation_id, actor_id=actor_id,
            tenant_id=self.head.tenant_id, workspace_id=self.workspace.store.workspace_id,
            scope_digest=self.head.scope_digest,
            family_ref=family_ref, family_digest=sha256_digest(family),
            event_id=event_id, case_id=bundle.change_set.id,
            case_revision_digest=derived["case_revision_digest"],
            cluster_id=derived["cluster_id"],
            source_change_key=derived["source_change_key"],
            dependency_keys_digest=derived["dependency_keys_digest"],
            context_tags=context_tags,
            head_ref=head.head_ref, head_digest=head.head_digest,
            head_generation=head.generation, package_digest=head.package_digest,
            bundle_digest=head.bundle.digest,
            reference_revision=head.bundle.payload["reference_revision"],
            reference_digest=head.bundle.resource_digests[REFERENCE_PATH],
            policy_digest=head.bundle.payload["policy_digest"],
            protected_kernel_digest=head.bundle.payload["protected_kernel_digest"],
            snapshot_ref=snapshot_ref, snapshot_digest=snapshot.digest,
            manifest_ref=manifest_ref, manifest_digest=manifest.digest,
            query_ref=manifest.query_ref, query_digest=manifest.query_digest,
            task_id=task.id, context_digest=context_digest,
            consumer_version=family["consumer_version"],
            compiler_version=head.bundle.payload["compiler_version"],
            model_id=family["model_id"], rubric_digest=family["rubric_digest"],
            budget_contract_digest=self._budget_digest(family, len(head.bundle.reference_bytes)),
            instruction_available_bytes=available,
        )
        if frozen.ref != input_ref:
            raise IntegrityError("FINANCE_INPUT_IDENTITY_INVALID")
        with self.workspace.store.transaction() as connection:
            self.require_current_for_author_value(frozen)
            self.workspace.store.require_before_commit(
                connection, lambda: self.require_current_for_author_value(frozen)
            )
            self.workspace.store.save_artifact(
                connection, input_ref, INPUT_MEDIA, frozen.model_dump(mode="json"),
            )
            self.workspace.store.append_event(connection, "FINANCE_EXPERIMENT_INPUT_FROZEN", {
                "input_ref": input_ref, "input_digest": frozen.digest,
                "family_ref": family_ref, "case_revision_digest": frozen.case_revision_digest,
                "snapshot_digest": frozen.snapshot_digest,
                "manifest_digest": frozen.manifest_digest,
                "budget_contract_digest": frozen.budget_contract_digest,
                "evidence_scope": frozen.evidence_scope, "target_writes": 0,
            })
        return input_ref

    def require_current(self, input_ref: str, *, evaluator: bool = False) -> ExperimentInputSnapshot:
        actor_id = self._actor(evaluator=evaluator)
        frozen = self._load_input(input_ref)
        self._validate_current(frozen, input_ref=input_ref, actor_id=actor_id, evaluator=evaluator)
        return frozen

    def require_current_for_governor(self, input_ref: str) -> ExperimentInputSnapshot:
        """Revalidate development input under the actual current governor.

        This does not borrow the evaluator Principal or return private query,
        lesson text, sealed gold, or any model output.
        """

        actor_id = self._governor()
        frozen = self._load_input(input_ref)
        self._validate_current(
            frozen, input_ref=input_ref, actor_id=actor_id,
            evaluator=False, governor=True,
        )
        return frozen

    def _load_input(self, input_ref: str) -> ExperimentInputSnapshot:
        try:
            frozen = ExperimentInputSnapshot.model_validate(
                self.workspace.store.load_artifact(input_ref, INPUT_MEDIA).payload,
            ).revalidated()
        except KeyError as exc:
            raise IntegrityError("FINANCE_INPUT_UNKNOWN_OR_INCOMPLETE") from exc
        return frozen

    def require_current_for_author_value(self, frozen: ExperimentInputSnapshot) -> None:
        self._validate_current(
            frozen, input_ref=frozen.ref, actor_id=self._actor(evaluator=False),
            evaluator=False,
        )

    def _validate_current(
        self, frozen: ExperimentInputSnapshot, *, input_ref: str,
        actor_id: str, evaluator: bool, governor: bool = False,
    ) -> None:
        if (
            frozen.ref != input_ref or frozen.scope_digest != self.head.scope_digest
            or frozen.tenant_id != self.head.tenant_id
            or frozen.workspace_id != self.workspace.store.workspace_id
            or frozen.actor_id != self.experiment.operator_actor_id
            or (not evaluator and not governor and actor_id != frozen.actor_id)
            or (evaluator and actor_id != self.experiment.evaluator_actor_id)
            or (governor and actor_id != self.experiment.release_governor_actor_id)
            or (evaluator and governor)
        ):
            raise IntegrityError("FINANCE_INPUT_IDENTITY_OR_ROLE_INVALID")
        family, case, derived = self._family_case(frozen.family_ref, frozen.event_id)
        head = self.head.resolve()
        if (
            frozen.family_digest != sha256_digest(family)
            or frozen.head_ref != head.head_ref
            or frozen.head_digest != head.head_digest
            or frozen.head_generation != head.generation
            or frozen.package_digest != head.package_digest
            or frozen.bundle_digest != head.bundle.digest
            or frozen.reference_revision != head.bundle.payload["reference_revision"]
            or frozen.reference_digest != head.bundle.resource_digests[REFERENCE_PATH]
            or frozen.policy_digest != head.bundle.payload["policy_digest"]
            or frozen.protected_kernel_digest != head.bundle.payload["protected_kernel_digest"]
            or frozen.case_revision_digest != derived["case_revision_digest"]
            or frozen.cluster_id != case["independence_cluster_id"]
            or frozen.source_change_key != case["source_change_key"]
            or frozen.dependency_keys_digest != derived["dependency_keys_digest"]
            or frozen.context_tags != _finance_recall_context_tags(
                self.workspace, frozen.event_id,
            )
            or frozen.rubric_digest != family["rubric_digest"]
            or frozen.consumer_version != family["consumer_version"]
            or frozen.model_id != family["model_id"]
            or frozen.compiler_version != head.bundle.payload["compiler_version"]
            or frozen.budget_contract_digest
            != self._budget_digest(family, len(head.bundle.reference_bytes))
            or frozen.instruction_available_bytes
            != min(3072, _GUIDANCE_BYTES - _WRAPPER_RESERVE_BYTES - len(head.bundle.reference_bytes))
        ):
            raise IntegrityError("FINANCE_INPUT_STALE_BASE_OR_CASE")
        if evaluator or governor:
            manifest, _ = self.recall.validate_frozen_manifest_for_evaluation(
                frozen.manifest_ref, snapshot_ref=frozen.snapshot_ref,
                purpose=PROFILE_ID, recipient="workspace-advisory",
                expected_manifest_digest=frozen.manifest_digest,
            )
        else:
            manifest, _ = self.recall.validate_manifest_for_consumption(
                frozen.manifest_ref, snapshot_ref=frozen.snapshot_ref,
                purpose=PROFILE_ID, recipient="workspace-advisory",
            )
        snapshot: MemorySnapshot = self.recall._snapshot(frozen.snapshot_ref)
        if (
            snapshot.digest != frozen.snapshot_digest
            or snapshot.coverage == "PARTIAL_COVERAGE"
            or len(snapshot.lesson_entries) > 1
            or len(manifest.selected_lessons) > 1
            or manifest.digest != frozen.manifest_digest
            or manifest.case_id != frozen.case_id
            or manifest.case_revision != frozen.case_revision_digest
            or manifest.evaluation_arm != "SKILL_PAIR_FIXED_MEMORY"
            or manifest.task_id != frozen.task_id
            or manifest.attempt_id != frozen.operation_id
            or manifest.context_digest != frozen.context_digest
            or manifest.query_ref != frozen.query_ref
            or manifest.query_digest != frozen.query_digest
            or manifest.memory_reserved_bytes != _MEMORY_BYTES
        ):
            raise IntegrityError("FINANCE_INPUT_FROZEN_MEMORY_CHANGED")


__all__ = ["INPUT_MEDIA", "ExperimentInputSnapshot", "FinanceExperimentInputService"]
