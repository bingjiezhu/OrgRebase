from __future__ import annotations

import os
import subprocess
import sys
import zipfile

from scripts import build_offline_release as release


def test_oac_runtime_view_loads_packaged_source_identity_outside_checkout(tmp_path):
    wheel = release.build_wheel(release.load_config(), tmp_path)
    site = tmp_path / "site"
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(site)
    run = tmp_path / "run"
    run.mkdir()
    code = """
from pathlib import Path
import os
import orgrebase
from orgrebase.agentteams_source import load_agentteams_source
from orgrebase.resource_paths import PROJECT_ROOT, runtime_asset_path
from orgrebase.store import StateStore
from orgrebase.workspace.oac_agentic_runtime import OACAgenticRuntime
from orgrebase.workspace.oac_quote_adaptation import OACQuoteAdaptationService
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.service import WorkspaceService

site = Path(os.environ['ORGREBASE_TEST_SITE'])
assert site in Path(orgrebase.__file__).resolve().parents
assert not (PROJECT_ROOT / 'agentteams/source-lock.json').exists()
pack = load_enterprise_quote_pilot_pack(runtime_asset_path('examples/enterprise-quote-pilot/evergreen'))
workspace = WorkspaceService(store_path=':memory:', runtime_configuration=pack)
adaptation = OACQuoteAdaptationService(
    store=StateStore(':memory:'), profile=pack.profile, runtime=pack,
    oac_root=os.environ['ORGREBASE_TEST_OAC_ROOT'],
    execution_run_id=workspace.effective_workflow_run_id,
)
try:
    runtime = OACAgenticRuntime(
        runtime_root=Path.cwd() / 'runtime', repo_root=PROJECT_ROOT,
        checkout=PROJECT_ROOT / '.tmp/agentteams',
        lock_path=PROJECT_ROOT / 'agentteams/teamharness-lock.json',
        pack_path=pack.pack_root, frozen_golden_root=Path.cwd() / 'golden',
        adaptation_service=adaptation, workspace_service=workspace,
        execution_mode='OFFLINE_LOCAL', shadow_model_provider='ollama-local',
    )
    view = runtime.view()
    assert view['status'] == 'NOT_STARTED'
    assert view['agent_mapping']['native_lifecycle']['runtime'] == f'AgentTeams {load_agentteams_source().tag}'
    assert not runtime.runtime_root.exists()
finally:
    adaptation.store.close()
    workspace.close()
print('OAC_WHEEL_INITIAL_VIEW_PASS')
"""
    env = dict(os.environ)
    env.update({
        "PYTHONPATH": str(site), "PYTHONNOUSERSITE": "1",
        "ORGREBASE_TEST_SITE": str(site),
        "ORGREBASE_TEST_OAC_ROOT": str(release.ROOT.parent / "oac-spec"),
    })
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=run, env=env,
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "OAC_WHEEL_INITIAL_VIEW_PASS"
