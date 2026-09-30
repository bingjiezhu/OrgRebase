from __future__ import annotations

import ast
import fnmatch
import hashlib
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

PRODUCT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PRODUCT_ROOT.parent
WORKFLOW = next(
    (path for path in (
        WORKSPACE_ROOT / ".github/workflows/docs.yml",
        WORKSPACE_ROOT / "github-root/.github/workflows/docs.yml",
    ) if path.is_file()),
    PRODUCT_ROOT / ".github/workflows/docs.yml",
)
PRODUCT_WORKFLOW = PRODUCT_ROOT / ".github/workflows/docs.yml"
pytestmark = pytest.mark.skipif(not WORKFLOW.is_file(), reason="Source distributions omit GitHub workflows")


def _site_root_pages() -> tuple[str, ...]:
    source = ast.parse((PRODUCT_ROOT / "documentation/build.py").read_text())
    assignment = next(
        node for node in source.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "ROOT_PAGES" for target in node.targets)
    )
    return ast.literal_eval(assignment.value)


def test_pages_build_runs_for_every_document_source_on_push_and_pr() -> None:
    if WORKFLOW != PRODUCT_WORKFLOW:
        assert WORKFLOW.read_bytes() == PRODUCT_WORKFLOW.read_bytes()
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    sources = [
        *(f"orgrebase/{name}" for name in _site_root_pages()),
        "orgrebase/docs/guide/index.zh.md",
        "orgrebase/docs/diagrams/example.html",
        "orgrebase/documentation/build.py",
        "orgrebase/src/orgrebase/workspace/service.py",  # linked-source pages
        "orgrebase/.github/workflows/docs.yml",
        ".github/workflows/docs.yml",
        "oac-spec/src/oac/compiler.py",  # downloadable source includes OAC
        "LICENSE",
        "README.md",
    ]
    for event in ("push", "pull_request"):
        paths = workflow["on"][event]["paths"]
        for source in sources:
            assert any(fnmatch.fnmatchcase(source, pattern) for pattern in paths), source
    assert workflow["on"]["push"]["branches"] == ["main"]


def test_documentation_actions_are_fixed_and_source_outputs_are_external() -> None:
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    assert workflow["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            if "uses" in step:
                assert re.fullmatch(r"(?:actions|astral-sh)/[\w-]+@[0-9a-f]{40}", step["uses"])
    source = next(
        step["run"] for step in workflow["jobs"]["build"]["steps"]
        if step.get("name") == "Build and verify matching public source"
    )
    assert "build_source_snapshot.py build" in source
    assert "build_source_snapshot.py verify" in source
    assert '--oac-root "$GITHUB_WORKSPACE/oac-spec"' in source
    assert source.count('--profile github --output-dir "$RUNNER_TEMP/orgrebase-docs-source"') == 2


def test_pr_build_cannot_receive_pages_deploy_permissions() -> None:
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    assert workflow["permissions"] == {"contents": "read"}
    build = workflow["jobs"]["build"]
    assert not build.get("permissions")
    assert any(step.get("uses", "").startswith("actions/upload-artifact@") for step in build["steps"])
    pages_artifact = next(
        step for step in build["steps"]
        if step.get("uses", "").startswith("actions/upload-pages-artifact@")
    )
    assert "github.ref == 'refs/heads/main'" in pages_artifact["if"]
    assert "github.event_name != 'pull_request'" in pages_artifact["if"]

    deploy = workflow["jobs"]["deploy"]
    assert deploy["needs"] == "build"
    assert "github.ref == 'refs/heads/main'" in deploy["if"]
    assert "github.event_name != 'pull_request'" in deploy["if"]
    assert deploy["permissions"]["pages"] == "write"
    assert deploy["permissions"]["id-token"] == "write"

    site_build = next(step for step in build["steps"] if step.get("name") == "Build static documentation")
    assert site_build["env"]["SITE_URL"] == "${{ inputs.site_url }}"
    assert "${{ inputs.site_url }}" not in site_build["run"]


@pytest.mark.parametrize(
    ("event", "ref", "site_url", "expected_url"),
    [
        ("pull_request", "refs/pull/12/merge", "https://preview.example/", None),
        ("workflow_dispatch", "refs/heads/docs-preview", "https://preview.example/", None),
        ("push", "refs/heads/main", "", "https://bingjiezhu.github.io/OrgRebase/"),
        ("workflow_dispatch", "refs/heads/main", "https://example.org/OrgRebase/", "https://example.org/OrgRebase/"),
    ],
)
def test_only_main_publication_build_receives_canonical_url(
    tmp_path: Path, event: str, ref: str, site_url: str, expected_url: str | None
) -> None:
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    build = workflow["jobs"]["build"]
    script = next(step["run"] for step in build["steps"] if step.get("name") == "Build static documentation")
    uv_stub = tmp_path / "uv"
    uv_stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    uv_stub.chmod(0o755)
    source = tmp_path / "orgrebase-docs-source/orgrebase-oac-source-snapshot.zip"
    source.parent.mkdir()
    source.write_bytes(b"matching-public-source")
    env = os.environ | {
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_EVENT_NAME": event,
        "GITHUB_REF": ref,
        "SITE_URL": site_url,
    }
    result = subprocess.run(["bash", "-e", "-c", script], env=env, capture_output=True, text=True, check=True)
    args = result.stdout.splitlines()
    assert "--output" in args
    assert args[args.index("--source-archive") + 1] == str(source)
    assert args[args.index("--source-sha256") + 1] == hashlib.sha256(source.read_bytes()).hexdigest()
    if expected_url is None:
        assert "--site-url" not in args
    else:
        assert args[args.index("--site-url") + 1] == expected_url
