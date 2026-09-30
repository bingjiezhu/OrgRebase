"""Authenticated production adapter for the persisted Pattern controller.

The legacy fixture/controller API remains unexposed.  This adapter derives all
actors from the current verified Principal and keeps author, evaluator, and
governor identities distinct.  Automatic adoption is disabled by default.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from orgrebase.auth import (
    AuthenticationError,
    JWTAuthenticator,
    Principal,
    authorize,
    current_authorization,
    request_authorization,
    request_principal,
)
from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError
from orgrebase.workspace.pattern_evolution import (
    CaseObservation,
    FeatureProfile,
    GovernedPatternService,
    ProposalBudget,
    SkillBoundary,
)
from orgrebase.workspace.quote_recovery_learning import (
    TARGET_SKILL,
    quote_recovery_content_bundle,
)
from orgrebase.workspace.skill_packages import InvocationContext


@dataclass(frozen=True)
class PatternAuthorityScope:
    tenant_id: str
    workspace_id: str
    corpus_actor_id: str
    author_actor_id: str
    evaluator_actor_id: str
    governor_actor_id: str
    reviewed_bundle_digest: str
    reviewed_target_skill: str
    reviewed_predecessor_package_digest: str

    def __post_init__(self) -> None:
        actors = (
            self.corpus_actor_id,
            self.author_actor_id,
            self.evaluator_actor_id,
            self.governor_actor_id,
        )
        if (
            not self.tenant_id
            or not self.workspace_id
            or not all(actors)
            or len(set(actors)) != 4
            or self.reviewed_target_skill != TARGET_SKILL
            or any(
                not isinstance(value, str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None
                for value in (
                    self.reviewed_bundle_digest,
                    self.reviewed_predecessor_package_digest,
                )
            )
        ):
            raise ValueError("PATTERN_PRODUCTION_AUTHORITY_SCOPE_INVALID")

    @property
    def digest(self) -> str:
        return sha256_digest(
            {
                "tenant_id": self.tenant_id,
                "workspace_id": self.workspace_id,
                "corpus_actor_id": self.corpus_actor_id,
                "author_actor_id": self.author_actor_id,
                "evaluator_actor_id": self.evaluator_actor_id,
                "governor_actor_id": self.governor_actor_id,
                "reviewed_bundle_digest": self.reviewed_bundle_digest,
                "reviewed_target_skill": self.reviewed_target_skill,
                "reviewed_predecessor_package_digest": (
                    self.reviewed_predecessor_package_digest
                ),
            }
        )


@dataclass(frozen=True)
class PatternAdoptionPolicy:
    enabled: bool = False
    candidate_refs: tuple[str, ...] = ()

    def permits(self, candidate_ref: str) -> bool:
        return self.enabled and candidate_ref in self.candidate_refs


class PrincipalPatternGovernance:
    """Bind controller capabilities to the current authenticated request."""

    def __init__(
        self,
        service: GovernedPatternService,
        scope: PatternAuthorityScope,
        *,
        adoption: PatternAdoptionPolicy | None = None,
    ) -> None:
        if (
            service.store.tenant_id not in {None, scope.tenant_id}
            or service.store.workspace_id != scope.workspace_id
            or service.corpus_authority != scope.corpus_actor_id
            or service.evaluator_authority != scope.evaluator_actor_id
            or service.governance_authority != scope.governor_actor_id
        ):
            raise ValueError("PATTERN_PRODUCTION_SERVICE_SCOPE_MISMATCH")
        reviewed = quote_recovery_content_bundle(service.registry)
        predecessor = service.registry.load(TARGET_SKILL).package_digest
        if (
            scope.reviewed_bundle_digest != reviewed.digest
            or scope.reviewed_target_skill != TARGET_SKILL
            or scope.reviewed_predecessor_package_digest != predecessor
            or reviewed.payload["predecessor_package_digest"] != predecessor
        ):
            raise ValueError("PATTERN_PRODUCTION_REVIEWED_CONTENT_MISMATCH")
        self.service = service
        self.scope = scope
        self.adoption = adoption or PatternAdoptionPolicy()

    def _principal(self, actor_id: str, action: str) -> Principal:
        check = current_authorization()
        if check is not None:
            check()
        principal = request_principal.get()
        if principal is None:
            raise AuthenticationError("PATTERN_VERIFIED_PRINCIPAL_REQUIRED", 401)
        authorize(principal, action, self.scope.tenant_id)
        if principal.actor_id != actor_id:
            raise AuthorizationError("PATTERN_PRINCIPAL_ROLE_SEPARATION_REQUIRED")
        return principal

    def _reauthorize(self, actor_id: str, action: str) -> None:
        self._principal(actor_id, action)

    def _binding(self, principal: Principal, role_phase: str) -> dict[str, Any]:
        return {
            "schema_version": "orgrebase.pattern-principal-binding.v1",
            "identity_mode": "VERIFIED_PRINCIPAL_IDENTITY",
            "issuer": principal.issuer,
            "issuer_digest": sha256_digest(principal.issuer),
            "subject": principal.subject,
            "subject_digest": sha256_digest(principal.subject),
            "tenant_id": principal.tenant_id,
            "workspace_id": self.scope.workspace_id,
            "actor_id": principal.actor_id,
            "expires_at": principal.expires_at,
            "scope_digest": self.scope.digest,
            "role_phase": role_phase,
        }

    def _verified_binding(
        self,
        record: dict[str, Any],
        *,
        actor_id: str,
        role_phase: str,
    ) -> dict[str, Any]:
        binding = self.service._phase_principal_binding(
            record.get("principal_binding"),
            actor_id=actor_id,
            role_phase=role_phase,
        )
        if binding is None or binding["scope_digest"] != self.scope.digest:
            raise AuthorizationError("PATTERN_PRODUCTION_CHAIN_REQUIRED")
        return binding

    def _require_verified_chain(
        self,
        candidate_ref: str,
        *,
        evaluation_ref: str | None = None,
        governor_binding: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        candidate = self.service._load(candidate_ref, "skill-candidate")
        proposal = self.service._load(candidate["proposal_ref"], "proposal")
        corpus = self.service._load(candidate["corpus_ref"], "corpus")
        if (
            proposal["corpus_ref"] != candidate["corpus_ref"]
            or proposal["replay_ref"] != candidate["replay_ref"]
            or proposal["author_id"] != self.scope.author_actor_id
            or proposal["skill_name"] != self.scope.reviewed_target_skill
            or proposal["base_package_digest"]
            != self.scope.reviewed_predecessor_package_digest
            or candidate["author_id"] != self.scope.author_actor_id
            or candidate["skill_name"] != self.scope.reviewed_target_skill
            or candidate["base_package_digest"]
            != self.scope.reviewed_predecessor_package_digest
            or candidate.get("content_bundle", {}).get("digest")
            != self.scope.reviewed_bundle_digest
            or candidate.get("content_bundle")
            != quote_recovery_content_bundle(self.service.registry).payload
            or corpus["issuer"] != self.scope.corpus_actor_id
        ):
            raise AuthorizationError("PATTERN_PRODUCTION_CHAIN_REQUIRED")
        corpus_binding = self._verified_binding(
            corpus,
            actor_id=self.scope.corpus_actor_id,
            role_phase="CORPUS_FREEZE",
        )
        proposal_binding = self._verified_binding(
            proposal,
            actor_id=self.scope.author_actor_id,
            role_phase="PROPOSAL_OPEN",
        )
        candidate_binding = self._verified_binding(
            candidate,
            actor_id=self.scope.author_actor_id,
            role_phase="CANDIDATE_AUTHOR",
        )
        if (
            proposal_binding["issuer"],
            proposal_binding["subject"],
            proposal_binding["actor_id"],
        ) != (
            candidate_binding["issuer"],
            candidate_binding["subject"],
            candidate_binding["actor_id"],
        ):
            raise AuthorizationError("PATTERN_PRODUCTION_AUTHOR_IDENTITY_CHANGED")
        result = {
            "candidate": candidate,
            "proposal": proposal,
            "corpus": corpus,
            "corpus_binding": corpus_binding,
            "proposal_binding": proposal_binding,
            "candidate_binding": candidate_binding,
        }
        identities = {
            (corpus_binding["issuer"], corpus_binding["subject"]),
            (candidate_binding["issuer"], candidate_binding["subject"]),
        }
        if len(identities) != 2:
            raise AuthorizationError("PATTERN_PRODUCTION_ROLE_IDENTITIES_NOT_DISTINCT")
        if evaluation_ref is not None:
            evaluation = self.service._load(evaluation_ref, "evaluation")
            if (
                evaluation["candidate_ref"] != candidate_ref
                or evaluation["candidate_digest"] != candidate["digest"]
                or evaluation["issuer"] != self.scope.evaluator_actor_id
                or evaluation["verdict"] != "QUALIFIED"
                or evaluation.get("business_oracle", {}).get("status") != "IMPROVED"
            ):
                raise AuthorizationError("PATTERN_PRODUCTION_EVALUATION_REQUIRED")
            evaluation_binding = self._verified_binding(
                evaluation,
                actor_id=self.scope.evaluator_actor_id,
                role_phase="INDEPENDENT_EVALUATOR",
            )
            identities.add((evaluation_binding["issuer"], evaluation_binding["subject"]))
            if len(identities) != 3:
                raise AuthorizationError("PATTERN_PRODUCTION_ROLE_IDENTITIES_NOT_DISTINCT")
            result.update(
                evaluation=evaluation,
                evaluation_binding=evaluation_binding,
                evaluation_ref=evaluation_ref,
            )
        if governor_binding is not None:
            if governor_binding["scope_digest"] != self.scope.digest:
                raise AuthorizationError("PATTERN_PRODUCTION_CHAIN_REQUIRED")
            identities.add((governor_binding["issuer"], governor_binding["subject"]))
            if len(identities) != 4:
                raise AuthorizationError("PATTERN_PRODUCTION_ROLE_IDENTITIES_NOT_DISTINCT")
            result["governor_binding"] = governor_binding
        if evaluation_ref is not None and governor_binding is not None:
            chain_body = {
                "schema_version": "orgrebase.pattern-production-chain.v1",
                "scope_digest": self.scope.digest,
                "reviewed_bundle_digest": self.scope.reviewed_bundle_digest,
                "reviewed_target_skill": self.scope.reviewed_target_skill,
                "reviewed_predecessor_package_digest": (
                    self.scope.reviewed_predecessor_package_digest
                ),
                "corpus_ref": candidate["corpus_ref"],
                "corpus_digest": corpus["digest"],
                "corpus_principal": corpus_binding,
                "proposal_ref": candidate["proposal_ref"],
                "proposal_digest": proposal["digest"],
                "proposal_principal": proposal_binding,
                "candidate_ref": candidate_ref,
                "candidate_digest": candidate["digest"],
                "candidate_principal": candidate_binding,
                "evaluation_ref": evaluation_ref,
                "evaluation_digest": result["evaluation"]["digest"],
                "evaluation_principal": result["evaluation_binding"],
                "governor_principal": governor_binding,
            }
            result["production_chain_digest"] = sha256_digest(chain_body)
        return result

    def _require_verified_release_chain(self, candidate_ref: str) -> dict[str, Any]:
        decisions = [
            item
            for item in self.service._family("decision")
            if item["candidate_ref"] == candidate_ref and item["verdict"] == "ADMIT"
        ]
        admissions = [
            item
            for item in self.service._family("admission")
            if item["candidate_ref"] == candidate_ref
        ]
        if len(decisions) != 1 or len(admissions) != 1:
            raise AuthorizationError("PATTERN_PRODUCTION_CHAIN_REQUIRED")
        decision, admission = decisions[0], admissions[0]
        governor_binding = self._verified_binding(
            decision,
            actor_id=self.scope.governor_actor_id,
            role_phase="RELEASE_GOVERNOR",
        )
        chain = self._require_verified_chain(
            candidate_ref,
            evaluation_ref=decision["evaluation_ref"],
            governor_binding=governor_binding,
        )
        source_id, version = admission["source_ref"].rsplit("@", 1)
        source = self.service.store.get_object(source_id, version)
        if (
            decision.get("authority_basis") != "VERIFIED_CURRENT_PRINCIPAL"
            or decision.get("production_chain_digest")
            != chain["production_chain_digest"]
            or admission["decision_ref"]
            != f"pattern-evolution:decision:{decision['digest'].removeprefix('sha256:')}"
            or source.state.value != "CURRENT"
            or source.digest != admission["source_digest"]
            or source.payload.get("candidate_ref") != candidate_ref
            or source.payload.get("decision_ref") != admission["decision_ref"]
            or source.payload.get("evaluation_ref") != decision["evaluation_ref"]
            or source.payload.get("production_chain_digest")
            != chain["production_chain_digest"]
            or source.payload.get("package_digest")
            != self.service._overlay(chain["candidate"])
            .load(chain["candidate"]["skill_name"])
            .package_digest
        ):
            raise AuthorizationError("PATTERN_PRODUCTION_RELEASE_CHAIN_INVALID")
        return {
            **chain,
            "decision": decision,
            "admission": admission,
            "source": source,
        }

    def _require_verified_restoration(self, restoration_ref: str) -> dict[str, Any]:
        restoration = self.service._load(restoration_ref, "restoration")
        restore_binding = self._verified_binding(
            restoration,
            actor_id=self.scope.governor_actor_id,
            role_phase="PREDECESSOR_RESTORE",
        )
        candidate_ref = restoration["candidate_ref"]
        decisions = [
            item
            for item in self.service._family("decision")
            if item["candidate_ref"] == candidate_ref and item["verdict"] == "ADMIT"
        ]
        if len(decisions) != 1:
            raise AuthorizationError("PATTERN_PRODUCTION_RESTORATION_INVALID")
        decision = decisions[0]
        governor_binding = self._verified_binding(
            decision,
            actor_id=self.scope.governor_actor_id,
            role_phase="RELEASE_GOVERNOR",
        )
        chain = self._require_verified_chain(
            candidate_ref,
            evaluation_ref=decision["evaluation_ref"],
            governor_binding=governor_binding,
        )
        predecessor = self.service.registry.load(self.scope.reviewed_target_skill)
        if (
            restoration["actor_id"] != self.scope.governor_actor_id
            or restoration["principal_binding"] != restore_binding
            or restoration.get("production_chain_digest")
            != chain["production_chain_digest"]
            or restoration["predecessor_package_digest"]
            != self.scope.reviewed_predecessor_package_digest
            or restoration["predecessor_package_digest"] != predecessor.package_digest
            or restoration["predecessor_resource_digests"]
            != predecessor.resource_digests
            or restoration["adoption_enabled"] is not False
            or self.service.evidence_status(candidate_ref)
            != "REQUALIFICATION_REQUIRED"
        ):
            raise AuthorizationError("PATTERN_PRODUCTION_RESTORATION_INVALID")
        return {**chain, "restoration": restoration, "restoration_binding": restore_binding}

    def freeze_corpus(
        self,
        profile: FeatureProfile,
        cases: tuple[CaseObservation, ...],
    ) -> str:
        principal = self._principal(self.scope.corpus_actor_id, "govern")
        return self.service.freeze_corpus(
            profile,
            cases,
            actor_id=self.scope.corpus_actor_id,
            principal_binding=self._binding(principal, "CORPUS_FREEZE"),
            authorization_check=lambda: self._reauthorize(
                self.scope.corpus_actor_id, "govern"
            ),
        )

    def freeze_corpus_v2(
        self, *, profile_id: str, assessment_refs: tuple[str, ...],
        assessment_service: Any,
    ) -> str:
        """Freeze independently reviewed cases under the corpus Principal."""
        principal = self._principal(self.scope.corpus_actor_id, "govern")
        return self.service.freeze_corpus_v2(
            profile_id=profile_id,
            assessment_refs=assessment_refs,
            assessment_service=assessment_service,
            actor_id=self.scope.corpus_actor_id,
            principal_binding=self._binding(principal, "CORPUS_FREEZE"),
            authorization_check=lambda: self._reauthorize(
                self.scope.corpus_actor_id, "govern"
            ),
        )

    def revalidate_corpus_v2(
        self, corpus_ref: str, *, assessment_service: Any,
    ) -> dict[str, Any]:
        self._principal(self.scope.corpus_actor_id, "govern")
        return self.service.revalidate_corpus_v2(
            corpus_ref, assessment_service=assessment_service,
            actor_id=self.scope.corpus_actor_id,
            authorization_check=lambda: self._reauthorize(
                self.scope.corpus_actor_id, "govern"
            ),
        )

    def open_proposal(
        self,
        *,
        proposal_id: str,
        corpus_ref: str,
        replay_ref: str,
        skill_name: str,
        budget: ProposalBudget,
    ) -> str:
        principal = self._principal(self.scope.author_actor_id, "propose")
        if skill_name != self.scope.reviewed_target_skill:
            raise AuthorizationError("PATTERN_PRODUCTION_REVIEWED_CONTENT_REQUIRED")
        corpus = self.service._load(corpus_ref, "corpus")
        corpus_binding = self._verified_binding(
            corpus,
            actor_id=self.scope.corpus_actor_id,
            role_phase="CORPUS_FREEZE",
        )
        if (
            corpus["issuer"] != self.scope.corpus_actor_id
            or (corpus_binding["issuer"], corpus_binding["subject"])
            == (principal.issuer, principal.subject)
        ):
            raise AuthorizationError("PATTERN_PRODUCTION_ROLE_IDENTITIES_NOT_DISTINCT")
        return self.service.open_proposal(
            proposal_id=proposal_id,
            corpus_ref=corpus_ref,
            replay_ref=replay_ref,
            skill_name=skill_name,
            author_id=self.scope.author_actor_id,
            budget=budget,
            principal_binding=self._binding(principal, "PROPOSAL_OPEN"),
            authorization_check=lambda: self._reauthorize(
                self.scope.author_actor_id, "propose"
            ),
        )

    def propose(
        self,
        proposal_ref: str,
        *,
        boundary: SkillBoundary,
        content_bundle: dict[str, Any] | None = None,
    ) -> tuple[str, ...]:
        principal = self._principal(self.scope.author_actor_id, "propose")
        reviewed = quote_recovery_content_bundle(self.service.registry)
        if (
            content_bundle is None
            or content_bundle != reviewed.payload
            or content_bundle.get("digest") != self.scope.reviewed_bundle_digest
            or content_bundle.get("target_skill") != self.scope.reviewed_target_skill
            or content_bundle.get("predecessor_package_digest")
            != self.scope.reviewed_predecessor_package_digest
        ):
            raise AuthorizationError("PATTERN_PRODUCTION_REVIEWED_CONTENT_REQUIRED")
        proposal = self.service._load(proposal_ref, "proposal")
        corpus = self.service._load(proposal["corpus_ref"], "corpus")
        proposal_binding = self._verified_binding(
            proposal,
            actor_id=self.scope.author_actor_id,
            role_phase="PROPOSAL_OPEN",
        )
        corpus_binding = self._verified_binding(
            corpus,
            actor_id=self.scope.corpus_actor_id,
            role_phase="CORPUS_FREEZE",
        )
        if (
            proposal["skill_name"] != self.scope.reviewed_target_skill
            or proposal["base_package_digest"]
            != self.scope.reviewed_predecessor_package_digest
            or proposal_binding["issuer"] != principal.issuer
            or proposal_binding["subject"] != principal.subject
            or (corpus_binding["issuer"], corpus_binding["subject"])
            == (principal.issuer, principal.subject)
        ):
            raise AuthorizationError("PATTERN_PRODUCTION_CHAIN_REQUIRED")
        return self.service.propose(
            proposal_ref,
            actor_id=self.scope.author_actor_id,
            boundary=boundary,
            content_bundle=content_bundle,
            principal_binding=self._binding(principal, "CANDIDATE_AUTHOR"),
            authorization_check=lambda: self._reauthorize(
                self.scope.author_actor_id, "propose"
            ),
        )

    def evaluate(self, candidate_ref: str) -> str:
        principal = self._principal(self.scope.evaluator_actor_id, "govern")
        self._require_verified_chain(candidate_ref)
        return self.service.evaluate(
            candidate_ref,
            actor_id=self.scope.evaluator_actor_id,
            principal_binding=self._binding(principal, "INDEPENDENT_EVALUATOR"),
            authorization_check=lambda: self._reauthorize(
                self.scope.evaluator_actor_id, "govern"
            ),
        )

    def decide(
        self,
        candidate_ref: str,
        evaluation_ref: str,
        *,
        verdict: Literal["ADMIT", "REJECT"],
        expected_candidate_digest: str,
        expected_head_package_digest: str,
        idempotency_key: str,
    ) -> str:
        principal = self._principal(self.scope.governor_actor_id, "govern")
        if expected_head_package_digest != self.scope.reviewed_predecessor_package_digest:
            raise AuthorizationError("PATTERN_PRODUCTION_REVIEWED_CONTENT_REQUIRED")
        binding = self._binding(principal, "RELEASE_GOVERNOR")
        prior_decisions = [
            item
            for item in self.service._family("decision")
            if item["candidate_ref"] == candidate_ref
        ]
        if len(prior_decisions) == 1:
            prior = prior_decisions[0]
            prior_binding = prior.get("principal_binding")
            if (
                prior.get("idempotency_key") == idempotency_key
                and prior.get("evaluation_ref") == evaluation_ref
                and prior.get("verdict") == verdict
                and isinstance(prior_binding, dict)
                and prior_binding.get("issuer") == principal.issuer
                and prior_binding.get("subject") == principal.subject
                and prior_binding.get("actor_id") == principal.actor_id
            ):
                binding = prior_binding
        chain = self._require_verified_chain(
            candidate_ref,
            evaluation_ref=evaluation_ref,
            governor_binding=binding,
        )
        return self.service.decide(
            candidate_ref,
            evaluation_ref,
            actor_id=self.scope.governor_actor_id,
            verdict=verdict,
            expected_candidate_digest=expected_candidate_digest,
            expected_head_package_digest=expected_head_package_digest,
            authorization_check=lambda: self._reauthorize(
                self.scope.governor_actor_id, "govern"
            ),
            principal_binding=binding,
            idempotency_key=idempotency_key,
            production_chain_digest=chain["production_chain_digest"],
        )

    def migrate_legacy_head(
        self, *, expected_source_ref: str, expected_source_digest: str,
        expected_package_digest: str, maintenance_fence: Callable[[], None],
        maintenance_window_digest: str,
    ) -> str:
        """Govern one exact S05 v1 upgrade while the deployment is fenced."""
        principal = self._principal(self.scope.governor_actor_id, "govern")
        if self.adoption.enabled:
            raise AuthorizationError("PATTERN_MAINTENANCE_ADOPTION_MUST_BE_OFF")
        return self.service.migrate_legacy_head(
            self.scope.reviewed_target_skill,
            actor_id=self.scope.governor_actor_id,
            expected_source_ref=expected_source_ref,
            expected_source_digest=expected_source_digest,
            expected_package_digest=expected_package_digest,
            maintenance_fence=maintenance_fence,
            authorization_check=lambda: self._reauthorize(
                self.scope.governor_actor_id, "govern"
            ),
            principal_binding=self._binding(principal, "LEGACY_MIGRATION"),
            maintenance_window_digest=maintenance_window_digest,
        )

    def restore_direct_parent(
        self, *, reason: str, expected_head_ref: str,
        expected_head_digest: str, expected_generation: int,
        expected_package_digest: str, qualification_check: Callable[[], None],
        maintenance_window_digest: str,
    ) -> str:
        """Govern a disabled, exact-CAS rollback to the direct version parent."""
        principal = self._principal(self.scope.governor_actor_id, "govern")
        if self.adoption.enabled:
            raise AuthorizationError("PATTERN_MAINTENANCE_ADOPTION_MUST_BE_OFF")
        return self.service.restore_direct_parent(
            self.scope.reviewed_target_skill,
            actor_id=self.scope.governor_actor_id,
            reason=reason,
            expected_head_ref=expected_head_ref,
            expected_head_digest=expected_head_digest,
            expected_generation=expected_generation,
            expected_package_digest=expected_package_digest,
            qualification_check=qualification_check,
            authorization_check=lambda: self._reauthorize(
                self.scope.governor_actor_id, "govern"
            ),
            principal_binding=self._binding(principal, "DIRECT_PARENT_RESTORE"),
            maintenance_window_digest=maintenance_window_digest,
        )

    def invoke(
        self,
        candidate_ref: str,
        public_input: dict[str, Any],
        *,
        context: InvocationContext,
        knowledge_refs: tuple[str, ...],
        qualification_refs: tuple[str, ...],
    ):
        principal = self._principal(context.actor_id, "read")
        if principal.actor_id != context.actor_id:
            raise AuthorizationError("PATTERN_INVOCATION_ACTOR_MISMATCH")
        if not self.adoption.permits(candidate_ref):
            raise AuthorizationError("PATTERN_PRODUCTION_ADOPTION_DISABLED")
        chain = self._require_verified_release_chain(candidate_ref)
        authority = self.service._issue_production_invocation_authority(
            candidate_ref=candidate_ref,
            reviewed_bundle_digest=self.scope.reviewed_bundle_digest,
            production_chain_digest=chain["production_chain_digest"],
            scope_digest=self.scope.digest,
        )
        return self.service.invoke(
            candidate_ref,
            public_input,
            context=context,
            knowledge_refs=knowledge_refs,
            qualification_refs=qualification_refs,
            authorization_check=lambda: self._reauthorize(context.actor_id, "read"),
            production_authority=authority,
        )

    def status(self, candidate_ref: str) -> dict[str, Any]:
        """Return a read-only projection of exact qualification and adoption state."""

        current = request_principal.get()
        self._principal(current.actor_id if current else "", "read")
        candidate = self.service._load(candidate_ref, "skill-candidate")
        evaluations = [
            item
            for item in self.service._family("evaluation")
            if item["candidate_ref"] == candidate_ref
        ]
        decisions = [
            item
            for item in self.service._family("decision")
            if item["candidate_ref"] == candidate_ref
        ]
        admissions = [
            item
            for item in self.service._family("admission")
            if item["candidate_ref"] == candidate_ref
        ]
        try:
            self._require_verified_release_chain(candidate_ref)
            production_chain_status = "VERIFIED"
        except AuthorizationError:
            production_chain_status = "LEGACY_OR_INCOMPLETE_NOT_ADOPTABLE"
        return {
            "schema_version": "orgrebase.pattern-governance-status.v1",
            "candidate_ref": candidate_ref,
            "candidate_digest": candidate["digest"],
            "skill_name": candidate["skill_name"],
            "content_bundle_digest": (
                candidate.get("content_bundle", {}).get("digest")
                if candidate.get("content_bundle")
                else None
            ),
            "evaluation_refs": [
                f"pattern-evolution:evaluation:{item['digest'].removeprefix('sha256:')}"
                for item in evaluations
            ],
            "decision_count": len(decisions),
            "admission_count": len(admissions),
            "evidence_status": self.service.evidence_status(candidate_ref),
            "current_head_package_digest": self.service.current_skill_head_package_digest(
                candidate["skill_name"]
            ),
            "configured_adoption": self.adoption.permits(candidate_ref),
            "automatic_adoption": (
                self.adoption.permits(candidate_ref)
                and production_chain_status == "VERIFIED"
            ),
            "production_chain_status": production_chain_status,
            "actual_steward_operation": (
                "AUTHENTICATED_MECHANISM_ONLY"
                if production_chain_status == "VERIFIED"
                else "NOT_RUN"
            ),
            "canonical_writes": 0,
            "target_writes": 0,
        }

    def invoke_restored_predecessor(
        self,
        restoration_ref: str,
        public_input: dict[str, Any],
        *,
        context: InvocationContext,
    ):
        principal = self._principal(context.actor_id, "read")
        if principal.actor_id != context.actor_id:
            raise AuthorizationError("PATTERN_INVOCATION_ACTOR_MISMATCH")
        self._require_verified_restoration(restoration_ref)
        return self.service.invoke_restored_predecessor(
            restoration_ref,
            public_input,
            context=context,
            authorization_check=lambda: self._reauthorize(context.actor_id, "read"),
        )

    def withdraw_and_restore_predecessor(
        self,
        candidate_ref: str,
        *,
        expected_predecessor_digest: str,
        reason: str,
    ) -> tuple[str, str]:
        principal = self._principal(self.scope.governor_actor_id, "govern")
        chain = self._require_verified_release_chain(candidate_ref)
        retraction = self.service.retract(
            candidate_ref,
            actor_id=self.scope.governor_actor_id,
            reason=reason,
        )
        restoration = self.service.restore_predecessor(
            candidate_ref,
            actor_id=self.scope.governor_actor_id,
            expected_predecessor_digest=expected_predecessor_digest,
            reason=reason,
            authorization_check=lambda: self._reauthorize(
                self.scope.governor_actor_id, "govern"
            ),
            principal_binding=self._binding(principal, "PREDECESSOR_RESTORE"),
            production_chain_digest=chain["production_chain_digest"],
        )
        return retraction, restoration


class PatternDecisionConfig(BaseModel):
    """Private deployment descriptor for one exact steward decision."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["orgrebase.pattern-governance-decision.v1"]
    workspace_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    access_token_variable: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
    enabled: bool = False
    corpus_actor_id: str = Field(min_length=1, max_length=256)
    author_actor_id: str = Field(min_length=1, max_length=256)
    evaluator_actor_id: str = Field(min_length=1, max_length=256)
    governor_actor_id: str = Field(min_length=1, max_length=256)
    reviewed_bundle_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    reviewed_target_skill: Literal["structured-domain-handoff"]
    reviewed_predecessor_package_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    candidate_ref: str = Field(min_length=1, max_length=512)
    evaluation_ref: str = Field(min_length=1, max_length=512)
    verdict: Literal["ADMIT", "REJECT"]
    expected_candidate_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    expected_head_package_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")

    @property
    def digest(self) -> str:
        return sha256_digest(self.model_dump(mode="json"))


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("PATTERN_GOVERNANCE_CONFIG_DUPLICATE_KEY")
        result[key] = value
    return result


