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

if __package__:
    from scripts import build_offline_release as release
else:
    import build_offline_release as release

ROOT = Path(__file__).resolve().parents[1]


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
        assets = release.wheel_asset_mappings()
        for source, relative_target in assets.items():
            _copy(source, site / relative_target)

        run_dir = base / "run"
        run_dir.mkdir()
        code = r'''
import json
from pathlib import Path
from orgrebase.agentteams_source import load_agentteams_source, load_teamharness_lock
from orgrebase.fixture import load_fixture
from orgrebase.resource_paths import runtime_asset_path
from orgrebase.api import RELEASE_FACTS_PATH
from orgrebase.cli import _enterprise_pilot_check
from orgrebase.service import OrgRebaseService
from orgrebase.workspace.benchmark import OWBBenchmarkRepository
from orgrebase.workspace.oac_wire import load_runtime_policy
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.pilot_authoring import (
    initialize_enterprise_quote_pilot_draft,
    seal_enterprise_quote_pilot_pack,
)
from orgrebase.workspace.service import WorkspaceService
from orgrebase.workspace.source_admission import exact_locator_assets
from orgrebase.workspace.skill_rollback import load_frozen_predecessor

source = load_agentteams_source()
source.require_teamharness_identity(load_teamharness_lock())
assert runtime_asset_path("benchmark/quote-value-v0.1/public/current-process-baseline.json").is_file()
fixture = load_fixture()
assert fixture.organization_id == "org:northstar"
release_facts = json.loads(RELEASE_FACTS_PATH.read_text(encoding="utf-8"))
assert release_facts["workspace"]["agentteams_live"] == "NOT_RUN"
repo = OWBBenchmarkRepository()
assert repo.verify()["case_count"] == 192
policy, policy_digest = load_runtime_policy()
assert policy["policy_id"] == "policy:orgrebase-oac-local-runtime-admission"
assert policy_digest.startswith("sha256:")
assert len(exact_locator_assets()) == 10
predecessor = load_frozen_predecessor(verify_retained_wheel=False)
assert predecessor.resource_mode == "INSTALLED_WHEEL"
assert predecessor.manifest["version"] == "1.3.0"
legacy = OrgRebaseService()
preview = legacy.preview()
assert preview["preview"].state == "READY"
result = WorkspaceService.run_explicit_local_product_loop(
    Path("workspace.db"),
    allow_scripted_approval=True,
)
assert result["final_quote"].version == "v3"
assert result["final_quote"].payload["launch_date"] == "2026-09-15"
assert result["final_quote"].payload["currency"] == "EUR"
assert result["final_graph_pointer"].version == "v3"
assert result["event_chain"]["status"] == "PASS"
package_root = Path(__import__("orgrebase").__file__).resolve().parent
pilot_draft = Path("enterprise-pilot-draft")
pilot_pack = Path("enterprise-pilot-sealed")
init_receipt = initialize_enterprise_quote_pilot_draft(pilot_draft)
seal_receipt = seal_enterprise_quote_pilot_pack(pilot_draft, pilot_pack)
pilot_runtime = load_enterprise_quote_pilot_pack(pilot_pack)
pilot_result = _enterprise_pilot_check(pack=pilot_pack, work_dir=Path("enterprise-pilot-check"))
assert init_receipt["status"] == "DRAFT_CREATED"
assert seal_receipt["status"] == "SEALED_AND_PREFLIGHT_PASSED"
assert pilot_result["final_quote"]["id"] == "work:quote-blue-harbor"
assert pilot_result["final_quote"]["version"] == "v3"
assert pilot_result["independent_verification"]["status"] == "PASS"
print(json.dumps({
    "status": "PASS",
    "fixture_digest": fixture.digest,
    "benchmark_cases": 192,
    "legacy_preview_digest": preview["preview"].digest,
    "workspace_quote_ref": result["final_quote"].ref,
    "workspace_graph_ref": result["final_graph_pointer"].ref,
    "enterprise_pilot_pack_digest": pilot_runtime.pack_digest,
    "enterprise_pilot_quote_ref": "@".join((
        pilot_result["final_quote"]["id"],
        pilot_result["final_quote"]["version"],
    )),
}, sort_keys=True))
'''
        env = dict(os.environ)
        env["PYTHONPATH"] = str(site)
        env["PYTHONNOUSERSITE"] = "1"
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
        value["staged_runtime_assets"] = len(assets)
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
