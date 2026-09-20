"""Independent evaluator for versioned ProductPath black-box observations.

This module intentionally imports no ``orgrebase`` package.  It recomputes
public export digests with RFC 8785 and evaluates only HTTP-observable values.
"""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

import rfc8785

ZERO_DIGEST = "sha256:" + "0" * 64
SOURCE_BOUND_BENCHMARK_VERSION = "ProductPath-v0.2-source-bound"
DEFAULT_BENCHMARK_VERSION = "ProductPath-v0.3-task-intake-bound"
CURRENT_STATE_SCHEMA = "orgrebase.workspace-state.v2"
CURRENT_RUNTIME_CONTRACT = "orgrebase.product-path-runtime-contract.v2"
SUPPORTED_BENCHMARK_VERSIONS = {
    "ProductPath-v0.1": 1,
    SOURCE_BOUND_BENCHMARK_VERSION: 2,
    DEFAULT_BENCHMARK_VERSION: 2,
}
ARTIFACT_IDS = (
    "benchmark_manifest",
    "public_cases",
    "evaluator_gold",
    "evaluator_mutations",
    "runner_source",
    "evaluator_source",
    "packaged_wheel",
    "raw_observations",
)
BASE_CORPUS_PATHS = (
    "public/cases.json",
    "evaluator/gold.json",
    "evaluator/mutations.json",
)


def _protocol_revision(benchmark_version: str) -> int:
    try:
        return SUPPORTED_BENCHMARK_VERSIONS[benchmark_version]
    except KeyError as exc:
        raise ValueError(f"unsupported ProductPath benchmark: {benchmark_version}") from exc


