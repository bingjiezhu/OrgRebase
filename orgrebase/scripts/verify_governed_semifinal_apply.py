#!/usr/bin/env python3
"""Independently verify the governed semifinal pack using only the stdlib."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

ZERO_DIGEST = "sha256:" + "0" * 64
EXPECTED_RUN_ID = "run:orgrebase:semifinal-closure:quote-001"


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _record(value: dict[str, Any], failures: list[str], label: str) -> None:
    declared = value.get("digest")
    body = {key: item for key, item in value.items() if key != "digest"}
    if declared != _digest(body):
        failures.append(f"{label}_DIGEST")


def _verify_index(root: Path, failures: list[str]) -> dict[str, Any]:
    index = _load(root / "evidence-index.json")
    _record(index, failures, "INDEX")
    expected = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": _file_digest(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and path != root / "evidence-index.json"
    ]
    if index.get("entries") != expected:
        failures.append("INDEX_ENTRIES")
    if index.get("entry_count") != len(expected):
        failures.append("INDEX_COUNT")
    if index.get("pack_digest") != _digest(expected):
        failures.append("INDEX_PACK_DIGEST")
    return index


def _verify_archive_databases(root: Path, failures: list[str]) -> bool:
    """Immutable reads require indexed, checkpointed archive files, never live databases."""
    valid = True
    for name in ("workspace.sqlite", "runtime-journal.sqlite"):
        path = root / "runtime" / name
        if not path.is_file() or path.is_symlink():
            failures.append(f"ARCHIVE_SQLITE_FILE_INVALID:{name}")
            valid = False
        for suffix in ("-wal", "-journal"):
            pending = path.with_name(path.name + suffix)
            if pending.exists() and pending.stat().st_size:
                failures.append(f"ARCHIVE_SQLITE_NOT_CHECKPOINTED:{name}")
                valid = False
    return valid


def _verify_event_chain(connection: sqlite3.Connection, failures: list[str]) -> str:
    rows = connection.execute(
        "SELECT sequence_no,event_type,payload_json,previous_digest,event_digest "
        "FROM domain_events ORDER BY sequence_no"
    ).fetchall()
    previous = ZERO_DIGEST
    for expected, row in enumerate(rows, start=1):
        payload = json.loads(row[2])
        envelope = {
            "sequence_no": expected,
            "event_type": row[1],
            "payload": payload,
            "previous_digest": previous,
        }
        observed = _digest(envelope)
        if row[0] != expected or row[3] != previous or row[4] != observed:
            failures.append(f"WORKSPACE_EVENT_CHAIN:{expected}")
            break
        previous = observed
    return previous


def _current_object(
    connection: sqlite3.Connection,
    object_id: str,
    failures: list[str],
) -> dict[str, Any]:
    row = connection.execute(
        "SELECT ov.payload_json FROM current_pointers cp "
        "JOIN object_versions ov ON ov.version_key=cp.version_key "
        "WHERE cp.object_id=?",
        (object_id,),
    ).fetchone()
    if row is None:
        failures.append(f"CURRENT_OBJECT_MISSING:{object_id}")
        return {}
    value = json.loads(row[0])
    declared = value.get("digest")
    digest_body = {
        key: item for key, item in value.items() if key not in {"digest", "state"}
    }
    if declared != _digest(digest_body):
        failures.append(f"CURRENT_OBJECT_DIGEST:{object_id}")
    return value


def _verify_workspace_database(
    root: Path,
    records: dict[str, dict[str, Any]],
    failures: list[str],
) -> None:
    connection = sqlite3.connect(
        (root / "runtime/workspace.sqlite").resolve().as_uri() + "?mode=ro&immutable=1", uri=True,
    )
    try:
        quote = _current_object(connection, "work:quote_acme", failures)
        graph = _current_object(connection, "graph:workspace", failures)
        if quote.get("version") != "v3" or graph.get("version") != "v3":
            failures.append("WORKSPACE_FINAL_POINTERS")
        for name, artifact_id in {
            "candidate-promotion": "candidate-promotion:enterprise-quote@v1",
            "launch-approval": "promotion-approval:launch-date@r1",
            "launch-apply": "promotion-apply:launch-date@r1",
            "currency-approval": "promotion-approval:currency@r1",
            "currency-apply": "promotion-apply:currency@r1",
        }.items():
            row = connection.execute(
                "SELECT payload_json,payload_digest FROM artifacts WHERE artifact_id=?",
                (artifact_id,),
            ).fetchone()
            if row is None:
                failures.append(f"WORKSPACE_ARTIFACT_MISSING:{name}")
                continue
            payload = json.loads(row[0])
            if payload != records[name] or row[1] != _digest(payload):
                failures.append(f"WORKSPACE_ARTIFACT_BINDING:{name}")
        head = _verify_event_chain(connection, failures)
        if records["final-state"].get("event_chain", {}).get("head_digest") != head:
            failures.append("WORKSPACE_EVENT_HEAD_BINDING")
    finally:
        connection.close()


def _verify_runtime_journal(root: Path, failures: list[str]) -> None:
    receipts = _load(root / "runtime/action-receipts.json")
    if not isinstance(receipts, list):
        failures.append("JOURNAL_RECEIPTS_ARRAY")
        return
    previous = ZERO_DIGEST
    for sequence, receipt in enumerate(receipts, start=1):
        if not isinstance(receipt, dict):
            failures.append(f"JOURNAL_RECEIPT_OBJECT:{sequence}")
            continue
        _record(receipt, failures, f"JOURNAL_RECEIPT_{sequence}")
        if (
            receipt.get("sequence") != sequence
            or receipt.get("run_id") != EXPECTED_RUN_ID
            or receipt.get("previous_action_digest") != previous
        ):
            failures.append(f"JOURNAL_RECEIPT_CHAIN:{sequence}")
        previous = str(receipt.get("digest"))
    verification = _load(root / "runtime/journal-verification.json")
    if verification != {
        "schema_version": "orgrebase.runtime-journal-verification.v1",
        "run_id": EXPECTED_RUN_ID,
        "valid": True,
        "action_count": 3,
        "attempt_count": 4,
        "checkpoint_count": 3,
        "action_receipt_count": 7,
        "head_action_digest": previous,
    }:
        failures.append("JOURNAL_VERIFICATION_BINDING")
    connection = sqlite3.connect(
        (root / "runtime/runtime-journal.sqlite").resolve().as_uri() + "?mode=ro&immutable=1", uri=True,
    )
    try:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "runtime_actions",
                "runtime_attempts",
                "runtime_checkpoints",
                "runtime_action_receipts",
            )
        }
        if counts != {
            "runtime_actions": 3,
            "runtime_attempts": 4,
            "runtime_checkpoints": 3,
            "runtime_action_receipts": 7,
        }:
            failures.append("JOURNAL_SQLITE_COUNTS")
        action_rows = connection.execute(
            "SELECT action_key,action_type,request_json,request_digest,state,"
            "result_json,result_digest,latest_receipt_digest "
            "FROM runtime_actions ORDER BY action_key"
        ).fetchall()
        expected_actions = {
            "01-prepare-promotion-preview": "WORKSPACE_PREPARE",
            "02-approve-apply-launch": "WORKSPACE_APPROVE_APPLY",
            "03-recover-and-apply-currency": "WORKSPACE_RECOVER_APPROVE_APPLY",
        }
        if (
            len(action_rows) != 3
            or {row[0]: row[1] for row in action_rows} != expected_actions
            or any(row[4] != "COMMITTED" for row in action_rows)
        ):
            failures.append("JOURNAL_SQLITE_TERMINAL_STATES")
        last_receipt_by_action = {
            str(receipt["action_key"]): str(receipt["digest"])
            for receipt in receipts
            if isinstance(receipt, dict)
        }
        for row in action_rows:
            action_key = str(row[0])
            try:
                request = json.loads(row[2])
                result = json.loads(row[5])
            except (TypeError, json.JSONDecodeError):
                failures.append(f"JOURNAL_SQLITE_ACTION_JSON:{action_key}")
                continue
            if row[3] != _digest(request):
                failures.append(f"JOURNAL_SQLITE_REQUEST_DIGEST:{action_key}")
            if row[6] != _digest(result):
                failures.append(f"JOURNAL_SQLITE_RESULT_DIGEST:{action_key}")
            if row[7] != last_receipt_by_action.get(action_key):
                failures.append(f"JOURNAL_SQLITE_LATEST_RECEIPT:{action_key}")

        sqlite_receipts = connection.execute(
            "SELECT sequence,receipt_json,action_digest "
            "FROM runtime_action_receipts ORDER BY sequence"
        ).fetchall()
        if len(sqlite_receipts) != len(receipts):
            failures.append("JOURNAL_SQLITE_RECEIPT_COUNT")
        else:
            for sequence, (row, retained) in enumerate(
                zip(sqlite_receipts, receipts, strict=True),
                start=1,
            ):
                try:
                    payload = json.loads(row[1])
                except json.JSONDecodeError:
                    failures.append(f"JOURNAL_SQLITE_RECEIPT_JSON:{sequence}")
                    continue
                if (
                    row[0] != sequence
                    or payload != retained
                    or row[2] != retained.get("digest")
                ):
                    failures.append(f"JOURNAL_SQLITE_RECEIPT_BINDING:{sequence}")
    finally:
        connection.close()


def verify(root: Path, parent: Path) -> dict[str, Any]:
    failures: list[str] = []
    index = _verify_index(root, failures)
    # A failed seal cannot authorize immutable SQLite access. In particular,
    # opening a WAL database as immutable must not hide uncheckpointed writes.
    databases_sealed = not failures and _verify_archive_databases(root, failures)
    summary = _load(root / "summary.json")
    _record(summary, failures, "SUMMARY")
    parent_summary = _load(parent / "summary.json")
    _record(parent_summary, failures, "PARENT_SUMMARY")
    if (
        summary.get("status") != "PASS"
        or summary.get("run_id") != EXPECTED_RUN_ID
        or summary.get("parent_candidate_summary_digest") != parent_summary.get("digest")
        or summary.get("parent_terminal_state") != "CANDIDATE_ACCEPTED"
        or summary.get("terminal_state") != "GOVERNED_APPLIED"
        or summary.get("final_quote_ref") != "work:quote_acme@v3"
        or summary.get("proposal_plane_target_writes") != 0
        or summary.get("canonical_target_writes") != 6
        or summary.get("approval_input_mode") != "CONTROLLED_LOCAL_SCRIPTED_COMMAND"
        or summary.get("external_human_validation") != "NOT_RUN"
        or summary.get("production_readiness") is not False
    ):
        failures.append("SUMMARY_SEMANTICS")

    records: dict[str, dict[str, Any]] = {}
    for name in (
        "candidate-promotion",
        "launch-approval",
        "launch-apply",
        "currency-approval",
        "currency-apply",
        "final-state",
        "run-bindings",
    ):
        value = _load(root / "records" / f"{name}.json")
        if not isinstance(value, dict):
            failures.append(f"RECORD_OBJECT:{name}")
            continue
        _record(value, failures, f"RECORD_{name.upper()}")
        records[name] = value
    promotion = records.get("candidate-promotion", {})
    launch_approval = records.get("launch-approval", {})
    currency_approval = records.get("currency-approval", {})
    launch_apply = records.get("launch-apply", {})
    currency_apply = records.get("currency-apply", {})
    bindings = records.get("run-bindings", {})
    if (
        promotion.get("candidate_action") != "APPLY_QUOTE"
        or promotion.get("proposal_plane_target_writes") != 0
        or len(promotion.get("roots", {}).get("domain_result_digests", {})) != 4
        or launch_approval.get("actor_id") != "human:product-owner"
        or currency_approval.get("actor_id") != "human:finance-owner"
        or launch_approval.get("input_mode") != "CONTROLLED_LOCAL_SCRIPTED_COMMAND"
        or currency_approval.get("input_mode") != "CONTROLLED_LOCAL_SCRIPTED_COMMAND"
        or launch_apply.get("before_quote_ref") != "work:quote_acme@v1"
        or launch_apply.get("after_quote_ref") != "work:quote_acme@v2"
        or currency_apply.get("before_quote_ref") != "work:quote_acme@v2"
        or currency_apply.get("after_quote_ref") != "work:quote_acme@v3"
        or launch_apply.get("canonical_target_writes") != 2
        or currency_apply.get("canonical_target_writes") != 2
        or set(bindings.get("stage_run_ids", {}).values()) != {EXPECTED_RUN_ID}
        or bindings.get("all_same_run") is not True
    ):
        failures.append("GOVERNANCE_RECORD_SEMANTICS")
    if summary.get("same_run_binding_digest") != bindings.get("digest"):
        failures.append("SUMMARY_SAME_RUN_BINDING")

    process = _load(root / "runtime/process-recovery.json")
    _record(process, failures, "PROCESS_RECOVERY")
    kills = process.get("kill_receipts", [])
    for index_number, receipt in enumerate(kills, start=1):
        if isinstance(receipt, dict):
            _record(receipt, failures, f"PROCESS_KILL_{index_number}")
    if (
        process.get("sigkill_count") != 2
        or process.get("real_process_restart_count") != 2
        or process.get("recovery_dispositions") != ["RETRY_INTENT", "ADOPT_COMMITTED"]
        or process.get("finish_worker_returncode") != 0
        or process.get("final_stage") != "QUOTE_V3"
        or len(set(process.get("worker_pids", []))) != 2
        or len(kills) != 2
        or any(
            item.get("signal") != "SIGKILL" or item.get("returncode") != -9
            for item in kills
            if isinstance(item, dict)
        )
    ):
        failures.append("PROCESS_RECOVERY_SEMANTICS")

    if databases_sealed:
        _verify_runtime_journal(root, failures)
        if len(records) == 7:
            _verify_workspace_database(root, records, failures)
    result = {
        "schema_version": "orgrebase.semifinal-governed-verification.v1",
        "status": "PASS" if not failures else "FAIL",
        "run_id": summary.get("run_id"),
        "terminal_state": summary.get("terminal_state"),
        "final_quote_ref": summary.get("final_quote_ref"),
        "sigkill_count": process.get("sigkill_count"),
        "pack_digest": index.get("pack_digest"),
        "failures": failures,
        "verification_mode": "INDEPENDENT_STDLIB_JSON_SQLITE_REPLAY",
    }
    return {**result, "digest": _digest(result)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "evidence/semifinal-governed/latest",
    )
    parser.add_argument(
        "--parent-pack",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "evidence/semifinal-closure/latest",
    )
    args = parser.parse_args()
    result = verify(args.evidence.resolve(), args.parent_pack.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
