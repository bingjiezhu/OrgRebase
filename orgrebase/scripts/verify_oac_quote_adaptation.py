#!/usr/bin/env python3
"""Independently verify the Spec 062 OAC adaptation evidence lane.

The verifier intentionally imports neither ``orgrebase`` nor the evidence
producer.  It reparses closed-world JSON, recomputes every OrgRebase content
digest, checks all cross-resource bindings, and invokes only the public OAC CLI
for OAC resource validation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from importlib.metadata import version
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = PROJECT_ROOT / "evidence" / "oac-quote-adaptation" / "latest"
RETAINED_OAC_WHEEL = "evidence/oac-evolution/wheel-check/wheels/oac_contract-0.3.0a0-py3-none-any.whl"
RETAINED_OAC_SHA256 = "aead48d80e921ddd8ea9505e4723d716e35ac351bb4237b7a88ad38a83e7e13b"
RETAINED_OAC_BOOTSTRAP = """
import hashlib, pathlib, runpy, sys, tempfile
wheel = pathlib.Path(sys.argv[1]).resolve(strict=True)
wheel_bytes = wheel.read_bytes()
if hashlib.sha256(wheel_bytes).hexdigest() != sys.argv[2]:
    raise RuntimeError("RETAINED_OAC_READER_UNAVAILABLE_OR_CHANGED")
with tempfile.TemporaryDirectory(prefix="orgrebase-retained-oac-") as temporary:
    private_wheel = pathlib.Path(temporary) / wheel.name
    private_wheel.write_bytes(wheel_bytes)
    sys.path.insert(0, str(private_wheel))
    import oac
    if not str(oac.__file__).startswith(str(private_wheel) + "/"):
        raise RuntimeError("RETAINED_OAC_READER_ORIGIN_INVALID")
    sys.argv = ["oac", *sys.argv[3:]]
    try:
        runpy.run_module("oac.cli", run_name="__main__")
    finally:
        for name, module in sys.modules.copy().items():
            if (name == "oac" or name.startswith("oac.")) and getattr(module, "__file__", None):
                if not str(module.__file__).startswith(str(private_wheel) + "/"):
                    raise RuntimeError("RETAINED_OAC_READER_ORIGIN_INVALID")
