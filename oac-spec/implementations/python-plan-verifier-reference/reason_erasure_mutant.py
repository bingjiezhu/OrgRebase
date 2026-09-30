#!/usr/bin/env python3
"""Reference-verdict mutant that erases every normative reason code."""

from __future__ import annotations

import sys

import adapter
import rfc8785


def main() -> int:
    response = adapter.handle(adapter.read_request())
    result = response.get("result")
    if (
        response.get("sutStatus") == "COMPLETED"
        and isinstance(result, dict)
        and "reasonCodes" in result
    ):
        result["reasonCodes"] = []
    sys.stdout.buffer.write(rfc8785.dumps(response) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