def _corpus_paths(benchmark_version: str) -> tuple[str, ...]:
    if _protocol_revision(benchmark_version) == 1:
        return BASE_CORPUS_PATHS
    return ("BENCHMARK-MIGRATION.json", *BASE_CORPUS_PATHS)


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _product_import_count(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            count += sum(alias.name == "orgrebase" or alias.name.startswith("orgrebase.") for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            count += module == "orgrebase" or module.startswith("orgrebase.")
    return count


def _manifest_status(path: Path, *, benchmark_version: str) -> dict[str, Any]:
    root = path.parent.resolve()
    entries: list[dict[str, Any]] = []
    failures: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            expected, relative = line.split("  ", 1)
        except ValueError:
            failures.append(f"INVALID_MANIFEST_LINE:{line}")
            continue
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            failures.append(f"MANIFEST_PATH_ESCAPE:{relative}")
            continue
        if not target.is_file():
            observed = None
            failures.append(f"MANIFEST_FILE_MISSING:{relative}")
        else:
            observed = hashlib.sha256(target.read_bytes()).hexdigest()
            if observed != expected:
                failures.append(f"MANIFEST_DIGEST_MISMATCH:{relative}")
        entries.append(
            {
                "path": relative,
                "expected_sha256": expected,
                "observed_sha256": observed,
            }
        )
    observed_paths = [entry["path"] for entry in entries]
    if observed_paths != list(_corpus_paths(benchmark_version)):
        failures.append("MANIFEST_CORPUS_SET_INVALID")
    return {
        "status": "PASS" if entries and not failures else "FAIL",
        "entries": entries,
        "failures": failures,
        "manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _diff_paths(before: Any, after: Any, *, prefix: str = "") -> set[str]:
    """Return exact changed JSON Pointer leaves, including list additions."""

    if isinstance(before, dict) and isinstance(after, dict):
        changes: set[str] = set()
        for key in sorted(set(before) | set(after)):
            child = f"{prefix}/{_pointer_token(str(key))}"
            if key not in before or key not in after:
                changes.add(child)
            else:
                changes.update(_diff_paths(before[key], after[key], prefix=child))
        return changes
    if isinstance(before, list) and isinstance(after, list):
        changes = set()
        for index in range(max(len(before), len(after))):
            child = f"{prefix}/{index}"
            if index >= len(before) or index >= len(after):
                changes.add(child)
            else:
                changes.update(_diff_paths(before[index], after[index], prefix=child))
        return changes
    return set() if before == after else {prefix or "/"}


def _resolve_pointer(value: Any, pointer: str) -> Any:
    cursor = value
    for encoded in pointer.lstrip("/").split("/") if pointer != "/" else ():
        token = encoded.replace("~1", "/").replace("~0", "~")
        cursor = cursor[int(token)] if isinstance(cursor, list) else cursor[token]
    return cursor


def _migration_status(
    *,
    project_root: Path,
    benchmark_root: Path,
    benchmark_version: str,
) -> dict[str, Any]:
    if benchmark_version == "ProductPath-v0.1":
        return {
            "status": "PASS",
            "applicability": "HISTORICAL_BASELINE",
            "failures": [],
        }
    migration_policy = {
        SOURCE_BOUND_BENCHMARK_VERSION: {
            "applicability": "SOURCE_BOUND_UPGRADE",
            "predecessor_version": "ProductPath-v0.1",
            "spec_ref": "Spec037/F1b",
            "corpus_paths": {
                "manifest_sha256": "MANIFEST.sha256",
                "public_cases_sha256": "public/cases.json",
                "evaluator_gold_sha256": "evaluator/gold.json",
                "evaluator_mutations_sha256": "evaluator/mutations.json",
            },
        },
        DEFAULT_BENCHMARK_VERSION: {
            "applicability": "TASK_INTAKE_BOUND_UPGRADE",
            "predecessor_version": SOURCE_BOUND_BENCHMARK_VERSION,
            "spec_ref": "Spec077/ProductPath-v0.3",
            "corpus_paths": {
                "manifest_sha256": "MANIFEST.sha256",
                "benchmark_migration_sha256": "BENCHMARK-MIGRATION.json",
                "public_cases_sha256": "public/cases.json",
                "evaluator_gold_sha256": "evaluator/gold.json",
                "evaluator_mutations_sha256": "evaluator/mutations.json",
            },
        },
    }.get(benchmark_version)
    if migration_policy is None:  # pragma: no cover - guarded by protocol lookup
        raise ValueError(f"unsupported ProductPath benchmark: {benchmark_version}")
    applicability = str(migration_policy["applicability"])
    failures: list[str] = []
    path = benchmark_root / "BENCHMARK-MIGRATION.json"
    if not path.is_file():
        return {
            "status": "FAIL",
            "applicability": applicability,
            "failures": ["MIGRATION_RECORD_MISSING"],
        }
    try:
        migration = _object(path)
        predecessor = migration["predecessor"]
        corpus = predecessor["corpus"]
        predecessor_root = (project_root / predecessor["benchmark_root"]).resolve()
        predecessor_root.relative_to(project_root.resolve())
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
        return {
            "status": "FAIL",
            "applicability": applicability,
            "failures": ["MIGRATION_RECORD_INVALID"],
        }
    if (
        migration.get("schema_version")
        != "orgrebase.product-path-benchmark-migration.v1"
        or migration.get("benchmark_version") != benchmark_version
        or predecessor.get("benchmark_version")
        != migration_policy["predecessor_version"]
        or migration.get("rationale", {}).get("spec_ref")
        != migration_policy["spec_ref"]
    ):
        failures.append("MIGRATION_METADATA_INVALID")
    predecessor_paths = {
        key: predecessor_root / str(relative)
        for key, relative in migration_policy["corpus_paths"].items()
    }
    if not isinstance(corpus, dict) or set(corpus) != set(predecessor_paths):
        failures.append("MIGRATION_PREDECESSOR_CORPUS_SET_INVALID")
        corpus = corpus if isinstance(corpus, dict) else {}
    for key, predecessor_path in predecessor_paths.items():
        observed = _file_digest(predecessor_path) if predecessor_path.is_file() else None
        if corpus.get(key) != observed:
            failures.append(f"MIGRATION_PREDECESSOR_DIGEST_MISMATCH:{key}")
    retained_wheel = predecessor.get("retained_wheel", {})
    if benchmark_version == SOURCE_BOUND_BENCHMARK_VERSION:
        if (
            retained_wheel.get("sha256")
            != "sha256:444d00125c77d63c77b393730a8cf158a8045a6647b9773a99a53053bcb8e89e"
            or not isinstance(retained_wheel.get("archive_member"), str)
        ):
            failures.append("MIGRATION_RETAINED_WHEEL_BINDING_INVALID")
    elif retained_wheel:
        failures.append("MIGRATION_UNSCOPED_RETAINED_WHEEL_INVALID")
    source_contract = migration.get("source_bound_contract")
    exact_contract_keys = {
        "profile_ref",
        "profile_digest",
        "admission_receipt_digest",
        "source_admission_receipt_digest",
        "runtime_projection_receipt_digest",
        "runtime_projection_digest",
        "binding_artifact_digest",
        "authority_assurance",
        "root_digests",
        "projection_digests",
    }
    digest_keys = exact_contract_keys - {
        "profile_ref",
        "authority_assurance",
        "root_digests",
        "projection_digests",
    }
    if not isinstance(source_contract, dict) or set(source_contract) != exact_contract_keys:
        failures.append("MIGRATION_SOURCE_BOUND_CONTRACT_INVALID")
        source_contract = {}
    digest_values = [source_contract.get(key) for key in digest_keys]
    root_digests = source_contract.get("root_digests")
    projection_digests = source_contract.get("projection_digests")
    if (
        any(
            not isinstance(value, str)
            or len(value) != 71
            or not value.startswith("sha256:")
            for value in digest_values
        )
        or not isinstance(root_digests, dict)
        or set(root_digests) != SOURCE_COMPONENT_KINDS
        or not isinstance(projection_digests, dict)
        or set(projection_digests) != SOURCE_COMPONENT_KINDS
        or any(
            not isinstance(value, str)
            or len(value) != 71
            or not value.startswith("sha256:")
            for value in [*root_digests.values(), *projection_digests.values()]
        )
    ):
        failures.append("MIGRATION_SOURCE_BOUND_DIGEST_SET_INVALID")

    task_intake_contract = migration.get("task_intake_bound_contract")
    if benchmark_version == DEFAULT_BENCHMARK_VERSION:
        expected_task_intake_contract = {
            "candidate_status": "READY_FOR_CONFIRMATION",
            "confirmation_status": "ADMITTED_FOR_FORMATION",
            "run_status": "FORMATION_COMPLETED",
            "actor_id": "employee:sales-owner",
            "customer_id": "customer:acme",
            "deliverable_kind": "QUOTE",
            "natural_language_authority": False,
            "canonical_target_writes": 0,
            "event_type": "WORKSPACE_TASK_INTAKE_BOUND",
            "review_duration_ms": 4000,
            "review_gate_store": "FILE_BACKED_SQLITE_ARTIFACT",
            "review_gate_restart_enforced": True,
            "identity_mode": "CONTROLLED_LOCAL_HEADER_IDENTITY",
            "identity_claim_boundary": (
                "CONTROLLED_LOCAL_HEADER_IDENTITY_NOT_EXTERNAL_IAM"
            ),
            "external_iam": "NOT_RUN",
        }
        if task_intake_contract != expected_task_intake_contract:
            failures.append("MIGRATION_TASK_INTAKE_BOUND_CONTRACT_INVALID")
    elif task_intake_contract is not None:
        failures.append("MIGRATION_TASK_INTAKE_BOUND_CONTRACT_UNEXPECTED")

    pairs = {
        "public_cases": (
            predecessor_root / "public" / "cases.json",
            benchmark_root / "public" / "cases.json",
        ),
        "evaluator_gold": (
            predecessor_root / "evaluator" / "gold.json",
            benchmark_root / "evaluator" / "gold.json",
        ),
        "evaluator_mutations": (
            predecessor_root / "evaluator" / "mutations.json",
            benchmark_root / "evaluator" / "mutations.json",
        ),
    }
    observed_delta: dict[str, list[str]] = {}
    for name, (before_path, after_path) in pairs.items():
        try:
            before = _object(before_path)
            after = _object(after_path)
            observed = sorted(_diff_paths(before, after))
            allowed = sorted(migration["allowed_delta"][name])
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
            failures.append(f"MIGRATION_DELTA_INPUT_INVALID:{name}")
            continue
        observed_delta[name] = observed
        if observed != allowed:
            failures.append(f"MIGRATION_DELTA_NOT_EXACT:{name}")

    before_gold: dict[str, Any] = {}
    after_gold: dict[str, Any] = {}
    try:
        before_gold = _object(pairs["evaluator_gold"][0])
        after_gold = _object(pairs["evaluator_gold"][1])
        for group in migration["required_unchanged"].values():
            for pointer in group:
                if _resolve_pointer(before_gold, pointer) != _resolve_pointer(
                    after_gold, pointer
                ):
                    failures.append(f"MIGRATION_REQUIRED_LOCK_CHANGED:{pointer}")
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
        failures.append("MIGRATION_REQUIRED_LOCK_INVALID")
    if benchmark_version == DEFAULT_BENCHMARK_VERSION:
        expected_event_types = [
            "WORKSPACE_SEED_LOADED",
            "WORKSPACE_TASK_COMMITTED",
            "WORKSPACE_TASK_INTAKE_BOUND",
            "TOOL_INVOKED",
            "WORKSPACE_CHANGE_PREVIEWED",
            "WORKSPACE_CHANGE_APPROVED",
            "REBASE_APPLIED",
            "WORKSPACE_CHANGE_OUTCOME_RECORDED",
            "WORKSPACE_CHANGE_PREVIEWED",
            "WORKSPACE_CHANGE_APPROVED",
            "REBASE_APPLIED",
            "WORKSPACE_CHANGE_OUTCOME_RECORDED",
        ]
        if (
            after_gold.get("product_oracle", {}).get("stable_event_records")
            != before_gold.get("product_oracle", {}).get("event_records", [])[:2]
            or after_gold.get("product_oracle", {}).get("event_types")
            != expected_event_types
        ):
            failures.append("MIGRATION_EVENT_CONTRACT_INVALID")
    return {
        "status": "PASS" if not failures else "FAIL",
        "applicability": applicability,
        "failures": sorted(set(failures)),
        "record_sha256": _file_digest(path),
        "predecessor_benchmark_version": predecessor.get("benchmark_version"),
        "predecessor_retained_wheel_sha256": retained_wheel.get("sha256"),
        "source_bound_contract_digest": _rfc8785_digest(source_contract),
        "task_intake_bound_contract_digest": (
            _rfc8785_digest(task_intake_contract)
            if isinstance(task_intake_contract, dict)
            else None
        ),
        "observed_delta": observed_delta,
    }


def _rfc8785_digest(value: Any) -> str:
    return f"sha256:{hashlib.sha256(rfc8785.dumps(value)).hexdigest()}"


def _product_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _file_digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _digest_string_valid(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 71
        and value.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def _content_digest_valid(payload: Any) -> bool:
    if not isinstance(payload, dict) or not isinstance(payload.get("digest"), str):
        return False
    document = {key: value for key, value in payload.items() if key != "digest"}
    return _product_digest(document) == payload["digest"]


def _claimed_rfc8785_digest_valid(payload: Any) -> bool:
    if not isinstance(payload, dict) or not isinstance(payload.get("digest"), str):
        return False
    document = {key: value for key, value in payload.items() if key != "digest"}
    return _rfc8785_digest(document) == payload["digest"]


def _logical_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def build_artifact_manifest(
    *,
    project_root: Path,
    runner_path: Path,
    evaluator_path: Path,
    observations_path: Path,
    wheel_path: Path,
    public_cases_path: Path,
    gold_path: Path,
    mutations_path: Path,
    benchmark_manifest_path: Path,
    benchmark_version: str = DEFAULT_BENCHMARK_VERSION,
) -> tuple[dict[str, Any], dict[str, Path]]:
    """Create the non-circular trust manifest for source, corpus and retained bytes."""

    paths = {
        "benchmark_manifest": benchmark_manifest_path,
        "public_cases": public_cases_path,
        "evaluator_gold": gold_path,
        "evaluator_mutations": mutations_path,
        "runner_source": runner_path,
        "evaluator_source": evaluator_path,
        "packaged_wheel": wheel_path,
        "raw_observations": observations_path,
    }
    entries = [
        {
            "id": artifact_id,
            "path": _logical_path(paths[artifact_id], project_root),
            "sha256": _file_digest(paths[artifact_id]),
            "size_bytes": paths[artifact_id].stat().st_size,
        }
        for artifact_id in ARTIFACT_IDS
    ]
    revision = _protocol_revision(benchmark_version)
    manifest: dict[str, Any] = {
        "schema_version": f"orgrebase.product-path-artifacts.v{revision}",
        "benchmark_version": benchmark_version,
        "digest_algorithm": "RFC8785_SHA256",
        "entries": entries,
    }
    manifest["digest"] = _rfc8785_digest(manifest)
    return manifest, paths


def _artifact_manifest_status(
    manifest: Any,
    *,
    project_root: Path,
    paths: dict[str, Path],
    benchmark_version: str,
) -> dict[str, Any]:
    failures: list[str] = []
    if not isinstance(manifest, dict):
        return {"status": "FAIL", "failures": ["ARTIFACT_MANIFEST_MISSING"]}
    revision = _protocol_revision(benchmark_version)
    if manifest.get("schema_version") != f"orgrebase.product-path-artifacts.v{revision}":
        failures.append("ARTIFACT_MANIFEST_SCHEMA_INVALID")
    if manifest.get("benchmark_version") != benchmark_version:
        failures.append("ARTIFACT_MANIFEST_BENCHMARK_INVALID")
    if not _claimed_rfc8785_digest_valid(manifest):
        failures.append("ARTIFACT_MANIFEST_DIGEST_INVALID")
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        entries = []
        failures.append("ARTIFACT_MANIFEST_ENTRIES_INVALID")
    by_id = {
        entry.get("id"): entry
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }
    if set(by_id) != set(ARTIFACT_IDS) or len(entries) != len(ARTIFACT_IDS):
        failures.append("ARTIFACT_MANIFEST_SET_INVALID")
    verified: list[dict[str, Any]] = []
    for artifact_id in ARTIFACT_IDS:
        path = paths[artifact_id]
        entry = by_id.get(artifact_id, {})
        observed_digest = _file_digest(path) if path.is_file() else None
        observed_size = path.stat().st_size if path.is_file() else None
        expected_logical = _logical_path(path, project_root)
        if (
            entry.get("path") != expected_logical
            or entry.get("sha256") != observed_digest
            or entry.get("size_bytes") != observed_size
        ):
            failures.append(f"ARTIFACT_BINDING_INVALID:{artifact_id}")
        verified.append(
            {
                "id": artifact_id,
                "path": expected_logical,
                "sha256": observed_digest,
                "size_bytes": observed_size,
            }
        )
    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": sorted(set(failures)),
        "entries": verified,
        "manifest_digest": manifest.get("digest"),
    }


def _wheel_inventory(path: Path) -> list[dict[str, Any]]:
    try:
        with zipfile.ZipFile(path) as archive:
            return [
                {
                    "path": item.filename,
                    "size_bytes": item.file_size,
                    "sha256": f"sha256:{hashlib.sha256(archive.read(item)).hexdigest()}",
                }
                for item in sorted(archive.infolist(), key=lambda value: value.filename)
                if not item.is_dir()
            ]
    except (OSError, zipfile.BadZipFile):
        return []


def _retained_bundle_failures(
    observations: dict[str, Any],
    *,
    artifact_status: dict[str, Any],
    wheel_path: Path,
    runner_path: Path,
    benchmark_version: str,
    case_count: int,
) -> list[str]:
    failures: list[str] = []
    execution = observations.get("execution")
    cases = observations.get("cases")
    surface = observations.get("oracle_surface")
    revision = _protocol_revision(benchmark_version)
    if observations.get("schema_version") != f"orgrebase.product-path-observations.v{revision}":
        failures.append("OBSERVATIONS_SCHEMA_INVALID")
    if observations.get("benchmark_version") != benchmark_version:
        failures.append("OBSERVATIONS_BENCHMARK_INVALID")
    if not _claimed_rfc8785_digest_valid(observations):
        failures.append("OBSERVATIONS_DIGEST_INVALID")
    if not isinstance(cases, list) or len(cases) != case_count:
        failures.append("OBSERVATIONS_CASE_SET_INVALID")
    if not isinstance(surface, dict) or set(surface) != {"quote_export", "evidence_export"}:
        failures.append("ORACLE_SURFACE_INCOMPLETE")
    if not isinstance(execution, dict):
        return sorted(set([*failures, "EXECUTION_BINDING_MISSING"]))
    if (
        execution.get("mode") != "BUILT_WHEEL_UNPACKED_HTTP_UVICORN"
        or execution.get("runner_product_imports") != 0
        or execution.get("module_probe", {}).get("module_loaded_from_wheel") is not True
        or execution.get("database_isolation") != "ONE_SQLITE_FILE_PER_CASE"
        or execution.get("real_process_restarts") != 3
        or execution.get("uvicorn_processes_started") != 15
        or execution.get("transport") != "HTTP_127.0.0.1"
    ):
        failures.append("EXECUTION_BOUNDARY_INVALID")
    if not wheel_path.is_file() or (
        execution.get("wheel_sha256") != _file_digest(wheel_path)
        or execution.get("wheel_size_bytes") != wheel_path.stat().st_size
        or execution.get("wheel_member_inventory") != _wheel_inventory(wheel_path)
    ):
        failures.append("RETAINED_WHEEL_BINDING_INVALID")
    if not runner_path.is_file() or execution.get("runner_sha256") != _file_digest(runner_path):
        failures.append("RUNNER_SOURCE_BINDING_INVALID")
    if artifact_status.get("status") != "PASS":
        failures.append("ARTIFACT_MANIFEST_INVALID")
    return sorted(set(failures))


def _export_digest_valid(payload: Any) -> bool:
    if not isinstance(payload, dict) or not isinstance(payload.get("digest"), str):
        return False
    document = {key: value for key, value in payload.items() if key != "digest"}
    return _product_digest(document) == payload["digest"]


def _rfc8785_export_digest_valid(payload: Any) -> bool:
    if not isinstance(payload, dict) or not isinstance(payload.get("digest"), str):
        return False
    document = {key: value for key, value in payload.items() if key != "digest"}
    return _rfc8785_digest(document) == payload["digest"]


def _normalize_integral_floats(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _normalize_integral_floats(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_integral_floats(item) for item in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _rfc8785_export_classification(payload: Any) -> str:
    """Distinguish the documented numeric dialect debt from semantic mismatch."""

    if not _export_digest_valid(payload):
        return "PRODUCT_DIGEST_INVALID"
    if _rfc8785_export_digest_valid(payload):
        return "MATCH"
    if not isinstance(payload, dict):  # pragma: no cover - guarded above
        return "NON_NUMERIC_SEMANTIC_MISMATCH"
    document = {key: value for key, value in payload.items() if key != "digest"}
    normalized = _normalize_integral_floats(document)
    product_bytes = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return (
        "NUMERIC_REPRESENTATION_DIFFERENCE"
        if product_bytes == rfc8785.dumps(document)
        else "NON_NUMERIC_SEMANTIC_MISMATCH"
    )


SOURCE_EXPORT_KEYS = (
    "enterprise_seed_profile",
    "enterprise_seed_admission",
    "enterprise_seed_source_admission",
    "enterprise_seed_runtime_projection",
    "enterprise_seed_runtime_binding",
)
SOURCE_COMPONENT_KINDS = {
    "DOMAIN",
    "KNOWLEDGE",
    "AUTHORITY",
    "CAPABILITY",
    "DEPENDENCY",
}


def _source_bound_failures(
    quote_export: dict[str, Any],
    evidence_export: dict[str, Any],
    *,
    expected_contract: dict[str, Any] | None,
) -> list[str]:
    """Verify Source commitments using only exported HTTP documents."""

    failures: list[str] = []
    if quote_export.get("schema_version") != "orgrebase.workspace-quote-export.v2":
        failures.append("QUOTE_EXPORT_SCHEMA_INVALID")
    if evidence_export.get("schema_version") != "orgrebase.workspace-evidence-export.v2":
        failures.append("EVIDENCE_EXPORT_SCHEMA_INVALID")
    if any(quote_export.get(key) != evidence_export.get(key) for key in SOURCE_EXPORT_KEYS):
        failures.append("SOURCE_EXPORT_MISMATCH")
    if not isinstance(expected_contract, dict):
        failures.append("SOURCE_BOUND_CONTRACT_MISSING")
        expected_contract = {}

    profile = evidence_export.get("enterprise_seed_profile")
    admission = evidence_export.get("enterprise_seed_admission")
    source = evidence_export.get("enterprise_seed_source_admission")
    runtime = evidence_export.get("enterprise_seed_runtime_projection")
    binding_record = evidence_export.get("enterprise_seed_runtime_binding")
    if not all(isinstance(item, dict) for item in (profile, admission, source, runtime, binding_record)):
        return sorted(set([*failures, "SOURCE_BOUND_OBJECT_MISSING"]))
    assert isinstance(profile, dict)
    assert isinstance(admission, dict)
    assert isinstance(source, dict)
    assert isinstance(runtime, dict)
    assert isinstance(binding_record, dict)

    if (
        profile.get("schema_version") != "orgrebase.enterprise-seed-profile.v1"
        or profile.get("profile_ref") != expected_contract.get("profile_ref")
        or profile.get("digest") != expected_contract.get("profile_digest")
        or profile.get("organization_id") != "org:northstar"
        or profile.get("data_class") != "SYNTHETIC_FIXTURE"
        or profile.get("reference_runtime_compatible") is not True
    ):
        failures.append("SOURCE_PROFILE_IDENTITY_INVALID")
    if not _content_digest_valid(admission):
        failures.append("SOURCE_ADMISSION_DIGEST_INVALID")
    if not _content_digest_valid(source):
        failures.append("SOURCE_ADMISSION_DIGEST_INVALID")
    if not _content_digest_valid(runtime):
        failures.append("SOURCE_RUNTIME_PROJECTION_DIGEST_INVALID")

    profile_digest = profile.get("digest")
    source_digest = source.get("digest")
    runtime_digest = runtime.get("digest")
    if (
        admission.get("digest") != expected_contract.get("admission_receipt_digest")
        or source.get("digest")
        != expected_contract.get("source_admission_receipt_digest")
        or runtime.get("digest")
        != expected_contract.get("runtime_projection_receipt_digest")
        or runtime.get("runtime_projection_digest")
        != expected_contract.get("runtime_projection_digest")
        or binding_record.get("artifact_digest")
        != expected_contract.get("binding_artifact_digest")
        or source.get("authority_assurance")
        != expected_contract.get("authority_assurance")
    ):
        failures.append("SOURCE_EXACT_COMMITMENT_MISMATCH")
    if (
        admission.get("profile_digest") != profile_digest
        or source.get("profile_digest") != profile_digest
        or runtime.get("profile_digest") != profile_digest
        or admission.get("source_admission_receipt_digest") != source_digest
        or runtime.get("source_admission_receipt_digest") != source_digest
    ):
        failures.append("SOURCE_RECEIPT_BINDING_INVALID")

    roots = source.get("root_observations")
    components = source.get("component_admissions")
    projections = source.get("admitted_projection_bindings")
    if not isinstance(roots, list) or len(roots) != 5:
        failures.append("SOURCE_ROOT_SET_INVALID")
        roots = []
    if not isinstance(components, list) or len(components) != 5:
        failures.append("SOURCE_COMPONENT_SET_INVALID")
        components = []
    if not isinstance(projections, list) or len(projections) != 5:
        failures.append("SOURCE_PROJECTION_SET_INVALID")
        projections = []
    root_by_kind = {
        item.get("component_kind"): item
        for item in roots
        if isinstance(item, dict) and isinstance(item.get("component_kind"), str)
    }
    component_by_kind = {
        item.get("component_kind"): item
        for item in components
        if isinstance(item, dict) and isinstance(item.get("component_kind"), str)
    }
    projection_by_kind = {
        item.get("component_kind"): item
        for item in projections
        if isinstance(item, dict) and isinstance(item.get("component_kind"), str)
    }
    if (
        set(root_by_kind) != SOURCE_COMPONENT_KINDS
        or set(component_by_kind) != SOURCE_COMPONENT_KINDS
        or set(projection_by_kind) != SOURCE_COMPONENT_KINDS
    ):
        failures.append("SOURCE_FIVE_DIMENSION_SET_INVALID")
    observed_root_digests = {
        kind: item.get("observed_digest") for kind, item in root_by_kind.items()
    }
    observed_projection_digests = {
        kind: item.get("projection_digest") for kind, item in root_by_kind.items()
    }
    if (
        observed_root_digests != expected_contract.get("root_digests")
        or observed_projection_digests != expected_contract.get("projection_digests")
    ):
        failures.append("SOURCE_EXACT_ROOT_COMMITMENT_MISMATCH")
    for kind in SOURCE_COMPONENT_KINDS:
        root = root_by_kind.get(kind, {})
        component = component_by_kind.get(kind, {})
        projection = projection_by_kind.get(kind, {})
        expected_locator = f"packaged://northstar/{kind.lower()}@r1"
        if (
            not _content_digest_valid(root)
            or root.get("locator") != expected_locator
            or root.get("declared_digest") != root.get("observed_digest")
            or root.get("verdict") != "ADMITTED"
        ):
            failures.append("SOURCE_ROOT_BINDING_INVALID")
        if (
            not _content_digest_valid(component)
            or component.get("declared_component_digest")
            != component.get("computed_component_digest")
            or component.get("verdict") != "ADMITTED"
            or component.get("admitted_projection_digest")
            != root.get("projection_digest")
            or projection.get("projection_digest") != root.get("projection_digest")
        ):
            failures.append("SOURCE_COMPONENT_BINDING_INVALID")
    if (
        admission.get("admitted_source_root_digests")
        != [item.get("observed_digest") for item in roots]
        or admission.get("component_admission_digests")
        != [item.get("digest") for item in components]
        or admission.get("source_profile_projection_digest")
        != source.get("profile_projection_digest")
    ):
        failures.append("SOURCE_ADMISSION_SUMMARY_BINDING_INVALID")

    runtime_observations = runtime.get("observations")
    if not isinstance(runtime_observations, list) or len(runtime_observations) != 5:
        failures.append("SOURCE_RUNTIME_PROJECTION_INVALID")
        runtime_observations = []
    runtime_by_kind = {
        item.get("component_kind"): item
        for item in runtime_observations
        if isinstance(item, dict) and isinstance(item.get("component_kind"), str)
    }
    if set(runtime_by_kind) != SOURCE_COMPONENT_KINDS:
        failures.append("SOURCE_RUNTIME_PROJECTION_INVALID")
    for kind in SOURCE_COMPONENT_KINDS:
        item = runtime_by_kind.get(kind, {})
        admitted = projection_by_kind.get(kind, {}).get("projection_digest")
        if (
            not _content_digest_valid(item)
            or item.get("status") != "MATCH"
            or item.get("admitted_projection_digest") != admitted
            or item.get("runtime_projection_digest") != admitted
        ):
            failures.append("SOURCE_RUNTIME_PROJECTION_INVALID")
    if runtime.get("verdict") != "MATCH":
        failures.append("SOURCE_RUNTIME_PROJECTION_INVALID")

    binding = binding_record.get("binding")
    if not isinstance(binding, dict):
        failures.append("SOURCE_RUNTIME_BINDING_INVALID")
        binding = {}
    if binding_record.get("artifact_digest") != _product_digest(binding):
        failures.append("SOURCE_RUNTIME_BINDING_DIGEST_INVALID")
    runtime_projection_bindings = [
        {
            "component_kind": item.get("component_kind"),
            "runtime_projection_digest": item.get("runtime_projection_digest"),
        }
        for item in runtime_observations
    ]
    if (
        binding.get("schema_version")
        != "orgrebase.enterprise-seed-runtime-binding.v1"
        or binding.get("profile") != profile
        or binding.get("admission_receipt") != admission
        or binding.get("source_admission_receipt") != source
        or binding.get("runtime_projection_receipt") != runtime
        or binding.get("source_admission_receipt_digest") != source_digest
        or binding.get("runtime_projection_receipt_digest") != runtime_digest
        or binding.get("admitted_source_root_digests")
        != admission.get("admitted_source_root_digests")
        or binding.get("component_admission_digests")
        != admission.get("component_admission_digests")
        or binding.get("source_profile_projection_digest")
        != source.get("profile_projection_digest")
        or binding.get("runtime_projection_digest")
        != runtime.get("runtime_projection_digest")
        or binding.get("runtime_projection_bindings") != runtime_projection_bindings
    ):
        failures.append("SOURCE_RUNTIME_BINDING_INVALID")
    write_counters = (
        admission.get("canonical_target_writes"),
        source.get("canonical_target_writes"),
        runtime.get("canonical_target_writes"),
        binding.get("canonical_target_writes_outside_formation_transaction"),
    )
    if write_counters != (0, 0, 0, 0):
        failures.append("SOURCE_CANONICAL_TARGET_WRITES_NONZERO")
    return sorted(set(failures))


def _source_bound_summary(
    surface: Any,
    *,
    revision: int,
    expected_contract: dict[str, Any] | None,
) -> dict[str, Any]:
    if revision != 2:
        return {"status": "NOT_APPLICABLE", "failures": []}
    if not isinstance(surface, dict):
        return {"status": "FAIL", "failures": ["PRODUCT_EXPORT_MISSING"]}
    quote = surface.get("quote_export")
    evidence = surface.get("evidence_export")
    if not isinstance(quote, dict) or not isinstance(evidence, dict):
        return {"status": "FAIL", "failures": ["PRODUCT_EXPORT_MISSING"]}
    failures = _source_bound_failures(
        quote, evidence, expected_contract=expected_contract
    )
    source = evidence.get("enterprise_seed_source_admission", {})
    runtime = evidence.get("enterprise_seed_runtime_projection", {})
    binding_record = evidence.get("enterprise_seed_runtime_binding", {})
    binding = binding_record.get("binding", {}) if isinstance(binding_record, dict) else {}
    roots = source.get("root_observations", []) if isinstance(source, dict) else []
    projections = runtime.get("observations", []) if isinstance(runtime, dict) else []
    write_counters = [
        value
        for value in (
            evidence.get("enterprise_seed_admission", {}).get("canonical_target_writes"),
            source.get("canonical_target_writes") if isinstance(source, dict) else None,
            runtime.get("canonical_target_writes") if isinstance(runtime, dict) else None,
            binding.get("canonical_target_writes_outside_formation_transaction")
            if isinstance(binding, dict)
            else None,
        )
        if isinstance(value, int)
    ]
    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "profile_ref": evidence.get("enterprise_seed_profile", {}).get("profile_ref"),
        "authority_assurance": source.get("authority_assurance")
        if isinstance(source, dict)
        else None,
        "component_root_count": len(roots) if isinstance(roots, list) else 0,
        "component_kinds": sorted(
            item.get("component_kind")
            for item in roots
            if isinstance(item, dict) and isinstance(item.get("component_kind"), str)
        ),
        "runtime_projection": {
            "matched": sum(
                item.get("status") == "MATCH"
                for item in projections
                if isinstance(item, dict)
            ),
            "total": len(projections) if isinstance(projections, list) else 0,
        },
        "canonical_target_writes": sum(write_counters) if len(write_counters) == 4 else None,
    }


def _event_link_failures(event_chain: Any, expected_records: Any) -> list[str]:
    if not isinstance(event_chain, dict):
        return ["EVENT_CHAIN_MISSING"]
    records = event_chain.get("records")
    if not isinstance(records, list):
        return ["EVENT_RECORDS_MISSING"]
    failures: list[str] = []
    previous = ZERO_DIGEST
    for expected_sequence, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            failures.append("EVENT_RECORD_INVALID")
            continue
        if record.get("sequence_no") != expected_sequence:
            failures.append("EVENT_SEQUENCE_MISMATCH")
        if record.get("previous_digest") != previous:
            failures.append("EVENT_PREDECESSOR_MISMATCH")
        event_digest = record.get("event_digest")
        if not _digest_string_valid(event_digest):
            failures.append("EVENT_DIGEST_MISSING")
        else:
            previous = event_digest
    if event_chain.get("events") != len(records):
        failures.append("EVENT_COUNT_MISMATCH")
    if event_chain.get("head_digest") != previous:
        failures.append("EVENT_HEAD_MISMATCH")
    if event_chain.get("status") != "PASS":
        failures.append("EVENT_STATUS_MISMATCH")
    # Payload envelopes remain outside this export.  Stable predecessor events
    # may retain exact digests in Gold, while nonce-derived successor events are
    # committed only by sequence/type and are cross-bound below to the run-local
    # Task Intake, Tool and Approval artifacts that expose their event digests.
    if (
        not isinstance(expected_records, list)
        or len(records) != len(expected_records)
        or any(
            _matches(expected, observed)
            for expected, observed in zip(expected_records, records, strict=True)
        )
    ):
        failures.append("EVENT_RECORD_COMMITMENT_MISMATCH")
    return sorted(set(failures))


def _event_records_of_type(event_chain: Any, event_type: str) -> list[dict[str, Any]]:
    if not isinstance(event_chain, dict) or not isinstance(
        event_chain.get("records"), list
    ):
        return []
    return [
        record
        for record in event_chain["records"]
        if isinstance(record, dict) and record.get("event_type") == event_type
    ]


def _task_intake_binding_failures(
    evidence: dict[str, Any], expected: dict[str, Any]
) -> list[str]:
    """Verify nonce-derived Intake receipts only against this exported run."""

    intake = evidence.get("task_intake")
    formation = evidence.get("formation")
    quote = evidence.get("quote")
    profile = evidence.get("enterprise_seed_profile")
    if not all(isinstance(item, dict) for item in (intake, formation, quote, profile)):
        return ["TASK_INTAKE_EVIDENCE_MISSING"]
    assert isinstance(intake, dict)
    assert isinstance(formation, dict)
    assert isinstance(quote, dict)
    assert isinstance(profile, dict)
    candidate = intake.get("candidate_receipt")
    confirmation = intake.get("confirmation_receipt")
    if not isinstance(candidate, dict) or not isinstance(confirmation, dict):
        return ["TASK_INTAKE_RAW_RECEIPTS_MISSING"]
    task_request = candidate.get("task_request")
    if not isinstance(task_request, dict):
        return ["TASK_INTAKE_TASK_REQUEST_MISSING"]

    failures: list[str] = []
    persisted_receipt = {
        key: value
        for key, value in intake.items()
        if key not in {"artifact_id", "artifact_payload_digest", "event_digest"}
    }
    if (
        not _content_digest_valid(persisted_receipt)
        or not _content_digest_valid(candidate)
        or not _content_digest_valid(confirmation)
        or not _content_digest_valid(task_request)
        or not _content_digest_valid(formation)
    ):
        failures.append("TASK_INTAKE_CONTENT_DIGEST_INVALID")
    if (
        intake.get("artifact_id") != "task-intake:quote-v1@r1"
        or intake.get("artifact_payload_digest")
        != _product_digest(persisted_receipt)
    ):
        failures.append("TASK_INTAKE_ARTIFACT_BINDING_MISMATCH")
    if (
        candidate.get("status") != expected.get("candidate_status")
        or confirmation.get("status") != expected.get("confirmation_status")
        or intake.get("status") != expected.get("run_status")
    ):
        failures.append("TASK_INTAKE_STATUS_MISMATCH")
    if (
        intake.get("actor_id") != expected.get("actor_id")
        or candidate.get("actor_id") != expected.get("actor_id")
        or confirmation.get("actor_id") != expected.get("actor_id")
        or task_request.get("actor_id") != expected.get("actor_id")
        or candidate.get("customer_id") != expected.get("customer_id")
        or task_request.get("customer_id") != expected.get("customer_id")
        or candidate.get("deliverable_kind") != expected.get("deliverable_kind")
        or task_request.get("deliverable_kind") != expected.get("deliverable_kind")
    ):
        failures.append("TASK_INTAKE_SCOPE_MISMATCH")
    run_ids = {
        intake.get("run_id"),
        candidate.get("intended_run_id"),
        confirmation.get("intended_run_id"),
    }
    workspace_nonces = {
        intake.get("workspace_instance_nonce"),
        candidate.get("workspace_instance_nonce"),
        confirmation.get("workspace_instance_nonce"),
    }
    if len(run_ids) != 1 or None in run_ids:
        failures.append("TASK_INTAKE_RUN_BINDING_MISMATCH")
    if (
        len(workspace_nonces) != 1
        or None in workspace_nonces
        or not _digest_string_valid(next(iter(workspace_nonces)))
    ):
        failures.append("TASK_INTAKE_WORKSPACE_NONCE_BINDING_MISMATCH")
    if (
        candidate.get("natural_language_authority")
        is not expected.get("natural_language_authority")
        or candidate.get("candidate_only") is not True
        or candidate.get("canonical_target_writes")
        != expected.get("canonical_target_writes")
        or confirmation.get("canonical_target_writes")
        != expected.get("canonical_target_writes")
        or intake.get("intake_canonical_target_writes")
        != expected.get("canonical_target_writes")
        or intake.get("intake_persisted") is not True
        or intake.get("formation_authority") != "ORGREBASE_CONTROL_PLANE"
    ):
        failures.append("TASK_INTAKE_AUTHORITY_BOUNDARY_MISMATCH")
    if (
        intake.get("prompt_digest") != candidate.get("prompt_digest")
        or intake.get("prompt_length") != candidate.get("prompt_length")
        or intake.get("candidate_digest") != candidate.get("digest")
        or intake.get("approval_digest") != confirmation.get("digest")
        or confirmation.get("candidate_digest") != candidate.get("digest")
        or intake.get("task_digest") != candidate.get("task_digest")
        or candidate.get("task_digest") != task_request.get("digest")
        or confirmation.get("task_digest") != candidate.get("task_digest")
        or confirmation.get("profile_digest") != candidate.get("profile_digest")
        or confirmation.get("template_digest") != candidate.get("template_digest")
        or candidate.get("profile_digest") != profile.get("digest")
        or candidate.get("template_ref") != task_request.get("template_ref")
    ):
        failures.append("TASK_INTAKE_RECEIPT_BINDING_MISMATCH")
    formation_quote_ref = formation.get("deliverable_ref")
    final_quote_id = quote.get("id")
    if (
        intake.get("formation_receipt_digest") != formation.get("digest")
        or intake.get("quote_ref") != formation_quote_ref
        or not isinstance(formation_quote_ref, str)
        or not formation_quote_ref.endswith("@v1")
        or not isinstance(final_quote_id, str)
        or formation_quote_ref.rsplit("@", 1)[0] != final_quote_id
    ):
        failures.append("TASK_INTAKE_FORMATION_BINDING_MISMATCH")
    intake_events = _event_records_of_type(
        evidence.get("event_chain"), "WORKSPACE_TASK_INTAKE_BOUND"
    )
    if (
        len(intake_events) != 1
        or intake.get("event_digest") != intake_events[0].get("event_digest")
    ):
        failures.append("TASK_INTAKE_EVENT_BINDING_MISMATCH")
    return sorted(set(failures))


def _approval_review_gate_failures(
    evidence: dict[str, Any],
    expected_owners: dict[str, str],
    expected: dict[str, Any],
) -> list[str]:
    """Verify the persisted four-second gate and Header identity projection."""

    changes = evidence.get("changes")
    if not isinstance(changes, dict):
        return ["APPROVAL_REVIEW_GATE_MISSING"]
    failures: list[str] = []
    event_chain = evidence.get("event_chain")
    for kind, expected_owner in expected_owners.items():
        change = changes.get(kind)
        preview_record = change.get("preview") if isinstance(change, dict) else None
        approval_record = change.get("approval") if isinstance(change, dict) else None
        if not isinstance(preview_record, dict) or not isinstance(
            approval_record, dict
        ):
            failures.append("APPROVAL_REVIEW_GATE_MISSING")
            continue
        gate_record = preview_record.get("review_gate")
        review_evidence = approval_record.get("approval_review_evidence")
        binding = approval_record.get("binding")
        bundle = preview_record.get("bundle")
        if not all(
            isinstance(item, dict)
            for item in (gate_record, review_evidence, binding, bundle)
        ):
            failures.append("APPROVAL_REVIEW_GATE_MISSING")
            continue
        assert isinstance(gate_record, dict)
        assert isinstance(review_evidence, dict)
        assert isinstance(binding, dict)
        assert isinstance(bundle, dict)
        gate = gate_record.get("gate")
        change_spec = bundle.get("change_spec")
        preview = bundle.get("preview")
        run_envelope = bundle.get("run_envelope")
        if not all(
            isinstance(item, dict)
            for item in (gate, change_spec, preview, run_envelope)
        ):
            failures.append("APPROVAL_REVIEW_GATE_MISSING")
            continue
        assert isinstance(gate, dict)
        assert isinstance(change_spec, dict)
        assert isinstance(preview, dict)
        assert isinstance(run_envelope, dict)
        if (
            not _content_digest_valid(gate)
            or gate_record.get("artifact_digest") != _product_digest(gate)
            or gate.get("preview_artifact_ref") != preview_record.get("artifact_id")
            or gate.get("preview_artifact_digest")
            != preview_record.get("artifact_digest")
            or gate.get("preview_digest") != preview.get("digest")
            or gate.get("owner_id") != expected_owner
            or gate.get("owner_id") != change_spec.get("owner_id")
            or gate.get("workflow_run_id") != run_envelope.get("run_id")
            or gate.get("run_nonce") != run_envelope.get("nonce")
            or gate.get("profile_digest")
            != evidence.get("enterprise_seed_profile", {}).get("digest")
            or gate.get("canonical_target_writes") != 0
        ):
            failures.append("APPROVAL_REVIEW_GATE_BINDING_MISMATCH")
        previewed_ms = gate.get("previewed_at_epoch_ms")
        not_before_ms = gate.get("not_before_epoch_ms")
        if (
            gate.get("review_duration_ms") != expected.get("review_duration_ms")
            or not isinstance(previewed_ms, int)
            or not isinstance(not_before_ms, int)
            or not_before_ms - previewed_ms != expected.get("review_duration_ms")
        ):
            failures.append("APPROVAL_REVIEW_GATE_DURATION_MISMATCH")
        if (
            gate.get("approval_identity_mode") != expected.get("identity_mode")
            or gate.get("identity_claim_boundary")
            != expected.get("identity_claim_boundary")
        ):
            failures.append("APPROVAL_IDENTITY_MODE_MISMATCH")
        if (
            not _content_digest_valid(binding)
            or approval_record.get("binding_digest") != binding.get("digest")
            or binding.get("workflow_run_id") != gate.get("workflow_run_id")
            or binding.get("run_nonce") != gate.get("run_nonce")
            or binding.get("profile_digest") != gate.get("profile_digest")
            or binding.get("preview_digest") != gate.get("preview_digest")
            or binding.get("owner_id") != expected_owner
            or binding.get("target_writes") != 0
        ):
            failures.append("APPROVAL_IDENTITY_BINDING_MISMATCH")
        observed_ms = review_evidence.get("approval_observed_at_epoch_ms")
        sequence_no = review_evidence.get("event_sequence_no")
        records = (
            event_chain.get("records")
            if isinstance(event_chain, dict) and isinstance(event_chain.get("records"), list)
            else []
        )
        event_record = (
            records[sequence_no - 1]
            if isinstance(sequence_no, int)
            and sequence_no > 0
            and sequence_no <= len(records)
            and isinstance(records[sequence_no - 1], dict)
            else None
        )
        if (
            review_evidence.get("review_wait_satisfied") is not True
            or review_evidence.get("review_duration_ms")
            != expected.get("review_duration_ms")
            or review_evidence.get("review_not_before_epoch_ms") != not_before_ms
            or review_evidence.get("review_not_before") != gate.get("not_before")
            or not isinstance(observed_ms, int)
            or not isinstance(not_before_ms, int)
            or observed_ms < not_before_ms
            or not isinstance(event_record, dict)
            or event_record.get("event_type") != "WORKSPACE_CHANGE_APPROVED"
            or event_record.get("event_digest")
            != review_evidence.get("event_digest")
        ):
            failures.append("APPROVAL_REVIEW_TIME_EVIDENCE_INVALID")
    return sorted(set(failures))


def _approval_binding_failures(
    evidence: dict[str, Any], expected_owners: dict[str, str]
) -> list[str]:
    failures: list[str] = []
    changes = evidence.get("changes")
    if not isinstance(changes, dict):
        return ["CHANGE_RECEIPTS_MISSING"]
    for kind, expected_owner in expected_owners.items():
        change = changes.get(kind)
        if not isinstance(change, dict):
            failures.append("CHANGE_RECEIPTS_MISSING")
            continue
        preview_record = change.get("preview")
        approval_record = change.get("approval")
        outcome_record = change.get("outcome")
        if (
            not isinstance(preview_record, dict)
            or not isinstance(approval_record, dict)
            or not isinstance(outcome_record, dict)
        ):
            failures.append("CHANGE_RECEIPTS_MISSING")
            continue
        bundle = preview_record.get("bundle")
        approval = approval_record.get("approval")
        outcome = outcome_record.get("outcome")
        if (
            not isinstance(bundle, dict)
            or not isinstance(approval, dict)
            or not isinstance(outcome, dict)
        ):
            failures.append("CHANGE_RECEIPTS_MISSING")
            continue
        preview = bundle.get("preview")
        change_spec = bundle.get("change_spec")
        change_set = bundle.get("change_set")
        certificate = bundle.get("minimal_rebase_certificate")
        receipt = outcome.get("rebase_receipt")
        if (
            not isinstance(preview, dict)
            or not isinstance(change_spec, dict)
            or not isinstance(change_set, dict)
            or not isinstance(certificate, dict)
            or not isinstance(receipt, dict)
        ):
            failures.append("REBASE_RECEIPT_MISSING")
            continue
        if (
            not _content_digest_valid(preview)
            or preview_record.get("preview_digest") != preview.get("digest")
            or preview_record.get("artifact_digest") != _product_digest(bundle)
        ):
            failures.append("PREVIEW_DIGEST_INVALID")
        if (
            not _content_digest_valid(change_spec)
            or not _content_digest_valid(change_set)
            or not _content_digest_valid(certificate)
        ):
            failures.append("PREVIEW_INPUT_DIGEST_INVALID")
        if (
            not _content_digest_valid(approval)
            or approval_record.get("approval_digest") != approval.get("digest")
            or approval_record.get("artifact_digest") != _product_digest(approval)
        ):
            failures.append("APPROVAL_DIGEST_INVALID")
        if (
            approval.get("actor_id") != expected_owner
            or change_spec.get("owner_id") != expected_owner
            or change_set.get("owner_id") != expected_owner
            or receipt.get("approval_actor_id") != expected_owner
        ):
            failures.append("OWNER_MISMATCH")
        if (
            approval.get("change_set_digest") != change_set.get("digest")
            or approval.get("preview_digest") != preview.get("digest")
            or approval.get("minimal_rebase_certificate_digest")
            != certificate.get("digest")
        ):
            failures.append("APPROVAL_PREVIEW_BINDING_MISMATCH")
        change_ref = f"{change_set.get('id')}@{change_set.get('revision')}"
        revision_lock = preview.get("revision_lock")
        if (
            preview.get("change_set_ref") != change_ref
            or not isinstance(revision_lock, dict)
            or revision_lock.get("change_set_revision") != change_ref
            or revision_lock.get("change_set_digest")
            != change_set.get("digest")
            or certificate.get("change_set_digest") != change_set.get("digest")
            or certificate.get("preview_digest") != preview.get("digest")
        ):
            failures.append("PREVIEW_CHANGESET_BINDING_MISMATCH")
        if not _content_digest_valid(receipt):
            failures.append("REBASE_RECEIPT_DIGEST_INVALID")
        bound_digests = {
            approval.get("digest"),
            approval_record.get("approval_digest"),
            outcome.get("approval_digest"),
            receipt.get("approval_digest"),
        }
        if len(bound_digests) != 1 or None in bound_digests:
            failures.append("APPROVAL_RECEIPT_BINDING_MISMATCH")
        if (
            receipt.get("approval_ref") != approval.get("id")
            or receipt.get("preview_ref") != preview.get("id")
            or receipt.get("minimal_rebase_certificate_digest")
            != certificate.get("digest")
            or outcome_record.get("artifact_digest") != _product_digest(outcome)
        ):
            failures.append("OUTCOME_RECEIPT_BINDING_MISMATCH")
        if receipt.get("status") != "COMPLETED":
            failures.append("REBASE_RECEIPT_STATUS_MISMATCH")
    return sorted(set(failures))


def _tool_binding_failures(
    tool: Any, expected: dict[str, Any], *, event_chain: Any
) -> list[str]:
    if not isinstance(tool, dict):
        return ["TOOL_EVIDENCE_MISSING"]
    failures: list[str] = []
    invocation = tool.get("invocation")
    called_event = tool.get("called_event")
    if not isinstance(invocation, dict) or not isinstance(called_event, dict):
        return ["TOOL_RAW_EVIDENCE_MISSING"]
    contract = invocation.get("contract")
    result = invocation.get("result")
    receipt = invocation.get("receipt")
    if (
        not isinstance(contract, dict)
        or not isinstance(result, dict)
        or not isinstance(receipt, dict)
    ):
        return ["TOOL_RAW_EVIDENCE_MISSING"]
    coverage = result.get("coverage")
    target_ids = sorted(
        str(item.get("object_id"))
        for item in coverage
        if isinstance(item, dict) and isinstance(item.get("object_id"), str)
    ) if isinstance(coverage, list) else []
    reconstructed_request = {
        "actor_id": receipt.get("actor_id"),
        "workflow_run_id": receipt.get("workflow_run_id"),
        "run_nonce": receipt.get("run_nonce"),
        "target_ids": target_ids,
        "graph_revision": result.get("graph_revision"),
    }
    expected_tool_ref = f"{contract.get('id')}@{contract.get('version')}"
    request_digest = _product_digest(reconstructed_request)
    result_digest = _product_digest(result)
    if tool.get("status") != expected.get("status") or receipt.get("status") != expected.get("status"):
        failures.append("TOOL_STATUS_MISMATCH")
    if tool.get("evidence_class") != "LOCAL_REAL_TOOL" or receipt.get("evidence_class") != "LOCAL_REAL_TOOL":
        failures.append("TOOL_EVIDENCE_CLASS_MISMATCH")
    if tool.get("target_writes") != 0:
        failures.append("TARGET_WRITES_NONZERO")
    if (
        receipt.get("request_digest") != request_digest
        or called_event.get("request_digest") != request_digest
        or receipt.get("request_digest") != expected.get("request_digest")
    ):
        failures.append("TOOL_REQUEST_DIGEST_INVALID")
    if (
        receipt.get("result_digest") != result_digest
        or called_event.get("result_digest") != result_digest
        or receipt.get("result_digest") != expected.get("result_digest")
    ):
        failures.append("TOOL_RESULT_COMMITMENT_MISMATCH")
    if not _content_digest_valid(contract) or not _content_digest_valid(receipt):
        failures.append("TOOL_CONTENT_DIGEST_INVALID")
    if not _content_digest_valid(called_event):
        failures.append("TOOL_CALLED_EVENT_DIGEST_INVALID")
    if (
        receipt.get("tool_ref") != expected_tool_ref
        or called_event.get("tool_ref") != expected_tool_ref
        or called_event.get("invocation_receipt_ref") != receipt.get("id")
        or tool.get("invocation_artifact_id") != receipt.get("id")
        or tool.get("invocation_artifact_digest") != _product_digest(invocation)
        or tool.get("called_event_artifact_id") != called_event.get("event_id")
        or tool.get("called_event_artifact_digest") != _product_digest(called_event)
    ):
        failures.append("TOOL_ARTIFACT_BINDING_MISMATCH")
    if "called_event_digest" in expected and called_event.get("digest") != expected["called_event_digest"]:
        failures.append("TOOL_CALLED_EVENT_DIGEST_COMMITMENT_MISMATCH")
    frozen_run_specific = {
        "receipt_digest": receipt.get("digest"),
        "audit_event_digest": receipt.get("audit_event_digest"),
        "invocation_artifact_digest": tool.get("invocation_artifact_digest"),
    }
    for key, observed in frozen_run_specific.items():
        if key in expected and observed != expected.get(key):
            failures.append(f"TOOL_{key.upper()}_COMMITMENT_MISMATCH")
    invoked_events = _event_records_of_type(event_chain, "TOOL_INVOKED")
    if (
        len(invoked_events) != 1
        or receipt.get("audit_event_digest")
        != invoked_events[0].get("event_digest")
    ):
        failures.append("TOOL_AUDIT_EVENT_BINDING_MISMATCH")
    # The Intake workspace nonce makes the chain predecessor, receipt digest
    # and invocation artifact digest run-specific.  Recompute them from this
    # invocation and bind them to the same event instead of freezing them.
    if (
        not _digest_string_valid(receipt.get("digest"))
        or not _digest_string_valid(receipt.get("audit_event_digest"))
        or tool.get("invocation_artifact_digest") != _product_digest(invocation)
    ):
        failures.append("TOOL_RUN_LOCAL_CONTENT_ADDRESS_INVALID")
    return sorted(set(failures))


def _runtime_contract(observations: dict[str, Any]) -> tuple[str, list[str]]:
    surface = observations.get("oracle_surface") or {}
    markers = [case.get("workspace_state_schema_version")
               for case in observations.get("cases", []) if isinstance(case, dict)]
    markers.extend((surface.get(name) or {}).get("workspace_state_schema_version")
                   for name in ("quote_export", "evidence_export"))
    if markers and all(marker is None for marker in markers):
        return "orgrebase.product-path-runtime-contract.v1", []
    if markers and all(marker == CURRENT_STATE_SCHEMA for marker in markers):
        return CURRENT_RUNTIME_CONTRACT, []
    return "UNSUPPORTED", ["RUNTIME_CONTRACT_MARKER_MISMATCH"]


def _current_case_contract(case: dict[str, Any], keys: list[str]) -> tuple[dict[str, Any], list[str]]:
    """Versioned semantic changes; frozen predecessor facts stay immutable."""
    current = copy.deepcopy(case)
    stages = {"QUOTE_V1": "CURRENT", "QUOTE_V2": "CURRENT", "QUOTE_V3": "CURRENT",
              "LAUNCH_PREVIEWED": "PREVIEWED", "LAUNCH_APPROVED": "APPROVED"}
    facts = current["expected_facts"]
    for key, value in tuple(facts.items()):
        if isinstance(value, str) and value in stages:
            facts[key] = stages[value]
    if case["id"] == "PP-001":
        facts["event_count"] = 14
    if case["id"] == "PP-006":
        current["title"] = "Preview a registered currency event before launch without a canonical write"
        facts.update(http_status=200, error_code=None, state_unchanged=False,
                     canonical_quote_unchanged=True, preview_event_id="currency")
        keys = [*keys, "canonical_quote_unchanged", "preview_event_id"]
    if case["id"] == "PP-012":
        facts["observed_stages"] = ["APPROVED"]
    return current, keys


def _versioned_object_digest_valid(obj: Any) -> bool:
    return isinstance(obj, dict) and obj.get("digest") == _product_digest(
        {key: value for key, value in obj.items() if key not in {"digest", "state"}}
    )


def _registered_change_failures(evidence: dict[str, Any], owners: dict[str, str]) -> list[str]:
    events = evidence.get("change_events")
    envelopes = evidence.get("change_registration_events")
    if not isinstance(events, list) or not isinstance(envelopes, list):
        return ["CHANGE_EVENT_EVIDENCE_MISSING"]
    expected_sources = {"launch_date": ("claim:product.launch_date", "v7", "v8", "2026-09-15"),
                        "currency": ("policy:finance.currency", "v1", "v2", "EUR")}
    records = _event_records_of_type(evidence.get("event_chain"), "WORKSPACE_CHANGE_REGISTERED")
    if len(events) != 2 or len(envelopes) != 2 or len(records) != 2:
        return ["CHANGE_EVENT_REGISTRATION_COUNT_INVALID"]
    failures = []
    initial_digests = {item.get("object_ref"): item.get("digest")
                       for item in evidence.get("formation_graph_snapshot", {}).get("object_digests", [])}
    for event, envelope, record, event_id in zip(events, envelopes, records, expected_sources, strict=True):
        object_id, base, version, value = expected_sources[event_id]
        proposal = event.get("proposal", {})
        spec = evidence.get("changes", {}).get(event_id, {}).get("preview", {}).get("bundle", {}).get("change_spec", {})
        payload = {"event_id": event_id, "event_digest": event.get("digest"),
                   "base_version": base, "object_ref": f"{object_id}@{version}"}
        envelope_digest = _product_digest({key: val for key, val in envelope.items() if key != "event_digest"})
        if (not _content_digest_valid(event) or not _versioned_object_digest_valid(proposal)
                or event.get("event_id") != event_id or event.get("slot_id") != event_id
                or event.get("owner_id") != owners[event_id] or event.get("organization_id") != "org:northstar"
                or event.get("operation") != "UPDATE" or event.get("base_version") != base
                or event.get("base_digest") != initial_digests.get(f"{object_id}@{base}")
                or proposal.get("id") != object_id or proposal.get("version") != version
                or proposal.get("payload", {}).get("canonical_value") != value
                or proposal.get("state") != "PROPOSED" or not proposal.get("source_refs")
                or spec.get("object_id") != object_id or spec.get("base_version") != base
                or spec.get("proposed_version") != version
                or envelope.get("payload") != payload or envelope.get("event_digest") != envelope_digest
                or {key: val for key, val in envelope.items() if key != "payload"} != record):
            failures.append("CHANGE_EVENT_REGISTRATION_BINDING_INVALID")
    return failures


def _current_tool_commitments(evidence: dict[str, Any], frozen: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Reconstruct the tool result from the sealed initial graph, not its claimed result."""
    snapshot = evidence.get("formation_graph_snapshot", {})
    formation = evidence.get("formation", {})
    tool = evidence.get("dependency_evidence_tool", {})
    invocation = tool.get("invocation", {})
    result = invocation.get("result", {})
    failures = []
    if (not _content_digest_valid(snapshot) or not _content_digest_valid(formation)
            or f"{snapshot.get('id')}@{snapshot.get('version')}" != formation.get("graph_snapshot_ref")):
        failures.append("TOOL_INITIAL_GRAPH_BINDING_INVALID")
    commits = evidence.get("formation_commit_events", [])
    records = _event_records_of_type(evidence.get("event_chain"), "WORKSPACE_TASK_COMMITTED")
    if len(commits) != 1 or len(records) != 1:
        failures.append("TOOL_INITIAL_GRAPH_BINDING_INVALID")
    else:
        commit = commits[0]
        body = commit.get("payload", {})
        if (body.get("snapshot_digest") != snapshot.get("digest")
                or body.get("snapshot_ref") != formation.get("graph_snapshot_ref")
                or body.get("task_receipt_digest") != formation.get("digest")
                or body.get("deliverable_ref") != formation.get("deliverable_ref")
                or commit.get("event_digest") != _product_digest({key: val for key, val in commit.items() if key != "event_digest"})
                or {key: val for key, val in commit.items() if key != "payload"} != records[0]):
            failures.append("TOOL_INITIAL_GRAPH_BINDING_INVALID")
    expected_sources = {"claim:product.enterprise_plan@v4", "claim:product.launch_date@v7",
                        "claim:product.residency_capability@v3", "claim:legal.customer_notice_required@v3",
                        "policy:finance.price_band@v7", "policy:finance.currency@v1",
                        "claim:gtm.partner_terms@v2", "skill:enterprise-quote-compose@1.0"}
    graph_edges = [edge for edge in snapshot.get("edges", [])
                   if edge.get("consumer_ref") == "work:quote_acme@v1"]
    if {edge.get("provider_ref") for edge in graph_edges} != expected_sources or len(graph_edges) != 8:
        failures.append("TOOL_INITIAL_SOURCE_SET_INVALID")
    projected = []
    for edge in sorted(graph_edges, key=lambda item: item["id"]):
        if not _content_digest_valid(edge):
            failures.append("TOOL_INITIAL_EDGE_DIGEST_INVALID")
        core = {"id": edge["id"], "source_id": edge["provider_ref"].rsplit("@", 1)[0],
                "target_id": "work:quote_acme", "relation": edge["relation"], "strength": edge["strength"],
                "coverage_basis": edge["coverage_basis"], "status": "ADMITTED",
                "provenance_refs": [edge["source_manifest_ref"], edge["source_evidence_ref"],
                                    f"source-version:{edge['provider_ref']}", "consumer-version:work:quote_acme@v1"]}
        projected.append({"edge_id": core["id"], **{key: value for key, value in core.items() if key != "id"},
                          "edge_digest": _product_digest(core)})
    coverage = result.get("coverage", [])
    if len(coverage) != 1 or coverage[0].get("object_id") != "work:quote_acme":
        failures.append("TOOL_RESULT_COMMITMENT_MISMATCH")
    else:
        manifest = coverage[0].get("dependency_manifest", {})
        slots = manifest.get("requirement_slots", [])
        expected_slots = {"product_plan": "claim:product.enterprise_plan", "launch_date": "claim:product.launch_date",
                          "data_residency": "claim:product.residency_capability", "notice_required": "claim:legal.customer_notice_required",
                          "price_band": "policy:finance.price_band", "currency": "policy:finance.currency",
                          "partner_terms": "claim:gtm.partner_terms", "quote_compose_skill": "skill:enterprise-quote-compose"}
        if (not _content_digest_valid(manifest) or manifest.get("target_id") != "work:quote_acme"
                or manifest.get("target_version") != "v1" or manifest.get("completeness") != "COMPLETE"
                or len(slots) != 8 or {slot.get("edge_id") for slot in slots} != {edge["edge_id"] for edge in projected}
                or {slot.get("slot_id"): slot.get("source_id") for slot in slots}
                != {f"slot:work:quote_acme:{key}": value for key, value in expected_slots.items()}):
            failures.append("TOOL_RESULT_COMMITMENT_MISMATCH")
        for slot in slots:
            edge = next((item for item in projected if item["edge_id"] == slot.get("edge_id")), {})
            if any(slot.get(key) != edge.get(key) for key in ("source_id", "relation", "strength", "coverage_basis")):
                failures.append("TOOL_RESULT_COMMITMENT_MISMATCH")
    expected_result = {"graph_revision": formation.get("revision_lock", {}).get("graph"),
                       "edges": projected, "coverage": coverage}
    if result != expected_result:
        failures.append("TOOL_RESULT_COMMITMENT_MISMATCH")
    expected = {**frozen, "result_digest": _product_digest(expected_result)}
    # A called event now binds exact versioned provenance. Its content address is
    # independently recomputed by _tool_binding_failures and the audit receipt.
    expected.pop("called_event_digest", None)
    return expected, failures


def _product_oracle(surface: dict[str, Any], gold: dict[str, Any]) -> list[str]:
    quote_export = surface.get("quote_export")
    evidence_export = surface.get("evidence_export")
    if not isinstance(quote_export, dict) or not isinstance(evidence_export, dict):
        return ["PRODUCT_EXPORT_MISSING"]
    expected = gold["product_oracle"]
    current = gold.get("_runtime_contract") == CURRENT_RUNTIME_CONTRACT
    failures: list[str] = []
    revision = _protocol_revision(gold["benchmark_version"])
    if quote_export.get("schema_version") != f"orgrebase.workspace-quote-export.v{revision}":
        failures.append("QUOTE_EXPORT_SCHEMA_INVALID")
    if evidence_export.get("schema_version") != f"orgrebase.workspace-evidence-export.v{revision}":
        failures.append("EVIDENCE_EXPORT_SCHEMA_INVALID")
    if revision == 2:
        failures.extend(
            _source_bound_failures(
                quote_export,
                evidence_export,
                expected_contract=gold.get("_source_bound_contract"),
            )
        )
    for exported in (quote_export, evidence_export):
        state_schema = exported.get("workspace_state_schema_version")
        if current and state_schema == CURRENT_STATE_SCHEMA:
            stage_valid = exported.get("stage") == "CURRENT" and exported.get("business_complete") is True
        elif not current and state_schema is None:
            stage_valid = exported.get("stage") == expected["final_stage"]
        else:
            stage_valid = False
        if not stage_valid:
            failures.append("STAGE_MISMATCH")
    quote = quote_export.get("quote")
    evidence_quote = evidence_export.get("quote")
    if not isinstance(quote, dict) or not isinstance(evidence_quote, dict):
        failures.append("QUOTE_MISSING")
    else:
        payload = quote.get("payload")
        evidence_payload = evidence_quote.get("payload")
        if (
            quote != evidence_quote
            or quote.get("version") != expected["quote_version"]
            or evidence_quote.get("version") != expected["quote_version"]
            or (not current and quote.get("digest") != expected["quote_digest"])
            or (not current and evidence_quote.get("digest") != expected["quote_digest"])
            or (current and not _versioned_object_digest_valid(quote))
            or not isinstance(payload, dict)
            or not isinstance(evidence_payload, dict)
            or payload.get("launch_date") != expected["launch_date"]
            or evidence_payload.get("launch_date") != expected["launch_date"]
            or payload.get("currency") != expected["currency"]
            or evidence_payload.get("currency") != expected["currency"]
        ):
            failures.append("QUOTE_FIELD_MISMATCH")
        if current:
            final_outcome = evidence_export.get("changes", {}).get("currency", {}).get("outcome", {}).get("outcome", {})
            delta = final_outcome.get("change_set", {}).get("deltas", [{}])[0]
            contexts = final_outcome.get("rebase_receipt", {}).get("context_manifests", [])
            context = next((item for item in contexts if item.get("target_object_id") == "work:quote_acme"), {})
            business_fields = {"owner": "sales-owner", "deliverable_kind": "QUOTE", "customer_id": "customer:acme",
                               "product_plan": "enterprise-plan-v4", "launch_date": expected["launch_date"],
                               "data_residency": "US region supported", "notice_required": True,
                               "price_band": "strategic", "currency": expected["currency"],
                               "partner_terms_code": "legal-review", "rebased_from": "work:quote_acme@v2",
                               "rebase_change_set": delta.get("digest"), "context_manifest": context.get("digest")}
            snapshot = evidence_export.get("graph_snapshot", {})
            quote_ref = f"{quote.get('id')}@{quote.get('version')}"
            bound_quote = next((item for item in snapshot.get("object_digests", []) if item.get("object_ref") == quote_ref), {})
            if (quote.get("id") != "work:quote_acme" or quote.get("state") != "CURRENT"
                    or quote.get("source_refs") != [evidence_export.get("formation", {}).get("context_manifest_ref")]
                    or payload != business_fields or final_outcome.get("quote") != quote
                    or not _content_digest_valid(context) or not _content_digest_valid(snapshot)
                    or bound_quote.get("digest") != quote.get("digest")):
                failures.append("QUOTE_FIELD_MISMATCH")
    if not _export_digest_valid(quote_export):
        failures.append("QUOTE_EXPORT_DIGEST_INVALID")
    if not _export_digest_valid(evidence_export):
        failures.append("EVIDENCE_EXPORT_DIGEST_INVALID")
    expected_event_records = expected.get("event_records")
    event_types = expected.get("event_types")
    stable_event_records = expected.get("stable_event_records")
    if current and isinstance(event_types, list):
        event_types = [event_types[0], "WORKSPACE_CHANGE_REGISTERED", "WORKSPACE_CHANGE_REGISTERED", *event_types[1:]]
        stable_event_records = stable_event_records[:1]
    if (
        expected_event_records is None
        and isinstance(event_types, list)
        and isinstance(stable_event_records, list)
    ):
        expected_event_records = [
            {"sequence_no": index, "event_type": event_type}
            for index, event_type in enumerate(event_types, start=1)
        ]
        for stable in stable_event_records:
            if (
                isinstance(stable, dict)
                and isinstance(stable.get("sequence_no"), int)
                and 0 < stable["sequence_no"] <= len(expected_event_records)
            ):
                expected_event_records[stable["sequence_no"] - 1] = stable
    failures.extend(
        _event_link_failures(
            evidence_export.get("event_chain"), expected_event_records
        )
    )
    if "task_intake_commitments" in expected:
        failures.extend(
            _task_intake_binding_failures(
                evidence_export, expected["task_intake_commitments"]
            )
        )
    tool = evidence_export.get("dependency_evidence_tool")
    tool_commitments = expected.get("tool_commitments", {})
    if current:
        failures.extend(_registered_change_failures(evidence_export, expected["approval_owners"]))
        tool_commitments, tool_failures = _current_tool_commitments(evidence_export, tool_commitments)
        failures.extend(tool_failures)
    failures.extend(
        _tool_binding_failures(
            tool,
            tool_commitments,
            event_chain=evidence_export.get("event_chain"),
        )
    )
    changes = evidence_export.get("changes")
    if not isinstance(changes, dict):
        failures.append("OWNER_MISSING")
    else:
        for kind, owner in expected["approval_owners"].items():
            try:
                observed_owner = changes[kind]["approval"]["approval"]["actor_id"]
            except (KeyError, TypeError):
                observed_owner = None
            if observed_owner != owner:
                failures.append("OWNER_MISMATCH")
    failures.extend(_approval_binding_failures(evidence_export, expected["approval_owners"]))
    if "approval_control_commitments" in expected:
        failures.extend(
            _approval_review_gate_failures(
                evidence_export,
                expected["approval_owners"],
                expected["approval_control_commitments"],
            )
        )
    quote_rfc = _rfc8785_export_classification(quote_export)
    evidence_rfc = _rfc8785_export_classification(evidence_export)
    if quote_rfc != "MATCH":
        failures.append("RFC8785_QUOTE_CLASSIFICATION_MISMATCH")
    if evidence_rfc != "NUMERIC_REPRESENTATION_DIFFERENCE":
        failures.append("RFC8785_EVIDENCE_CLASSIFICATION_MISMATCH")
    return sorted(set(failures))


def _matches(expected: Any, observed: Any, *, prefix: str = "") -> list[str]:
    failures: list[str] = []
    if isinstance(expected, dict):
        if not isinstance(observed, dict):
            return [f"{prefix or '<root>'}:EXPECTED_OBJECT"]
        for key, value in expected.items():
            path = f"{prefix}.{key}" if prefix else key
            if key not in observed:
                failures.append(f"{path}:MISSING")
            else:
                failures.extend(_matches(value, observed[key], prefix=path))
        return failures
    if expected != observed:
        failures.append(f"{prefix}:EXPECTED={expected!r}:OBSERVED={observed!r}")
    return failures


def _set_path(value: dict[str, Any], path: list[Any], replacement: Any) -> None:
    cursor: Any = value
    for item in path[:-1]:
        cursor = cursor[item]
    cursor[path[-1]] = replacement


def _single_byte_tamper(quote_export: dict[str, Any]) -> dict[str, Any]:
    encoded = bytearray(rfc8785.dumps(quote_export))
    needle = b'"currency":"EUR"'
    start = bytes(encoded).find(needle)
    if start < 0:
        raise ValueError("canonical Quote export does not contain the expected currency")
    offset = start + len(b'"currency":"')
    before = bytes(encoded)
    encoded[offset] = ord("X")
    after = bytes(encoded)
    changed_bytes = sum(left != right for left, right in zip(before, after, strict=True))
    tampered = json.loads(after)
    return {
        "status": "PASS" if changed_bytes == 1 and not _export_digest_valid(tampered) else "FAIL",
        "changed_bytes": changed_bytes,
        "byte_offset": offset,
        "before_byte": chr(before[offset]),
        "after_byte": chr(after[offset]),
        "tampered_document_parseable": isinstance(tampered, dict),
        "tampered_digest_accepted": _export_digest_valid(tampered),
    }


def _case_fact_failures(
    case_id: str,
    facts: dict[str, Any],
    expected: dict[str, Any],
    expected_keys: Any,
    *,
    current: bool = False,
) -> list[str]:
    failures: list[str] = []
    if not isinstance(expected_keys, list) or set(facts) != set(expected_keys):
        failures.append("CASE_FACT_SET_MISMATCH")
    if case_id in {"PP-001", "PP-002", "PP-003"}:
        process_ids = facts.get("process_ids")
        if (
            not isinstance(process_ids, list)
            or len(process_ids) != 2
            or any(not isinstance(value, int) or value <= 0 for value in process_ids)
            or len(set(process_ids)) != 2
        ):
            failures.append("RESTART_PROCESS_EVIDENCE_INVALID")
    if case_id == "PP-001":
        if facts.get("event_count") != (14 if current else 12):
            failures.append("PRIMARY_EVENT_COUNT_MISMATCH")
        if not _digest_string_valid(facts.get("event_chain_head")):
            failures.append("PRIMARY_EVENT_HEAD_INVALID")
    if case_id == "PP-012":
        overlap_ms = facts.get("forced_overlap_ms")
        if not isinstance(overlap_ms, int) or overlap_ms < 700:
            failures.append("FORCED_OVERLAP_DURATION_INVALID")
        if facts.get("observed_stages") != (["APPROVED"] if current else ["LAUNCH_APPROVED"]):
            failures.append("CONCURRENT_STATE_SET_INVALID")
    return failures


def _reseal_export(payload: dict[str, Any]) -> None:
    document = {key: value for key, value in payload.items() if key != "digest"}
    payload["digest"] = _product_digest(document)


def _set_all_integral_floats_to_strings(value: Any) -> int:
    changed = 0
    if isinstance(value, dict):
        for key, item in list(value.items()):
            if isinstance(item, float) and item.is_integer():
                value[key] = f"{item:.1f}"
                changed += 1
            else:
                changed += _set_all_integral_floats_to_strings(item)
    elif isinstance(value, list):
        for index, item in enumerate(list(value)):
            if isinstance(item, float) and item.is_integer():
                value[index] = f"{item:.1f}"
                changed += 1
            else:
                changed += _set_all_integral_floats_to_strings(item)
    return changed


def _integrity_attack_results(
    surface: dict[str, Any], gold: dict[str, Any]
) -> list[dict[str, Any]]:
    attacks: list[tuple[str, str, dict[str, Any]]] = []

    event_reseal = copy.deepcopy(surface)
    chain = event_reseal["evidence_export"]["event_chain"]
    previous = ZERO_DIGEST
    for index, record in enumerate(chain["records"], start=1):
        record["previous_digest"] = previous
        record["event_digest"] = _product_digest(
            {
                "sequence_no": index,
                "event_type": record.get("event_type"),
                "attacker_payload": f"forged-{index}",
                "previous_digest": previous,
            }
        )
        previous = record["event_digest"]
    chain["head_digest"] = previous
    _reseal_export(event_reseal["evidence_export"])
    attacks.append(
        ("PP-ATTACK-EVENT-RESEAL", "EVENT_RECORD_COMMITMENT_MISMATCH", event_reseal)
    )

    approval_reseal = copy.deepcopy(surface)
    launch = approval_reseal["evidence_export"]["changes"]["launch_date"]
    forged = "sha256:" + "f" * 64
    launch["approval"]["approval"]["digest"] = forged
    launch["approval"]["approval_digest"] = forged
    launch["outcome"]["outcome"]["approval_digest"] = forged
    launch["outcome"]["outcome"]["rebase_receipt"]["approval_digest"] = forged
    _reseal_export(approval_reseal["evidence_export"])
    attacks.append(("PP-ATTACK-APPROVAL-DIGEST", "APPROVAL_DIGEST_INVALID", approval_reseal))

    failed_tool = copy.deepcopy(surface)
    tool = failed_tool["evidence_export"]["dependency_evidence_tool"]
    tool["status"] = "FAILED"
    receipt = tool["invocation"]["receipt"]
    receipt["status"] = "FAILED"
    receipt["digest"] = _product_digest(
        {key: value for key, value in receipt.items() if key != "digest"}
    )
    tool["invocation_artifact_digest"] = _product_digest(tool["invocation"])
    _reseal_export(failed_tool["evidence_export"])
    attacks.append(("PP-ATTACK-FAILED-TOOL", "TOOL_STATUS_MISMATCH", failed_tool))

    forged_tool = copy.deepcopy(surface)
    tool = forged_tool["evidence_export"]["dependency_evidence_tool"]
    tool["invocation"]["result"]["graph_revision"] = "graph:attacker@v999"
    result_digest = _product_digest(tool["invocation"]["result"])
    receipt = tool["invocation"]["receipt"]
    receipt["result_digest"] = result_digest
    receipt["digest"] = _product_digest(
        {key: value for key, value in receipt.items() if key != "digest"}
    )
    called_event = tool["called_event"]
    called_event["result_digest"] = result_digest
    called_event["digest"] = _product_digest(
        {key: value for key, value in called_event.items() if key != "digest"}
    )
    tool["invocation_artifact_digest"] = _product_digest(tool["invocation"])
    tool["called_event_artifact_digest"] = _product_digest(called_event)
    _reseal_export(forged_tool["evidence_export"])
    attacks.append(
        ("PP-ATTACK-FORGED-TOOL-RESULT", "TOOL_RESULT_COMMITMENT_MISMATCH", forged_tool)
    )

    nonnumeric = copy.deepcopy(surface)
    changed = _set_all_integral_floats_to_strings(nonnumeric["evidence_export"])
    _reseal_export(nonnumeric["evidence_export"])
    attacks.append(
        (
            "PP-ATTACK-NONNUMERIC-RFC-MISMATCH",
            "RFC8785_EVIDENCE_CLASSIFICATION_MISMATCH",
            nonnumeric,
        )
    )

    results: list[dict[str, Any]] = []
    for attack_id, expected_failure, candidate in attacks:
        observed = _product_oracle(candidate, gold)
        results.append(
            {
                "id": attack_id,
                "status": "REJECTED" if expected_failure in observed else "ACCEPTED",
                "expected_failure": expected_failure,
                "observed_failures": observed,
                **(
                    {"integral_floats_changed": changed}
                    if attack_id == "PP-ATTACK-NONNUMERIC-RFC-MISMATCH"
                    else {}
                ),
            }
        )
    return results


def evaluate(
    *,
    observations: dict[str, Any],
    gold: dict[str, Any],
    mutations: dict[str, Any],
    evaluator_path: Path,
    gold_path: Path,
    mutations_path: Path,
    manifest_path: Path,
    artifact_manifest: dict[str, Any],
    artifact_paths: dict[str, Path],
    project_root: Path,
) -> dict[str, Any]:
    benchmark_version = gold.get("benchmark_version")
    if not isinstance(benchmark_version, str):
        raise ValueError("Gold benchmark_version must be a string")
    revision = _protocol_revision(benchmark_version)
    benchmark_root = manifest_path.parent
    manifest = _manifest_status(
        manifest_path, benchmark_version=benchmark_version
    )
    migration = _migration_status(
        project_root=project_root,
        benchmark_root=benchmark_root,
        benchmark_version=benchmark_version,
    )
    migration_record = (
        _object(benchmark_root / "BENCHMARK-MIGRATION.json")
        if revision == 2
        else {}
    )
    source_bound_contract = migration_record.get("source_bound_contract")
    task_intake_bound_contract = migration_record.get(
        "task_intake_bound_contract"
    )
    evaluation_gold = {
        **gold,
        "_source_bound_contract": source_bound_contract,
    }
    runtime_contract, runtime_failures = _runtime_contract(observations)
    evaluation_gold["_runtime_contract"] = runtime_contract
    current = runtime_contract == CURRENT_RUNTIME_CONTRACT
    corpus_failures: list[str] = list(runtime_failures)
    expected_corpus_schemas = {
        "gold": f"orgrebase.product-path-evaluator-gold.v{revision}",
        "mutations": f"orgrebase.product-path-evaluator-mutations.v{revision}",
    }
    if gold.get("schema_version") != expected_corpus_schemas["gold"]:
        corpus_failures.append("GOLD_SCHEMA_INVALID")
    if mutations.get("schema_version") != expected_corpus_schemas["mutations"]:
        corpus_failures.append("MUTATIONS_SCHEMA_INVALID")
    if mutations.get("benchmark_version") != benchmark_version:
        corpus_failures.append("MUTATIONS_BENCHMARK_INVALID")
    if benchmark_version == DEFAULT_BENCHMARK_VERSION:
        product_oracle = gold.get("product_oracle", {})
        if not isinstance(task_intake_bound_contract, dict):
            corpus_failures.append("TASK_INTAKE_BOUND_CONTRACT_MISSING")
        elif (
            product_oracle.get("task_intake_commitments")
            != {
                key: task_intake_bound_contract[key]
                for key in (
                    "candidate_status",
                    "confirmation_status",
                    "run_status",
                    "actor_id",
                    "customer_id",
                    "deliverable_kind",
                    "natural_language_authority",
                    "canonical_target_writes",
                    "event_type",
                )
            }
            or product_oracle.get("approval_control_commitments")
            != {
                key: task_intake_bound_contract[key]
                for key in (
                    "review_duration_ms",
                    "review_gate_store",
                    "review_gate_restart_enforced",
                    "identity_mode",
                    "identity_claim_boundary",
                    "external_iam",
                )
            }
        ):
            corpus_failures.append("TASK_INTAKE_BOUND_GOLD_MISMATCH")
    artifact_status = _artifact_manifest_status(
        artifact_manifest,
        project_root=project_root,
        paths=artifact_paths,
        benchmark_version=benchmark_version,
    )
    retained_bundle_failures = _retained_bundle_failures(
        observations,
        artifact_status=artifact_status,
        wheel_path=artifact_paths["packaged_wheel"],
        runner_path=artifact_paths["runner_source"],
        benchmark_version=benchmark_version,
        case_count=gold["case_count"],
    )
    evaluator_product_imports = _product_import_count(evaluator_path)
    observations_digest_valid = _claimed_rfc8785_digest_valid(observations)
    public_entry = next(
        (
            entry
            for entry in manifest["entries"]
            if entry.get("path") == "public/cases.json"
        ),
        {},
    )
    public_cases_binding = (
        observations.get("execution", {}).get("public_cases_sha256")
        == f"sha256:{public_entry.get('expected_sha256')}"
    )
    expected_cases = {case["id"]: case for case in gold["cases"]}
    observed_cases = {
        case.get("id"): case
        for case in observations.get("cases", [])
        if isinstance(case, dict) and isinstance(case.get("id"), str)
    }
    unexpected_case_ids = sorted(set(observed_cases) - set(expected_cases))
    case_results: list[dict[str, Any]] = []
    for case_id, expected in expected_cases.items():
        expected_keys = gold.get("case_fact_keys", {}).get(case_id)
        if current and isinstance(expected_keys, list):
            expected, expected_keys = _current_case_contract(expected, expected_keys)
        observed = observed_cases.get(case_id)
        if observed is None:
            failures = ["CASE_MISSING"]
            facts: dict[str, Any] = {}
        else:
            facts_value = observed.get("facts")
            facts = facts_value if isinstance(facts_value, dict) else {}
            failures = list(observed.get("execution_failures", []))
            failures.extend(_matches(expected["expected_facts"], facts))
            failures.extend(
                _case_fact_failures(
                    case_id,
                    facts,
                    expected,
                    expected_keys,
                    current=current,
                )
            )
        case_results.append(
            {
                "id": case_id,
                "title": expected["title"],
                "status": "PASS" if not failures else "FAIL",
                "failures": failures,
                "facts": facts,
            }
        )

    oracle_surface = observations.get("oracle_surface")
    oracle_failures = (
        _product_oracle(oracle_surface, evaluation_gold)
        if isinstance(oracle_surface, dict)
        else ["PRODUCT_EXPORT_MISSING"]
    )
    source_bound_summary = _source_bound_summary(
        oracle_surface,
        revision=revision,
        expected_contract=source_bound_contract,
    )
    tamper = (
        _single_byte_tamper(oracle_surface["quote_export"])
        if isinstance(oracle_surface, dict)
        and isinstance(oracle_surface.get("quote_export"), dict)
        else {"status": "FAIL", "reason": "QUOTE_EXPORT_MISSING"}
    )
    rfc8785_probe = {
        "quote_export": (
            _rfc8785_export_classification(oracle_surface.get("quote_export"))
            if isinstance(oracle_surface, dict)
            else "EXPORT_MISSING"
        ),
        "evidence_export": (
            _rfc8785_export_classification(oracle_surface.get("evidence_export"))
            if isinstance(oracle_surface, dict)
            else "EXPORT_MISSING"
        ),
        "explanation": (
            "The current product digest dialect preserves integral floats such as 1.0; "
            "RFC 8785 emits the mathematically equivalent number as 1."
        ),
    }
    for result in case_results:
        if result["id"] == "PP-011":
            extra_failures = list(oracle_failures)
            if tamper.get("status") != "PASS":
                extra_failures.append("SINGLE_BYTE_TAMPER_NOT_REJECTED")
            result["failures"].extend(extra_failures)
            result["status"] = "PASS" if not result["failures"] else "FAIL"

    mutation_results: list[dict[str, Any]] = []
    if isinstance(oracle_surface, dict) and not oracle_failures:
        for mutation in mutations["mutations"]:
            candidate = copy.deepcopy(oracle_surface)
            _set_path(candidate, mutation["path"], mutation["replacement"])
            failures = _product_oracle(candidate, evaluation_gold)
            expected_failure = mutation["expected_failure"]
            killed = expected_failure in failures
            mutation_results.append(
                {
                    "id": mutation["id"],
                    "category": mutation["category"],
                    "status": "KILLED" if killed else "SURVIVED",
                    "expected_failure": expected_failure,
                    "observed_failures": failures,
                }
            )
    else:
        for mutation in mutations["mutations"]:
            mutation_results.append(
                {
                    "id": mutation["id"],
                    "category": mutation["category"],
                    "status": "NOT_RUN",
                    "expected_failure": mutation["expected_failure"],
                    "observed_failures": oracle_failures,
                }
            )

    integrity_attacks = (
        _integrity_attack_results(oracle_surface, evaluation_gold)
        if isinstance(oracle_surface, dict) and not oracle_failures
        else []
    )
    attacks_rejected = sum(item["status"] == "REJECTED" for item in integrity_attacks)
    cases_passed = sum(result["status"] == "PASS" for result in case_results)
    mutations_killed = sum(result["status"] == "KILLED" for result in mutation_results)
    all_pass = (
        cases_passed == gold["case_count"]
        and mutations_killed == len(mutation_results)
        and not oracle_failures
        and tamper.get("status") == "PASS"
        and manifest["status"] == "PASS"
        and migration["status"] == "PASS"
        and not corpus_failures
        and artifact_status["status"] == "PASS"
        and not retained_bundle_failures
        and evaluator_product_imports == 0
        and observations_digest_valid
        and public_cases_binding
        and not unexpected_case_ids
        and len(integrity_attacks) == 5
        and attacks_rejected == 5
    )
    report: dict[str, Any] = {
        "schema_version": f"orgrebase.product-path-blackbox.v{revision}",
        "benchmark_version": gold["benchmark_version"],
        **({"runtime_contract": runtime_contract} if current else {}),
        "status": "PASS" if all_pass else "FAIL",
        "evidence_class": "LOCAL_REAL_HTTP_BLACKBOX",
        "execution": observations.get("execution", {}),
        "independent_evaluator": {
            "status": "PASS" if not oracle_failures else "FAIL",
            "implementation": "SEPARATE_PROCESS_NO_ORGREBASE_IMPORT",
            "product_imports": evaluator_product_imports,
            "canonicalization": "INDEPENDENT_PRODUCT_CANONICAL_JSON_V1_PLUS_RFC8785_PROBE",
            "digest_algorithm": "SHA-256",
            "oracle_failures": oracle_failures,
            "rfc8785_probe": rfc8785_probe,
            "event_scope": (
                "public sequence/predecessor/head linkage; stable predecessor events remain "
                "frozen while nonce-derived Intake/Tool/Approval events are bound to the "
                "same-run exported receipts; event payload envelopes are not exposed"
            ),
            "receipt_binding_scope": (
                "independent Preview/Approval/RebaseReceipt content digests and exact "
                "Preview/Owner/Outcome bindings"
            ),
            "tool_binding_scope": (
                "raw receipt/result/called-event content digests, request reconstruction, "
                "frozen request/result/called-event commitments, same-run audit-event and "
                "invocation-artifact bindings, and zero target writes"
            ),
            "task_intake_binding_scope": (
                "candidate/confirmation/run receipt content addresses, exact run and dynamic "
                "workspace nonce equality, Profile/Task/Formation/Quote bindings, and the "
                "WORKSPACE_TASK_INTAKE_BOUND event"
                if benchmark_version == DEFAULT_BENCHMARK_VERSION
                else "NOT_APPLICABLE_TO_PREDECESSOR_BENCHMARK"
            ),
            "approval_review_gate_scope": (
                "persisted four-second Preview gate, controlled-local Header identity, "
                "Approval binding, not-before evidence and Approval event cross-binding"
                if benchmark_version == DEFAULT_BENCHMARK_VERSION
                else "NOT_APPLICABLE_TO_PREDECESSOR_BENCHMARK"
            ),
            "source_bound_scope": (
                "Profile/Admission/Source/Runtime/Binding digest relations, five Northstar "
                "component roots, 5/5 runtime projection equivalence and zero canonical writes"
                if revision == 2
                else "NOT_APPLICABLE_TO_HISTORICAL_V0_1"
            ),
            "source_bound_oracle": source_bound_summary,
            "evaluator_sha256": hashlib.sha256(evaluator_path.read_bytes()).hexdigest(),
        },
        "case_summary": {
            "total": gold["case_count"],
            "passed": cases_passed,
            "failed": gold["case_count"] - cases_passed,
        },
        "unexpected_case_ids": unexpected_case_ids,
        "cases": case_results,
        "single_byte_tamper": tamper,
        "mutation_summary": {
            "total": len(mutation_results),
            "killed": mutations_killed,
            "survived": len(mutation_results) - mutations_killed,
            "score": (
                mutations_killed / len(mutation_results) if mutation_results else 0.0
            ),
        },
        "mutations": mutation_results,
        "integrity_attack_summary": {
            "total": len(integrity_attacks),
            "rejected": attacks_rejected,
            "accepted": len(integrity_attacks) - attacks_rejected,
        },
        "integrity_attacks": integrity_attacks,
        "boundaries": gold["boundaries"],
        "claim_boundary": (
            (
                "Twelve local black-box cases exercise one frozen synthetic Northstar/Acme "
                "profile with a Task Intake gate and controlled-local Header identity. External "
                "participants are zero; authenticated production identity, arbitrary-enterprise "
                "generalization, live Workspace AgentTeams, scale and ROI are not claimed."
            )
            if benchmark_version == DEFAULT_BENCHMARK_VERSION
            else (
                "Twelve local black-box cases exercise one frozen synthetic Northstar/Acme "
                "profile. External participants are zero; arbitrary-enterprise generalization, "
                "production identity, live Workspace AgentTeams, scale and ROI are not claimed."
            )
        ),
        "source_bindings": {
            "separation": "RUNNER_PUBLIC_CASES_ONLY_EVALUATOR_OWNS_GOLD_AND_MUTATIONS",
            "public_cases_binding": "PASS" if public_cases_binding else "FAIL",
            "observations_digest_valid": observations_digest_valid,
            "gold_sha256": hashlib.sha256(gold_path.read_bytes()).hexdigest(),
            "mutations_sha256": hashlib.sha256(mutations_path.read_bytes()).hexdigest(),
            "observations_rfc8785_digest": _rfc8785_digest(observations),
            "manifest": manifest,
            "benchmark_migration": migration,
            "corpus_failures": corpus_failures,
            "artifact_manifest": artifact_status,
            "retained_bundle": {
                "status": "PASS" if not retained_bundle_failures else "FAIL",
                "failures": retained_bundle_failures,
                "raw_observations": _logical_path(
                    artifact_paths["raw_observations"], project_root
                ),
                "packaged_wheel": _logical_path(
                    artifact_paths["packaged_wheel"], project_root
                ),
            },
        },
    }
    if current:
        report["independent_evaluator"].update({
            "event_scope": "14 exact event types; immutable seed commitment; two complete ChangeEvent contents and registration envelopes independently hashed and bound to sequence; same-run Intake/Tool/Approval receipt binding",
            "tool_binding_scope": "independent projection of eight exact source versions from the sealed Formation graph; request/result/called-event digests; coverage slots, same-run audit and invocation bindings; zero target writes",
            "quote_binding_scope": "independent VersionedObject digest excluding mutable trust state; exact business fields, quote version, source reference, successor context and final graph object commitment",
        })
    report["digest"] = _rfc8785_digest(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--mutations", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--artifact-manifest", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    observations = _object(args.observations)
    gold = _object(args.gold)
    mutations = _object(args.mutations)
    artifact_manifest, artifact_paths = build_artifact_manifest(
        project_root=args.project_root,
        runner_path=args.runner,
        evaluator_path=Path(__file__),
        observations_path=args.observations,
        wheel_path=args.wheel,
        public_cases_path=args.manifest.parent / "public" / "cases.json",
        gold_path=args.gold,
        mutations_path=args.mutations,
        benchmark_manifest_path=args.manifest,
        benchmark_version=str(gold.get("benchmark_version")),
    )
    args.artifact_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.artifact_manifest.write_text(
        json.dumps(artifact_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report = evaluate(
        observations=observations,
        gold=gold,
        mutations=mutations,
        evaluator_path=Path(__file__),
        gold_path=args.gold,
        mutations_path=args.mutations,
        manifest_path=args.manifest,
        artifact_manifest=artifact_manifest,
        artifact_paths=artifact_paths,
        project_root=args.project_root,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "cases": report["case_summary"],
                "mutations": report["mutation_summary"],
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    if report["status"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
