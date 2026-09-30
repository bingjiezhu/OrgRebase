from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PRODUCT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PRODUCT_ROOT.parent
CANONICAL_WORKFLOW = WORKSPACE_ROOT / ".github/workflows/ci.yml"
COMPATIBILITY_WORKFLOW = PRODUCT_ROOT / ".github/workflows/ci.yml"
WORKSPACE_LAYOUT = (WORKSPACE_ROOT / "oac-spec/pyproject.toml").is_file()
WORKFLOW = (
    CANONICAL_WORKFLOW
    if WORKSPACE_LAYOUT and CANONICAL_WORKFLOW.is_file()
    else COMPATIBILITY_WORKFLOW
)
pytestmark = pytest.mark.skipif(not WORKFLOW.is_file(), reason="Source distributions omit GitHub workflows")


def jobs() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]


def test_workspace_root_workflow_is_canonical_and_compatibility_copy_cannot_drift() -> None:
    if not WORKSPACE_LAYOUT or not CANONICAL_WORKFLOW.is_file():
        pytest.skip("Standalone product source distribution has no workspace-root workflow")
    assert COMPATIBILITY_WORKFLOW.read_bytes() == CANONICAL_WORKFLOW.read_bytes()
    canonical_docs = CANONICAL_WORKFLOW.with_name("docs.yml")
    compatibility_docs = COMPATIBILITY_WORKFLOW.with_name("docs.yml")
    assert canonical_docs.read_bytes() == compatibility_docs.read_bytes()
    for relative in ("dependabot.yml", "pull_request_template.md"):
        assert (WORKSPACE_ROOT / ".github" / relative).read_bytes() == (
            PRODUCT_ROOT / ".github" / relative
        ).read_bytes()


def test_core_ci_runs_from_orgrebase_without_oac_variables_or_secrets() -> None:
    core = jobs()["core"]
    assert not core.get("if") and not core.get("needs") and not core.get("env")
    assert core["defaults"]["run"]["working-directory"] == "orgrebase"
    assert any(step.get("run") == "make check-core" for step in core["steps"])
    for step in core["steps"]:
        assert "secrets." not in str(step)
        if step.get("uses", "").startswith("actions/checkout@"):
            assert not step.get("with", {}).get("repository")
            assert not step.get("with", {}).get("path")
            assert step["with"]["persist-credentials"] is False


def test_full_check_uses_sibling_oac_tree() -> None:
    check = jobs()["check"]
    assert check["needs"] == "core" or check["needs"] == ["core"]
    assert "workflow_dispatch" in check["if"]
    assert check["defaults"]["run"]["working-directory"] == "orgrebase"
    assert check["env"]["ORGREBASE_OAC_ROOT"] == "${{ github.workspace }}/oac-spec"
    assert any("verify_oac_dependency.py" in str(step.get("run", "")) for step in check["steps"])
    for step in check["steps"]:
        if step.get("uses", "").startswith("actions/checkout@"):
            assert not step.get("with", {}).get("repository")
            assert not step.get("with", {}).get("path")
            assert step["with"]["persist-credentials"] is False


def test_all_product_changes_run_the_single_complete_enterprise_test_list() -> None:
    boundary = jobs()["enterprise-boundaries"]
    assert boundary["needs"] == "core"
    assert boundary["if"] == "github.event_name != 'workflow_dispatch'"
    assert boundary["env"]["ORGREBASE_REQUIRE_POSTGRES_TESTS"] == "1"
    commands = "\n".join(str(step.get("run", "")) for step in boundary["steps"])
    assert "postgresql-17" in commands
    assert "verify_oac_dependency.py" in commands
    assert any(step.get("run") == "make check-enterprise-boundaries" for step in boundary["steps"])
    assert "ENTERPRISE_BOUNDARY_TESTS=" not in commands


@pytest.mark.parametrize("path", [
    "orgrebase/src/orgrebase/api.py",
    "orgrebase/src/orgrebase/workspace/change_budget.py",
    "orgrebase/src/orgrebase/workspace/onboarding_drafts.py",
    "orgrebase/src/orgrebase/workspace/pattern_governance.py",
    "orgrebase/src/orgrebase/workspace/rebuild.py",
    "orgrebase/demo/console/app.js",
    "orgrebase/skills/structured-domain-handoff/SKILL.md",
    "orgrebase/examples/enterprise-quote-pilot/evergreen/pack.json",
    "orgrebase/fixtures/enterprise.json",
    "orgrebase/run-enterprise-pilot.sh",
    "oac-spec/src/oac/__init__.py",
])
def test_protected_product_inputs_trigger_both_push_and_pull_request(path: str) -> None:
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    for event in ("push", "pull_request"):
        trigger = workflow["on"][event]
        assert not trigger or "paths" not in trigger, path


def test_every_pr_runs_required_checks_without_path_filter_deadlocks() -> None:
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    assert workflow["on"]["push"] == {"branches": ["main"]}
    assert not workflow["on"]["pull_request"]
    assert set(workflow["jobs"]) >= {"core", "oac-contract", "enterprise-boundaries"}


def test_skipped_or_failed_full_check_cannot_qualify_a_release_candidate() -> None:
    candidate = jobs()["release-candidate"]
    assert set(candidate["needs"]) == {"core", "check"}
    assert "needs.core.result == 'success'" in candidate["if"]
    assert "needs.check.result == 'success'" in candidate["if"]
    assert "github.event_name == 'workflow_dispatch'" in candidate["if"]
    assert "github.ref == 'refs/heads/main'" in candidate["if"]
    assert candidate["defaults"]["run"]["working-directory"] == "orgrebase"
    assert candidate["env"]["ORGREBASE_OAC_ROOT"] == "${{ github.workspace }}/oac-spec"


