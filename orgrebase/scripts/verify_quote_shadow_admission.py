"""Replay an Enterprise Quote Shadow Admission receipt from its bound input."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from orgrebase.workspace.quote_observations import (
    QuoteProcessObservationRecord,
    QuoteShadowAdmissionReceipt,
    stable_observation_record_ref,
    verify_quote_shadow_receipt,
)


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


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
        "--receipt",
        type=Path,
        default=root / "evidence" / "quote-shadow" / "latest" / "quote-shadow-admission-receipt.json",
    )
    parser.add_argument("--verification", type=Path)
    return parser


def verify_files(args: argparse.Namespace) -> tuple[str, ...]:
    failures: list[str] = []
    try:
        record = QuoteProcessObservationRecord.model_validate_json(args.record.read_text(encoding="utf-8"))
        receipt = QuoteShadowAdmissionReceipt.model_validate_json(args.receipt.read_text(encoding="utf-8"))
        failures.extend(
            verify_quote_shadow_receipt(
                record,
                receipt,
                source_root=args.source_root.resolve(),
            )
        )
        verification_path = args.verification or args.receipt.parent / "verification.json"
        metadata = json.loads(verification_path.read_text(encoding="utf-8"))
        expected_ref = stable_observation_record_ref(
            args.record,
            project_root=args.project_root.resolve(),
            record_digest=record.digest,
        )
        if metadata.get("schema_version") != "orgrebase.workspace-quote-shadow-verification.v1":
            failures.append("VERIFICATION_SCHEMA_VERSION_INVALID")
        if metadata.get("status") != "PASS" or metadata.get("failures") != []:
            failures.append("VERIFICATION_STATUS_INVALID")
        if metadata.get("record_ref") != expected_ref:
            failures.append("VERIFICATION_RECORD_REF_INVALID")
        if not str(metadata.get("record_ref", "")).startswith(("benchmark://", "project://", "content://")):
            failures.append("VERIFICATION_RECORD_REF_NOT_LOGICAL")
        if metadata.get("record_digest") != record.digest:
            failures.append("VERIFICATION_RECORD_DIGEST_MISMATCH")
        if metadata.get("receipt_digest") != receipt.digest:
            failures.append("VERIFICATION_RECEIPT_DIGEST_MISMATCH")
        if metadata.get("receipt_file_sha256") != _file_digest(args.receipt):
            failures.append("VERIFICATION_RECEIPT_FILE_DIGEST_MISMATCH")
    except (OSError, ValidationError, ValueError) as exc:
        failures.append(f"LOAD_OR_REPLAY_FAILED:{exc}")
    return tuple(failures)


def main() -> int:
    failures = verify_files(_parser().parse_args())
    result = {
        "schema_version": "orgrebase.workspace-quote-shadow-verification.v1",
        "status": "PASS" if not failures else "FAIL",
        "verification_mode": "DETERMINISTIC_SOURCE_AND_RECEIPT_REPLAY",
        "failures": list(failures),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
