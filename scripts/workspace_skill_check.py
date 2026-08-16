#!/usr/bin/env python3
"""Build and evaluate the exact immutable Workspace Skill candidate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from orgrebase.digest import canonical_json
from orgrebase.store import StateStore
from orgrebase.workspace.formation import seed_workspace_store
from orgrebase.workspace.skill_foundry import SkillFoundryService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("evidence/workspace/latest/skill-foundry.json"))
    args = parser.parse_args()
    store = StateStore(":memory:")
    try:
        seed_workspace_store(store)
        result = SkillFoundryService(store).run()
        chain = store.verify_event_chain()
    finally:
        store.close()
    payload = json.loads(canonical_json({**result, "event_chain": chain}))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(args.output)}))


if __name__ == "__main__":
    main()
