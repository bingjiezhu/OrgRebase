#!/usr/bin/env python3
"""Run the same-run candidate-to-human-approval-to-canonical-apply closure.

Two worker processes are deliberately killed with SIGKILL after durable
checkpoints.  The supervisor reopens SQLite, fences uncertain attempts, adopts
or reconciles exact results, and completes Quote v3 under the original
semifinal run id.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.workspace.candidate_promotion import (
    APPLY_MEDIA_TYPE,
    APPROVAL_MEDIA_TYPE,
    PROMOTION_ARTIFACT_ID,
    PROMOTION_MEDIA_TYPE,
    PromotionApplyReceipt,
    PromotionApproval,
    apply_promoted_change,
    approve_promoted_change,
    build_candidate_promotion,
    load_candidate_promotion,
    persist_candidate_promotion,
    prepare_promoted_change,
)
from orgrebase.workspace.runtime_journal import RuntimeJournal
from orgrebase.workspace.service import WorkspaceService

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_ID = "run:orgrebase:semifinal-closure:quote-001"
DEFAULT_PARENT_PACK = ROOT / "evidence/semifinal-closure/latest"
DEFAULT_OUTPUT_ROOT = ROOT / "evidence/semifinal-governed"


class GovernedRunError(RuntimeError):
    """Fail-closed governed-run error."""


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise GovernedRunError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def _record(value: dict[str, Any]) -> dict[str, Any]:
    return {**value, "digest": sha256_digest(value)}


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _environment() -> dict[str, str]:
    environment = dict(os.environ)
    source = str(ROOT / "src")
    environment["PYTHONPATH"] = (
        source
        if not environment.get("PYTHONPATH")
        else source + os.pathsep + environment["PYTHONPATH"]
    )
    return environment


def _durable_marker(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    with path.open("wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _worker_result(
    *,
    phase: str,
    database: Path,
    parent_pack: Path,
    run_id: str,
) -> dict[str, Any]:
    service = WorkspaceService(store_path=database, workflow_run_id=run_id)
    try:
        if phase == "prepare":
            if service.state()["stage"] != "EMPTY":
                raise GovernedRunError("PREPARE_REQUIRES_EMPTY_WORKSPACE")
            formed = service.form_quote_with_dependency_evidence()
            bundle = build_candidate_promotion(
                service,
                evidence_pack=parent_pack,
                run_id=run_id,
            )
            persist_candidate_promotion(service, bundle)
            preview = prepare_promoted_change(
                service,
                "launch_date",
                promotion_digest=bundle.digest,
            )
            state = service.state()
            result = {
                "schema_version": "orgrebase.governed-worker-result.v1",
                "phase": phase,
                "run_id": run_id,
                "stage": state["stage"],
                "formation_run_id": formed["formation_run_id"],
                "workspace_tool_run_id": formed["tool_invocation"]["receipt"][
                    "workflow_run_id"
                ],
                "promotion_digest": bundle.digest,
                "preview_binding_digest": preview.digest,
                "state_digest": sha256_digest(state),
            }
        elif phase == "launch":
            bundle = load_candidate_promotion(service)
            preview = prepare_promoted_change(
                service,
                "launch_date",
                promotion_digest=bundle.digest,
            )
            approval = approve_promoted_change(
                service,
                "launch_date",
                actor_id="human:product-owner",
                preview_binding_digest=preview.digest,
            )
            applied = apply_promoted_change(
                service,
                "launch_date",
                promotion_approval_digest=approval.digest,
            )
            state = service.state()
            result = {
                "schema_version": "orgrebase.governed-worker-result.v1",
                "phase": phase,
                "run_id": run_id,
                "stage": state["stage"],
                "promotion_digest": bundle.digest,
                "approval_digest": approval.digest,
                "apply_receipt_digest": applied.digest,
                "after_quote_ref": applied.after_quote_ref,
                "state_digest": sha256_digest(state),
            }
        elif phase == "finish":
            bundle = load_candidate_promotion(service)
            launch_approval = PromotionApproval.model_validate(
                service.store.load_artifact(
                    "promotion-approval:launch-date@r1", APPROVAL_MEDIA_TYPE
                ).payload
            )
            # This is the response-loss recovery probe: the exact Apply command
            # is issued again after SIGKILL and must return the retained receipt.
            recovered_launch = apply_promoted_change(
                service,
                "launch_date",
                promotion_approval_digest=launch_approval.digest,
            )
            currency_preview = prepare_promoted_change(
                service,
                "currency",
                promotion_digest=bundle.digest,
            )
            currency_approval = approve_promoted_change(
                service,
                "currency",
                actor_id="human:finance-owner",
                preview_binding_digest=currency_preview.digest,
            )
            currency_apply = apply_promoted_change(
                service,
                "currency",
                promotion_approval_digest=currency_approval.digest,
            )
            state = service.state()
            result = {
                "schema_version": "orgrebase.governed-worker-result.v1",
                "phase": phase,
                "run_id": run_id,
                "stage": state["stage"],
                "promotion_digest": bundle.digest,
                "recovered_launch_apply_receipt_digest": recovered_launch.digest,
                "currency_approval_digest": currency_approval.digest,
                "currency_apply_receipt_digest": currency_apply.digest,
                "after_quote_ref": currency_apply.after_quote_ref,
                "state_digest": sha256_digest(state),
            }
        else:  # pragma: no cover - argparse closes the public surface
            raise GovernedRunError(f"UNKNOWN_WORKER_PHASE:{phase}")
        return _record(result)
    finally:
        service.close()


def _worker_main(args: argparse.Namespace) -> int:
    result = _worker_result(
        phase=args.worker_phase,
        database=args.database.resolve(),
        parent_pack=args.parent_pack.resolve(),
        run_id=args.run_id,
    )
    if args.commit_journal:
        if not all((args.journal, args.action_key, args.attempt, args.fencing_token)):
            raise GovernedRunError("WORKER_JOURNAL_BINDING_REQUIRED")
        with RuntimeJournal(args.journal) as journal:
            journal.commit_action(
                run_id=args.run_id,
                action_key=args.action_key,
                attempt=args.attempt,
                fencing_token=args.fencing_token,
                result=result,
            )
    if args.marker is not None:
        _durable_marker(args.marker, {**result, "worker_pid": os.getpid()})
    if args.wait_for_kill:
        while True:
            signal.pause()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _worker_command(
    *,
    phase: str,
    database: Path,
    parent_pack: Path,
    run_id: str,
    marker: Path | None = None,
    wait_for_kill: bool = False,
    journal: Path | None = None,
    action_key: str | None = None,
    attempt: int | None = None,
    fencing_token: str | None = None,
    commit_journal: bool = False,
) -> list[str]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker-phase",
        phase,
        "--database",
        str(database),
        "--parent-pack",
        str(parent_pack),
        "--run-id",
        run_id,
    ]
    if marker is not None:
        command.extend(["--marker", str(marker)])
    if wait_for_kill:
        command.append("--wait-for-kill")
    if commit_journal:
        command.extend(
            [
                "--commit-journal",
                "--journal",
                str(journal),
                "--action-key",
                str(action_key),
                "--attempt",
                str(attempt),
                "--fencing-token",
                str(fencing_token),
            ]
        )
    return command


def _spawn_kill(command: list[str], marker: Path) -> dict[str, Any]:
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 30
    while not marker.is_file():
        returncode = process.poll()
        if returncode is not None:
            stdout, stderr = process.communicate()
            raise GovernedRunError(
                f"WORKER_EXITED_BEFORE_MARKER:{returncode}:{stderr or stdout}"
            )
        if time.monotonic() >= deadline:
            process.kill()
            process.wait(timeout=10)
            raise GovernedRunError("WORKER_MARKER_TIMEOUT")
        time.sleep(0.05)
    marker_payload = _load(marker)
    if marker_payload.get("worker_pid") != process.pid:
        process.kill()
        process.wait(timeout=10)
        raise GovernedRunError("WORKER_MARKER_PID_MISMATCH")
    os.kill(process.pid, signal.SIGKILL)
    returncode = process.wait(timeout=10)
    stdout, stderr = process.communicate()
    if returncode != -signal.SIGKILL:
        raise GovernedRunError(f"WORKER_SIGKILL_NOT_OBSERVED:{returncode}")
    return _record(
        {
            "schema_version": "orgrebase.process-kill-receipt.v1",
            "phase": marker_payload["phase"],
            "run_id": marker_payload["run_id"],
            "worker_pid": process.pid,
            "signal": "SIGKILL",
            "returncode": returncode,
            "marker_digest": _file_digest(marker),
            "marker_result_digest": marker_payload["digest"],
            "stdout": stdout,
            "stderr": stderr,
        }
    )


def _run_worker(command: list[str]) -> tuple[dict[str, Any], int]:
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=_environment(),
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if result.returncode != 0:
        raise GovernedRunError(
            f"WORKER_FAILED:{result.returncode}:{result.stderr or result.stdout}"
        )
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise GovernedRunError("WORKER_RESULT_OBJECT_REQUIRED")
    return value, result.returncode


def _artifact(service: WorkspaceService, artifact_id: str, media_type: str) -> dict[str, Any]:
    return service.store.load_artifact(artifact_id, media_type).payload


def _retained_records(
    *,
    database: Path,
    run_id: str,
    stage: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    service = WorkspaceService.reopen(database, workflow_run_id=run_id)
    try:
        state = service.state()
        if state["stage"] != "QUOTE_V3":
            raise GovernedRunError("FINAL_QUOTE_V3_REQUIRED")
        promotion = _artifact(
            service,
            PROMOTION_ARTIFACT_ID,
            PROMOTION_MEDIA_TYPE,
        )
        records = {
            "candidate-promotion": promotion,
            "launch-approval": _artifact(
                service,
                "promotion-approval:launch-date@r1",
                APPROVAL_MEDIA_TYPE,
            ),
            "launch-apply": _artifact(
                service,
                "promotion-apply:launch-date@r1",
                APPLY_MEDIA_TYPE,
            ),
            "currency-approval": _artifact(
                service,
                "promotion-approval:currency@r1",
                APPROVAL_MEDIA_TYPE,
            ),
            "currency-apply": _artifact(
                service,
                "promotion-apply:currency@r1",
                APPLY_MEDIA_TYPE,
            ),
        }
        for name, value in records.items():
            _write(stage / "records" / f"{name}.json", value)
        state_record = _record(
            {
                "schema_version": "orgrebase.governed-final-state.v1",
                "run_id": run_id,
                "stage": state["stage"],
                "quote": state["quote"],
                "graph_pointer": state["graph_pointer"],
                "event_chain": state["event_chain"],
                "latest_outcome": state["latest_outcome"],
            }
        )
        _write(stage / "records/final-state.json", state_record)

        formation = service.store.load_artifact("task-receipt:quote_acme@v1").payload
        formation_trace = service.store.load_artifact(formation["trace_ref"]).payload
        tool = state["dependency_evidence_tool"]["invocation"]["receipt"]
        launch_apply = PromotionApplyReceipt.model_validate(records["launch-apply"])
        currency_apply = PromotionApplyReceipt.model_validate(records["currency-apply"])
        run_bindings = _record(
            {
                "schema_version": "orgrebase.governed-run-bindings.v1",
                "run_id": run_id,
                "stage_run_ids": {
                    "formation_trace": formation_trace["run_id"],
                    "workspace_dependency_tool": tool["workflow_run_id"],
                    "candidate_promotion": promotion["run_id"],
                    "launch_apply": launch_apply.run_id,
                    "currency_apply": currency_apply.run_id,
                },
                "all_same_run": True,
            }
        )
        if set(run_bindings["stage_run_ids"].values()) != {run_id}:
            raise GovernedRunError("SAME_RUN_BINDING_FAILED")
        _write(stage / "records/run-bindings.json", run_bindings)
        return state_record, run_bindings
    finally:
        service.close()


def _checkpoint_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()


def _write_index(stage: Path) -> dict[str, Any]:
    entries = [
        {
            "path": path.relative_to(stage).as_posix(),
            "sha256": _file_digest(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(stage.rglob("*"))
        if path.is_file() and path != stage / "evidence-index.json"
    ]
    index = _record(
        {
            "schema_version": "orgrebase.semifinal-governed-index.v1",
            "entry_count": len(entries),
            "entries": entries,
            "pack_digest": sha256_digest(entries),
        }
    )
    _write(stage / "evidence-index.json", index)
    return index


def _output_target(output_dir: Path) -> tuple[Path, Path]:
    output = output_dir.expanduser().resolve()
    root = DEFAULT_OUTPUT_ROOT.resolve()
    if output.parent != root or output.name in {"", "archive", "failed"}:
        raise GovernedRunError("UNSAFE_OUTPUT_TARGET")
    return output, root


def run_governed_semifinal(
    *,
    output_dir: Path,
    parent_pack: Path,
    run_id: str,
) -> dict[str, Any]:
    output, output_root = _output_target(output_dir)
    parent = parent_pack.expanduser().resolve()
    parent_summary = _load(parent / "summary.json")
    if parent_summary.get("run_id") != run_id or parent_summary.get("status") != "PASS":
        raise GovernedRunError("PARENT_PACK_NOT_ADMISSIBLE")
    output_root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".governed-stage-", dir=output_root))
    runtime = stage / "runtime"
    runtime.mkdir()
    database = runtime / "workspace.sqlite"
    journal_path = runtime / "runtime-journal.sqlite"
    process_receipts: list[dict[str, Any]] = []
    recoveries: list[dict[str, Any]] = []
    try:
        prepare_request = {
            "phase": "prepare",
            "run_id": run_id,
            "parent_summary_digest": parent_summary["digest"],
        }
        with RuntimeJournal(journal_path) as journal:
            prepare_intent = journal.begin_action(
                run_id=run_id,
                action_key="01-prepare-promotion-preview",
                action_type="WORKSPACE_PREPARE",
                request=prepare_request,
            )
            journal.write_checkpoint(
                run_id=run_id,
                action_key="01-prepare-promotion-preview",
                attempt=prepare_intent["attempt"],
                fencing_token=prepare_intent["fencing_token"],
                checkpoint_key="worker-spawned",
                payload={"expected_stage": "LAUNCH_PREVIEWED"},
            )
        prepare_marker = runtime / "prepare.marker.json"
        process_receipts.append(
            _spawn_kill(
                _worker_command(
                    phase="prepare",
                    database=database,
                    parent_pack=parent,
                    run_id=run_id,
                    marker=prepare_marker,
                    wait_for_kill=True,
                ),
                prepare_marker,
            )
        )
        prepare_result = _load(prepare_marker)
        with RuntimeJournal(journal_path) as journal:
            recovered = journal.recover_action(
                run_id=run_id,
                action_key="01-prepare-promotion-preview",
                request_digest=prepare_intent["request_digest"],
            )
            if recovered["disposition"] != "RETRY_INTENT":
                raise GovernedRunError("PREPARE_RETRY_RECOVERY_REQUIRED")
            recoveries.append(recovered)
            journal.write_checkpoint(
                run_id=run_id,
                action_key="01-prepare-promotion-preview",
                attempt=recovered["attempt"],
                fencing_token=recovered["fencing_token"],
                checkpoint_key="canonical-state-reconciled",
                payload={
                    "stage": prepare_result["stage"],
                    "state_digest": prepare_result["state_digest"],
                },
            )
            journal.commit_action(
                run_id=run_id,
                action_key="01-prepare-promotion-preview",
                attempt=recovered["attempt"],
                fencing_token=recovered["fencing_token"],
                result={key: value for key, value in prepare_result.items() if key != "worker_pid"},
            )

        launch_request = {
            "phase": "launch",
            "run_id": run_id,
            "promotion_digest": prepare_result["promotion_digest"],
            "preview_binding_digest": prepare_result["preview_binding_digest"],
        }
        with RuntimeJournal(journal_path) as journal:
            launch_intent = journal.begin_action(
                run_id=run_id,
                action_key="02-approve-apply-launch",
                action_type="WORKSPACE_APPROVE_APPLY",
                request=launch_request,
            )
            journal.write_checkpoint(
                run_id=run_id,
                action_key="02-approve-apply-launch",
                attempt=launch_intent["attempt"],
                fencing_token=launch_intent["fencing_token"],
                checkpoint_key="worker-spawned",
                payload={"expected_stage": "QUOTE_V2"},
            )
        launch_marker = runtime / "launch.marker.json"
        process_receipts.append(
            _spawn_kill(
                _worker_command(
                    phase="launch",
                    database=database,
                    parent_pack=parent,
                    run_id=run_id,
                    marker=launch_marker,
                    wait_for_kill=True,
                    journal=journal_path,
                    action_key="02-approve-apply-launch",
                    attempt=launch_intent["attempt"],
                    fencing_token=launch_intent["fencing_token"],
                    commit_journal=True,
                ),
                launch_marker,
            )
        )
        with RuntimeJournal(journal_path) as journal:
            recovered = journal.recover_action(
                run_id=run_id,
                action_key="02-approve-apply-launch",
                request_digest=launch_intent["request_digest"],
            )
            if recovered["disposition"] != "ADOPT_COMMITTED":
                raise GovernedRunError("LAUNCH_ADOPT_RECOVERY_REQUIRED")
            recoveries.append(recovered)

        finish_request = {
            "phase": "finish",
            "run_id": run_id,
            "launch_result_digest": _load(launch_marker)["digest"],
        }
        with RuntimeJournal(journal_path) as journal:
            finish_intent = journal.begin_action(
                run_id=run_id,
                action_key="03-recover-and-apply-currency",
                action_type="WORKSPACE_RECOVER_APPROVE_APPLY",
                request=finish_request,
            )
        finish_result, finish_returncode = _run_worker(
            _worker_command(
                phase="finish",
                database=database,
                parent_pack=parent,
                run_id=run_id,
            )
        )
        with RuntimeJournal(journal_path) as journal:
            journal.commit_action(
                run_id=run_id,
                action_key="03-recover-and-apply-currency",
                attempt=finish_intent["attempt"],
                fencing_token=finish_intent["fencing_token"],
                result=finish_result,
            )
            journal_verification = journal.verify_chain(run_id)
            action_receipts = journal.list_action_receipts(run_id)
        _write(stage / "runtime/action-receipts.json", action_receipts)
        _write(stage / "runtime/journal-verification.json", journal_verification)
        process_evidence = _record(
            {
                "schema_version": "orgrebase.process-recovery-evidence.v1",
                "run_id": run_id,
                "sigkill_count": 2,
                "real_process_restart_count": 2,
                "worker_pids": [item["worker_pid"] for item in process_receipts],
                "kill_receipts": process_receipts,
                "recovery_dispositions": [
                    item["disposition"] for item in recoveries
                ],
                "finish_worker_returncode": finish_returncode,
                "final_stage": finish_result["stage"],
            }
        )
        _write(stage / "runtime/process-recovery.json", process_evidence)
        _checkpoint_database(database)
        _checkpoint_database(journal_path)
        state, run_bindings = _retained_records(
            database=database,
            run_id=run_id,
            stage=stage,
        )
        summary = _record(
            {
                "schema_version": "orgrebase.semifinal-governed-summary.v1",
                "status": "PASS",
                "evidence_class": (
                    "CONTROLLED_LOCAL_GOVERNED_CANONICAL_WRITE_WITH_SIGKILL_RECOVERY"
                ),
                "run_id": run_id,
                "parent_candidate_summary_digest": parent_summary["digest"],
                "parent_terminal_state": parent_summary["terminal_state"],
                "promotion_digest": prepare_result["promotion_digest"],
                "terminal_state": "GOVERNED_APPLIED",
                "final_quote_ref": state["quote"]["id"]
                + "@"
                + state["quote"]["version"],
                "final_quote_digest": state["quote"]["digest"],
                "proposal_plane_target_writes": 0,
                "canonical_target_writes": 6,
                "canonical_write_accounting": {
                    "initial_quote_and_graph": 2,
                    "launch_rebase_quote_and_graph": 2,
                    "currency_rebase_quote_and_graph": 2,
                },
                "approval_mode": "TWO_EXACT_DOMAIN_OWNER_COMMANDS",
                "approval_input_mode": "CONTROLLED_LOCAL_SCRIPTED_COMMAND",
                "external_human_validation": "NOT_RUN",
                "same_run_binding_digest": run_bindings["digest"],
                "killed_process_resume": "VALIDATED_SQLITE_WAL_SIGKILL",
                "runtime_recovery_dispositions": [
                    "RETRY_INTENT_RECONCILED",
                    "ADOPT_COMMITTED",
                ],
                "agentteams_authority": "CANDIDATE_ONLY_ZERO_CANONICAL_WRITES",
                "canonical_authority": "STATESTORE_REBASE_WORKFLOW_ONLY",
                "live_distributed_agentteams": "NOT_RUN",
                "real_enterprise_connector": "NOT_RUN",
                "real_enterprise_value": "NOT_RUN",
                "production_ha_dr_sla_multitenancy": "NOT_RUN",
                "production_readiness": False,
                "claim_boundary": (
                    "One controlled-local same-run candidate promotion, two scripted owner "
                    "approvals, deterministic canonical Quote v3 writes, two real SIGKILL "
                    "recoveries, and independently retained SQLite state. It is not external "
                    "human, live distributed, real enterprise, or production evidence."
                ),
            }
        )
        _write(stage / "summary.json", summary)
        index = _write_index(stage)

        if output.exists():
            archive_root = output_root / "archive"
            archive_root.mkdir(exist_ok=True)
            timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            archived = archive_root / f"{output.name}-{timestamp}-{summary['digest'][7:19]}"
            shutil.move(str(output), str(archived))
        os.replace(stage, output)
        return {
            "status": "PASS",
            "output": str(output),
            "run_id": run_id,
            "terminal_state": summary["terminal_state"],
            "final_quote_ref": summary["final_quote_ref"],
            "summary_digest": summary["digest"],
            "pack_digest": index["pack_digest"],
            "sigkill_count": process_evidence["sigkill_count"],
            "canonical_target_writes": summary["canonical_target_writes"],
        }
    except Exception:
        failed = output_root / "failed"
        failed.mkdir(exist_ok=True)
        if stage.exists():
            destination = failed / (
                "stage-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            )
            shutil.move(str(stage), str(destination))
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT / "latest",
    )
    parser.add_argument("--parent-pack", type=Path, default=DEFAULT_PARENT_PACK)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument(
        "--worker-phase",
        choices=("prepare", "launch", "finish"),
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--database", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--marker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--wait-for-kill", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--journal", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--action-key", help=argparse.SUPPRESS)
    parser.add_argument("--attempt", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--fencing-token", help=argparse.SUPPRESS)
    parser.add_argument("--commit-journal", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker_phase is not None:
        if args.database is None:
            parser.error("worker phase requires --database")
        return _worker_main(args)
    result = run_governed_semifinal(
        output_dir=args.output_dir,
        parent_pack=args.parent_pack,
        run_id=args.run_id,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