"""
EXPECTED_FILES = {
    "artifacts/evergreen/activation-binding.json",
    "artifacts/evergreen/adaptation-draft.json",
    "artifacts/evergreen/adapter-capsule.json",
    "artifacts/evergreen/approval.json",
    "artifacts/evergreen/golden-summary.json",
    "artifacts/evergreen/organization-snapshot.json",
    "artifacts/evergreen/organizational-demand.json",
    "artifacts/evergreen/owner-review-summary.json",
    "artifacts/evergreen/quote-formation-parity-receipt.json",
    "artifacts/evergreen/review-gate.json",
    "artifacts/evergreen/review-observation.json",
    "artifacts/evergreen/source-admission-receipt.json",
    "artifacts/veracier/approval-rejection.json",
    "artifacts/veracier/hold-draft.json",
    "mutations/cross-profile-capsule.json",
    "mutations/digest-substitution.json",
    "mutations/parity-omission.json",
    "mutations/self-approval.json",
    "mutations/stale-approval.json",
    "mutations/unknown-erasure.json",
    "summary.json",
}
KINDS = ("DOMAIN", "KNOWLEDGE", "AUTHORITY", "CAPABILITY", "DEPENDENCY")
OBLIGATIONS = (
    "PRODUCT_FACTS",
    "LEGAL_POLICY",
    "FINANCE_POLICY",
    "GTM_COMPOSITION",
    "INDEPENDENT_REVIEW",
)
OWNER_REVIEW_ACKNOWLEDGEMENTS = (
    "REVIEWED_SOURCE_TO_CONTRACT_SUMMARY",
    "ACCEPTED_DECLARED_UNKNOWNS",
    "UNDERSTAND_NO_BUSINESS_APPROVAL",
)
MUTATION_EXPECTATIONS = {
    "cross-profile-capsule.json": (
        "CROSS_PROFILE_CAPSULE",
        "CAPSULE_PROFILE_BINDING_MISMATCH",
    ),
    "digest-substitution.json": (
        "DIGEST_SUBSTITUTION",
        "CAPSULE_MAPPING_BINDING_MISMATCH",
    ),
    "parity-omission.json": (
        "PARITY_OMISSION",
        "PARITY_TASK_COUNT_INVALID",
    ),
    "self-approval.json": (
        "SELF_APPROVAL",
        "EVOLUTION_SELF_ADMISSION_FORBIDDEN",
    ),
    "stale-approval.json": (
        "STALE_APPROVAL",
        "APPROVAL_BEFORE_NOT_BEFORE",
    ),
    "unknown-erasure.json": (
        "UNKNOWN_ERASURE",
        "HOLD_GAPS_REQUIRED",
    ),
}


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def _is_digest(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 71 or not value.startswith("sha256:"):
        return False
    try:
        int(value[7:], 16)
    except ValueError:
        return False
    return True


def _sealed(value: dict[str, Any], label: str, failures: list[str]) -> bool:
    claimed = value.get("digest")
    payload = {key: item for key, item in value.items() if key != "digest"}
    valid = _is_digest(claimed) and claimed == _digest(payload)
    if not valid:
        failures.append(f"{label}_DIGEST_MISMATCH")
    return valid


def _require(condition: bool, code: str, failures: list[str]) -> None:
    if not condition:
        failures.append(code)


def _sorted(values: set[str]) -> tuple[str, ...]:
    return tuple(sorted(values, key=str.encode))


def _verify_manifest(root: Path, failures: list[str]) -> dict[str, Any]:
    manifest = _load(root / "manifest.json")
    _sealed(manifest, "MANIFEST", failures)
    _require(
        manifest.get("schema_version")
        == "orgrebase.oac-quote-adaptation-evidence-manifest.v1",
        "MANIFEST_SCHEMA_UNSUPPORTED",
        failures,
    )
    _require(manifest.get("status") == "CLOSED_WORLD", "MANIFEST_STATUS_INVALID", failures)
    _require(
        manifest.get("evidence_class") == "VALIDATED_CONTROLLED_LOCAL",
        "MANIFEST_EVIDENCE_CLASS_INVALID",
        failures,
    )
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        failures.append("MANIFEST_ENTRIES_INVALID")
        return manifest
    observed_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path != root / "manifest.json"
    }
    indexed_paths = {
        str(item.get("path")) for item in entries if isinstance(item, dict)
    }
    _require(observed_paths == EXPECTED_FILES, "EVIDENCE_FILE_SET_DRIFT", failures)
    _require(indexed_paths == observed_paths, "MANIFEST_NOT_CLOSED_WORLD", failures)
    _require(len(indexed_paths) == len(entries), "MANIFEST_PATH_DUPLICATE", failures)
    _require(manifest.get("entry_count") == len(entries), "MANIFEST_COUNT_INVALID", failures)
    _require(manifest.get("pack_digest") == _digest(entries), "MANIFEST_PACK_DIGEST_INVALID", failures)
    for item in entries:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "bytes"}:
            failures.append("MANIFEST_ENTRY_SHAPE_INVALID")
            continue
        relative = Path(str(item["path"]))
        target = (root / relative).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            failures.append(f"MANIFEST_PATH_ESCAPE:{item['path']}")
            continue
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not target.is_file()
            or target.is_symlink()
        ):
            failures.append(f"MANIFEST_FILE_INVALID:{item['path']}")
            continue
        if item.get("sha256") != _file_digest(target):
            failures.append(f"MANIFEST_FILE_DIGEST_MISMATCH:{item['path']}")
        if item.get("bytes") != target.stat().st_size:
            failures.append(f"MANIFEST_FILE_SIZE_MISMATCH:{item['path']}")
    return manifest


def _locate_oac_root(explicit: Path | None) -> Path:
    if explicit is not None:
        candidates = [explicit]
    elif os.environ.get("ORGREBASE_OAC_ROOT"):
        candidates = [Path(os.environ["ORGREBASE_OAC_ROOT"])]
    else:
        candidates = [PROJECT_ROOT.parent / "oac-spec", Path.cwd().parent / "oac-spec"]
    for candidate in candidates:
        selected = candidate.expanduser().resolve()
        if (selected / "pyproject.toml").is_file() and (selected / "src" / "oac").is_dir():
            return selected
    raise FileNotFoundError("OAC_SPEC_CHECKOUT_NOT_FOUND")


def _oac_command(root: Path, arguments: list[str]) -> list[str]:
    if root.suffix == ".whl":
        if not root.is_file() or hashlib.sha256(root.read_bytes()).hexdigest() != RETAINED_OAC_SHA256:
            raise RuntimeError("RETAINED_OAC_READER_UNAVAILABLE_OR_CHANGED")
        return [sys.executable, "-I", "-c", RETAINED_OAC_BOOTSTRAP,
                str(root), RETAINED_OAC_SHA256, *arguments]
    python = root / ".venv" / "bin" / "python"
    if python.is_file():
        return [str(python), "-m", "oac.cli", *arguments]
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("OAC_PUBLIC_CLI_RUNTIME_NOT_FOUND")
    return [uv, "run", "--project", str(root), "oac", *arguments]


def _run_oac(root: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _oac_command(root, arguments),
        cwd=root.parent if root.suffix == ".whl" else root,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _oac_pass(
    root: Path,
    arguments: list[str],
    label: str,
    failures: list[str],
) -> None:
    observed = _run_oac(root, arguments)
    if observed.returncode != 0:
        failures.append(f"OAC_PUBLIC_CLI_FAILED:{label}")


def _verify_mapping_set(
    draft: dict[str, Any],
    *,
    expect_unknowns: bool,
    failures: list[str],
    label: str,
) -> None:
    mappings = draft.get("candidate_mappings")
    if not isinstance(mappings, list):
        failures.append(f"{label}_MAPPINGS_INVALID")
        return
    _require(len(mappings) == 5, f"{label}_MAPPING_COUNT_INVALID", failures)
    _require(
        tuple(item.get("component_kind") for item in mappings if isinstance(item, dict)) == KINDS,
        f"{label}_MAPPING_ORDER_INVALID",
        failures,
    )
    for index, mapping in enumerate(mappings):
        if not isinstance(mapping, dict):
            failures.append(f"{label}_MAPPING_INVALID:{index}")
            continue
        _sealed(mapping, f"{label}_MAPPING_{index}", failures)
        unknowns = mapping.get("declared_unknowns")
        reasons = mapping.get("reason_codes")
        _require(
            isinstance(unknowns, list)
            and isinstance(reasons, list)
            and bool(unknowns) == bool(reasons),
            f"{label}_UNKNOWN_REASON_BINDING_INVALID:{index}",
            failures,
        )
        _require(mapping.get("candidate_only") is True, f"{label}_CANDIDATE_ONLY_INVALID", failures)
        _require(mapping.get("canonical_target_writes") == 0, f"{label}_TARGET_WRITES_INVALID", failures)
        _require(mapping.get("effect_ceiling") == "ZERO_EXTERNAL_EFFECTS", f"{label}_EFFECT_INVALID", failures)
        _require(mapping.get("adaptation_run_id") == draft.get("adaptation_run_id"), f"{label}_RUN_BINDING_INVALID", failures)
        _require(_is_digest(mapping.get("source_digest")), f"{label}_SOURCE_DIGEST_INVALID", failures)
        if not expect_unknowns:
            _require(unknowns == [] and reasons == [], f"{label}_UNEXPECTED_UNKNOWN", failures)
    if len(mappings) == 5 and all(isinstance(item, dict) for item in mappings):
        expected = _digest([item.get("digest") for item in mappings])
        _require(
            draft.get("mapping_set_digest") == expected,
            f"{label}_MAPPING_SET_DIGEST_INVALID",
            failures,
        )


def _verify_oac_resources(
    *,
    root: Path,
    oac_root: Path,
    snapshot: dict[str, Any],
    demand: dict[str, Any],
    source: dict[str, Any],
    failures: list[str],
) -> None:
    _require(snapshot.get("kind") == "OrganizationSnapshot", "SNAPSHOT_KIND_INVALID", failures)
    _require(demand.get("kind") == "OrganizationalDemand", "DEMAND_KIND_INVALID", failures)
    _require(source.get("kind") == "SourceAdmissionReceipt", "SOURCE_ADMISSION_KIND_INVALID", failures)
    _require(snapshot.get("apiVersion") == "oac.dev/v0alpha1", "SNAPSHOT_API_INVALID", failures)
    _require(demand.get("apiVersion") == "oac.dev/v0alpha1", "DEMAND_API_INVALID", failures)
    _require(source.get("apiVersion") == "oac.dev/v0alpha1", "SOURCE_ADMISSION_API_INVALID", failures)
    snapshot_spec = snapshot.get("spec", {})
    demand_spec = demand.get("spec", {})
    source_spec = source.get("spec", {})
    snapshot_nodes = snapshot_spec.get("nodes")
    if not isinstance(snapshot_nodes, list):
        failures.append("SNAPSHOT_NODES_INVALID")
        snapshot_nodes = []
    node_ids = [
        item.get("nodeId")
        for item in snapshot_nodes
        if isinstance(item, dict) and isinstance(item.get("nodeId"), str)
    ]
    _require(
        len(node_ids) == len(snapshot_nodes),
        "SNAPSHOT_NODE_SHAPE_INVALID",
        failures,
    )
    _require(
        len(node_ids) == len(set(node_ids)),
        "SNAPSHOT_NODE_ID_DUPLICATE",
        failures,
    )
    required_root_ids = {
        "source-root:authority",
        "source-root:capability",
        "source-root:dependency",
        "source-root:domain",
        "source-root:knowledge",
    }
    root_nodes = {
        item.get("nodeId"): item
        for item in snapshot_nodes
        if isinstance(item, dict)
        and isinstance(item.get("nodeId"), str)
        and item["nodeId"].startswith("source-root:")
    }
    _require(
        set(root_nodes) == required_root_ids,
        "SNAPSHOT_FIVE_ROOTS_INVALID",
        failures,
    )
    _require(
        all(
            item.get("nodeType") == "enterprise-contract-root"
            and item.get("admissionStatus") == "candidate"
            and item.get("domainRef") == "domain:enterprise-quote"
            and item.get("ownerRoleRef") == "role:enterprise-contract-owner"
            for item in root_nodes.values()
        ),
        "SNAPSHOT_ROOT_SEMANTICS_INVALID",
        failures,
    )
    snapshot_edges = snapshot_spec.get("dependencyEdges")
    if not isinstance(snapshot_edges, list):
        failures.append("SNAPSHOT_EDGES_INVALID")
        snapshot_edges = []
    edge_ids = [
        item.get("edgeId")
        for item in snapshot_edges
        if isinstance(item, dict) and isinstance(item.get("edgeId"), str)
    ]
    _require(
        len(edge_ids) == len(snapshot_edges),
        "SNAPSHOT_EDGE_SHAPE_INVALID",
        failures,
    )
    _require(
        len(edge_ids) == len(set(edge_ids)),
        "SNAPSHOT_EDGE_ID_DUPLICATE",
        failures,
    )
    endpoint_refs = {
        value
        for item in snapshot_edges
        if isinstance(item, dict)
        for value in (item.get("sourceRef"), item.get("targetRef"))
        if isinstance(value, str)
    }
    _require(
        endpoint_refs.issubset(set(node_ids)),
        "SNAPSHOT_EDGE_ENDPOINT_UNKNOWN",
        failures,
    )
    completeness = snapshot_spec.get("completeness")
    if isinstance(completeness, dict):
        _require(
            completeness.get("status") == "complete"
            and completeness.get("knownGaps") == [],
            "SNAPSHOT_COMPLETENESS_STATUS_INVALID",
            failures,
        )
        _require(
            completeness.get("coveredNodeRefs") == sorted(node_ids),
            "SNAPSHOT_COVERED_NODES_INVALID",
            failures,
        )
        _require(
            completeness.get("coveredRelationTypes")
            == sorted(
                {
                    str(item["relationType"])
                    for item in snapshot_edges
                    if isinstance(item, dict) and "relationType" in item
                }
            ),
            "SNAPSHOT_COVERED_RELATIONS_INVALID",
            failures,
        )
    else:
        failures.append("SNAPSHOT_COMPLETENESS_INVALID")
    _require(
        snapshot_spec.get("impactRules") == [],
        "SNAPSHOT_IMPACT_RULES_INVALID",
        failures,
    )
    _require(demand_spec.get("effectCeiling") == "zero_effect", "DEMAND_EFFECT_INVALID", failures)
    _require(len(demand_spec.get("evidenceObligationRefs", [])) == 5, "DEMAND_OBLIGATION_COUNT_INVALID", failures)
    expected_snapshot_ref = {
        "apiVersion": snapshot.get("apiVersion"),
        "kind": snapshot.get("kind"),
        "namespace": snapshot.get("metadata", {}).get("namespace"),
        "resourceId": snapshot.get("metadata", {}).get("id"),
        "revision": snapshot.get("metadata", {}).get("revision"),
        "digest": snapshot.get("digest"),
    }
    _require(demand_spec.get("snapshotRef") == expected_snapshot_ref, "DEMAND_SNAPSHOT_BINDING_INVALID", failures)
    _require(source_spec.get("admissionPurpose") == "enterprise_intake", "SOURCE_ADMISSION_PURPOSE_INVALID", failures)
    _require(source_spec.get("verdict") == "ADMITTED", "SOURCE_ADMISSION_VERDICT_INVALID", failures)
    _require(source_spec.get("unresolvedRefs") == [], "SOURCE_ADMISSION_UNRESOLVED_INVALID", failures)
    _require(source_spec.get("admittedSubjectRefs") == source_spec.get("subjectRefs"), "SOURCE_ADMISSION_SUBJECT_BINDING_INVALID", failures)
    proposers = {
        json.dumps(item, sort_keys=True, separators=(",", ":"))
        for item in source_spec.get("proposerRefs", [])
    }
    reviewers = {
        json.dumps(item, sort_keys=True, separators=(",", ":"))
        for item in source_spec.get("reviewerRefs", [])
    }
    authority = json.dumps(
        source_spec.get("decisionAuthorityRef"), sort_keys=True, separators=(",", ":")
    )
    _require(authority not in proposers and not proposers & reviewers, "SOURCE_ADMISSION_SEPARATION_INVALID", failures)

    snapshot_path = root / "artifacts/evergreen/organization-snapshot.json"
    demand_path = root / "artifacts/evergreen/organizational-demand.json"
    source_path = root / "artifacts/evergreen/source-admission-receipt.json"
    _oac_pass(oac_root, ["validate", str(snapshot_path), "--verify-digest"], "SNAPSHOT_VALIDATE", failures)
    _oac_pass(oac_root, ["validate", str(demand_path), "--verify-digest"], "DEMAND_VALIDATE", failures)
    _oac_pass(
        oac_root,
        ["validate-evolution", str(demand_path), "--snapshot", str(snapshot_path)],
        "DEMAND_EVOLUTION",
        failures,
    )
    _oac_pass(oac_root, ["validate", str(source_path), "--verify-digest"], "SOURCE_VALIDATE", failures)
    _oac_pass(oac_root, ["validate-evolution", str(source_path)], "SOURCE_EVOLUTION", failures)


def _verify_parity(
    parity: dict[str, Any],
    *,
    draft: dict[str, Any],
    golden: dict[str, Any],
    failures: list[str],
) -> None:
    _sealed(parity, "PARITY", failures)
    attempts = parity.get("task_attempts")
    if not isinstance(attempts, list):
        failures.append("PARITY_ATTEMPTS_INVALID")
        return
    _require(len(attempts) == 7, "PARITY_TASK_COUNT_INVALID", failures)
    for index, attempt in enumerate(attempts):
        if not isinstance(attempt, dict):
            failures.append(f"PARITY_ATTEMPT_INVALID:{index}")
            continue
        _sealed(attempt, f"PARITY_ATTEMPT_{index}", failures)
        _require(attempt.get("candidate_only") is True, "PARITY_CANDIDATE_ONLY_INVALID", failures)
        _require(attempt.get("target_writes") == 0, "PARITY_TARGET_WRITES_INVALID", failures)
        _require(attempt.get("run_id") == parity.get("golden_run_id"), "PARITY_RUN_BINDING_INVALID", failures)
        _require(attempt.get("correlation_id") == parity.get("golden_correlation_id"), "PARITY_CORRELATION_BINDING_INVALID", failures)
    if len(attempts) != 7 or not all(isinstance(item, dict) for item in attempts):
        return
    _require(len({item.get("task_id") for item in attempts}) == 7, "PARITY_TASK_IDS_INVALID", failures)
    _require({item.get("obligation_ref") for item in attempts} == set(OBLIGATIONS), "PARITY_OBLIGATION_COVERAGE_INVALID", failures)
    by_domain = {
        domain: [item for item in attempts if item.get("domain") == domain]
        for domain in ("product", "legal", "finance", "gtm", "coalition")
    }
    _require(tuple(len(by_domain[item]) for item in by_domain) == (1, 1, 2, 1, 2), "PARITY_DOMAIN_CARDINALITY_INVALID", failures)
    finance = sorted(by_domain["finance"], key=lambda item: item.get("attempt"))
    reviewers = sorted(by_domain["coalition"], key=lambda item: item.get("attempt"))
    if len(finance) == 2:
        _require(tuple(item.get("outcome") for item in finance) == ("ABSTAIN", "PASS"), "PARITY_FINANCE_RECOVERY_INVALID", failures)
    if len(reviewers) == 2:
        _require(tuple(item.get("outcome") for item in reviewers) == ("REPLAN", "PASS"), "PARITY_REVIEW_RECOVERY_INVALID", failures)
    worker_principals = {
        str(item.get("principal_ref")) for item in attempts if item.get("role") == "DOMAIN_WORKER"
    }
    reviewer_principals = {
        str(item.get("principal_ref")) for item in attempts if item.get("role") == "REVIEWER"
    }
    _require(not worker_principals & reviewer_principals, "PARITY_WORKER_REVIEWER_SEPARATION_INVALID", failures)
    principals = _sorted(worker_principals | reviewer_principals)
    _require(tuple(parity.get("authorized_principal_refs", [])) == principals, "PARITY_AUTHORIZED_PRINCIPALS_INVALID", failures)
    _require(parity.get("authority_binding_digest") == _digest(list(principals)), "PARITY_AUTHORITY_DIGEST_INVALID", failures)
    _require(parity.get("attempt_set_digest") == _digest([item.get("digest") for item in attempts]), "PARITY_ATTEMPT_SET_DIGEST_INVALID", failures)
    _require(
        parity.get("obligation_coverage_digest")
        == _digest(
            {
                obligation: [
                    item.get("task_id")
                    for item in attempts
                    if item.get("obligation_ref") == obligation
                ]
                for obligation in OBLIGATIONS
            }
        ),
        "PARITY_OBLIGATION_DIGEST_INVALID",
        failures,
    )
    _require(
        parity.get("happens_before_digest")
        == _digest(
            [
                {
                    "task_id": item.get("task_id"),
                    "predecessor_task_refs": item.get("predecessor_task_refs"),
                }
                for item in attempts
            ]
        ),
        "PARITY_HAPPENS_BEFORE_DIGEST_INVALID",
        failures,
    )
    seen: set[str] = set()
    for item in attempts:
        predecessors = set(item.get("predecessor_task_refs", []))
        _require(predecessors <= seen, "PARITY_HAPPENS_BEFORE_INVALID", failures)
        seen.add(str(item.get("task_id")))
    _require(parity.get("adaptation_run_id") == draft.get("adaptation_run_id"), "PARITY_ADAPTATION_BINDING_INVALID", failures)
    _require(parity.get("adaptation_draft_digest") == draft.get("digest"), "PARITY_DRAFT_BINDING_INVALID", failures)
    _require(parity.get("mapping_set_digest") == draft.get("mapping_set_digest"), "PARITY_MAPPING_BINDING_INVALID", failures)
    _require(parity.get("profile_digest") == draft.get("profile_digest"), "PARITY_PROFILE_BINDING_INVALID", failures)
    _require(parity.get("pack_digest") == draft.get("pack_digest"), "PARITY_PACK_BINDING_INVALID", failures)
    _require(parity.get("golden_summary_digest") == golden.get("digest"), "PARITY_GOLDEN_DIGEST_INVALID", failures)
    _require(parity.get("golden_run_id") == golden.get("run_id"), "PARITY_GOLDEN_RUN_INVALID", failures)
    _require(parity.get("golden_correlation_id") == golden.get("correlation_id"), "PARITY_GOLDEN_CORRELATION_INVALID", failures)
    _require(parity.get("logical_obligation_count") == 5, "PARITY_LOGICAL_COUNT_INVALID", failures)
    _require(parity.get("dynamic_task_attempt_count") == 7, "PARITY_DYNAMIC_COUNT_INVALID", failures)
    _require(parity.get("binding_timing") == "POST_RUN_SAME_RUN_REPLAY", "PARITY_TIMING_INVALID", failures)
    _require(parity.get("formation_authority") == "ORGREBASE_CONTROL_PLANE", "PARITY_AUTHORITY_INVALID", failures)
    _require(parity.get("oac_plan_produced") is False, "PARITY_OAC_PLAN_CLAIM_INVALID", failures)
    _require(parity.get("oac_plan_certificate_produced") is False, "PARITY_OAC_CERT_CLAIM_INVALID", failures)
    _require(parity.get("oac_runtime_invoked") is False, "PARITY_OAC_RUNTIME_CLAIM_INVALID", failures)
    _require(parity.get("canonical_target_writes") == 0, "PARITY_CANONICAL_WRITES_INVALID", failures)


def _verify_mutations(
    root: Path,
    *,
    oac_root: Path,
    draft: dict[str, Any],
    hold: dict[str, Any],
    veracier_profile_digest: str,
    failures: list[str],
) -> dict[str, str]:
    rejected: dict[str, str] = {}
    for filename, (expected_class, expected_code) in MUTATION_EXPECTATIONS.items():
        mutation = _load(root / "mutations" / filename)
        _sealed(mutation, f"MUTATION_{expected_class}", failures)
        _require(mutation.get("mutation_class") == expected_class, f"MUTATION_CLASS_INVALID:{filename}", failures)
        _require(mutation.get("expected_rejection_code") == expected_code, f"MUTATION_EXPECTATION_INVALID:{filename}", failures)
        payload = mutation.get("payload")
        if not isinstance(payload, dict):
            failures.append(f"MUTATION_PAYLOAD_INVALID:{filename}")
            continue

        observed_code: str | None = None
        if expected_class == "DIGEST_SUBSTITUTION":
            if _sealed(payload, "MUTATION_DIGEST_SUBSTITUTION_PAYLOAD", failures) and payload.get("mapping_set_digest") != draft.get("mapping_set_digest"):
                observed_code = "CAPSULE_MAPPING_BINDING_MISMATCH"
        elif expected_class == "UNKNOWN_ERASURE":
            content_failures: list[str] = []
            valid_content = _sealed(payload, "MUTATION_UNKNOWN_ERASURE_PAYLOAD", content_failures)
            mappings = payload.get("candidate_mappings", [])
            for index, mapping in enumerate(mappings if isinstance(mappings, list) else []):
                if isinstance(mapping, dict):
                    valid_content = _sealed(mapping, f"MUTATION_UNKNOWN_MAPPING_{index}", content_failures) and valid_content
            failures.extend(content_failures)
            if valid_content and payload.get("status") == "HOLD" and payload.get("gaps") == []:
                observed_code = "HOLD_GAPS_REQUIRED"
        elif expected_class == "SELF_APPROVAL":
            with tempfile.TemporaryDirectory(prefix="orgrebase-oac-self-approval-") as raw:
                path = Path(raw) / "source-admission.json"
                path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
                schema = _run_oac(oac_root, ["validate", str(path), "--verify-digest"])
                semantic = _run_oac(oac_root, ["validate-evolution", str(path)])
            diagnostic = semantic.stderr + semantic.stdout
            if schema.returncode == 0 and semantic.returncode != 0 and expected_code in diagnostic:
                observed_code = expected_code
        elif expected_class == "CROSS_PROFILE_CAPSULE":
            if (
                _sealed(payload, "MUTATION_CROSS_PROFILE_PAYLOAD", failures)
                and payload.get("profile_digest") == veracier_profile_digest
                and payload.get("profile_digest") != draft.get("profile_digest")
            ):
                observed_code = "CAPSULE_PROFILE_BINDING_MISMATCH"
        elif expected_class == "STALE_APPROVAL":
            gate = draft.get("review_gate", {})
            if (
                _sealed(payload, "MUTATION_STALE_APPROVAL_PAYLOAD", failures)
                and payload.get("candidate_digest") == draft.get("mapping_set_digest")
                and payload.get("approved_at_epoch_ms", 0) < gate.get("not_before_epoch_ms", 0)
            ):
                observed_code = "APPROVAL_BEFORE_NOT_BEFORE"
        elif expected_class == "PARITY_OMISSION":
            attempts = payload.get("task_attempts")
            content_valid = _sealed(payload, "MUTATION_PARITY_PAYLOAD", failures)
            if isinstance(attempts, list):
                for index, attempt in enumerate(attempts):
                    if isinstance(attempt, dict):
                        content_valid = _sealed(attempt, f"MUTATION_PARITY_ATTEMPT_{index}", failures) and content_valid
            if content_valid and isinstance(attempts, list) and len(attempts) != 7:
                observed_code = "PARITY_TASK_COUNT_INVALID"

        if observed_code != expected_code:
            failures.append(f"MUTATION_NOT_REJECTED:{expected_class}")
        else:
            rejected[expected_class] = observed_code

    _require(set(rejected) == {value[0] for value in MUTATION_EXPECTATIONS.values()}, "MUTATION_REJECTION_COVERAGE_INVALID", failures)
    _require(hold.get("profile_digest") == veracier_profile_digest, "VERACIER_MUTATION_BASELINE_INVALID", failures)
    return rejected


def verify(root: Path, oac_root: Path, project_root: Path) -> dict[str, Any]:
    failures: list[str] = []
    manifest = _verify_manifest(root, failures)
    summary = _load(root / "summary.json")
    draft = _load(root / "artifacts/evergreen/adaptation-draft.json")
    frozen_owner_review_summary = _load(
        root / "artifacts/evergreen/owner-review-summary.json"
    )
    snapshot = _load(root / "artifacts/evergreen/organization-snapshot.json")
    demand = _load(root / "artifacts/evergreen/organizational-demand.json")
    gate = _load(root / "artifacts/evergreen/review-gate.json")
    review = _load(root / "artifacts/evergreen/review-observation.json")
    approval = _load(root / "artifacts/evergreen/approval.json")
    source = _load(root / "artifacts/evergreen/source-admission-receipt.json")
    parity = _load(root / "artifacts/evergreen/quote-formation-parity-receipt.json")
    capsule = _load(root / "artifacts/evergreen/adapter-capsule.json")
    activation = _load(root / "artifacts/evergreen/activation-binding.json")
    golden = _load(root / "artifacts/evergreen/golden-summary.json")
    hold = _load(root / "artifacts/veracier/hold-draft.json")
    hold_rejection = _load(root / "artifacts/veracier/approval-rejection.json")
    owner_review_summary = draft.get("owner_review_summary")

    for label, value in (
        ("SUMMARY", summary),
        ("DRAFT", draft),
        ("GATE", gate),
        ("REVIEW", review),
        ("APPROVAL", approval),
        ("CAPSULE", capsule),
        ("ACTIVATION", activation),
        ("GOLDEN", golden),
        ("HOLD", hold),
        ("HOLD_REJECTION", hold_rejection),
    ):
        _sealed(value, label, failures)
    if isinstance(owner_review_summary, dict):
        _sealed(owner_review_summary, "OWNER_REVIEW_SUMMARY", failures)
        _require(
            owner_review_summary == frozen_owner_review_summary,
            "OWNER_REVIEW_SUMMARY_FROZEN_COPY_DRIFT",
            failures,
        )
    else:
        failures.append("OWNER_REVIEW_SUMMARY_MISSING")

    _require(summary.get("schema_version") == "orgrebase.oac-quote-adaptation-evidence-summary.v1", "SUMMARY_SCHEMA_UNSUPPORTED", failures)
    _require(summary.get("status") == "PASS", "SUMMARY_STATUS_INVALID", failures)
    _require(summary.get("evidence_class") == "VALIDATED_CONTROLLED_LOCAL", "SUMMARY_EVIDENCE_CLASS_INVALID", failures)
    _require(summary.get("claim_ceiling") == "CONTROLLED_LOCAL_ADAPTATION_MECHANISM_ONLY", "SUMMARY_CLAIM_CEILING_INVALID", failures)
    _require(summary.get("canonical_target_writes") == 0, "SUMMARY_TARGET_WRITES_INVALID", failures)
    for key in ("real_enterprise_connectors", "real_enterprise_data", "enterprise_uat", "production_sla_ha_dr"):
        _require(summary.get(key) == "NOT_RUN", f"SUMMARY_NOT_RUN_BOUNDARY_INVALID:{key}", failures)

    _require(draft.get("schema_version") == "orgrebase.oac-quote-adaptation-draft.v1", "DRAFT_SCHEMA_UNSUPPORTED", failures)
    _require(draft.get("status") == "OWNER_REVIEW_PENDING", "DRAFT_STATUS_INVALID", failures)
    _verify_mapping_set(draft, expect_unknowns=False, failures=failures, label="DRAFT")
    _require(draft.get("gaps") == [], "DRAFT_GAPS_INVALID", failures)
    _require(draft.get("organization_snapshot") == snapshot, "DRAFT_SNAPSHOT_MISMATCH", failures)
    _require(draft.get("organizational_demand") == demand, "DRAFT_DEMAND_MISMATCH", failures)
    _require(draft.get("review_gate") == gate, "DRAFT_GATE_MISMATCH", failures)
    if isinstance(owner_review_summary, dict):
        _require(
            owner_review_summary.get("adaptation_run_id")
            == draft.get("adaptation_run_id"),
            "OWNER_REVIEW_RUN_BINDING_INVALID",
            failures,
        )
        _require(
            owner_review_summary.get("profile_digest") == draft.get("profile_digest"),
            "OWNER_REVIEW_PROFILE_BINDING_INVALID",
            failures,
        )
        _require(
            owner_review_summary.get("pack_digest") == draft.get("pack_digest"),
            "OWNER_REVIEW_PACK_BINDING_INVALID",
            failures,
        )
        _require(
            owner_review_summary.get("candidate_mapping_set_digest")
            == draft.get("mapping_set_digest"),
            "OWNER_REVIEW_MAPPING_BINDING_INVALID",
            failures,
        )
        _require(
            owner_review_summary.get("decision_owner_ref")
            == draft.get("human_authority_ref"),
            "OWNER_REVIEW_DECISION_OWNER_INVALID",
            failures,
        )
        _require(
            owner_review_summary.get("data_class") == "SYNTHETIC_FIXTURE"
            and owner_review_summary.get("synthetic") is True
            and owner_review_summary.get("evidence_label")
            == "CONTROLLED_SYNTHETIC_ENTERPRISE_INPUT",
            "OWNER_REVIEW_EVIDENCE_CLASS_INVALID",
            failures,
        )
        component_reviews = owner_review_summary.get("component_reviews")
        _require(
            isinstance(component_reviews, list)
            and tuple(
                item.get("component_kind")
                for item in component_reviews
                if isinstance(item, dict)
            )
            == KINDS,
            "OWNER_REVIEW_COMPONENT_ORDER_INVALID",
            failures,
        )
        if isinstance(component_reviews, list):
            knowledge = next(
                (
                    item
                    for item in component_reviews
                    if isinstance(item, dict)
                    and item.get("component_kind") == "KNOWLEDGE"
                ),
                None,
            )
            samples = knowledge.get("sample_items") if isinstance(knowledge, dict) else None
            _require(
                isinstance(samples, list)
                and all(
                    isinstance(sample, str)
                    and sample.startswith("field:")
                    and "=" not in sample
                    for sample in samples
                ),
                "OWNER_REVIEW_KNOWLEDGE_PROJECTION_UNSAFE",
                failures,
            )
    public_validation = draft.get("oac_public_validation")
    if isinstance(public_validation, dict):
        _sealed(public_validation, "OAC_PUBLIC_VALIDATION", failures)
        _require(public_validation.get("status") == "PASS", "OAC_PUBLIC_VALIDATION_STATUS_INVALID", failures)
        _require(public_validation.get("canonical_target_writes") == 0, "OAC_PUBLIC_VALIDATION_WRITES_INVALID", failures)
        _require(public_validation.get("organization_snapshot_digest") == snapshot.get("digest"), "OAC_PUBLIC_SNAPSHOT_BINDING_INVALID", failures)
        _require(public_validation.get("organizational_demand_digest") == demand.get("digest"), "OAC_PUBLIC_DEMAND_BINDING_INVALID", failures)
    else:
        failures.append("OAC_PUBLIC_VALIDATION_MISSING")
    for key in ("oac_plan_produced", "oac_plan_certificate_produced", "oac_runtime_invoked"):
        _require(draft.get(key) is False, f"DRAFT_{key.upper()}_INVALID", failures)
    _require(draft.get("canonical_target_writes") == 0, "DRAFT_TARGET_WRITES_INVALID", failures)

    _require(gate.get("adaptation_run_id") == draft.get("adaptation_run_id"), "GATE_RUN_BINDING_INVALID", failures)
    _require(gate.get("mapping_set_digest") == draft.get("mapping_set_digest"), "GATE_MAPPING_BINDING_INVALID", failures)
    _require(gate.get("organization_snapshot_digest") == snapshot.get("digest"), "GATE_SNAPSHOT_BINDING_INVALID", failures)
    _require(gate.get("organizational_demand_digest") == demand.get("digest"), "GATE_DEMAND_BINDING_INVALID", failures)
    _require(
        isinstance(owner_review_summary, dict)
        and gate.get("owner_review_summary_digest")
        == owner_review_summary.get("digest"),
        "GATE_OWNER_REVIEW_SUMMARY_BINDING_INVALID",
        failures,
    )
    _require(gate.get("profile_digest") == draft.get("profile_digest"), "GATE_PROFILE_BINDING_INVALID", failures)
    _require(gate.get("pack_digest") == draft.get("pack_digest"), "GATE_PACK_BINDING_INVALID", failures)
    _require(gate.get("owner_ref") == draft.get("human_authority_ref"), "GATE_OWNER_BINDING_INVALID", failures)
    _require(isinstance(gate.get("review_duration_ms"), int) and gate["review_duration_ms"] >= 4000, "GATE_DURATION_INVALID", failures)
    _require(gate.get("not_before_epoch_ms", 0) - gate.get("prepared_at_epoch_ms", 0) == gate.get("review_duration_ms"), "GATE_TIME_BINDING_INVALID", failures)
    _require(gate.get("canonical_target_writes") == 0, "GATE_TARGET_WRITES_INVALID", failures)

    _require(review.get("review_mode") == "SCRIPTED_CONTROLLED_LOCAL_APPROVAL", "REVIEW_MODE_INVALID", failures)
    _require(review.get("real_enterprise_human_review") == "NOT_RUN", "REVIEW_CLAIM_BOUNDARY_INVALID", failures)
    _require(review.get("observed_elapsed_ms", 0) >= 4000, "REVIEW_ELAPSED_UNDER_FOUR_SECONDS", failures)
    _require(review.get("server_review_duration_ms", 0) >= 4000, "REVIEW_SERVER_GATE_INVALID", failures)
    _require(review.get("early_approval_probe", {}).get("status") == "REJECTED", "REVIEW_EARLY_PROBE_INVALID", failures)
    _require(review.get("early_approval_probe", {}).get("error_code") == "OAC_ADAPTATION_REVIEW_GATE_NOT_READY", "REVIEW_EARLY_CODE_INVALID", failures)
    _require(review.get("approved_actor_id") == draft.get("human_authority_ref"), "REVIEW_ACTOR_INVALID", failures)
    _require(review.get("approved_candidate_digest") == draft.get("mapping_set_digest"), "REVIEW_CANDIDATE_INVALID", failures)
    _require(
        isinstance(owner_review_summary, dict)
        and review.get("approved_owner_review_summary_digest")
        == owner_review_summary.get("digest"),
        "REVIEW_OWNER_SUMMARY_BINDING_INVALID",
        failures,
    )
    _require(
        tuple(review.get("acknowledgements", ())) == OWNER_REVIEW_ACKNOWLEDGEMENTS,
        "REVIEW_ACKNOWLEDGEMENTS_INVALID",
        failures,
    )
    _require(review.get("canonical_target_writes") == 0, "REVIEW_TARGET_WRITES_INVALID", failures)

    _require(approval.get("adaptation_run_id") == draft.get("adaptation_run_id"), "APPROVAL_RUN_BINDING_INVALID", failures)
    _require(approval.get("actor_id") == draft.get("human_authority_ref"), "APPROVAL_OWNER_INVALID", failures)
    _require(approval.get("candidate_digest") == draft.get("mapping_set_digest"), "APPROVAL_CANDIDATE_BINDING_INVALID", failures)
    _require(
        isinstance(owner_review_summary, dict)
        and approval.get("owner_review_summary_digest")
        == owner_review_summary.get("digest"),
        "APPROVAL_OWNER_SUMMARY_BINDING_INVALID",
        failures,
    )
    _require(
        tuple(approval.get("acknowledgements", ()))
        == OWNER_REVIEW_ACKNOWLEDGEMENTS,
        "APPROVAL_ACKNOWLEDGEMENTS_INVALID",
        failures,
    )
    _require(approval.get("review_gate_digest") == gate.get("digest"), "APPROVAL_GATE_BINDING_INVALID", failures)
    _require(approval.get("review_gate") == gate, "APPROVAL_EMBEDDED_GATE_INVALID", failures)
    _require(approval.get("approved_at_epoch_ms", 0) >= gate.get("not_before_epoch_ms", 0), "APPROVAL_BEFORE_NOT_BEFORE", failures)
    _require(approval.get("source_admission_receipt") == source, "APPROVAL_SOURCE_RECEIPT_MISMATCH", failures)
    approval_validation = approval.get("public_validation")
    if isinstance(approval_validation, dict):
        _sealed(approval_validation, "SOURCE_PUBLIC_VALIDATION", failures)
        _require(approval_validation.get("status") == "PASS", "SOURCE_PUBLIC_VALIDATION_STATUS_INVALID", failures)
        _require(approval_validation.get("source_admission_receipt_digest") == source.get("digest"), "SOURCE_PUBLIC_VALIDATION_BINDING_INVALID", failures)
        _require(approval_validation.get("canonical_target_writes") == 0, "SOURCE_PUBLIC_VALIDATION_WRITES_INVALID", failures)
    else:
        failures.append("SOURCE_PUBLIC_VALIDATION_MISSING")
    _require(approval.get("canonical_target_writes") == 0, "APPROVAL_TARGET_WRITES_INVALID", failures)

    _verify_oac_resources(root=root, oac_root=oac_root, snapshot=snapshot, demand=demand, source=source, failures=failures)
    _require(source.get("spec", {}).get("intakeManifestDigest") == draft.get("mapping_set_digest"), "SOURCE_ADMISSION_MAPPING_BINDING_INVALID", failures)
    _verify_parity(parity, draft=draft, golden=golden, failures=failures)

    _require(capsule.get("status") == "READY_FOR_ORGREBASE", "CAPSULE_STATUS_INVALID", failures)
    for field, expected in (
        ("adaptation_run_id", draft.get("adaptation_run_id")),
        ("profile_digest", draft.get("profile_digest")),
        ("pack_digest", draft.get("pack_digest")),
        ("mapping_set_digest", draft.get("mapping_set_digest")),
        (
            "owner_review_summary_digest",
            owner_review_summary.get("digest")
            if isinstance(owner_review_summary, dict)
            else None,
        ),
        ("organization_snapshot_digest", snapshot.get("digest")),
        ("organizational_demand_digest", demand.get("digest")),
        ("source_admission_receipt_digest", source.get("digest")),
        ("approval_digest", approval.get("digest")),
        ("quote_formation_parity_digest", parity.get("digest")),
    ):
        _require(capsule.get(field) == expected, f"CAPSULE_{field.upper()}_MISMATCH", failures)
    _require(capsule.get("formation_authority") == "ORGREBASE_CONTROL_PLANE", "CAPSULE_FORMATION_AUTHORITY_INVALID", failures)
    _require(capsule.get("effect_ceiling") == "ZERO_EXTERNAL_EFFECTS", "CAPSULE_EFFECT_INVALID", failures)
    _require(capsule.get("canonical_target_writes") == 0, "CAPSULE_TARGET_WRITES_INVALID", failures)
    for key in ("oac_plan_produced", "oac_plan_certificate_produced", "oac_runtime_invoked"):
        _require(capsule.get(key) is False, f"CAPSULE_{key.upper()}_INVALID", failures)

    _require(activation.get("adaptation_run_id") == draft.get("adaptation_run_id"), "ACTIVATION_RUN_BINDING_INVALID", failures)
    _require(activation.get("adapter_capsule_digest") == capsule.get("digest"), "ACTIVATION_CAPSULE_BINDING_INVALID", failures)
    _require(activation.get("profile_digest") == draft.get("profile_digest"), "ACTIVATION_PROFILE_BINDING_INVALID", failures)
    _require(activation.get("pack_digest") == draft.get("pack_digest"), "ACTIVATION_PACK_BINDING_INVALID", failures)
    _require(activation.get("binding_timing") == "PRE_EXECUTION_EXACT_BINDING", "ACTIVATION_TIMING_INVALID", failures)
    _require(isinstance(activation.get("execution_run_id"), str) and bool(activation["execution_run_id"]), "ACTIVATION_EXECUTION_RUN_INVALID", failures)
    _require(activation.get("canonical_target_writes") == 0, "ACTIVATION_TARGET_WRITES_INVALID", failures)

    _require(golden.get("schema_version") == "orgrebase.golden-competition-summary.v1", "GOLDEN_SCHEMA_INVALID", failures)
    _require(golden.get("status") == "PASS", "GOLDEN_STATUS_INVALID", failures)
    _require(golden.get("canonical_target_writes") == 0, "GOLDEN_TARGET_WRITES_INVALID", failures)
    live_golden_path = project_root / "evidence/golden-competition/latest/pilot/golden-run/summary.json"
    try:
        live_golden = _load(live_golden_path)
    except (OSError, json.JSONDecodeError, ValueError):
        failures.append("FROZEN_GOLDEN_SOURCE_UNAVAILABLE")
    else:
        _require(live_golden == golden, "FROZEN_GOLDEN_COPY_DRIFT", failures)

    _require(hold.get("status") == "HOLD", "HOLD_STATUS_INVALID", failures)
    _verify_mapping_set(hold, expect_unknowns=True, failures=failures, label="HOLD")
    gaps = hold.get("gaps")
    _require(isinstance(gaps, list) and len(gaps) > 0, "HOLD_GAPS_REQUIRED", failures)
    if isinstance(gaps, list):
        for index, gap in enumerate(gaps):
            if isinstance(gap, dict):
                _sealed(gap, f"HOLD_GAP_{index}", failures)
                _require(gap.get("status") == "OPEN", "HOLD_GAP_STATUS_INVALID", failures)
                _require(gap.get("canonical_target_writes") == 0, "HOLD_GAP_TARGET_WRITES_INVALID", failures)
            else:
                failures.append(f"HOLD_GAP_INVALID:{index}")
    _require(hold.get("pack_digest") is None, "HOLD_PACK_DIGEST_MUST_BE_ABSENT", failures)
    for key in (
        "organization_snapshot",
        "organizational_demand",
        "oac_public_validation",
        "owner_review_summary",
        "review_gate",
    ):
        _require(hold.get(key) is None, f"HOLD_{key.upper()}_MUST_BE_ABSENT", failures)
    _require(hold.get("profile_digest") != draft.get("profile_digest"), "HOLD_PROFILE_NOT_DISTINCT", failures)
    _require(hold.get("canonical_target_writes") == 0, "HOLD_TARGET_WRITES_INVALID", failures)
    _require(hold_rejection.get("status") == "REJECTED", "HOLD_REJECTION_STATUS_INVALID", failures)
    _require(hold_rejection.get("adaptation_run_id") == hold.get("adaptation_run_id"), "HOLD_REJECTION_RUN_INVALID", failures)
    _require(hold_rejection.get("profile_digest") == hold.get("profile_digest"), "HOLD_REJECTION_PROFILE_INVALID", failures)
    _require(hold_rejection.get("error_code") == "OAC_ADAPTATION_HOLD_NOT_APPROVABLE", "HOLD_REJECTION_CODE_INVALID", failures)
    _require(hold_rejection.get("capsule_produced") is False, "HOLD_CAPSULE_CLAIM_INVALID", failures)
    _require(hold_rejection.get("execution_started") is False, "HOLD_EXECUTION_CLAIM_INVALID", failures)
    _require(hold_rejection.get("canonical_target_writes") == 0, "HOLD_REJECTION_WRITES_INVALID", failures)

    evergreen_summary = summary.get("evergreen", {})
    veracier_summary = summary.get("veracier", {})
    formation_summary = summary.get("formation_parity", {})
    boundary = summary.get("oac_boundary", {})
    _require(evergreen_summary.get("status") == "READY_FOR_ORGREBASE", "SUMMARY_EVERGREEN_STATUS_INVALID", failures)
    _require(evergreen_summary.get("adapter_capsule_digest") == capsule.get("digest"), "SUMMARY_CAPSULE_BINDING_INVALID", failures)
    _require(
        isinstance(owner_review_summary, dict)
        and evergreen_summary.get("owner_review_summary_digest")
        == owner_review_summary.get("digest"),
        "SUMMARY_OWNER_REVIEW_BINDING_INVALID",
        failures,
    )
    _require(evergreen_summary.get("activation_binding_digest") == activation.get("digest"), "SUMMARY_ACTIVATION_BINDING_INVALID", failures)
    _require(evergreen_summary.get("bound_execution_started") is False, "SUMMARY_EXECUTION_CLAIM_INVALID", failures)
    _require(veracier_summary.get("status") == "HOLD", "SUMMARY_VERACIER_STATUS_INVALID", failures)
    _require(veracier_summary.get("gap_count") == len(gaps or []), "SUMMARY_VERACIER_GAP_COUNT_INVALID", failures)
    _require(veracier_summary.get("capsule_produced") is False and veracier_summary.get("execution_started") is False, "SUMMARY_VERACIER_CLAIM_INVALID", failures)
    _require(formation_summary.get("authority") == "ORGREBASE_CONTROL_PLANE", "SUMMARY_FORMATION_AUTHORITY_INVALID", failures)
    _require(formation_summary.get("binding_timing") == "POST_RUN_SAME_RUN_REPLAY", "SUMMARY_FORMATION_TIMING_INVALID", failures)
    _require(formation_summary.get("logical_obligation_count") == 5 and formation_summary.get("dynamic_task_attempt_count") == 7, "SUMMARY_FORMATION_COUNTS_INVALID", failures)
    _require(boundary.get("source_and_demand_validated") is True and boundary.get("source_admission_validated") is True, "SUMMARY_OAC_VALIDATION_INVALID", failures)
    _require(boundary.get("oac_plan_produced") is False and boundary.get("oac_plan_certificate_produced") is False and boundary.get("oac_runtime_invoked") is False, "SUMMARY_OAC_CLAIM_BOUNDARY_INVALID", failures)
    _require(summary.get("mutation_count") == 6, "SUMMARY_MUTATION_COUNT_INVALID", failures)

    mutation_rejections = _verify_mutations(
        root,
        oac_root=oac_root,
        draft=draft,
        hold=hold,
        veracier_profile_digest=str(hold.get("profile_digest")),
        failures=failures,
    )
    result_payload = {
        "schema_version": "orgrebase.oac-quote-adaptation-verification.v1",
        "status": "PASS" if not failures else "FAIL",
        "evidence_class": "VALIDATED_CONTROLLED_LOCAL",
        "claim_ceiling": "CONTROLLED_LOCAL_ADAPTATION_MECHANISM_ONLY",
        "manifest_pack_digest": manifest.get("pack_digest"),
        "evergreen_status": draft.get("status") if failures else "READY_FOR_ORGREBASE",
        "veracier_status": hold.get("status"),
        "real_review_wait_ms": review.get("observed_elapsed_ms"),
        "oac_public_cli_checks": 5,
        "mutation_rejections": mutation_rejections,
        "failure_codes": sorted(set(failures), key=str.encode),
        "canonical_target_writes": 0,
        "verification_scope": "RETAINED_ARTIFACT" if oac_root.suffix == ".whl" else "CURRENT_OAC_VALIDATION",
        "current_release_qualified": False,
        "oac_reader": {
            "digest_projection": "LEGACY_TYPED_PROJECTION" if oac_root.suffix == ".whl" else "CURRENT_PUBLIC_CLI",
            "wheel_sha256": RETAINED_OAC_SHA256 if oac_root.suffix == ".whl" else None,
            "python": sys.version.split()[0] if oac_root.suffix == ".whl" else None,
            "pydantic": version("pydantic") if oac_root.suffix == ".whl" else None,
            "rfc8785": version("rfc8785") if oac_root.suffix == ".whl" else None,
            "original_build_environment_reproduced": False,
        },
    }
    return {**result_payload, "digest": _digest(result_payload)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    reader = parser.add_mutually_exclusive_group()
    reader.add_argument("--oac-root", type=Path)
    reader.add_argument("--retained-build", action="store_true",
                        help="Read historical typed-digest evidence with the pinned retained OAC wheel.")
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    arguments = parser.parse_args()
    try:
        root = arguments.root.resolve(strict=True)
        project_root = arguments.project_root.resolve(strict=True)
        oac_root = project_root / RETAINED_OAC_WHEEL if arguments.retained_build else _locate_oac_root(arguments.oac_root)
        if arguments.retained_build:
            _oac_command(oac_root, [])
        result = verify(root, oac_root, project_root)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
