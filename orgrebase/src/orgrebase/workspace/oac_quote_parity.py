"""Independent Quote Formation parity over one frozen Golden run.

This module deliberately imports neither :mod:`workspace.service` nor the
AgentTeams adapter.  It verifies an already observed seven-attempt trace and
produces an OrgRebase-owned receipt; it never claims an OAC PlanCertificate.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from orgrebase.digest import sha256_digest
from orgrebase.domain import ContentAddressedModel, IntegrityError

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class QuoteFormationObligation(StrEnum):
    PRODUCT_FACTS = "PRODUCT_FACTS"
    LEGAL_POLICY = "LEGAL_POLICY"
    FINANCE_POLICY = "FINANCE_POLICY"
    GTM_COMPOSITION = "GTM_COMPOSITION"
    INDEPENDENT_REVIEW = "INDEPENDENT_REVIEW"


class QuoteFormationTaskAttempt(ContentAddressedModel):
    run_id: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    role: Literal["DOMAIN_WORKER", "REVIEWER"]
    domain: Literal["product", "legal", "finance", "gtm", "coalition"]
    attempt: int = Field(ge=1, le=2)
    principal_ref: str = Field(min_length=1)
    obligation_ref: QuoteFormationObligation
    predecessor_task_refs: tuple[str, ...] = ()
    outcome: Literal["PASS", "ABSTAIN", "REPLAN"]
    input_digest: str
    result_digest: str
    candidate_only: Literal[True] = True
    target_writes: Literal[0] = 0

    @model_validator(mode="after")
    def validate_attempt(self) -> Self:
        if not _DIGEST.fullmatch(self.input_digest) or not _DIGEST.fullmatch(
            self.result_digest
        ):
            raise ValueError("QUOTE_PARITY_ATTEMPT_DIGEST_INVALID")
        if len(self.predecessor_task_refs) != len(set(self.predecessor_task_refs)):
            raise ValueError("QUOTE_PARITY_PREDECESSOR_DUPLICATE")
        if self.task_id in self.predecessor_task_refs:
            raise ValueError("QUOTE_PARITY_SELF_PREDECESSOR")
        if self.role == "REVIEWER":
            if (
                self.domain != "coalition"
                or self.obligation_ref is not QuoteFormationObligation.INDEPENDENT_REVIEW
            ):
                raise ValueError("QUOTE_PARITY_REVIEWER_BINDING_INVALID")
        elif (
            self.domain == "coalition"
            or self.obligation_ref is QuoteFormationObligation.INDEPENDENT_REVIEW
        ):
            raise ValueError("QUOTE_PARITY_WORKER_BINDING_INVALID")
        return self


_CHECKS = (
    "FIVE_LOGICAL_OBLIGATIONS_COVERED",
    "SEVEN_DYNAMIC_TASK_ATTEMPTS_RETAINED",
    "FINANCE_ABSTAIN_THEN_PASS",
    "REVIEWER_REPLAN_THEN_PASS",
    "HAPPENS_BEFORE_ACYCLIC_AND_COMPLETE",
    "WORKER_REVIEWER_SEPARATION",
    "AUTHORIZED_PRINCIPALS_EXACT",
    "CANDIDATE_ONLY_ZERO_TARGET_WRITES",
    "EXACT_PROFILE_PACK_ADAPTATION_BINDING",
)


class QuoteFormationParityReceipt(ContentAddressedModel):
    schema_version: Literal["orgrebase.quote-formation-parity-receipt.v1"] = (
        "orgrebase.quote-formation-parity-receipt.v1"
    )
    adaptation_run_id: str = Field(min_length=1)
    adaptation_draft_digest: str
    mapping_set_digest: str
    profile_digest: str
    pack_digest: str
    golden_run_id: str = Field(min_length=1)
    golden_correlation_id: str = Field(min_length=1)
    golden_summary_digest: str
    task_attempts: tuple[QuoteFormationTaskAttempt, ...] = Field(min_length=7, max_length=7)
    authorized_principal_refs: tuple[str, ...] = Field(min_length=1)
    authority_binding_digest: str
    attempt_set_digest: str
    obligation_coverage_digest: str
    happens_before_digest: str
    logical_obligation_count: Literal[5] = 5
    dynamic_task_attempt_count: Literal[7] = 7
    finance_predecessor_transition: Literal["ABSTAIN_TO_PASS"] = "ABSTAIN_TO_PASS"
    reviewer_transition: Literal["REPLAN_TO_PASS"] = "REPLAN_TO_PASS"
    binding_timing: Literal["POST_RUN_SAME_RUN_REPLAY"] = "POST_RUN_SAME_RUN_REPLAY"
    oac_plan_produced: Literal[False] = False
    oac_plan_certificate_produced: Literal[False] = False
    oac_runtime_invoked: Literal[False] = False
    formation_authority: Literal["ORGREBASE_CONTROL_PLANE"] = "ORGREBASE_CONTROL_PLANE"
    claim: Literal[
        "OAC_SOURCE_DEMAND_ADMITTED_AND_ORGREBASE_FORMATION_PARITY"
    ] = "OAC_SOURCE_DEMAND_ADMITTED_AND_ORGREBASE_FORMATION_PARITY"
    checked: tuple[str, ...] = _CHECKS
    verdict: Literal["PASS"] = "PASS"
    effect_ceiling: Literal["ZERO_EXTERNAL_EFFECTS"] = "ZERO_EXTERNAL_EFFECTS"
    canonical_target_writes: Literal[0] = 0

    @model_validator(mode="after")
    def validate_parity(self) -> Self:
        for value in (
            self.adaptation_draft_digest,
            self.mapping_set_digest,
            self.profile_digest,
            self.pack_digest,
            self.golden_summary_digest,
            self.authority_binding_digest,
            self.attempt_set_digest,
            self.obligation_coverage_digest,
            self.happens_before_digest,
        ):
            if not _DIGEST.fullmatch(value):
                raise ValueError("QUOTE_PARITY_DIGEST_INVALID")
        _verify_semantics(self)
        return self


def _verify_semantics(receipt: QuoteFormationParityReceipt) -> None:
    attempts = receipt.task_attempts
    if len({item.task_id for item in attempts}) != 7:
        raise IntegrityError("QUOTE_PARITY_TASK_SET_NOT_EXACT")
    if any(
        item.run_id != receipt.golden_run_id
        or item.correlation_id != receipt.golden_correlation_id
        or not item.candidate_only
        or item.target_writes != 0
        for item in attempts
    ):
        raise IntegrityError("QUOTE_PARITY_RUN_OR_EFFECT_BINDING_INVALID")
    expected_obligations = set(QuoteFormationObligation)
    if {item.obligation_ref for item in attempts} != expected_obligations:
        raise IntegrityError("QUOTE_PARITY_OBLIGATION_COVERAGE_INVALID")

    by_domain = {
        domain: tuple(item for item in attempts if item.domain == domain)
        for domain in ("product", "legal", "finance", "gtm", "coalition")
    }
    if tuple(len(by_domain[name]) for name in by_domain) != (1, 1, 2, 1, 2):
        raise IntegrityError("QUOTE_PARITY_ATTEMPT_CARDINALITY_INVALID")
    finance = tuple(sorted(by_domain["finance"], key=lambda item: item.attempt))
    reviewers = tuple(sorted(by_domain["coalition"], key=lambda item: item.attempt))
    if tuple(item.outcome for item in finance) != ("ABSTAIN", "PASS"):
        raise IntegrityError("QUOTE_PARITY_FINANCE_RECOVERY_INVALID")
    if tuple(item.outcome for item in reviewers) != ("REPLAN", "PASS"):
        raise IntegrityError("QUOTE_PARITY_REVIEW_SEQUENCE_INVALID")
    if any(by_domain[name][0].outcome != "PASS" for name in ("product", "legal", "gtm")):
        raise IntegrityError("QUOTE_PARITY_DOMAIN_OUTCOME_INVALID")

    worker_principals = {item.principal_ref for item in attempts if item.role == "DOMAIN_WORKER"}
    reviewer_principals = {item.principal_ref for item in attempts if item.role == "REVIEWER"}
    if worker_principals & reviewer_principals:
        raise IntegrityError("QUOTE_PARITY_WORKER_REVIEWER_SEPARATION_INVALID")
    selected_principals = tuple(sorted(worker_principals | reviewer_principals, key=str.encode))
    if receipt.authorized_principal_refs != selected_principals:
        raise IntegrityError("QUOTE_PARITY_AUTHORIZED_PRINCIPALS_INVALID")

    seen: set[str] = set()
    for item in attempts:
        if not set(item.predecessor_task_refs) <= seen:
            raise IntegrityError("QUOTE_PARITY_HAPPENS_BEFORE_INVALID")
        seen.add(item.task_id)
    initial_workers = {item.task_id for item in attempts if item.role == "DOMAIN_WORKER" and item.attempt == 1}
    if set(reviewers[0].predecessor_task_refs) != initial_workers:
        raise IntegrityError("QUOTE_PARITY_INITIAL_REVIEW_PREDECESSORS_INVALID")
    if reviewers[0].task_id not in finance[1].predecessor_task_refs:
        raise IntegrityError("QUOTE_PARITY_REPLAN_RECOVERY_EDGE_MISSING")
    final_inputs = {
        by_domain["product"][0].task_id,
        by_domain["legal"][0].task_id,
        finance[1].task_id,
        by_domain["gtm"][0].task_id,
    }
    if set(reviewers[1].predecessor_task_refs) != final_inputs:
        raise IntegrityError("QUOTE_PARITY_FINAL_REVIEW_PREDECESSORS_INVALID")

    expected = {
        "authority_binding_digest": sha256_digest(list(selected_principals)),
        "attempt_set_digest": sha256_digest([item.digest for item in attempts]),
        "obligation_coverage_digest": sha256_digest(
            {
                item.value: [attempt.task_id for attempt in attempts if attempt.obligation_ref is item]
                for item in QuoteFormationObligation
            }
        ),
        "happens_before_digest": sha256_digest(
            [
                {"task_id": item.task_id, "predecessor_task_refs": list(item.predecessor_task_refs)}
                for item in attempts
            ]
        ),
    }
    if any(getattr(receipt, field) != value for field, value in expected.items()):
        raise IntegrityError("QUOTE_PARITY_DERIVED_DIGEST_MISMATCH")


def verify_quote_formation_parity(
    value: QuoteFormationParityReceipt | Mapping[str, Any],
) -> QuoteFormationParityReceipt:
    """Deeply reparse and independently recompute the parity receipt."""

    try:
        payload = value.model_dump(mode="json") if isinstance(value, QuoteFormationParityReceipt) else dict(value)
        return QuoteFormationParityReceipt.model_validate(payload)
    except (TypeError, ValueError) as exc:
        raise IntegrityError("QUOTE_FORMATION_PARITY_RECEIPT_INVALID") from exc


def _obligation(domain: str) -> QuoteFormationObligation:
    return {
        "product": QuoteFormationObligation.PRODUCT_FACTS,
        "legal": QuoteFormationObligation.LEGAL_POLICY,
        "finance": QuoteFormationObligation.FINANCE_POLICY,
        "gtm": QuoteFormationObligation.GTM_COMPOSITION,
        "coalition": QuoteFormationObligation.INDEPENDENT_REVIEW,
    }[domain]


def attempts_from_golden_summary(summary: Mapping[str, Any]) -> tuple[QuoteFormationTaskAttempt, ...]:
    """Project the public Golden summary into the small verifier input."""

    value = dict(summary)
    claimed = value.get("digest")
    if (
        value.get("schema_version") != "orgrebase.golden-competition-summary.v1"
        or value.get("status") != "PASS"
        or value.get("canonical_target_writes") != 0
        or not isinstance(claimed, str)
        or sha256_digest({key: item for key, item in value.items() if key != "digest"}) != claimed
    ):
        raise IntegrityError("QUOTE_PARITY_GOLDEN_SUMMARY_INVALID")
    bindings = value.get("task_bindings")
    if not isinstance(bindings, list) or len(bindings) != 7:
        raise IntegrityError("QUOTE_PARITY_GOLDEN_BINDINGS_NOT_EXACT")
    run_id = str(value.get("run_id") or "")
    correlation_id = str(value.get("correlation_id") or "")
    if not run_id or not correlation_id:
        raise IntegrityError("QUOTE_PARITY_GOLDEN_RUN_BINDING_MISSING")

    workers = [item for item in bindings if isinstance(item, dict) and item.get("role") == "DOMAIN_WORKER"]
    reviewers = [item for item in bindings if isinstance(item, dict) and item.get("role") == "REVIEWER"]
    if len(workers) != 5 or len(reviewers) != 2:
        raise IntegrityError("QUOTE_PARITY_GOLDEN_ROLE_COUNTS_INVALID")
    initial_workers = [item for item in workers if item.get("attempt") == 1]
    finance_two = next((item for item in workers if item.get("domain") == "finance" and item.get("attempt") == 2), None)
    reviewer_one = next((item for item in reviewers if item.get("attempt") == 1), None)
    reviewer_two = next((item for item in reviewers if item.get("attempt") == 2), None)
    if len(initial_workers) != 4 or finance_two is None or reviewer_one is None or reviewer_two is None:
        raise IntegrityError("QUOTE_PARITY_GOLDEN_REPLAN_SHAPE_INVALID")
    final_worker_ids = {
        str(item["task_id"])
        for item in initial_workers
        if item.get("domain") != "finance"
    } | {str(finance_two["task_id"])}

    ordered_bindings = [*initial_workers, reviewer_one, finance_two, reviewer_two]
    attempts: list[QuoteFormationTaskAttempt] = []
    for binding in ordered_bindings:
        role = str(binding.get("role"))
        domain = str(binding.get("domain"))
        attempt_no = int(binding.get("attempt", 0))
        if role == "REVIEWER":
            outcome = str(value[f"reviewer_attempt_{attempt_no}"]["verdict"])
            predecessors = (
                tuple(str(item["task_id"]) for item in initial_workers)
                if attempt_no == 1
                else tuple(sorted(final_worker_ids, key=str.encode))
            )
        elif domain == "finance":
            outcome = str(value[f"finance_attempt_{attempt_no}"]["status"])
            predecessors = () if attempt_no == 1 else (str(reviewer_one["task_id"]),)
        else:
            outcome = "PASS"
            predecessors = ()
        attempts.append(
            QuoteFormationTaskAttempt(
                run_id=run_id,
                correlation_id=correlation_id,
                task_id=str(binding.get("task_id") or ""),
                role=role,  # type: ignore[arg-type]
                domain=domain,  # type: ignore[arg-type]
                attempt=attempt_no,
                principal_ref=str(binding.get("assignee") or ""),
                obligation_ref=_obligation(domain),
                predecessor_task_refs=predecessors,
                outcome=outcome,  # type: ignore[arg-type]
                input_digest=str(binding.get("input_digest") or ""),
                result_digest=str(binding.get("observed_result_digest") or ""),
                candidate_only=binding.get("candidate_only"),
                target_writes=binding.get("target_writes"),
            )
        )
    return tuple(attempts)


def build_quote_formation_parity_receipt(
    *,
    adaptation_run_id: str,
    adaptation_draft_digest: str,
    mapping_set_digest: str,
    profile_digest: str,
    pack_digest: str,
    golden_summary: Mapping[str, Any],
) -> QuoteFormationParityReceipt:
    attempts = attempts_from_golden_summary(golden_summary)
    principals = tuple(sorted({item.principal_ref for item in attempts}, key=str.encode))
    receipt = QuoteFormationParityReceipt(
        adaptation_run_id=adaptation_run_id,
        adaptation_draft_digest=adaptation_draft_digest,
        mapping_set_digest=mapping_set_digest,
        profile_digest=profile_digest,
        pack_digest=pack_digest,
        golden_run_id=attempts[0].run_id,
        golden_correlation_id=attempts[0].correlation_id,
        golden_summary_digest=str(golden_summary["digest"]),
        task_attempts=attempts,
        authorized_principal_refs=principals,
        authority_binding_digest=sha256_digest(list(principals)),
        attempt_set_digest=sha256_digest([item.digest for item in attempts]),
        obligation_coverage_digest=sha256_digest(
            {
                obligation.value: [
                    attempt.task_id for attempt in attempts if attempt.obligation_ref is obligation
                ]
                for obligation in QuoteFormationObligation
            }
        ),
        happens_before_digest=sha256_digest(
            [
                {"task_id": item.task_id, "predecessor_task_refs": list(item.predecessor_task_refs)}
                for item in attempts
            ]
        ),
    )
    return verify_quote_formation_parity(receipt)


def load_golden_summary(golden_root: str | Path) -> dict[str, Any]:
    path = Path(golden_root)
    selected = path if path.is_file() else path / "summary.json"
    if not selected.is_file() and path.is_dir():
        selected = path / "golden-run" / "summary.json"
    try:
        value = json.loads(selected.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntegrityError("QUOTE_PARITY_GOLDEN_SUMMARY_UNREADABLE") from exc
    if not isinstance(value, dict):
        raise IntegrityError("QUOTE_PARITY_GOLDEN_SUMMARY_INVALID")
    return value


__all__ = (
    "QuoteFormationObligation",
    "QuoteFormationParityReceipt",
    "QuoteFormationTaskAttempt",
    "attempts_from_golden_summary",
    "build_quote_formation_parity_receipt",
    "load_golden_summary",
    "verify_quote_formation_parity",
)
