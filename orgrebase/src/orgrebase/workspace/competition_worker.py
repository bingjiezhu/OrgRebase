"""Independent subprocess entry points for the Golden Competition Runtime.

Domain workers receive only their admitted projection after AgentTeams ACK.
The reviewer makes one explicitly selected Ollama or Vertex structured call and
returns a candidate-only advisory; neither subprocess can write canonical
Workspace state, and the deterministic reviewer remains authoritative.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from orgrebase.digest import sha256_digest
from orgrebase.workspace.deepseek_provider import DeepSeekStructuredProvider
from orgrebase.workspace.domain_agents import (
    DeterministicDomainProvider,
    LocalSourceValue,
)
from orgrebase.workspace.model_observations import ModelAttemptObserver, provider_observations
from orgrebase.workspace.model_provider import (
    VERTEX_MODEL_ID,
    LocalOllamaStructuredProvider,
    VertexAIStructuredProvider,
)
from orgrebase.workspace.models import (
    ActorContextProjection,
    ClaimCandidate,
    CoalitionPlan,
    DomainCandidateBundle,
    ModelRequest,
    SemanticKind,
    TaskRequest,
    TaskTemplateVersion,
)

DOMAINS = ("product", "legal", "finance", "gtm")
OLLAMA_MODEL_DIGEST = "357c53fb659c5076de1d65ccb0b397446227b71a42be9d1603d46168015c9e4b"
WORKERS = {
    "product": "product-steward",
    "legal": "legal-steward",
    "finance": "finance-steward",
    "gtm": "gtm-steward",
}
MODEL_PROVIDERS = ("ollama-local", "vertex-ai", "deepseek")

def reviewer_model_id(provider: str) -> str:
    return {"ollama-local": "qwen2.5:3b", "vertex-ai": VERTEX_MODEL_ID, "deepseek": "deepseek-flash"}[provider]


def _validated_plan_task_lineage(
    payload: dict[str, Any],
    *,
    expected_kind: str,
    expected_domain: str,
    expected_assignee: str,
    execution_projection: ActorContextProjection | None = None,
) -> dict[str, Any]:
    """Validate and return the sealed plan lineage when a plan is supplied."""

    plan_digest = payload.get("agentteams_execution_plan_digest")
    sealed = payload.get("sealed_plan_task")
    if plan_digest is None and sealed is None:
        return {}
    if not isinstance(plan_digest, str) or not isinstance(sealed, dict):
        raise ValueError("COMPETITION_WORKER_PLAN_BINDING_INCOMPLETE")
    logical_digest = payload.get("logical_plan_task_digest")
    sealed_digest = sealed.get("digest")
    admitted_projection: ActorContextProjection | None = None
    if expected_kind == "DOMAIN":
        try:
            admitted_projection = ActorContextProjection.model_validate(
                payload.get("admitted_actor_projection")
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("COMPETITION_WORKER_ADMITTED_PROJECTION_INVALID") from exc
    if (
        not isinstance(logical_digest, str)
        or sealed_digest != logical_digest
        or sha256_digest({key: value for key, value in sealed.items() if key != "digest"}) != logical_digest
        or payload.get("logical_plan_task_id") != sealed.get("task_id")
        or sealed.get("task_kind") != expected_kind
        or sealed.get("domain_id") != expected_domain
        or sealed.get("assignee_actor_id") != expected_assignee
        or sealed.get("context_envelope_digest") != payload.get("context_envelope_digest")
        or sealed.get("formation_receipt_id") != payload.get("formation_receipt_id")
        or sealed.get("formation_receipt_digest") != payload.get("formation_receipt_digest")
        or sealed.get("effect_ceiling") != "ZERO_EXTERNAL_EFFECTS"
        or sealed.get("candidate_only") is not True
        or sealed.get("canonical_target_writes") != 0
        or payload.get("candidate_only") is not True
        or payload.get("target_writes") != 0
        or (
            expected_kind == "DOMAIN"
            and (
                execution_projection is None
                or admitted_projection is None
                or sealed.get("actor_projection_ref") != admitted_projection.ref
                or sealed.get("actor_projection_digest") != admitted_projection.digest
                or execution_projection.actor_id != admitted_projection.actor_id
                or execution_projection.task_context_ref
                not in {
                    admitted_projection.task_context_ref,
                    f"{admitted_projection.task_context_ref}-pre-admission",
                }
                or execution_projection.purpose != admitted_projection.purpose
                or execution_projection.expires_at != admitted_projection.expires_at
                or not set(execution_projection.included_refs).issubset(admitted_projection.included_refs)
            )
        )
    ):
        raise ValueError("COMPETITION_WORKER_PLAN_BINDING_INVALID")
    return {
        "agentteams_execution_plan_digest": plan_digest,
        "context_envelope_digest": payload["context_envelope_digest"],
        "logical_plan_task_id": sealed["task_id"],
        "logical_plan_task_digest": logical_digest,
        "formation_receipt_id": sealed["formation_receipt_id"],
        "formation_receipt_digest": sealed["formation_receipt_digest"],
    }


class ReviewerDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["PASS", "REPLAN"]
    missing_domains: list[Literal["product", "legal", "finance", "gtm"]]
    reason_codes: list[str] = Field(min_length=1)


def _read(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("COMPETITION_PROCESS_INPUT_OBJECT_REQUIRED")
    return value


def _write(path: str | Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)


def produce_domain_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Produce a domain result from the projection bytes available at execution.

    This function deliberately derives the result only after loading the input;
    callers bind its input digest and output schema, never an expected output
    digest.  Missing projected source refs cause a real ``ABSTAIN``.
    """

    run_id = str(payload["run_id"])
    correlation_id = str(payload["correlation_id"])
    task_id = str(payload["task_id"])
    domain = str(payload["domain"])
    attempt = int(payload["attempt"])
    if domain not in DOMAINS or attempt < 1:
        raise ValueError("COMPETITION_WORKER_DOMAIN_OR_ATTEMPT_INVALID")
    request = TaskRequest.model_validate(payload["request"])
    template = TaskTemplateVersion.model_validate(payload["template"])
    plan = CoalitionPlan.model_validate(payload["coalition"])
    projection = ActorContextProjection.model_validate(payload["projection"])
    worker_id = WORKERS[domain]
    if projection.actor_id != worker_id:
        raise ValueError("COMPETITION_WORKER_PROJECTION_ACTOR_MISMATCH")
    plan_lineage = _validated_plan_task_lineage(
        payload,
        expected_kind="DOMAIN",
        expected_domain=domain,
        expected_assignee=worker_id,
        execution_projection=projection,
    )
    slot_ids = tuple(sorted(item.slot_id for item in plan.coverage if item.domain_id == domain))
    raw_sources = payload.get("source_values")
    if not isinstance(raw_sources, dict) or not raw_sources:
        raise ValueError("COMPETITION_WORKER_DOMAIN_SOURCE_VALUES_REQUIRED")
    sources: dict[str, LocalSourceValue] = {}
    for slot_id, raw in raw_sources.items():
        if not isinstance(slot_id, str) or not isinstance(raw, dict):
            raise ValueError("COMPETITION_WORKER_DOMAIN_SOURCE_VALUE_INVALID")
        source = LocalSourceValue(
            slot_id=str(raw["slot_id"]),
            object_ref=str(raw["object_ref"]),
            domain_id=str(raw["domain_id"]),
            value=raw["value"],
            semantic_kind=SemanticKind(str(raw["semantic_kind"])),
            authority_ref=str(raw["authority_ref"]),
            source_id=str(raw["source_id"]),
            source_version=str(raw["source_version"]),
            sensitivity=str(raw.get("sensitivity") or "INTERNAL"),
            raw_private_value=(
                str(raw["raw_private_value"]) if raw.get("raw_private_value") is not None else None
            ),
        )
        if source.slot_id != slot_id or source.domain_id != domain:
            raise ValueError("COMPETITION_WORKER_CROSS_DOMAIN_SOURCE_FORBIDDEN")
        if raw.get("source_digest") != source.source_digest:
            raise ValueError("COMPETITION_WORKER_SOURCE_DIGEST_MISMATCH")
        sources[slot_id] = source
    projected_refs = set(projection.included_refs)
    missing_fields = [
        slot_id
        for slot_id in slot_ids
        if slot_id not in sources or sources[slot_id].object_ref not in projected_refs
    ]
    base: dict[str, Any] = {
        "schema_version": "orgrebase.golden-domain-result.v1",
        "run_id": run_id,
        "correlation_id": correlation_id,
        "task_id": task_id,
        "domain": domain,
        "attempt": attempt,
        "worker_id": worker_id,
        "process_id": os.getpid(),
        "input_digest": sha256_digest(payload),
        "projection_digest": projection.digest,
        "supplemental_tool_receipt_digest": payload.get("supplemental_tool_receipt_digest"),
        "supplemental_tool_result_digest": payload.get("supplemental_tool_result_digest"),
        "candidate_only": True,
        "target_writes": 0,
        **plan_lineage,
    }
    provider = DeterministicDomainProvider(domain, worker_id, sources)
    selected_slots = tuple(slot for slot in slot_ids if slot not in missing_fields)
    if not selected_slots:
        return {
            **base,
            "status": "ABSTAIN",
            "reason_codes": ["REQUIRED_PROJECTED_FIELD_MISSING"],
            "missing_fields": missing_fields,
            "claim_candidates": [],
            "candidate_bundle": None,
        }
    candidates, bundle = provider.produce(
        task=request,
        template=template,
        plan=plan,
        slot_ids=selected_slots,
        actor_projection=projection,
        now=str(payload["observed_at"]),
    )
    bundle_payload = bundle.model_dump(mode="json", exclude={"digest"})
    bundle_payload["delegation_task_ref"] = task_id
    bundle_payload["transport_mode"] = "CONTROLLED_LOCAL_AGENTTEAMS"
    bundle_payload["evidence_class"] = "CONTROLLED_LOCAL_AGENTTEAMS"
    bound_bundle = DomainCandidateBundle.model_validate(bundle_payload)
    return {
        **base,
        "status": "ABSTAIN" if missing_fields else "PASS",
        "reason_codes": (
            ["REQUIRED_PROJECTED_FIELD_MISSING"] if missing_fields else ["RUNTIME_PROJECTION_RESOLVED"]
        ),
        "missing_fields": missing_fields,
        "claim_candidates": [item.model_dump(mode="json") for item in candidates],
        "candidate_bundle": bound_bundle.model_dump(mode="json"),
    }


