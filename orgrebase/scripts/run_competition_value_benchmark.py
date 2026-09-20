"""Measure the executable selective-Rebase path without inventing enterprise ROI.

This benchmark deliberately refuses to turn the repository's modelled
``broadcast-invalidate-all`` baseline into an executed full rebuild.  The
current Workspace product has a governed selective-Rebase executor, but no
semantically equivalent executor that rebuilds every target in the Pack's
impact universe.  Consequently, the selective strategy is run repeatedly and
the full-rebuild comparison remains explicit ``NOT_RUN``.

The receipt is a controlled synthetic engineering measurement.  Wall-clock
results are environment-sensitive, the approval command is driven by this
automation harness (not a verified human), and no value is reported as money,
realized savings, or enterprise ROI.
"""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.domain import Approval, ToolInvocationReceipt
from orgrebase.workspace.formation import MEDIA
from orgrebase.workspace.models import WorkspacePreviewBundle
from orgrebase.workspace.pilot import (
    enterprise_quote_pilot_run_id,
    load_enterprise_quote_pilot_pack,
)
from orgrebase.workspace.service import WorkspaceService

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACK_ROOT = ROOT / "examples" / "enterprise-quote-pilot" / "evergreen"
RECEIPT_SCHEMA_VERSION = "orgrebase.competition-value-benchmark-receipt.v1"
BENCHMARK_STATUS = "CONTROLLED_SYNTHETIC_MEASURED_BENCHMARK"
MINIMUM_TRIAL_COUNT = 7
ZERO_DIGEST = "sha256:" + ("0" * 64)


