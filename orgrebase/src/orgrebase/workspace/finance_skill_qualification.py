"""Independent sealed comparison and exact Finance Skill head promotion.

This gate intentionally cannot qualify the existing one-arm static evaluation.
Both arms must have separately persisted V4 intent/result receipts bound to a
pre-frozen sealed suite, and a distinct evaluator must review current private
evidence.  Publication remains a separate current-governor decision.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from orgrebase.auth import AuthenticationError, authorize, current_authorization, request_principal
from orgrebase.database import workspace_registry
from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.private_records import PrivateRecordStore
from orgrebase.workspace.advisory import DomainAdvisoryCandidate
from orgrebase.workspace.experience_governance_operations import ExperiencePhaseActors, _recall
from orgrebase.workspace.finance_experiment import (
    REVIEWED_FINANCE_RUBRIC_DIGEST,
    FinanceExperimentService,
    FinanceIndependentOracle,
)
from orgrebase.workspace.finance_experiment_input import FinanceExperimentInputService
from orgrebase.workspace.finance_skill_candidates import CANDIDATE_MEDIA, FinanceSkillCandidateService
from orgrebase.workspace.models import ModelRequestV4, ModelResponseReceiptV4
from orgrebase.workspace.skill_evolution_v2 import (
    CONTENT_MEDIA,
    FinanceSkillHeadService,
    FinanceSkillResolution,
    SkillContentBundleV2,
)
from orgrebase.workspace.vertex_candidate import build_vertex_advice_body

SUITE_MEDIA = "application/vnd.orgrebase.finance-sealed-suite.v1+json"
QUALIFICATION_MEDIA = "application/vnd.orgrebase.finance-skill-qualification.v1+json"
CONTENT_RELEASE_MEDIA = "application/vnd.orgrebase.finance-content-release.v1+json"
EVALUATION_INTENT_MEDIA = "application/vnd.orgrebase.finance-evaluation-intent.v1+json"
EVALUATION_RESULT_MEDIA = "application/vnd.orgrebase.finance-evaluation-result.v1+json"
_SUITE_SCHEMA = "orgrebase.finance-sealed-suite.v1"
_QUALIFICATION_SCHEMA = "orgrebase.finance-skill-qualification.v1"
_RELEASE_SCHEMA = "orgrebase.finance-content-release.v1"


@dataclass(frozen=True)
class FinancePairJudgment:
    case_ref: str
    case_revision_digest: str
    independence_cluster_id: str
    baseline_result_ref: str
    candidate_result_ref: str
    grounded_score_before: int
    grounded_score_after: int
    next_step_score_before: int
    next_step_score_after: int
    hard_safety_passed: bool
    reason_code: str
    baseline_assessment_ref: str | None = None
    candidate_assessment_ref: str | None = None

    def __post_init__(self) -> None:
        if (
            not all((
                self.case_ref, self.case_revision_digest, self.independence_cluster_id,
                self.baseline_result_ref, self.candidate_result_ref, self.reason_code,
            ))
            or self.baseline_result_ref == self.candidate_result_ref
            or any(
                not 0 <= value <= 4 for value in (
                    self.grounded_score_before, self.grounded_score_after,
                    self.next_step_score_before, self.next_step_score_after,
                )
            )
        ):
            raise ValueError("FINANCE_QUALIFICATION_PAIR_INVALID")

    @property
    def gain(self) -> int:
        return (
            self.grounded_score_after + self.next_step_score_after
            - self.grounded_score_before - self.next_step_score_before
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_ref": self.case_ref,
            "case_revision_digest": self.case_revision_digest,
            "independence_cluster_id": self.independence_cluster_id,
            "baseline_result_ref": self.baseline_result_ref,
            "candidate_result_ref": self.candidate_result_ref,
            "grounded_score_before": self.grounded_score_before,
            "grounded_score_after": self.grounded_score_after,
            "next_step_score_before": self.next_step_score_before,
            "next_step_score_after": self.next_step_score_after,
            "hard_safety_passed": self.hard_safety_passed,
            "reason_code": self.reason_code,
            "baseline_assessment_ref": self.baseline_assessment_ref,
            "candidate_assessment_ref": self.candidate_assessment_ref,
        }


class FinanceSkillQualificationService:
    """The evaluator/reviewer use one checker but different current Principals."""

    def __init__(
        self, head: FinanceSkillHeadService, *, workspace: Any,
        evaluator_actor_id: str,
        assessor_actor_id: str | None = None,
        optimizer_actor_id: str | None = None,
        experiment_operator_actor_id: str | None = None,
        experiment_input: FinanceExperimentInputService | None = None,
    ) -> None:
        if (
            not evaluator_actor_id
            or workspace.store is not head.store
            or workspace.profile.organization_id != head.tenant_id
            or workspace.private_retention_seconds <= 0
        ):
            raise ValueError("FINANCE_QUALIFICATION_VERIFIER_REQUIRED")
        self.head = head
        self.store = head.store
        self.workspace = workspace
        self.evaluator_actor_id = evaluator_actor_id
        self.assessor_actor_id = assessor_actor_id
        self.optimizer_actor_id = optimizer_actor_id
        self.experiment_operator_actor_id = experiment_operator_actor_id
        if experiment_input is not None and (
            experiment_input.workspace is not workspace
            or experiment_input.head is not head
            or experiment_input.experiment.evaluator_actor_id != evaluator_actor_id
            or experiment_input.experiment.operator_actor_id != experiment_operator_actor_id
        ):
            raise ValueError("FINANCE_QUALIFICATION_INPUT_SERVICE_SCOPE_INVALID")
        self.experiment_input = experiment_input

    def _input_service(self) -> FinanceExperimentInputService:
        if self.experiment_input is not None:
            return self.experiment_input
        actors = ExperiencePhaseActors.from_deployment()
        return FinanceExperimentInputService(
            self.workspace, head=self.head,
            experiment=self._experiment_service(),
            recall=_recall(self.workspace, actors),
        )

    def _experiment_service(self) -> FinanceExperimentService:
        if not self.experiment_operator_actor_id:
            raise IntegrityError("FINANCE_EXPERIMENT_OPERATOR_UNCONFIGURED")
        return FinanceExperimentService(
            self.store, tenant_id=self.head.tenant_id,
            evaluator_actor_id=self.evaluator_actor_id,
            operator_actor_id=self.experiment_operator_actor_id,
            release_governor_actor_id=self.head._current_source().payload["actor_id"],
            workspace=self.workspace,
        )

    def _quality_oracle(self, author_actor_id: str) -> FinanceIndependentOracle:
        """Resolve all phase actors from deployment-owned service configuration."""

        if not self.assessor_actor_id or not self.optimizer_actor_id:
            raise IntegrityError("FINANCE_INDEPENDENT_QUALITY_ACTORS_UNCONFIGURED")
        current_governor = self.head._current_source().payload["actor_id"]
        try:
            return FinanceIndependentOracle(
                self.workspace,
                gold_owner_actor_id=self.evaluator_actor_id,
                assessor_actor_id=self.assessor_actor_id,
                candidate_author_actor_id=author_actor_id,
                optimizer_actor_id=self.optimizer_actor_id,
                release_governor_actor_id=current_governor,
            )
        except ValueError as exc:
            raise IntegrityError("FINANCE_INDEPENDENT_QUALITY_ROLES_INVALID") from exc

    def _candidate_proof(
        self, *, candidate_ref: str | None, candidate: SkillContentBundleV2,
        author_actor_id: str,
    ) -> dict[str, Any]:
        if not candidate_ref:
            raise IntegrityError("FINANCE_CANDIDATE_AUTHOR_PROOF_REQUIRED")
        record = self.store.load_artifact(candidate_ref, CANDIDATE_MEDIA).payload
        is_v2 = record.get("schema_version") == "orgrebase.finance-skill-candidate.v2"
        phase = "EVALUATOR"
        input_service = None
        if is_v2:
            principal = request_principal.get()
            if principal is None:
                raise AuthenticationError("FINANCE_QUALIFICATION_PRINCIPAL_REQUIRED")
            governor_id = self.head._current_source().payload["actor_id"]
            if principal.actor_id == self.evaluator_actor_id:
                phase = "EVALUATOR"
            elif principal.actor_id == governor_id:
                phase = "GOVERNOR"
            else:
                raise AuthorizationError("FINANCE_CANDIDATE_PHASE_ACTOR_REQUIRED")
            input_service = self._input_service()
        proof = FinanceSkillCandidateService(
            self.workspace, self.head, experiment_input=input_service,
        ).verify_for_qualification(candidate_ref, candidate=candidate, phase=phase)
        if proof["author_actor_id"] != author_actor_id:
            raise IntegrityError("FINANCE_CANDIDATE_AUTHOR_MISMATCH")
        return proof

    def _private_store(self) -> PrivateRecordStore:
        return PrivateRecordStore(
            self.store, self.workspace.clock,
            retention_seconds=self.workspace.private_retention_seconds,
        )

    def _lock_workspace_qualification_scope(self, connection: Any) -> None:
        """Serialize qualification reads with canonical workspace writes."""

        row = self.store.execute(
            connection,
            self.store._lock_row(
                select(workspace_registry.c.workspace_id).where(
                    workspace_registry.c.workspace_id == self.store.workspace_id,
                )
            ),
        ).fetchone()
        if row is None:
            raise IntegrityError("FINANCE_QUALIFICATION_WORKSPACE_SCOPE_MISSING")

    def _evaluator(self) -> str:
        principal = request_principal.get()
        if principal is None:
            raise AuthenticationError("FINANCE_QUALIFICATION_PRINCIPAL_REQUIRED")
        authorize(principal, "govern", self.head.tenant_id)
        if principal.actor_id != self.evaluator_actor_id:
            raise AuthorizationError("FINANCE_QUALIFICATION_EVALUATOR_REQUIRED")
        current = self.head._current_source()
        if principal.actor_id == current.payload["actor_id"]:
            raise AuthorizationError("FINANCE_QUALIFICATION_ROLE_COLLISION")
        check = current_authorization()
        if check is not None:
            check()
        return principal.actor_id

    def freeze_suite(
        self, *, suite_id: str, parent: FinanceSkillResolution,
        case_clusters: Mapping[str, str], case_gold_refs: Mapping[str, str],
        rubric_digest: str, experiment_family_id: str,
        max_physical_attempts: int, max_reserved_microusd: int,
        min_gain: int, max_regressions: int = 0,
    ) -> str:
        """Register low-sensitivity split/thresholds before any paired intent."""
        actor_id = self._evaluator()
        if (
            not suite_id or len(case_clusters) < 3
            or len(set(case_clusters.values())) != len(case_clusters)
            or set(case_clusters) != set(case_gold_refs)
            or rubric_digest != REVIEWED_FINANCE_RUBRIC_DIGEST
            or not experiment_family_id
            or max_physical_attempts < 1
            or max_reserved_microusd < 1
            or min_gain < 1 or max_regressions != 0
        ):
            raise IntegrityError("FINANCE_SEALED_SUITE_INVALID")
        if self.head.resolve() != parent:
            raise IntegrityError("FINANCE_SKILL_STALE_BASE")
        gold = {}
        for case_ref, record_ref in sorted(case_gold_refs.items()):
            try:
                payload = self._private_store().read_owned(
                    record_ref, owner_id=actor_id,
                    scope_ref="finance-sealed-suite:" + suite_id,
                )
            except (PermissionError, KeyError) as exc:
                raise IntegrityError("FINANCE_SEALED_GOLD_UNAVAILABLE") from exc
            if (
                payload is None
                or payload.get("schema_version") != "orgrebase.finance-sealed-gold-case.v1"
                or payload.get("case_ref") != case_ref
                or payload.get("independence_cluster_id") != case_clusters[case_ref]
                or not payload.get("case_revision_digest")
                or not payload.get("expected_source_refs_digest")
                or payload.get("rubric_digest") != REVIEWED_FINANCE_RUBRIC_DIGEST
            ):
                raise IntegrityError("FINANCE_SEALED_GOLD_INVALID")
            gold[case_ref] = {
                "record_ref": record_ref,
                "record_digest": sha256_digest(payload),
                "case_revision_digest": payload["case_revision_digest"],
                "expected_source_refs_digest": payload["expected_source_refs_digest"],
            }
        suite = {
            "schema_version": _SUITE_SCHEMA,
            "suite_id": suite_id,
            "scope_digest": self.head.scope_digest,
            "parent_head_ref": parent.head_ref,
            "parent_head_digest": parent.head_digest,
            "parent_generation": parent.generation,
            "parent_package_digest": parent.package_digest,
            "case_clusters": dict(sorted(case_clusters.items())),
            "case_gold": gold,
            "rubric_digest": rubric_digest,
            "experiment_family_id": experiment_family_id,
            "max_physical_attempts": max_physical_attempts,
            "max_reserved_microusd": max_reserved_microusd,
            "min_gain": min_gain,
            "max_regressions": 0,
            "evaluator_actor_id": actor_id,
            "candidate_only": True,
        }
        ref = "finance-sealed-suite:" + sha256_digest(suite)[7:]
        with self.store.transaction() as connection:
            self._evaluator()
            self._lock_workspace_qualification_scope(connection)
            self.store.require_before_commit(connection, self._evaluator)
            if self.head.resolve() != parent:
                raise IntegrityError("FINANCE_SKILL_STALE_BASE")
            for item in gold.values():
                available = self._private_store().read_owned(
                    item["record_ref"], owner_id=actor_id,
                    scope_ref="finance-sealed-suite:" + suite_id,
                )
                if available is None or sha256_digest(available) != item["record_digest"]:
                    raise IntegrityError("FINANCE_SEALED_GOLD_UNAVAILABLE")
            existing_cases = 0
            for case_ref in sorted(case_clusters):
                case_key = "finance-sealed-case:" + sha256_digest({
                    "scope_digest": self.head.scope_digest,
                    "case_ref": case_ref,
                })[7:]
                try:
                    prior = self.store.get_idempotent(
                        case_key, sha256_digest(suite), connection=connection,
                    )
                except RuntimeError as exc:
                    raise IntegrityError("FINANCE_SEALED_CASE_ALREADY_USED") from exc
                if prior is not None:
                    if prior.get("suite_ref") != ref:
                        raise IntegrityError("FINANCE_SEALED_CASE_ALREADY_USED")
                    existing_cases += 1
                    continue
                self.store.save_idempotent(
                    connection, case_key, sha256_digest(suite), {"suite_ref": ref},
                )
            if existing_cases:
                if existing_cases != len(case_clusters):
                    raise IntegrityError("FINANCE_SEALED_CASE_PARTIAL_REUSE")
                if self.store.load_artifact(ref, SUITE_MEDIA).payload != suite:
                    raise IntegrityError("FINANCE_SEALED_SUITE_CONFLICT")
                return ref
            self.store.save_artifact(connection, ref, SUITE_MEDIA, suite)
            self.store.append_event(connection, "FINANCE_SEALED_SUITE_FROZEN", {
                "suite_ref": ref, "suite_digest": sha256_digest(suite),
                "head_ref": parent.head_ref, "head_digest": parent.head_digest,
                "case_count": len(case_clusters), "evaluator_actor_id": actor_id,
            })
        return ref

    def _read_current_gold(self, suite: Mapping[str, Any], case_ref: str) -> dict[str, Any]:
        item = suite["case_gold"].get(case_ref)
        if not isinstance(item, dict):
            raise IntegrityError("FINANCE_SEALED_GOLD_MISSING")
        try:
            payload = self._private_store().read_owned(
                item["record_ref"], owner_id=self.evaluator_actor_id,
                scope_ref="finance-sealed-suite:" + suite["suite_id"],
            )
        except (PermissionError, KeyError) as exc:
            raise IntegrityError("FINANCE_SEALED_GOLD_UNAVAILABLE") from exc
        if (
            payload is None
            or sha256_digest(payload) != item["record_digest"]
            or payload.get("case_ref") != case_ref
            or payload.get("case_revision_digest") != item["case_revision_digest"]
            or payload.get("expected_source_refs_digest")
            != item["expected_source_refs_digest"]
            or payload.get("rubric_digest") != REVIEWED_FINANCE_RUBRIC_DIGEST
        ):
            raise IntegrityError("FINANCE_SEALED_GOLD_UNAVAILABLE")
        return payload

    def _require_suite_precedes_intents(
        self, suite_ref: str, intent_refs: set[str], *, candidate_proposal_sequence: int,
    ) -> None:
        """Use the immutable workspace event order, never client timestamps."""
        suite_sequence: int | None = None
        reserved: dict[str, int] = {}
        cursor = 0
        examined = 0
        while True:
            page = self.store.event_page(
                after=cursor, limit=500,
                event_types=(
                    "FINANCE_SEALED_SUITE_FROZEN",
                    "FINANCE_EVALUATION_INTENT_RESERVED",
                ),
            )
            rows = page["items"]
            examined += len(rows)
            if examined > 10_000:
                raise IntegrityError("FINANCE_QUALIFICATION_EVENT_ORDER_UNBOUNDED")
            for event in rows:
                payload = event["payload"]
                if event["event_type"] == "FINANCE_SEALED_SUITE_FROZEN" and payload.get("suite_ref") == suite_ref:
                    if suite_sequence is not None:
                        raise IntegrityError("FINANCE_QUALIFICATION_SUITE_EVENT_AMBIGUOUS")
                    suite_sequence = event["sequence_no"]
                if event["event_type"] == "FINANCE_EVALUATION_INTENT_RESERVED":
                    ref = payload.get("intent_ref")
                    if ref in intent_refs:
                        if ref in reserved:
                            raise IntegrityError("FINANCE_QUALIFICATION_INTENT_EVENT_AMBIGUOUS")
                        reserved[ref] = event["sequence_no"]
            if page["next_cursor"] is None:
                break
            cursor = page["next_cursor"]
        if (
            suite_sequence is None
            or set(reserved) != intent_refs
            or not suite_sequence < candidate_proposal_sequence
            or any(sequence <= candidate_proposal_sequence for sequence in reserved.values())
        ):
            raise IntegrityError("FINANCE_QUALIFICATION_SUITE_NOT_PREDECLARED")

    def _read_pair_result(
        self, ref: str, *, expected_bundle: SkillContentBundleV2,
        parent: FinanceSkillResolution, suite_ref: str, suite_digest: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        result = self.store.load_artifact(ref, EVALUATION_RESULT_MEDIA).payload
        intent_ref = result.get("intent_ref")
        if not isinstance(intent_ref, str):
            raise IntegrityError("FINANCE_QUALIFICATION_RESULT_INVALID")
        intent = self.store.load_artifact(intent_ref, EVALUATION_INTENT_MEDIA).payload
        if (
            not ref.startswith("finance-evaluation-result:")
            or result.get("schema_version") != "orgrebase.finance-evaluation-result.v1"
            or intent.get("schema_version") != "orgrebase.finance-evaluation-intent.v1"
            or result.get("intent_digest") != sha256_digest(intent)
            or intent.get("scope") != "SEALED_HOLDOUT_PAIR"
            or result.get("scope") != "SEALED_HOLDOUT_PAIR"
            or result.get("status") != "PROTOCOL_VALID"
            or result.get("intent_ref") != intent_ref
            or result.get("event_id") != intent.get("event_id")
            or result.get("operation_id") != intent.get("operation_id")
            or result.get("experiment_family_id") != intent.get("experiment_family_id")
            or result.get("execution_mode") != "EVALUATION_ONLY"
            or intent.get("execution_mode") != "EVALUATION_ONLY"
            or intent.get("sealed_suite_ref") != suite_ref
            or intent.get("sealed_suite_digest") != suite_digest
            or not isinstance(intent.get("experiment_family_id"), str)
            or not intent.get("budget_contract_digest")
            or not isinstance(intent.get("reserved_microusd"), int)
            or intent["reserved_microusd"] <= 0
            or intent.get("head_ref") != parent.head_ref
            or intent.get("head_digest") != parent.head_digest
            or intent.get("head_generation") != parent.generation
            or intent.get("candidate_bundle_digest") != expected_bundle.digest
            or result.get("candidate_bundle_digest") != expected_bundle.digest
            or result.get("package_digest") != expected_bundle.package_digest
            or intent.get("package_digest") != expected_bundle.package_digest
            or result.get("observed_model_id") != "gemini-3.8-flash"
            or result.get("physical_attempts_observed", 0) < 1
            or result.get("attempt_observation_coverage") != "COMPLETE"
            or not all(result.get(field) for field in (
                "finance_request_digest", "finance_receipt_digest",
                "finance_wire_body_digest", "finance_provider_request_id",
                "private_record_ref", "private_record_digest",
            ))
            or result.get("target_writes") != 0
            or intent.get("target_writes") != 0
            or result.get("quality_status") != "NOT_EVALUATED"
        ):
            raise IntegrityError("FINANCE_QUALIFICATION_RESULT_INVALID")
        return intent, result

    def _verify_current_private_result(
        self, intent: Mapping[str, Any], result: Mapping[str, Any],
        gold: Mapping[str, Any],
    ) -> None:
        """Rebuild the actual V4 body and exact provider/candidate bindings."""
        try:
            private = self._private_store().read_owned(
                result["private_record_ref"], owner_id=intent["actor_id"],
                scope_ref=result["intent_ref"],
            )
        except (PermissionError, KeyError) as exc:
            raise IntegrityError("FINANCE_QUALIFICATION_PRIVATE_EVIDENCE_UNAVAILABLE") from exc
        if (
            private is None
            or sha256_digest(private) != result["private_record_digest"]
            or private.get("schema_version") != "orgrebase.finance-evaluation-private.v1"
            or private.get("intent_ref") != result["intent_ref"]
            or private.get("intent_digest") != result["intent_digest"]
            or not isinstance(private.get("collaboration"), dict)
            or not isinstance(private.get("finance_wire_body"), dict)
        ):
            raise IntegrityError("FINANCE_QUALIFICATION_PRIVATE_EVIDENCE_INVALID")
        handoffs = private["collaboration"].get("handoffs")
        if not isinstance(handoffs, list):
            raise IntegrityError("FINANCE_QUALIFICATION_PRIVATE_EVIDENCE_INVALID")
        finance_rows = [
            row["payload"]["model_advisory"]
            for row in handoffs
            if isinstance(row, dict)
            and isinstance(row.get("payload"), dict)
            and isinstance(row["payload"].get("model_advisory"), dict)
            and row["payload"]["model_advisory"].get("request", {}).get("contract_version") == "4"
        ]
        if len(finance_rows) != 1:
            raise IntegrityError("FINANCE_QUALIFICATION_V4_RESULT_REQUIRED")
        try:
            request = ModelRequestV4.model_validate(finance_rows[0]["request"]).revalidated()
            receipt = ModelResponseReceiptV4.model_validate(finance_rows[0]["receipt"]).revalidated()
            candidate = DomainAdvisoryCandidate.model_validate(receipt.value)
            wire = build_vertex_advice_body(request, DomainAdvisoryCandidate)
        except (KeyError, TypeError, ValueError) as exc:
            raise IntegrityError("FINANCE_QUALIFICATION_V4_RESULT_INVALID") from exc
        advice = request.advice_context
        business_refs = sorted(item.ref for item in request.business_input_projections)
        if (
            request.digest != result["finance_request_digest"]
            or receipt.digest != result["finance_receipt_digest"]
            or receipt.request_digest != request.digest
            or receipt.request_ref != request.request_id
            or receipt.status != "VALID"
            or receipt.provider_request_id != result["finance_provider_request_id"]
            or receipt.body_digest != result["finance_wire_body_digest"]
            or sha256_digest(wire) != receipt.body_digest
            or wire != private["finance_wire_body"]
            or advice.execution_mode != "EVALUATION_ONLY"
            or advice.head_ref != intent["head_ref"]
            or advice.head_digest != intent["head_digest"]
            or advice.head_generation != intent["head_generation"]
            or advice.package_digest != intent["package_digest"]
            or advice.package_ref != "skill-content-v2:" + intent["candidate_bundle_digest"][7:]
            or advice.memory_snapshot_digest != intent["snapshot_digest"]
            or advice.recall_manifest_digest != intent["manifest_digest"]
            or request.model_id != "gemini-3.8-flash"
            or request.domain_id != "finance"
            or request.tenant_id != self.head.tenant_id
            or request.workspace_id != self.store.workspace_id
            or candidate.domain_id != "finance"
            or set(candidate.object_ids) != set(request.object_ids)
            or sorted(candidate.source_refs) != business_refs
            or sha256_digest(business_refs) != gold["expected_source_refs_digest"]
        ):
            raise IntegrityError("FINANCE_QUALIFICATION_V4_BINDING_INVALID")

    def _verify_pairs(
        self, *, suite_ref: str, parent: FinanceSkillResolution,
        candidate: SkillContentBundleV2, pairs: tuple[FinancePairJudgment, ...],
        author_actor_id: str, candidate_proposal_sequence: int,
        for_release: bool = False,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        suite = self.store.load_artifact(suite_ref, SUITE_MEDIA).payload
        suite_digest = sha256_digest(suite)
        if (
            suite.get("schema_version") != _SUITE_SCHEMA
            or suite_ref != "finance-sealed-suite:" + suite_digest[7:]
            or suite.get("scope_digest") != self.head.scope_digest
            or suite.get("parent_head_ref") != parent.head_ref
            or suite.get("parent_head_digest") != parent.head_digest
            or suite.get("parent_generation") != parent.generation
            or suite.get("parent_package_digest") != parent.package_digest
            or suite.get("evaluator_actor_id") != self.evaluator_actor_id
            or not isinstance(suite.get("suite_id"), str)
            or not suite["suite_id"]
            or len(suite.get("case_clusters", {})) < 3
            or not isinstance(suite.get("case_gold"), dict)
            or set(suite["case_gold"]) != set(suite["case_clusters"])
            or suite.get("min_gain", 0) < 1
            or suite.get("rubric_digest") != REVIEWED_FINANCE_RUBRIC_DIGEST
            or suite.get("max_regressions") != 0
            or len(pairs) != len(suite.get("case_clusters", {}))
        ):
            raise IntegrityError("FINANCE_QUALIFICATION_SUITE_INVALID")
        seen: set[str] = set()
        rows: list[dict[str, Any]] = []
        family_id: str | None = None
        total_attempts = 0
        total_reserved = 0
        used_result_refs: set[str] = set()
        used_intent_refs: set[str] = set()
        for pair in pairs:
            if (
                pair.case_ref in seen
                or suite["case_clusters"].get(pair.case_ref) != pair.independence_cluster_id
                or not pair.hard_safety_passed
            ):
                raise IntegrityError("FINANCE_QUALIFICATION_PAIR_INVALID")
            seen.add(pair.case_ref)
            baseline_intent, baseline = self._read_pair_result(
                pair.baseline_result_ref, expected_bundle=parent.bundle,
                parent=parent, suite_ref=suite_ref, suite_digest=suite_digest,
            )
            candidate_intent, current = self._read_pair_result(
                pair.candidate_result_ref, expected_bundle=candidate,
                parent=parent, suite_ref=suite_ref, suite_digest=suite_digest,
            )
            if (
                baseline_intent.get("event_id") != pair.case_ref
                or baseline_intent.get("event_id") != candidate_intent.get("event_id")
                or baseline_intent.get("case_revision_digest") != pair.case_revision_digest
                or candidate_intent.get("case_revision_digest") != pair.case_revision_digest
                or baseline_intent.get("snapshot_digest") != candidate_intent.get("snapshot_digest")
                or baseline_intent.get("manifest_digest") != candidate_intent.get("manifest_digest")
                or baseline_intent.get("experiment_family_id")
                != candidate_intent.get("experiment_family_id")
                or baseline_intent.get("evaluation_arm") == candidate_intent.get("evaluation_arm")
            ):
                raise IntegrityError("FINANCE_QUALIFICATION_PAIR_UNPAIRED")
            gold = self._read_current_gold(suite, pair.case_ref)
            if pair.case_revision_digest != gold["case_revision_digest"]:
                raise IntegrityError("FINANCE_QUALIFICATION_GOLD_REVISION_MISMATCH")
            if family_id is None:
                family_id = baseline_intent["experiment_family_id"]
            elif family_id != baseline_intent["experiment_family_id"]:
                raise IntegrityError("FINANCE_QUALIFICATION_FAMILY_MISMATCH")
            self._verify_current_private_result(baseline_intent, baseline, gold)
            self._verify_current_private_result(candidate_intent, current, gold)
            experiment = self._experiment_service()
            verify_lineage = (
                experiment.verify_sealed_pair_lineage_for_release
                if for_release else experiment.verify_sealed_pair_lineage
            )
            verify_lineage(
                family_ref=experiment.family_ref(suite["experiment_family_id"]),
                suite_ref=suite_ref,
                case_ref=pair.case_ref,
                case_revision_digest=pair.case_revision_digest,
                cluster_id=pair.independence_cluster_id,
                baseline_result_ref=pair.baseline_result_ref,
                candidate_result_ref=pair.candidate_result_ref,
                parent_bundle_digest=parent.bundle.digest,
                candidate_bundle_digest=candidate.digest,
            )
            if (
                not pair.baseline_assessment_ref
                or not pair.candidate_assessment_ref
                or pair.baseline_assessment_ref == pair.candidate_assessment_ref
            ):
                raise IntegrityError("FINANCE_INDEPENDENT_QUALITY_RECEIPT_REQUIRED")
            oracle = self._quality_oracle(author_actor_id)
            verify = (
                oracle.verify_assessment_for_release
                if for_release else oracle.verify_assessment_receipt
            )
            common = {
                "expected_gold_ref": suite["case_gold"][pair.case_ref]["record_ref"],
                "expected_rubric_digest": suite["rubric_digest"],
                "expected_case_ref": pair.case_ref,
                "expected_cluster_id": pair.independence_cluster_id,
                "require_sealed_pair": True,
            }
            baseline_quality = verify(
                pair.baseline_assessment_ref,
                expected_result_ref=pair.baseline_result_ref,
                expected_grounded_score=pair.grounded_score_before,
                expected_next_step_score=pair.next_step_score_before,
                require_hard_pass=False, require_concept_pass=False, **common,
            )
            candidate_quality = verify(
                pair.candidate_assessment_ref,
                expected_result_ref=pair.candidate_result_ref,
                expected_grounded_score=pair.grounded_score_after,
                expected_next_step_score=pair.next_step_score_after,
                require_hard_pass=True, require_concept_pass=True, **common,
            )
            used_result_refs.update((pair.baseline_result_ref, pair.candidate_result_ref))
            used_intent_refs.update((baseline["intent_ref"], current["intent_ref"]))
            total_attempts += (
                baseline["physical_attempts_observed"] + current["physical_attempts_observed"]
            )
            total_reserved += (
                baseline_intent["reserved_microusd"] + candidate_intent["reserved_microusd"]
            )
            rows.append({
                **pair.as_dict(),
                "baseline_result_digest": sha256_digest(baseline),
                "candidate_result_digest": sha256_digest(current),
                "experiment_family_id": baseline_intent["experiment_family_id"],
                "memory_snapshot_digest": baseline_intent["snapshot_digest"],
                "recall_manifest_digest": baseline_intent["manifest_digest"],
                "baseline_assessment_digest": sha256_digest(baseline_quality),
                "candidate_assessment_digest": sha256_digest(candidate_quality),
            })
        if set(seen) != set(suite["case_clusters"]):
            raise IntegrityError("FINANCE_QUALIFICATION_CASE_SET_INVALID")
        if (
            family_id != suite.get("experiment_family_id")
            or total_attempts > suite["max_physical_attempts"]
            or total_reserved > suite["max_reserved_microusd"]
        ):
            raise IntegrityError("FINANCE_QUALIFICATION_FAMILY_BUDGET_INVALID")
        family_intents = [
            row for row in self.store.list_artifacts(
                artifact_id_prefix="finance-evaluation-intent:",
                expected_media_type=EVALUATION_INTENT_MEDIA,
            )
            if row.payload.get("experiment_family_id") == family_id
        ]
        if len(family_intents) != 2 * len(pairs):
            raise IntegrityError("FINANCE_QUALIFICATION_FAMILY_INCOMPLETE")
        expected_result_refs = {
            "finance-evaluation-result:" + row.artifact_id.removeprefix("finance-evaluation-intent:")
            for row in family_intents
        }
        if expected_result_refs != used_result_refs:
            raise IntegrityError("FINANCE_QUALIFICATION_FAMILY_INCOMPLETE")
        self._require_suite_precedes_intents(
            suite_ref, used_intent_refs,
            candidate_proposal_sequence=candidate_proposal_sequence,
        )
        gains = [pair.gain for pair in pairs]
        if sum(gains) < suite["min_gain"] or sum(gain < 0 for gain in gains) > suite["max_regressions"]:
            raise IntegrityError("FINANCE_QUALIFICATION_NO_IMPROVEMENT")
        return suite, rows

    def issue_qualification(
        self, *, suite_ref: str, candidate: SkillContentBundleV2,
        pairs: tuple[FinancePairJudgment, ...], author_actor_id: str,
        candidate_proposal_ref: str | None = None,
    ) -> str:
        evaluator_id = self._evaluator()
        candidate = SkillContentBundleV2.from_payload(candidate.payload, policy=self.head.policy)
        if evaluator_id == author_actor_id:
            raise AuthorizationError("FINANCE_QUALIFICATION_ROLE_COLLISION")
        parent = self.head.resolve()
        if (
            candidate.payload["parent_head_ref"] != parent.head_ref
            or candidate.payload["parent_head_digest"] != parent.head_digest
            or candidate.payload["parent_package_digest"] != parent.package_digest
            or candidate.reference_bytes != parent.bundle.reference_bytes
            or candidate.instruction_bytes == parent.bundle.instruction_bytes
        ):
            raise IntegrityError("FINANCE_QUALIFICATION_CANDIDATE_INVALID")
        candidate_proof = self._candidate_proof(
            candidate_ref=candidate_proposal_ref, candidate=candidate,
            author_actor_id=author_actor_id,
        )
        if candidate_proof.get("experiment_input_scope") == "CONTROLLED_DEVELOPMENT_INPUT_ONLY":
            raise IntegrityError("FINANCE_V2_REAL_SEALED_INPUT_NOT_QUALIFIED")
        suite, rows = self._verify_pairs(
            suite_ref=suite_ref, parent=parent, candidate=candidate, pairs=pairs,
            author_actor_id=author_actor_id,
            candidate_proposal_sequence=candidate_proof["candidate_proposal_sequence"],
        )
        body = {
            "schema_version": _QUALIFICATION_SCHEMA,
            "status": "QUALIFIED",
            "evidence_scope": "SEALED_PAIRED_V4_WITH_INDEPENDENT_REVIEW",
            "scope_digest": self.head.scope_digest,
            "parent_head_ref": parent.head_ref,
            "parent_head_digest": parent.head_digest,
            "parent_generation": parent.generation,
            "parent_package_digest": parent.package_digest,
            "candidate_bundle_digest": candidate.digest,
            "candidate_package_digest": candidate.package_digest,
            "candidate_proposal_ref": candidate_proposal_ref,
            "candidate_proposal_digest": candidate_proof["candidate_record_digest"],
            "suite_ref": suite_ref,
            "suite_digest": sha256_digest(suite),
            "rubric_digest": suite["rubric_digest"],
            "pairs": rows,
            "total_gain": sum(item.gain for item in pairs),
            "regression_count": 0,
            "hard_safety_failures": 0,
            "evaluator_actor_id": evaluator_id,
            "author_actor_id": author_actor_id,
            "target_writes": 0,
        }
        ref = "finance-skill-qualification:" + sha256_digest(body)[7:]
        with self.store.transaction() as connection:
            self._evaluator()
            self._lock_workspace_qualification_scope(connection)
            self.store.require_before_commit(connection, self._evaluator)
            if self.head.resolve() != parent:
                raise IntegrityError("FINANCE_SKILL_STALE_BASE")
            self._candidate_proof(
                candidate_ref=candidate_proposal_ref, candidate=candidate,
                author_actor_id=author_actor_id,
            )
            self._verify_pairs(
                suite_ref=suite_ref, parent=parent, candidate=candidate, pairs=pairs,
                author_actor_id=author_actor_id,
                candidate_proposal_sequence=candidate_proof["candidate_proposal_sequence"],
            )
            self.store.save_artifact(connection, ref, QUALIFICATION_MEDIA, body)
            self.store.append_event(connection, "FINANCE_SKILL_QUALIFICATION_RECORDED", {
                "qualification_ref": ref, "qualification_digest": sha256_digest(body),
                "parent_head_ref": parent.head_ref,
                "candidate_bundle_digest": candidate.digest,
                "case_count": len(pairs), "evaluator_actor_id": evaluator_id,
                "target_writes": 0,
            })
        return ref

    def review_content(
        self, *, candidate: SkillContentBundleV2, qualification_ref: str,
        author_actor_id: str, reason_code: str,
    ) -> str:
        governor = self.head._governor()
        candidate = SkillContentBundleV2.from_payload(candidate.payload, policy=self.head.policy)
        if not reason_code or governor.actor_id in {author_actor_id, self.evaluator_actor_id}:
            raise AuthorizationError("FINANCE_CONTENT_REVIEW_ROLE_COLLISION")
        current = self.head.resolve()
        qualification = self.store.load_artifact(qualification_ref, QUALIFICATION_MEDIA).payload
        candidate_proof = self._candidate_proof(
            candidate_ref=qualification.get("candidate_proposal_ref"),
            candidate=candidate, author_actor_id=author_actor_id,
        )
        if candidate_proof.get("experiment_input_scope") == "CONTROLLED_DEVELOPMENT_INPUT_ONLY":
            raise IntegrityError("FINANCE_V2_REAL_SEALED_INPUT_NOT_QUALIFIED")
        if (
            qualification.get("schema_version") != _QUALIFICATION_SCHEMA
            or qualification.get("status") != "QUALIFIED"
            or qualification.get("candidate_bundle_digest") != candidate.digest
            or qualification.get("candidate_package_digest") != candidate.package_digest
            or qualification.get("parent_head_ref") != current.head_ref
            or qualification.get("parent_head_digest") != current.head_digest
            or qualification.get("parent_generation") != current.generation
            or qualification.get("parent_package_digest") != current.package_digest
            or qualification.get("evaluator_actor_id") != self.evaluator_actor_id
            or qualification.get("author_actor_id") != author_actor_id
            or qualification.get("candidate_proposal_digest")
            != candidate_proof["candidate_record_digest"]
        ):
            raise IntegrityError("FINANCE_CONTENT_REVIEW_QUALIFICATION_INVALID")
        pairs = tuple(FinancePairJudgment(**{
            key: row.get(key) for key in FinancePairJudgment.__dataclass_fields__
        }) for row in qualification["pairs"])
        suite, verified_rows = self._verify_pairs(
            suite_ref=qualification["suite_ref"], parent=current,
            candidate=candidate, pairs=pairs,
            author_actor_id=author_actor_id,
            candidate_proposal_sequence=candidate_proof["candidate_proposal_sequence"],
            for_release=True,
        )
        if (
            qualification.get("suite_digest") != sha256_digest(suite)
            or qualification.get("pairs") != verified_rows
            or qualification.get("total_gain") != sum(item.gain for item in pairs)
        ):
            raise IntegrityError("FINANCE_CONTENT_REVIEW_QUALIFICATION_DRIFT")
        body = {
            "schema_version": _RELEASE_SCHEMA,
            "scope_digest": self.head.scope_digest,
            "parent_head_ref": current.head_ref,
            "parent_head_digest": current.head_digest,
            "parent_generation": current.generation,
            "parent_package_digest": current.package_digest,
            "candidate_bundle_digest": candidate.digest,
            "candidate_package_digest": candidate.package_digest,
            "candidate_proposal_ref": qualification["candidate_proposal_ref"],
            "candidate_proposal_digest": candidate_proof["candidate_record_digest"],
            "qualification_ref": qualification_ref,
            "qualification_digest": sha256_digest(qualification),
            "reviewer_actor_id": governor.actor_id,
            "author_actor_id": author_actor_id,
            "reason_code": reason_code,
            "allowed_purposes": ["skill_evaluation", "finance_advisory_candidate"],
            "allowed_recipients": ["current_workspace_finance_advisory"],
            "adoption_enabled": False,
        }
        ref = "finance-content-release:" + sha256_digest(body)[7:]
        with self.store.transaction() as connection:
            self.head._governor()
            self._lock_workspace_qualification_scope(connection)
            self.store.require_before_commit(connection, self.head._governor)
            if self.head.resolve() != current:
                raise IntegrityError("FINANCE_SKILL_STALE_BASE")
            self._candidate_proof(
                candidate_ref=qualification["candidate_proposal_ref"], candidate=candidate,
                author_actor_id=author_actor_id,
            )
            self._verify_pairs(
                suite_ref=qualification["suite_ref"], parent=current,
                candidate=candidate, pairs=pairs,
                author_actor_id=author_actor_id,
                candidate_proposal_sequence=candidate_proof["candidate_proposal_sequence"],
                for_release=True,
            )
            self.store.save_artifact(
                connection, self.head._bundle_ref(candidate), CONTENT_MEDIA, candidate.payload
            )
            self.store.save_artifact(connection, ref, CONTENT_RELEASE_MEDIA, body)
            self.store.append_event(connection, "FINANCE_CONTENT_RELEASE_REVIEWED", {
                "release_ref": ref, "release_digest": sha256_digest(body),
                "candidate_bundle_digest": candidate.digest,
                "qualification_ref": qualification_ref,
                "reviewer_actor_id": governor.actor_id,
                "adoption_enabled": False,
            })
        return ref

    def promote(
        self, *, candidate: SkillContentBundleV2,
        qualification_ref: str, content_release_ref: str,
        expected_head_ref: str, expected_head_digest: str,
        expected_generation: int, expected_package_digest: str,
    ) -> FinanceSkillResolution:
        governor = self.head._governor()
        candidate = SkillContentBundleV2.from_payload(candidate.payload, policy=self.head.policy)
        current = self.head.resolve()
        if (
            current.head_ref != expected_head_ref
            or current.head_digest != expected_head_digest
            or current.generation != expected_generation
            or current.package_digest != expected_package_digest
        ):
            raise IntegrityError("FINANCE_SKILL_STALE_BASE")
        qualification = self.store.load_artifact(qualification_ref, QUALIFICATION_MEDIA).payload
        release = self.store.load_artifact(content_release_ref, CONTENT_RELEASE_MEDIA).payload
        candidate_proof = self._candidate_proof(
            candidate_ref=qualification.get("candidate_proposal_ref"),
            candidate=candidate, author_actor_id=qualification.get("author_actor_id", ""),
        )
        if candidate_proof.get("experiment_input_scope") == "CONTROLLED_DEVELOPMENT_INPUT_ONLY":
            raise IntegrityError("FINANCE_V2_REAL_SEALED_INPUT_NOT_QUALIFIED")
        if (
            qualification.get("schema_version") != _QUALIFICATION_SCHEMA
            or qualification.get("status") != "QUALIFIED"
            or qualification.get("evaluator_actor_id") != self.evaluator_actor_id
            or qualification.get("parent_head_ref") != current.head_ref
            or qualification.get("parent_head_digest") != current.head_digest
            or qualification.get("parent_generation") != current.generation
            or qualification.get("candidate_bundle_digest") != candidate.digest
            or qualification.get("candidate_package_digest") != candidate.package_digest
            or qualification.get("parent_package_digest") != current.package_digest
            or qualification.get("candidate_proposal_digest")
            != candidate_proof["candidate_record_digest"]
            or release.get("schema_version") != _RELEASE_SCHEMA
            or release.get("reviewer_actor_id") != governor.actor_id
            or release.get("qualification_ref") != qualification_ref
            or release.get("qualification_digest") != sha256_digest(qualification)
            or release.get("candidate_bundle_digest") != candidate.digest
            or release.get("candidate_package_digest") != candidate.package_digest
            or release.get("candidate_proposal_ref") != qualification["candidate_proposal_ref"]
            or release.get("candidate_proposal_digest")
            != candidate_proof["candidate_record_digest"]
            or release.get("parent_head_ref") != current.head_ref
            or release.get("parent_head_digest") != current.head_digest
            or release.get("parent_generation") != current.generation
            or release.get("parent_package_digest") != current.package_digest
            or release.get("adoption_enabled") is not False
            or governor.actor_id in {
                self.evaluator_actor_id, qualification.get("author_actor_id")
            }
        ):
            raise IntegrityError("FINANCE_SKILL_PROMOTION_EVIDENCE_INVALID")
        pairs = tuple(FinancePairJudgment(**{
            key: row.get(key) for key in FinancePairJudgment.__dataclass_fields__
        }) for row in qualification["pairs"])
        suite, verified_rows = self._verify_pairs(
            suite_ref=qualification["suite_ref"], parent=current,
            candidate=candidate, pairs=pairs,
            author_actor_id=qualification["author_actor_id"],
            candidate_proposal_sequence=candidate_proof["candidate_proposal_sequence"],
            for_release=True,
        )
        if (
            qualification.get("suite_digest") != sha256_digest(suite)
            or qualification.get("pairs") != verified_rows
            or qualification.get("total_gain") != sum(item.gain for item in pairs)
            or qualification.get("regression_count") != 0
            or qualification.get("hard_safety_failures") != 0
        ):
            raise IntegrityError("FINANCE_SKILL_QUALIFICATION_DRIFT")
        previous = self.head._current_source()
        promoted = self.head._head_object(
            generation=current.generation + 1, transition_kind="PROMOTE",
            bundle=candidate, previous=previous, actor_id=governor.actor_id,
            qualification_status="QUALIFIED", qualification_ref=qualification_ref,
            content_release_ref=content_release_ref,
        )
        with self.store.transaction() as connection:
            self.head._governor()
            self._lock_workspace_qualification_scope(connection)
            self.store.require_before_commit(connection, self.head._governor)
            if self.head.resolve() != current:
                raise IntegrityError("FINANCE_SKILL_STALE_BASE")
            self._candidate_proof(
                candidate_ref=qualification["candidate_proposal_ref"], candidate=candidate,
                author_actor_id=qualification["author_actor_id"],
            )
            self._verify_pairs(
                suite_ref=qualification["suite_ref"], parent=current,
                candidate=candidate, pairs=pairs,
                author_actor_id=qualification["author_actor_id"],
                candidate_proposal_sequence=candidate_proof["candidate_proposal_sequence"],
                for_release=True,
            )
            stored = self.store.load_artifact(self.head._bundle_ref(candidate), CONTENT_MEDIA)
            if stored.payload != candidate.payload:
                raise IntegrityError("FINANCE_SKILL_CONTENT_RELEASE_DRIFT")
            self.store.insert_version(connection, promoted, make_current=False)
            self.store.promote_version(
                connection, self.head.head_id, previous.version, promoted.version
            )
            self.store.append_event(connection, "FINANCE_SKILL_HEAD_PROMOTED", {
                "head_ref": promoted.ref, "head_digest": promoted.digest,
                "previous_head_ref": previous.ref,
                "generation": current.generation + 1,
                "package_digest": candidate.package_digest,
                "qualification_ref": qualification_ref,
                "content_release_ref": content_release_ref,
                "adoption_enabled": False,
            })
        return self.head.resolve()


__all__ = [
    "CONTENT_RELEASE_MEDIA",
    "QUALIFICATION_MEDIA",
    "SUITE_MEDIA",
    "FinancePairJudgment",
    "FinanceSkillQualificationService",
    "verify_current_release_for_adoption",
]


def verify_current_release_for_adoption(
    workspace: Any, *, expected_head_ref: str, expected_head_digest: str,
    expected_package_digest: str, qualification_ref: str,
    qualification_digest: str, content_release_ref: str,
) -> None:
    """Fail closed until real sealed-family lineage can be rechecked for use.

    This is the only deployment hook the normal Finance selector may call.
    Mechanical QUALIFIED fixtures and isolated protocol receipts cannot grant
    adoption.  The final family/trial/first-exposure verifier belongs here.
    """

    head = FinanceSkillHeadService(
        workspace.store, tenant_id=workspace.profile.organization_id,
    )
    current = head.resolve()
    source = head._current_source()
    if (
        current.head_ref != expected_head_ref
        or current.head_digest != expected_head_digest
        or current.package_digest != expected_package_digest
        or current.qualification_status != "QUALIFIED"
        or source.payload.get("qualification_ref") != qualification_ref
        or source.payload.get("content_release_ref") != content_release_ref
    ):
        raise IntegrityError("FINANCE_ADOPTION_CURRENT_RELEASE_INVALID")
    try:
        qualification = workspace.store.load_artifact(
            qualification_ref, QUALIFICATION_MEDIA,
        ).payload
        release = workspace.store.load_artifact(
            content_release_ref, CONTENT_RELEASE_MEDIA,
        ).payload
    except KeyError as exc:
        raise IntegrityError("FINANCE_ADOPTION_RELEASE_EVIDENCE_MISSING") from exc
    if (
        sha256_digest(qualification) != qualification_digest
        or qualification.get("schema_version") != _QUALIFICATION_SCHEMA
        or qualification.get("status") != "QUALIFIED"
        or qualification.get("rubric_digest") != REVIEWED_FINANCE_RUBRIC_DIGEST
        or qualification.get("candidate_bundle_digest") != current.bundle.digest
        or qualification.get("candidate_package_digest") != current.package_digest
        or release.get("schema_version") != _RELEASE_SCHEMA
        or release.get("qualification_ref") != qualification_ref
        or release.get("qualification_digest") != qualification_digest
        or release.get("candidate_bundle_digest") != current.bundle.digest
        or release.get("candidate_package_digest") != current.package_digest
        or release.get("adoption_enabled") is not False
    ):
        raise IntegrityError("FINANCE_ADOPTION_RELEASE_EVIDENCE_INVALID")
    raise IntegrityError("FINANCE_ADOPTION_SEALED_FAMILY_VERIFICATION_NOT_READY")
