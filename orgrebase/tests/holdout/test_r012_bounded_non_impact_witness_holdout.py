from __future__ import annotations

import copy
import importlib.util
from collections import defaultdict, deque
from itertools import pairwise
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.fixture import EnterpriseFixture
from orgrebase.impact import ImpactEngine, build_change_set

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_bounded_non_impact_witness.py"
TRUSTED = {
    "RUNTIME_OBSERVED",
    "OWNER_DECLARED_COMPLETE",
    "CONTRACT_DECLARED",
    "IMPORTED_VERIFIED",
}


def _load_holdout_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("r012_holdout_checker", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = _load_holdout_checker()


def _address(value: dict[str, Any]) -> None:
    value["digest"] = sha256_digest({key: item for key, item in value.items() if key != "digest"})


def _holdout_bundle(
    fixture: EnterpriseFixture, *, proposed_value: Any | None = None
) -> tuple[dict[str, Any], str]:
    target_id = "work:legal_review_c"
    change = build_change_set(fixture, proposed_value=proposed_value)
    preview = ImpactEngine(fixture).preview(change)
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
    root = sha256_digest(world)
    bundle = {
        "schema_version": checker.SCHEMA,
        "world_digest": root,
        "world": world,
        "certificate": certificate.model_dump(mode="json"),
    }
    _address(bundle)
    return bundle, root


def _new_edge(edge_id: str, source: str, target: str) -> dict[str, Any]:
    edge = {
        "id": edge_id,
        "source_id": source,
        "target_id": target,
        "relation": "ASSUMES",
        "strength": "HARD",
        "coverage_basis": "CONTRACT_DECLARED",
        "status": "ADMITTED",
        "provenance_refs": ["holdout:r012"],
    }
    _address(edge)
    return edge


def _recommit_holdout_graph(bundle: dict[str, Any]) -> None:
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
    adjacency: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in eligible:
        adjacency[edge["source_id"]].append(edge)
    queue = deque([(traversal["source_id"], 0)])
    depth = {traversal["source_id"]: 0}
    used: set[str] = set()
    frontier: set[str] = set()
    while queue:
        node, level = queue.popleft()
        if level >= checker.MAX_DEPTH:
            frontier.update(edge["id"] for edge in adjacency.get(node, []))
            continue
        for edge in adjacency.get(node, []):
            used.add(edge["id"])
            candidate = level + 1
            if candidate < depth.get(edge["target_id"], checker.MAX_DEPTH + 1):
                depth[edge["target_id"]] = candidate
                queue.append((edge["target_id"], candidate))
    used_edges = [edge for edge in eligible if edge["id"] in used]
    traversal.update(
        {
            "dependency_snapshot_digest": sha256_digest(edges),
            "eligible_edge_ids": [edge["id"] for edge in eligible],
            "excluded_edges": excluded,
            "reachable_node_ids": sorted(depth),
            "reachable_edge_ids": [edge["id"] for edge in used_edges],
            "reachable_slice_digest": sha256_digest(used_edges),
            "truncated_frontier_edge_ids": sorted(frontier),
            "budget_exhausted": bool(frontier),
        }
    )
    bundle["world_digest"] = sha256_digest(bundle["world"])
    _address(bundle["certificate"])
    _address(bundle)


def _holdout_code(bundle: dict[str, Any], root: str) -> str:
    with pytest.raises(checker.CheckError) as raised:
        checker.check_bundle_json(canonical_json(bundle).encode(), root)
    return raised.value.code


def test_shortcut_cannot_hide_a_nine_edge_dag_path(
    fixture: EnterpriseFixture,
) -> None:
    bundle, _ = _holdout_bundle(fixture)
    source = bundle["certificate"]["traversal_commitment"]["source_id"]
    nodes = [source, *(f"claim:holdout-chain-{index}" for index in range(1, 10))]
    for index, (left, right) in enumerate(pairwise(nodes), start=1):
        bundle["world"]["dependencies"].append(_new_edge(f"edge:holdout-chain-{index:02d}", left, right))
    bundle["world"]["dependencies"].append(_new_edge("edge:holdout-shortcut", source, nodes[-2]))
    _recommit_holdout_graph(bundle)

    traversal = bundle["certificate"]["traversal_commitment"]
    assert traversal["truncated_frontier_edge_ids"] == []
    assert traversal["budget_exhausted"] is False
    assert _holdout_code(bundle, bundle["world_digest"]) == "TRAVERSAL_BUDGET_EXHAUSTED"


def test_source_reachable_cycle_is_outside_supported_domain(
    fixture: EnterpriseFixture,
) -> None:
    bundle, _ = _holdout_bundle(fixture)
    source = bundle["certificate"]["traversal_commitment"]["source_id"]
    bundle["world"]["dependencies"].extend(
        (
            _new_edge("edge:holdout-cycle-1", source, "claim:holdout-cycle-a"),
            _new_edge("edge:holdout-cycle-2", "claim:holdout-cycle-a", "claim:holdout-cycle-b"),
            _new_edge("edge:holdout-cycle-3", "claim:holdout-cycle-b", "claim:holdout-cycle-a"),
        )
    )
    _recommit_holdout_graph(bundle)

    assert _holdout_code(bundle, bundle["world_digest"]) == "UNSUPPORTED_REACHABLE_GRAPH_CYCLIC"


def test_mutable_target_state_is_still_pinned_by_external_root(
    fixture: EnterpriseFixture,
) -> None:
    bundle, old_root = _holdout_bundle(fixture)
    target = bundle["world"]["target_object"]
    original_object_digest = target["digest"]
    target["state"] = "ACTIVE"
    assert target["digest"] == original_object_digest
    bundle["world_digest"] = sha256_digest(bundle["world"])
    _address(bundle)

    assert _holdout_code(bundle, old_root) == "WORLD_ROOT_MISMATCH"


def test_two_distinct_inbound_edges_cannot_reuse_one_manifest_slot(
    fixture: EnterpriseFixture,
) -> None:
    bundle, _ = _holdout_bundle(fixture)
    manifest = bundle["world"]["target_manifest"]
    target = manifest["target_id"]
    extra = _new_edge("edge:holdout-second-legal-input", "policy:legal.extra", target)
    bundle["world"]["dependencies"].append(extra)
    first_slot = copy.deepcopy(manifest["requirement_slots"][0])
    manifest["requirement_slots"].append(
        {
            **first_slot,
            "edge_id": extra["id"],
            "source_id": extra["source_id"],
            "relation": extra["relation"],
            "strength": extra["strength"],
            "coverage_basis": extra["coverage_basis"],
            "provenance_refs": extra["provenance_refs"],
        }
    )
    _address(manifest)
    _recommit_holdout_graph(bundle)

    assert _holdout_code(bundle, bundle["world_digest"]) == "MANIFEST_SLOT_ID_DUPLICATE"


def test_slot_cannot_omit_one_bound_edge_provenance_ref(
    fixture: EnterpriseFixture,
) -> None:
    bundle, _ = _holdout_bundle(fixture)
    manifest = bundle["world"]["target_manifest"]
    slot = manifest["requirement_slots"][0]
    edge = next(item for item in bundle["world"]["dependencies"] if item["id"] == slot["edge_id"])
    edge["provenance_refs"].append("holdout:second-edge-source")
    _address(edge)
    _recommit_holdout_graph(bundle)

    assert _holdout_code(bundle, bundle["world_digest"]) == "MANIFEST_SLOT_BINDING_INVALID"


def test_cross_run_change_bytes_cannot_be_spliced_under_old_anchor(
    fixture: EnterpriseFixture,
) -> None:
    first, first_root = _holdout_bundle(fixture)
    second, second_root = _holdout_bundle(fixture, proposed_value="2026-10-01")
    assert first_root != second_root
    first["world"]["change_set"] = second["world"]["change_set"]
    first["world_digest"] = sha256_digest(first["world"])
    _address(first)

    assert _holdout_code(first, first_root) == "WORLD_ROOT_MISMATCH"
