"""Least-sufficient context compiler and purpose-bound policy adapter."""

from __future__ import annotations

from orgrebase.domain import (
    AuthorizationError,
    ContextItem,
    ContextManifest,
    EvidenceClass,
    VersionedObject,
)
from orgrebase.fixture import EnterpriseFixture
from orgrebase.store import StateStore


class LocalPolicyAdapter:
    """Contract-compatible P0 replacement for remote OpenFGA/OPA services."""

    def __init__(self, fixture: EnterpriseFixture) -> None:
        self.fixture = fixture

    def decide(
        self,
        *,
        actor_id: str,
        item: VersionedObject,
        purpose: str,
        requested_as_premise: bool,
    ) -> tuple[bool, str]:
        profile = self.fixture.context_profiles.get(actor_id)
        if profile is None:
            return False, "UNKNOWN_PRINCIPAL"
        if purpose not in item.allowed_purposes:
            return False, "PURPOSE_NOT_ALLOWED"
        if item.sensitivity == "RESTRICTED" and actor_id not in {"legal-steward"}:
            return False, "SENSITIVITY_CEILING_EXCEEDED"
        if requested_as_premise and item.state.value in {
            "PROPOSED",
            "SUPERSEDED",
            "STALE",
        }:
            return False, "GOVERNANCE_STATE_NOT_USABLE"
        return True, "RELATION_AND_PURPOSE_ALLOWED"


class ContextCompiler:
    def __init__(self, fixture: EnterpriseFixture, store: StateStore) -> None:
        self.fixture = fixture
        self.store = store
        self.policy = LocalPolicyAdapter(fixture)

    @staticmethod
    def _safe_payload(item_id: str, payload: dict[str, object]) -> dict[str, object]:
        if item_id.startswith("source:legal"):
            return {"redacted": True}
        return payload

    def compile(self, actor_id: str, purpose: str = "change_rebase") -> ContextManifest:
        profile = self.fixture.context_profiles.get(actor_id)
        if profile is None:
            raise AuthorizationError(f"unknown context profile: {actor_id}")
        included: list[ContextItem] = []
        excluded: list[ContextItem] = []

        for object_id in profile["include"]:
            item = self.store.get_object(object_id)
            allowed, reason = self.policy.decide(
                actor_id=actor_id,
                item=item,
                purpose=purpose,
                requested_as_premise=True,
            )
            if not allowed:
                raise AuthorizationError(f"required context denied: {object_id}: {reason}")
            included.append(
                ContextItem(
                    object_ref=item.ref,
                    label=item.label,
                    disposition="INCLUDED_AS_PREMISE",
                    reason_code=reason,
                    payload=self._safe_payload(item.id, item.payload),
                )
            )

        for object_id in profile["explicitly_consider"]:
            item = self.store.get_object(object_id)
            allowed, reason = self.policy.decide(
                actor_id=actor_id,
                item=item,
                purpose=purpose,
                requested_as_premise=True,
            )
            excluded.append(
                ContextItem(
                    object_ref=item.ref,
                    label=item.label,
                    disposition="EXCLUDED",
                    reason_code=(reason if not allowed else "NOT_REQUIRED_FOR_TASK"),
                    payload=None,
                )
            )

        return ContextManifest(
            id=f"context:{actor_id}-launch-rebase",
            version="v1",
            actor_id=actor_id,
            target_object_id=profile["target"],
            purpose=purpose,
            graph_revision=self.fixture.revisions["graph"],
            policy_revision=self.fixture.revisions["policy"],
            authorization_revision=self.fixture.revisions["authorization"],
            expires_at="2026-09-16T00:00:00Z",
            included=tuple(included),
            excluded=tuple(excluded),
            evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
        )

    @staticmethod
    def validate_used_premises(manifest: ContextManifest, premise_refs: tuple[str, ...]) -> None:
        admitted = {item.object_ref for item in manifest.included}
        unsupported = sorted(set(premise_refs) - admitted)
        if unsupported:
            raise AuthorizationError(f"UNSUPPORTED_PREMISE: {', '.join(unsupported)}")
