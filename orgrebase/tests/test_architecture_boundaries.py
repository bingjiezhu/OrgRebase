from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "src" / "orgrebase"
COMPOSITION_ROOTS = {"api.py", "cli.py", "runtime_config.py"}
OAC_BRIDGE_MODULES = {
    "evolution_demo.py",
    "evolution_evidence.py",
    "evolution_runtime.py",
    "governed_evolution.py",
    "oac_wire.py",
    "outcome_contracts.py",
    "outcome_lab.py",
    "outcome_runtime.py",
    "outcome_verification.py",
    "outcome_portability.py",
    "outcome_pattern_bridge.py",
    "oac_admission.py",
    "oac_bridge_evidence.py",
    "oac_bridge.py",
    "outcome_assurance.py",
}

SPEC_039_MODULES = {
    "evolution_contracts.py",
    "evolution_demo.py",
    "evolution_evidence.py",
    "evolution_runtime.py",
    "governed_evolution.py",
    "outcome_assurance.py",
}


def test_pure_impact_preview_does_not_import_server_or_database_dependencies() -> None:
    script = """
import importlib.abc
import sys

blocked = {'httpx', 'httpx2', 'fastapi', 'uvicorn', 'sqlalchemy', 'psycopg', 'authlib'}
class PureEngineOnly(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in blocked:
            raise AssertionError('Unexpected service dependency: ' + fullname)
sys.meta_path.insert(0, PureEngineOnly())
from orgrebase.fixture import load_fixture
from orgrebase.impact import ImpactEngine, build_change_set

fixture = load_fixture()
preview = ImpactEngine(fixture).preview(build_change_set(fixture))
assert preview.state == 'READY'
assert preview.counts['affected_hard'] == 2
assert preview.counts['unknown'] == 1
assert 'orgrebase.service' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, text=True, capture_output=True,
        env={**os.environ, "PYTHONPATH": str(CORE.parent)}, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_root_service_export_remains_compatible_when_loaded_lazily() -> None:
    script = """
import orgrebase
import sys

assert orgrebase.__all__ == ['OrgRebaseService']
assert 'OrgRebaseService' in dir(orgrebase)
assert 'orgrebase.service' not in sys.modules
from orgrebase import OrgRebaseService
from orgrebase.service import OrgRebaseService as service
assert OrgRebaseService is service
assert orgrebase.OrgRebaseService is service
assert not hasattr(orgrebase, 'nonexistent_export')
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, text=True, capture_output=True,
        env={**os.environ, "PYTHONPATH": str(CORE.parent)}, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_core_modules_do_not_depend_on_workspace_implementation() -> None:
    violations: list[str] = []
    for path in sorted(CORE.glob("*.py")):
        if path.name in COMPOSITION_ROOTS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("orgrebase.workspace"):
                violations.append(f"{path.name}:{node.lineno}:{node.module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("orgrebase.workspace"):
                        violations.append(f"{path.name}:{node.lineno}:{alias.name}")
    assert violations == []


def test_runtime_composition_imports_only_the_workspace_factory_dependencies() -> None:
    tree = ast.parse((CORE / "runtime_config.py").read_text())
    dependencies = {
        node.module for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module == "orgrebase.workspace" or (node.module or "").startswith("orgrebase.workspace."))
    }
    assert dependencies == {"orgrebase.workspace.pilot", "orgrebase.workspace.service"}


def test_oac_bridge_uses_only_the_sibling_public_cli_boundary() -> None:
    violations: list[str] = []
    workspace = CORE / "workspace"
    for name in sorted(OAC_BRIDGE_MODULES):
        path = workspace / name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "oac":
                violations.append(f"{name}:{node.lineno}:{node.module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] == "oac":
                        violations.append(f"{name}:{node.lineno}:{alias.name}")
    assert violations == []


def test_oac_bridge_module_direction_is_acyclic_and_facade_has_no_logic() -> None:
    workspace = CORE / "workspace"
    forbidden = {
        "oac_wire.py": {"orgrebase.workspace.oac_admission", "orgrebase.workspace.oac_bridge_evidence"},
        "oac_admission.py": {"orgrebase.workspace.oac_bridge_evidence"},
    }
    violations: list[str] = []
    for name, forbidden_modules in forbidden.items():
        path = workspace / name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in forbidden_modules:
                violations.append(f"{name}:{node.lineno}:{node.module}")
    facade = ast.parse((workspace / "oac_bridge.py").read_text(encoding="utf-8"))
    assert [node for node in facade.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))] == []
    assert violations == []


def test_production_entrypoints_cannot_call_workspace_auto_approval_helpers() -> None:
    production_paths = (
        CORE / "api.py",
        CORE / "cli.py",
        *sorted((ROOT / "scripts").glob("*.py")),
    )
    forbidden = {"run_local_loop", "apply_change", "_apply_change_test_only"}
    violations: list[str] = []
    for path in production_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in forbidden:
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}:{node.attr}")
    assert violations == []

    service_tree = ast.parse((CORE / "workspace" / "service.py").read_text(encoding="utf-8"))
    public_methods = {
        node.name
        for node in ast.walk(service_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_")
    }
    assert "run_local_loop" not in public_methods
    assert "apply_change" not in public_methods


def test_dependency_tool_is_orchestrated_only_by_persisted_product_form() -> None:
    service_path = CORE / "workspace" / "service.py"
    tree = ast.parse(service_path.read_text(encoding="utf-8"), filename=str(service_path))
    callers: list[str] = []
    for function in (
        node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        if any(
            isinstance(node, ast.Attribute) and node.attr == "_invoke_dependency_evidence_tool"
            for node in ast.walk(function)
        ):
            callers.append(function.name)
    assert callers == ["form_quote_with_dependency_evidence"]


def test_spec_039_runtime_is_topology_driven_not_fixture_dispatched() -> None:
    path = CORE / "workspace" / "evolution_runtime.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    forbidden_literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in {"BASE", "SPLIT"}
    }
    compared_case_fields = [
        node.lineno for node in ast.walk(tree) if isinstance(node, ast.Attribute) and node.attr == "case_id"
    ]

    assert forbidden_literals == set()
    assert compared_case_fields == []


def test_spec_039_outcome_assurance_does_not_import_runtime_or_compiler_truth() -> None:
    path = CORE / "workspace" / "outcome_assurance.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    forbidden = {
        "orgrebase.workspace.evolution_runtime",
        "orgrebase.workspace.oac_wire",
        "oac.compiler",
        "oac.runtime_lowering",
    }
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imports.add(node.module or "")
        elif isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)

    assert not imports & forbidden
    assert "HANDLER_REGISTRY" not in path.read_text(encoding="utf-8")


def test_spec_039_focused_modules_stay_within_the_hard_size_budget() -> None:
    workspace = CORE / "workspace"
    counts = {
        name: sum(bool(line.strip()) for line in (workspace / name).read_text(encoding="utf-8").splitlines())
        for name in SPEC_039_MODULES
    }

    assert all(count <= 700 for count in counts.values()), counts


def test_quote_service_has_no_supplier_specific_evolution_branch() -> None:
    source = (CORE / "workspace" / "service.py").read_text(encoding="utf-8")

    assert "veracier" not in source.lower()
    assert "supplier_sc008" not in source.lower()