def deterministic_review(payload: dict[str, Any]) -> ReviewerDecision:
    """Verify exact slot coverage and provenance; the model is advisory only."""

    results = payload.get("domain_results")
    if not isinstance(results, list):
        raise ValueError("COMPETITION_REVIEW_RESULTS_REQUIRED")
    required_slots = payload.get("required_slots")
    expected_bindings = payload.get("expected_bindings")
    if not isinstance(required_slots, dict) or not isinstance(expected_bindings, dict):
        raise ValueError("COMPETITION_REVIEW_BINDINGS_REQUIRED")
    failures: list[str] = []
    failed_domains: set[str] = set()
    candidates_by_slot: dict[str, list[tuple[str, ClaimCandidate]]] = {}
    results_by_domain: dict[str, dict[str, Any]] = {}
    for item in results:
        if not isinstance(item, dict):
            failures.append("RESULT_NOT_OBJECT")
            continue
        domain = str(item.get("domain") or "")
        if domain not in DOMAINS or domain in results_by_domain:
            failures.append(f"DOMAIN_RESULT_CARDINALITY:{domain or 'unknown'}")
            if domain in DOMAINS:
                failed_domains.add(domain)
            continue
        results_by_domain[domain] = item
        binding = expected_bindings.get(domain)
        if not isinstance(binding, dict):
            failures.append(f"EXPECTED_BINDING_MISSING:{domain}")
            failed_domains.add(domain)
            continue
        for field in (
            "run_id",
            "correlation_id",
            "task_id",
            "attempt",
            "worker_id",
            "input_digest",
            "projection_digest",
        ):
            if item.get(field) != binding.get(field):
                failures.append(f"BINDING_{field.upper()}_MISMATCH:{domain}")
                failed_domains.add(domain)
        for field in (
            "agentteams_execution_plan_digest",
            "context_envelope_digest",
            "logical_plan_task_id",
            "logical_plan_task_digest",
            "formation_receipt_id",
            "formation_receipt_digest",
        ):
            if field in binding and item.get(field) != binding.get(field):
                failures.append(f"BINDING_{field.upper()}_MISMATCH:{domain}")
                failed_domains.add(domain)
        if item.get("candidate_only") is not True or item.get("target_writes") != 0:
            failures.append(f"EFFECT_BOUNDARY_MISMATCH:{domain}")
            failed_domains.add(domain)
        if item.get("status") != "PASS":
            failures.append(f"DOMAIN_NOT_PASS:{domain}")
            failed_domains.add(domain)
        raw_candidates = item.get("claim_candidates")
        raw_bundle = item.get("candidate_bundle")
        try:
            candidates = [ClaimCandidate.model_validate(value) for value in raw_candidates]
            bundle = DomainCandidateBundle.model_validate(raw_bundle)
        except (TypeError, ValueError):
            failures.append(f"CANDIDATE_SCHEMA_INVALID:{domain}")
            failed_domains.add(domain)
            continue
        expected_slots = sorted(str(slot) for slot in required_slots.get(domain, []))
        if sorted(candidate.predicate for candidate in candidates) != expected_slots:
            failures.append(f"DOMAIN_SLOT_SET_MISMATCH:{domain}")
            failed_domains.add(domain)
        if (
            bundle.domain_id != domain
            or bundle.worker_id != WORKERS[domain]
            or bundle.delegation_task_ref != item.get("task_id")
            or bundle.candidate_refs != tuple(candidate.digest for candidate in candidates)
            or bundle.candidate_set_digest
            != sha256_digest(sorted(candidate.digest for candidate in candidates))
        ):
            failures.append(f"BUNDLE_BINDING_MISMATCH:{domain}")
            failed_domains.add(domain)
        for candidate in candidates:
            source_binding = binding.get("source_bindings", {}).get(candidate.predicate)
            source_ref = candidate.source_refs[0] if len(candidate.source_refs) == 1 else None
            if (
                not isinstance(source_binding, dict)
                or candidate.issuer_domain_id != domain
                or candidate.predicate not in expected_slots
                or candidate.subject_ref != source_binding.get("object_ref")
                or sha256_digest(candidate.value) != source_binding.get("value_digest")
                or candidate.semantic_kind.value != source_binding.get("semantic_kind")
                or candidate.authority_ref != source_binding.get("authority_ref")
                or candidate.sensitivity != source_binding.get("sensitivity")
                or candidate.purpose != binding.get("task_purpose")
                or set(candidate.recipients) != {"workspace-renderer", WORKERS[domain]}
                or candidate.temporal_state != "CURRENT"
                or candidate.value_schema_ref != f"schema:workspace.{candidate.predicate}@v1"
                or source_ref is None
                or source_ref.source_id != source_binding.get("source_id")
                or source_ref.source_version != source_binding.get("source_version")
                or source_ref.source_digest != source_binding.get("source_digest")
                or source_ref.locator_class != "SYNTHETIC_LOCAL_SOURCE"
            ):
                failures.append(f"CANDIDATE_PROVENANCE_MISMATCH:{domain}:{candidate.predicate}")
                failed_domains.add(domain)
            candidates_by_slot.setdefault(candidate.predicate, []).append((domain, candidate))
        expected_tool_digest = binding.get("tool_receipt_digest")
        observed_tool_digest = item.get("supplemental_tool_receipt_digest")
        if observed_tool_digest != expected_tool_digest:
            failures.append(f"TOOL_SUPPLEMENT_BINDING_MISMATCH:{domain}")
            failed_domains.add(domain)
        expected_tool_result_digest = binding.get("tool_result_digest")
        observed_tool_result_digest = item.get("supplemental_tool_result_digest")
        if observed_tool_result_digest != expected_tool_result_digest:
            failures.append(f"TOOL_RESULT_BINDING_MISMATCH:{domain}")
            failed_domains.add(domain)
    for domain in DOMAINS:
        if domain not in results_by_domain:
            failures.append(f"DOMAIN_RESULT_MISSING:{domain}")
            failed_domains.add(domain)
        for slot in required_slots.get(domain, []):
            if len(candidates_by_slot.get(str(slot), [])) != 1:
                failures.append(f"SLOT_CARDINALITY_MISMATCH:{domain}:{slot}")
                failed_domains.add(domain)
    return ReviewerDecision(
        verdict="REPLAN" if failures else "PASS",
        missing_domains=sorted(failed_domains),
        reason_codes=sorted(set(failures))
        if failures
        else ["EXACT_SLOT_PROVENANCE_AND_EFFECT_BOUNDARY_PASS"],
    )


