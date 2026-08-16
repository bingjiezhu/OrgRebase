#!/usr/bin/env python3
"""Verify the five submission-facing Workspace review criteria."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from orgrebase.workspace.readiness import audit_repository


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Optional alternate review-readiness manifest (used by negative tests).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evidence/workspace/latest/review-readiness.json"),
    )
    args = parser.parse_args()
    report = audit_repository(args.root, manifest_path=args.manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if report["status"] != "PASS":
        raise SystemExit("REVIEW_READINESS_FAILED")


if __name__ == "__main__":
    main()
