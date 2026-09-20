"""Deterministic four-domain coalition binding for candidate-only quote composition."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from orgrebase.workspace.native_taskflow import digest_json

Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
Hex64 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Domain = Literal["product", "legal", "finance", "gtm"]
REQUIRED_DOMAINS = ("product", "legal", "finance", "gtm")
EVIDENCE_CLASS = "CONTROLLED_LOCAL_FOUR_DOMAIN_CANDIDATE_COMPOSITION"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CoalitionDomainResult(_StrictModel):
    domain: Domain
    task_id: str = Field(min_length=1)
    attempt: Literal[1]
    plan_revision: Literal[1]
    task_binding_digest: Digest
    admission_binding_digest: Digest
    delegation_digest: Digest
    context_projection_digest: Digest
    source_lock_digest: Digest
    observed_result_digest: Digest
    control_decision_ref: Digest
    status: Literal["completed"]
    candidate_only: Literal[True]
    target_writes: Literal[0]


class CoalitionResultBinding(_StrictModel):
    schema_version: Literal["orgrebase.workspace-coalition-result-binding.v1"]
    evidence_class: Literal[
        "CONTROLLED_LOCAL_FOUR_DOMAIN_CANDIDATE_COMPOSITION"
    ]
    claim_boundary: Literal[
        "FOUR_ADMITTED_CONTROLLED_CANDIDATES_NOT_INDEPENDENT_AGENT_REASONING"
    ]
    run_id: str = Field(min_length=1)
    nonce: Hex64
    project_id: str = Field(min_length=1)
    plan_digest: Digest
    native_receipt_digest: Digest
    members: list[CoalitionDomainResult] = Field(min_length=4, max_length=4)
    candidate_only: Literal[True]
    target_writes: Literal[0]
    coalition_digest: Digest

    @model_validator(mode="after")
    def _exact_domains_and_digest(self) -> CoalitionResultBinding:
        domains = tuple(member.domain for member in self.members)
        if domains != REQUIRED_DOMAINS:
            raise ValueError("coalition members must use the canonical four-domain order")
        body = self.model_dump(mode="json", exclude={"coalition_digest"})
        if self.coalition_digest != digest_json(body):
            raise ValueError("coalition digest mismatch")
        return self


class CoalitionBindingError(ValueError):
    """Fail-closed coalition construction or verification error."""


def _verified_native_body(native_receipt: Mapping[str, Any]) -> dict[str, Any]:
    native = dict(native_receipt)
    supplied = native.pop("receipt_digest", None)
    if not isinstance(supplied, str) or supplied != digest_json(native):
        raise CoalitionBindingError("NATIVE_RECEIPT_DIGEST_MISMATCH")
    native["receipt_digest"] = supplied
    if (
        native.get("schema_version")
        != "orgrebase.workspace-agentteams-lifecycle-receipt.v1"
        or native.get("evidence_class") != "CONTROLLED_LOCAL_NATIVE_TASKFLOW"
        or native.get("project_terminal_state") != "completed"
        or native.get("canonical_target_writes") != 0
    ):
        raise CoalitionBindingError("NATIVE_RECEIPT_NOT_ADMISSIBLE")
    return native


def _decision_index(native: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    decisions = native.get("control_decisions")
    if not isinstance(decisions, list):
        raise CoalitionBindingError("CONTROL_DECISIONS_REQUIRED")
    indexed: dict[str, dict[str, Any]] = {}
    for item in decisions:
        if not isinstance(item, Mapping):
            raise CoalitionBindingError("CONTROL_DECISION_INVALID")
        decision = dict(item)
        declared = decision.pop("decision_digest", None)
        if not isinstance(declared, str) or declared != digest_json(decision):
            raise CoalitionBindingError("CONTROL_DECISION_DIGEST_MISMATCH")
        if declared in indexed:
            raise CoalitionBindingError("CONTROL_DECISION_DUPLICATE")
        decision["decision_digest"] = declared
        indexed[declared] = decision
    return indexed


def build_coalition_result_binding(
    native_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Build an exact four-domain coalition from admitted primary task bindings."""

    native = _verified_native_body(native_receipt)
    bindings = native.get("bindings")
    if not isinstance(bindings, list):
        raise CoalitionBindingError("NATIVE_BINDINGS_REQUIRED")
    decisions = _decision_index(native)
    members: list[dict[str, Any]] = []
    for domain in REQUIRED_DOMAINS:
        eligible = [
            dict(item)
            for item in bindings
            if isinstance(item, Mapping)
            and item.get("domain") == domain
            and item.get("attempt") == 1
            and item.get("plan_revision") == 1
            and item.get("predecessor_task_id") is None
            and item.get("status") == "completed"
        ]
        if len(eligible) != 1:
            raise CoalitionBindingError(f"EXACT_PRIMARY_DOMAIN_BINDING_REQUIRED:{domain}")
        binding = eligible[0]
        if (
            binding.get("run_id") != native.get("run_id")
            or binding.get("nonce") != native.get("nonce")
            or binding.get("project_id") != native.get("project_id")
            or binding.get("plan_digest") != native.get("plan_digest")
            or binding.get("candidate_only") is not True
            or binding.get("target_writes") != 0
            or binding.get("active_attempt_ref") != binding.get("attempt_ref")
        ):
            raise CoalitionBindingError(f"DOMAIN_BINDING_AUTHORITY_MISMATCH:{domain}")
        observed_result = binding.get("observed_result_digest")
        decision_ref = binding.get("control_decision_ref")
        admission_digest = binding.get("admission_binding_digest")
        if not all(
            isinstance(value, str) and value.startswith("sha256:")
            for value in (observed_result, decision_ref, admission_digest)
        ):
            raise CoalitionBindingError(f"DOMAIN_ADMISSION_ROOT_REQUIRED:{domain}")
        decision = decisions.get(str(decision_ref))
        if (
            decision is None
            or decision.get("verdict") != "ADMIT"
            or decision.get("run_id") != native.get("run_id")
            or decision.get("nonce") != native.get("nonce")
            or decision.get("project_id") != native.get("project_id")
            or decision.get("task_id") != binding.get("task_id")
            or decision.get("domain") != domain
            or decision.get("attempt") != 1
            or decision.get("expected_binding_digest") != admission_digest
            or decision.get("observed_result_digest") != observed_result
            or decision.get("candidate_only") is not True
            or decision.get("target_writes") != 0
        ):
            raise CoalitionBindingError(f"DOMAIN_CONTROL_DECISION_MISMATCH:{domain}")
        members.append(
            {
                "domain": domain,
                "task_id": binding["task_id"],
                "attempt": 1,
                "plan_revision": 1,
                "task_binding_digest": digest_json(binding),
                "admission_binding_digest": admission_digest,
                "delegation_digest": binding["delegation_digest"],
                "context_projection_digest": binding["context_projection_digest"],
                "source_lock_digest": binding["source_lock_digest"],
                "observed_result_digest": observed_result,
                "control_decision_ref": decision_ref,
                "status": "completed",
                "candidate_only": True,
                "target_writes": 0,
            }
        )
    base = {
        "schema_version": "orgrebase.workspace-coalition-result-binding.v1",
        "evidence_class": EVIDENCE_CLASS,
        "claim_boundary": (
            "FOUR_ADMITTED_CONTROLLED_CANDIDATES_NOT_INDEPENDENT_AGENT_REASONING"
        ),
        "run_id": native["run_id"],
        "nonce": native["nonce"],
        "project_id": native["project_id"],
        "plan_digest": native["plan_digest"],
        "native_receipt_digest": native["receipt_digest"],
        "members": members,
        "candidate_only": True,
        "target_writes": 0,
    }
    return CoalitionResultBinding.model_validate(
        {**base, "coalition_digest": digest_json(base)}
    ).model_dump(mode="json")


def verify_coalition_result_binding(
    coalition: Mapping[str, Any], native_receipt: Mapping[str, Any]
) -> dict[str, Any]:
    """Reconstruct the coalition from native bytes and reject semantic drift."""

    try:
        observed = CoalitionResultBinding.model_validate(dict(coalition)).model_dump(
            mode="json"
        )
    except ValueError as exc:
        raise CoalitionBindingError("COALITION_MODEL_INVALID") from exc
    expected = build_coalition_result_binding(native_receipt)
    if observed != expected:
        raise CoalitionBindingError("COALITION_NATIVE_RECONSTRUCTION_MISMATCH")
    return observed
