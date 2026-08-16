#!/usr/bin/env python3
"""Smoke-test the installed-wheel asset layout without building a wheel.

The execution environment used for this proof may not have Hatch cached and may
have no network access. This script stages the exact force-include layout from
``pyproject.toml`` and imports OrgRebase from that isolated package directory,
proving checkout-only paths are not required by the deterministic runtime.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ASSETS = {
    ROOT / "demo/console": Path("orgrebase/static"),
    ROOT / "fixtures/canonical-enterprise.json": Path(
        "orgrebase/_assets/fixtures/canonical-enterprise.json"
    ),
    ROOT / "orchestration/task-intents.json": Path(
        "orgrebase/_assets/orchestration/task-intents.json"
    ),
    ROOT / "agentteams/identities": Path("orgrebase/_assets/agentteams/identities"),
    ROOT / "benchmark/orgworkbench": Path("orgrebase/_assets/benchmark/orgworkbench"),
    ROOT / "configs/workspace/metric-registry.json": Path(
        "orgrebase/_assets/configs/workspace/metric-registry.json"
    ),
    ROOT / "evidence/release-facts.json": Path(
        "orgrebase/_assets/evidence/release-facts.json"
    ),
    ROOT / "skills/enterprise-launch-readiness/contract.json": Path(
        "orgrebase/skill_contracts/enterprise-launch-readiness.json"
    ),
}


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target, dirs_exist_ok=True)
    else:
        shutil.copy2(source, target)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="orgrebase-packaged-assets-") as raw:
        base = Path(raw)
        site = base / "site"
        package = site / "orgrebase"
        shutil.copytree(ROOT / "src/orgrebase", package)
        for source, relative_target in ASSETS.items():
            _copy(source, site / relative_target)

        run_dir = base / "run"
        run_dir.mkdir()
        code = r'''
import json
from pathlib import Path
from orgrebase.fixture import load_fixture
from orgrebase.api import RELEASE_FACTS_PATH
from orgrebase.service import OrgRebaseService
from orgrebase.workspace.benchmark import OWBBenchmarkRepository
from orgrebase.workspace.service import WorkspaceService

fixture = load_fixture()
assert fixture.organization_id == "org:northstar"
release_facts = json.loads(RELEASE_FACTS_PATH.read_text(encoding="utf-8"))
assert release_facts["workspace"]["agentteams_live"] == "NOT_RUN"
repo = OWBBenchmarkRepository()
assert repo.verify()["case_count"] == 192
legacy = OrgRebaseService()
preview = legacy.preview()
assert preview["preview"].state == "READY"
workspace = WorkspaceService(store_path=Path("workspace.db"))
try:
    result = workspace.run_local_loop()
finally:
    try:
        workspace.close()
    except Exception:
        pass
assert result["final_quote"].version == "v3"
assert result["final_quote"].payload["launch_date"] == "2026-09-15"
assert result["final_quote"].payload["currency"] == "EUR"
assert result["final_graph_pointer"].version == "v3"
assert result["event_chain"]["status"] == "PASS"
print(json.dumps({
    "status": "PASS",
    "fixture_digest": fixture.digest,
    "benchmark_cases": 192,
    "legacy_preview_digest": preview["preview"].digest,
    "workspace_quote_ref": result["final_quote"].ref,
    "workspace_graph_ref": result["final_graph_pointer"].ref,
}, sort_keys=True))
'''
        env = dict(os.environ)
        env["PYTHONPATH"] = str(site)
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=run_dir,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            sys.stderr.write(completed.stdout)
            sys.stderr.write(completed.stderr)
            raise SystemExit(completed.returncode)
        value = json.loads(completed.stdout.strip())
        value["staged_runtime_assets"] = len(ASSETS)
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
