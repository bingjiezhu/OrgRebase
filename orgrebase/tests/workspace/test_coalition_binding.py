from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from orgrebase.workspace.coalition import (
    CoalitionBindingError,
    build_coalition_result_binding,
    verify_coalition_result_binding,
)
from orgrebase.workspace.native_taskflow import digest_json

ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / "evidence/semifinal-closure/latest/agentteams/lifecycle-receipt.json"


def _native() -> dict[str, object]:
    return json.loads(NATIVE.read_text(encoding="utf-8"))


def _reseal_native(value: dict[str, object]) -> dict[str, object]:
    body = {key: item for key, item in value.items() if key != "receipt_digest"}
    value["receipt_digest"] = digest_json(body)
    return value


def test_builds_exact_four_domain_coalition() -> None:
    coalition = build_coalition_result_binding(_native())
    assert [item["domain"] for item in coalition["members"]] == [
        "product",
        "legal",
        "finance",
        "gtm",
    ]
    assert coalition["candidate_only"] is True
    assert coalition["target_writes"] == 0
    assert verify_coalition_result_binding(coalition, _native()) == coalition


@pytest.mark.parametrize("domain", ["product", "legal", "finance", "gtm"])
def test_rejects_missing_primary_domain_even_after_reseal(domain: str) -> None:
    native = _native()
    native["bindings"] = [
        item
        for item in native["bindings"]  # type: ignore[index]
        if not (
            item["domain"] == domain
            and item["attempt"] == 1
            and item["plan_revision"] == 1
            and item["status"] == "completed"
        )
    ]
    _reseal_native(native)
    with pytest.raises(
        CoalitionBindingError, match=f"EXACT_PRIMARY_DOMAIN_BINDING_REQUIRED:{domain}"
    ):
        build_coalition_result_binding(native)


def test_rejects_substituted_domain_result_even_after_reseal() -> None:
    native = _native()
    legal = next(item for item in native["bindings"] if item["domain"] == "legal")  # type: ignore[index]
    legal["observed_result_digest"] = "sha256:" + "1" * 64
    _reseal_native(native)
    with pytest.raises(CoalitionBindingError, match="DOMAIN_CONTROL_DECISION_MISMATCH:legal"):
        build_coalition_result_binding(native)


def test_rejects_cross_run_binding_even_after_reseal() -> None:
    native = _native()
    finance = next(item for item in native["bindings"] if item["domain"] == "finance")  # type: ignore[index]
    finance["run_id"] = "run:attacker"
    _reseal_native(native)
    with pytest.raises(CoalitionBindingError, match="DOMAIN_BINDING_AUTHORITY_MISMATCH:finance"):
        build_coalition_result_binding(native)


def test_rejects_rehashed_coalition_member_substitution() -> None:
    native = _native()
    coalition = build_coalition_result_binding(native)
    mutated = copy.deepcopy(coalition)
    mutated["members"][0]["observed_result_digest"] = "sha256:" + "2" * 64
    body = {key: item for key, item in mutated.items() if key != "coalition_digest"}
    mutated["coalition_digest"] = digest_json(body)
    with pytest.raises(CoalitionBindingError, match="COALITION_NATIVE_RECONSTRUCTION_MISMATCH"):
        verify_coalition_result_binding(mutated, native)
