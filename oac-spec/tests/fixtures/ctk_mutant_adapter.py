"""Deliberately wrong black-box SUT used to prove the CTK catches a mutant."""

from __future__ import annotations

import json
import sys

import rfc8785

from oac.ctk_adapter import handle


def main() -> int:
    request = json.loads(sys.stdin.buffer.read())
    response = handle(request)
    if (
        request.get("operation") == "strongKleene"
        and request.get("payload", {}).get("operator") == "all"
        and "UNKNOWN" in request.get("payload", {}).get("values", [])
        and response.get("sutStatus") == "COMPLETED"
    ):
        response["result"]["result"] = "TRUE"
    sys.stdout.buffer.write(rfc8785.dumps(response) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
