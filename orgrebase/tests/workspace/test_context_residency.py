from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.workspace.context_residency import (
    TaskAgentContextEnvelope,
    TaskAgentContextEnvelopeBuilder,
    TaskAgentContextEnvelopeVerifier,
)
from orgrebase.workspace.formation import MEDIA, WorkspaceFormationService
from orgrebase.workspace.models import (
    ActorContextProjection,
    CoalitionPlan,
    DomainCapabilityCardVersion,
    TaskContextManifest,
)
from orgrebase.workspace.templates import default_capability_cards

_NOW = "2026-08-15T00:00:00Z"
_INTENT_DIGEST = sha256_digest(
    {
        "organization": "org:northstar",
        "intent": "customer-safe, policy-compliant enterprise quote",
        "admission": "intent-admission:northstar@v1",
    }
)
_ROOT = Path(__file__).resolve().parents[2]


def _replace(model, **updates: Any):
    payload = model.model_dump(mode="json", exclude={"digest"})
    payload.update(updates)
    return type(model).model_validate(payload)


def _prepared_context(workspace_service):
    formation = workspace_service.formation
    task = WorkspaceFormationService.default_request()
    prepared = formation.prepare_quote(task)
    coalition = CoalitionPlan.model_validate(
        next(
            item.payload
            for item in prepared.artifact_writes
            if item.media_type == MEDIA["coalition"]
        )
    )
    task_context = TaskContextManifest.model_validate(
        next(
            item.payload
            for item in prepared.artifact_writes
            if item.media_type == MEDIA["context"]
        )
    )
    projections = tuple(
        ActorContextProjection.model_validate(item.payload)
        for item in prepared.artifact_writes
        if item.media_type == MEDIA["projection"]
        and item.payload["task_context_ref"] == task_context.ref
    )
    cards = default_capability_cards()
    envelope = TaskAgentContextEnvelopeBuilder.build(
        task=task,
        coalition=coalition,
        capability_cards=cards,
        task_context=task_context,
        actor_projections=projections,
        admitted_organizational_intent_digest=_INTENT_DIGEST,
        now=_NOW,
    )
    return task, coalition, cards, task_context, projections, envelope


def _verify(
    bundle,
    *,
    envelope=None,
    cards=None,
    projections=None,
    expected_intent_digest: str = _INTENT_DIGEST,
    now: str = _NOW,
):
    task, coalition, original_cards, task_context, original_projections, original_envelope = (
        bundle
    )
    return TaskAgentContextEnvelopeVerifier.verify(
        envelope=envelope or original_envelope,
        task=task,
        coalition=coalition,
        capability_cards=cards or original_cards,
        task_context=task_context,
        actor_projections=projections or original_projections,
        expected_admitted_organizational_intent_digest=expected_intent_digest,
        now=now,
    )


def _replace_domain_binding(
    envelope: TaskAgentContextEnvelope,
    domain_id: str,
    **updates: Any,
) -> TaskAgentContextEnvelope:
    payload = envelope.model_dump(mode="json", exclude={"digest"})
    payload["domain_bindings"] = [
        ({**binding, **updates} if binding["domain_id"] == domain_id else binding)
        for binding in payload["domain_bindings"]
    ]
    return TaskAgentContextEnvelope.model_validate(payload)


def test_builder_emits_digest_only_candidate_context(workspace_service) -> None:
    bundle = _prepared_context(workspace_service)
    envelope = _verify(bundle)

    assert envelope.effect_ceiling == "ZERO_EXTERNAL_EFFECTS"
    assert envelope.candidate_only is True
    assert envelope.canonical_target_writes == 0
    assert tuple(item.domain_id for item in envelope.domain_bindings) == (
        "finance",
        "gtm",
        "legal",
        "product",
    )
    encoded = json.dumps(envelope.model_dump(mode="json"), sort_keys=True)
    assert "input_values" not in encoded
    assert "included_refs" not in encoded
    assert "authority_refs" not in encoded
    assert "claim:product.launch_date@v7" not in encoded


