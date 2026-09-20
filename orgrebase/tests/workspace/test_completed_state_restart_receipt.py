from __future__ import annotations

import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _state() -> dict:
    run_id = "run:golden-competition:test-restart"
    return {
        "stage": "QUOTE_V3",
        "quote": {"version": "v3"},
        "execution": {
            "run_id": run_id,
            "oac_activation": {
                "status": "CONSUMED_BY_QUOTE_FORMATION",
                "execution_run_id": run_id,
                "activation_binding_digest": "sha256:" + "1" * 64,
            },
        },
        "competition_evidence": {"run_id": run_id},
        "task_intake": {
            "status": "FORMATION_COMPLETED",
            "run_id": run_id,
            "intake_persisted": True,
            "digest": "sha256:" + "2" * 64,
        },
        "experience_governance": {
            "status": "APPROVED_CANARY",
            "discoverable": True,
            "loadable": True,
            "callable": True,
            "current_quote_consumed_candidate": False,
        },
        "event_chain": {
            "status": "PASS",
            "events": 16,
            "head_digest": "sha256:" + "3" * 64,
        },
        "changes": {
            "launch_date": {
                "approval": {
                    "approval_review_evidence": {
                        "review_wait_satisfied": True,
                        "event_sequence_no": 10,
                        "event_digest": "sha256:" + "4" * 64,
                    }
                }
            },
            "currency": {
                "approval": {
                    "approval_review_evidence": {
                        "review_wait_satisfied": True,
                        "event_sequence_no": 14,
                        "event_digest": "sha256:" + "5" * 64,
                    }
                }
            },
        },
    }


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _fixture(tmp_path: Path):
    builder = _load_script("build_completed_state_restart_receipt.py")
    verifier = _load_script("verify_completed_state_restart_receipt.py")
    before = _state()
    after = deepcopy(before)
    golden_runs = tmp_path / "golden-runs"
    summary = {"status": "PASS", "run_id": before["execution"]["run_id"]}
    _write_json(golden_runs / "run-00001" / "summary.json", summary)
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    receipt_path = tmp_path / "restart-receipt.json"
    _write_json(before_path, before)
    _write_json(after_path, after)
    receipt = builder.build_receipt(
        before_state=before,
        after_state=after,
        golden_runs_root=golden_runs,
    )
    _write_json(receipt_path, receipt)
    return builder, verifier, before_path, after_path, receipt_path, golden_runs


def test_completed_state_restart_receipt_is_independently_verifiable(
    tmp_path: Path,
) -> None:
    _, verifier, before, after, receipt, golden_runs = _fixture(tmp_path)
    result = verifier.verify(
        receipt_path=receipt,
        before_path=before,
        after_path=after,
        golden_runs_root=golden_runs,
    )
    assert result == {
        "status": "PASS",
        "verification_mode": "STDLIB_ONLY_NO_PRODUCT_IMPORTS",
        "product_imports": 0,
        "run_id": "run:golden-competition:test-restart",
        "state_stable": True,
        "failures": [],
    }


def test_builder_rejects_changed_reopened_state(tmp_path: Path) -> None:
    builder = _load_script("build_completed_state_restart_receipt.py")
    before = _state()
    after = deepcopy(before)
    after["experience_governance"]["callable"] = False
    golden_runs = tmp_path / "golden-runs"
    _write_json(
        golden_runs / "run-00001" / "summary.json",
        {"status": "PASS", "run_id": before["execution"]["run_id"]},
    )
    with pytest.raises(
        builder.RestartReceiptError,
        match="COMPLETED_EXPERIENCE_GOVERNANCE_INVALID",
    ):
        builder.build_receipt(
            before_state=before,
            after_state=after,
            golden_runs_root=golden_runs,
        )


def test_verifier_rejects_receipt_tamper(tmp_path: Path) -> None:
    _, verifier, before, after, receipt, golden_runs = _fixture(tmp_path)
    value = json.loads(receipt.read_text(encoding="utf-8"))
    value["state_stable"] = False
    _write_json(receipt, value)
    result = verifier.verify(
        receipt_path=receipt,
        before_path=before,
        after_path=after,
        golden_runs_root=golden_runs,
    )
    assert result["status"] == "FAIL"
    assert result["failures"] == ["RECEIPT_DIGEST", "RECEIPT_FACTS"]


def test_verifier_rejects_second_golden_run(tmp_path: Path) -> None:
    _, verifier, before, after, receipt, golden_runs = _fixture(tmp_path)
    _write_json(
        golden_runs / "run-00002" / "summary.json",
        {"status": "PASS", "run_id": "run:golden-competition:foreign"},
    )
    result = verifier.verify(
        receipt_path=receipt,
        before_path=before,
        after_path=after,
        golden_runs_root=golden_runs,
    )
    assert result["status"] == "FAIL"
    assert "GOLDEN_RUN_DIRECTORY_COUNT" in result["failures"]
    assert "RECEIPT_FACTS" in result["failures"]


def test_public_frozen_golden_alias_is_independently_verifiable(
    tmp_path: Path,
) -> None:
    _, verifier, before, after, receipt, golden_runs = _fixture(tmp_path)
    frozen = tmp_path / "public-pack" / "golden-run"
    frozen.parent.mkdir(parents=True)
    (golden_runs / "run-00001").rename(frozen)

    result = verifier.verify(
        receipt_path=receipt,
        before_path=before,
        after_path=after,
        golden_run=frozen,
    )

    assert result["status"] == "PASS"
    assert result["failures"] == []
