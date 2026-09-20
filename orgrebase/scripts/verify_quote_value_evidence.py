"""Product-independent deterministic replay verifier for a QuoteValue receipt.

The CLI deliberately reuses the benchmark's canonical ``verify_receipt``
implementation.  It is independent from OrgRebase product modules, not an
independent second implementation of the QuoteValue evaluator.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from scripts.run_quote_value_benchmark import verify_receipt
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from run_quote_value_benchmark import verify_receipt


def _load_receipt(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("RECEIPT_OBJECT_REQUIRED")
    return value


def _parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--receipt",
        type=Path,
        default=root / "evidence" / "quote-value" / "latest" / "quote-value-receipt.json",
    )
    parser.add_argument("--project-root", type=Path, default=root)
    parser.add_argument("--verification-output", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        receipt = _load_receipt(args.receipt)
        failures = verify_receipt(receipt, project_root=args.project_root.resolve())
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        failures = [f"RECEIPT_LOAD_FAILED:{exc}"]
        receipt = {}
    result = {
        "schema_version": "orgrebase.quote-value-verification.v1",
        "status": "PASS" if not failures else "FAIL",
        "verification_mode": "PRODUCT_INDEPENDENT_DETERMINISTIC_REPLAY",
        "implementation_independence": "SHARED_VERIFY_RECEIPT_IMPLEMENTATION",
        "receipt_ref": str(args.receipt),
        "receipt_digest": receipt.get("digest"),
        "verifier_product_imports": 0,
        "failures": failures,
    }
    if args.verification_output:
        args.verification_output.parent.mkdir(parents=True, exist_ok=True)
        args.verification_output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