def test_every_packaged_runtime_asset_is_protected_by_ci() -> None:
    import tomllib

    project = tomllib.loads((PRODUCT_ROOT / "pyproject.toml").read_text())
    assets = project["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    for asset in assets:
        path = "orgrebase/" + asset
        if (PRODUCT_ROOT / asset).is_dir():
            path += "/runtime-resource"
        for event in ("push", "pull_request"):
            trigger = workflow["on"][event]
            assert not trigger or "paths" not in trigger, path


def test_actions_are_immutable_and_signing_permissions_are_restricted() -> None:
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    for name, job in workflow["jobs"].items():
        for step in job["steps"]:
            if "uses" in step:
                assert re.fullmatch(r"(?:actions|astral-sh)/[\w-]+@[0-9a-f]{40}", step["uses"])
        if name != "release-candidate":
            assert not job.get("permissions")
    candidate = workflow["jobs"]["release-candidate"]
    assert candidate["permissions"] == {
        "contents": "read", "id-token": "write", "attestations": "write",
    }
    inventories = [step for step in candidate["steps"] if step.get("with", {}).get("sbom-path")]
    assert len(inventories) == 2
    for step in inventories:
        subject = step["with"]["subject-path"]
        if "orgrebase.sbom" in step["with"]["sbom-path"]:
            assert "orgrebase-*.whl" in subject and "oac_contract" not in subject
        else:
            assert "oac_contract-*.whl" in subject and "/orgrebase-*.whl" not in subject


@pytest.mark.parametrize("build_auxiliary", [False, True])
def test_candidate_delivery_uses_exact_component_inputs_and_clean_assets(
    tmp_path: Path, build_auxiliary: bool
) -> None:
    candidate = jobs()["release-candidate"]
    script = next(
        step["run"] for step in candidate["steps"]
        if step.get("name") == "Bind each SBOM to its own exact distributions"
    )
    code = script.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    ast.parse(code)
    workspace, temporary = tmp_path / "workspace", tmp_path / "runner"
    temporary.mkdir()
    for component, name, build_directory in (
        ("orgrebase", "orgrebase", "orgrebase-dist"),
        ("oac-spec", "oac-contract", "oac-dist"),
    ):
        root = workspace / component
        root.mkdir(parents=True)
        (root / "pyproject.toml").write_text(
            f'[project]\nname="{name}"\nversion="1.0"\nlicense="Apache-2.0"\n'
        )
        (root / "uv.lock").write_text(
            f'[[package]]\nname="{name}"\nversion="1.0"\n'
            'dependencies=[{name="sample-dependency"}]\n'
            '[[package]]\nname="sample-dependency"\nversion="2.0"\n'
        )
        metadata = root / ".venv/lib/python3.12/site-packages/sample_dependency-2.0.dist-info/METADATA"
        metadata.parent.mkdir(parents=True)
        metadata.write_text(
            'Metadata-Version: 2.4\nName: sample-dependency\nVersion: 2.0\nLicense-Expression: MIT\n\n'
        )
        distribution = temporary / build_directory
        distribution.mkdir()
        for suffix in (".whl", ".tar.gz"):
            (distribution / (name + suffix)).write_bytes((name + suffix).encode())
        if build_auxiliary:
            (distribution / ".gitignore").write_text("*\n")
    source = temporary / "orgrebase-source/orgrebase-oac-source-snapshot.zip"
    source.parent.mkdir()
    source.write_bytes(b"reviewed-public-source")
    subprocess.run(
        [sys.executable, "-c", code], cwd=PRODUCT_ROOT,
        env=os.environ | {"GITHUB_WORKSPACE": str(workspace), "RUNNER_TEMP": str(temporary)},
        check=True, capture_output=True, text=True,
    )
    delivery = temporary / "orgrebase-candidate"
    assert {path.name for path in delivery.iterdir()} == {
        "orgrebase.whl", "orgrebase.tar.gz", "oac-contract.whl", "oac-contract.tar.gz",
        source.name, "orgrebase.sbom.cdx.json", "oac-contract.sbom.cdx.json", "SHA256SUMS.txt",
    }
    for component in ("orgrebase", "oac-contract"):
        bom = json.loads((delivery / (component + ".sbom.cdx.json")).read_text())
        assert bom["metadata"]["component"]["name"] == component
        assert bom["components"][0]["licenses"] == [{"expression": "MIT"}]
        artifact_properties = {
            item["name"]: item["value"] for item in bom["metadata"]["component"]["properties"]
            if item["name"].startswith("orgrebase.artifact.")
        }
        assert artifact_properties[f"orgrebase.artifact.{component}.whl.sha256"] == hashlib.sha256(
            (delivery / (component + ".whl")).read_bytes()
        ).hexdigest()
        other = "oac-contract" if component == "orgrebase" else "orgrebase"
        assert not any(f".{other}." in key for key in artifact_properties)
    for line in (delivery / "SHA256SUMS.txt").read_text().splitlines():
        digest, name = line.split("  ", 1)
        assert digest == hashlib.sha256((delivery / name).read_bytes()).hexdigest()
