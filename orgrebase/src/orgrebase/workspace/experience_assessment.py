"""Independent assessment of observed experience cases.

This records an evaluator's versioned judgment.  Neither an APPLIED business
outcome nor a collector observation automatically becomes a learning label.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, Literal

from orgrebase.auth import AuthenticationError, request_principal
from orgrebase.clock import utc_datetime
from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, IntegrityError, ObjectState
from orgrebase.workspace.change_proposals import require_action
from orgrebase.workspace.experience_collection import OBSERVATION_MEDIA
from orgrebase.workspace.experience_contracts import (
    CaseAssessmentReceipt,
    CaseObservationV2,
    IndependenceClusterProof,
)
from orgrebase.workspace.experience_source_hold import is_case_source_held
from orgrebase.workspace.quote_pattern_bridge import quote_recovery_observation_v2
from orgrebase.workspace.read_dependencies import (
    ReadDependencyError,
    validate_read_dependencies,
    validate_source_observations,
)

ASSESSMENT_MEDIA = "application/vnd.orgrebase.case-assessment+json"
CLUSTER_PROOF_MEDIA = "application/vnd.orgrebase.independence-cluster-proof+json"
REVISION_SELECTION_MEDIA = "application/vnd.orgrebase.experience-revision-selection+json"
REVISION_SELECTION_POLICY = "latest-observed-case-and-independent-repair.v1"


def require_quote_case_current(workspace: Any, case: CaseObservationV2) -> None:
    """Conservatively bind an evaluated recovery to today's business source."""

    require_action(workspace, "read")
    if is_case_source_held(workspace, case):
        raise IntegrityError("EXPERIENCE_SOURCE_INVALIDATED")
    event = workspace.changes.get(case.business_event_id)
    if event.digest != case.business_event_digest:
        raise IntegrityError("EXPERIENCE_CASE_BUSINESS_EVENT_CHANGED")
    try:
        current = workspace.store.get_object(event.proposal.id)
    except KeyError as exc:
        raise IntegrityError("EXPERIENCE_CASE_SOURCE_NOT_CURRENT") from exc
    now = utc_datetime(workspace.clock.now())
    if (
        current.state not in {ObjectState.CURRENT, ObjectState.ACTIVE}
        or current.ref != event.proposal.ref
        or current.digest != event.proposal.digest
        or not set(event.proposal.source_refs) <= set(current.source_refs)
        or utc_datetime(current.valid_from) > now
        or (current.valid_to is not None and now >= utc_datetime(current.valid_to))
    ):
        raise IntegrityError("EXPERIENCE_CASE_SOURCE_NOT_CURRENT")
    observation_ref = event.proposal.payload.get("source_observation_ref")
    if (
        not event.proposal.version.startswith("source-")
        or not isinstance(observation_ref, str)
        or re.fullmatch(r"source-observation:[0-9a-f]{64}", observation_ref) is None
    ):
        raise IntegrityError("EXPERIENCE_CONTROLLED_SOURCE_REQUIRED")
    try:
        # Reuse the source connector's current coverage/ACL/lease and read
        # dependency verifier. A human-entered source_refs string is not a
        # qualified independent provenance locator.
        validate_source_observations(workspace, (event.proposal,), workspace.clock.now())
        validate_read_dependencies(workspace, (event.proposal,), workspace.clock.now())
    except (ReadDependencyError, KeyError, TypeError, ValueError) as exc:
        raise IntegrityError("EXPERIENCE_CONTROLLED_SOURCE_NOT_CURRENT") from exc


def _assessment_prefix(case_digest: str, profile_id: str) -> str:
    return "experience-assessment:" + case_digest[7:] + ":" + sha256_digest(profile_id)[7:] + ":"


def _allowed_case_evidence(case: CaseObservationV2) -> frozenset[str]:
    """Only refs independently rebound by `_case` to existing source facts."""

    return frozenset(value for value in (
        case.business_event_digest, *case.origin_event_refs,
        case.request_digest, case.resume_digest, case.outcome_artifact_digest,
    ) if value is not None)


