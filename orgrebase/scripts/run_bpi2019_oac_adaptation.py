#!/usr/bin/env python3
"""Run BPI 2019 -> AgentTeams candidate -> OAC binding -> typed-scope replay."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from orgrebase.agentteams_source import default_agentteams_checkout
from orgrebase.digest import sha256_digest
from orgrebase.workspace.bpi_oac_adaptation import (
    finalize_bpi_oac_execution,
    prepare_bpi_oac_adaptation,
)

ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _manifest(root: Path) -> dict[str, object]:
    entries = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": "sha256:" + __import__("hashlib").sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
        }
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().encode())
        if path.is_file() and path.name != "manifest.json"
    ]
    payload: dict[str, object] = {
        "schema_version": "orgrebase.bpi-oac-adaptation-evidence-manifest.v1",
        "status": "CLOSED_WORLD",
        "entries": entries,
        "entry_count": len(entries),
        "claim_ceiling": "PUBLIC_REAL_DATA_OAC_MAPPING_AND_TYPED_SCOPE_EXECUTION",
    }
    return {**payload, "digest": sha256_digest(payload)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--benchmark-root",
        type=Path,
        default=ROOT / "benchmark/quote-value-v0.4-bpi-real-process",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs/oac/bpi2019-p2p-adaptation-v1.json",
    )
    parser.add_argument(
        "--checkout",
        type=Path,
        default=Path(os.getenv("AGENTTEAMS_CHECKOUT", str(default_agentteams_checkout(ROOT)))),
    )
    parser.add_argument("--lock", type=Path, default=ROOT / "agentteams/teamharness-lock.json")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evidence/oac-public-real-process/latest",
    )
    parser.add_argument("--vertex-project")
    args = parser.parse_args()
    output = args.output.resolve()
    initial = prepare_bpi_oac_adaptation(
        benchmark_root=args.benchmark_root,
        config_path=args.config,
        checkout=args.checkout,
        lock_path=args.lock,
        output_dir=output,
        vertex_project=args.vertex_project,
    )
    execution_dir = output / "typed-scope-execution"
    receipt_path = execution_dir / "bpi2019-real-process-benchmark-receipt.json"
    summary_path = execution_dir / "summary.json"
    verification_path = execution_dir / "bpi2019-real-process-verification.json"
    subprocess.run(
        [
            "uv",
            "run",
            "python",
            str(ROOT / "scripts/run_bpi2019_real_process_benchmark.py"),
            "--benchmark-root",
            str(args.benchmark_root.resolve()),
            "--output",
            str(receipt_path),
            "--summary-output",
            str(summary_path),
        ],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    subprocess.run(
        [
            "uv",
            "run",
            "python",
            str(ROOT / "scripts/verify_bpi2019_real_process_benchmark.py"),
            "--project-root",
            str(ROOT),
            "--receipt",
            str(receipt_path),
            "--output",
            str(verification_path),
        ],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    final = finalize_bpi_oac_execution(
        adaptation_receipt=initial,
        execution_receipt_path=receipt_path,
        independent_verification_path=verification_path,
        output_path=output / "adaptation-receipt.json",
    )
    manifest = _manifest(output)
    _write(output / "manifest.json", manifest)
    print(
        json.dumps(
            {
                "status": final.status,
                "claim_ceiling": final.claim_ceiling,
                "adaptation_run_id": final.adaptation_run_id,
                "execution_run_id": final.execution_run_id,
                "mapping_lane": final.selected_mapping_lane,
                "vertex_status": final.model_attempt["status"],
                "human_review_elapsed_ms": final.human_admission["admission"]["elapsed_ms"],
                "queries_independently_replayed": final.typed_scope_execution["query_count"],
                "canonical_target_writes": final.canonical_target_writes,
                "manifest_digest": manifest["digest"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
