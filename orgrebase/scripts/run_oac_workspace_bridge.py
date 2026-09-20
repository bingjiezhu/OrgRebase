"""Build or independently verify the local OAC Runtime Admission evidence pack."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from orgrebase.workspace.oac_bridge import (
    DEFAULT_POLICY_PATH,
    run_oac_admission_demo,
    verify_oac_bridge_evidence,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("evidence/oac-bridge/latest"),
    )
    parser.add_argument(
        "--oac-root",
        type=Path,
        help=(
            "separate oac-spec checkout; overrides ORGREBASE_OAC_ROOT and sibling "
            "../oac-spec discovery (generation only)"
        ),
    )
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY_PATH)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="verify an exported pack standalone; no OAC checkout is required",
    )
    args = parser.parse_args()
    result = (
        verify_oac_bridge_evidence(args.output_dir, policy_path=args.policy)
        if args.verify
        else run_oac_admission_demo(
            args.output_dir,
            oac_root=args.oac_root,
            policy_path=args.policy,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
