#!/usr/bin/env python3
"""Build or independently verify the Workspace release evidence directory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from orgrebase.workspace.evidence import WorkspaceEvidenceBuilder, verify_evidence_directory


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", nargs="?", type=Path, default=Path("evidence/workspace/latest"))
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    result = verify_evidence_directory(args.directory) if args.verify else WorkspaceEvidenceBuilder(args.directory).build().model_dump(mode="json")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
