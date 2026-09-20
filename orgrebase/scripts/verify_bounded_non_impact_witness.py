#!/usr/bin/env python3
# ruff: noqa: SIM905
"""Verify one externally anchored bounded-non-impact world, without OrgRebase."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import stat
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

VERSION = "orgrebase.standalone-dag-witness-checker@1.0.1"
SCHEMA = "orgrebase.bounded-non-impact-world.v1"
VERIFIER_VERSION = "orgrebase.impact-certificate-verifier@1.0.0"
MAX_BYTES, MAX_JSON_DEPTH, MAX_JSON_NODES, MAX_EDGES, MAX_DEPTH = (4_000_000, 32, 500_000, 100_000, 8)
SELECTION_RULE = "strongest_conservative_transfer_then_shortest_then_lexical"
TRUSTED = {"RUNTIME_OBSERVED", "OWNER_DECLARED_COMPLETE", "CONTRACT_DECLARED", "IMPORTED_VERIFIED"}
COVERAGE = TRUSTED | {"AGENT_INFERRED", "UNKNOWN_COVERAGE"}
EDGE_STATUS = {"ADMITTED", "PROPOSED_EDGE", "DISPUTED", "RETRACTED"}
STRENGTH = {"HARD", "REVIEW", "INFORMATIONAL"}
CURRENT_STATE = {"CURRENT", "ACTIVE", "CANARY"}
# fmt: off
ALLOWED_IMPORTS = {"__future__", "argparse", "ast", "collections", "hashlib", "json", "math", "pathlib", "stat", "sys", "typing"}
CHANGE_KEYS = set("digest id revision state owner_id purpose scope deltas".split())
DELTA_KEYS = set("digest object_id base_version proposed_version base_value proposed_value changed_fields semantic_classification admitted_by".split())
TARGET_KEYS = set("digest id version kind label domain state payload source_refs valid_from valid_to sensitivity allowed_purposes coverage_complete coverage_basis".split())
EDGE_KEYS = set("digest id source_id target_id relation strength coverage_basis status provenance_refs".split())
MANIFEST_KEYS = set("digest id version target_id target_version issuer_id authority_domain completeness requirement_slots provenance_refs".split())
SLOT_KEYS = set("slot_id edge_id source_id relation strength coverage_basis provenance_refs".split())
CERTIFICATE_KEYS = set("digest id schema_version certificate_type subject_id classification reason_code change_set_digest result_digest revision_lock_digest traversal_commitment claim_boundary verifier_version evidence_class".split())
TRAVERSAL_KEYS = set("source_id target_id max_depth selection_rule dependency_snapshot_digest eligible_edge_ids excluded_edges reachable_node_ids reachable_edge_ids reachable_slice_digest truncated_frontier_edge_ids budget_exhausted selected_path_edge_ids selected_path_classification target_coverage target_coverage_digest".split())
CLAIM = "No admitted trusted path exists in the committed dependency snapshot and the target declares complete coverage; this is not a global non-impact claim."
NOT_CHECKED = ("CHECKER_SOURCE_IDENTITY_AND_RUNTIME_IMPORT_CLOSURE", "EXPECTED_WORLD_ROOT_ORIGIN_AND_AUTHORITY", "RUN_ID_NONCE_FRESHNESS_OR_REPLAY", "OBJECT_TEMPORAL_VALIDITY", "REAL_WORLD_DEPENDENCY_GRAPH_COMPLETENESS", "MANIFEST_COMPLETENESS_TRUTH", "RUNTIME_OBSERVED_PROVENANCE_AUTHENTICITY", "OUT_OF_WORLD_DEPENDENCIES_OR_CAUSALITY", "GLOBAL_NON_IMPACT", "ENTERPRISE_PRODUCTION_CORRECTNESS")
# fmt: on


class CheckError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def need(condition: bool, code: str) -> None:
    if not condition:
        raise CheckError(code)


def mapping(value: Any, code: str) -> dict[str, Any]:
    need(type(value) is dict, code)
    return value


def sequence(value: Any, code: str) -> list[Any]:
    need(type(value) is list, code)
    return value


def text(value: Any, code: str) -> str:
    need(type(value) is str and bool(value), code)
    return value


def digest(value: Any, code: str) -> str:
    item = text(value, code)
    need(len(item) == 71 and item.startswith("sha256:"), code)
    try:
        int(item[7:], 16)
    except ValueError as exc:
        raise CheckError(code) from exc
    return item


def exact(value: dict[str, Any], keys: set[str], code: str) -> None:
    need(set(value) == keys, code)


def canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise CheckError("NON_CANONICAL_JSON") from exc


def sha(value: Any) -> str:
    return f"sha256:{hashlib.sha256(canonical(value)).hexdigest()}"


def addressed(value: dict[str, Any], code: str, *, omit: set[str] | None = None) -> str:
    declared = digest(value.get("digest"), code)
    ignored = {"digest"} | (omit or set())
    need(sha({key: item for key, item in value.items() if key not in ignored}) == declared, code)
    return declared


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        need(key not in result, "JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _constant(_value: str) -> Any:
    raise CheckError("JSON_NON_FINITE_NUMBER")


def _nodes(value: Any, depth: int = 0) -> int:
    need(depth <= MAX_JSON_DEPTH, "JSON_DEPTH_LIMIT_EXCEEDED")
    if value is None or type(value) in {bool, int, str}:
        return 1
    if type(value) is float:
        need(math.isfinite(value), "JSON_NON_FINITE_NUMBER")
        return 1
    values = value if type(value) is list else value.values() if type(value) is dict else None
    need(values is not None, "JSON_TYPE_INVALID")
    total = 1
    for item in values:
        total += _nodes(item, depth + 1)
        need(total <= MAX_JSON_NODES, "JSON_NODE_LIMIT_EXCEEDED")
    return total


def parse(raw: bytes) -> dict[str, Any]:
    need(type(raw) is bytes, "JSON_BYTES_REQUIRED")
    need(len(raw) <= MAX_BYTES, "JSON_BYTE_LIMIT_EXCEEDED")
    try:
        value = json.loads(raw.decode(), object_pairs_hook=_pairs, parse_constant=_constant)
    except CheckError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise CheckError("JSON_PARSE_INVALID") from exc
    _nodes(value)
    return mapping(value, "BUNDLE_ROOT_INVALID")


def read_bundle_file(path: Path) -> bytes:
    metadata = path.stat()
    need(stat.S_ISREG(metadata.st_mode), "BUNDLE_FILE_NOT_REGULAR")
    need(metadata.st_size <= MAX_BYTES, "JSON_BYTE_LIMIT_EXCEEDED")
    with path.open("rb") as handle:
        raw = handle.read(MAX_BYTES + 1)
    need(len(raw) <= MAX_BYTES, "JSON_BYTE_LIMIT_EXCEEDED")
    return raw


def strings(value: Any, code: str, *, unique: bool = False) -> list[str]:
    items = sequence(value, code)
    need(all(type(item) is str and item for item in items), code)
    need(not unique or len(set(items)) == len(items), code)
    return items


def validate_change(change: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    exact(change, CHANGE_KEYS, "CHANGE_SHAPE_INVALID")
    addressed(change, "CHANGE_DIGEST_INVALID")
    need(change["state"] == "READY_FOR_PREVIEW", "CHANGE_STATE_INVALID")
    for key in ("id", "revision", "owner_id", "purpose"):
        text(change[key], "CHANGE_FIELD_INVALID")
    scope = strings(change["scope"], "CHANGE_SCOPE_INVALID", unique=True)
    deltas = sequence(change["deltas"], "CHANGE_DELTAS_INVALID")
    need(len(deltas) == 1, "EXACTLY_ONE_DELTA_REQUIRED")
    delta = mapping(deltas[0], "DELTA_INVALID")
    exact(delta, DELTA_KEYS, "DELTA_SHAPE_INVALID")
    addressed(delta, "DELTA_DIGEST_INVALID")
    need(delta["semantic_classification"] == "SEMANTIC_DELTA", "SEMANTIC_DELTA_REQUIRED")
    for key in ("object_id", "base_version", "proposed_version"):
        text(delta[key], "DELTA_FIELD_INVALID")
    text(delta["admitted_by"], "DELTA_ADMISSION_REQUIRED")
    need(bool(strings(delta["changed_fields"], "DELTA_CHANGED_FIELDS_INVALID", unique=True)), "DELTA_CHANGED_FIELDS_INVALID")
    need(sha(delta["base_value"]) != sha(delta["proposed_value"]), "SEMANTIC_DELTA_VALUE_MISMATCH")
    return delta, scope


def validate_target(target: dict[str, Any]) -> None:
    exact(target, TARGET_KEYS, "TARGET_SHAPE_INVALID")
    addressed(target, "TARGET_DIGEST_INVALID", omit={"state"})
    for key in ("id", "version", "kind", "label", "domain", "state", "valid_from", "sensitivity"):
        text(target[key], "TARGET_FIELD_INVALID")
    need(target["valid_to"] is None or type(target["valid_to"]) is str, "TARGET_FIELD_INVALID")
    need(text(target["state"], "TARGET_CURRENT_STATE_REQUIRED") in CURRENT_STATE, "TARGET_CURRENT_STATE_REQUIRED")
    mapping(target["payload"], "TARGET_PAYLOAD_INVALID")
    strings(target["source_refs"], "TARGET_SOURCE_REFS_INVALID")
    strings(target["allowed_purposes"], "TARGET_PURPOSES_INVALID")
    need(target["coverage_complete"] is True, "TARGET_COVERAGE_DECLARATION_REQUIRED")
    basis = strings(target["coverage_basis"], "TARGET_COVERAGE_INVALID")
    need(bool(basis) and all(item in TRUSTED for item in basis), "TARGET_COVERAGE_INVALID")


def validate_edges(raw_edges: Any) -> list[dict[str, Any]]:
    edges = [mapping(item, "EDGE_INVALID") for item in sequence(raw_edges, "EDGES_INVALID")]
    need(len(edges) <= MAX_EDGES, "EDGE_LIMIT_EXCEEDED")
    ids: set[str] = set()
    for edge in edges:
        exact(edge, EDGE_KEYS, "EDGE_SHAPE_INVALID")
        addressed(edge, "EDGE_DIGEST_INVALID")
        edge_id = text(edge["id"], "EDGE_ID_INVALID")
        need(edge_id not in ids, "EDGE_ID_DUPLICATE")
        ids.add(edge_id)
        for key in ("source_id", "target_id", "relation"):
            text(edge[key], "EDGE_FIELD_INVALID")
        need(text(edge["strength"], "EDGE_STRENGTH_INVALID") in STRENGTH, "EDGE_STRENGTH_INVALID")
        need(text(edge["coverage_basis"], "EDGE_COVERAGE_INVALID") in COVERAGE, "EDGE_COVERAGE_INVALID")
        need(text(edge["status"], "EDGE_STATUS_INVALID") in EDGE_STATUS, "EDGE_STATUS_INVALID")
        refs = strings(edge["provenance_refs"], "EDGE_PROVENANCE_INVALID")
        need(edge["status"] != "ADMITTED" or edge["coverage_basis"] not in TRUSTED or bool(refs), "TRUSTED_EDGE_PROVENANCE_MISSING")
    need(edges == sorted(edges, key=lambda item: item["id"]), "EDGE_ORDER_NON_CANONICAL")
    return edges


def partition(edges: list[dict[str, Any]], traversal: dict[str, Any]) -> list[dict[str, Any]]:
    eligible, excluded = [], []
    for edge in edges:
        if edge["status"] != "ADMITTED":
            excluded.append({"edge_id": edge["id"], "reason": f"STATUS_{edge['status']}"})
        elif edge["coverage_basis"] not in TRUSTED:
            excluded.append({"edge_id": edge["id"], "reason": f"UNTRUSTED_COVERAGE_{edge['coverage_basis']}"})
        else:
            eligible.append(edge)
    need(
        strings(traversal["eligible_edge_ids"], "ELIGIBLE_IDS_INVALID", unique=True) == [edge["id"] for edge in eligible],
        "EDGE_PARTITION_INVALID",
    )
    need(traversal["excluded_edges"] == excluded, "EDGE_PARTITION_INVALID")
    return eligible


def reachable(eligible: list[dict[str, Any]], source: str) -> tuple[list[str], list[dict[str, Any]], list[str]]:
    adjacency: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in eligible:
        adjacency[edge["source_id"]].append(edge)
    queue, depths, used, frontier = deque([(source, 0)]), {source: 0}, set(), set()
    while queue:
        node, depth = queue.popleft()
        if depth >= MAX_DEPTH:
            frontier.update(edge["id"] for edge in adjacency.get(node, []))
            continue
        for edge in adjacency.get(node, []):
            used.add(edge["id"])
            candidate = depth + 1
            if candidate < depths.get(edge["target_id"], MAX_DEPTH + 1):
                depths[edge["target_id"]] = candidate
                queue.append((edge["target_id"], candidate))
    return sorted(depths), [edge for edge in eligible if edge["id"] in used], sorted(frontier)


def dag_depth(eligible: list[dict[str, Any]], source: str) -> tuple[int, int]:
    adjacency: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in eligible:
        adjacency[edge["source_id"]].append(edge)
    nodes, discover = {source}, deque([source])
    while discover:
        for edge in adjacency.get(discover.popleft(), []):
            if edge["target_id"] not in nodes:
                nodes.add(edge["target_id"])
                discover.append(edge["target_id"])
    edges = [edge for edge in eligible if edge["source_id"] in nodes]
    indegree = {node: 0 for node in nodes}
    for edge in edges:
        indegree[edge["target_id"]] += 1
    ready = deque(node for node, degree in indegree.items() if degree == 0)
    longest, visited, maximum = {node: 0 for node in ready}, 0, 0
    while ready:
        node = ready.popleft()
        visited += 1
        maximum = max(maximum, longest.get(node, 0))
        for edge in adjacency.get(node, []):
            target = edge["target_id"]
            longest[target] = max(longest.get(target, 0), longest.get(node, 0) + 1)
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    need(visited == len(nodes), "UNSUPPORTED_REACHABLE_GRAPH_CYCLIC")
    need(maximum <= MAX_DEPTH, "TRAVERSAL_BUDGET_EXHAUSTED")
    return len(nodes), maximum


def validate_manifest(manifest: dict[str, Any], target: dict[str, Any], eligible: list[dict[str, Any]]) -> None:
    exact(manifest, MANIFEST_KEYS, "MANIFEST_SHAPE_INVALID")
    addressed(manifest, "MANIFEST_DIGEST_INVALID")
    for key in ("id", "version", "target_id", "target_version", "issuer_id", "authority_domain"):
        text(manifest[key], "MANIFEST_FIELD_INVALID")
    need(
        manifest["target_id"] == target["id"] and manifest["target_version"] == target["version"],
        "MANIFEST_TARGET_INVALID",
    )
    need(manifest["authority_domain"] == target["domain"], "MANIFEST_DOMAIN_INVALID")
    need(manifest["completeness"] == "COMPLETE", "MANIFEST_NOT_COMPLETE")
    need(
        bool(strings(manifest["provenance_refs"], "MANIFEST_PROVENANCE_INVALID")),
        "MANIFEST_PROVENANCE_INVALID",
    )
    inbound = {edge["id"]: edge for edge in eligible if edge["target_id"] == target["id"]}
    slots = [mapping(item, "MANIFEST_SLOT_INVALID") for item in sequence(manifest["requirement_slots"], "MANIFEST_SLOTS_INVALID")]
    slot_ids, edge_ids = set(), set()
    for slot in slots:
        exact(slot, SLOT_KEYS, "MANIFEST_SLOT_SHAPE_INVALID")
        slot_id, edge_id = (
            text(slot["slot_id"], "MANIFEST_SLOT_ID_INVALID"),
            text(slot["edge_id"], "MANIFEST_EDGE_ID_INVALID"),
        )
        slot_refs = strings(slot["provenance_refs"], "MANIFEST_SLOT_PROVENANCE_INVALID")
        need(slot_id not in slot_ids, "MANIFEST_SLOT_ID_DUPLICATE")
        need(edge_id not in edge_ids, "MANIFEST_EDGE_ID_DUPLICATE")
        slot_ids.add(slot_id)
        edge_ids.add(edge_id)
        need(edge_id in inbound, "MANIFEST_EDGE_BIJECTION_INVALID")
        edge = inbound[edge_id]
        need(
            all(slot[key] == edge[key] for key in ("source_id", "relation", "strength", "coverage_basis")) and set(edge["provenance_refs"]) <= set(slot_refs),
            "MANIFEST_SLOT_BINDING_INVALID",
        )
        need(
            bool(slot_refs),
            "MANIFEST_SLOT_PROVENANCE_INVALID",
        )
    need(edge_ids == set(inbound), "MANIFEST_EDGE_BIJECTION_INVALID")


# fmt: off
def check_bundle_json(raw: bytes, expected_world_root: str) -> dict[str, Any]:
    bundle = parse(raw)
    exact(bundle, {"digest", "schema_version", "world_digest", "world", "certificate"}, "BUNDLE_SHAPE_INVALID")
    need(bundle["schema_version"] == SCHEMA, "BUNDLE_SCHEMA_INVALID")
    addressed(bundle, "BUNDLE_DIGEST_INVALID")
    world = mapping(bundle["world"], "WORLD_INVALID")
    exact(world, {"change_set", "target_object", "dependencies", "target_manifest", "revisions"}, "WORLD_SHAPE_INVALID")
    world_root = sha(world)
    need(bundle["world_digest"] == world_root, "WORLD_DIGEST_INVALID")
    need(digest(expected_world_root, "EXPECTED_WORLD_ROOT_INVALID") == world_root, "WORLD_ROOT_MISMATCH")

    change = mapping(world["change_set"], "CHANGE_INVALID")
    delta, scope = validate_change(change)
    target = mapping(world["target_object"], "TARGET_INVALID")
    validate_target(target)
    edges = validate_edges(world["dependencies"])
    revisions = mapping(world["revisions"], "REVISIONS_INVALID")
    exact(revisions, {"graph", "policy", "skill_registry", "runtime_registry", "evaluation_suite"}, "REVISIONS_SHAPE_INVALID")
    for value in revisions.values():
        text(value, "REVISION_VALUE_INVALID")

    certificate = mapping(bundle["certificate"], "CERTIFICATE_INVALID")
    exact(certificate, CERTIFICATE_KEYS, "CERTIFICATE_SHAPE_INVALID")
    certificate_digest = addressed(certificate, "CERTIFICATE_DIGEST_INVALID")
    need(certificate["schema_version"] == "orgrebase.impact-certificate.v1", "CERTIFICATE_SCHEMA_INVALID")
    expected_claim = ("BOUNDED_NON_IMPACT", "UNAFFECTED_WITHIN_DECLARED_BOUNDARY", "COMPLETE_COVERAGE_NO_ADMITTED_PATH")
    need((certificate["certificate_type"], certificate["classification"], certificate["reason_code"]) == expected_claim, "CERTIFICATE_CLAIM_INVALID")
    need(certificate["claim_boundary"] == CLAIM and certificate["verifier_version"] == VERIFIER_VERSION and certificate["evidence_class"] == "LOCAL_DETERMINISTIC", "CERTIFICATE_PROTOCOL_INVALID")
    target_id = target["id"]
    need(certificate["subject_id"] == target_id and target_id in scope, "CERTIFICATE_TARGET_INVALID")
    need(certificate["id"] == f"impact-certificate:{target_id}@{change['revision']}", "CERTIFICATE_ID_INVALID")
    need(certificate["change_set_digest"] == change["digest"], "CERTIFICATE_CHANGE_BINDING_INVALID")
    lock = sha({
        "change_set_revision": f"{change['id']}@{change['revision']}", "change_set_digest": change["digest"],
        "graph_revision": revisions["graph"], "policy_revision": revisions["policy"],
        "skill_registry_revision": revisions["skill_registry"], "runtime_registry_revision": revisions["runtime_registry"],
        "evaluation_scope_digest": sha({"targets": sorted(scope), "suite": revisions["evaluation_suite"]}),
    })
    need(certificate["revision_lock_digest"] == lock, "CERTIFICATE_REVISION_BINDING_INVALID")

    traversal = mapping(certificate["traversal_commitment"], "TRAVERSAL_INVALID")
    exact(traversal, TRAVERSAL_KEYS, "TRAVERSAL_SHAPE_INVALID")
    need(traversal["source_id"] == delta["object_id"] and traversal["target_id"] == target_id, "TRAVERSAL_ENDPOINT_INVALID")
    need(type(traversal["max_depth"]) is int and traversal["max_depth"] == MAX_DEPTH and traversal["selection_rule"] == SELECTION_RULE, "TRAVERSAL_POLICY_INVALID")
    need(traversal["dependency_snapshot_digest"] == sha(edges), "TRAVERSAL_SNAPSHOT_INVALID")
    eligible = partition(edges, traversal)
    nodes, used_edges, frontier = reachable(eligible, traversal["source_id"])
    need(strings(traversal["reachable_node_ids"], "REACHABLE_NODES_INVALID", unique=True) == nodes, "REACHABLE_NODES_MISMATCH")
    need(strings(traversal["reachable_edge_ids"], "REACHABLE_EDGES_INVALID", unique=True) == [edge["id"] for edge in used_edges], "REACHABLE_EDGES_MISMATCH")
    need(traversal["reachable_slice_digest"] == sha(used_edges), "REACHABLE_SLICE_INVALID")
    need(strings(traversal["truncated_frontier_edge_ids"], "FRONTIER_INVALID", unique=True) == frontier and not frontier and traversal["budget_exhausted"] is False, "TRAVERSAL_BUDGET_EXHAUSTED")
    need(target_id not in nodes and traversal["selected_path_edge_ids"] == [] and traversal["selected_path_classification"] is None, "TARGET_REACHABLE")
    graph_nodes, longest_depth = dag_depth(eligible, traversal["source_id"])

    manifest = mapping(world["target_manifest"], "MANIFEST_INVALID")
    validate_manifest(manifest, target, eligible)
    slots = [slot["slot_id"] for slot in manifest["requirement_slots"]]
    coverage = {"target_ref": f"{target_id}@{target['version']}", "manifest_ref": f"{manifest['id']}@{manifest['version']}", "manifest_digest": manifest["digest"], "manifest_completeness": "COMPLETE", "requirement_slot_ids": slots, "assessment_failures": []}
    need(traversal["target_coverage"] == coverage and traversal["target_coverage_digest"] == sha(coverage), "TARGET_COVERAGE_INVALID")
    result = {
        "object_id": target_id, "label": target["label"], "classification": "UNAFFECTED_WITHIN_DECLARED_BOUNDARY",
        "reason_code": "COMPLETE_COVERAGE_NO_ADMITTED_PATH", "proof_path": [],
        "boundary": {"dependency_manifest": f"{manifest['id']}@{manifest['version']}", "dependency_manifest_digest": manifest["digest"], "requirement_slots": slots, "graph_revision": revisions["graph"], "evaluated_change": delta["digest"], "scope": [target_id]},
        "missing_evidence": [],
    }
    need(certificate["result_digest"] == sha(result), "CERTIFICATE_RESULT_BINDING_INVALID")
    return {
        "status": "PASS", "checker_version": VERSION, "certificate_id": certificate["id"],
        "certificate_digest": certificate_digest, "subject_id": target_id, "world_root_digest": world_root,
        "support_domain": "DAG_ONLY_SOURCE_REACHABLE_GRAPH_MAX_DEPTH_8",
        "checked": ("externally_anchored_canonical_world", "content_addresses", "eligible_excluded_edge_partition", "dag_bounded_non_reachability", "declared_complete_manifest_edge_bijection", "bounded_result_and_certificate_binding"),
        "not_independently_checked": NOT_CHECKED,
        "graph_facts": {"source_reachable_nodes": graph_nodes, "longest_path_depth": longest_depth},
        "claim_boundary": "PASS proves structural bounded non-reachability only inside the caller-anchored canonical JSON world and DAG support domain; it does not bind whitespace or JSON member order and does not prove anchor authority, real-world completeness, global non-impact, or production correctness.",
    }
# fmt: on


def self_audit() -> dict[str, Any]:
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = sorted({node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module} | {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names})
    loaded = sorted(name for name in sys.modules if name == "orgrebase" or name.startswith("orgrebase."))
    need(not any(name.startswith("orgrebase") for name in imports), "SELF_AUDIT_PRODUCT_IMPORT")
    need(set(imports) <= ALLOWED_IMPORTS, "SELF_AUDIT_NON_STDLIB_IMPORT")
    need(not loaded, "SELF_AUDIT_RUNTIME_PRODUCT_MODULE_LOADED")
    return {
        "status": "PASS",
        "imports": imports,
        "loaded_orgrebase_modules": loaded,
        "source_sha256": f"sha256:{hashlib.sha256(source.encode()).hexdigest()}",
    }


def main() -> int:
    parser = argparse.ArgumentParser(exit_on_error=False)
    parser.add_argument("--self-audit", action="store_true")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--expected-world-root")
    try:
        args, unknown = parser.parse_known_args()
        need(not unknown, "CLI_ARGUMENTS_INVALID")
        if args.self_audit:
            need(args.bundle is None and args.expected_world_root is None, "CLI_ARGUMENTS_INVALID")
            receipt = self_audit()
        else:
            need(args.bundle is not None and args.expected_world_root is not None, "CLI_ARGUMENTS_REQUIRED")
            receipt = check_bundle_json(read_bundle_file(args.bundle), args.expected_world_root)
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
        return 0
    except (argparse.ArgumentError, CheckError, OSError) as exc:
        code = exc.code if isinstance(exc, CheckError) else "BUNDLE_READ_FAILED" if isinstance(exc, OSError) else "CLI_ARGUMENTS_INVALID"
        print(json.dumps({"status": "FAIL", "code": code}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
