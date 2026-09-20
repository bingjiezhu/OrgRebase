#!/usr/bin/env python3
"""Verify the Spec 045 same-run semifinal closure without product imports."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_MATRIX = {
    "enterprise_quote_operating_model": "VALIDATED_SYNTHETIC_AND_MODELLED",
    "enterprise_shadow_observation_contract": "VALIDATED_SYNTHETIC_CONTRACT",
    "agentteams_native_taskflow": "VALIDATED_CONTROLLED_LOCAL",
    "four_domain_coalition_binding": "VALIDATED_CONTROLLED_LOCAL",
    "skill_package_lifecycle": "VALIDATED_RUN_LOCAL",
    "source_tool_otlp_operations": "VALIDATED_CONTROLLED_LOCAL",
    "workspace_approval_apply_same_run": "NOT_RUN",
    "live_distributed_agentteams": "NOT_RUN",
    "real_enterprise_connectors": "NOT_RUN",
    "real_enterprise_value": "NOT_RUN",
    "production_ha_dr_sla": "NOT_RUN",
}
REQUIRED_COALITION_DOMAINS = ("product", "legal", "finance", "gtm")
REQUIRED_SKILL_VERSIONS = {
    "enterprise-launch-readiness": "1.4.2",
    "enterprise-quote-compose": "1.3.1",
    "structured-domain-handoff": "1.1.2",
}
FORBIDDEN_PUBLIC_PATH_MARKERS = (
    "/" + "Users/",
    "/home/",
    "/root/",
    "/private/var/",
    "/var/folders/",
    "/tmp/",
    ".semifinal-stage-",
    ":\\\\Users\\\\",
)
EXPECTED_OPERATIONS_FILES = frozenset(
    {
        "evidence-index.json",
        "observability/alert-receipt.json",
        "observability/export-receipts.json",
        "observability/logs.otlp.json",
        "observability/metrics.otlp.json",
        "observability/negative-alert-probe.json",
        "observability/privacy-probe.json",
        "observability/privacy-rejection.json",
        "observability/query-receipt.json",
        "observability/retention-receipt.json",
        "observability/telemetry.sqlite",
        "observability/traces.otlp.json",
        "operations/backup-restore-receipt.json",
        "operations/canonical-state.backup.sqlite",
        "operations/canonical-state.restored.sqlite",
        "operations/canonical-state.sqlite",
        "operations/capacity-smoke-receipt.json",
        "operations/deployment-profile.json",
        "operations/sbom.cdx.json",
        "source/health.json",
        "source/not-modified-receipt.json",
        "source/payload.json",
        "source/receipt.json",
        "summary.json",
        "tool/receipt.json",
        "tool/result.json",
    }
)
EXPECTED_SKILL_FILES = frozenset(
    {
        "discovery.json",
        "evaluations/enterprise-launch-readiness.json",
        "evaluations/enterprise-quote-compose.json",
        "evaluations/structured-domain-handoff.json",
        "evidence-index.json",
        "quote-compose/input.json",
        "quote-compose/invocation-receipt.json",
        "quote-compose/result.json",
        "releases/enterprise-launch-readiness.json",
        "releases/enterprise-quote-compose.json",
        "releases/structured-domain-handoff.json",
        "rollback-decision-probes/enterprise-launch-readiness.json",
        "rollback-decision-probes/enterprise-quote-compose.json",
        "rollback-decision-probes/structured-domain-handoff.json",
        "summary.json",
    }
)
EXPECTED_QUOTE_VALUE_FILES = frozenset(
    {
        "evidence-index.json",
        "quote-value-receipt.json",
        "verification.json",
    }
)
EXPECTED_QUOTE_SHADOW_FILES = frozenset(
    {
        "evidence-index.json",
        "quote-shadow-admission-receipt.json",
        "verification.json",
    }
)
RETAINED_QUOTE_VALUE_SOURCE_PATHS = {
    "benchmark_manifest": "benchmark/quote-value-v0.1/MANIFEST.sha256",
    "metric_registry": "configs/workspace/quote-value-metric-registry.json",
    "current_process_baseline": "benchmark/quote-value-v0.1/public/current-process-baseline.json",
    "value_cases": "benchmark/quote-value-v0.1/public/value-cases.json",
    "action_cost_model": "benchmark/quote-value-v0.1/evaluator/action-cost-model.json",
    "evaluation_suite": "evidence/workspace/latest/evaluation-suite.json",
    "product_path_observations": "evidence/workspace/latest/product-path-observations.json",
    "currency_vmrc": "evidence/workspace/latest/rebase/currency-minimal-rebase-certificate.json",
    "launch_vmrc": "evidence/workspace/latest/rebase/launch-minimal-rebase-certificate.json",
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


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _record_ok(value: Any) -> bool:
    return isinstance(value, dict) and value.get("digest") == _digest(
        {key: item for key, item in value.items() if key != "digest"}
    )


def _run_json(arguments: list[str]) -> dict[str, Any]:
    environment = dict(os.environ)
    source_path = str(ROOT / "src")
    environment["PYTHONPATH"] = (
        source_path
        if not environment.get("PYTHONPATH")
        else source_path + os.pathsep + environment["PYTHONPATH"]
    )
    result = subprocess.run(
        [sys.executable, *arguments],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "CHILD_VERIFIER_FAILED:"
            + Path(arguments[0]).name
            + ":"
            + (result.stderr.strip() or result.stdout.strip())
        )
    value = json.loads(result.stdout)
    if not isinstance(value, dict) or value.get("status") != "PASS":
        raise RuntimeError("CHILD_VERIFIER_DID_NOT_PASS:" + Path(arguments[0]).name)
    return value


def _verify_index(root: Path, failures: list[str]) -> dict[str, Any]:
    index = _load(root / "evidence-index.json")
    entries = index.get("entries", []) if isinstance(index, dict) else []
    if not _record_ok(index):
        failures.append("INDEX_DIGEST")
    observed = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path != root / "evidence-index.json"
    }
    indexed = {
        str(item.get("path")) for item in entries if isinstance(item, dict)
    }
    if (
        indexed != observed
        or index.get("entry_count") != len(observed)
        or not isinstance(entries, list)
        or len(entries) != len(observed)
        or any(
            not isinstance(item, dict)
            or set(item) != {"path", "sha256", "bytes"}
            for item in entries
        )
    ):
        failures.append("INDEX_CLOSURE")
    if index.get("pack_digest") != _digest(entries):
        failures.append("PACK_DIGEST")
    for item in entries:
        path = (root / str(item.get("path"))).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError:
            failures.append(f"PATH_ESCAPE:{item.get('path')}")
            continue
        if (
            not path.is_file()
            or item.get("sha256") != _file_digest(path)
            or item.get("bytes") != path.stat().st_size
        ):
            failures.append(f"FILE_BINDING:{item.get('path')}")
    return index


def _public_path_failures(root: Path) -> list[str]:
    """Reject host-specific paths from every UTF-8 publication artifact.

    Runtime code may use real absolute paths while it is executing.  The
    retained competition pack is a portable publication boundary, so those
    paths must be projected to stable public tokens before indexing.
    """

    failures: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        matched = sorted(
            marker for marker in FORBIDDEN_PUBLIC_PATH_MARKERS if marker in text
        )
        if matched:
            failures.append(
                "PUBLIC_HOST_PATH:"
                + path.relative_to(root).as_posix()
                + ":"
                + "|".join(matched)
            )
    return failures


def _publication_surface_failures(
    root: Path,
    *,
    summary: dict[str, Any],
    native: dict[str, Any],
) -> list[str]:
    """Enforce the exact reviewed public artifact surface, not a self-declared set."""

    expected = {
        "evidence-index.json",
        "summary.json",
        "verification/child-verifiers.json",
        "agentteams/action-journal.json",
        "agentteams/coalition-result-binding.json",
        "agentteams/lifecycle-receipt.json",
        "agentteams/source-verification.json",
        "agentteams/runtime/adapter-bin/mc",
        "agentteams/runtime/mc-process-invocations.jsonl",
    }
    wheel_file = summary.get("wheel_file")
    if isinstance(wheel_file, str) and wheel_file.startswith("artifacts/"):
        expected.add(wheel_file)

    project_id = str(native.get("project_id") or "")
    if project_id:
        expected.update(
            {
                f"agentteams/runtime/shared/projects/{project_id}/meta.json",
                f"agentteams/runtime/shared/projects/{project_id}/plan.md",
            }
        )
    for action in native.get("actions", []):
        if not isinstance(action, dict):
            continue
        sequence = action.get("sequence")
        key = action.get("key")
        raw_ref = action.get("raw_ref")
        if isinstance(sequence, int) and isinstance(key, str):
            expected.add(
                f"agentteams/actions/{sequence:03d}-{key.replace(':', '-')}.json"
            )
        if isinstance(raw_ref, str):
            expected.add("agentteams/" + raw_ref)
    for binding in native.get("bindings", []):
        if not isinstance(binding, dict):
            continue
        task_id = binding.get("task_id")
        if not isinstance(task_id, str):
            continue
        expected.update(
            {
                f"agentteams/bindings/{task_id}.json",
                f"agentteams/runtime/shared/tasks/{task_id}/meta.json",
                f"agentteams/runtime/shared/tasks/{task_id}/spec.md",
            }
        )
    expected.update("operations/" + path for path in EXPECTED_OPERATIONS_FILES)
    skill_files = set(EXPECTED_SKILL_FILES)
    skill_summary_path = root / "skills/summary.json"
    if skill_summary_path.is_file() and _load(skill_summary_path).get(
        "schema_version"
    ) == "orgrebase.skill-lifecycle-evidence-summary.v2":
        skill_files.update(
            {
                "distribution/installed-wheel-receipt.json",
                "requalifications/evaluations/enterprise-launch-readiness.json",
                "requalifications/evaluations/enterprise-quote-compose.json",
                "requalifications/evaluations/structured-domain-handoff.json",
            }
        )
    expected.update("skills/" + path for path in skill_files)
    expected.update("quote-value/" + path for path in EXPECTED_QUOTE_VALUE_FILES)
    expected.update("quote-shadow/" + path for path in EXPECTED_QUOTE_SHADOW_FILES)

    observed = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }
    failures = [
        f"PUBLICATION_SURFACE_MISSING:{path}" for path in sorted(expected - observed)
    ]
    failures.extend(
        f"PUBLICATION_SURFACE_EXTRA:{path}" for path in sorted(observed - expected)
    )
    return failures


def _integrated_root_failures(
    *,
    summary: dict[str, Any],
    native: dict[str, Any],
    coalition: dict[str, Any],
    skill: dict[str, Any],
    invocation: dict[str, Any],
    skill_input: dict[str, Any],
    skill_result: dict[str, Any],
    operations: dict[str, Any],
    tool_receipt: dict[str, Any],
    tool_result: dict[str, Any],
) -> list[str]:
    """Independently bind the parent roots across all retained child views."""

    expected_correlation_root = _digest(
        {
            "run_id": summary.get("run_id"),
            "task_id": summary.get("task_id"),
            "delegation_id": summary.get("delegation_id"),
            "coalition_result_binding_digest": coalition.get("coalition_digest"),
            "native_receipt_digest": native.get("receipt_digest"),
            "skill_package_digest": summary.get("skill_package_digest"),
            "skill_invocation_receipt_digest": invocation.get("digest"),
            "graph_digest": native.get("plan_digest"),
        }
    )
    failures: list[str] = []
    if (
        operations.get("native_receipt_digest") != native.get("receipt_digest")
        or operations.get("skill_package_digest")
        != summary.get("skill_package_digest")
        or operations.get("skill_invocation_receipt_digest")
        != invocation.get("digest")
        or operations.get("coalition_result_binding_digest")
        != coalition.get("coalition_digest")
        or operations.get("correlation_root") != expected_correlation_root
        or summary.get("skill_package_digest")
        != skill.get("packages", {}).get("enterprise-quote-compose")
    ):
        failures.append("OPERATIONS_AUTHORITY_ROOT_BINDING")
    dependency_result_digest = _digest(tool_result)
    if (
        tool_receipt.get("response_digest") != dependency_result_digest
        or summary.get("dependency_result_digest") != dependency_result_digest
        or skill_input.get("dependency_result_digest") != dependency_result_digest
        or skill_result.get("dependency_result_digest") != dependency_result_digest
        or skill_input.get("dependency_tool_receipt_digest")
        != tool_receipt.get("digest")
        or skill_result.get("dependency_tool_receipt_digest")
        != tool_receipt.get("digest")
    ):
        failures.append("TOOL_RESULT_SKILL_BINDING")
    return failures


def _skill_distribution_failures(
    *,
    summary: dict[str, Any],
    skill: dict[str, Any],
    discovery: Any,
) -> list[str]:
    """Reject a source-checkout or stale-version substitute for the retained wheel."""

    if not isinstance(discovery, list):
        return ["SKILL_DISCOVERY"]
    by_name = {
        str(item.get("name")): item
        for item in discovery
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    if set(by_name) != set(REQUIRED_SKILL_VERSIONS):
        return ["SKILL_DISCOVERY"]
    if any(
        item.get("version") != REQUIRED_SKILL_VERSIONS[name]
        or item.get("resource_mode") != "INSTALLED_WHEEL"
        or not str(item.get("input_schema_digest", "")).startswith("sha256:")
        or not str(item.get("output_schema_digest", "")).startswith("sha256:")
        or skill.get("packages", {}).get(name) != item.get("package_digest")
        for name, item in by_name.items()
    ):
        return ["SKILL_DISTRIBUTION_BINDING"]
    quote = by_name["enterprise-quote-compose"]
    if (
        skill.get("runtime_resource_mode") != "INSTALLED_WHEEL"
        or skill.get("schema_resolution") != "LOCAL_BUNDLED_ONLY"
        or skill.get("network_schema_resolution") is not False
        or skill.get("schema_resources_per_package") != 2
        or summary.get("skill_runtime_resource_mode") != "INSTALLED_WHEEL"
        or summary.get("quote_skill_version") != quote.get("version")
        or summary.get("skill_package_digest") != quote.get("package_digest")
    ):
        return ["SKILL_DISTRIBUTION_BINDING"]
    return []


def _operations_correlation_failures(
    *,
    native: dict[str, Any],
    coalition: dict[str, Any],
    operations: dict[str, Any],
    gtm_binding: dict[str, Any] | None,
    quote_skill_version: str,
) -> list[str]:
    """Bind OTLP correlation facts to native and Skill authority bytes."""

    if gtm_binding is None:
        return ["OPERATIONS_CORRELATION_FACTS"]
    expected_reassignments = sum(
        isinstance(item, dict) and item.get("predecessor_task_id") is not None
        for item in native.get("bindings", [])
    )
    if (
        operations.get("native_nonce") != native.get("nonce")
        or operations.get("native_nonce_binding") != "BOUND"
        or operations.get("agentteams_project_id") != native.get("project_id")
        or operations.get("agentteams_project_binding") != "BOUND"
        or operations.get("agentteams_attempt_id") != gtm_binding.get("attempt_ref")
        or operations.get("agentteams_attempt_number") != gtm_binding.get("attempt")
        or operations.get("agentteams_attempt_binding") != "BOUND"
        or operations.get("agentteams_retry_count") != "NOT_BOUND"
        or operations.get("agentteams_reassign_count") != expected_reassignments
        or operations.get("skill_name") != "enterprise-quote-compose"
        or operations.get("skill_version") != quote_skill_version
        or operations.get("skill_version_binding") != "BOUND"
        or operations.get("coalition_result_binding_digest")
        != coalition.get("coalition_digest")
    ):
        return ["OPERATIONS_CORRELATION_FACTS"]
    return []


def _coalition_failures(
    *, native: dict[str, Any], coalition: dict[str, Any]
) -> list[str]:
    """Independently reconstruct the four-domain coalition from native bytes."""

    failures: list[str] = []
    native_body = {
        key: value for key, value in native.items() if key != "receipt_digest"
    }
    if native.get("receipt_digest") != _digest(native_body):
        return ["COALITION_NATIVE_RECEIPT_DIGEST"]
    decisions: dict[str, dict[str, Any]] = {}
    for item in native.get("control_decisions", []):
        if not isinstance(item, dict):
            failures.append("COALITION_CONTROL_DECISION")
            continue
        body = {key: value for key, value in item.items() if key != "decision_digest"}
        declared = item.get("decision_digest")
        if declared != _digest(body) or declared in decisions:
            failures.append("COALITION_CONTROL_DECISION")
            continue
        decisions[str(declared)] = item

    expected_members: list[dict[str, Any]] = []
    bindings = native.get("bindings", [])
    for domain in REQUIRED_COALITION_DOMAINS:
        eligible = [
            item
            for item in bindings
            if isinstance(item, dict)
            and item.get("domain") == domain
            and item.get("attempt") == 1
            and item.get("plan_revision") == 1
            and item.get("predecessor_task_id") is None
            and item.get("status") == "completed"
        ]
        if len(eligible) != 1:
            failures.append(f"COALITION_PRIMARY_DOMAIN:{domain}")
            continue
        binding = eligible[0]
        decision = decisions.get(str(binding.get("control_decision_ref")))
        if (
            binding.get("run_id") != native.get("run_id")
            or binding.get("nonce") != native.get("nonce")
            or binding.get("project_id") != native.get("project_id")
            or binding.get("plan_digest") != native.get("plan_digest")
            or binding.get("active_attempt_ref") != binding.get("attempt_ref")
            or binding.get("candidate_only") is not True
            or binding.get("target_writes") != 0
            or decision is None
            or decision.get("verdict") != "ADMIT"
            or decision.get("run_id") != native.get("run_id")
            or decision.get("nonce") != native.get("nonce")
            or decision.get("project_id") != native.get("project_id")
            or decision.get("task_id") != binding.get("task_id")
            or decision.get("domain") != domain
            or decision.get("attempt") != 1
            or decision.get("expected_binding_digest")
            != binding.get("admission_binding_digest")
            or decision.get("observed_result_digest")
            != binding.get("observed_result_digest")
            or decision.get("candidate_only") is not True
            or decision.get("target_writes") != 0
        ):
            failures.append(f"COALITION_DOMAIN_ADMISSION:{domain}")
            continue
        expected_members.append(
            {
                "domain": domain,
                "task_id": binding.get("task_id"),
                "attempt": 1,
                "plan_revision": 1,
                "task_binding_digest": _digest(binding),
                "admission_binding_digest": binding.get(
                    "admission_binding_digest"
                ),
                "delegation_digest": binding.get("delegation_digest"),
                "context_projection_digest": binding.get(
                    "context_projection_digest"
                ),
                "source_lock_digest": binding.get("source_lock_digest"),
                "observed_result_digest": binding.get("observed_result_digest"),
                "control_decision_ref": binding.get("control_decision_ref"),
                "status": "completed",
                "candidate_only": True,
                "target_writes": 0,
            }
        )
    expected_base = {
        "schema_version": "orgrebase.workspace-coalition-result-binding.v1",
        "evidence_class": "CONTROLLED_LOCAL_FOUR_DOMAIN_CANDIDATE_COMPOSITION",
        "claim_boundary": (
            "FOUR_ADMITTED_CONTROLLED_CANDIDATES_NOT_INDEPENDENT_AGENT_REASONING"
        ),
        "run_id": native.get("run_id"),
        "nonce": native.get("nonce"),
        "project_id": native.get("project_id"),
        "plan_digest": native.get("plan_digest"),
        "native_receipt_digest": native.get("receipt_digest"),
        "members": expected_members,
        "candidate_only": True,
        "target_writes": 0,
    }
    expected = {**expected_base, "coalition_digest": _digest(expected_base)}
    if coalition != expected:
        failures.append("COALITION_NATIVE_RECONSTRUCTION")
    return failures


def _wheel_sbom_failures(
    root: Path,
    *,
    summary: dict[str, Any],
    sbom: dict[str, Any],
    require_current_build: bool = True,
) -> list[str]:
    relative = summary.get("wheel_file")
    if not isinstance(relative, str) or not relative:
        return ["WHEEL_BINDING"]
    wheel = (root / relative).resolve()
    try:
        wheel.relative_to(root.resolve())
    except ValueError:
        return ["WHEEL_PATH_ESCAPE"]
    if not wheel.is_file() or wheel.suffix != ".whl":
        return ["WHEEL_BINDING"]
    wheel_digest = _file_digest(wheel)
    properties = {
        item.get("name"): item.get("value")
        for item in sbom.get("metadata", {}).get("component", {}).get("properties", [])
        if isinstance(item, dict)
    }
    expected = {
        f"orgrebase.artifact.{wheel.name}.sha256": wheel_digest.split(":", 1)[1],
        f"orgrebase.artifact.{wheel.name}.bytes": str(wheel.stat().st_size),
    }
    if summary.get("wheel_sha256") != wheel_digest or any(
        properties.get(key) != value for key, value in expected.items()
    ):
        return ["WHEEL_SBOM_BINDING"]
    binding = _current_build_binding(sbom)
    if any(item["status"] == "INVALID_RETAINED_DIGEST" for item in binding["files"]):
        return ["WHEEL_SBOM_BUILD_DIGEST_INVALID"]
    if require_current_build and binding["status"] != "MATCH":
        return ["WHEEL_CURRENT_BUILD_BINDING"]
    return []


def _current_build_binding(sbom: dict[str, Any]) -> dict[str, Any]:
    properties = {
        item.get("name"): item.get("value")
        for item in sbom.get("metadata", {}).get("component", {}).get("properties", [])
        if isinstance(item, dict)
    }
    files = []
    for filename, property_name in (
        ("uv.lock", "orgrebase.uv-lock.sha256"),
        ("pyproject.toml", "orgrebase.pyproject.sha256"),
    ):
        recorded = properties.get(property_name)
        try:
            current = hashlib.sha256((ROOT / filename).read_bytes()).hexdigest()
        except OSError:
            current = None
        if not isinstance(recorded, str) or re.fullmatch(r"[0-9a-f]{64}", recorded) is None:
            status = "INVALID_RETAINED_DIGEST"
        elif current is None:
            status = "CURRENT_FILE_UNAVAILABLE"
        else:
            status = "MATCH" if current == recorded else "MISMATCH"
        files.append({"path": filename, "retained_sha256": recorded,
                      "current_sha256": current, "status": status})
    return {"status": "MATCH" if all(item["status"] == "MATCH" for item in files) else "MISMATCH",
            "files": files, "scope": "UV_LOCK_AND_PROJECT_METADATA_ONLY"}


def _retained_quote_value_inputs(
    pack: Path, snapshot: Path, receipt: dict[str, Any], failures: list[str],
) -> tuple[Path, dict[str, Any]]:
    """Bind a separate input snapshot without changing the sealed parent pack."""

    def require(condition: bool, code: str) -> None:
        if not condition:
            raise ValueError("RETAINED_QUOTE_VALUE_INPUTS_" + code)

    snapshot = snapshot.absolute()
    require(not any(path.is_symlink() for path in (snapshot, *snapshot.parents)), "SYMLINK")
    require(snapshot.is_dir(), "MISSING")
    require(not snapshot.resolve().is_relative_to(pack.resolve()), "INSIDE_PARENT_PACK")
    observed: set[str] = set()
    for path in snapshot.rglob("*"):
        require(not path.is_symlink(), "SYMLINK")
        require(path.is_file() or path.is_dir(), "SPECIAL_FILE")
        require(path.resolve().is_relative_to(snapshot.resolve()), "PATH_ESCAPE")
        if path.is_file():
            observed.add(path.relative_to(snapshot).as_posix())
    expected = {"manifest.json", *("source-root/" + path for path in RETAINED_QUOTE_VALUE_SOURCE_PATHS.values())}
    require(observed == expected, "FILE_SET")
    manifest = _load(snapshot / "manifest.json")
    require(isinstance(manifest, dict) and set(manifest) == {
        "schema_version", "verification_scope", "current_release_qualified",
        "parent_index_file_sha256", "parent_pack_digest", "receipt_file_sha256",
        "receipt_digest", "recovery_method", "source_execution_replayed", "sources", "digest",
    }, "MANIFEST_SHAPE")
    require(_record_ok(manifest), "MANIFEST_DIGEST")
    require(
        manifest["schema_version"] == "orgrebase.retained-quote-value-inputs.v1"
        and manifest["verification_scope"] == "RETAINED_ARTIFACT"
        and manifest["current_release_qualified"] is False
        and manifest["recovery_method"] == "BYTE_IDENTICAL_COPY_VALIDATED_AGAINST_RETAINED_RECEIPT"
        and manifest["source_execution_replayed"] is False,
        "SCOPE",
    )
    if (
        manifest["parent_index_file_sha256"] != _file_digest(pack / "evidence-index.json")
        or manifest["parent_pack_digest"] != _load(pack / "evidence-index.json").get("pack_digest")
    ):
        # Keep evaluating independent semantics so a reindexed attack also
        # exposes its original failure, rather than only the archive binding.
        failures.append("RETAINED_QUOTE_VALUE_INPUTS_PARENT_BINDING")
    require(
        manifest["receipt_file_sha256"] == _file_digest(pack / "quote-value/quote-value-receipt.json")
        and manifest["receipt_digest"] == receipt.get("digest"),
        "RECEIPT_BINDING",
    )
    sources = receipt.get("source_evidence")
    require(isinstance(sources, list) and len(sources) == len(RETAINED_QUOTE_VALUE_SOURCE_PATHS), "SOURCE_SET")
    entries = manifest["sources"]
    require(isinstance(entries, list) and len(entries) == len(sources), "SOURCE_SET")
    for source, entry, (source_id, relative) in zip(sources, entries, RETAINED_QUOTE_VALUE_SOURCE_PATHS.items(), strict=True):
        require(
            isinstance(source, dict)
            and set(source) == {"id", "path", "file_sha256", "evidence_class"}
            and source.get("id") == source_id and source.get("path") == relative,
            "SOURCE_PATH",
        )
        require(isinstance(entry, dict) and set(entry) == {
            "id", "path", "file_sha256", "evidence_class", "bytes", "recovered_from",
        }, "SOURCE_SHAPE")
        require({key: entry[key] for key in source} == source, "RECEIPT_SOURCE_BINDING")
        recovered_from = entry["recovered_from"]
        require(
            isinstance(recovered_from, str) and bool(recovered_from)
            and not Path(recovered_from).is_absolute()
            and ".." not in Path(recovered_from).parts and "\\" not in recovered_from,
            "RECOVERY_PATH",
        )
        path = snapshot / "source-root" / relative
        require(
            type(entry["bytes"]) is int and entry["bytes"] == path.stat().st_size
            and entry["file_sha256"] == _file_digest(path),
            "SOURCE_BYTES",
        )
    return snapshot / "source-root", {
        "scope": "RETAINED_CONTENT_ADDRESSED_INPUTS",
        "manifest_digest": manifest["digest"],
        "receipt_file_sha256": manifest["receipt_file_sha256"],
        "source_count": len(sources),
        "current_release_qualified": False,
    }


def verify(
    root: Path, *, checkout: Path | None = None, require_current_build: bool = True,
    lock_path: Path | None = None, retained_quote_value_inputs: Path | None = None,
) -> dict[str, Any]:
    if require_current_build and retained_quote_value_inputs is not None:
        raise SystemExit("SEMIFINAL_CLOSURE_VERIFY_FAILED:RETAINED_INPUTS_IN_CURRENT_MODE")
    if not require_current_build and retained_quote_value_inputs is None:
        raise SystemExit("SEMIFINAL_CLOSURE_VERIFY_FAILED:RETAINED_QUOTE_VALUE_INPUTS_REQUIRED")
    failures: list[str] = []
    index = _verify_index(root, failures)
    failures.extend(_public_path_failures(root))
    summary = _load(root / "summary.json")
    if (
        not _record_ok(summary)
        or summary.get("status") != "PASS"
        or summary.get("evidence_class")
        != "CONTROLLED_LOCAL_INTEGRATED_VERTICAL_SLICE"
        or summary.get("terminal_state") != "CANDIDATE_ACCEPTED"
        or summary.get("canonical_target_writes") != 0
        or summary.get("production_readiness") is not False
        or summary.get("completion_matrix") != REQUIRED_MATRIX
    ):
        failures.append("SUMMARY")

    native = _load(root / "agentteams/lifecycle-receipt.json")
    coalition = _load(root / "agentteams/coalition-result-binding.json")
    failures.extend(
        _publication_surface_failures(root, summary=summary, native=native)
    )
    skill = _load(root / "skills/summary.json")
    skill_discovery = _load(root / "skills/discovery.json")
    skill_input = _load(root / "skills/quote-compose/input.json")
    invocation = _load(root / "skills/quote-compose/invocation-receipt.json")
    skill_result = _load(root / "skills/quote-compose/result.json")
    operations = _load(root / "operations/summary.json")
    source_receipt = _load(root / "operations/source/receipt.json")
    source_payload = _load(root / "operations/source/payload.json")
    tool_receipt = _load(root / "operations/tool/receipt.json")
    tool_result = _load(root / "operations/tool/result.json")
    sbom = _load(root / "operations/operations/sbom.cdx.json")
    quote_value = _load(root / "quote-value/quote-value-receipt.json")
    quote_value_root = ROOT
    quote_value_input_binding: dict[str, Any] = {"scope": "CURRENT_PROJECT_INPUTS"}
    if retained_quote_value_inputs is not None:
        try:
            quote_value_root, quote_value_input_binding = _retained_quote_value_inputs(
                root, retained_quote_value_inputs, quote_value, failures,
            )
        except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
            raise SystemExit("SEMIFINAL_CLOSURE_VERIFY_FAILED:" + str(exc)) from exc
    quote_shadow = _load(
        root / "quote-shadow/quote-shadow-admission-receipt.json"
    )

    run_id = str(summary.get("run_id") or "")
    task_id = str(summary.get("task_id") or "")
    delegation_id = str(summary.get("delegation_id") or "")
    common_runs = {
        native.get("run_id"),
        coalition.get("run_id"),
        skill.get("run_id"),
        invocation.get("run_id"),
        operations.get("run_id"),
        source_receipt.get("run_id"),
        tool_receipt.get("run_id"),
    }
    if common_runs != {run_id}:
        failures.append("SAME_RUN_BINDING")
    failures.extend(_coalition_failures(native=native, coalition=coalition))
    if {
        skill.get("task_id"),
        invocation.get("task_id"),
        operations.get("task_id"),
    } != {task_id}:
        failures.append("SAME_TASK_BINDING")
    if {
        skill.get("delegation_id"),
        invocation.get("delegation_id"),
        operations.get("delegation_id"),
    } != {delegation_id}:
        failures.append("SAME_DELEGATION_BINDING")

    gtm_bindings = [
        item
        for item in native.get("bindings", [])
        if isinstance(item, dict)
        and item.get("domain") == "gtm"
        and item.get("attempt") == 1
        and str(item.get("status", "")).upper() == "COMPLETED"
    ]
    if (
        len(gtm_bindings) != 1
        or gtm_bindings[0].get("task_id") != task_id
        or gtm_bindings[0].get("delegation_digest") != delegation_id
        or gtm_bindings[0].get("run_id") != run_id
        or gtm_bindings[0].get("target_writes") != 0
    ):
        failures.append("GTM_BINDING_DERIVATION")
    gtm_binding = gtm_bindings[0] if len(gtm_bindings) == 1 else None

    failures.extend(
        _skill_distribution_failures(
            summary=summary,
            skill=skill,
            discovery=skill_discovery,
        )
    )
    failures.extend(
        _operations_correlation_failures(
            native=native,
            coalition=coalition,
            operations=operations,
            gtm_binding=gtm_binding,
            quote_skill_version=str(summary.get("quote_skill_version") or ""),
        )
    )

    if (
        summary.get("source_receipt_digest") != source_receipt.get("digest")
        or summary.get("source_receipt_digest")
        != operations.get("source_receipt_digest")
        or summary.get("source_payload_digest") != _digest(source_payload)
        or native.get("nonce")
        != _digest(
            {
                "run_id": run_id,
                "source_receipt_digest": source_receipt.get("digest"),
                "source_payload_digest": _digest(source_payload),
            }
        ).split(":", 1)[1]
        or summary.get("native_nonce") != native.get("nonce")
        or source_receipt.get("run_id") != run_id
        or summary.get("native_receipt_digest") != native.get("receipt_digest")
        or summary.get("coalition_result_binding_digest")
        != coalition.get("coalition_digest")
        or summary.get("coalition_member_count") != len(coalition.get("members", []))
        or summary.get("native_source_commit")
        != native.get("source_verification", {}).get("commit")
        or summary.get("skill_summary_digest") != skill.get("digest")
        or summary.get("skill_invocation_receipt_digest") != invocation.get("digest")
        or summary.get("operations_summary_digest") != operations.get("digest")
        or summary.get("tool_receipt_digest") != tool_receipt.get("digest")
        or summary.get("quote_value_receipt_digest") != quote_value.get("digest")
        or summary.get("quote_shadow_admission_receipt_digest")
        != quote_shadow.get("digest")
        or summary.get("graph_digest") != native.get("plan_digest")
        or summary.get("graph_digest") != operations.get("graph_digest")
    ):
        failures.append("CHILD_RECEIPT_BINDING")
    failures.extend(
        _integrated_root_failures(
            summary=summary,
            native=native,
            coalition=coalition,
            skill=skill,
            invocation=invocation,
            skill_input=skill_input,
            skill_result=skill_result,
            operations=operations,
            tool_receipt=tool_receipt,
            tool_result=tool_result,
        )
    )
    failures.extend(_wheel_sbom_failures(
        root, summary=summary, sbom=sbom, require_current_build=require_current_build,
    ))
    if (
        invocation.get("target_writes") != 0
        or skill_result.get("candidate_only") is not True
        or skill_result.get("target_writes") != 0
        or invocation.get("input_digest") != _digest(skill_input)
        or invocation.get("output_digest") != _digest(skill_result)
        or invocation.get("package_digest") != summary.get("skill_package_digest")
        or operations.get("canonical_target_writes") != 0
        or operations.get("approval_apply_status") != "NOT_RUN"
    ):
        failures.append("AUTHORITY_BOUNDARY")
    if (
        skill_input.get("dependency_tool_receipt_digest") != tool_receipt.get("digest")
        or skill_result.get("dependency_tool_receipt_digest")
        != tool_receipt.get("digest")
        or skill_input.get("dependency_result_digest")
        != summary.get("dependency_result_digest")
        or skill_result.get("dependency_result_digest")
        != summary.get("dependency_result_digest")
    ):
        failures.append("SKILL_TOOL_BINDING")
    expected_domain_results = {
        item.get("domain"): item.get("observed_result_digest")
        for item in coalition.get("members", [])
        if isinstance(item, dict)
    }
    if (
        skill_input.get("coalition_result_binding_digest")
        != coalition.get("coalition_digest")
        or skill_result.get("coalition_result_binding_digest")
        != coalition.get("coalition_digest")
        or skill_input.get("domain_result_digests") != expected_domain_results
        or skill_result.get("domain_result_digests") != expected_domain_results
    ):
        failures.append("SKILL_COALITION_BINDING")
    value_metrics = {
        item.get("metric_id"): item
        for item in quote_value.get("metrics", [])
        if isinstance(item, dict)
    }
    enterprise_not_run_metrics = {
        "quote_cycle_elapsed_minutes",
        "policy_confirmation_active_minutes",
        "first_pass_rework_rate",
    }
    if (
        quote_value.get("evidence_ceiling") != "SYNTHETIC_CONTROLLED_VALUE_PROOF"
        or any(
            value_metrics.get(metric_id, {}).get("status") != "NOT_RUN"
            for metric_id in enterprise_not_run_metrics
        )
        or summary.get("business_value_same_run") is not False
        or quote_shadow.get("evidence_ceiling") != "SYNTHETIC_CONTRACT_FIXTURE"
        or quote_shadow.get("measurement_status") != "CALCULATED"
        or len(quote_shadow.get("metrics", [])) != 7
        or any(
            not isinstance(item, dict)
            or item.get("status") != "CALCULATED"
            or item.get("evidence_class") != "SYNTHETIC_CONTRACT_FIXTURE"
            for item in quote_shadow.get("metrics", [])
        )
        or summary.get("quote_shadow_evidence_ceiling")
        != "SYNTHETIC_CONTRACT_FIXTURE"
    ):
        failures.append("VALUE_CLAIM_BOUNDARY")

    native_arguments = [
        str(ROOT / "scripts/verify_native_taskflow_evidence.py"),
        "--evidence",
        str(root / "agentteams"),
        "--checkout",
        "",
    ]
    if checkout is not None:
        native_arguments[-1] = str(checkout)
    if lock_path is not None:
        native_arguments.extend(("--lock", str(lock_path)))
    try:
        child_results = {
            "native": _run_json(native_arguments),
            "skills": _run_json(
                [
                    str(ROOT / "scripts/verify_skill_package_evidence.py"),
                    "--evidence",
                    str(root / "skills"),
                ]
            ),
            "operations": _run_json(
                [
                    str(ROOT / "scripts/verify_controlled_local_evidence.py"),
                    "--evidence",
                    str(root / "operations"),
                ]
            ),
            "quote_value": _run_json(
                [
                    str(ROOT / "scripts/verify_quote_value_evidence.py"),
                    "--receipt",
                    str(root / "quote-value/quote-value-receipt.json"),
                    "--project-root",
                    str(quote_value_root),
                ]
            ),
            "quote_shadow": _run_json(
                [
                    str(ROOT / "scripts/verify_quote_shadow_admission.py"),
                    "--record",
                    str(
                        ROOT
                        / "benchmark/quote-value-v0.2-shadow/synthetic/observation-record.json"
                    ),
                    "--source-root",
                    str(ROOT / "benchmark/quote-value-v0.2-shadow"),
                    "--receipt",
                    str(root / "quote-shadow/quote-shadow-admission-receipt.json"),
                ]
            ),
        }
    except (RuntimeError, json.JSONDecodeError) as exc:
        failures.append(str(exc))
        child_results = {}

    retained_verification = _load(root / "verification/child-verifiers.json")
    normalized = {
        name: {
            key: value
            for key, value in result.items()
            if key
            in {
                "status",
                "run_id",
                "receipt_digest",
                "quote_invocation_receipt",
                "pack_digest",
                "evidence_class",
                "claim_ceiling",
            }
        }
        for name, result in child_results.items()
    }
    if retained_verification != normalized:
        failures.append("CHILD_VERIFIER_REPLAY")

    if failures:
        raise SystemExit("SEMIFINAL_CLOSURE_VERIFY_FAILED:" + ",".join(failures))
    return {
        "status": "PASS",
        "run_id": run_id,
        "task_id": task_id,
        "delegation_id": delegation_id,
        "evidence_class": summary["evidence_class"],
        "terminal_state": summary["terminal_state"],
        "canonical_target_writes": 0,
        "production_readiness": False,
        "verification_scope": "CURRENT_BUILD_INPUTS" if require_current_build else "RETAINED_ARTIFACT",
        "current_build_binding": _current_build_binding(sbom),
        "current_release_qualified": False,
        "quote_value_input_binding": quote_value_input_binding,
        "native_verification_strength": child_results["native"].get(
            "verification_strength"
        ),
        "pack_digest": index["pack_digest"],
        "agentteams_commit": native["source_verification"]["commit"],
        "source_lock_digest": native["source_verification"]["source_lock_digest"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--lock", type=Path, help="Exact source lock for the retained native evidence")
    parser.add_argument(
        "--retained-build", action="store_true",
        help="Verify the frozen wheel/SBOM without claiming they describe the current checkout; report input drift separately.",
    )
    parser.add_argument(
        "--retained-quote-value-inputs", type=Path,
        help="Required with --retained-build: separately bound historical QuoteValue input snapshot.",
    )
    parser.add_argument(
        "--checkout",
        type=Path,
        help=(
            "Optional pinned AgentTeams checkout for source-byte replay; without it "
            "the native child verifier uses the retained lock record."
        ),
    )
    args = parser.parse_args()
    checkout = args.checkout.resolve() if args.checkout is not None else None
    print(
        json.dumps(
            verify(args.evidence.resolve(), checkout=checkout, require_current_build=not args.retained_build,
                   lock_path=args.lock, retained_quote_value_inputs=args.retained_quote_value_inputs),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
