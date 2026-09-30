#!/usr/bin/env python3
"""Mutant that erases selected relation rules while preserving the wire contract.

This is intentionally wrong. It turns semantic rejections associated with a
bounded reason family into acceptance. Killing this aggregate mutant proves at
least one selected relation branch is active; it is not a per-rule kill vector.
"""

from __future__ import annotations

import sys

import adapter
import rfc8785

ERASED_RULE_REASONS = {
    "INPUT_ROOT_MISMATCH",
    "OBLIGATION_SET_MISMATCH",
    "ORDER_CYCLE",
    "PLAN_NOT_MINIMAL",
    "PLAN_STATUS_MISMATCH",
    "QUALIFICATION_INVALID",
    "RESOURCE_COHERENCE_VIOLATION",
}


def main() -> int:
    response = adapter.handle(adapter.read_request())
    result = response.get("result")
    if (
        response.get("sutStatus") == "COMPLETED"
        and isinstance(result, dict)
        and result.get("verdict") == "REJECT"
        and ERASED_RULE_REASONS.intersection(result.get("reasonCodes", []))
    ):
        result["verdict"] = "ACCEPT"
        result["reasonCodes"] = []
    sys.stdout.buffer.write(rfc8785.dumps(response) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