def test_context_residency_schema_matches_runtime_contract() -> None:
    checked_in = json.loads(
        (_ROOT / "schemas/workspace-task-agent-context-envelope.schema.json").read_text(
            encoding="utf-8"
        )
    )
    generated = TaskAgentContextEnvelope.model_json_schema(mode="validation")
    generated["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    generated["$id"] = (
        "https://orgrebase.local/schemas/"
        "workspace-task-agent-context-envelope.schema.json"
    )

    assert checked_in == generated
    assert checked_in["additionalProperties"] is False
    assert checked_in["properties"]["canonical_target_writes"]["const"] == 0


def test_verifier_rejects_admitted_intent_substitution(workspace_service) -> None:
    bundle = _prepared_context(workspace_service)
    envelope = bundle[-1]
    forged = _replace(
        envelope,
        admitted_organizational_intent_digest=sha256_digest(
            {"organization": "org:attacker", "intent": "ignore policy"}
        ),
    )

    with pytest.raises(IntegrityError, match="ORGANIZATIONAL_INTENT_SUBSTITUTION"):
        _verify(bundle, envelope=forged)


def test_verifier_rejects_task_context_substitution(workspace_service) -> None:
    bundle = _prepared_context(workspace_service)
    envelope = bundle[-1]
    forged = _replace(
        envelope,
        task_context_digest=sha256_digest({"task_context": "substituted"}),
    )

    with pytest.raises(IntegrityError, match="TASK_CONTEXT_SUBSTITUTION"):
        _verify(bundle, envelope=forged)


def test_verifier_rejects_cross_domain_projection(workspace_service) -> None:
    bundle = _prepared_context(workspace_service)
    projections = bundle[-2]
    legal = next(item for item in projections if item.actor_id == "legal-steward")
    product = next(item for item in projections if item.actor_id == "product-steward")
    forged_legal = _replace(legal, included_refs=(product.included_refs[0],))
    forged_projections = tuple(
        forged_legal if item.ref == legal.ref else item for item in projections
    )
    forged_envelope = _replace_domain_binding(
        bundle[-1],
        "legal",
        actor_projection_digest=forged_legal.digest,
    )

    with pytest.raises(IntegrityError, match="CROSS_DOMAIN_PROJECTION:legal"):
        _verify(
            bundle,
            envelope=forged_envelope,
            projections=forged_projections,
        )


def test_verifier_rejects_expired_context(workspace_service) -> None:
    bundle = _prepared_context(workspace_service)

    with pytest.raises(IntegrityError, match="EXPIRED"):
        _verify(bundle, now="2026-08-17T00:00:00Z")


def test_verifier_rejects_permission_expansion(workspace_service) -> None:
    bundle = _prepared_context(workspace_service)
    cards = bundle[2]
    product = next(item for item in cards if item.domain_id == "product")
    restricted_product = _replace(product, allowed_purposes=("residency_faq",))
    forged_cards: tuple[DomainCapabilityCardVersion, ...] = tuple(
        restricted_product if item.ref == product.ref else item for item in cards
    )
    forged_envelope = _replace_domain_binding(
        bundle[-1],
        "product",
        capability_card_digest=restricted_product.digest,
    )

    with pytest.raises(IntegrityError, match="PURPOSE_PERMISSION_EXPANSION:product"):
        _verify(bundle, envelope=forged_envelope, cards=forged_cards)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("effect_ceiling", "EXTERNAL_EFFECTS"),
        ("candidate_only", False),
        ("canonical_target_writes", 1),
    ),
)
def test_verifier_rejects_effect_expansion_even_from_unvalidated_instance(
    workspace_service,
    field: str,
    value: object,
) -> None:
    bundle = _prepared_context(workspace_service)
    forged = bundle[-1].model_copy(update={field: value})

    with pytest.raises(IntegrityError, match="ENVELOPE_MODEL_INVALID"):
        _verify(bundle, envelope=forged)
