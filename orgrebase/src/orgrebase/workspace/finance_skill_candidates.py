"""Authenticated, private Finance instruction proposals.

The exact unreviewed bundle stays in PrivateRecordStore.  The canonical
artifact records who actually submitted it and the parent head it was built
from; a caller cannot later nominate a different author for qualification.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from orgrebase.auth import (
    AuthenticationError,
    authorize,
    current_authorization,
    request_principal,
)
from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, IntegrityError
from orgrebase.private_records import PrivateRecordStore
from orgrebase.workspace.finance_experiment_input import FinanceExperimentInputService
from orgrebase.workspace.skill_evolution_v2 import FinanceSkillHeadService, SkillContentBundleV2

CANDIDATE_MEDIA = "application/vnd.orgrebase.finance-skill-candidate.v1+json"
_OPERATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")


class FinanceSkillCandidateService:
    """Create and verify exact candidate bytes under a real request Principal."""

    def __init__(
        self, workspace: Any, head: FinanceSkillHeadService,
        *, experiment_input: FinanceExperimentInputService | None = None,
    ) -> None:
        if (
            workspace.store is not head.store
            or workspace.profile.organization_id != head.tenant_id
            or workspace.private_retention_seconds <= 0
        ):
            raise ValueError("FINANCE_CANDIDATE_WORKSPACE_REQUIRED")
        self.workspace = workspace
        self.head = head
        if experiment_input is not None and (
            experiment_input.workspace is not workspace or experiment_input.head is not head
        ):
            raise ValueError("FINANCE_CANDIDATE_INPUT_SCOPE_INVALID")
        self.experiment_input = experiment_input
        self.private = PrivateRecordStore(
            workspace.store, workspace.clock,
            retention_seconds=workspace.private_retention_seconds,
        )

    def _author(self) -> Any:
        principal = request_principal.get()
        if principal is None:
            raise AuthenticationError("FINANCE_CANDIDATE_PRINCIPAL_REQUIRED")
        authorize(principal, "propose", self.head.tenant_id)
        if principal.actor_id == self.head._current_source().payload["actor_id"]:
            raise AuthorizationError("FINANCE_CANDIDATE_GOVERNOR_CANNOT_AUTHOR")
        check = current_authorization()
        if check is not None:
            check()
        return principal

    def _reviewer(self) -> Any:
        principal = request_principal.get()
        if principal is None:
            raise AuthenticationError("FINANCE_CANDIDATE_REVIEWER_REQUIRED")
        authorize(principal, "govern", self.head.tenant_id)
        check = current_authorization()
        if check is not None:
            check()
        return principal

    def _ref(self, operation_id: str) -> str:
        if not isinstance(operation_id, str) or _OPERATION_ID.fullmatch(operation_id) is None:
            raise ValueError("FINANCE_CANDIDATE_OPERATION_INVALID")
        return "finance-skill-candidate:" + sha256_digest({
            "scope_digest": self.head.scope_digest, "operation_id": operation_id,
        })[7:]

    def propose_instruction(
        self, *, operation_id: str, instruction_text: str,
        experiment_input_ref: str | None = None,
    ) -> str:
        author = self._author()
        frozen = None
        if experiment_input_ref is not None:
            if self.experiment_input is None:
                raise IntegrityError("FINANCE_CANDIDATE_INPUT_VERIFIER_REQUIRED")
            frozen = self.experiment_input.require_current(experiment_input_ref)
        candidate_ref = self._ref(operation_id)
        parent = self.head.resolve()
        candidate = self.head.prepare_instruction_patch(
            instruction_text,
            expected_head_ref=parent.head_ref,
            expected_head_digest=parent.head_digest,
            expected_generation=parent.generation,
            expected_package_digest=parent.package_digest,
        )
        if frozen is not None and (
            frozen.actor_id != author.actor_id
            or frozen.head_ref != parent.head_ref
            or frozen.head_digest != parent.head_digest
            or frozen.package_digest != parent.package_digest
            or candidate.resource_digests["references/change-explanation.md"]
            != frozen.reference_digest
            or len(candidate.instruction_bytes) > frozen.instruction_available_bytes
        ):
            raise IntegrityError("FINANCE_CANDIDATE_FROZEN_INPUT_OR_BUDGET_INVALID")
        private_ref = "finance-skill-candidate-private:" + candidate_ref.rsplit(":", 1)[1]
        body = {
            "schema_version": (
                "orgrebase.finance-skill-candidate.v2" if frozen is not None
                else "orgrebase.finance-skill-candidate.v1"
            ),
            "scope_digest": self.head.scope_digest,
            "operation_id": operation_id,
            "parent_head_ref": parent.head_ref,
            "parent_head_digest": parent.head_digest,
            "parent_generation": parent.generation,
            "parent_package_digest": parent.package_digest,
            "candidate_bundle_digest": candidate.digest,
            "candidate_package_digest": candidate.package_digest,
            "private_ref": private_ref,
            "private_digest": sha256_digest(candidate.payload),
            "author_actor_id": author.actor_id,
            "author_issuer": author.issuer,
            "author_subject": author.subject,
            "content_status": "PRIVATE_NOT_RELEASED",
            **({
                "experiment_input_ref": frozen.ref,
                "experiment_input_digest": frozen.digest,
            } if frozen is not None else {}),
        }
        with self.workspace.store.transaction() as connection:
            self._author()
            self.workspace.store.require_before_commit(connection, self._author)
            if frozen is not None:
                assert self.experiment_input is not None
                self.workspace.store.require_before_commit(
                    connection,
                    lambda: self.experiment_input.require_current(frozen.ref),
                )
            if self.head.resolve() != parent:
                raise IntegrityError("FINANCE_SKILL_STALE_BASE")
            try:
                existing = self.workspace.store.load_artifact(candidate_ref, CANDIDATE_MEDIA).payload
            except KeyError:
                existing = None
            if existing is not None:
                if existing != body:
                    raise IntegrityError("FINANCE_CANDIDATE_OPERATION_CONFLICT")
                return candidate_ref
            self.private.write(
                connection, record_id=private_ref, scope_ref=candidate_ref,
                owner_id=author.actor_id, payload=candidate.payload,
            )
            self.workspace.store.save_artifact(connection, candidate_ref, CANDIDATE_MEDIA, body)
            self.workspace.store.append_event(connection, "FINANCE_SKILL_CANDIDATE_PROPOSED", {
                "candidate_ref": candidate_ref,
                "candidate_record_digest": sha256_digest(body),
                "candidate_bundle_digest": candidate.digest,
                "parent_head_ref": parent.head_ref,
                "author_actor_id": author.actor_id,
                **({"experiment_input_digest": frozen.digest} if frozen is not None else {}),
                "target_writes": 0,
            })
        return candidate_ref

    def verify_for_qualification(
        self, candidate_ref: str, *, candidate: SkillContentBundleV2,
        phase: Literal["EVALUATOR", "GOVERNOR"] = "EVALUATOR",
    ) -> dict[str, Any]:
        reviewer = self._reviewer()
        if phase not in {"EVALUATOR", "GOVERNOR"}:
            raise ValueError("FINANCE_CANDIDATE_REVIEW_PHASE_INVALID")
        body = self.workspace.store.load_artifact(candidate_ref, CANDIDATE_MEDIA).payload
        if (
            body.get("schema_version") not in {
                "orgrebase.finance-skill-candidate.v1",
                "orgrebase.finance-skill-candidate.v2",
            }
            or body.get("scope_digest") != self.head.scope_digest
            or candidate_ref != self._ref(body["operation_id"])
            or reviewer.actor_id == body.get("author_actor_id")
            or body.get("content_status") != "PRIVATE_NOT_RELEASED"
            or body.get("candidate_bundle_digest") != candidate.digest
            or body.get("candidate_package_digest") != candidate.package_digest
            or body.get("private_digest") != sha256_digest(candidate.payload)
        ):
            raise IntegrityError("FINANCE_CANDIDATE_PROPOSAL_INVALID")
        if body["schema_version"] == "orgrebase.finance-skill-candidate.v2":
            if self.experiment_input is None:
                raise IntegrityError("FINANCE_CANDIDATE_INPUT_VERIFIER_REQUIRED")
            input_ref = body.get("experiment_input_ref", "")
            frozen = (
                self.experiment_input.require_current(input_ref, evaluator=True)
                if phase == "EVALUATOR"
                else self.experiment_input.require_current_for_governor(input_ref)
            )
            if (
                frozen.digest != body.get("experiment_input_digest")
                or frozen.actor_id != body["author_actor_id"]
                or candidate.resource_digests["references/change-explanation.md"]
                != frozen.reference_digest
                or len(candidate.instruction_bytes) > frozen.instruction_available_bytes
            ):
                raise IntegrityError("FINANCE_CANDIDATE_FROZEN_INPUT_OR_BUDGET_INVALID")
        current = self.head.resolve()
        if (
            body["parent_head_ref"] != current.head_ref
            or body["parent_head_digest"] != current.head_digest
            or body["parent_generation"] != current.generation
            or body["parent_package_digest"] != current.package_digest
        ):
            raise IntegrityError("FINANCE_SKILL_STALE_BASE")
        private = self.private.read_owned(
            body["private_ref"], owner_id=body["author_actor_id"], scope_ref=candidate_ref,
        )
        if private is None or private != candidate.payload:
            raise IntegrityError("FINANCE_CANDIDATE_PRIVATE_CONTENT_UNAVAILABLE")
        matching = 0
        proposal_sequence: int | None = None
        exposure_sequences: list[int] = []
        cursor = 0
        seen = 0
        while True:
            page = self.workspace.store.event_page(
                after=cursor, limit=500,
                event_types=(
                    "FINANCE_SKILL_CANDIDATE_PROPOSED",
                    "FINANCE_EVALUATION_INTENT_RESERVED",
                    "FINANCE_EXPERIMENT_TRIAL_RESERVED",
                ),
            )
            for event in page["items"]:
                seen += 1
                if seen > 10_000:
                    raise IntegrityError("FINANCE_CANDIDATE_EVENT_COVERAGE_INCOMPLETE")
                payload = event["payload"]
                if event["event_type"] == "FINANCE_SKILL_CANDIDATE_PROPOSED" and payload.get("candidate_ref") == candidate_ref:
                    if (
                        payload.get("candidate_record_digest") != sha256_digest(body)
                        or payload.get("candidate_bundle_digest") != candidate.digest
                        or payload.get("author_actor_id") != body["author_actor_id"]
                    ):
                        raise IntegrityError("FINANCE_CANDIDATE_EVENT_INVALID")
                    matching += 1
                    proposal_sequence = event["sequence_no"]
                elif event["event_type"] in {
                    "FINANCE_EVALUATION_INTENT_RESERVED",
                    "FINANCE_EXPERIMENT_TRIAL_RESERVED",
                }:
                    ref = payload.get("intent_ref") or payload.get("trial_ref")
                    media = (
                        "application/vnd.orgrebase.finance-evaluation-intent.v1+json"
                        if event["event_type"] == "FINANCE_EVALUATION_INTENT_RESERVED"
                        else "application/vnd.orgrebase.finance-experiment-trial.v1+json"
                    )
                    if not isinstance(ref, str):
                        raise IntegrityError("FINANCE_CANDIDATE_EXPOSURE_COVERAGE_INCOMPLETE")
                    try:
                        intent = self.workspace.store.load_artifact(ref, media).payload
                    except KeyError as exc:
                        raise IntegrityError("FINANCE_CANDIDATE_EXPOSURE_COVERAGE_INCOMPLETE") from exc
                    if intent.get("candidate_bundle_digest") == candidate.digest:
                        exposure_sequences.append(event["sequence_no"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        if matching != 1 or proposal_sequence is None:
            raise IntegrityError("FINANCE_CANDIDATE_AUTHOR_PROOF_MISSING")
        if exposure_sequences and min(exposure_sequences) <= proposal_sequence:
            raise IntegrityError("FINANCE_CANDIDATE_PROPOSAL_AFTER_EXPOSURE")
        return {
            "candidate_ref": candidate_ref,
            "candidate_record_digest": sha256_digest(body),
            "candidate_proposal_sequence": proposal_sequence,
            "author_actor_id": body["author_actor_id"],
            "author_issuer": body["author_issuer"],
            "author_subject": body["author_subject"],
            "parent_head_ref": body["parent_head_ref"],
            "candidate_bundle_digest": candidate.digest,
            **({
                "experiment_input_ref": frozen.ref,
                "experiment_input_digest": frozen.digest,
                "experiment_input_scope": frozen.evidence_scope,
            } if body["schema_version"] == "orgrebase.finance-skill-candidate.v2" else {}),
        }


__all__ = ["CANDIDATE_MEDIA", "FinanceSkillCandidateService"]
