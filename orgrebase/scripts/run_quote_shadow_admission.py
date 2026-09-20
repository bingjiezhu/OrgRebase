"""Run the enterprise quote Shadow Observation admission boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from orgrebase.digest import sha256_digest
from orgrebase.workspace.quote_observations import (
    QuoteProcessObservationRecord,
    QuoteShadowAdmissionError,
    admit_quote_observation,
    stable_observation_record_ref,
    verify_quote_shadow_receipt,
)


def _load_record(path: Path) -> QuoteProcessObservationRecord:
    value = json.loads(path.read_text(encoding="utf-8"))
    return QuoteProcessObservationRecord.model_validate(value)


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _stable_output_ref(path: Path, *, project_root: Path, digest: str) -> str:
    try:
        relative = path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return f"content://{digest}"
    return f"project://{relative}"


def run(args: argparse.Namespace) -> dict[str, Any]:
    record = _load_record(args.record.resolve())
    receipt = admit_quote_observation(record, source_root=args.source_root.resolve())
    failures = verify_quote_shadow_receipt(
        record,
        receipt,
        source_root=args.source_root.resolve(),
    )
    if failures:
        raise QuoteShadowAdmissionError("RECEIPT_REPLAY_FAILED", ";".join(failures))

    output_dir = args.output_dir.resolve()
    receipt_path = output_dir / "quote-shadow-admission-receipt.json"
    _write_json(receipt_path, receipt)
    verification = {
        "schema_version": "orgrebase.workspace-quote-shadow-verification.v1",
        "status": "PASS",
        "verification_mode": "DETERMINISTIC_SOURCE_AND_RECEIPT_REPLAY",
        "record_ref": stable_observation_record_ref(
            args.record,
            project_root=args.project_root.resolve(),
            record_digest=record.digest,
        ),
        "record_digest": record.digest,
        "receipt_ref": receipt_path.name,
        "receipt_digest": receipt.digest,
        "receipt_file_sha256": _file_digest(receipt_path),
        "failures": [],
    }
    verification_path = output_dir / "verification.json"
    _write_json(verification_path, verification)
    index = {
        "schema_version": "orgrebase.workspace-quote-shadow-evidence-index.v1",
        "status": "PASS",
        "entries": [
            {"path": receipt_path.name, "file_sha256": _file_digest(receipt_path)},
            {"path": verification_path.name, "file_sha256": _file_digest(verification_path)},
        ],
    }
    index["digest"] = sha256_digest(index)
    _write_json(output_dir / "evidence-index.json", index)
    return {
        "status": "PASS",
        "evidence_ceiling": receipt.evidence_ceiling,
        "measurement_status": receipt.measurement_status,
        "calculated_metrics": sum(item.status == "CALCULATED" for item in receipt.metrics),
        "not_run_metrics": sum(item.status == "NOT_RUN" for item in receipt.metrics),
        "receipt": _stable_output_ref(
            receipt_path,
            project_root=args.project_root.resolve(),
            digest=receipt.digest,
        ),
        "receipt_digest": receipt.digest,
        "real_enterprise_shadow_pilot": "NOT_RUN"
        if receipt.evidence_ceiling != "OBSERVED_ENTERPRISE_SHADOW"
        else "OBSERVED_INPUT_ADMITTED",
        "enterprise_roi": "NOT_RUN",
    }


def _parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[1]
    benchmark_root = root / "benchmark" / "quote-value-v0.2-shadow"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=root)
    parser.add_argument(
        "--record",
        type=Path,
        default=benchmark_root / "synthetic" / "observation-record.json",
    )
    parser.add_argument("--source-root", type=Path, default=benchmark_root)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=root / "evidence" / "quote-shadow" / "latest",
    )
    return parser


def main() -> int:
    try:
        result = run(_parser().parse_args())
    except (OSError, json.JSONDecodeError, ValidationError, QuoteShadowAdmissionError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