class ExperienceAssessmentService:
    def __init__(
        self, workspace: Any, *, evaluator_actor_id: str,
        excluded_actor_ids: tuple[str, ...], rubric_version: str,
        source_qualification_check: Callable[[CaseObservationV2], None] | None = None,
    ) -> None:
        if (
            not evaluator_actor_id
            or evaluator_actor_id in excluded_actor_ids
            or not rubric_version
        ):
            raise ValueError("EXPERIENCE_ASSESSMENT_SEPARATION_REQUIRED")
        self.workspace = workspace
        self.evaluator_actor_id = evaluator_actor_id
        self.rubric_version = rubric_version
        self.source_qualification_check = source_qualification_check

    def _authorize(self) -> None:
        principal = request_principal.get()
        if principal is None:
            raise AuthenticationError("EXPERIENCE_EVALUATOR_PRINCIPAL_REQUIRED")
        if principal.actor_id != self.evaluator_actor_id or principal.tenant_id != self.workspace.store.tenant_id:
            raise AuthorizationError("EXPERIENCE_EVALUATOR_SCOPE_DENIED")
        require_action(self.workspace, "govern")

    def _case(self, case_ref: str) -> CaseObservationV2:
        stored = self.workspace.store.load_artifact(case_ref, OBSERVATION_MEDIA)
        case = CaseObservationV2.model_validate(stored.payload).revalidated()
        if (
            case.tenant_id != self.workspace.store.tenant_id
            or case.workspace_id != self.workspace.store.workspace_id
            or case_ref != f"experience-case:{case.case_id}:{case.revision[7:]}@v2"
            or not case.private_episode_ref
            or self.workspace.private_records.record_status(case.private_episode_ref) != "AVAILABLE"
        ):
            raise IntegrityError("EXPERIENCE_CASE_NOT_CURRENTLY_QUALIFIED")
        if is_case_source_held(self.workspace, case):
            raise IntegrityError("EXPERIENCE_SOURCE_INVALIDATED")
        event = self.workspace.store.event_by_digest(case.origin_event_refs[-1])
        if event is None:
            raise IntegrityError("EXPERIENCE_CASE_SOURCE_EVENT_MISSING")
        fresh, _ = quote_recovery_observation_v2(self.workspace, event)
        for name in (
            "case_id", "revision", "business_event_digest", "request_digest",
            "resume_digest", "outcome_artifact_digest", "execution_outcome",
            "independence_cluster_id", "resolver_version",
        ):
            if getattr(fresh, name) != getattr(case, name):
                raise IntegrityError("EXPERIENCE_CASE_SOURCE_CHANGED")
        return case

    def record_assessment(
        self, *, operation_id: str, case_ref: str, profile_id: str,
        verdict: Literal["SUPPORT", "COUNTEREXAMPLE", "UNKNOWN", "DISPUTED"],
        reason_code: str, evidence_refs: tuple[str, ...],
        independence_cluster_id: str | None = None,
    ) -> str:
        self._authorize()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}", operation_id):
            raise ValueError("EXPERIENCE_ASSESSMENT_OPERATION_INVALID")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,95}", reason_code):
            raise ValueError("EXPERIENCE_ASSESSMENT_REASON_INVALID")
        if not profile_id or not case_ref:
            raise ValueError("EXPERIENCE_ASSESSMENT_SCOPE_REQUIRED")
        if any(not re.fullmatch(r"sha256:[0-9a-f]{64}", ref) for ref in evidence_refs):
            raise ValueError("EXPERIENCE_ASSESSMENT_EVIDENCE_REF_INVALID")
        if tuple(sorted(set(evidence_refs))) != evidence_refs:
            raise ValueError("EXPERIENCE_ASSESSMENT_EVIDENCE_SET_INVALID")
        selected = self._case(case_ref)
        if not set(evidence_refs) <= _allowed_case_evidence(selected):
            raise IntegrityError("EXPERIENCE_ASSESSMENT_EVIDENCE_UNBOUND")
        positive = verdict in {"SUPPORT", "COUNTEREXAMPLE"}
        if positive:
            if self.source_qualification_check is None:
                raise IntegrityError("EXPERIENCE_SOURCE_QUALIFICATION_REQUIRED")
            self.source_qualification_check(selected)
            if (
                selected.cluster_status not in {"PROVISIONAL", "PROVEN"}
                or not selected.independence_cluster_id
                or independence_cluster_id != selected.independence_cluster_id
                or not evidence_refs
            ):
                raise IntegrityError("EXPERIENCE_CLUSTER_PROOF_REQUIRED")
            business_event = self.workspace.changes.get(selected.business_event_id)
            source_refs = business_event.proposal.source_refs
            if not source_refs:
                raise IntegrityError("EXPERIENCE_CLUSTER_BASIS_MISSING")
        else:
            source_refs = ()
            if independence_cluster_id is not None:
                raise IntegrityError("EXPERIENCE_UNTRUSTED_CLUSTER_LABEL")
        command = {
            "operation_id": operation_id, "case_ref": case_ref,
            "case_digest": selected.digest, "profile_id": profile_id,
            "verdict": verdict, "reason_code": reason_code,
            "evidence_refs": evidence_refs,
            "independence_cluster_id": independence_cluster_id,
            "rubric_version": self.rubric_version,
            "evaluator_actor_id": self.evaluator_actor_id,
        }
        command_digest = sha256_digest(command)
        operation_key = "experience-assess:" + sha256_digest({
            "case_ref": case_ref, "profile_id": profile_id,
            "operation_id": operation_id,
        })[7:]
        with self.workspace.store.transaction() as connection:
            self._authorize()
            self.workspace.store.require_before_commit(connection, self._authorize)
            previous = self.workspace.store.get_idempotent(
                operation_key, command_digest, connection=connection,
            )
            if previous is not None:
                return previous["assessment_ref"]
            self._case(case_ref)
            if positive:
                assert independence_cluster_id is not None
                assert self.source_qualification_check is not None
                self.source_qualification_check(selected)
                proof = IndependenceClusterProof(
                    tenant_id=selected.tenant_id,
                    workspace_id=selected.workspace_id,
                    profile_id=profile_id,
                    case_ref=case_ref, case_digest=selected.digest,
                    independence_cluster_id=independence_cluster_id,
                    basis_ref_digests=tuple(sorted({sha256_digest(ref) for ref in source_refs})),
                    issuer_id=self.evaluator_actor_id,
                    issued_at=self.workspace.clock.now(), operation_id=operation_id,
                )
                proof_ref = "experience-cluster-proof:" + proof.digest[7:] + "@v1"
                self.workspace.store.save_artifact(
                    connection, proof_ref, CLUSTER_PROOF_MEDIA, proof.model_dump(mode="json"),
                )
            else:
                proof = None
                proof_ref = None
            receipt = CaseAssessmentReceipt(
                tenant_id=selected.tenant_id,
                workspace_id=selected.workspace_id,
                profile_id=profile_id,
                case_ref=case_ref, case_digest=selected.digest,
                independence_cluster_id=independence_cluster_id,
                verdict=verdict, rubric_version=self.rubric_version,
                reason_code=reason_code, evidence_refs=evidence_refs,
                evaluator_id=self.evaluator_actor_id,
                evaluated_at=self.workspace.clock.now(),
                cluster_proof_ref=proof_ref,
                cluster_proof_digest=proof.digest if proof is not None else None,
                operation_id=operation_id,
            )
            assessment_ref = _assessment_prefix(selected.digest, profile_id) + receipt.digest[7:] + "@v1"
            self.workspace.store.save_artifact(
                connection, assessment_ref, ASSESSMENT_MEDIA, receipt.model_dump(mode="json"),
            )
            self.workspace.store.save_idempotent(
                connection, operation_key, command_digest, {"assessment_ref": assessment_ref},
            )
            self.workspace.store.append_event(connection, "EXPERIENCE_CASE_ASSESSED", {
                "case_ref": case_ref, "assessment_ref": assessment_ref,
                "assessment_digest": receipt.digest,
                "profile_id": profile_id, "verdict": verdict,
                "evaluator_id": self.evaluator_actor_id,
            })
        return assessment_ref

    def read_assessment(
        self, assessment_ref: str,
    ) -> tuple[CaseObservationV2, CaseAssessmentReceipt, IndependenceClusterProof | None]:
        """Recheck exact case, current source and proof before corpus use."""

        self._authorize()
        return self._verify_assessment(assessment_ref)

    def verify_assessment_for_corpus(
        self, assessment_ref: str, *, authorized_corpus_actor_id: str,
    ) -> tuple[CaseObservationV2, CaseAssessmentReceipt, IndependenceClusterProof | None]:
        """Permit a separate governed corpus actor to verify, never to sign."""

        principal = request_principal.get()
        if principal is None:
            raise AuthenticationError("EXPERIENCE_CORPUS_PRINCIPAL_REQUIRED")
        if (
            principal.actor_id != authorized_corpus_actor_id
            or principal.actor_id == self.evaluator_actor_id
            or principal.tenant_id != self.workspace.store.tenant_id
        ):
            raise AuthorizationError("EXPERIENCE_CORPUS_SCOPE_DENIED")
        require_action(self.workspace, "govern")
        case, receipt, proof = self._verify_assessment(assessment_ref)
        if self._has_conflicting_verdicts(case, receipt.profile_id):
            raise IntegrityError("EXPERIENCE_ASSESSMENT_DISPUTED")
        self._revision_selection(assessment_ref, case=case, receipt=receipt)
        return case, receipt, proof

    def verify_assessment_for_governed_use(
        self, assessment_ref: str, *, authorized_actor_id: str,
    ) -> tuple[CaseObservationV2, CaseAssessmentReceipt, IndependenceClusterProof | None]:
        """Let a distinct current governor review evidence for lesson release."""

        return self.verify_assessment_for_corpus(
            assessment_ref, authorized_corpus_actor_id=authorized_actor_id,
        )

    def evidence_current_for_reader(self, assessment_ref: str) -> bool:
        """Recheck qualification without disclosing assessment or source text."""

        return self.evidence_verdict_for_reader(assessment_ref) == "SUPPORT"

    def evidence_verdict_for_reader(self, assessment_ref: str) -> str | None:
        """Return only a current, unconflicted label for hard filtering."""

        require_action(self.workspace, "read")
        try:
            case, receipt, _ = self._verify_assessment(assessment_ref)
            if self._has_conflicting_verdicts(case, receipt.profile_id):
                return None
            self._revision_selection(assessment_ref, case=case, receipt=receipt)
            return receipt.verdict
        except (IntegrityError, KeyError, ValueError, PermissionError):
            return None

    def revision_selection_for_governed_use(
        self, assessment_ref: str, *, authorized_actor_id: str,
    ) -> dict[str, Any]:
        case, receipt, _ = self.verify_assessment_for_governed_use(
            assessment_ref, authorized_actor_id=authorized_actor_id,
        )
        return self._revision_selection(assessment_ref, case=case, receipt=receipt)

    def revision_selection_for_reader(self, assessment_ref: str) -> dict[str, Any]:
        require_action(self.workspace, "read")
        case, receipt, _ = self._verify_assessment(assessment_ref)
        return self._revision_selection(assessment_ref, case=case, receipt=receipt)

    def revision_selection_for_corpus(
        self, assessment_ref: str, *, authorized_corpus_actor_id: str,
    ) -> dict[str, Any]:
        case, receipt, _ = self.verify_assessment_for_corpus(
            assessment_ref, authorized_corpus_actor_id=authorized_corpus_actor_id,
        )
        return self._revision_selection(assessment_ref, case=case, receipt=receipt)

    def verify_historical_counter_for_governed_use(
        self, assessment_ref: str, *, authorized_actor_id: str,
    ) -> tuple[CaseObservationV2, CaseAssessmentReceipt, IndependenceClusterProof | None]:
        principal = request_principal.get()
        if (
            principal is None or principal.actor_id != authorized_actor_id
            or principal.actor_id == self.evaluator_actor_id
            or principal.tenant_id != self.workspace.store.tenant_id
        ):
            raise AuthorizationError("EXPERIENCE_HISTORICAL_COUNTER_SCOPE_DENIED")
        require_action(self.workspace, "govern")
        case, receipt, proof = self._verify_assessment(assessment_ref)
        if receipt.verdict != "COUNTEREXAMPLE":
            raise IntegrityError("EXPERIENCE_HISTORICAL_COUNTER_INVALID")
        return case, receipt, proof

    def historical_counter_valid_for_reader(
        self, assessment_ref: str, *, support_assessment_refs: tuple[str, ...],
    ) -> bool:
        require_action(self.workspace, "read")
        try:
            _, counter, _ = self._verify_assessment(assessment_ref)
            if counter.verdict != "COUNTEREXAMPLE":
                return False
            for support_ref in support_assessment_refs:
                support_case, support, _ = self._verify_assessment(support_ref)
                selection = self._revision_selection(
                    support_ref, case=support_case, receipt=support,
                )
                if assessment_ref in selection["historical_counter_refs"]:
                    return True
            case, receipt, _ = self._verify_assessment(assessment_ref)
            self._revision_selection(assessment_ref, case=case, receipt=receipt)
            return True
        except (IntegrityError, KeyError, ValueError, PermissionError):
            return False

    def _has_conflicting_verdicts(self, case: CaseObservationV2, profile_id: str) -> bool:
        rows = self._assessment_rows(case, profile_id)
        verdicts = {
            CaseAssessmentReceipt.model_validate(row.payload).revalidated().verdict
            for row in rows
        }
        return len(verdicts) > 1

    def _assessment_rows(self, case: CaseObservationV2, profile_id: str) -> tuple[Any, ...]:
        rows, complete = self._assessment_page(case, profile_id, max_records=1000)
        if not complete:
            raise IntegrityError("EXPERIENCE_ASSESSMENT_COVERAGE_INCOMPLETE")
        return rows

    def _assessment_page(
        self, case: CaseObservationV2, profile_id: str, *, max_records: int,
    ) -> tuple[tuple[Any, ...], bool]:
        prefix = _assessment_prefix(case.digest, profile_id)
        rows = []
        cursor = None
        while len(rows) < max_records:
            page = self.workspace.store.artifact_page(
                artifact_id_prefix=prefix, expected_media_type=ASSESSMENT_MEDIA,
                after=cursor, limit=min(100, max_records - len(rows)),
            )
            rows.extend(page["items"])
            if page["next_cursor"] is None:
                return tuple(rows), True
            cursor = page["next_cursor"]
        return tuple(rows), False

    def _related_case_rows(self, case: CaseObservationV2) -> tuple[tuple[Any, CaseObservationV2, int], ...]:
        rows = []
        cursor = None
        while len(rows) < 5000:
            page = self.workspace.store.artifact_page(
                artifact_id_prefix="experience-case:", expected_media_type=OBSERVATION_MEDIA,
                after=cursor, limit=min(100, 5000 - len(rows)),
            )
            rows.extend(page["items"])
            if page["next_cursor"] is None:
                break
            cursor = page["next_cursor"]
        else:
            raise IntegrityError("EXPERIENCE_REVISION_COVERAGE_INCOMPLETE")
        related = []
        for row in rows:
            candidate = CaseObservationV2.model_validate(row.payload).revalidated()
            if (
                candidate.tenant_id != self.workspace.store.tenant_id
                or candidate.workspace_id != self.workspace.store.workspace_id
                or row.artifact_id != f"experience-case:{candidate.case_id}:{candidate.revision[7:]}@v2"
            ):
                raise IntegrityError("EXPERIENCE_CASE_SCOPE_INVALID")
            if (
                candidate.case_id != case.case_id
                and (not case.independence_cluster_id
                     or candidate.independence_cluster_id != case.independence_cluster_id)
            ):
                continue
            event = self.workspace.store.event_by_digest(candidate.origin_event_refs[-1])
            if event is None:
                raise IntegrityError("EXPERIENCE_CASE_SOURCE_EVENT_MISSING")
            related.append((row, candidate, event["sequence_no"]))
        if not related:
            raise IntegrityError("EXPERIENCE_REVISION_MEMBERS_MISSING")
        return tuple(sorted(related, key=lambda item: (item[2], item[0].artifact_id)))

    def _revision_selection(
        self, assessment_ref: str, *, case: CaseObservationV2,
        receipt: CaseAssessmentReceipt,
    ) -> dict[str, Any]:
        """Discover all revisions/cluster aliases, not only caller-supplied refs."""

        related = self._related_case_rows(case)
        latest_sequence = related[-1][2]
        if sum(sequence == latest_sequence for _, _, sequence in related) != 1:
            raise IntegrityError("EXPERIENCE_REVISION_ORDER_AMBIGUOUS")
        members = []
        historical_counters = []
        latest_refs = []
        for row, member_case, sequence in related:
            assessment_rows = self._assessment_rows(member_case, receipt.profile_id)
            labels = []
            refs = []
            for assessment_row in assessment_rows:
                selected = CaseAssessmentReceipt.model_validate(assessment_row.payload).revalidated()
                if (
                    selected.tenant_id != self.workspace.store.tenant_id
                    or selected.workspace_id != self.workspace.store.workspace_id
                    or selected.case_ref != row.artifact_id
                    or selected.case_digest != member_case.digest
                    or selected.profile_id != receipt.profile_id
                    or selected.evaluator_id != self.evaluator_actor_id
                    or assessment_row.artifact_id != _assessment_prefix(
                        member_case.digest, selected.profile_id,
                    ) + selected.digest[7:] + "@v1"
                ):
                    raise IntegrityError("EXPERIENCE_REVISION_ASSESSMENT_BINDING_INVALID")
                labels.append(selected.verdict)
                refs.append(assessment_row.artifact_id)
                if sequence < latest_sequence and selected.verdict == "COUNTEREXAMPLE":
                    historical_counters.append(assessment_row.artifact_id)
                if sequence == latest_sequence and selected.rubric_version == self.rubric_version:
                    latest_refs.append((selected.evaluated_at, assessment_row.artifact_id, selected))
            members.append({
                "case_ref": row.artifact_id,
                "case_digest": member_case.digest,
                "case_id": member_case.case_id,
                "case_revision": member_case.revision,
                "cluster_id": member_case.independence_cluster_id,
                "event_sequence_no": sequence,
                "assessment_refs": tuple(sorted(refs)),
                "assessment_verdicts": tuple(sorted(set(labels))),
                "disposition": "SELECTED" if sequence == latest_sequence else "OLDER_REVISION_EXCLUDED",
            })
        if not latest_refs:
            raise IntegrityError("EXPERIENCE_REVISION_LATEST_UNASSESSED")
        if len({item[2].verdict for item in latest_refs}) > 1 or any(
            item[2].verdict == "DISPUTED" for item in latest_refs
        ):
            raise IntegrityError("EXPERIENCE_ASSESSMENT_DISPUTED")
        chosen = max(latest_refs, key=lambda item: (item[0], item[1]))
        if assessment_ref != chosen[1]:
            raise IntegrityError("EXPERIENCE_REVISION_SELECTION_STALE")
        repair_required = chosen[2].verdict == "SUPPORT" and bool(historical_counters)
        if repair_required:
            latest_case = related[-1][1]
            old_evidence = {
                value for _, old_case, sequence in related if sequence < latest_sequence
                for value in (old_case.resume_digest, old_case.outcome_artifact_digest)
                if value is not None
            }
            repair_evidence = {
                value for value in (latest_case.resume_digest, latest_case.outcome_artifact_digest)
                if value is not None and value not in old_evidence
            }
            if (
                chosen[2].reason_code != "REPAIR_VERIFIED"
                or not repair_evidence.intersection(chosen[2].evidence_refs)
            ):
                raise IntegrityError("EXPERIENCE_REVISION_REPAIR_UNPROVEN")
        selection = {
            "policy_version": REVISION_SELECTION_POLICY,
            "tenant_id": self.workspace.store.tenant_id,
            "workspace_id": self.workspace.store.workspace_id,
            "profile_id": receipt.profile_id,
            "cluster_id": case.independence_cluster_id,
            "selected_case_ref": related[-1][0].artifact_id,
            "selected_assessment_ref": chosen[1],
            "selected_verdict": chosen[2].verdict,
            "repair_required": repair_required,
            "historical_counter_refs": tuple(sorted(set(historical_counters))),
            "members": members,
            "member_digest": sha256_digest(members),
        }
        return {**selection, "digest": sha256_digest(selection)}

    def _verify_assessment(
        self, assessment_ref: str,
    ) -> tuple[CaseObservationV2, CaseAssessmentReceipt, IndependenceClusterProof | None]:
        receipt = CaseAssessmentReceipt.model_validate(
            self.workspace.store.load_artifact(assessment_ref, ASSESSMENT_MEDIA).payload,
        ).revalidated()
        case = self._case(receipt.case_ref)
        if (
            receipt.case_digest != case.digest
            or receipt.tenant_id != case.tenant_id
            or receipt.workspace_id != case.workspace_id
            or receipt.evaluator_id != self.evaluator_actor_id
            or receipt.rubric_version != self.rubric_version
            or not set(receipt.evidence_refs) <= _allowed_case_evidence(case)
            or assessment_ref != _assessment_prefix(case.digest, receipt.profile_id) + receipt.digest[7:] + "@v1"
        ):
            raise IntegrityError("EXPERIENCE_ASSESSMENT_BINDING_INVALID")
        if receipt.verdict in {"SUPPORT", "COUNTEREXAMPLE"}:
            if self.source_qualification_check is None:
                raise IntegrityError("EXPERIENCE_SOURCE_QUALIFICATION_REQUIRED")
            if (
                case.cluster_status not in {"PROVISIONAL", "PROVEN"}
                or receipt.independence_cluster_id != case.independence_cluster_id
            ):
                raise IntegrityError("EXPERIENCE_CLUSTER_PROOF_INVALID")
            self.source_qualification_check(case)
            proof = IndependenceClusterProof.model_validate(
                self.workspace.store.load_artifact(receipt.cluster_proof_ref, CLUSTER_PROOF_MEDIA).payload,
            ).revalidated()
            if (
                proof.digest != receipt.cluster_proof_digest
                or proof.case_ref != receipt.case_ref
                or proof.case_digest != receipt.case_digest
                or proof.profile_id != receipt.profile_id
                or proof.independence_cluster_id != receipt.independence_cluster_id
                or proof.issuer_id != receipt.evaluator_id
                or receipt.cluster_proof_ref != "experience-cluster-proof:" + proof.digest[7:] + "@v1"
                or proof.basis_ref_digests != tuple(sorted({
                    sha256_digest(ref) for ref in self.workspace.changes.get(case.business_event_id).proposal.source_refs
                }))
            ):
                raise IntegrityError("EXPERIENCE_CLUSTER_PROOF_INVALID")
        else:
            proof = None
        return case, receipt, proof

    def assessment_conflicts(self, *, case_ref: str, profile_id: str) -> tuple[str, ...]:
        """Show all judgments; disagreement cannot be silently cherry-picked."""

        self._authorize()
        case = self._case(case_ref)
        rows = self._assessment_rows(case, profile_id)
        verdicts = set()
        refs = []
        for row in rows:
            _, receipt, _ = self.read_assessment(row.artifact_id)
            verdicts.add(receipt.verdict)
            refs.append(row.artifact_id)
        return tuple(refs) if len(verdicts) > 1 else ()

    def assessment_projection_for_reader(
        self, case_ref: str, profile_id: str, *, max_records: int = 16,
    ) -> dict[str, Any]:
        """Bounded current read projection; incomplete pages never mean empty."""

        require_action(self.workspace, "read")
        if type(max_records) is not int or not 1 <= max_records <= 100:
            raise ValueError("EXPERIENCE_ASSESSMENT_PROJECTION_BOUND_INVALID")
        stored = self.workspace.store.load_artifact(case_ref, OBSERVATION_MEDIA)
        case = CaseObservationV2.model_validate(stored.payload).revalidated()
        if (
            case.tenant_id != self.workspace.store.tenant_id
            or case.workspace_id != self.workspace.store.workspace_id
            or case_ref != f"experience-case:{case.case_id}:{case.revision[7:]}@v2"
        ):
            raise IntegrityError("EXPERIENCE_CASE_SCOPE_INVALID")
        if is_case_source_held(self.workspace, case):
            return {
                "status": "HOLD", "verdict": None,
                "assessment_refs": (), "assessment_count_observed": 0,
                "coverage": "UNKNOWN",
            }
        rows, complete = self._assessment_page(case, profile_id, max_records=max_records)
        refs = tuple(row.artifact_id for row in rows)
        if not complete:
            return {
                "status": "PARTIAL", "verdict": None,
                "assessment_refs": refs, "assessment_count_observed": len(rows),
                "coverage": "PARTIAL_COVERAGE",
            }
        if not rows:
            return {
                "status": "UNASSESSED", "verdict": None,
                "assessment_refs": (), "assessment_count_observed": 0,
                "coverage": "COMPLETE",
            }
        receipts = [CaseAssessmentReceipt.model_validate(row.payload).revalidated() for row in rows]
        if any(receipt.case_ref != case_ref or receipt.profile_id != profile_id for receipt in receipts):
            raise IntegrityError("EXPERIENCE_ASSESSMENT_BINDING_INVALID")
        for row in rows:
            try:
                self._verify_assessment(row.artifact_id)
            except (IntegrityError, KeyError, ValueError, PermissionError):
                return {
                    "status": "HOLD", "verdict": None,
                    "assessment_refs": refs, "assessment_count_observed": len(rows),
                    "coverage": "COMPLETE",
                }
        verdicts = {receipt.verdict for receipt in receipts}
        if "DISPUTED" in verdicts or len(verdicts) > 1:
            return {
                "status": "DISPUTED", "verdict": None,
                "assessment_refs": refs, "assessment_count_observed": len(rows),
                "coverage": "COMPLETE",
            }
        selected_index = max(
            range(len(receipts)), key=lambda index: (receipts[index].evaluated_at, refs[index]),
        )
        try:
            self._revision_selection(
                refs[selected_index], case=case, receipt=receipts[selected_index],
            )
        except (IntegrityError, KeyError, ValueError, PermissionError):
            return {
                "status": "HOLD", "verdict": None,
                "assessment_refs": refs, "assessment_count_observed": len(rows),
                "coverage": "COMPLETE",
            }
        verdict = verdicts.pop()
        return {
            "status": verdict, "verdict": verdict,
            "assessment_refs": refs, "assessment_count_observed": len(rows),
            "coverage": "COMPLETE",
        }

    def assessment_summary_for_reader(self, *, case_ref: str, profile_id: str) -> dict[str, Any]:
        """Compatibility shorthand for internal low-sensitivity callers."""

        projection = self.assessment_projection_for_reader(case_ref, profile_id)
        return {
            "status": projection["status"],
            "assessment_count": projection["assessment_count_observed"],
        }
