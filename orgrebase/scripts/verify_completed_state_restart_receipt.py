#!/usr/bin/env python3
"""Independently verify a completed-state restart receipt using stdlib only."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "orgrebase.completed-state-restart-receipt.v1"
EVIDENCE_CLASS = "CONTROLLED_LOCAL_COMPLETED_STATE_RESTART"
CLAIM_BOUNDARY = (
    "SERVICE_REOPEN_PERSISTENCE_ONLY_NOT_KILLED_PROCESS_RESUME_OR_HA_DR"
)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(path.name)
    return value


def _state_facts(state: dict[str, Any]) -> dict[str, Any]:
    run_id = state["execution"]["run_id"]
    oac = state["execution"]["oac_activation"]
    task_intake = state["task_intake"]
    experience = state["experience_governance"]
    event_chain = state["event_chain"]
    launch_approval = state["changes"]["launch_date"]["approval"][
        "approval_review_evidence"
    ]
    currency_approval = state["changes"]["currency"]["approval"][
        "approval_review_evidence"
    ]
    if not (
        state.get("stage") == "QUOTE_V3"
        and state["quote"]["version"] == "v3"
        and isinstance(run_id, str)
        and run_id.startswith("run:golden-competition:")
        and state["competition_evidence"]["run_id"] == run_id
        and oac.get("status") == "CONSUMED_BY_QUOTE_FORMATION"
        and oac.get("execution_run_id") == run_id
        and task_intake.get("status") == "FORMATION_COMPLETED"
        and task_intake.get("run_id") == run_id
        and task_intake.get("intake_persisted") is True
        and experience.get("status") == "APPROVED_CANARY"
        and experience.get("discoverable") is True
        and experience.get("loadable") is True
        and experience.get("callable") is True
        and experience.get("current_quote_consumed_candidate") is False
        and event_chain.get("status") == "PASS"
        and event_chain.get("events") == 16
        and isinstance(event_chain.get("head_digest"), str)
        and launch_approval.get("review_wait_satisfied") is True
        and launch_approval.get("event_sequence_no") == 10
        and currency_approval.get("review_wait_satisfied") is True
        and currency_approval.get("event_sequence_no") == 14
    ):
        raise ValueError("COMPLETED_STATE_FACTS")
    return {
        "stage": "QUOTE_V3",
        "quote_version": "v3",
        "run_id": run_id,
        "oac_activation_status": oac["status"],
        "oac_activation_binding_digest": oac["activation_binding_digest"],
        "task_intake_status": task_intake["status"],
        "task_intake_digest": task_intake["digest"],
        "experience_status": experience["status"],
        "experience_discoverable": True,
        "experience_loadable": True,
        "experience_callable": True,
        "current_quote_consumed_candidate": False,
        "event_count": 16,
        "event_head_digest": event_chain["head_digest"],
        "approval_event_sequence_nos": [10, 14],
        "approval_event_digests": [
            launch_approval["event_digest"],
            currency_approval["event_digest"],
        ],
    }


def verify(
    *,
    receipt_path: Path,
    before_path: Path,
    after_path: Path,
    golden_runs_root: Path | None = None,
    golden_run: Path | None = None,
) -> dict[str, Any]:
    failures: list[str] = []

    def fail(code: str) -> None:
        if code not in failures:
            failures.append(code)

    try:
        receipt = _load(receipt_path)
        before = _load(before_path)
        after = _load(after_path)
        before_facts = _state_facts(before)
        after_facts = _state_facts(after)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return {
            "status": "FAIL",
            "verification_mode": "STDLIB_ONLY_NO_PRODUCT_IMPORTS",
            "product_imports": 0,
            "failures": ["RESTART_INPUT_INVALID"],
        }

    before_digest = _digest(before)
    after_digest = _digest(after)
    if before_facts != after_facts:
        fail("RESTART_FACTS_CHANGED")
    if before_digest != after_digest:
        fail("RESTART_STATE_CHANGED")

    if (golden_runs_root is None) == (golden_run is None):
        return {
            "status": "FAIL",
            "verification_mode": "STDLIB_ONLY_NO_PRODUCT_IMPORTS",
            "product_imports": 0,
            "failures": ["GOLDEN_RUN_INPUT_CARDINALITY"],
        }
    if golden_runs_root is not None:
        run_directories = sorted(
            path
            for path in golden_runs_root.glob("run-*")
            if path.is_dir() and not path.is_symlink()
        )
    else:
        assert golden_run is not None
        run_directories = (
            [golden_run]
            if golden_run.is_dir() and not golden_run.is_symlink()
            else []
        )
    summary: dict[str, Any] = {}
    if len(run_directories) != 1:
        fail("GOLDEN_RUN_DIRECTORY_COUNT")
    else:
        try:
            summary = _load(run_directories[0] / "summary.json")
        except (OSError, ValueError, json.JSONDecodeError):
            fail("GOLDEN_RUN_SUMMARY")
        if (
            summary.get("status") != "PASS"
            or summary.get("run_id") != before_facts["run_id"]
        ):
            fail("GOLDEN_RUN_SUMMARY")

    if receipt.get("schema_version") != SCHEMA_VERSION:
        fail("RECEIPT_SCHEMA_VERSION")
    if receipt.get("digest") != _digest(
        {key: value for key, value in receipt.items() if key != "digest"}
    ):
        fail("RECEIPT_DIGEST")
    source_run_ref = receipt.get("golden_run_ref")
    if not isinstance(source_run_ref, str) or re.fullmatch(
        r"run-[0-9]+", source_run_ref
    ) is None:
        fail("GOLDEN_SOURCE_RUN_REF")
    if (
        golden_runs_root is not None
        and len(run_directories) == 1
        and source_run_ref != run_directories[0].name
    ):
        fail("GOLDEN_SOURCE_RUN_REF")
    expected_body = {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS",
        "evidence_class": EVIDENCE_CLASS,
        "claim_boundary": CLAIM_BOUNDARY,
        "store_profile": "FILE_BACKED_SQLITE",
        "closed_stage": before_facts["stage"],
        "reopened_stage": after_facts["stage"],
        "state_digest_before_close": before_digest,
        "state_digest_after_reopen": after_digest,
        "state_stable": True,
        "run_id": before_facts["run_id"],
        "golden_run_directory_count": 1,
        "golden_run_ref": source_run_ref,
        "golden_summary_digest": _digest(summary) if summary else None,
        "persisted_facts": before_facts,
    }
    if {key: value for key, value in receipt.items() if key != "digest"} != expected_body:
        fail("RECEIPT_FACTS")

    return {
        "status": "PASS" if not failures else "FAIL",
        "verification_mode": "STDLIB_ONLY_NO_PRODUCT_IMPORTS",
        "product_imports": 0,
        "run_id": before_facts["run_id"],
        "state_stable": before_digest == after_digest,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    golden = parser.add_mutually_exclusive_group(required=True)
    golden.add_argument("--golden-runs", type=Path)
    golden.add_argument("--golden-run", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify(
        receipt_path=args.receipt,
        before_path=args.before,
        after_path=args.after,
        golden_runs_root=args.golden_runs,
        golden_run=args.golden_run,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
