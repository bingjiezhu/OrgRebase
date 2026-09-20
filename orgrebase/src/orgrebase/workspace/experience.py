"""Human-governed extraction of one Golden-run recovery into a Skill candidate.

This is intentionally a thin post-run governance slice, not a general
``Experience Engine``.  It recognizes one evidence-backed pattern, creates an
immutable candidate plus an eight-partition evaluation, waits for an explicit
Skill Steward decision, and only then delegates release authority to the
existing :class:`SkillReleaseLedger`.

The business run never consumes this candidate.  An approved candidate is
proved callable only by a controlled-local RELEASE dry-call bound to the same
run; production generalization remains unclaimed.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from orgrebase.auth import current_authorization
from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.store import StateStore
from orgrebase.workspace.skill_packages import (
    PARTITIONS,
    SKILL_REGISTRY_AUTHORITY,
    InvocationContext,
    SkillCandidateOverlayRegistry,
    SkillEvaluationCase,
    SkillPackageEvaluator,
    SkillPackageRegistry,
    SkillReleaseLedger,
)

EXPERIENCE_CANDIDATE_MEDIA_TYPE = (
    "application/vnd.orgrebase.experience-candidate+json"
)
EXPERIENCE_EVALUATION_MEDIA_TYPE = (
    "application/vnd.orgrebase.experience-evaluation+json"
)
EXPERIENCE_REVIEW_GATE_MEDIA_TYPE = (
    "application/vnd.orgrebase.experience-review-gate+json"
)
EXPERIENCE_DECISION_MEDIA_TYPE = (
    "application/vnd.orgrebase.experience-decision+json"
)
EXPERIENCE_RELEASE_MEDIA_TYPE = (
    "application/vnd.orgrebase.experience-skill-release+json"
)

EXPERIENCE_TARGET_SKILL = "structured-domain-handoff"
EXPERIENCE_STEWARD_ID = "human:skill-steward"
EXPERIENCE_MIN_REVIEW_DURATION_MS = 4_000
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _record(body: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(body))
    payload["digest"] = sha256_digest(body)
    return payload


def _verify_record(payload: Mapping[str, Any], *, error: str) -> None:
    digest = payload.get("digest")
    body = {key: value for key, value in payload.items() if key != "digest"}
    if not isinstance(digest, str) or digest != sha256_digest(body):
        raise IntegrityError(error)


def _valid_digest(value: Any) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _iso_from_epoch_ms(epoch_ms: int) -> str:
    return (
        datetime.fromtimestamp(epoch_ms / 1000, tz=UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _run_token(run_id: str) -> str:
    return sha256_digest({"run_id": run_id}).removeprefix("sha256:")[:20]


class ExperienceReviewGatePending(RuntimeError):
    """Stable conflict returned when the four-second server gate is pending."""

    code = "EXPERIENCE_REVIEW_GATE_NOT_READY"

    def __init__(self, *, remaining_ms: int, not_before: str) -> None:
        self.remaining_ms = remaining_ms
        self.not_before = not_before
        super().__init__(
            f"{self.code}:remaining_ms={remaining_ms},not_before={not_before}"
        )


class GovernedExperienceService:
    """Persisted candidate/evaluation/decision façade over the existing ledger."""

    def __init__(
        self,
        store: StateStore,
        *,
        wall_clock: Callable[[], float] | None = None,
        registry: SkillPackageRegistry | None = None,
    ) -> None:
        self.store = store
        self._wall_clock = wall_clock or time.time
        self.registry = registry or SkillPackageRegistry()

    @staticmethod
    def _artifact_ids(run_id: str) -> dict[str, str]:
        token = _run_token(run_id)
        return {
            "candidate": f"experience-candidate:{token}@v1",
            "evaluation": f"experience-evaluation:{token}@v1",
            "gate": f"experience-review-gate:{token}@v1",
            "decision": f"experience-decision:{token}@v1",
            "release": f"experience-release:{token}@v1",
        }

    def _load(
        self, artifact_id: str, media_type: str
    ) -> dict[str, Any] | None:
        try:
            stored = self.store.load_artifact(artifact_id, media_type)
        except KeyError:
            return None
        return {
            "artifact_id": artifact_id,
            "artifact_digest": stored.payload_digest,
            "payload": stored.payload,
        }

    @staticmethod
    def _find_run(
        runs: Sequence[Mapping[str, Any]], *, domain: str, attempt: int
    ) -> Mapping[str, Any] | None:
        suffix = f"-{domain}-a{attempt}"
        for run in runs:
            task_id = str(run.get("task_id", ""))
            if run.get("agent_name") == f"{domain}-steward" and task_id.endswith(
                suffix
            ):
                return run
        return None

    @staticmethod
    def _find_reviewer(
        runs: Sequence[Mapping[str, Any]], *, attempt: int
    ) -> Mapping[str, Any] | None:
        semantic_reviewers = [run for run in runs if run.get("role") == "REVIEWER"]
        if semantic_reviewers:
            matches = [
                run
                for run in semantic_reviewers
                if run.get("attempt") == attempt
            ]
            return matches[0] if len(matches) == 1 else None

        # Compatibility is deliberately restricted to historical frozen packs
        # that predate role/attempt projection on agent_runs.  A suffix is not
        # authority: it is accepted only when it identifies exactly one run.
        suffix = f"-reviewer-a{attempt}"
        matches = [
            run
            for run in runs
            if str(run.get("task_id", "")).endswith(suffix)
        ]
        return matches[0] if len(matches) == 1 else None

    def _eligible_pattern(
        self, evidence: Mapping[str, Any]
    ) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
        collaboration = evidence.get("agent_collaboration")
        if not isinstance(collaboration, Mapping):
            return None, ("COLLABORATION_EVIDENCE_MISSING",)
        runs = collaboration.get("agent_runs")
        reviewer = collaboration.get("reviewer")
        tool = collaboration.get("tool")
        if not isinstance(runs, list) or not isinstance(reviewer, Mapping) or not isinstance(
            tool, Mapping
        ):
            return None, ("RECOVERY_CHAIN_EVIDENCE_MISSING",)
        finance_a1 = self._find_run(runs, domain="finance", attempt=1)
        finance_a2 = self._find_run(runs, domain="finance", attempt=2)
        reviewer_a1 = self._find_reviewer(runs, attempt=1)
        reviewer_a2 = self._find_reviewer(runs, attempt=2)
        attempt_1 = reviewer.get("attempt_1")
        attempt_2 = reviewer.get("attempt_2")
        reason_codes = (
            attempt_1.get("reason_codes", ())
            if isinstance(attempt_1, Mapping)
            else ()
        )
        checks = {
            "golden_pass": evidence.get("status") == "PASS",
            "terminal": evidence.get("project_terminal_state") == "completed",
            "candidate_only": evidence.get("canonical_target_writes_before_control_commit")
            == 0,
            "finance_a1_abstain": isinstance(finance_a1, Mapping)
            and finance_a1.get("status") == "ABSTAIN",
            "reviewer_a1_replan": isinstance(attempt_1, Mapping)
            and attempt_1.get("verdict") == "REPLAN",
            "price_band_missing": any("price_band" in str(code) for code in reason_codes),
            "http_tool_pass": tool.get("status") == "SUCCEEDED"
            and tool.get("operation") == "READ_DEPENDENCY_EVIDENCE"
            and tool.get("target_writes") == 0,
            "finance_a2_pass": isinstance(finance_a2, Mapping)
            and finance_a2.get("status") == "TRUSTED_COMPLETE",
            "reviewer_a2_pass": isinstance(attempt_2, Mapping)
            and attempt_2.get("verdict") == "PASS",
        }
        failures = tuple(key for key, passed in checks.items() if not passed)
        if failures:
            return None, tuple(f"ELIGIBILITY_FAILED:{item}" for item in failures)
        required = {
            "finance_a1_output_digest": finance_a1.get("output_digest"),
            "reviewer_a1_output_digest": reviewer_a1.get("output_digest")
            if isinstance(reviewer_a1, Mapping)
            else None,
            "tool_receipt_digest": tool.get("receipt_digest"),
            "finance_a2_output_digest": finance_a2.get("output_digest"),
            "reviewer_a2_output_digest": reviewer_a2.get("output_digest")
            if isinstance(reviewer_a2, Mapping)
            else None,
        }
        if not all(_valid_digest(value) for value in required.values()):
            return None, ("ELIGIBILITY_FAILED:SOURCE_DIGEST_MISSING",)
        return dict(required), ()

    def _build_candidate(
        self,
        evidence: Mapping[str, Any],
        *,
        created_at: str,
    ) -> dict[str, Any]:
        run_id = str(evidence.get("run_id", ""))
        source, reasons = self._eligible_pattern(evidence)
        base = self.registry.load(EXPERIENCE_TARGET_SKILL)
        if source is None:
            return _record(
                {
                    "schema_version": "orgrebase.experience-candidate.v1",
                    "id": f"experience-candidate:{_run_token(run_id)}@v1",
                    "outcome": "NO_CANDIDATE",
                    "source_run_id": run_id,
                    "source_summary_digest": evidence.get("summary_digest"),
                    "reason_codes": list(reasons),
                    "candidate_only": True,
                    "target_writes": 0,
                    "created_at": created_at,
                }
            )
        body = {
            "schema_version": "orgrebase.experience-candidate.v1",
            "id": f"experience-candidate:{_run_token(run_id)}@v1",
            "outcome": "IMPROVE",
            "source_run_id": run_id,
            "target_skill_name": EXPERIENCE_TARGET_SKILL,
            "base_package_digest": base.package_digest,
            "proposed_version": f"1.2.0-experience.{_run_token(run_id)[:8]}",
            "maturity": "SINGLE_RUN_SEED",
            "problem_pattern": "FINANCE_REQUIRED_SLOT_MISSING:price_band",
            "trigger": {
                "domain": "finance",
                "required_slot": "price_band",
                "attempt_1_status": "ABSTAIN",
                "reviewer_verdict": "REPLAN",
            },
            "preconditions": [
                "same_run_exact_task_and_handoff_bindings",
                "read_only_dependency_evidence_tool_available",
                "finance_retry_preserves_candidate_only_effect_ceiling",
            ],
            "steps": [
                "ABSTAIN when required price_band provenance is absent",
                "Reviewer emits REPLAN with exact missing-slot reason",
                "invoke READ_DEPENDENCY_EVIDENCE with zero target writes",
                "retry only Finance handoff and require Reviewer PASS",
            ],
            "stop_conditions": [
                "tool receipt missing or stale",
                "permission or recipient expands",
                "reviewer does not PASS exact recovered evidence",
            ],
            "required_evidence": source,
            "permissions": {
                "effect_ceiling": "CANDIDATE_ONLY",
                "allowed_tool": "dependency-evidence:READ_ONLY",
                "target_writes": 0,
                "human_approval_required": True,
            },
            "source": {
                "run_id": run_id,
                "correlation_id": evidence.get("correlation_id"),
                "summary_digest": evidence.get("summary_digest"),
                "competition_evidence_digest": evidence.get("digest"),
            },
            "limitations": [
                "single controlled-local Golden run only",
                "evaluation-only overlay; package resources unchanged",
                "not used by the current Quote run",
                "not production generalization evidence",
            ],
            "proposed_evaluation_partitions": list(PARTITIONS),
            "normalizer": "DETERMINISTIC_PROVENANCE_AND_PERMISSION_GATE",
            "model_assistance": "NOT_RUN_BY_DESIGN_FOR_THIN_MVP",
            "candidate_only": True,
            "target_writes": 0,
            "created_at": created_at,
        }
        return _record(body)

    @staticmethod
    def _handoff_input(run_id: str, **updates: Any) -> dict[str, Any]:
        value: dict[str, Any] = {
            "run_id": run_id,
            "task_id": "task:experience-handoff-evaluation",
            "delegation_id": "delegation:experience-handoff:finance",
            "delegation_task_digest": sha256_digest(
                {"run_id": run_id, "delegation": "finance"}
            ),
            "context_projection_digest": sha256_digest(
                {"run_id": run_id, "projection": "finance-recovery"}
            ),
            "candidate_bundle": {
                "claims": [{"ref": "claim:finance.price_band@recovered"}]
            },
        }
        value.update(updates)
        return value

    @classmethod
    def _evaluation_cases(cls, run_id: str) -> tuple[SkillEvaluationCase, ...]:
        valid = cls._handoff_input(run_id)
        values: dict[str, tuple[dict[str, Any], str]] = {
            "REPLAY": (valid, "HANDOFF"),
            "HELD_OUT": (
                cls._handoff_input(
                    run_id, candidate_bundle={"claims": [{"ref": "claim:finance.tax@v1"}]}
                ),
                "HANDOFF",
            ),
            "NEGATIVE_TRANSFER": (
                cls._handoff_input(
                    run_id, candidate_bundle={"claims": [], "applicable": False}
                ),
                "HANDOFF",
            ),
            "PERMISSION": (
                cls._handoff_input(run_id, permission_expansion=True),
                "DENY",
            ),
            "INJECTION": (
                cls._handoff_input(run_id, prompt_injection=True),
                "ABSTAIN",
            ),
            "MALFORMED": (
                {key: value for key, value in valid.items() if key != "candidate_bundle"},
                "ABSTAIN",
            ),
            "RESOURCE_OR_DEADLINE": (
                cls._handoff_input(run_id, deadline_expired=True),
                "ABSTAIN",
            ),
            "CANARY": (
                cls._handoff_input(run_id, candidate_bundle={"claims": []}),
                "HANDOFF",
            ),
        }
        return tuple(
            SkillEvaluationCase(
                case_id=f"experience-handoff:{partition.lower()}",
                partition=partition,
                public_input=public_input,
                expected_action=expected,
            )
            for partition, (public_input, expected) in values.items()
        )

    def _overlay(self, candidate: Mapping[str, Any]) -> SkillCandidateOverlayRegistry:
        return SkillCandidateOverlayRegistry(
            self.registry,
            candidate_ref=str(candidate["id"]),
            candidate_digest=str(candidate["digest"]),
            proposed_version=str(candidate["proposed_version"]),
            source_run_id=str(candidate["source_run_id"]),
        )

    def _evaluate(
        self, candidate: Mapping[str, Any], *, evaluated_at: str
    ) -> tuple[dict[str, Any], SkillPackageEvaluator, SkillCandidateOverlayRegistry]:
        overlay = self._overlay(candidate)
        package = overlay.load(EXPERIENCE_TARGET_SKILL)
        evaluator = SkillPackageEvaluator(overlay)
        evaluation = evaluator.evaluate(
            EXPERIENCE_TARGET_SKILL,
            self._evaluation_cases(str(candidate["source_run_id"])),
            evaluated_at=evaluated_at,
            premise_lock={
                "package": package.package_digest,
                "runtime": "restricted-skill-registry@1.0.0",
                "dependencies": sha256_digest(package.manifest["dependencies"]),
                "gold_boundary": "single-run-seed:evaluator-only",
                "candidate": str(candidate["digest"]),
                "source_run": str(candidate["source_run_id"]),
            },
        )
        return evaluation, evaluator, overlay

    def ensure_candidate(
        self, evidence: Mapping[str, Any]
    ) -> dict[str, Any]:
        run_id = str(evidence.get("run_id", "")).strip()
        if not run_id:
            raise IntegrityError("EXPERIENCE_SOURCE_RUN_ID_MISSING")
        ids = self._artifact_ids(run_id)
        existing = self._load(ids["candidate"], EXPERIENCE_CANDIDATE_MEDIA_TYPE)
        if existing is not None:
            return self.view(run_id)
        now_ms = math.floor(self._wall_clock() * 1000)
        created_at = _iso_from_epoch_ms(now_ms)
        candidate = self._build_candidate(evidence, created_at=created_at)
        _verify_record(candidate, error="EXPERIENCE_CANDIDATE_DIGEST_MISMATCH")
        with self.store.transaction() as connection:
            self.store.save_artifact(
                connection,
                ids["candidate"],
                EXPERIENCE_CANDIDATE_MEDIA_TYPE,
                candidate,
            )
            if candidate["outcome"] == "IMPROVE":
                evaluation, _, overlay = self._evaluate(
                    candidate, evaluated_at=created_at
                )
                package = overlay.load(EXPERIENCE_TARGET_SKILL)
                gate = _record(
                    {
                        "schema_version": "orgrebase.experience-review-gate.v1",
                        "id": ids["gate"],
                        "run_id": run_id,
                        "owner_id": EXPERIENCE_STEWARD_ID,
                        "candidate_digest": candidate["digest"],
                        "evaluation_digest": evaluation["digest"],
                        "evaluation_verdict": evaluation["verdict"],
                        "evaluation_partition_count": len(PARTITIONS),
                        "observed_skill_head_digest": candidate[
                            "base_package_digest"
                        ],
                        "proposed_package_digest": package.package_digest,
                        "review_duration_ms": EXPERIENCE_MIN_REVIEW_DURATION_MS,
                        "review_started_at_epoch_ms": now_ms,
                        "not_before_epoch_ms": (
                            now_ms + EXPERIENCE_MIN_REVIEW_DURATION_MS
                        ),
                        "not_before": _iso_from_epoch_ms(
                            now_ms + EXPERIENCE_MIN_REVIEW_DURATION_MS
                        ),
                        "candidate_only": True,
                        "target_writes": 0,
                    }
                )
                self.store.save_artifact(
                    connection,
                    ids["evaluation"],
                    EXPERIENCE_EVALUATION_MEDIA_TYPE,
                    evaluation,
                )
                self.store.save_artifact(
                    connection,
                    ids["gate"],
                    EXPERIENCE_REVIEW_GATE_MEDIA_TYPE,
                    gate,
                )
        return self.view(run_id)

    def _require_improve_records(
        self, run_id: str
    ) -> tuple[
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
        dict[str, str],
    ]:
        ids = self._artifact_ids(run_id)
        candidate_record = self._load(
            ids["candidate"], EXPERIENCE_CANDIDATE_MEDIA_TYPE
        )
        evaluation_record = self._load(
            ids["evaluation"], EXPERIENCE_EVALUATION_MEDIA_TYPE
        )
        gate_record = self._load(ids["gate"], EXPERIENCE_REVIEW_GATE_MEDIA_TYPE)
        if candidate_record is None:
            raise RuntimeError("EXPERIENCE_CANDIDATE_REQUIRED")
        candidate = candidate_record["payload"]
        if candidate.get("outcome") != "IMPROVE":
            raise RuntimeError("EXPERIENCE_NO_CANDIDATE_TO_REVIEW")
        if evaluation_record is None or gate_record is None:
            raise IntegrityError("EXPERIENCE_REVIEW_BINDING_INCOMPLETE")
        evaluation = evaluation_record["payload"]
        gate = gate_record["payload"]
        _verify_record(candidate, error="EXPERIENCE_CANDIDATE_DIGEST_MISMATCH")
        _verify_record(evaluation, error="EXPERIENCE_EVALUATION_DIGEST_MISMATCH")
        _verify_record(gate, error="EXPERIENCE_REVIEW_GATE_DIGEST_MISMATCH")
        if (
            candidate.get("source_run_id") != run_id
            or gate.get("run_id") != run_id
            or gate.get("candidate_digest") != candidate.get("digest")
            or gate.get("evaluation_digest") != evaluation.get("digest")
            or gate.get("evaluation_verdict") != "CANARY"
            or gate.get("evaluation_partition_count") != len(PARTITIONS)
        ):
            raise IntegrityError("EXPERIENCE_REVIEW_BINDING_INVALID")
        return candidate, evaluation, gate, ids

    def _rebuild_release(
        self,
        *,
        candidate: Mapping[str, Any],
        persisted_evaluation: Mapping[str, Any],
        approved_at: str,
    ) -> dict[str, Any]:
        evaluation, evaluator, overlay = self._evaluate(
            candidate,
            evaluated_at=str(persisted_evaluation["evaluated_at"]),
        )
        if evaluation != dict(persisted_evaluation):
            raise IntegrityError("EXPERIENCE_EVALUATION_REPLAY_MISMATCH")
        ledger = SkillReleaseLedger(overlay, evaluator)
        for state in ("EVALUATED", "SHADOW", "CANARY"):
            ledger.transition(
                EXPERIENCE_TARGET_SKILL,
                evaluation,
                to_state=state,
                actor_id=SKILL_REGISTRY_AUTHORITY,
                reason_codes=(
                    "HUMAN_APPROVED_EXPERIENCE_CANDIDATE",
                    f"QUALIFIED_FOR_{state}",
                ),
                created_at=approved_at,
            )
        package = overlay.load(EXPERIENCE_TARGET_SKILL)
        dry_input = self._handoff_input(
            str(candidate["source_run_id"]),
            task_id="task:experience-approved-canary-dry-call",
            delegation_id="delegation:experience-approved-canary-dry-call",
        )
        invocation = ledger.invoke(
            EXPERIENCE_TARGET_SKILL,
            dry_input,
            context=InvocationContext(
                run_id=str(candidate["source_run_id"]),
                task_id=str(dry_input["task_id"]),
                delegation_id=str(dry_input["delegation_id"]),
                actor_id=SKILL_REGISTRY_AUTHORITY,
            ),
            observed_dependencies=package.manifest["dependencies"],
            created_at=approved_at,
        )
        head = ledger.head(EXPERIENCE_TARGET_SKILL)
        if head is None or head.get("to_state") != "CANARY":
            raise IntegrityError("EXPERIENCE_RELEASE_HEAD_NOT_CANARY")
        return {
            "schema_version": "orgrebase.experience-skill-release.v1",
            "run_id": candidate["source_run_id"],
            "candidate_digest": candidate["digest"],
            "evaluation_digest": evaluation["digest"],
            "skill_name": EXPERIENCE_TARGET_SKILL,
            "package_id": package.manifest["package_id"],
            "package_digest": package.package_digest,
            "predecessor_package_digest": candidate["base_package_digest"],
            "release_state": "CANARY",
            "release_head_digest": head["digest"],
            "release_history": list(ledger.history),
            "authorization_mode": invocation.receipt["authorization_mode"],
            "dry_call": {
                "purpose": "CONTROLLED_LOCAL_RELEASE_AUTHORIZATION_PROOF",
                "current_quote_consumed": False,
                "result": invocation.result,
                "receipt": invocation.receipt,
            },
            "claim_boundary": (
                "CONTROLLED_LOCAL_SINGLE_RUN_SEED_NOT_PRODUCTION_GENERALIZATION"
            ),
            "candidate_only": True,
            "target_writes": 0,
            "released_at": approved_at,
        }

    def decide(
        self,
        *,
        run_id: str,
        decision: str,
        actor_id: str,
        candidate_digest: str,
        evaluation_digest: str,
        observed_skill_head_digest: str,
    ) -> dict[str, Any]:
        selected = decision.strip().upper()
        if selected not in {"APPROVE", "REJECT"}:
            raise ValueError("EXPERIENCE_DECISION_INVALID")
        if actor_id != EXPERIENCE_STEWARD_ID:
            raise AuthorizationError(
                "EXPERIENCE_STEWARD_MISMATCH:"
                f"expected={EXPERIENCE_STEWARD_ID},actual={actor_id}"
            )
        candidate, evaluation, gate, ids = self._require_improve_records(run_id)
        if (
            candidate_digest != candidate["digest"]
            or evaluation_digest != evaluation["digest"]
        ):
            raise IntegrityError("EXPERIENCE_DECISION_DIGEST_MISMATCH")
        current = self.registry.load(EXPERIENCE_TARGET_SKILL)
        if (
            observed_skill_head_digest != gate["observed_skill_head_digest"]
            or current.package_digest != gate["observed_skill_head_digest"]
            or candidate["base_package_digest"] != current.package_digest
        ):
            raise IntegrityError("EXPERIENCE_SKILL_HEAD_DRIFT")
        existing = self._load(ids["decision"], EXPERIENCE_DECISION_MEDIA_TYPE)
        if existing is not None:
            payload = existing["payload"]
            if (
                payload.get("decision") != selected
                or payload.get("actor_id") != actor_id
                or payload.get("candidate_digest") != candidate_digest
                or payload.get("evaluation_digest") != evaluation_digest
                or payload.get("observed_skill_head_digest")
                != observed_skill_head_digest
            ):
                raise RuntimeError("EXPERIENCE_DECISION_ALREADY_FINAL")
            return self.view(run_id)
        now_ms = math.floor(self._wall_clock() * 1000)
        if now_ms < int(gate["not_before_epoch_ms"]):
            raise ExperienceReviewGatePending(
                remaining_ms=int(gate["not_before_epoch_ms"]) - now_ms,
                not_before=str(gate["not_before"]),
            )
        observed_at = _iso_from_epoch_ms(now_ms)
        decision_record = _record(
            {
                "schema_version": "orgrebase.experience-decision.v1",
                "id": ids["decision"],
                "run_id": run_id,
                "decision": selected,
                "actor_id": actor_id,
                "candidate_digest": candidate_digest,
                "evaluation_digest": evaluation_digest,
                "observed_skill_head_digest": observed_skill_head_digest,
                "review_gate_digest": gate["digest"],
                "review_wait_satisfied": True,
                "decided_at_epoch_ms": now_ms,
                "decided_at": observed_at,
                "candidate_only": True,
                "target_writes": 0,
            }
        )
        release = None
        if selected == "APPROVE":
            release_body = self._rebuild_release(
                candidate=candidate,
                persisted_evaluation=evaluation,
                approved_at=observed_at,
            )
            release_body["approval_digest"] = decision_record["digest"]
            release = _record(release_body)
        with self.store.transaction() as connection:
            authorization = current_authorization()
            if authorization is not None:
                authorization()
                self.store.require_before_commit(connection, authorization)
            self.store.save_artifact(
                connection,
                ids["decision"],
                EXPERIENCE_DECISION_MEDIA_TYPE,
                decision_record,
            )
            if release is not None:
                self.store.save_artifact(
                    connection,
                    ids["release"],
                    EXPERIENCE_RELEASE_MEDIA_TYPE,
                    release,
                )
        return self.view(run_id)

    def _approved_records(
        self, run_id: str
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        candidate, evaluation, _, ids = self._require_improve_records(run_id)
        decision_record = self._load(ids["decision"], EXPERIENCE_DECISION_MEDIA_TYPE)
        release_record = self._load(ids["release"], EXPERIENCE_RELEASE_MEDIA_TYPE)
        if decision_record is None or decision_record["payload"].get("decision") != "APPROVE":
            raise AuthorizationError("EXPERIENCE_CANDIDATE_NOT_APPROVED")
        if release_record is None:
            raise IntegrityError("EXPERIENCE_APPROVAL_WITHOUT_RELEASE")
        decision = decision_record["payload"]
        release = release_record["payload"]
        _verify_record(decision, error="EXPERIENCE_DECISION_DIGEST_MISMATCH")
        _verify_record(release, error="EXPERIENCE_RELEASE_DIGEST_MISMATCH")
        if (
            decision.get("run_id") != run_id
            or decision.get("candidate_digest") != candidate["digest"]
            or decision.get("evaluation_digest") != evaluation["digest"]
            or release.get("approval_digest") != decision["digest"]
            or release.get("candidate_digest") != candidate["digest"]
            or release.get("evaluation_digest") != evaluation["digest"]
            or release.get("release_state") != "CANARY"
            or release.get("authorization_mode") != "RELEASE"
        ):
            raise IntegrityError("EXPERIENCE_APPROVED_RELEASE_BINDING_INVALID")
        return candidate, evaluation, release

    def discover_approved(self, run_id: str) -> tuple[dict[str, str], ...]:
        candidate, _, release = self._approved_records(run_id)
        overlay = self._overlay(candidate)
        discovered = tuple(
            item
            for item in overlay.discover()
            if item["name"] == EXPERIENCE_TARGET_SKILL
            and item["package_digest"] == release["package_digest"]
        )
        if len(discovered) != 1:
            raise IntegrityError("EXPERIENCE_RELEASE_DISCOVERY_MISMATCH")
        return discovered

    def load_approved(self, run_id: str):
        candidate, _, release = self._approved_records(run_id)
        return self._overlay(candidate).load(
            EXPERIENCE_TARGET_SKILL,
            expected_package_digest=str(release["package_digest"]),
        )

    def invoke_approved(self, run_id: str) -> dict[str, Any]:
        """Return the persisted controlled-local RELEASE dry-call proof."""

        _, _, release = self._approved_records(run_id)
        return deepcopy(release["dry_call"])

    def view(self, run_id: str) -> dict[str, Any]:
        ids = self._artifact_ids(run_id)
        records = {
            "candidate": self._load(
                ids["candidate"], EXPERIENCE_CANDIDATE_MEDIA_TYPE
            ),
            "evaluation": self._load(
                ids["evaluation"], EXPERIENCE_EVALUATION_MEDIA_TYPE
            ),
            "review_gate": self._load(
                ids["gate"], EXPERIENCE_REVIEW_GATE_MEDIA_TYPE
            ),
            "decision": self._load(
                ids["decision"], EXPERIENCE_DECISION_MEDIA_TYPE
            ),
            "release": self._load(ids["release"], EXPERIENCE_RELEASE_MEDIA_TYPE),
        }
        candidate_record = records["candidate"]
        if candidate_record is None:
            return {
                "schema_version": "orgrebase.experience-governance-view.v1",
                "run_id": run_id,
                "status": "NOT_RUN",
                "candidate_only": True,
                "target_writes": 0,
            }
        candidate = candidate_record["payload"]
        decision = (
            records["decision"]["payload"]
            if records["decision"] is not None
            else None
        )
        release = (
            records["release"]["payload"]
            if records["release"] is not None
            else None
        )
        if candidate.get("outcome") == "NO_CANDIDATE":
            status = "NO_CANDIDATE"
        elif decision is None:
            status = "AWAITING_HUMAN_APPROVAL"
        elif decision.get("decision") == "REJECT":
            status = "REJECTED"
        elif release is not None:
            status = "APPROVED_CANARY"
        else:
            status = "FAIL_CLOSED"
        current_head = self.registry.load(EXPERIENCE_TARGET_SKILL).package_digest
        gate = (
            records["review_gate"]["payload"]
            if records["review_gate"] is not None
            else None
        )
        remaining_ms = (
            max(
                0,
                int(gate["not_before_epoch_ms"])
                - math.floor(self._wall_clock() * 1000),
            )
            if gate is not None and decision is None
            else 0
        )
        body = {
            "schema_version": "orgrebase.experience-governance-view.v1",
            "run_id": run_id,
            "status": status,
            "owner_id": EXPERIENCE_STEWARD_ID,
            "candidate": candidate,
            "evaluation": (
                records["evaluation"]["payload"]
                if records["evaluation"] is not None
                else None
            ),
            "review_gate": gate,
            "review_remaining_ms": remaining_ms,
            "decision": decision,
            "release": release,
            "current_skill_head_digest": current_head,
            "head_fresh": (
                gate is None
                or gate.get("observed_skill_head_digest") == current_head
            ),
            "discoverable": status == "APPROVED_CANARY",
            "loadable": status == "APPROVED_CANARY",
            "callable": status == "APPROVED_CANARY",
            "current_quote_consumed_candidate": False,
            "candidate_only": True,
            "target_writes": 0,
        }
        return {**body, "digest": sha256_digest(body)}
