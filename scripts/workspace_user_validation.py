#!/usr/bin/env python3
"""Summarize optional consented Workspace walkthrough records."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from orgrebase.workspace.models import UserWalkthroughRecord
from orgrebase.workspace.user_validation import UserValidationService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path, default=Path("evidence/workspace/latest/user-validation.json"))
    args = parser.parse_args()
    records: tuple[UserWalkthroughRecord, ...] = ()
    if args.input and args.input.is_file():
        raw = json.loads(args.input.read_text(encoding="utf-8"))
        records = tuple(UserWalkthroughRecord.model_validate(item) for item in raw)
    summary = UserValidationService.summarize(records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
