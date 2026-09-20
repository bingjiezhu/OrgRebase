#!/usr/bin/env python3
"""Run one exact predecessor rollback against the retained semifinal bindings."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.workspace.skill_rollback import (
    ROLLBACK_AUTHORITY,
    SkillPredecessorRollbackExecutor,
    load_frozen_predecessor,
)

ROOT = Path(__file__).resolve().parents[1]


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _record(value: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(value)
    result["digest"] = sha256_digest(value)
    return result


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _expect_error(action: Callable[[], object], expected: str) -> dict[str, str]:
    try:
        action()
    except IntegrityError as exc:
        observed = str(exc)
        if observed != expected:
            raise
        return {"expected_error": expected, "observed_error": observed, "status": "PASS"}
    raise AssertionError(f"expected IntegrityError: {expected}")


def _build_index(stage: Path) -> dict[str, Any]:
    paths = sorted(
        path.relative_to(stage).as_posix()
        for path in stage.rglob("*.json")
        if path.name != "evidence-index.json"
    )
    entries = [
        {
            "path": relative,
            "bytes": (stage / relative).stat().st_size,
            "sha256": _file_digest(stage / relative),
        }
        for relative in paths
    ]
    return _record(
        {
            "schema_version": "orgrebase.skill-predecessor-rollback-evidence-index.v1",
            "entry_count": len(entries),
            "entries": entries,
            "pack_digest": sha256_digest(entries),
        }
    )


def run_skill_predecessor_rollback(
    *,
    run_id: str,
    semifinal_root: Path,
    current_manifest_path: Path,
    predecessor_root: Path,
    output: Path,
    created_at: str,
    tool_receipt_path: Path | None = None,
    tool_result_path: Path | None = None,
    current_invocation_path: Path | None = None,
    current_input_path: Path | None = None,
    current_result_path: Path | None = None,
) -> dict[str, Any]:
    output = output.resolve()
    if not output.exists() and output.parent.exists() and any(
        path.name.startswith(f".{output.name}.stage-") for path in output.parent.iterdir()
    ):
        raise IntegrityError("SKILL_PREDECESSOR_ROLLBACK_PUBLICATION_RECOVERY_REQUIRED")
    package = load_frozen_predecessor(predecessor_root, checkout_root=ROOT, verify_retained_wheel=True)
    tool_receipt = _load(tool_receipt_path or semifinal_root / "operations" / "tool" / "receipt.json")
    tool_result = _load(tool_result_path or semifinal_root / "operations" / "tool" / "result.json")
    current_invocation = _load(
        current_invocation_path or semifinal_root / "skills" / "quote-compose" / "invocation-receipt.json"
    )
    current_input = _load(current_input_path or semifinal_root / "skills" / "quote-compose" / "input.json")
    current_result = _load(current_result_path or semifinal_root / "skills" / "quote-compose" / "result.json")
    current_manifest = _load(current_manifest_path)
    if any(value.get("run_id") != run_id for value in (tool_receipt, tool_result, current_invocation)):
        raise IntegrityError("SKILL_PREDECESSOR_ROLLBACK_CROSS_RUN_EVIDENCE")

    idempotency_key = f"skill-predecessor-rollback:{run_id}:enterprise-quote-compose"
    request = {
        "run_id": run_id,
        "actor_id": ROLLBACK_AUTHORITY,
        "idempotency_key": idempotency_key,
        "current_manifest": current_manifest,
        "current_invocation_receipt": current_invocation,
        "current_input": current_input,
        "current_result": current_result,
        "tool_receipt": tool_receipt,
        "tool_result": tool_result,
        "reason_codes": ("CANARY_REGRESSION_EXECUTE_EXACT_PREDECESSOR",),
        "created_at": created_at,
    }
    # Publication history is authoritative only inside one exact upstream
    # evidence partition.  A byte-identical retry must rehydrate that ledger so
    # it returns the original receipt across processes.  If the fixed demo
    # run_id is regenerated with new Tool/Skill roots, the prior publication is
    # a different partition: validate it, archive it after the new pack passes,
    # and start a fresh ledger instead of raising a stale-key conflict.
    retained_history: list[dict[str, Any]] = []
    if output.exists():
        prior_receipt = _load(output / "rollback-execution-receipt.json")
        prior_ledger_value = json.loads((output / "ledger.json").read_text(encoding="utf-8"))
        if not isinstance(prior_ledger_value, list) or not all(
            isinstance(item, dict) for item in prior_ledger_value
        ):
            raise IntegrityError("SKILL_PREDECESSOR_ROLLBACK_PUBLICATION_LEDGER_INVALID")
        prior_ledger = [dict(item) for item in prior_ledger_value]
        # Construction performs the full receipt-chain validation before the
        # old publication is trusted or replaced.
        SkillPredecessorRollbackExecutor(package, prior_ledger)
        if not prior_ledger or prior_ledger[-1] != prior_receipt:
            raise IntegrityError("SKILL_PREDECESSOR_ROLLBACK_PUBLICATION_LEDGER_INVALID")
        expected_partition = {
            "run_id": run_id,
            "from_package_digest": current_manifest.get("manifest_digest"),
            "current_invocation_receipt_digest": current_invocation.get("digest"),
            "current_input_digest": sha256_digest(current_input),
            "current_output_digest": sha256_digest(current_result),
            "dependency_tool_receipt_digest": tool_receipt.get("digest"),
            "dependency_result_digest": sha256_digest(tool_result),
            "effective_package_digest": package.package_digest,
        }
        if all(prior_receipt.get(field) == value for field, value in expected_partition.items()):
            retained_history = prior_ledger
    executor = SkillPredecessorRollbackExecutor(package, retained_history)
    execution = executor.execute(**request)
    replay = executor.execute(**{**request, "created_at": "2099-01-01T00:00:00Z"})
    if (
        not replay.idempotent_replay
        or replay.receipt["digest"] != execution.receipt["digest"]
        or len(executor.history) != 1
    ):
        raise IntegrityError("SKILL_PREDECESSOR_ROLLBACK_IDEMPOTENCY_FAILED")
    rehydrated = SkillPredecessorRollbackExecutor(package, json.loads(json.dumps(list(executor.history))))
    resumed_replay = rehydrated.execute(**{**request, "created_at": "2099-01-01T00:00:01Z"})
    if (
        not resumed_replay.idempotent_replay
        or resumed_replay.receipt["digest"] != execution.receipt["digest"]
        or len(rehydrated.history) != 1
    ):
        raise IntegrityError("SKILL_PREDECESSOR_ROLLBACK_REHYDRATION_FAILED")

    unauthorized = {**request, "actor_id": "agent:skill-curator"}
    changed_result = deepcopy(tool_result)
    changed_result["target_id"] = "work:tampered-target"
    tampered_tool = {**request, "tool_result": changed_result}
    wrong_lineage_manifest = deepcopy(current_manifest)
    wrong_lineage_manifest["release_artifact"]["predecessor_package_digest"] = "sha256:" + "0" * 64
    wrong_lineage = {**request, "current_manifest": wrong_lineage_manifest}
    conflicting_replay = {
        **request,
        "reason_codes": ("DIFFERENT_REASON_MUST_NOT_REUSE_IDEMPOTENCY_KEY",),
    }
    probes = _record(
        {
            "schema_version": "orgrebase.skill-predecessor-rollback-negative-probes.v1",
            "run_id": run_id,
            "probes": {
                "authority": _expect_error(
                    lambda: SkillPredecessorRollbackExecutor(package).execute(**unauthorized),
                    "SKILL_PREDECESSOR_ROLLBACK_AUTHORITY_DENIED",
                ),
                "direct_lineage": _expect_error(
                    lambda: SkillPredecessorRollbackExecutor(package).execute(**wrong_lineage),
                    "SKILL_PREDECESSOR_DIRECT_LINEAGE_MISMATCH",
                ),
                "idempotency_conflict": _expect_error(
                    lambda: executor.execute(**conflicting_replay),
                    "SKILL_PREDECESSOR_ROLLBACK_IDEMPOTENCY_CONFLICT",
                ),
                "tool_result_tamper": _expect_error(
                    lambda: SkillPredecessorRollbackExecutor(package).execute(**tampered_tool),
                    "SKILL_ROLLBACK_TOOL_BINDING_INVALID",
                ),
            },
            "status": "PASS",
        }
    )
    idempotency = _record(
        {
            "schema_version": "orgrebase.skill-predecessor-rollback-idempotency-proof.v1",
            "run_id": run_id,
            "idempotency_key": idempotency_key,
            "first_receipt_digest": execution.receipt["digest"],
            "replay_receipt_digest": replay.receipt["digest"],
            "rehydrated_replay_receipt_digest": resumed_replay.receipt["digest"],
            "ledger_event_count_after_replay": len(executor.history),
            "ledger_event_count_after_rehydration": len(rehydrated.history),
            "preexisting_ledger_event_count": len(retained_history),
            "process_state_rehydration": "PASS",
            "status": "PASS",
        }
    )
    summary = _record(
        {
            "schema_version": "orgrebase.skill-predecessor-rollback-summary.v1",
            "status": "PASS",
            "run_id": run_id,
            "evidence_class": "CONTROLLED_LOCAL_EXECUTABLE_DIRECT_PREDECESSOR_ROLLBACK",
            "from_package_digest": execution.receipt["from_package_digest"],
            "effective_package_digest": execution.receipt["effective_package_digest"],
            "execution_receipt_digest": execution.receipt["digest"],
            "predecessor_action": execution.result["action"],
            "restoration_status": "EXECUTED_AND_INVOKED",
            "lineage_status": "DIRECT_DECLARED_PREDECESSOR",
            "append_only_events": len(executor.history),
            "idempotent_replay": True,
            "process_state_rehydration": "PASS",
            "negative_probe_count": len(probes["probes"]),
            "candidate_only": True,
            "target_writes": 0,
            "source_wheel_digest": package.provenance["source_wheel"]["sha256"],
            "claim_boundary": (
                "Exact retained 1.3.0 Skill bytes were restored as the direct release head "
                "and its restricted program was invoked against the same-run Tool bindings. "
                "This is controlled-local release rollback evidence, not enterprise production rollout."
            ),
        }
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.stage-", dir=output.parent))
    try:
        _write(stage / "predecessor-input.json", execution.predecessor_input)
        _write(stage / "predecessor-result.json", execution.result)
        _write(stage / "rollback-execution-receipt.json", execution.receipt)
        _write(stage / "ledger.json", list(executor.history))
        _write(stage / "idempotency-proof.json", idempotency)
        _write(stage / "negative-probes.json", probes)
        _write(stage / "summary.json", summary)
        _write(stage / "evidence-index.json", _build_index(stage))
        if output.exists():
            current_index = _load(output / "evidence-index.json")
            staged_index = _load(stage / "evidence-index.json")
            if current_index.get("pack_digest") == staged_index.get("pack_digest"):
                shutil.rmtree(stage)
                return summary
            previous_pack = str(current_index.get("pack_digest", "")).removeprefix("sha256:")
            if len(previous_pack) != 64:
                raise IntegrityError("SKILL_PREDECESSOR_ROLLBACK_ARCHIVE_BINDING_INVALID")
            archive = output.parent / "archive" / f"pack-{previous_pack[:16]}"
            archive.parent.mkdir(parents=True, exist_ok=True)
            if archive.exists():
                raise IntegrityError("SKILL_PREDECESSOR_ROLLBACK_ARCHIVE_CONFLICT")
            os.replace(output, archive)
        os.replace(stage, output)
    finally:
        # If publication lost its current name, retain the fully staged pack.
        # A later invocation must require recovery, not silently start a new ledger.
        if stage.exists() and output.exists():
            shutil.rmtree(stage)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--semifinal-root", type=Path, default=ROOT / "evidence" / "semifinal-closure" / "latest"
    )
    parser.add_argument(
        "--current-manifest",
        type=Path,
        default=ROOT / "skills" / "enterprise-quote-compose" / "package.json",
    )
    parser.add_argument(
        "--predecessor-root",
        type=Path,
        default=ROOT / "skills" / "predecessors" / "enterprise-quote-compose" / "1.3.0",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evidence" / "skill-predecessor-rollback" / "latest",
    )
    parser.add_argument(
        "--created-at",
        default=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )
    parser.add_argument("--tool-receipt", type=Path)
    parser.add_argument("--tool-result", type=Path)
    parser.add_argument("--current-invocation", type=Path)
    parser.add_argument("--current-input", type=Path)
    parser.add_argument("--current-result", type=Path)
    args = parser.parse_args()
    summary = run_skill_predecessor_rollback(
        run_id=args.run_id,
        semifinal_root=args.semifinal_root,
        current_manifest_path=args.current_manifest,
        predecessor_root=args.predecessor_root,
        output=args.output,
        created_at=args.created_at,
        tool_receipt_path=args.tool_receipt,
        tool_result_path=args.tool_result,
        current_invocation_path=args.current_invocation,
        current_input_path=args.current_input,
        current_result_path=args.current_result,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
