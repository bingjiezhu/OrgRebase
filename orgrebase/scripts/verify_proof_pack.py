"""Closed-world ProofPack builder and independent structural verifier.

This script may import ``orgrebase.digest`` so hashes match the project
canonical JSON algorithm. It must not import impact, workflow, store,
service, certificates, collaboration, or fixture loaders: those re-run the
engine this pack is supposed to check from the outside.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any

from orgrebase.digest import sha256_digest

ROOT = Path(__file__).resolve().parents[1]
FROZEN_LIVE_PLAN_DIGEST = (
    "sha256:2869a6106aa4b032b4c12370f6e437b3561fc6f4d05c1cd0145bc3bf5738f85d"
)
SCHEMA_VERSION = "orgrebase.proof-pack.v1"
VERIFIER_VERSION = "orgrebase.proof-pack-verifier@1.0.0"
FORBIDDEN_IMPORT_ROOTS = (
    "orgrebase.impact",
    "orgrebase.workflow",
    "orgrebase.store",
    "orgrebase.service",
    "orgrebase.certificates",
    "orgrebase.collaboration",
    "orgrebase.fixture",
)
TRUSTED_COVERAGE = {
    "RUNTIME_OBSERVED",
    "OWNER_DECLARED_COMPLETE",
    "CONTRACT_DECLARED",
    "IMPORTED_VERIFIED",
}
DISPOSITION_TABLE = {
    "AFFECTED_HARD": "REBUILD",
    "AFFECTED_REVIEW": "HOLD_FOR_REVIEW",
    "AFFECTED_INFORMATIONAL": "HOLD_FOR_REVIEW",
    "UNKNOWN": "HOLD_FOR_REVIEW",
    "UNAFFECTED_WITHIN_DECLARED_BOUNDARY": "PRESERVE_WITHIN_BOUNDARY",
    "REQUALIFICATION_REQUIRED": "REQUALIFY",
}
CERTIFICATE_TYPE_BY_CLASSIFICATION = {
    "UNAFFECTED_WITHIN_DECLARED_BOUNDARY": "BOUNDED_NON_IMPACT",
    "UNKNOWN": "UNCERTAINTY_WITNESS",
}
REQUIRED_NOT_INDEPENDENT = {
    "PATH_CLASSIFICATION",
    "COVERAGE_ORACLE",
    "LIVE_CANDIDATE_AUTHENTICITY",
    "IDENTITY_TO_PLAN_SYNTHESIS",
}
PLAN_INVARIANTS = (
    "exactly_one_task_per_declared_agent",
    "acyclic_declared_dependencies",
    "capabilities_and_outputs_are_identity_subsets",
    "all_tasks_bind_exact_changeset_preview_and_revision_lock",
    "candidate_only_no_normative_state_writer",
)
INDEPENDENT_CHECKS = (
    "content_address",
    "disposition_table_frozen",
    "vmrc_effect_set_binding",
    "plan_coordination_ingestion_chain",
    "ingestion_zero_writes",
    "identity_and_intents_lock",
    "unaffected_manifest_edge_bijection",
    "receipt_digest_bindings",
    "frozen_live_plan_digest",
)


class ProofPackError(RuntimeError):
    pass


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ProofPackError(code)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProofPackError(f"PROOFPACK_JSON_INVALID:{path.name}") from exc


def _mapping(value: Any, code: str) -> dict[str, Any]:
    _require(isinstance(value, dict), code)
    return value


def _items(value: Any, code: str) -> list[Any]:
    _require(isinstance(value, list), code)
    return value


def _self_digest(value: dict[str, Any]) -> str:
    payload = {key: item for key, item in value.items() if key != "digest"}
    return sha256_digest(payload)


def _assert_self_digest(value: dict[str, Any], code: str) -> None:
    digest = value.get("digest")
    _require(isinstance(digest, str) and digest.startswith("sha256:"), code)
    _require(_self_digest(value) == digest, code)


def _reference_digest_shape(value: dict[str, Any]) -> bool:
    return set(value) <= {"name", "digest"} and "digest" in value and "name" in value


def _walk_self_digests(value: Any, *, path: str) -> None:
    if isinstance(value, dict):
        if "digest" in value and not _reference_digest_shape(value):
            _assert_self_digest(value, f"PROOFPACK_DIGEST_MISMATCH:{path}")
        for key, item in value.items():
            if key == "digest":
                continue
            _walk_self_digests(item, path=f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _walk_self_digests(item, path=f"{path}[{index}]")


def _assert_source_independence() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = [
        name
        for name in imported
        if name in FORBIDDEN_IMPORT_ROOTS
        or any(name.startswith(f"{root}.") for root in FORBIDDEN_IMPORT_ROOTS)
    ]
    _require(not forbidden, f"PROOFPACK_FORBIDDEN_IMPORT:{','.join(sorted(forbidden))}")


def _current_object(objects: list[dict[str, Any]], object_id: str) -> dict[str, Any]:
    matches = [
        item for item in objects if isinstance(item, dict) and item.get("id") == object_id
    ]
    current = [
        item for item in matches if item.get("state") in {"CURRENT", "ACTIVE"}
    ]
    chosen = current or matches
    _require(len(chosen) == 1, f"PROOFPACK_OBJECT_AMBIGUOUS:{object_id}")
    return chosen[0]


def _identity_index(identities: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for item in identities:
        mapping = _mapping(item, "PROOFPACK_IDENTITY_INVALID")
        name = mapping.get("name")
        _require(isinstance(name, str) and name, "PROOFPACK_IDENTITY_INVALID")
        _require(name not in indexed, f"PROOFPACK_IDENTITY_DUPLICATE:{name}")
        indexed[name] = mapping
    return indexed


def _load_disk_identities(root: Path) -> dict[str, dict[str, Any]]:
    directory = root / "agentteams" / "identities"
    _require(directory.is_dir(), "PROOFPACK_IDENTITY_DIR_MISSING")
    indexed: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json")):
        payload = _mapping(_read_json(path), f"PROOFPACK_IDENTITY_FILE_INVALID:{path.name}")
        name = payload.get("name")
        _require(name == path.stem, f"PROOFPACK_IDENTITY_NAME_MISMATCH:{path.name}")
        indexed[str(name)] = payload
    _require(indexed, "PROOFPACK_IDENTITY_DIR_EMPTY")
    return indexed


def _unaffected_bijection(world: dict[str, Any]) -> None:
    preview = _mapping(world.get("preview"), "PROOFPACK_PREVIEW_MISSING")
    slice_ = _mapping(world.get("fixture_slice"), "PROOFPACK_FIXTURE_SLICE_MISSING")
    objects = [
        _mapping(item, "PROOFPACK_FIXTURE_OBJECT_INVALID")
        for item in _items(slice_.get("objects"), "PROOFPACK_FIXTURE_OBJECTS_MISSING")
    ]
    edges = [
        _mapping(item, "PROOFPACK_FIXTURE_EDGE_INVALID")
        for item in _items(slice_.get("dependencies"), "PROOFPACK_FIXTURE_EDGES_MISSING")
    ]
    manifests = [
        _mapping(item, "PROOFPACK_FIXTURE_MANIFEST_INVALID")
        for item in _items(
            slice_.get("dependency_manifests"), "PROOFPACK_FIXTURE_MANIFESTS_MISSING"
        )
    ]
    eligible = [
        edge
        for edge in edges
        if edge.get("status") == "ADMITTED" and edge.get("coverage_basis") in TRUSTED_COVERAGE
    ]
    for result in _items(preview.get("results"), "PROOFPACK_PREVIEW_RESULTS_MISSING"):
        mapping = _mapping(result, "PROOFPACK_PREVIEW_RESULT_INVALID")
        if mapping.get("classification") != "UNAFFECTED_WITHIN_DECLARED_BOUNDARY":
            continue
        target_id = mapping.get("object_id")
        _require(isinstance(target_id, str) and target_id, "PROOFPACK_UNAFFECTED_TARGET_MISSING")
        target = _current_object(objects, target_id)
        matches = [
            item
            for item in manifests
            if item.get("target_id") == target_id
            and item.get("target_version") == target.get("version")
        ]
        _require(len(matches) == 1, f"PROOFPACK_MANIFEST_MISSING:{target_id}")
        manifest = matches[0]
        reasons: set[str] = set()
        if manifest.get("completeness") != "COMPLETE":
            reasons.add("MANIFEST_NOT_COMPLETE")
        if manifest.get("authority_domain") != target.get("domain"):
            reasons.add("MANIFEST_AUTHORITY_DOMAIN_MISMATCH")
        if not manifest.get("provenance_refs"):
            reasons.add("MANIFEST_PROVENANCE_MISSING")
        inbound = {
            edge["id"]: edge
            for edge in eligible
            if edge.get("target_id") == target_id and isinstance(edge.get("id"), str)
        }
        slots = [
            _mapping(item, f"PROOFPACK_SLOT_INVALID:{target_id}")
            for item in _items(
                manifest.get("requirement_slots"), f"PROOFPACK_SLOTS_MISSING:{target_id}"
            )
        ]
        declared_ids = [slot.get("edge_id") for slot in slots]
        if len(set(declared_ids)) != len(declared_ids):
            reasons.add("MANIFEST_DUPLICATE_REQUIREMENT_EDGE")
        if set(declared_ids) != set(inbound):
            reasons.add("MANIFEST_REQUIREMENT_SET_MISMATCH")
        for slot in slots:
            edge = inbound.get(str(slot.get("edge_id")))
            if edge is None:
                continue
            if (
                slot.get("source_id") != edge.get("source_id")
                or slot.get("relation") != edge.get("relation")
                or slot.get("strength") != edge.get("strength")
                or slot.get("coverage_basis") != edge.get("coverage_basis")
            ):
                reasons.add("MANIFEST_REQUIREMENT_BINDING_MISMATCH")
            if not slot.get("provenance_refs"):
                reasons.add("MANIFEST_REQUIREMENT_PROVENANCE_MISSING")
        _require(not reasons, f"PROOFPACK_UNAFFECTED_BIJECTION:{target_id}:{','.join(sorted(reasons))}")


def _verify_vmrc(world: dict[str, Any]) -> None:
    preview = _mapping(world.get("preview"), "PROOFPACK_PREVIEW_MISSING")
    vmrc = _mapping(
        world.get("minimal_rebase_certificate"), "PROOFPACK_VMRC_MISSING"
    )
    change_set = _mapping(world.get("change_set"), "PROOFPACK_CHANGE_SET_MISSING")
    results = [
        _mapping(item, "PROOFPACK_PREVIEW_RESULT_INVALID")
        for item in _items(preview.get("results"), "PROOFPACK_PREVIEW_RESULTS_MISSING")
    ]
    certificates = [
        _mapping(item, "PROOFPACK_CERTIFICATE_INVALID")
        for item in _items(preview.get("certificates"), "PROOFPACK_CERTIFICATES_MISSING")
    ]
    effects = [
        _mapping(item, "PROOFPACK_EFFECT_INVALID")
        for item in _items(vmrc.get("effects"), "PROOFPACK_EFFECTS_MISSING")
    ]
    result_ids = [item.get("object_id") for item in results]
    cert_ids = [item.get("subject_id") for item in certificates]
    effect_ids = [item.get("target_id") for item in effects]
    _require(result_ids == effect_ids, "PROOFPACK_EFFECT_TARGET_SET_MISMATCH")
    _require(set(result_ids) == set(cert_ids), "PROOFPACK_CERTIFICATE_TARGET_SET_MISMATCH")
    _require(
        set(change_set.get("scope") or ()) == set(result_ids),
        "PROOFPACK_SCOPE_TARGET_SET_MISMATCH",
    )
    _require(
        all(item.get("classification") != "OUT_OF_SCOPE" for item in results),
        "PROOFPACK_OUT_OF_SCOPE_IN_RESULTS",
    )
    cert_by_subject = {item["subject_id"]: item for item in certificates}
    result_by_id = {item["object_id"]: item for item in results}
    for effect in effects:
        target_id = effect.get("target_id")
        result = result_by_id[target_id]
        certificate = cert_by_subject[target_id]
        classification = result.get("classification")
        _require(classification != "OUT_OF_SCOPE", "PROOFPACK_OUT_OF_SCOPE_CANNOT_AUTHORIZE")
        expected = DISPOSITION_TABLE.get(str(classification))
        _require(expected is not None, f"PROOFPACK_UNHANDLED_CLASSIFICATION:{classification}")
        _require(effect.get("disposition") == expected, f"PROOFPACK_DISPOSITION_MISMATCH:{target_id}")
        _require(
            effect.get("impact_result_digest") == result.get("digest"),
            f"PROOFPACK_EFFECT_RESULT_DIGEST:{target_id}",
        )
        _require(
            effect.get("impact_certificate_digest") == certificate.get("digest"),
            f"PROOFPACK_EFFECT_CERTIFICATE_DIGEST:{target_id}",
        )
        _require(
            certificate.get("result_digest") == result.get("digest"),
            f"PROOFPACK_CERTIFICATE_RESULT_DIGEST:{target_id}",
        )
        _require(
            certificate.get("classification") == classification,
            f"PROOFPACK_CERTIFICATE_CLASSIFICATION:{target_id}",
        )
        expected_type = CERTIFICATE_TYPE_BY_CLASSIFICATION.get(
            str(classification), "POSITIVE_WITNESS"
        )
        _require(
            certificate.get("certificate_type") == expected_type,
            f"PROOFPACK_CERTIFICATE_TYPE:{target_id}",
        )
        if classification == "AFFECTED_HARD":
            _require(effect.get("disposition") == "REBUILD", f"PROOFPACK_HARD_NOT_REBUILD:{target_id}")
        else:
            _require(
                effect.get("disposition") != "REBUILD",
                f"PROOFPACK_NON_HARD_REBUILD:{target_id}",
            )
        if classification == "UNKNOWN":
            _require(
                effect.get("disposition") == "HOLD_FOR_REVIEW",
                f"PROOFPACK_UNKNOWN_NOT_HOLD:{target_id}",
            )
    _require(
        vmrc.get("impact_certificate_set_digest")
        == sha256_digest(sorted(item["digest"] for item in certificates)),
        "PROOFPACK_CERTIFICATE_SET_DIGEST",
    )
    lock = _mapping(preview.get("revision_lock"), "PROOFPACK_REVISION_LOCK_MISSING")
    _require(vmrc.get("change_set_digest") == change_set.get("digest"), "PROOFPACK_VMRC_CHANGESET")
    _require(vmrc.get("preview_digest") == preview.get("digest"), "PROOFPACK_VMRC_PREVIEW")
    _require(vmrc.get("revision_lock_digest") == lock.get("digest"), "PROOFPACK_VMRC_LOCK")


def _verify_plan_chain(world: dict[str, Any]) -> None:
    change_set = _mapping(world.get("change_set"), "PROOFPACK_CHANGE_SET_MISSING")
    preview = _mapping(world.get("preview"), "PROOFPACK_PREVIEW_MISSING")
    lock = _mapping(preview.get("revision_lock"), "PROOFPACK_REVISION_LOCK_MISSING")
    plan = _mapping(world.get("orchestration_plan"), "PROOFPACK_PLAN_MISSING")
    compilation = _mapping(
        world.get("compilation_receipt"), "PROOFPACK_COMPILATION_MISSING"
    )
    coordination = _mapping(
        world.get("coordination_receipt"), "PROOFPACK_COORDINATION_MISSING"
    )
    ingestion = _mapping(
        world.get("candidate_ingestion"), "PROOFPACK_INGESTION_MISSING"
    )
    identities = _identity_index(
        [
            _mapping(item, "PROOFPACK_IDENTITY_INVALID")
            for item in _items(world.get("identities"), "PROOFPACK_IDENTITIES_MISSING")
        ]
    )
    intents = _mapping(world.get("task_intents"), "PROOFPACK_INTENTS_MISSING")
    tasks = [
        _mapping(item, "PROOFPACK_TASK_INVALID")
        for item in _items(plan.get("tasks"), "PROOFPACK_TASKS_MISSING")
    ]
    handoffs = [
        _mapping(item, "PROOFPACK_HANDOFF_INVALID")
        for item in _items(world.get("handoffs"), "PROOFPACK_HANDOFFS_MISSING")
    ]
    runs = [
        _mapping(item, "PROOFPACK_RUN_INVALID")
        for item in _items(world.get("agent_runs"), "PROOFPACK_RUNS_MISSING")
    ]
    _require(plan.get("digest") == FROZEN_LIVE_PLAN_DIGEST, "PROOFPACK_FROZEN_PLAN_DRIFT")
    _require(tuple(plan.get("invariants") or ()) == PLAN_INVARIANTS, "PROOFPACK_PLAN_INVARIANTS")
    _require(len(tasks) == 5, "PROOFPACK_TASK_COUNT")
    _require(len(identities) == 5, "PROOFPACK_IDENTITY_COUNT")
    seen_ids: list[str] = []
    seen_agents: list[str] = []
    expected_inputs = {
        change_set.get("digest"),
        preview.get("digest"),
        lock.get("digest"),
    }
    for task in tasks:
        task_id = task.get("id")
        agent_name = task.get("agent_name")
        _require(isinstance(task_id, str) and task_id not in seen_ids, "PROOFPACK_TASK_DUPLICATE")
        _require(
            isinstance(agent_name, str) and agent_name not in seen_agents,
            "PROOFPACK_AGENT_DUPLICATE",
        )
        depends_on = task.get("depends_on") or []
        _require(isinstance(depends_on, list), "PROOFPACK_TASK_DEPENDS_ON")
        _require(set(depends_on).issubset(seen_ids), "PROOFPACK_PLAN_NOT_ACYCLIC")
        identity = identities.get(str(agent_name))
        _require(identity is not None, f"PROOFPACK_UNKNOWN_AGENT:{agent_name}")
        _require(
            task.get("authority_domain") == identity.get("authority_domain"),
            f"PROOFPACK_AUTHORITY_MISMATCH:{agent_name}",
        )
        _require(
            set(task.get("required_capabilities") or ()).issubset(
                identity.get("capabilities") or ()
            ),
            f"PROOFPACK_CAPABILITY_SUPERSET:{agent_name}",
        )
        _require(
            set(task.get("allowed_output_kinds") or ()).issubset(identity.get("outputs") or ()),
            f"PROOFPACK_OUTPUT_SUPERSET:{agent_name}",
        )
        _require(set(task.get("input_refs") or ()) == expected_inputs, "PROOFPACK_TASK_INPUTS")
        _require(task.get("candidate_only") is True, "PROOFPACK_TASK_NOT_CANDIDATE")
        seen_ids.append(str(task_id))
        seen_agents.append(str(agent_name))
    _require(set(seen_agents) == set(identities), "PROOFPACK_PLAN_TEAM_MISMATCH")
    _require(
        compilation.get("task_intents_digest") == sha256_digest(intents),
        "PROOFPACK_INTENTS_DIGEST",
    )
    compilation_identities = {
        item["name"]: item["digest"]
        for item in _items(
            compilation.get("identity_digests"), "PROOFPACK_COMPILATION_IDENTITIES"
        )
        if isinstance(item, dict) and "name" in item and "digest" in item
    }
    _require(set(compilation_identities) == set(identities), "PROOFPACK_COMPILATION_IDENTITY_SET")
    for name, payload in identities.items():
        _require(
            compilation_identities[name] == sha256_digest(payload),
            f"PROOFPACK_IDENTITY_LOCK:{name}",
        )
    _require(
        compilation.get("orchestration_plan_digest") == plan.get("digest"),
        "PROOFPACK_COMPILATION_PLAN",
    )
    _require(
        compilation.get("change_set_digest") == change_set.get("digest"),
        "PROOFPACK_COMPILATION_CHANGESET",
    )
    _require(
        compilation.get("preview_digest") == preview.get("digest"),
        "PROOFPACK_COMPILATION_PREVIEW",
    )
    _require(len(handoffs) == len(tasks) and len(runs) == len(tasks), "PROOFPACK_EXECUTION_COVER")
    task_by_id = {item["id"]: item for item in tasks}
    handoff_by_task = {item.get("task_id"): item for item in handoffs}
    run_by_task = {item.get("task_id"): item for item in runs}
    _require(set(task_by_id) == set(handoff_by_task), "PROOFPACK_HANDOFF_TASK_SET")
    _require(set(task_by_id) == set(run_by_task), "PROOFPACK_RUN_TASK_SET")
    for task in tasks:
        handoff = handoff_by_task[task["id"]]
        run = run_by_task[task["id"]]
        _require(handoff.get("from_agent") == task.get("agent_name"), "PROOFPACK_HANDOFF_AGENT")
        _require(
            handoff.get("orchestration_plan_digest") == plan.get("digest"),
            "PROOFPACK_HANDOFF_PLAN",
        )
        _require(
            handoff.get("delegation_task_digest") == task.get("digest"),
            "PROOFPACK_HANDOFF_TASK_DIGEST",
        )
        _require(handoff.get("candidate_only") is True, "PROOFPACK_HANDOFF_NOT_CANDIDATE")
        _require(
            handoff.get("payload", {}).get("kind") in set(task.get("allowed_output_kinds") or ()),
            "PROOFPACK_HANDOFF_KIND",
        )
        _require(
            set(handoff.get("input_refs") or ()) == set(task.get("input_refs") or ()),
            "PROOFPACK_HANDOFF_INPUTS",
        )
        _require(run.get("agent_name") == task.get("agent_name"), "PROOFPACK_RUN_AGENT")
        _require(
            run.get("input_digest") == sha256_digest(task.get("input_refs")),
            "PROOFPACK_RUN_INPUT_DIGEST",
        )
        _require(
            run.get("output_digest") == sha256_digest(handoff.get("payload")),
            "PROOFPACK_RUN_OUTPUT_DIGEST",
        )
    _require(
        list(coordination.get("handoff_digests") or []) == [item["digest"] for item in handoffs],
        "PROOFPACK_COORDINATION_HANDOFFS",
    )
    _require(
        list(coordination.get("agent_run_digests") or []) == [item["digest"] for item in runs],
        "PROOFPACK_COORDINATION_RUNS",
    )
    _require(
        coordination.get("orchestration_plan_digest") == plan.get("digest"),
        "PROOFPACK_COORDINATION_PLAN",
    )
    _require(coordination.get("status") == "PASS", "PROOFPACK_COORDINATION_STATUS")
    _require(ingestion.get("target_writes") == 0, "PROOFPACK_INGESTION_WRITES")
    decisions = [
        _mapping(item, "PROOFPACK_DECISION_INVALID")
        for item in _items(ingestion.get("decisions"), "PROOFPACK_DECISIONS_MISSING")
    ]
    _require(
        all(not item.get("admitted_effects") for item in decisions),
        "PROOFPACK_ADMITTED_EFFECTS",
    )
    _require(
        ingestion.get("live_receipt_digest") == coordination.get("digest"),
        "PROOFPACK_INGESTION_COORDINATION",
    )
    _require(
        ingestion.get("orchestration_plan_digest") == plan.get("digest"),
        "PROOFPACK_INGESTION_PLAN",
    )
    _require(
        set(ingestion.get("admitted_candidate_digests") or ())
        == {item["digest"] for item in handoffs},
        "PROOFPACK_INGESTION_CANDIDATES",
    )


def _verify_receipt(world: dict[str, Any]) -> None:
    receipt = _mapping(world.get("rebase_receipt"), "PROOFPACK_RECEIPT_MISSING")
    vmrc = _mapping(
        world.get("minimal_rebase_certificate"), "PROOFPACK_VMRC_MISSING"
    )
    compilation = _mapping(
        world.get("compilation_receipt"), "PROOFPACK_COMPILATION_MISSING"
    )
    ingestion = _mapping(
        world.get("candidate_ingestion"), "PROOFPACK_INGESTION_MISSING"
    )
    approval = _mapping(world.get("approval"), "PROOFPACK_APPROVAL_MISSING")
    preview = _mapping(world.get("preview"), "PROOFPACK_PREVIEW_MISSING")
    _require(
        receipt.get("candidate_ingestion_digest") == ingestion.get("digest"),
        "PROOFPACK_RECEIPT_INGESTION",
    )
    _require(
        receipt.get("compilation_receipt_digest") == compilation.get("digest"),
        "PROOFPACK_RECEIPT_COMPILATION",
    )
    _require(
        receipt.get("minimal_rebase_certificate_digest") == vmrc.get("digest"),
        "PROOFPACK_RECEIPT_VMRC",
    )
    _require(receipt.get("approval_digest") == approval.get("digest"), "PROOFPACK_RECEIPT_APPROVAL")
    _require(
        approval.get("minimal_rebase_certificate_digest") == vmrc.get("digest"),
        "PROOFPACK_APPROVAL_VMRC",
    )
    _require(approval.get("preview_digest") == preview.get("digest"), "PROOFPACK_APPROVAL_PREVIEW")
    preserve = {
        item.get("target_id")
        for item in _items(vmrc.get("effects"), "PROOFPACK_EFFECTS_MISSING")
        if isinstance(item, dict) and item.get("disposition") == "PRESERVE_WITHIN_BOUNDARY"
    }
    bounded = {
        item.get("object_id")
        for item in _items(
            receipt.get("bounded_unaffected"), "PROOFPACK_BOUNDED_UNAFFECTED_MISSING"
        )
        if isinstance(item, dict)
    }
    _require(preserve == bounded, "PROOFPACK_RECEIPT_PRESERVE_SET")


def _verify_disk_lock(world: dict[str, Any], root: Path) -> None:
    disk_identities = _load_disk_identities(root)
    pack_identities = _identity_index(
        [
            _mapping(item, "PROOFPACK_IDENTITY_INVALID")
            for item in _items(world.get("identities"), "PROOFPACK_IDENTITIES_MISSING")
        ]
    )
    _require(disk_identities == pack_identities, "PROOFPACK_DISK_IDENTITY_DRIFT")
    intents_path = root / "orchestration" / "task-intents.json"
    disk_intents = _mapping(_read_json(intents_path), "PROOFPACK_DISK_INTENTS_INVALID")
    _require(disk_intents == world.get("task_intents"), "PROOFPACK_DISK_INTENTS_DRIFT")
    fixture = _mapping(
        _read_json(root / "fixtures" / "canonical-enterprise.json"),
        "PROOFPACK_DISK_FIXTURE_INVALID",
    )
    slice_ = _mapping(world.get("fixture_slice"), "PROOFPACK_FIXTURE_SLICE_MISSING")
    _require(slice_.get("objects") == fixture.get("objects"), "PROOFPACK_DISK_OBJECTS_DRIFT")
    _require(
        slice_.get("dependencies") == fixture.get("dependencies"),
        "PROOFPACK_DISK_EDGES_DRIFT",
    )
    _require(
        slice_.get("dependency_manifests") == fixture.get("dependency_manifests"),
        "PROOFPACK_DISK_MANIFESTS_DRIFT",
    )


def verify_pack(pack: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    _assert_source_independence()
    _require(pack.get("schema_version") == SCHEMA_VERSION, "PROOFPACK_SCHEMA")
    _require(pack.get("verifier_version") == VERIFIER_VERSION, "PROOFPACK_VERIFIER_VERSION")
    _require(pack.get("frozen_live_plan_digest") == FROZEN_LIVE_PLAN_DIGEST, "PROOFPACK_FROZEN_FIELD")
    _require(pack.get("disposition_table") == DISPOSITION_TABLE, "PROOFPACK_DISPOSITION_TABLE")
    _require(
        tuple(pack.get("independent_checks") or ()) == INDEPENDENT_CHECKS,
        "PROOFPACK_INDEPENDENT_CHECKS",
    )
    not_independent = set(pack.get("not_independent") or ())
    _require(
        not_independent >= REQUIRED_NOT_INDEPENDENT,
        "PROOFPACK_NOT_INDEPENDENT_UNDERCLAIM",
    )
    _assert_self_digest(pack, "PROOFPACK_PACK_DIGEST")
    world = _mapping(pack.get("world"), "PROOFPACK_WORLD_MISSING")
    _walk_self_digests(world, path="world")
    _verify_vmrc(world)
    _verify_plan_chain(world)
    _verify_receipt(world)
    _unaffected_bijection(world)
    if root is not None:
        _verify_disk_lock(world, root)
    return {
        "schema_version": "orgrebase.proof-pack-verification.v1",
        "status": "PASS",
        "verifier_version": VERIFIER_VERSION,
        "pack_digest": pack.get("digest"),
        "orchestration_plan_digest": world["orchestration_plan"]["digest"],
        "independent_checks": list(INDEPENDENT_CHECKS),
        "not_independent": sorted(REQUIRED_NOT_INDEPENDENT),
        "claim_boundary": pack.get("claim_boundary"),
    }


def build_from_demo(demo: dict[str, Any], *, root: Path) -> dict[str, Any]:
    collaboration = _mapping(demo.get("collaboration"), "PROOFPACK_DEMO_COLLABORATION")
    fixture = _mapping(
        _read_json(root / "fixtures" / "canonical-enterprise.json"),
        "PROOFPACK_DISK_FIXTURE_INVALID",
    )
    disk_identities = _load_disk_identities(root)
    identities = [disk_identities[name] for name in sorted(disk_identities)]
    intents = _mapping(
        _read_json(root / "orchestration" / "task-intents.json"),
        "PROOFPACK_DISK_INTENTS_INVALID",
    )
    pack = {
        "schema_version": SCHEMA_VERSION,
        "verifier_version": VERIFIER_VERSION,
        "frozen_live_plan_digest": FROZEN_LIVE_PLAN_DIGEST,
        "claim_boundary": (
            "This pack proves closed-world digest and set consistency for one committed "
            "Northstar snapshot. It does not prove path classification, coverage of a "
            "real organization, live candidate authenticity, or identity-to-DAG synthesis."
        ),
        "independent_checks": list(INDEPENDENT_CHECKS),
        "not_independent": sorted(REQUIRED_NOT_INDEPENDENT),
        "disposition_table": dict(DISPOSITION_TABLE),
        "world": {
            "change_set": demo.get("change_set"),
            "preview": demo.get("preview"),
            "minimal_rebase_certificate": demo.get("minimal_rebase_certificate"),
            "approval": demo.get("approval"),
            "orchestration_plan": collaboration.get("orchestration_plan"),
            "compilation_receipt": collaboration.get("compilation_receipt"),
            "coordination_receipt": collaboration.get("coordination_receipt"),
            "handoffs": collaboration.get("handoffs"),
            "agent_runs": collaboration.get("agent_runs"),
            "candidate_ingestion": collaboration.get("candidate_ingestion"),
            "rebase_receipt": demo.get("receipt"),
            "task_intents": intents,
            "identities": identities,
            "fixture_slice": {
                "objects": fixture.get("objects"),
                "dependencies": fixture.get("dependencies"),
                "dependency_manifests": fixture.get("dependency_manifests"),
            },
        },
    }
    pack["digest"] = _self_digest(pack)
    return pack


def verify_path(path: Path, *, root: Path | None = None) -> dict[str, Any]:
    pack = _mapping(_read_json(path), f"PROOFPACK_JSON_OBJECT_REQUIRED:{path.name}")
    return verify_pack(pack, root=root)


def write_pack(root: Path, *, demo_path: Path | None = None, output: Path | None = None) -> dict[str, Any]:
    source = demo_path or (root / "evidence" / "latest" / "demo.json")
    target = output or (root / "evidence" / "latest" / "proof-pack.json")
    demo = _mapping(_read_json(source), "PROOFPACK_DEMO_INVALID")
    pack = build_from_demo(demo, root=root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = verify_pack(pack, root=root)
    report["path"] = str(target)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Build evidence/latest/proof-pack.json from demo.json, then verify.",
    )
    parser.add_argument(
        "pack",
        nargs="?",
        type=Path,
        default=None,
        help="Proof pack to verify. Defaults to evidence/latest/proof-pack.json.",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        if args.write:
            report = write_pack(root, output=args.pack.resolve() if args.pack else None)
        else:
            path = (args.pack or (root / "evidence" / "latest" / "proof-pack.json")).resolve()
            report = verify_path(path, root=root)
            report["path"] = str(path)
    except ProofPackError as exc:
        report = {
            "schema_version": "orgrebase.proof-pack-verification.v1",
            "status": "FAIL",
            "error": str(exc),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        raise SystemExit(2) from exc
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