class CompetitionValueBenchmarkError(RuntimeError):
    """Stable failure raised when a measured run violates its evidence contract."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise CompetitionValueBenchmarkError(code)


def _metric_summary(values: Sequence[int | float], *, unit: str) -> dict[str, Any]:
    _require(bool(values), "METRIC_VALUES_REQUIRED")
    normalized = [float(value) for value in values]
    return {
        "status": "MEASURED",
        "sample_count": len(normalized),
        "unit": unit,
        "min": round(min(normalized), 6),
        "median": round(float(statistics.median(normalized)), 6),
        "max": round(max(normalized), 6),
    }


def _not_run_metric(*, unit: str, reason: str) -> dict[str, str]:
    return {"status": "NOT_RUN", "unit": unit, "reason": reason}


def _domain_worker_bundles(service: WorkspaceService) -> tuple[dict[str, Any], ...]:
    rows = service.store.connection.execute(
        "SELECT payload_json FROM artifacts WHERE media_type=? ORDER BY artifact_id",
        (MEDIA["bundle"],),
    ).fetchall()
    bundles = tuple(json.loads(str(row["payload_json"])) for row in rows)
    _require(all(isinstance(item, dict) for item in bundles), "DOMAIN_BUNDLE_INVALID")
    domains = [item.get("domain_id") for item in bundles]
    _require(
        len(domains) == len(set(domains)) and all(isinstance(item, str) for item in domains),
        "DOMAIN_WORKER_INVOCATION_BIJECTION_INVALID",
    )
    return bundles


def _run_selective_trial(
    *,
    pack_root: Path,
    database: Path,
    trial_index: int,
) -> dict[str, Any]:
    _require(not database.exists(), "TRIAL_DATABASE_ALREADY_EXISTS")
    runtime = load_enterprise_quote_pilot_pack(pack_root)
    base_run_id = enterprise_quote_pilot_run_id(runtime)
    run_id = f"{base_run_id}:competition-value-trial-{trial_index:02d}"
    started_ns = time.perf_counter_ns()
    service = WorkspaceService(
        store_path=database,
        workflow_run_id=run_id,
        runtime_configuration=runtime,
    )
    try:
        formation = service.form_quote_with_dependency_evidence()
        bundles = _domain_worker_bundles(service)
        tool_receipt = ToolInvocationReceipt.model_validate(
            formation["tool_invocation"]["receipt"]
        )
        _require(tool_receipt.status == "SUCCEEDED", "DEPENDENCY_TOOL_NOT_SUCCEEDED")
        _require(tool_receipt.workflow_run_id == run_id, "DEPENDENCY_TOOL_RUN_ID_MISMATCH")

        selective_started_ns = time.perf_counter_ns()
        preview_record = service.preview_command("launch_date")
        preview = WorkspacePreviewBundle.model_validate(preview_record["bundle"])
        target_ids = tuple(item.object_id for item in preview.preview.results)
        before = service.store.state_snapshot(list(target_ids))

        # This calls the real persisted approval gate, but the caller is this
        # automation harness.  It is therefore not evidence of a verified human.
        approval_record = service.approve_change(
            "launch_date",
            actor_id=runtime.change_owners["launch_date"],
            preview_digest=preview.preview.digest,
        )
        approval = Approval.model_validate(approval_record["approval"])
        outcome_record = service.apply_approved_change(
            "launch_date",
            approval_digest=approval.digest,
        )
        selective_completed_ns = time.perf_counter_ns()
        completed_ns = time.perf_counter_ns()

        rebase_receipt = outcome_record["outcome"]["rebase_receipt"]
        rebuilt_count = int(rebase_receipt["metrics"]["work_items_rebased"])
        preserved = tuple(rebase_receipt["bounded_unaffected"])
        preserved_count = len(preserved)
        after = service.store.state_snapshot(list(target_ids))
        rebuilt_ids = tuple(
            item["object_id"]
            for item in rebase_receipt["transitions"]
            if item.get("to_state") == "CURRENT" and item.get("object_id") in target_ids
        )
        preserved_ids = tuple(item["object_id"] for item in preserved)

        _require(rebuilt_count == len(rebuilt_ids), "REBUILT_TARGET_COUNT_MISMATCH")
        _require(
            preserved_count == int(rebase_receipt["metrics"]["bounded_unaffected"]),
            "PRESERVED_PROOF_COUNT_MISMATCH",
        )
        _require(
            all(before[target_id] == after[target_id] for target_id in preserved_ids),
            "PRESERVED_TARGET_MUTATED",
        )
        _require(
            all(
                isinstance(item.get("proof_digest"), str)
                and item["proof_digest"].startswith("sha256:")
                and item["proof_digest"] != ZERO_DIGEST
                for item in preserved
            ),
            "PRESERVED_PROOF_DIGEST_INVALID",
        )
        _require(
            rebase_receipt["workflow_run_id"] == run_id,
            "REBASE_RECEIPT_RUN_ID_MISMATCH",
        )

        advisory_runs = tuple(rebase_receipt["agent_runs"])
        worker_invocation_count = len(bundles)
        result = {
            "trial_index": trial_index,
            "run_id": run_id,
            "pack_digest": runtime.pack_digest,
            "profile_digest": runtime.profile.digest,
            "change_kind": "launch_date",
            "target_universe_count": len(target_ids),
            "target_ids": list(target_ids),
            "rebuilt_target_count": rebuilt_count,
            "rebuilt_target_ids": list(rebuilt_ids),
            "preserved_proof_count": preserved_count,
            "preserved_target_ids": list(preserved_ids),
            "preserved_proof_digests": [item["proof_digest"] for item in preserved],
            "worker_invocation_count": worker_invocation_count,
            "worker_invocation_basis": "PERSISTED_DOMAIN_CANDIDATE_BUNDLE_COUNT",
            "worker_domains": sorted(str(item["domain_id"]) for item in bundles),
            "advisory_agent_run_count": len(advisory_runs),
            "advisory_agent_names": sorted(str(item["agent_name"]) for item in advisory_runs),
            "tool_invocation_count": 1,
            "tool_invocation_receipt_digest": tool_receipt.digest,
            "skill_package_invocation_count": 0,
            "skill_package_invocation_boundary": (
                "NO_PACKAGE_LEVEL_SKILL_INVOKED_IN_THIS_MEASURED_WORKSPACE_PATH;"
                "DO_NOT_JOIN_WITH_GOLDEN_RUNTIME_SKILL_EVIDENCE"
            ),
            "human_touchpoint_count": 1,
            "human_touchpoint_mode": "AUTOMATION_HARNESS_DRIVEN_OWNER_COMMAND",
            "verified_external_human_status": "NOT_RUN",
            "approval_digest": approval.digest,
            "rebase_receipt_digest": rebase_receipt["digest"],
            "selective_operation_wall_time_ms": round(
                (selective_completed_ns - selective_started_ns) / 1_000_000,
                6,
            ),
            "wall_time_ms": round((completed_ns - started_ns) / 1_000_000, 6),
        }
        result["digest"] = sha256_digest(result)
        return result
    finally:
        service.close()


def _full_rebuild_not_run(*, target_ids: Sequence[str]) -> dict[str, Any]:
    reason = (
        "NO_SEMANTICALLY_EQUIVALENT_FULL_REBUILD_EXECUTOR:the current Pack path "
        "executes governed selective Rebase for QUOTE; broadcast-invalidate-all "
        "only rewrites benchmark predictions and is not a rebuild executor"
    )
    return {
        "execution_status": "NOT_RUN",
        "reason": reason,
        "required_target_ids": list(target_ids),
        "required_target_count": len(target_ids),
        "metrics": {
            "rebuilt_target_count": _not_run_metric(unit="TARGET", reason=reason),
            "preserved_proof_count": _not_run_metric(unit="PROOF", reason=reason),
            "worker_invocation_count": _not_run_metric(unit="INVOCATION", reason=reason),
            "tool_invocation_count": _not_run_metric(unit="INVOCATION", reason=reason),
            "skill_package_invocation_count": _not_run_metric(
                unit="INVOCATION", reason=reason
            ),
            "human_touchpoint_count": _not_run_metric(unit="TOUCHPOINT", reason=reason),
            "wall_time_ms": _not_run_metric(unit="MILLISECOND", reason=reason),
        },
        "forbidden_substitutes": [
            "MODELLED_NORMALIZED_ACTION_COST",
            "BROADCAST_INVALIDATE_ALL_PREDICTION",
            "CONSTANT_OR_ASSUMED_REBUILD_COUNT",
        ],
    }


def run_benchmark(
    *,
    pack_root: Path = DEFAULT_PACK_ROOT,
    trial_count: int = MINIMUM_TRIAL_COUNT,
    working_root: Path | None = None,
) -> dict[str, Any]:
    """Run the controlled benchmark and return a content-addressed receipt."""

    _require(trial_count >= MINIMUM_TRIAL_COUNT, "AT_LEAST_SEVEN_TRIALS_REQUIRED")
    selected_pack = pack_root.resolve()
    runtime = load_enterprise_quote_pilot_pack(selected_pack)

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if working_root is None:
        temporary = tempfile.TemporaryDirectory(prefix="orgrebase-competition-value-")
        selected_working_root = Path(temporary.name)
    else:
        selected_working_root = working_root.resolve()
        selected_working_root.mkdir(parents=True, exist_ok=True)

    try:
        trials = tuple(
            _run_selective_trial(
                pack_root=selected_pack,
                database=selected_working_root / f"selective-trial-{index:02d}.sqlite3",
                trial_index=index,
            )
            for index in range(1, trial_count + 1)
        )
    finally:
        if temporary is not None:
            temporary.cleanup()

    pack_digests = {item["pack_digest"] for item in trials}
    profile_digests = {item["profile_digest"] for item in trials}
    target_universes = {tuple(item["target_ids"]) for item in trials}
    _require(pack_digests == {runtime.pack_digest}, "TRIAL_PACK_DIGEST_DRIFT")
    _require(profile_digests == {runtime.profile.digest}, "TRIAL_PROFILE_DIGEST_DRIFT")
    _require(len(target_universes) == 1, "TRIAL_TARGET_UNIVERSE_DRIFT")
    target_ids = next(iter(target_universes))

    metric_fields = {
        "rebuilt_target_count": "TARGET",
        "preserved_proof_count": "PROOF",
        "worker_invocation_count": "INVOCATION",
        "advisory_agent_run_count": "INVOCATION",
        "tool_invocation_count": "INVOCATION",
        "skill_package_invocation_count": "INVOCATION",
        "human_touchpoint_count": "TOUCHPOINT",
        "selective_operation_wall_time_ms": "MILLISECOND",
        "wall_time_ms": "MILLISECOND",
    }
    selective_summary = {
        field: _metric_summary([item[field] for item in trials], unit=unit)
        for field, unit in metric_fields.items()
    }
    full_rebuild = _full_rebuild_not_run(target_ids=target_ids)
    comparison_reason = (
        "FULL_REBUILD_NOT_RUN; measured strategy deltas, saving rates, and ROI are not computable"
    )
    receipt: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "benchmark_id": "competition-value:evergreen-launch-date@v1",
        "status": BENCHMARK_STATUS,
        "verification_status": "PASS",
        "evidence_class": "CONTROLLED_SYNTHETIC_LOCAL_MEASUREMENT",
        "input_binding": {
            "pack_id": runtime.pack_id,
            "pack_revision": runtime.pack_revision,
            "pack_digest": runtime.pack_digest,
            "profile_digest": runtime.profile.digest,
            "scenario_id": runtime.scenario["id"],
            "data_class": "SYNTHETIC_ENTERPRISE_PACK",
            "change_kind": "launch_date",
            "trial_count": trial_count,
            "same_pack_and_seed_across_trials": True,
        },
        "strategies": {
            "selective_rebase": {
                "execution_status": "EXECUTED",
                "executor": (
                    "WorkspaceService.form_quote_with_dependency_evidence -> preview_command -> "
                    "approve_change -> apply_approved_change"
                ),
                "trials": list(trials),
                "summary": selective_summary,
            },
            "full_rebuild": full_rebuild,
        },
        "comparison": {
            "status": "NOT_RUN",
            "reason": comparison_reason,
            "rebuilt_target_delta": _not_run_metric(unit="TARGET", reason=comparison_reason),
            "wall_time_delta_ms": _not_run_metric(
                unit="MILLISECOND", reason=comparison_reason
            ),
            "saving_rate": _not_run_metric(unit="RATIO", reason=comparison_reason),
            "enterprise_roi": _not_run_metric(unit="ROI", reason=comparison_reason),
        },
        "measurement_boundaries": {
            "wall_time": (
                "ENVIRONMENT_SENSITIVE_SINGLE_PROCESS_LOCAL_MEASUREMENT; use median/min/max, "
                "not a production latency or SLA claim"
            ),
            "human": (
                "The harness sends one owner-scoped command through the persisted approval gate; "
                "no external human identity or human response time was measured"
            ),
            "skill": (
                "This measured Workspace path produced no package-level Skill invocation; "
                "Golden Runtime evidence, if present elsewhere, is not joined into this receipt"
            ),
            "value": (
                "Not money, realized savings, enterprise ROI, production throughput, or a "
                "full-rebuild comparison"
            ),
        },
        "claim_boundary": (
            "CONTROLLED_SYNTHETIC_MEASURED_BENCHMARK_ONLY; selective execution is measured; "
            "semantic full rebuild, real enterprise data, verified human operation, and "
            "enterprise ROI are NOT_RUN"
        ),
    }
    receipt["digest"] = sha256_digest(receipt)
    return receipt


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, default=DEFAULT_PACK_ROOT)
    parser.add_argument("--trials", type=int, default=MINIMUM_TRIAL_COUNT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--working-root",
        type=Path,
        help="Optional directory retaining per-trial SQLite stores for audit.",
    )
    args = parser.parse_args(argv)
    receipt = run_benchmark(
        pack_root=args.pack,
        trial_count=args.trials,
        working_root=args.working_root,
    )
    _write_json(args.output, receipt)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
