#!/usr/bin/env python3
"""Independently verify the Formation-compiled TeamHarness evidence pack.

This verifier intentionally imports no OrgRebase runtime, runner, Pydantic
contract, or TeamHarness code.  It recomputes content digests and derives the
only permitted native task set directly from the sealed execution-plan bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

_EVIDENCE_CLASS = "CONTROLLED_LOCAL_FORMATION_COMPILED_AGENTTEAMS"
_CLAIM_BOUNDARY = "PINNED_IN_PROCESS_TEAMHARNESS_DYNAMIC_TOPOLOGY_NOT_LIVE_WORKER_OR_BUSINESS_ADMISSION"
_DOMAIN_SET = ("legal", "product")
_TASK_ACTIONS = (
    "delegate_task",
    "ack_task",
    "submit_task",
    "check_task",
    "accept_task_result",
)
_PROJECT_SEQUENCE = (
    "create_project",
    "plan_dag",
    "ready_nodes",
    *_TASK_ACTIONS,
    *_TASK_ACTIONS,
    "ready_nodes",
    *_TASK_ACTIONS,
    "complete_project",
)
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class FormationTaskflowVerificationError(RuntimeError):
    """Stable independent-verifier failure."""


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


def _bytes_digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FormationTaskflowVerificationError(f"FORMATION_TASKFLOW_JSON_INVALID:{path.name}") from exc
    if not isinstance(value, dict):
        raise FormationTaskflowVerificationError(f"FORMATION_TASKFLOW_JSON_OBJECT_REQUIRED:{path.name}")
    return value


def _load_list(path: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FormationTaskflowVerificationError(f"FORMATION_TASKFLOW_JSON_INVALID:{path.name}") from exc
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise FormationTaskflowVerificationError(f"FORMATION_TASKFLOW_JSON_LIST_REQUIRED:{path.name}")
    return value


def _sealed(value: dict[str, Any]) -> bool:
    supplied = value.get("digest")
    return isinstance(supplied, str) and supplied == _digest(
        {key: item for key, item in value.items() if key != "digest"}
    )


def _raw_payload(raw: dict[str, Any]) -> dict[str, Any]:
    try:
        text = raw["response"]["content"][0]["text"]
        value = json.loads(text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise FormationTaskflowVerificationError("FORMATION_TASKFLOW_RAW_MCP_PAYLOAD_INVALID") from exc
    if not isinstance(value, dict):
        raise FormationTaskflowVerificationError("FORMATION_TASKFLOW_RAW_MCP_PAYLOAD_INVALID")
    return value


def _source_from_lock(lock_path: Path) -> dict[str, Any]:
    lock = _load(lock_path)
    files = lock.get("source_files")
    if (
        not isinstance(files, dict)
        or len(files) != 3
        or not all(isinstance(key, str) and isinstance(value, str) for key, value in files.items())
        or not isinstance(lock.get("upstream"), str)
        or not isinstance(lock.get("commit"), str)
    ):
        raise FormationTaskflowVerificationError("FORMATION_TASKFLOW_SOURCE_LOCK_INVALID")
    return {
        "checkout": "agentteams://pinned-checkout",
        "origin": lock["upstream"],
        "commit": lock["commit"],
        "source_lock_digest": _digest(lock),
        "files": dict(sorted(files.items())),
        "path_disclosure_status": "PUBLIC_PATH_REDACTED",
        "status": "PASS",
    }


def _verify_checkout(checkout: Path, lock_path: Path) -> dict[str, Any]:
    source = _source_from_lock(lock_path)
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=checkout,
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        ).stdout.strip()
        origin = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=checkout,
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise FormationTaskflowVerificationError("FORMATION_TASKFLOW_CHECKOUT_GIT_FAILED") from exc
    if commit != source["commit"]:
        raise FormationTaskflowVerificationError("FORMATION_TASKFLOW_CHECKOUT_COMMIT_DRIFT")
    if origin.removesuffix(".git").lower() != source["origin"].removesuffix(".git").lower():
        raise FormationTaskflowVerificationError("FORMATION_TASKFLOW_CHECKOUT_ORIGIN_DRIFT")
    for relative, expected in source["files"].items():
        path = (checkout / relative).resolve()
        if checkout not in path.parents or not path.is_file():
            raise FormationTaskflowVerificationError(f"FORMATION_TASKFLOW_CHECKOUT_SOURCE_MISSING:{relative}")
        if _bytes_digest(path.read_bytes()) != expected:
            raise FormationTaskflowVerificationError(f"FORMATION_TASKFLOW_CHECKOUT_SOURCE_DRIFT:{relative}")
    return source


def _plan_task_projection(task: dict[str, Any]) -> dict[str, Any]:
    title = (
        f"{str(task['domain_id']).title()} domain candidate"
        if task["task_kind"] == "DOMAIN"
        else "Independent reviewer barrier"
    )
    actor = re.sub(
        r"[^a-zA-Z0-9._=+/\-]+",
        "-",
        str(task["assignee_actor_id"]),
    ).strip("-")
    return {
        "taskId": task["task_id"],
        "title": title,
        "assignedTo": f"@{actor}:controlled.local",
        "dependsOn": list(task["depends_on"]),
    }


def _expected_from_plan(plan: dict[str, Any]) -> tuple[list[dict[str, Any]], tuple[str, ...], str]:
    if not _sealed(plan):
        raise FormationTaskflowVerificationError("FORMATION_TASKFLOW_EXECUTION_PLAN_UNSEALED")
    if (
        plan.get("schema_version") != "orgrebase.agentteams-execution-plan.v1"
        or tuple(plan.get("selected_domain_ids") or ()) != _DOMAIN_SET
        or plan.get("candidate_only") is not True
        or plan.get("canonical_target_writes") != 0
    ):
        raise FormationTaskflowVerificationError("FORMATION_TASKFLOW_EXECUTION_PLAN_BOUNDARY")
    tasks = plan.get("tasks")
    if (
        not isinstance(tasks, list)
        or len(tasks) != 3
        or not all(isinstance(item, dict) and _sealed(item) for item in tasks)
    ):
        raise FormationTaskflowVerificationError("FORMATION_TASKFLOW_EXECUTION_PLAN_TASKS")
    domain_tasks = tasks[:-1]
    reviewer = tasks[-1]
    if (
        tuple(item.get("domain_id") for item in domain_tasks) != _DOMAIN_SET
        or any(
            item.get("task_kind") != "DOMAIN"
            or item.get("depends_on") != []
            or item.get("candidate_only") is not True
            or item.get("canonical_target_writes") != 0
            or not item.get("capability_card_digest")
            or not item.get("actor_projection_digest")
            for item in domain_tasks
        )
        or reviewer.get("task_kind") != "REVIEWER_BARRIER"
        or reviewer.get("domain_id") != "reviewer"
        or tuple(reviewer.get("depends_on") or ()) != tuple(item["task_id"] for item in domain_tasks)
        or reviewer.get("candidate_only") is not True
        or reviewer.get("canonical_target_writes") != 0
    ):
        raise FormationTaskflowVerificationError("FORMATION_TASKFLOW_REVIEWER_BARRIER")
    task_ids = tuple(item["task_id"] for item in tasks)
    if len(set(task_ids)) != 3:
        raise FormationTaskflowVerificationError("FORMATION_TASKFLOW_DUPLICATE_PLAN_TASK")
    return ([_plan_task_projection(item) for item in tasks], task_ids, reviewer["task_id"])


def verify_formation_taskflow_probe(
    *,
    evidence_dir: str | Path,
    lock_path: str | Path,
    checkout: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(evidence_dir).expanduser().resolve()
    lock = Path(lock_path).expanduser().resolve()
    plan = _load(root / "inputs/execution-plan.json")
    receipt = _load(root / "probe-receipt.json")
    journal = _load_list(root / "action-journal.json")
    expected_nodes, expected_task_ids, reviewer_task_id = _expected_from_plan(plan)

    # The plan_dag request is the materialized topology.  Compare it before
    # trusting journal summaries so add/remove/substitute attacks fail on the
    # exact task-set boundary even when another digest also becomes stale.
    plan_actions = [item for item in journal if item.get("action") == "plan_dag"]
    if len(plan_actions) != 1:
        raise FormationTaskflowVerificationError("FORMATION_TASKFLOW_PLAN_ACTION_COUNT")
    plan_raw = _load((root / str(plan_actions[0].get("raw_ref") or "")).resolve())
    try:
        actual_nodes = plan_raw["request"]["payload"]["tasks"]
    except (KeyError, TypeError) as exc:
        raise FormationTaskflowVerificationError("FORMATION_TASKFLOW_ACTUAL_TASK_SET_MISSING") from exc
    if actual_nodes != expected_nodes:
        raise FormationTaskflowVerificationError("FORMATION_TASKFLOW_ACTUAL_TASK_SET_MISMATCH")

    if not _sealed(receipt):
        raise FormationTaskflowVerificationError("FORMATION_TASKFLOW_RECEIPT_UNSEALED")
    if (
        receipt.get("evidence_class") != _EVIDENCE_CLASS
        or receipt.get("claim_boundary") != _CLAIM_BOUNDARY
        or receipt.get("execution_plan_id") != plan.get("id")
        or receipt.get("execution_plan_digest") != plan.get("digest")
        or receipt.get("formation_receipt_digest") != plan.get("formation_receipt_digest")
        or receipt.get("context_envelope_digest") != plan.get("context_envelope_digest")
        or receipt.get("coalition_plan_digest") != plan.get("coalition_plan_digest")
        or tuple(receipt.get("selected_domain_ids") or ()) != _DOMAIN_SET
        or tuple(receipt.get("expected_task_ids") or ()) != expected_task_ids
        or tuple(receipt.get("planned_task_ids") or ()) != expected_task_ids
        or tuple(receipt.get("actual_terminal_task_ids") or ()) != expected_task_ids
        or receipt.get("reviewer_task_id") != reviewer_task_id
        or receipt.get("project_terminal_state") != "completed"
        or receipt.get("candidate_status") != "CANDIDATE_PREPARED_FOR_CONTROL_ADMISSION"
        or receipt.get("candidate_only") is not True
        or receipt.get("canonical_target_writes") != 0
    ):
        raise FormationTaskflowVerificationError("FORMATION_TASKFLOW_RECEIPT_BOUNDARY")

    current_source = (
        _source_from_lock(lock)
        if checkout is None
        else _verify_checkout(Path(checkout).expanduser().resolve(), lock)
    )
    if (
        receipt.get("source_verification") != current_source
        or _load(root / "source-verification.json") != current_source
    ):
        raise FormationTaskflowVerificationError("FORMATION_TASKFLOW_SOURCE_VERIFICATION")

    if (
        len(journal) != len(_PROJECT_SEQUENCE)
        or tuple(item.get("action") for item in journal) != _PROJECT_SEQUENCE
    ):
        raise FormationTaskflowVerificationError("FORMATION_TASKFLOW_ACTION_SEQUENCE")
    action_by_digest: dict[str, tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = {}
    for sequence, action in enumerate(journal, start=1):
        base = {key: value for key, value in action.items() if key != "digest"}
        digest = action.get("digest")
        if action.get("sequence") != sequence or not isinstance(digest, str) or digest != _digest(base):
            raise FormationTaskflowVerificationError(f"FORMATION_TASKFLOW_ACTION_DIGEST:{sequence}")
        raw_ref = str(action.get("raw_ref") or "")
        raw_path = (root / raw_ref).resolve()
        if root not in raw_path.parents or not raw_path.is_file():
            raise FormationTaskflowVerificationError(f"FORMATION_TASKFLOW_RAW_MCP_MISSING:{sequence}")
        raw = _load(raw_path)
        payload = _raw_payload(raw)
        if (
            raw.get("sequence") != sequence
            or raw.get("key") != action.get("key")
            or action.get("request_digest") != _digest(raw.get("request"))
            or action.get("response_digest") != _digest(raw.get("response"))
            or action.get("payload_digest") != _digest(payload)
            or action.get("ok") is not (payload.get("ok") is True)
        ):
            raise FormationTaskflowVerificationError(f"FORMATION_TASKFLOW_RAW_MCP_BINDING:{sequence}")
        action_by_digest[digest] = (action, raw, payload)

    if receipt.get("agentteams_action_count") != len(journal) or receipt.get("agentteams_action_digests") != [
        item["digest"] for item in journal
    ]:
        raise FormationTaskflowVerificationError("FORMATION_TASKFLOW_ACTION_RECEIPT_SET")

    task_receipts = receipt.get("task_receipts")
    if (
        not isinstance(task_receipts, list)
        or tuple(item.get("task_id") for item in task_receipts if isinstance(item, dict)) != expected_task_ids
    ):
        raise FormationTaskflowVerificationError("FORMATION_TASKFLOW_TASK_RECEIPT_SET")
    plan_task_by_id = {item["task_id"]: item for item in plan["tasks"]}
    for task_receipt in task_receipts:
        if not isinstance(task_receipt, dict) or not _sealed(task_receipt):
            raise FormationTaskflowVerificationError("FORMATION_TASKFLOW_TASK_RECEIPT_UNSEALED")
        task_id = task_receipt["task_id"]
        plan_task = plan_task_by_id[task_id]
        if (
            task_receipt.get("task_kind") != plan_task.get("task_kind")
            or task_receipt.get("domain_id") != plan_task.get("domain_id")
            or task_receipt.get("assignee_actor_id") != plan_task.get("assignee_actor_id")
            or task_receipt.get("depends_on") != plan_task.get("depends_on")
            or task_receipt.get("execution_task_digest") != plan_task.get("digest")
            or task_receipt.get("terminal_status") != "completed"
            or task_receipt.get("candidate_only") is not True
            or task_receipt.get("canonical_target_writes") != 0
        ):
            raise FormationTaskflowVerificationError(f"FORMATION_TASKFLOW_TASK_RECEIPT_BOUNDARY:{task_id}")
        action_digests = task_receipt.get("action_digests")
        if not isinstance(action_digests, dict) or set(action_digests) != set(_TASK_ACTIONS):
            raise FormationTaskflowVerificationError(f"FORMATION_TASKFLOW_TASK_ACTION_SET:{task_id}")
        evidence = [action_by_digest.get(action_digests[name]) for name in _TASK_ACTIONS]
        if any(item is None for item in evidence):
            raise FormationTaskflowVerificationError(f"FORMATION_TASKFLOW_TASK_ACTION_MISSING:{task_id}")
        delegate = evidence[0]
        ack = evidence[1]
        submit = evidence[2]
        check = evidence[3]
        accept = evidence[4]
        assert delegate is not None and ack is not None and submit is not None
        assert check is not None and accept is not None
        try:
            spec_text = delegate[1]["request"]["payload"]["spec"]
            spec = json.loads(spec_text)
            submitted_summary = submit[1]["request"]["payload"]["summary"]
            result = json.loads(submitted_summary)
            checked_summary = check[2]["result"]["summary"]
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise FormationTaskflowVerificationError(
                f"FORMATION_TASKFLOW_TASK_HANDOFF_INVALID:{task_id}"
            ) from exc
        if (
            delegate[1]["request"]["payload"].get("taskId") != task_id
            or delegate[1]["request"]["payload"].get("assignedTo") != task_receipt.get("transport_assignee")
            or spec.get("execution_plan_digest") != plan["digest"]
            or spec.get("execution_task") != plan_task
            or ack[2].get("spec") != spec_text + "\n"
            or ack[2].get("task", {}).get("status") != "in_progress"
            or submit[2].get("task", {}).get("status") != "submitted"
            or checked_summary != submitted_summary
            or result.get("run_id") != receipt.get("run_id")
            or result.get("project_id") != receipt.get("project_id")
            or result.get("task_id") != task_id
            or result.get("execution_plan_digest") != plan["digest"]
            or result.get("execution_task_digest") != plan_task["digest"]
            or result.get("candidate_only") is not True
            or result.get("canonical_target_writes") != 0
            or check[2].get("effective") is not True
            or check[2].get("validationErrors") != []
            or accept[2].get("accepted") is not True
            or accept[2].get("nodeStatus") != "completed"
            or task_receipt.get("task_spec_digest") != _bytes_digest(spec_text.encode("utf-8"))
            or task_receipt.get("result_digest") != _bytes_digest(submitted_summary.encode("utf-8"))
        ):
            raise FormationTaskflowVerificationError(f"FORMATION_TASKFLOW_TASK_LIFECYCLE:{task_id}")
        if plan_task["task_kind"] == "REVIEWER_BARRIER" and (
            tuple(result.get("reviewed_task_ids") or ()) != tuple(plan_task["depends_on"])
            or result.get("verdict_candidate") != "PASS"
            or result.get("control_plane_admission_required") is not True
        ):
            raise FormationTaskflowVerificationError("FORMATION_TASKFLOW_REVIEWER_RESULT_BOUNDARY")

    complete = action_by_digest[journal[-1]["digest"]][2]
    terminal_tasks = complete.get("project", {}).get("tasks")
    if (
        complete.get("project", {}).get("status") != "completed"
        or not isinstance(terminal_tasks, list)
        or tuple(item.get("task_id") for item in terminal_tasks) != expected_task_ids
        or any(item.get("status") != "completed" for item in terminal_tasks)
    ):
        raise FormationTaskflowVerificationError("FORMATION_TASKFLOW_TERMINAL_TASK_SET")

    public_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    )
    forbidden = (("/" + "Users/"), "/private/tmp", "/var/folders/", "finance-steward", "gtm-steward")
    if any(marker in public_text for marker in forbidden):
        raise FormationTaskflowVerificationError("FORMATION_TASKFLOW_PUBLIC_EVIDENCE_LEAK")
    result = {
        "status": "PASS",
        "evidence_class": _EVIDENCE_CLASS,
        "claim_boundary": _CLAIM_BOUNDARY,
        "verification_strength": (
            "PINNED_CHECKOUT_REPLAY" if checkout is not None else "RETAINED_LOCK_REPLAY"
        ),
        "run_id": receipt["run_id"],
        "execution_plan_digest": plan["digest"],
        "selected_domain_ids": list(_DOMAIN_SET),
        "planned_domain_ids": [item["domain_id"] for item in plan["tasks"] if item["task_kind"] == "DOMAIN"],
        "actual_domain_ids": [item["domain_id"] for item in task_receipts if item["task_kind"] == "DOMAIN"],
        "actual_task_ids": list(expected_task_ids),
        "agentteams_actions": len(journal),
        "task_binding_count": len(task_receipts),
        "domain_task_count": 2,
        "reviewer_task_count": 1,
        "topology_match": True,
        "project_terminal_state": "completed",
        "candidate_only": True,
        "canonical_target_writes": 0,
        "verified_receipt_digest": receipt["digest"],
    }
    return {**result, "digest": _digest(result)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--lock", required=True)
    parser.add_argument("--checkout")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        result = verify_formation_taskflow_probe(
            evidence_dir=args.evidence,
            lock_path=args.lock,
            checkout=args.checkout,
        )
    except FormationTaskflowVerificationError as exc:
        print(
            json.dumps(
                {"status": "FAIL", "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1
    encoded = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        destination = Path(args.output).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.tmp")
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(destination)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
