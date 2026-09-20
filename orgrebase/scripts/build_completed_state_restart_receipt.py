#!/usr/bin/env python3
"""Build a public-safe receipt for one completed-state service restart."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "orgrebase.completed-state-restart-receipt.v1"
EVIDENCE_CLASS = "CONTROLLED_LOCAL_COMPLETED_STATE_RESTART"
CLAIM_BOUNDARY = (
    "SERVICE_REOPEN_PERSISTENCE_ONLY_NOT_KILLED_PROCESS_RESUME_OR_HA_DR"
)


class RestartReceiptError(RuntimeError):
    """Raised when the supplied snapshots cannot prove a stable restart."""


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
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RestartReceiptError(f"INVALID_JSON:{path.name}") from exc
    if not isinstance(value, dict):
        raise RestartReceiptError(f"INVALID_OBJECT:{path.name}")
    return value


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise RestartReceiptError(code)


def _state_facts(state: dict[str, Any]) -> dict[str, Any]:
    try:
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
        quote_version = state["quote"]["version"]
        competition_run_id = state["competition_evidence"]["run_id"]
    except (KeyError, TypeError) as exc:
        raise RestartReceiptError("COMPLETED_STATE_FACTS_MISSING") from exc

    _require(state.get("stage") == "QUOTE_V3", "COMPLETED_STAGE_INVALID")
    _require(quote_version == "v3", "COMPLETED_QUOTE_VERSION_INVALID")
    _require(
        isinstance(run_id, str)
        and run_id.startswith("run:golden-competition:")
        and competition_run_id == run_id,
        "COMPLETED_RUN_BINDING_INVALID",
    )
    _require(
        oac.get("status") == "CONSUMED_BY_QUOTE_FORMATION"
        and oac.get("execution_run_id") == run_id,
        "COMPLETED_OAC_BINDING_INVALID",
    )
    _require(
        task_intake.get("status") == "FORMATION_COMPLETED"
        and task_intake.get("run_id") == run_id
        and task_intake.get("intake_persisted") is True,
        "COMPLETED_TASK_INTAKE_INVALID",
    )
    _require(
        experience.get("status") == "APPROVED_CANARY"
        and experience.get("discoverable") is True
        and experience.get("loadable") is True
        and experience.get("callable") is True
        and experience.get("current_quote_consumed_candidate") is False,
        "COMPLETED_EXPERIENCE_GOVERNANCE_INVALID",
    )
    _require(
        event_chain.get("status") == "PASS"
        and event_chain.get("events") == 16
        and isinstance(event_chain.get("head_digest"), str),
        "COMPLETED_EVENT_CHAIN_INVALID",
    )
    _require(
        launch_approval.get("review_wait_satisfied") is True
        and launch_approval.get("event_sequence_no") == 10
        and currency_approval.get("review_wait_satisfied") is True
        and currency_approval.get("event_sequence_no") == 14,
        "COMPLETED_APPROVAL_BINDING_INVALID",
    )
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


def build_receipt(
    *,
    before_state: dict[str, Any],
    after_state: dict[str, Any],
    golden_runs_root: Path,
) -> dict[str, Any]:
    before_facts = _state_facts(before_state)
    after_facts = _state_facts(after_state)
    before_digest = _digest(before_state)
    after_digest = _digest(after_state)
    _require(before_facts == after_facts, "RESTART_FACTS_CHANGED")
    _require(before_digest == after_digest, "RESTART_STATE_CHANGED")

    run_directories = sorted(
        path
        for path in golden_runs_root.glob("run-*")
        if path.is_dir() and not path.is_symlink()
    )
    _require(len(run_directories) == 1, "GOLDEN_RUN_DIRECTORY_COUNT_INVALID")
    summary = _load(run_directories[0] / "summary.json")
    _require(
        summary.get("status") == "PASS"
        and summary.get("run_id") == before_facts["run_id"],
        "GOLDEN_RUN_SUMMARY_INVALID",
    )

    body: dict[str, Any] = {
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
        "golden_run_ref": run_directories[0].name,
        "golden_summary_digest": _digest(summary),
        "persisted_facts": before_facts,
    }
    return {**body, "digest": _digest(body)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--golden-runs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt = build_receipt(
        before_state=_load(args.before),
        after_state=_load(args.after),
        golden_runs_root=args.golden_runs,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "PASS", "receipt": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
