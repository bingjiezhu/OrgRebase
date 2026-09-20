"""Language-neutral micro-closure used by the independent CTK Phase A slice.

This is intentionally smaller than the Supplier Profile.  It freezes the
tri-valued route-state transition, authority boundary, simple-path rule, depth
cut, and resource budget without selecting any Agent topology.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Mapping
from typing import Any

from .json_types import JsonValue
from .resource_profile import SUPPLIER_RESOURCE_PROFILE, ResourceProfileExceeded
from .text import has_oac_non_whitespace

_ADMISSIONS = {"admitted", "candidate", "disputed", "retracted"}
_RESULTS = {"TRUE", "FALSE", "UNKNOWN"}


def derive_micro_closure(value: Mapping[str, Any]) -> dict[str, JsonValue]:
    if set(value) != {"root", "maxDepth", "maxPathPrefixes", "nodes", "edges"}:
        raise ValueError("closureMicro fields do not match the frozen input")
    root = _string(value, "root")
    max_depth = _positive_integer(value, "maxDepth")
    max_prefixes = _positive_integer(value, "maxPathPrefixes")
    nodes_raw = _sequence(value, "nodes")
    edges_raw = _sequence(value, "edges")
    if max_depth > SUPPLIER_RESOURCE_PROFILE.max_semantic_depth:
        raise ResourceProfileExceeded(
            "maxDepth", max_depth, SUPPLIER_RESOURCE_PROFILE.max_semantic_depth
        )
    if max_prefixes > SUPPLIER_RESOURCE_PROFILE.max_path_prefixes:
        raise ResourceProfileExceeded(
            "maxPathPrefixes",
            max_prefixes,
            SUPPLIER_RESOURCE_PROFILE.max_path_prefixes,
        )
    if len(nodes_raw) > SUPPLIER_RESOURCE_PROFILE.max_nodes:
        raise ResourceProfileExceeded("nodes", len(nodes_raw), SUPPLIER_RESOURCE_PROFILE.max_nodes)
    if len(edges_raw) > SUPPLIER_RESOURCE_PROFILE.max_edges:
        raise ResourceProfileExceeded("edges", len(edges_raw), SUPPLIER_RESOURCE_PROFILE.max_edges)

    nodes: dict[str, str] = {}
    for item in nodes_raw:
        if not isinstance(item, Mapping):
            raise ValueError("nodes entries must be objects")
        if set(item) != {"id", "admission"}:
            raise ValueError("node fields do not match the frozen input")
        node_id = _string(item, "id")
        admission = _string(item, "admission")
        if admission not in _ADMISSIONS:
            raise ValueError(f"unsupported node admission: {admission}")
        if node_id in nodes:
            raise ValueError(f"duplicate node id: {node_id}")
        nodes[node_id] = admission
    if root not in nodes:
        raise ValueError("root must resolve to a declared node")
    if nodes[root] == "retracted":
        raise ValueError("root must not be retracted")

    edges: list[dict[str, JsonValue]] = []
    edge_ids: set[str] = set()
    for item in edges_raw:
        if not isinstance(item, Mapping):
            raise ValueError("edges entries must be objects")
        if set(item) != {
            "id",
            "source",
            "target",
            "result",
            "admission",
            "covered",
        }:
            raise ValueError("edge fields do not match the frozen input")
        edge_id = _string(item, "id")
        source = _string(item, "source")
        target = _string(item, "target")
        result = _string(item, "result")
        admission = _string(item, "admission")
        covered = item.get("covered")
        if edge_id in edge_ids:
            raise ValueError(f"duplicate edge id: {edge_id}")
        if source not in nodes or target not in nodes:
            raise ValueError(f"edge endpoints must resolve: {edge_id}")
        if result not in _RESULTS:
            raise ValueError(f"unsupported applicability result: {result}")
        if admission not in _ADMISSIONS:
            raise ValueError(f"unsupported edge admission: {admission}")
        if not isinstance(covered, bool):
            raise ValueError("edge covered must be boolean")
        edge_ids.add(edge_id)
        edges.append(
            {
                "id": edge_id,
                "source": source,
                "target": target,
                "result": result,
                "admission": admission,
                "covered": covered,
            }
        )
    edges.sort(key=lambda item: str(item["id"]))
    outgoing: dict[str, list[dict[str, JsonValue]]] = defaultdict(list)
    for edge in edges:
        outgoing[str(edge["source"])].append(edge)

    prefix_count = 1
    if prefix_count > max_prefixes:
        raise ResourceProfileExceeded("pathPrefixes", prefix_count, max_prefixes)
    root_state = "affected" if nodes[root] == "admitted" else "unknown"
    root_reasons = () if root_state == "affected" else ("CANDIDATE_INPUT_NOT_AUTHORITY",)
    root_path: dict[str, JsonValue] = {
        "targetRef": root,
        "state": root_state,
        "edgeRefs": [],
        "reasonCodes": list(root_reasons),
        "truncated": False,
    }
    paths: list[dict[str, JsonValue]] = [root_path]
    false_frontiers: list[dict[str, JsonValue]] = []
    queue: deque[tuple[str, str, tuple[str, ...], tuple[str, ...], tuple[str, ...], dict[str, JsonValue]]] = deque([(root, root_state, (), (root,), root_reasons, root_path)])

    while queue:
        (
            source,
            source_state,
            edge_path,
            node_path,
            inherited_reasons,
            source_path,
        ) = queue.popleft()
        has_boundary_continuation = False
        for edge in outgoing.get(source, []):
            edge_id = str(edge["id"])
            target = str(edge["target"])
            if edge["admission"] == "retracted" or nodes[target] == "retracted":
                continue
            if target in node_path:
                continue
            next_edges = (*edge_path, edge_id)

            endpoints_admitted = nodes[source] == "admitted" and nodes[target] == "admitted"
            authoritative = (
                source_state == "affected"
                and edge["admission"] == "admitted"
                and edge["covered"] is True
                and endpoints_admitted
            )
            if edge["result"] == "FALSE":
                false_frontiers.append(
                    {
                        "edgeId": edge_id,
                        "sourceRef": source,
                        "targetRef": target,
                        "edgeRefs": list(next_edges),
                        "authoritative": authoritative,
                    }
                )
                continue

            if len(edge_path) == max_depth:
                has_boundary_continuation = True
                continue

            reasons = set(inherited_reasons)
            if edge["result"] == "UNKNOWN":
                reasons.add("APPLICABILITY_INPUT_UNKNOWN")
            if edge["admission"] != "admitted":
                reasons.add("CANDIDATE_EDGE_NOT_AUTHORITY")
            if not endpoints_admitted:
                reasons.add("CANDIDATE_INPUT_NOT_AUTHORITY")
            if edge["covered"] is not True:
                reasons.add("GRAPH_COVERAGE_PARTIAL")
            state = "affected" if authoritative and edge["result"] == "TRUE" else "unknown"
            next_nodes = (*node_path, target)

            prefix_count += 1
            if prefix_count > max_prefixes:
                raise ResourceProfileExceeded("pathPrefixes", prefix_count, max_prefixes)
            paths.append(
                {
                    "targetRef": target,
                    "state": state,
                    "edgeRefs": list(next_edges),
                    "reasonCodes": sorted(reasons),
                    "truncated": False,
                }
            )
            next_path = paths[-1]
            queue.append(
                (
                    target,
                    state,
                    next_edges,
                    next_nodes,
                    tuple(sorted(reasons)),
                    next_path,
                )
            )
        if has_boundary_continuation:
            truncated_reasons = set(inherited_reasons)
            truncated_reasons.add("IMPACT_SEARCH_TRUNCATED")
            source_path["state"] = "unknown"
            source_path["reasonCodes"] = sorted(truncated_reasons)
            source_path["truncated"] = True

    paths.sort(
        key=lambda item: (
            tuple(item["edgeRefs"]),  # type: ignore[arg-type]
            str(item["targetRef"]),
            str(item["state"]),
        )
    )
    false_frontiers.sort(
        key=lambda item: (tuple(item["edgeRefs"]), str(item["edgeId"]))  # type: ignore[arg-type]
    )
    unresolved = sorted({str(path["targetRef"]) for path in paths if path["state"] == "unknown"})
    return {
        "kind": "ClosureMicroReport",
        "rootRef": root,
        "paths": paths,
        "falseFrontiers": false_frontiers,
        "unresolvedRefs": unresolved,
    }


def _string(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if (
        not isinstance(item, str)
        or not item
        or len(item) > 4096
        or not has_oac_non_whitespace(item)
    ):
        raise ValueError(f"{key} must be a non-empty string")
    return item


def _positive_integer(value: Mapping[str, Any], key: str) -> int:
    item = value.get(key)
    if isinstance(item, bool) or not isinstance(item, int) or item < 1:
        raise ValueError(f"{key} must be a positive integer")
    return item


def _sequence(value: Mapping[str, Any], key: str) -> list[object]:
    item = value.get(key)
    if not isinstance(item, list):
        raise ValueError(f"{key} must be an array")
    return item