def load_pattern_decision_config(path: Path) -> PatternDecisionConfig:
    """Read a bounded regular config without following a symbolic link."""

    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or info.st_size > 65_536:
                raise ValueError("PATTERN_GOVERNANCE_CONFIG_INVALID")
            raw = stream.read(65_537)
        return PatternDecisionConfig.model_validate(
            json.loads(raw, object_pairs_hook=_unique_object)
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("PATTERN_GOVERNANCE_"):
            raise
        raise ValueError("PATTERN_GOVERNANCE_CONFIG_INVALID") from exc


def run_pattern_decision(config_path: Path) -> dict[str, Any]:
    """Apply one exact authenticated decision; no candidate creation or auto-adoption."""

    from orgrebase.runtime_config import DeploymentSettings, open_workspace

    config = load_pattern_decision_config(config_path)
    if not config.enabled:
        raise ValueError("PATTERN_GOVERNANCE_DECISION_DISABLED")
    settings = DeploymentSettings.from_environment()
    if settings.mode != "production" or settings.identity is None:
        raise ValueError("PATTERN_GOVERNANCE_PRODUCTION_IDENTITY_REQUIRED")
    settings = settings.for_workspace(config.workspace_id)
    authenticator = JWTAuthenticator(settings.identity)
    principal = authenticator.authenticate(
        f"Bearer {os.environ.get(config.access_token_variable, '')}"
    )
    authorize(principal, "govern", settings.identity.tenant_id)
    settings.authorize_workspace(principal.subject)
    if principal.actor_id != config.governor_actor_id:
        raise AuthorizationError("PATTERN_PRINCIPAL_ROLE_SEPARATION_REQUIRED")

    def check_authorization() -> None:
        renewed = authenticator.authenticate(
            f"Bearer {os.environ.get(config.access_token_variable, '')}"
        )
        if (
            renewed.issuer,
            renewed.subject,
            renewed.tenant_id,
            renewed.actor_id,
        ) != (
            principal.issuer,
            principal.subject,
            principal.tenant_id,
            principal.actor_id,
        ):
            raise AuthenticationError("PATTERN_GOVERNANCE_IDENTITY_CHANGED", 403)
        authorize(renewed, "govern", settings.identity.tenant_id)
        settings.authorize_workspace(renewed.subject)
        if load_pattern_decision_config(config_path).digest != config.digest:
            raise ValueError("PATTERN_GOVERNANCE_CONFIG_CHANGED")
        request_principal.set(renewed)

    workspace = open_workspace(settings)
    authorization_token = request_authorization.set(check_authorization)
    principal_token = request_principal.set(principal)
    try:
        service = GovernedPatternService(
            workspace.store,
            corpus_authority=config.corpus_actor_id,
            evaluator_authority=config.evaluator_actor_id,
            governance_authority=config.governor_actor_id,
        )
        controller = PrincipalPatternGovernance(
            service,
            PatternAuthorityScope(
                tenant_id=settings.identity.tenant_id,
                workspace_id=config.workspace_id,
                corpus_actor_id=config.corpus_actor_id,
                author_actor_id=config.author_actor_id,
                evaluator_actor_id=config.evaluator_actor_id,
                governor_actor_id=config.governor_actor_id,
                reviewed_bundle_digest=config.reviewed_bundle_digest,
                reviewed_target_skill=config.reviewed_target_skill,
                reviewed_predecessor_package_digest=(
                    config.reviewed_predecessor_package_digest
                ),
            ),
        )
        decision_ref = controller.decide(
            config.candidate_ref,
            config.evaluation_ref,
            verdict=config.verdict,
            expected_candidate_digest=config.expected_candidate_digest,
            expected_head_package_digest=config.expected_head_package_digest,
            idempotency_key=config.idempotency_key,
        )
        decision = service._load(decision_ref, "decision")
        return {
            "schema_version": "orgrebase.pattern-governance-command-result.v1",
            "workspace_id": config.workspace_id,
            "decision_ref": decision_ref,
            "verdict": config.verdict,
            "policy_digest": config.digest,
            "reviewed_bundle_digest": config.reviewed_bundle_digest,
            "production_chain_digest": decision["production_chain_digest"],
            "automatic_adoption": False,
            "actual_steward_operation": (
                "AUTHENTICATED_PRINCIPAL_COMMAND_COMPLETED_NOT_REVIEW_COMPREHENSION"
            ),
        }
    finally:
        request_principal.reset(principal_token)
        request_authorization.reset(authorization_token)
        workspace.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply one exact authenticated Pattern decision")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(run_pattern_decision(args.config), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main(argv)
    raise SystemExit(main())
