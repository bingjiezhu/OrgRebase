from __future__ import annotations

import ast
import copy
import importlib.util
import json
import os
import subprocess
import sys
from collections import defaultdict, deque
from itertools import pairwise
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.fixture import EnterpriseFixture
from orgrebase.impact import ImpactEngine, build_change_set
from orgrebase.workspace.service import WorkspaceService

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_bounded_non_impact_witness.py"
TRUSTED = {
    "RUNTIME_OBSERVED",
    "OWNER_DECLARED_COMPLETE",
    "CONTRACT_DECLARED",
    "IMPORTED_VERIFIED",
}


def _load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("r012_bounded_non_impact_checker", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def _address(value: dict[str, Any], *, omit: set[str] | None = None) -> None:
    omitted = {"digest"} | (omit or set())
    value["digest"] = sha256_digest({key: item for key, item in value.items() if key not in omitted})


def _seal_bundle(
    bundle: dict[str, Any], *, world_changed: bool = False, certificate_changed: bool = False
) -> None:
    if world_changed:
        bundle["world_digest"] = sha256_digest(bundle["world"])
    if certificate_changed:
        _address(bundle["certificate"])
    _address(bundle)


def _raw(bundle: dict[str, Any]) -> bytes:
    return canonical_json(bundle).encode("utf-8")


def _bundle_from_preview(
    fixture: EnterpriseFixture, change: Any, preview: Any, target_id: str
) -> tuple[dict[str, Any], str]:
    certificate = next(item for item in preview.certificates if item.subject_id == target_id)
    world = {
        "change_set": change.model_dump(mode="json"),
        "target_object": fixture.object(target_id).model_dump(mode="json"),
        "dependencies": [
            item.model_dump(mode="json") for item in sorted(fixture.dependencies, key=lambda item: item.id)
        ],
        "target_manifest": fixture.dependency_manifest(target_id).model_dump(mode="json"),
        "revisions": {
            key: fixture.revisions[key]
            for key in (
                "graph",
                "policy",
                "skill_registry",
                "runtime_registry",
                "evaluation_suite",
            )
        },
    }
    world_root = sha256_digest(world)
    bundle = {
        "schema_version": checker.SCHEMA,
        "world_digest": world_root,
        "world": world,
        "certificate": certificate.model_dump(mode="json"),
    }
    _address(bundle)
    return bundle, world_root


def _canonical_bundle(
    fixture: EnterpriseFixture, target_id: str, *, proposed_value: Any | None = None
) -> tuple[dict[str, Any], str]:
    change = build_change_set(fixture, proposed_value=proposed_value)
    return _bundle_from_preview(fixture, change, ImpactEngine(fixture).preview(change), target_id)


def _edge(edge_id: str, source_id: str, target_id: str) -> dict[str, Any]:
    edge = {
        "id": edge_id,
        "source_id": source_id,
        "target_id": target_id,
        "relation": "ASSUMES",
        "strength": "HARD",
        "coverage_basis": "CONTRACT_DECLARED",
        "status": "ADMITTED",
        "provenance_refs": ["test:r012-adversarial"],
    }
    _address(edge)
    return edge


def _recommit_graph(bundle: dict[str, Any]) -> None:
    edges = sorted(bundle["world"]["dependencies"], key=lambda item: item["id"])
    bundle["world"]["dependencies"] = edges
    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    for edge in edges:
        if edge["status"] != "ADMITTED":
            excluded.append({"edge_id": edge["id"], "reason": f"STATUS_{edge['status']}"})
        elif edge["coverage_basis"] not in TRUSTED:
            excluded.append(
                {
                    "edge_id": edge["id"],
                    "reason": f"UNTRUSTED_COVERAGE_{edge['coverage_basis']}",
                }
            )
        else:
            eligible.append(edge)

    traversal = bundle["certificate"]["traversal_commitment"]
    source = traversal["source_id"]
    adjacency: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in eligible:
        adjacency[edge["source_id"]].append(edge)
    queue = deque([(source, 0)])
    depths = {source: 0}
    used: set[str] = set()
    frontier: set[str] = set()
    while queue:
        node, depth = queue.popleft()
        if depth >= checker.MAX_DEPTH:
            frontier.update(edge["id"] for edge in adjacency.get(node, []))
            continue
        for edge in adjacency.get(node, []):
            used.add(edge["id"])
            next_depth = depth + 1
            if next_depth < depths.get(edge["target_id"], checker.MAX_DEPTH + 1):
                depths[edge["target_id"]] = next_depth
                queue.append((edge["target_id"], next_depth))

    used_edges = [edge for edge in eligible if edge["id"] in used]
    traversal.update(
        {
            "dependency_snapshot_digest": sha256_digest(edges),
            "eligible_edge_ids": [edge["id"] for edge in eligible],
            "excluded_edges": excluded,
            "reachable_node_ids": sorted(depths),
            "reachable_edge_ids": [edge["id"] for edge in used_edges],
            "reachable_slice_digest": sha256_digest(used_edges),
            "truncated_frontier_edge_ids": sorted(frontier),
            "budget_exhausted": bool(frontier),
        }
    )
    _seal_bundle(bundle, world_changed=True, certificate_changed=True)


def _assert_rejected(bundle: dict[str, Any], root: str, code: str) -> None:
    with pytest.raises(checker.CheckError) as raised:
        checker.check_bundle_json(_raw(bundle), root)
    assert raised.value.code == code


@pytest.mark.parametrize("target_id", ("work:legal_review_c", "work:finance_analysis_d"))
def test_real_impact_engine_bounded_non_impact_certificates_pass(
    fixture: EnterpriseFixture, target_id: str
) -> None:
    bundle, world_root = _canonical_bundle(fixture, target_id)

    receipt = checker.check_bundle_json(_raw(bundle), world_root)

    assert receipt["status"] == "PASS"
    assert receipt["checker_version"] == "orgrebase.standalone-dag-witness-checker@1.0.1"
    assert receipt["subject_id"] == target_id
    assert receipt["world_root_digest"] == world_root
    assert receipt["support_domain"] == "DAG_ONLY_SOURCE_REACHABLE_GRAPH_MAX_DEPTH_8"
    assert "static_stdlib_import_surface" not in receipt["checked"]
    assert "CHECKER_SOURCE_IDENTITY_AND_RUNTIME_IMPORT_CLOSURE" in receipt["not_independently_checked"]
    assert "RUN_ID_NONCE_FRESHNESS_OR_REPLAY" in receipt["not_independently_checked"]
    assert "OBJECT_TEMPORAL_VALIDITY" in receipt["not_independently_checked"]
    assert "EXPECTED_WORLD_ROOT_ORIGIN_AND_AUTHORITY" in receipt["not_independently_checked"]
    assert "GLOBAL_NON_IMPACT" in receipt["not_independently_checked"]


def test_real_workspace_quote_additive_slot_provenance_passes_isolated_checker(
    tmp_path: Path,
) -> None:
    service = WorkspaceService(store_path=tmp_path / "workspace.sqlite")
    try:
        service.form_quote()
        preview_bundle = service.preview_change("launch_date")
        fixture = service._fixture_for_change(preview_bundle.change_spec)
        bundle, world_root = _bundle_from_preview(
            fixture,
            preview_bundle.change_set,
            preview_bundle.preview,
            "work:finance_analysis_d",
        )
    finally:
        service.close()

    manifest = bundle["world"]["target_manifest"]
    slot = manifest["requirement_slots"][0]
    edge = next(item for item in bundle["world"]["dependencies"] if item["id"] == slot["edge_id"])
    assert set(edge["provenance_refs"]) < set(slot["provenance_refs"])
    bundle_path = tmp_path / "workspace-finance-bundle.json"
    bundle_path.write_bytes(_raw(bundle))

    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            str(SCRIPT),
            "--bundle",
            str(bundle_path),
            "--expected-world-root",
            world_root,
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout
    receipt = json.loads(completed.stdout)
    assert receipt["status"] == "PASS"
    assert receipt["subject_id"] == "work:finance_analysis_d"


def test_fresh_isolated_python_closure_loads_no_product_module(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--self-audit"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    receipt = json.loads(completed.stdout)
    assert receipt["status"] == "PASS"
    assert receipt["loaded_orgrebase_modules"] == []
    assert all(not name.startswith("orgrebase") for name in receipt["imports"])


def test_checker_is_small_and_has_only_stdlib_imports() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        node.module.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }

    assert len(source.splitlines()) <= 450
    assert "orgrebase" not in imports
    assert imports <= sys.stdlib_module_names | {"__future__"}


@pytest.mark.parametrize("surface", ("target_state", "manifest", "change", "revision", "edges"))
def test_external_world_root_pins_every_world_surface(fixture: EnterpriseFixture, surface: str) -> None:
    bundle, original_root = _canonical_bundle(fixture, "work:legal_review_c")
    world = bundle["world"]
    if surface == "target_state":
        # VersionedObject intentionally excludes mutable state from its own address.
        world["target_object"]["state"] = "ACTIVE"
    elif surface == "manifest":
        world["target_manifest"]["issuer_id"] = "human:other-legal-owner"
        _address(world["target_manifest"])
    elif surface == "change":
        world["change_set"]["purpose"] = "other_rebase_run"
        _address(world["change_set"])
    elif surface == "revision":
        world["revisions"]["graph"] = "graph:canonical@r2"
    else:
        world["dependencies"][0]["provenance_refs"].append("test:other-run")
        _address(world["dependencies"][0])
    _seal_bundle(bundle, world_changed=True)

    _assert_rejected(bundle, original_root, "WORLD_ROOT_MISMATCH")


@pytest.mark.parametrize(
    ("field", "code"),
    (
        ("change_set_digest", "CERTIFICATE_CHANGE_BINDING_INVALID"),
        ("result_digest", "CERTIFICATE_RESULT_BINDING_INVALID"),
        ("revision_lock_digest", "CERTIFICATE_REVISION_BINDING_INVALID"),
        ("claim_boundary", "CERTIFICATE_PROTOCOL_INVALID"),
    ),
)
def test_resealed_certificate_cannot_escape_world_binding(
    fixture: EnterpriseFixture, field: str, code: str
) -> None:
    bundle, root = _canonical_bundle(fixture, "work:finance_analysis_d")
    if field == "claim_boundary":
        bundle["certificate"][field] = "This falsely claims global non-impact."
    else:
        bundle["certificate"][field] = "sha256:" + "f" * 64
    _seal_bundle(bundle, certificate_changed=True)

    _assert_rejected(bundle, root, code)


def test_resealed_target_coverage_digest_tamper_is_rejected(
    fixture: EnterpriseFixture,
) -> None:
    bundle, root = _canonical_bundle(fixture, "work:legal_review_c")
    traversal = bundle["certificate"]["traversal_commitment"]
    traversal["target_coverage_digest"] = "sha256:" + "a" * 64
    _seal_bundle(bundle, certificate_changed=True)

    _assert_rejected(bundle, root, "TARGET_COVERAGE_INVALID")


@pytest.mark.parametrize(
    ("duplicate", "code"),
    (
        ("slot", "MANIFEST_SLOT_ID_DUPLICATE"),
        ("edge", "MANIFEST_EDGE_ID_DUPLICATE"),
    ),
)
def test_manifest_slot_and_edge_ids_are_bijective(
    fixture: EnterpriseFixture, duplicate: str, code: str
) -> None:
    bundle, _ = _canonical_bundle(fixture, "work:legal_review_c")
    manifest = bundle["world"]["target_manifest"]
    copied = copy.deepcopy(manifest["requirement_slots"][0])
    if duplicate == "edge":
        copied["slot_id"] = "slot:legal-residency-copy"
    manifest["requirement_slots"].append(copied)
    _address(manifest)
    _seal_bundle(bundle, world_changed=True)

    _assert_rejected(bundle, bundle["world_digest"], code)


def test_reachable_target_cannot_retain_bounded_non_impact_claim(
    fixture: EnterpriseFixture,
) -> None:
    bundle, _ = _canonical_bundle(fixture, "work:legal_review_c")
    traversal = bundle["certificate"]["traversal_commitment"]
    bundle["world"]["dependencies"].append(
        _edge("edge:r012-source-to-target", traversal["source_id"], traversal["target_id"])
    )
    _recommit_graph(bundle)

    _assert_rejected(bundle, bundle["world_digest"], "TARGET_REACHABLE")


def test_real_truncated_frontier_fails_closed(fixture: EnterpriseFixture) -> None:
    bundle, _ = _canonical_bundle(fixture, "work:legal_review_c")
    source = bundle["certificate"]["traversal_commitment"]["source_id"]
    nodes = [source, *(f"claim:r012-depth-{index}" for index in range(1, 10))]
    for index, (left, right) in enumerate(pairwise(nodes), start=1):
        bundle["world"]["dependencies"].append(_edge(f"edge:r012-depth-{index:02d}", left, right))
    _recommit_graph(bundle)

    assert bundle["certificate"]["traversal_commitment"]["truncated_frontier_edge_ids"]
    _assert_rejected(bundle, bundle["world_digest"], "TRAVERSAL_BUDGET_EXHAUSTED")


@pytest.mark.parametrize(
    ("raw", "code"),
    (
        (b'{"digest":"one","digest":"two"}', "JSON_DUPLICATE_KEY"),
        (b'{"value":NaN}', "JSON_NON_FINITE_NUMBER"),
        (b'{"value":Infinity}', "JSON_NON_FINITE_NUMBER"),
        (b'{"value":1e999}', "JSON_NON_FINITE_NUMBER"),
        (b"\xff", "JSON_PARSE_INVALID"),
    ),
)
def test_strict_json_parser_rejects_ambiguous_or_non_finite_inputs(raw: bytes, code: str) -> None:
    with pytest.raises(checker.CheckError) as raised:
        checker.check_bundle_json(raw, "sha256:" + "0" * 64)
    assert raised.value.code == code


def test_strict_json_parser_enforces_depth_size_and_bytes() -> None:
    root = "sha256:" + "0" * 64
    too_deep = b"[" * (checker.MAX_JSON_DEPTH + 2) + b"0" + b"]" * (checker.MAX_JSON_DEPTH + 2)
    with pytest.raises(checker.CheckError) as depth:
        checker.check_bundle_json(too_deep, root)
    assert depth.value.code == "JSON_DEPTH_LIMIT_EXCEEDED"

    with pytest.raises(checker.CheckError) as size:
        checker.check_bundle_json(b" " * (checker.MAX_BYTES + 1), root)
    assert size.value.code == "JSON_BYTE_LIMIT_EXCEEDED"

    with pytest.raises(checker.CheckError) as shape:
        checker.check_bundle_json((b"{}",), root)
    assert shape.value.code == "JSON_BYTES_REQUIRED"


@pytest.mark.parametrize(
    ("field", "code"),
    (
        ("strength", "EDGE_STRENGTH_INVALID"),
        ("coverage_basis", "EDGE_COVERAGE_INVALID"),
        ("status", "EDGE_STATUS_INVALID"),
    ),
)
def test_valid_json_unhashable_enums_fail_closed(fixture: EnterpriseFixture, field: str, code: str) -> None:
    bundle, _ = _canonical_bundle(fixture, "work:legal_review_c")
    edge = bundle["world"]["dependencies"][0]
    edge[field] = []
    _address(edge)
    _seal_bundle(bundle, world_changed=True)

    _assert_rejected(bundle, bundle["world_digest"], code)


def test_target_must_itself_declare_trusted_complete_coverage(
    fixture: EnterpriseFixture,
) -> None:
    bundle, _ = _canonical_bundle(fixture, "work:legal_review_c")
    target = bundle["world"]["target_object"]
    target["coverage_complete"] = False
    _address(target, omit={"state"})
    _seal_bundle(bundle, world_changed=True)

    _assert_rejected(bundle, bundle["world_digest"], "TARGET_COVERAGE_DECLARATION_REQUIRED")


def test_mislabeled_noop_delta_is_rejected(fixture: EnterpriseFixture) -> None:
    bundle, _ = _canonical_bundle(fixture, "work:legal_review_c")
    delta = bundle["world"]["change_set"]["deltas"][0]
    delta["proposed_value"] = copy.deepcopy(delta["base_value"])
    _address(delta)
    _address(bundle["world"]["change_set"])
    _seal_bundle(bundle, world_changed=True)

    _assert_rejected(bundle, bundle["world_digest"], "SEMANTIC_DELTA_VALUE_MISMATCH")


@pytest.mark.parametrize(
    ("mutation", "code"),
    (
        ("empty_edge", "TRUSTED_EDGE_PROVENANCE_MISSING"),
        ("mismatched_slot", "MANIFEST_SLOT_BINDING_INVALID"),
        ("unhashable_slot", "MANIFEST_SLOT_PROVENANCE_INVALID"),
    ),
)
def test_trusted_edge_and_slot_provenance_are_structurally_coherent(
    fixture: EnterpriseFixture, mutation: str, code: str
) -> None:
    bundle, _ = _canonical_bundle(fixture, "work:legal_review_c")
    manifest = bundle["world"]["target_manifest"]
    edge_id = manifest["requirement_slots"][0]["edge_id"]
    edge = next(item for item in bundle["world"]["dependencies"] if item["id"] == edge_id)
    if mutation == "empty_edge":
        edge["provenance_refs"] = []
        manifest["requirement_slots"][0]["provenance_refs"] = []
    elif mutation == "mismatched_slot":
        manifest["requirement_slots"][0]["provenance_refs"] = ["source:unbound"]
    else:
        manifest["requirement_slots"][0]["provenance_refs"] = [{"source": "unhashable"}]
    _address(edge)
    _address(manifest)
    _seal_bundle(bundle, world_changed=True)

    _assert_rejected(bundle, bundle["world_digest"], code)


def test_world_root_commits_canonical_world_not_transport_whitespace(
    fixture: EnterpriseFixture,
) -> None:
    bundle, root = _canonical_bundle(fixture, "work:finance_analysis_d")
    pretty = json.dumps(bundle, ensure_ascii=False, indent=2).encode()

    receipt = checker.check_bundle_json(pretty, root)

    assert "externally_anchored_canonical_world" in receipt["checked"]
    assert "canonical JSON world" in receipt["claim_boundary"]
    assert "does not bind whitespace" in receipt["claim_boundary"]


def test_json_node_limit_is_enforced_before_semantic_hashing() -> None:
    raw = ("[" + ",".join("0" for _ in range(checker.MAX_JSON_NODES + 1)) + "]").encode()
    assert len(raw) < checker.MAX_BYTES
    with pytest.raises(checker.CheckError) as raised:
        checker.check_bundle_json(raw, "sha256:" + "0" * 64)
    assert raised.value.code == "JSON_NODE_LIMIT_EXCEEDED"


def test_cli_failure_is_machine_readable_and_exits_two(tmp_path: Path) -> None:
    bundle_path = tmp_path / "duplicate.json"
    bundle_path.write_bytes(b'{"digest":"one","digest":"two"}')

    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            str(SCRIPT),
            "--bundle",
            str(bundle_path),
            "--expected-world-root",
            "sha256:" + "0" * 64,
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert json.loads(completed.stdout) == {
        "status": "FAIL",
        "code": "JSON_DUPLICATE_KEY",
    }


def test_cli_bounds_regular_files_before_reading_and_rejects_fifo(
    tmp_path: Path,
) -> None:
    root = "sha256:" + "0" * 64
    oversized = tmp_path / "oversized.json"
    with oversized.open("wb") as handle:
        handle.seek(checker.MAX_BYTES)
        handle.write(b"x")
    fifo = tmp_path / "bundle.fifo"
    os.mkfifo(fifo)

    for path, code in (
        (oversized, "JSON_BYTE_LIMIT_EXCEEDED"),
        (fifo, "BUNDLE_FILE_NOT_REGULAR"),
    ):
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                str(SCRIPT),
                "--bundle",
                str(path),
                "--expected-world-root",
                root,
            ],
            cwd=tmp_path,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert completed.returncode == 2
        assert json.loads(completed.stdout) == {"status": "FAIL", "code": code}


@pytest.mark.parametrize("arguments", (("--unknown",), ("--bundle",)))
def test_cli_argument_errors_are_machine_readable(arguments: tuple[str, ...]) -> None:
    completed = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert json.loads(completed.stdout) == {
        "status": "FAIL",
        "code": "CLI_ARGUMENTS_INVALID",
    }
    assert "Traceback" not in completed.stderr