def review_domain_results(
    payload: dict[str, Any],
    *,
    observation_directory: Path | None = None,
) -> dict[str, Any]:
    run_id = str(payload["run_id"])
    correlation_id = str(payload["correlation_id"])
    task_id = str(payload["task_id"])
    phase = int(payload["phase"])
    reviewer_plan_lineage = _validated_plan_task_lineage(
        payload,
        expected_kind="REVIEWER_BARRIER",
        expected_domain="reviewer",
        expected_assignee="independent-reviewer",
    )
    results = payload.get("domain_results")
    if not isinstance(results, list) or not results:
        raise ValueError("COMPETITION_REVIEWER_DOMAIN_RESULTS_REQUIRED")
    prompt_results = [
        {
            "domain": item.get("domain"),
            "attempt": item.get("attempt"),
            "status": item.get("status"),
            "missing_fields": item.get("missing_fields", []),
            "reason_codes": item.get("reason_codes", []),
            "candidate_count": len(item.get("claim_candidates", [])),
            "result_digest": sha256_digest(item),
        }
        for item in results
        if isinstance(item, dict)
    ]
    prompt_payload = {
        "run_id": run_id,
        "correlation_id": correlation_id,
        "review_phase": phase,
        "required_domains": list(DOMAINS),
        "rule": "ABSTAIN or any missing field means REPLAN; otherwise PASS",
        "domain_results": prompt_results,
    }
    schema = ReviewerDecision.model_json_schema(mode="validation")
    model_provider = str(payload.get("model_provider") or "ollama-local")
    if model_provider not in MODEL_PROVIDERS:
        raise ValueError("COMPETITION_REVIEWER_MODEL_PROVIDER_INVALID")
    model_id = (
        reviewer_model_id(model_provider) if model_provider != "ollama-local" else str(payload.get("model_id") or "qwen2.5:3b")
    )
    model_version = (
        reviewer_model_id(model_provider) if model_provider != "ollama-local" else f"ollama-manifest:{OLLAMA_MODEL_DIGEST}"
    )
    request = ModelRequest(
        request_id=f"model-request:{run_id}:review-{phase}",
        run_id=run_id,
        task_ref=task_id,
        actor_id="reviewer:quote-coalition",
        purpose="candidate_coalition_review",
        schema_name="ReviewerDecision",
        schema_digest=sha256_digest(schema),
        context_refs=tuple(str(item["result_digest"]) for item in prompt_results),
        input_refs=tuple(str(item["projection_digest"]) for item in results),
        allowed_tool_ids=(),
        provider=model_provider,
        model_id=model_id,
        model_version=model_version,
        prompt_template_ref="prompt:golden-competition-reviewer@v1",
        prompt_template_digest=sha256_digest(
            {
                "system": "candidate-only-reviewer",
                "rule": prompt_payload["rule"],
            }
        ),
        temperature=0.0,
        seed=None if model_provider != "ollama-local" else 42,
        max_output_tokens=2048 if model_provider != "ollama-local" else 192,
        attempt=0,
    )
    observer = ModelAttemptObserver(observation_directory)
    if model_provider == "vertex-ai":
        provider = VertexAIStructuredProvider(prompt_payload=prompt_payload, model_id=VERTEX_MODEL_ID, observer=observer)
    elif model_provider == "deepseek":
        provider = DeepSeekStructuredProvider(prompt_payload=prompt_payload, observer=observer)
    else:
        provider = LocalOllamaStructuredProvider(
            prompt_payload=prompt_payload,
            endpoint=(str(payload["ollama_endpoint"]) if payload.get("ollama_endpoint") else None),
            model_id=request.model_id, expected_model_digest=OLLAMA_MODEL_DIGEST, observer=observer,
        )
    receipt = provider.generate_structured(request=request, output_model=ReviewerDecision)
    base = {
        "schema_version": "orgrebase.golden-reviewer-result.v1",
        "run_id": run_id,
        "correlation_id": correlation_id,
        "task_id": task_id,
        "phase": phase,
        "reviewer_id": "reviewer:quote-coalition",
        "process_id": os.getpid(),
        "input_digest": sha256_digest(payload),
        "prompt_payload_digest": sha256_digest(prompt_payload),
        "model_provider": model_provider,
        "model_receipt": receipt.model_dump(mode="json"),
        "model_runtime_binding": dict(provider.runtime_binding),
        "model_attempt_observations": provider_observations(provider, run_ref=run_id),
        "model_authority": "ADVISORY_ONLY_DETERMINISTIC_REVIEWER_AUTHORITATIVE",
        "candidate_only": True,
        "target_writes": 0,
        **reviewer_plan_lineage,
    }
    if receipt.status != "VALID" or receipt.value is None:
        return {
            **base,
            "status": "NOT_RUN",
            "decision": None,
            "reason_codes": [receipt.error_code or "MODEL_REVIEW_NOT_RUN"],
        }
    model_decision = ReviewerDecision.model_validate(receipt.value)
    deterministic = deterministic_review(payload)
    advisory_accepted = model_decision.verdict == deterministic.verdict and set(
        model_decision.missing_domains
    ) == set(deterministic.missing_domains)
    return {
        **base,
        "status": "PASS",
        "decision": deterministic.model_dump(mode="json"),
        "model_advisory": model_decision.model_dump(mode="json"),
        "model_advisory_accepted": advisory_accepted,
        "model_advisory_disposition_reason": (
            "MODEL_ADVISORY_MATCHED_DETERMINISTIC_VERIFIER"
            if advisory_accepted
            else "MODEL_ADVISORY_OVERRIDDEN_BY_DETERMINISTIC_VERIFIER"
        ),
        "reason_codes": ["OBSERVED_MODEL_PLUS_DETERMINISTIC_REVIEW"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("worker", "reviewer"))
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    payload = _read(args.input)
    result = (
        produce_domain_result(payload)
        if args.mode == "worker"
        else review_domain_results(
            payload,
            observation_directory=Path(args.output).parent / "model-attempts",
        )
    )
    _write(args.output, result)
    print(
        json.dumps(
            {
                "mode": args.mode,
                "status": result["status"],
                "output_digest": sha256_digest(result),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
