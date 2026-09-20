from __future__ import annotations

from pathlib import Path

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.workspace.oac_quote_parity import (
    QuoteFormationObligation,
    build_quote_formation_parity_receipt,
    load_golden_summary,
    verify_quote_formation_parity,
)

ROOT = Path(__file__).resolve().parents[2]
GOLDEN_ROOT = ROOT / "evidence" / "golden-competition" / "latest" / "pilot"


def _digest(label: str) -> str:
    return sha256_digest({"test": label})


@pytest.fixture(scope="module")
def parity_receipt():
    return build_quote_formation_parity_receipt(
        adaptation_run_id="run:oac-adaptation:test@v1",
        adaptation_draft_digest=_digest("draft"),
        mapping_set_digest=_digest("mappings"),
        profile_digest=_digest("profile"),
        pack_digest=_digest("pack"),
        golden_summary=load_golden_summary(GOLDEN_ROOT),
    )


def test_parity_proves_five_obligations_seven_attempts_and_both_recovery_transitions(
    parity_receipt,
) -> None:
    assert parity_receipt.verdict == "PASS"
    assert parity_receipt.logical_obligation_count == 5
    assert parity_receipt.dynamic_task_attempt_count == 7
    assert {item.obligation_ref for item in parity_receipt.task_attempts} == set(
        QuoteFormationObligation
    )
    assert [
        item.outcome for item in parity_receipt.task_attempts if item.domain == "finance"
    ] == ["ABSTAIN", "PASS"]
    assert [
        item.outcome for item in parity_receipt.task_attempts if item.role == "REVIEWER"
    ] == ["REPLAN", "PASS"]
    assert parity_receipt.finance_predecessor_transition == "ABSTAIN_TO_PASS"
    assert parity_receipt.reviewer_transition == "REPLAN_TO_PASS"
    assert parity_receipt.binding_timing == "POST_RUN_SAME_RUN_REPLAY"
    assert parity_receipt.canonical_target_writes == 0
    assert parity_receipt.oac_plan_produced is False
    assert parity_receipt.oac_plan_certificate_produced is False
    assert parity_receipt.oac_runtime_invoked is False
    assert verify_quote_formation_parity(
        parity_receipt.model_dump(mode="json")
    ) == parity_receipt


@pytest.mark.parametrize(
    ("mutation", "error"),
    (
        ("omit-attempt", "QUOTE_FORMATION_PARITY_RECEIPT_INVALID"),
        ("substitute-derived-digest", "QUOTE_PARITY_DERIVED_DIGEST_MISMATCH"),
        ("stale-run", "QUOTE_PARITY_RUN_OR_EFFECT_BINDING_INVALID"),
        ("erase-obligation", "QUOTE_PARITY_OBLIGATION_COVERAGE_INVALID"),
    ),
)
def test_independent_parity_verifier_rejects_omission_substitution_and_stale_binding(
    parity_receipt,
    mutation: str,
    error: str,
) -> None:
    payload = parity_receipt.model_dump(mode="json")
    payload["digest"] = ""
    if mutation == "omit-attempt":
        payload["task_attempts"] = payload["task_attempts"][:-1]
    elif mutation == "substitute-derived-digest":
        payload["attempt_set_digest"] = "sha256:" + "0" * 64
    elif mutation == "stale-run":
        payload["task_attempts"][0]["run_id"] = "run:stale-substitution@v1"
        payload["task_attempts"][0]["digest"] = ""
    elif mutation == "erase-obligation":
        for attempt in payload["task_attempts"]:
            if attempt["obligation_ref"] == "FINANCE_POLICY":
                attempt["obligation_ref"] = "LEGAL_POLICY"
                attempt["digest"] = ""
    else:  # pragma: no cover - parametrization invariant
        raise AssertionError(mutation)
    with pytest.raises(IntegrityError, match=error):
        verify_quote_formation_parity(payload)


def test_parity_generation_is_byte_stable_for_the_same_golden_summary(
    parity_receipt,
) -> None:
    second = build_quote_formation_parity_receipt(
        adaptation_run_id=parity_receipt.adaptation_run_id,
        adaptation_draft_digest=parity_receipt.adaptation_draft_digest,
        mapping_set_digest=parity_receipt.mapping_set_digest,
        profile_digest=parity_receipt.profile_digest,
        pack_digest=parity_receipt.pack_digest,
        golden_summary=load_golden_summary(GOLDEN_ROOT),
    )
    assert second.model_dump_json() == parity_receipt.model_dump_json()
    assert second.digest == parity_receipt.digest
