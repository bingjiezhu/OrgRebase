"""GOAI-facing executable AgentTeams reference-run contract and verifier."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.domain import AgentCandidateIngestionReceipt

CONFIG_SCHEMA = "orgrebase.goai-agentteams-config.v2"
REQUEST_SCHEMA = "orgrebase.goai-agentteams-request.v1"
SUMMARY_SCHEMA = "orgrebase.goai-agentteams-run-summary.v2"
VERIFY_SCHEMA = "orgrebase.goai-agentteams-verification.v2"

REFERENCE_CLASSIFICATION = {
    "evidence_class": "LOCAL_DETERMINISTIC_REFERENCE",
    "semantic_acceptance": "REFERENCE_FIXTURE_ONLY",
    "autonomous_collaboration": False,
    "current_workspace_live": "NOT_RUN",
}
HISTORICAL_TRANSPORT_CLASSIFICATION = {
    "status": "TRANSPORT_ONLY",
    "evidence_class": "TRANSPORT_ONLY",
    "semantic_acceptance": "NOT_ESTABLISHED",
    "autonomous_collaboration": False,
    "current_workspace_live": "NOT_RUN",
}
FRESH_CORE_CLASSIFICATION = {
    "status": "PASS",
    "evidence_class": "LIVE_AGENTTEAMS",
    "scope_evidence_class": "LIVE_AGENTTEAMS_CORE_PROPOSAL_PLANE",
    "current_workspace_live": "NOT_RUN",
    "oac_runtime_bridge": "NOT_USED_IN_THIS_RUN",
}
PUBLIC_TRANSPORT_FIXTURE_SCHEMA = "orgrebase.agentteams-public-transport-fixture.v1"
FRESH_CORE_SUMMARY_SCHEMA = "orgrebase.agentteams-fresh-live-run-summary.v1"
LIVE_RECEIPT_SCHEMA = "orgrebase.agentteams-live-evidence.v2"
EXPECTED_PUBLIC_TRANSPORT_FIXTURE = "evidence/agentteams/public/historical-transport-v1.2.2.json"
EXPECTED_FRESH_CORE_EVIDENCE = {
    "bundle_dir": "evidence/agentteams/fresh-live/2026-08-25-561171ed039b",
    "run_summary": ("evidence/agentteams/fresh-live/2026-08-25-561171ed039b/RUN-SUMMARY.json"),
    "live_receipt": ("evidence/agentteams/fresh-live/2026-08-25-561171ed039b/live-receipt.json"),
    "semantic_ingestion": ("evidence/agentteams/fresh-live/2026-08-25-561171ed039b/semantic-ingestion.json"),
    "checksums": ("evidence/agentteams/fresh-live/2026-08-25-561171ed039b/SHA256SUMS"),
}
PRIVATE_RUNTIME_EVIDENCE_PATHS = frozenset(
    {
        "evidence/agentteams/live-receipt.json",
        "evidence/agentteams/candidate-ingestion-receipt.json",
        "evidence/agentteams/preflight.json",
        "evidence/agentteams/vertex-provider-probe.json",
    }
)
PUBLIC_TRANSPORT_FIXTURE_FIELDS = frozenset(
    {
        "schema_version",
        *HISTORICAL_TRANSPORT_CLASSIFICATION,
        "agentteams_version",
        "source_commit",
        "worker_count",
        "provider_call_count",
        "source_receipt_digest",
        "source_receipt_raw_sha256",
        "sanitization",
        "claim_boundary",
    }
)
EXPECTED_AGENTTEAMS_VERSION = "v1.2.2"
EXPECTED_AGENTTEAMS_SOURCE_COMMIT = "849182af8e017168a5a200a87b1062142caf462d"
EXPECTED_EXECUTION_TRANSPORT = "DIRECT_OPENCLAW_GATEWAY_NOT_MATRIX_INBOUND"
EXPECTED_MATRIX_ROLE = "IDENTITY_PUBLICATION_COLLECTION_NOT_INBOUND_DELEGATION"
_SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
_CHECKSUM_LINE_PATTERN = re.compile(r"([0-9a-f]{64})  \./(.+)\Z")
EXPECTED_PACKAGE_PATHS = {
    "entrypoint": "uv run orgrebase agentteams-demo",
    "sample_input": "examples/agentteams/change-request.json",
    "sample_output": "examples/agentteams/run-summary.example.json",
    "runtime_evidence": "evidence/goai-agentteams/latest/run-summary.json",
}

EXPECTED_CHANGE = {
    "object_id": "claim:product.launch_date",
    "base_version": "v7",
    "base_value": "2026-09-01",
    "proposed_version": "v8",
    "proposed_value": "2026-09-15",
}
EXPECTED_TEAM = (
    "change-coordinator",
    "product-steward",
    "legal-steward",
    "gtm-steward",
    "skill-curator",
)
EXPECTED_SPECIALISTS = EXPECTED_TEAM[1:]


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"INVALID_JSON:{path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def _raw_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_PATTERN.fullmatch(value) is not None


def _agentteams_paths(config: dict[str, Any]) -> tuple[str, dict[str, str]]:
    agentteams = config.get("agentteams")
    if not isinstance(agentteams, dict):
        raise ValueError("GOAI_AGENTTEAMS_CONFIG_MISSING")
    historical = agentteams.get("historical_transport_fixture")
    fresh = agentteams.get("fresh_core_evidence")
    configured_paths = {
        value
        for value in (
            historical,
            *(fresh.values() if isinstance(fresh, dict) else ()),
        )
        if isinstance(value, str)
    }
    if configured_paths & PRIVATE_RUNTIME_EVIDENCE_PATHS:
        raise ValueError("GOAI_PRIVATE_RUNTIME_EVIDENCE_FORBIDDEN")
    if historical != EXPECTED_PUBLIC_TRANSPORT_FIXTURE:
        raise ValueError("GOAI_PUBLIC_TRANSPORT_FIXTURE_PATH_MISMATCH")
    if fresh != EXPECTED_FRESH_CORE_EVIDENCE:
        raise ValueError("GOAI_FRESH_CORE_EVIDENCE_PATHS_MISMATCH")
    return historical, dict(fresh)


def _verify_checksum_manifest(bundle_dir: Path, manifest_path: Path) -> int:
    try:
        lines = manifest_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError("GOAI_FRESH_CORE_CHECKSUMS_UNREADABLE") from exc
    seen: set[str] = set()
    for line in lines:
        match = _CHECKSUM_LINE_PATTERN.fullmatch(line)
        if match is None:
            raise ValueError("GOAI_FRESH_CORE_CHECKSUM_LINE_INVALID")
        expected, relative = match.groups()
        relative_path = Path(relative)
        if (
            relative_path.is_absolute()
            or ".." in relative_path.parts
            or relative in seen
            or relative == "SHA256SUMS"
        ):
            raise ValueError("GOAI_FRESH_CORE_CHECKSUM_PATH_INVALID")
        seen.add(relative)
        artifact = bundle_dir / relative_path
        if not artifact.is_file() or artifact.is_symlink():
            raise ValueError(f"GOAI_FRESH_CORE_CHECKSUM_ARTIFACT_MISSING:{relative}")
        if _raw_sha256(artifact) != f"sha256:{expected}":
            raise ValueError(f"GOAI_FRESH_CORE_CHECKSUM_MISMATCH:{relative}")
    required = {"RUN-SUMMARY.json", "live-receipt.json", "semantic-ingestion.json"}
    if not lines or not required <= seen:
        raise ValueError("GOAI_FRESH_CORE_CHECKSUM_COVERAGE_MISSING")
    actual_files: set[str] = set()
    for artifact in bundle_dir.rglob("*"):
        if artifact.is_symlink():
            raise ValueError("GOAI_FRESH_CORE_SYMLINK_FORBIDDEN")
        if artifact.is_file():
            actual_files.add(artifact.relative_to(bundle_dir).as_posix())
    if actual_files != seen | {"SHA256SUMS"}:
        raise ValueError("GOAI_FRESH_CORE_CHECKSUM_COVERAGE_MISMATCH")
    return len(seen)


def load_agentteams_evidence(
    *, config: dict[str, Any], project_root: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load the publishable historical fixture and the frozen fresh Core bundle.

    Private root-level runtime receipts are deliberately not accepted here.  The
    returned views expose only the facts needed by GOAI surfaces and preserve the
    distinct evidence classes of the two sources.
    """

    historical_relative, fresh_relatives = _agentteams_paths(config)
    agentteams = config["agentteams"]
    expected_version = agentteams["version"]
    expected_commit = agentteams["source_commit"]

    historical_path = project_root / historical_relative
    historical_source = _load_object(historical_path)
    if set(historical_source) != PUBLIC_TRANSPORT_FIXTURE_FIELDS:
        raise ValueError("GOAI_PUBLIC_TRANSPORT_FIXTURE_FIELDS_MISMATCH")
    historical_boundary = {key: historical_source.get(key) for key in HISTORICAL_TRANSPORT_CLASSIFICATION}
    if historical_source.get("schema_version") != PUBLIC_TRANSPORT_FIXTURE_SCHEMA:
        raise ValueError("GOAI_PUBLIC_TRANSPORT_FIXTURE_SCHEMA_UNSUPPORTED")
    if historical_boundary != HISTORICAL_TRANSPORT_CLASSIFICATION:
        raise ValueError("GOAI_PUBLIC_TRANSPORT_FIXTURE_BOUNDARY_MISMATCH")
    if (
        historical_source.get("agentteams_version") != expected_version
        or historical_source.get("source_commit") != expected_commit
        or historical_source.get("worker_count") != 5
        or historical_source.get("provider_call_count") != 4
    ):
        raise ValueError("GOAI_PUBLIC_TRANSPORT_FIXTURE_METADATA_MISMATCH")
    expected_sanitization = {
        "contains_raw_runtime_sources": False,
        "contains_credentials": False,
        "contains_provider_request_ids": False,
        "contains_matrix_event_ids": False,
        "contains_prompts_or_outputs": False,
    }
    if historical_source.get("sanitization") != expected_sanitization:
        raise ValueError("GOAI_PUBLIC_TRANSPORT_FIXTURE_SANITIZATION_MISMATCH")
    if not _is_sha256(historical_source.get("source_receipt_digest")) or not _is_sha256(
        historical_source.get("source_receipt_raw_sha256")
    ):
        raise ValueError("GOAI_PUBLIC_TRANSPORT_FIXTURE_DIGEST_INVALID")
    if not historical_source.get("claim_boundary"):
        raise ValueError("GOAI_PUBLIC_TRANSPORT_FIXTURE_CLAIM_BOUNDARY_MISSING")
    historical = {
        **HISTORICAL_TRANSPORT_CLASSIFICATION,
        "agentteams_version": historical_source["agentteams_version"],
        "source_commit": historical_source["source_commit"],
        "worker_count": historical_source["worker_count"],
        "provider_call_count": historical_source["provider_call_count"],
        # The legacy receipt_path field remains for existing readers, but now
        # resolves to an allowlisted public summary rather than private evidence.
        "receipt_path": historical_relative,
        "fixture_path": historical_relative,
        "receipt_raw_sha256": _raw_sha256(historical_path),
        "fixture_raw_sha256": _raw_sha256(historical_path),
        "source_receipt_digest": historical_source["source_receipt_digest"],
        "source_receipt_raw_sha256": historical_source["source_receipt_raw_sha256"],
        "sanitization": expected_sanitization,
        "claim_boundary": historical_source["claim_boundary"],
    }

    bundle_dir = project_root / fresh_relatives["bundle_dir"]
    checksum_path = project_root / fresh_relatives["checksums"]
    checksummed_files = _verify_checksum_manifest(bundle_dir, checksum_path)
    fresh_summary_path = project_root / fresh_relatives["run_summary"]
    live_path = project_root / fresh_relatives["live_receipt"]
    ingestion_path = project_root / fresh_relatives["semantic_ingestion"]
    fresh_summary = _load_object(fresh_summary_path)
    live = _load_object(live_path)
    ingestion = AgentCandidateIngestionReceipt.model_validate(_load_object(ingestion_path))

    if (
        fresh_summary.get("schema_version") != FRESH_CORE_SUMMARY_SCHEMA
        or fresh_summary.get("status") != "PASS"
        or fresh_summary.get("evidence_class") != FRESH_CORE_CLASSIFICATION["scope_evidence_class"]
    ):
        raise ValueError("GOAI_FRESH_CORE_SUMMARY_CLASSIFICATION_MISMATCH")
    run_id = fresh_summary.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("GOAI_FRESH_CORE_RUN_ID_MISSING")
    runtime = fresh_summary.get("runtime")
    if not isinstance(runtime, dict) or (
        runtime.get("agentteams_version") != expected_version
        or runtime.get("agentteams_source_commit") != expected_commit
        or runtime.get("coordinator_identities") != 1
        or runtime.get("specialist_identities") != 4
        or runtime.get("execution_transport") != EXPECTED_EXECUTION_TRANSPORT
    ):
        raise ValueError("GOAI_FRESH_CORE_RUNTIME_MISMATCH")
    model = runtime.get("model")
    if not isinstance(model, str) or not model:
        raise ValueError("GOAI_FRESH_CORE_MODEL_MISSING")

    executions = fresh_summary.get("provider_executions")
    if not isinstance(executions, dict) or (
        executions.get("successful") != 4
        or executions.get("failed_in_frozen_candidate_set") != 0
        or tuple(executions.get("workers", ())) != EXPECTED_SPECIALISTS
    ):
        raise ValueError("GOAI_FRESH_CORE_PROVIDER_EXECUTIONS_MISMATCH")
    semantic_summary = fresh_summary.get("semantic_ingestion")
    if not isinstance(semantic_summary, dict) or {
        key: semantic_summary.get(key)
        for key in ("accepted", "rejected", "target_writes", "admitted_effects")
    } != {
        "accepted": 4,
        "rejected": 0,
        "target_writes": 0,
        "admitted_effects": 0,
    }:
        raise ValueError("GOAI_FRESH_CORE_SEMANTIC_SUMMARY_MISMATCH")

    live_summary = fresh_summary.get("live_receipt")
    if not isinstance(live_summary, dict) or (
        live_summary.get("status") != "PASS" or live_summary.get("source_evidence_class") != "LIVE_AGENTTEAMS"
    ):
        raise ValueError("GOAI_FRESH_CORE_LIVE_SUMMARY_MISMATCH")
    if (
        live.get("schema_version") != LIVE_RECEIPT_SCHEMA
        or live.get("status") != "PASS"
        or live.get("evidence_class") != "LIVE_AGENTTEAMS"
        or live.get("receipt_digest") != live_summary.get("receipt_digest")
    ):
        raise ValueError("GOAI_FRESH_CORE_LIVE_RECEIPT_MISMATCH")
    live_evidence = live.get("evidence")
    if (
        not isinstance(live_evidence, dict)
        or live_evidence.get("run_id") != run_id
        or live.get("receipt_digest") != sha256_digest(live_evidence)
    ):
        raise ValueError("GOAI_FRESH_CORE_LIVE_RUN_MISMATCH")
    live_agentteams = live_evidence.get("agentteams")
    live_kubernetes = live_evidence.get("kubernetes")
    live_model_calls = live_evidence.get("model_calls")
    live_artifacts = live_evidence.get("artifacts")
    live_matrix = live_evidence.get("matrix")
    if not all(
        isinstance(value, dict)
        for value in (
            live_agentteams,
            live_kubernetes,
            live_model_calls,
            live_artifacts,
            live_matrix,
        )
    ):
        raise ValueError("GOAI_FRESH_CORE_LIVE_EVIDENCE_MISSING")
    if live_agentteams != {
        "version": expected_version,
        "source_commit": expected_commit,
    }:
        raise ValueError("GOAI_FRESH_CORE_LIVE_SOURCE_LOCK_MISMATCH")
    workers = live_kubernetes.get("workers")
    candidate_artifacts = live_artifacts.get("candidate_artifacts")
    candidate_events = live_matrix.get("candidate_event_ids")
    runtime_skill = live_artifacts.get("skill")
    worker_names = (
        {worker.get("worker_name") for worker in workers if isinstance(worker, dict)}
        if isinstance(workers, list)
        else set()
    )
    if (
        not isinstance(workers, list)
        or len(workers) != 5
        or worker_names != set(EXPECTED_TEAM)
        or live_model_calls.get("successful_calls") != 4
        or live_model_calls.get("successful_workers") != sorted(EXPECTED_SPECIALISTS)
        or live_model_calls.get("models") != [model]
        or not isinstance(candidate_artifacts, list)
        or len(candidate_artifacts) != 4
        or not isinstance(candidate_events, list)
        or len(candidate_events) != 4
        or not isinstance(runtime_skill, dict)
        or not runtime_skill.get("name")
        or runtime_skill.get("assigned_worker") != "skill-curator"
        or not _is_sha256(runtime_skill.get("digest"))
    ):
        raise ValueError("GOAI_FRESH_CORE_LIVE_COUNTS_MISMATCH")

    if (
        ingestion.run_id != run_id
        or ingestion.live_receipt_digest != live["receipt_digest"]
        or ingestion.digest != semantic_summary.get("receipt_digest")
        or len(ingestion.decisions) != 4
        or len(ingestion.admitted_candidate_digests) != 4
        or ingestion.rejected_candidate_digests
        or ingestion.target_writes != 0
        or any(
            decision.decision != "ADVISORY_ACCEPTED" or decision.admitted_effects
            for decision in ingestion.decisions
        )
        or ingestion.source_evidence_class.value != "LIVE_AGENTTEAMS"
        or ingestion.verifier_evidence_class.value != "LOCAL_DETERMINISTIC"
    ):
        raise ValueError("GOAI_FRESH_CORE_INGESTION_MISMATCH")
    authority = fresh_summary.get("authority_boundary")
    if authority != {
        "candidate_only": True,
        "approval_authority_granted": False,
        "apply_authority_granted": False,
        "canonical_write_authority_granted": False,
    }:
        raise ValueError("GOAI_FRESH_CORE_AUTHORITY_BOUNDARY_MISMATCH")
    adjacent = fresh_summary.get("adjacent_status")
    if adjacent != {
        "workspace_live": "NOT_RUN",
        "oac_runtime_bridge": "NOT_USED_IN_THIS_RUN",
        "historical_run": "TRANSPORT_ONLY",
    }:
        raise ValueError("GOAI_FRESH_CORE_ADJACENT_STATUS_MISMATCH")
    publication = fresh_summary.get("matrix_publication")
    if (
        not isinstance(publication, dict)
        or publication.get("candidate_events") != 4
        or "not evidence of Matrix-inbound Worker task delivery" not in str(publication.get("claim_boundary"))
    ):
        raise ValueError("GOAI_FRESH_CORE_MATRIX_PUBLICATION_MISMATCH")
    excluded_material = fresh_summary.get("excluded_material")
    if not isinstance(excluded_material, list) or not {
        "private-sessions",
        "credentials and secrets",
        "complete dispatch logs",
    } <= set(excluded_material):
        raise ValueError("GOAI_FRESH_CORE_EXCLUSION_BOUNDARY_MISMATCH")
    if not fresh_summary.get("claim_boundary"):
        raise ValueError("GOAI_FRESH_CORE_CLAIM_BOUNDARY_MISSING")

    reason_codes = sorted({reason for decision in ingestion.decisions for reason in decision.reason_codes})
    fresh = {
        **FRESH_CORE_CLASSIFICATION,
        "run_id": run_id,
        "agentteams_version": expected_version,
        "source_commit": expected_commit,
        "execution_transport": EXPECTED_EXECUTION_TRANSPORT,
        "matrix_role": EXPECTED_MATRIX_ROLE,
        "worker_count": 5,
        "coordinator_identities": 1,
        "specialist_identities": 4,
        "successful_provider_executions": 4,
        "candidate_artifacts": 4,
        "candidate_events": 4,
        "model_ids": [model],
        "runtime_skill": {
            "name": runtime_skill.get("name"),
            "digest": runtime_skill["digest"],
            "assigned_worker": runtime_skill["assigned_worker"],
        },
        "semantic_ingestion": {
            "status": "PASS",
            "receipt_digest": ingestion.digest,
            "orchestration_plan_digest": ingestion.orchestration_plan_digest,
            "evaluated": len(ingestion.decisions),
            "advisory_accepted": len(ingestion.admitted_candidate_digests),
            "rejected": len(ingestion.rejected_candidate_digests),
            "target_writes": ingestion.target_writes,
            "admitted_effects": sum(len(decision.admitted_effects) for decision in ingestion.decisions),
            "rejection_reason_counts": {
                reason: sum(reason in decision.reason_codes for decision in ingestion.decisions)
                for reason in reason_codes
            },
        },
        "bundle_path": fresh_relatives["bundle_dir"],
        "run_summary_path": fresh_relatives["run_summary"],
        "run_summary_raw_sha256": _raw_sha256(fresh_summary_path),
        "live_receipt_path": fresh_relatives["live_receipt"],
        "live_receipt_digest": live["receipt_digest"],
        "live_receipt_raw_sha256": _raw_sha256(live_path),
        "semantic_ingestion_path": fresh_relatives["semantic_ingestion"],
        "semantic_ingestion_raw_sha256": _raw_sha256(ingestion_path),
        "checksums_path": fresh_relatives["checksums"],
        "checksums_raw_sha256": _raw_sha256(checksum_path),
        "checksummed_files": checksummed_files,
        "claim_boundary": fresh_summary.get("claim_boundary"),
    }
    return historical, fresh


def load_run_contract(config_path: Path, request_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load and fail-closed validate the one currently supported competition run."""

    config = _load_object(config_path)
    request = _load_object(request_path)
    if config.get("schema_version") != CONFIG_SCHEMA:
        raise ValueError("GOAI_CONFIG_SCHEMA_UNSUPPORTED")
    if request.get("schema_version") != REQUEST_SCHEMA:
        raise ValueError("GOAI_REQUEST_SCHEMA_UNSUPPORTED")
    if config.get("profile") != request.get("scenario") or config.get("profile") != "core-change-advisory":
        raise ValueError("GOAI_SCENARIO_UNSUPPORTED")
    if request.get("organization_id") != "org:northstar":
        raise ValueError("GOAI_ORGANIZATION_UNSUPPORTED")
    if request.get("requested_by") != "human:product-owner" or request.get("purpose") != "change_rebase":
        raise ValueError("GOAI_AUTHORITY_OR_PURPOSE_MISMATCH")
    if request.get("change") != EXPECTED_CHANGE:
        raise ValueError("GOAI_CHANGE_OUTSIDE_FROZEN_FIXTURE")
    if tuple(request.get("expected_team", ())) != EXPECTED_TEAM:
        raise ValueError("GOAI_TEAM_ROSTER_MISMATCH")
    agentteams = config.get("agentteams")
    if not isinstance(agentteams, dict):
        raise ValueError("GOAI_AGENTTEAMS_CONFIG_MISSING")
    if (
        agentteams.get("version") != EXPECTED_AGENTTEAMS_VERSION
        or agentteams.get("source_commit") != EXPECTED_AGENTTEAMS_SOURCE_COMMIT
    ):
        raise ValueError("GOAI_AGENTTEAMS_SOURCE_LOCK_MISMATCH")
    _agentteams_paths(config)
    required_outputs = config.get("required_outputs")
    if not isinstance(required_outputs, list) or "run-summary.json" not in required_outputs:
        raise ValueError("GOAI_REQUIRED_OUTPUTS_INVALID")
    if config.get("run_classification") != REFERENCE_CLASSIFICATION:
        raise ValueError("GOAI_RUN_CLASSIFICATION_MISMATCH")
    if any(config.get(key) != value for key, value in EXPECTED_PACKAGE_PATHS.items()):
        raise ValueError("GOAI_PACKAGE_PATHS_MISMATCH")
    return config, request


def build_run_summary(
    *,
    demo: dict[str, Any],
    output_dir: Path,
    config: dict[str, Any],
    request: dict[str, Any],
    project_root: Path,
) -> dict[str, Any]:
    """Build the small, judge-facing index over the detailed evidence pack."""

    collaboration = demo.get("collaboration")
    receipt = demo.get("receipt")
    if not isinstance(collaboration, dict) or not isinstance(receipt, dict):
        raise ValueError("GOAI_DEMO_COLLABORATION_OR_RECEIPT_MISSING")
    runs = collaboration.get("agent_runs")
    handoffs = collaboration.get("handoffs")
    tool_invocations = collaboration.get("tool_invocations")
    if not isinstance(runs, list) or not isinstance(handoffs, list) or not isinstance(tool_invocations, list):
        raise ValueError("GOAI_DEMO_EXECUTION_EVIDENCE_MISSING")

    coordination = collaboration.get("coordination_receipt")
    if not isinstance(coordination, dict) or coordination.get("status") != "PASS":
        raise ValueError("GOAI_COORDINATION_NOT_VERIFIED")
    tool_receipts = [item.get("receipt", {}) for item in tool_invocations if isinstance(item, dict)]
    if not tool_receipts or any(item.get("status") != "SUCCEEDED" for item in tool_receipts):
        raise ValueError("GOAI_TOOL_INVOCATION_NOT_VERIFIED")

    failure = _load_object(output_dir / "failure-receipt.json")
    conflict = _load_object(output_dir / "conflict-receipt.json")
    rollback = _load_object(output_dir / "rollback-receipt.json")
    manifest = _load_object(output_dir / "manifest.json")
    historical_transport, fresh_core = load_agentteams_evidence(config=config, project_root=project_root)

    required = tuple(str(item) for item in config["required_outputs"] if item != "run-summary.json")
    artifact_digests: dict[str, str] = {}
    for relative in required:
        path = output_dir / relative
        if not path.is_file():
            raise ValueError(f"GOAI_REQUIRED_OUTPUT_MISSING:{relative}")
        artifact_digests[relative] = _raw_sha256(path)

    run_agents = tuple(str(item.get("agent_name")) for item in runs if isinstance(item, dict))
    if run_agents != EXPECTED_TEAM:
        raise ValueError("GOAI_EXECUTED_TEAM_ROSTER_MISMATCH")
    task_graph = collaboration.get("task_graph") if isinstance(collaboration.get("task_graph"), dict) else {}
    qualification = receipt.get("qualification_report")
    if not isinstance(qualification, dict):
        raise ValueError("GOAI_SKILL_QUALIFICATION_MISSING")

    summary = {
        "schema_version": SUMMARY_SCHEMA,
        "status": "PASS",
        "execution_profile": config["execution_mode"],
        "run_classification": dict(REFERENCE_CLASSIFICATION),
        "input": {
            "request_digest": sha256_digest(request),
            "scenario": request["scenario"],
            "change": request["change"],
        },
        "agent_collaboration": {
            "framework_mapping": task_graph.get("manager_workers"),
            "agent_count": len(runs),
            "agents": list(run_agents),
            "structured_handoffs": len(handoffs),
            "candidate_only": bool(task_graph.get("candidate_only")),
            "orchestration_plan_digest": task_graph.get("plan_digest"),
            "coordination_receipt_digest": coordination.get("digest"),
            "coordination_status": coordination.get("status"),
        },
        "tool_call": {
            "count": len(tool_receipts),
            "tool_refs": [item.get("tool_ref") for item in tool_receipts],
            "statuses": [item.get("status") for item in tool_receipts],
            "receipt_digests": [item.get("digest") for item in tool_receipts],
        },
        "output": {
            "rebase_status": receipt.get("status"),
            "approval_actor_id": receipt.get("approval_actor_id"),
            "work_items_rebased": receipt.get("metrics", {}).get("work_items_rebased"),
            "unauthorized_disclosures": receipt.get("metrics", {}).get("unauthorized_disclosures"),
            "false_invalidations": receipt.get("metrics", {}).get("false_invalidations"),
            "skill_candidate_state": qualification.get("candidate_state"),
            "event_chain": demo.get("event_chain"),
        },
        "exception_and_recovery": {
            "authority_conflict": conflict.get("status"),
            "expired_preview": {
                "status": failure.get("status"),
                "error_code": failure.get("error_code"),
                "target_writes": failure.get("target_writes"),
            },
            "rollback": {
                "status": rollback.get("status"),
                "authoritative_claims_changed": rollback.get("metrics", {}).get(
                    "authoritative_claims_changed"
                ),
                "work_items_compensated": rollback.get("metrics", {}).get("work_items_compensated"),
            },
        },
        "observability": {
            "structured_logs": "logs.otlp.json",
            "trace": "traces.otlp.json",
            "metrics": "metrics.otlp.json",
            "evidence_manifest_schema": manifest.get("schema_version"),
        },
        "historical_agentteams_transport": historical_transport,
        # Compatibility alias for v1 readers.  New consumers must use the scoped
        # name above; this object is TRANSPORT_ONLY, never a live semantic PASS.
        "frozen_live_agentteams_receipt": historical_transport,
        "fresh_core_agentteams_evidence": fresh_core,
        "artifact_raw_sha256": artifact_digests,
        "claim_boundaries": config["claim_boundaries"],
    }
    return summary


def verify_run_directory(output_dir: Path) -> dict[str, Any]:
    """Verify the compact summary and every detailed artifact it commits to."""

    summary_path = output_dir / "run-summary.json"
    summary = _load_object(summary_path)
    failures: list[str] = []
    if summary.get("schema_version") != SUMMARY_SCHEMA or summary.get("status") != "PASS":
        failures.append("SUMMARY_SCHEMA_OR_STATUS")
    classification = summary.get("run_classification")
    if classification != REFERENCE_CLASSIFICATION:
        failures.append("RUN_CLASSIFICATION")
    collaboration = summary.get("agent_collaboration")
    if not isinstance(collaboration, dict):
        failures.append("COLLABORATION_MISSING")
    else:
        if collaboration.get("agent_count") != 5 or tuple(collaboration.get("agents", ())) != EXPECTED_TEAM:
            failures.append("TEAM_ROSTER")
        if (
            collaboration.get("structured_handoffs") != 5
            or collaboration.get("coordination_status") != "PASS"
        ):
            failures.append("HANDOFF_OR_COORDINATION")
    tool = summary.get("tool_call")
    if not isinstance(tool, dict) or tool.get("count") != 1 or tool.get("statuses") != ["SUCCEEDED"]:
        failures.append("TOOL_CALL")
    output = summary.get("output")
    if not isinstance(output, dict) or output.get("rebase_status") != "COMPLETED":
        failures.append("OUTPUT_STATUS")
    exception = summary.get("exception_and_recovery")
    if not isinstance(exception, dict):
        failures.append("EXCEPTION_EVIDENCE")
    else:
        expired = exception.get("expired_preview", {})
        rollback = exception.get("rollback", {})
        if expired.get("status") != "REJECTED_BEFORE_WRITE" or expired.get("error_code") != "PREVIEW_EXPIRED":
            failures.append("FAIL_CLOSED_PREVIEW")
        if rollback.get("status") != "ROLLED_BACK_PENDING_REBASE":
            failures.append("ROLLBACK")
    historical = summary.get("historical_agentteams_transport")
    legacy_historical = summary.get("frozen_live_agentteams_receipt")
    if historical is None:
        historical = legacy_historical
    elif legacy_historical is not None and legacy_historical != historical:
        failures.append("HISTORICAL_TRANSPORT_ALIAS_MISMATCH")
    if not isinstance(historical, dict):
        failures.append("HISTORICAL_TRANSPORT_MISSING")
    else:
        boundary = {
            key: historical.get(key) for key in HISTORICAL_TRANSPORT_CLASSIFICATION
        }
        if boundary != HISTORICAL_TRANSPORT_CLASSIFICATION:
            failures.append("HISTORICAL_TRANSPORT_BOUNDARY")
        if (
            historical.get("worker_count") != 5
            or historical.get("agentteams_version") != "v1.2.2"
        ):
            failures.append("HISTORICAL_TRANSPORT_METADATA")
        if historical.get("fixture_path") != EXPECTED_PUBLIC_TRANSPORT_FIXTURE:
            failures.append("HISTORICAL_TRANSPORT_PUBLIC_FIXTURE")
    fresh = summary.get("fresh_core_agentteams_evidence")
    if not isinstance(fresh, dict):
        failures.append("FRESH_CORE_EVIDENCE_MISSING")
    else:
        fresh_boundary = {key: fresh.get(key) for key in FRESH_CORE_CLASSIFICATION}
        if fresh_boundary != FRESH_CORE_CLASSIFICATION:
            failures.append("FRESH_CORE_EVIDENCE_BOUNDARY")
        if (
            fresh.get("execution_transport") != EXPECTED_EXECUTION_TRANSPORT
            or fresh.get("matrix_role") != EXPECTED_MATRIX_ROLE
        ):
            failures.append("FRESH_CORE_TRANSPORT_BOUNDARY")
        if (
            fresh.get("worker_count") != 5
            or fresh.get("successful_provider_executions") != 4
            or fresh.get("candidate_artifacts") != 4
            or fresh.get("candidate_events") != 4
        ):
            failures.append("FRESH_CORE_EXECUTION_METADATA")
        semantic = fresh.get("semantic_ingestion")
        if not isinstance(semantic, dict) or {
            key: semantic.get(key)
            for key in (
                "status",
                "evaluated",
                "advisory_accepted",
                "rejected",
                "target_writes",
                "admitted_effects",
            )
        } != {
            "status": "PASS",
            "evaluated": 4,
            "advisory_accepted": 4,
            "rejected": 0,
            "target_writes": 0,
            "admitted_effects": 0,
        }:
            failures.append("FRESH_CORE_SEMANTIC_INGESTION")
        expected_fresh_paths = {
            "bundle_path": EXPECTED_FRESH_CORE_EVIDENCE["bundle_dir"],
            "run_summary_path": EXPECTED_FRESH_CORE_EVIDENCE["run_summary"],
            "live_receipt_path": EXPECTED_FRESH_CORE_EVIDENCE["live_receipt"],
            "semantic_ingestion_path": EXPECTED_FRESH_CORE_EVIDENCE["semantic_ingestion"],
            "checksums_path": EXPECTED_FRESH_CORE_EVIDENCE["checksums"],
        }
        if any(fresh.get(key) != value for key, value in expected_fresh_paths.items()):
            failures.append("FRESH_CORE_EVIDENCE_PATHS")
    digests = summary.get("artifact_raw_sha256")
    if not isinstance(digests, dict) or not digests:
        failures.append("ARTIFACT_DIGESTS_MISSING")
    else:
        for relative, expected in sorted(digests.items()):
            path = output_dir / str(relative)
            if not path.is_file():
                failures.append(f"MISSING:{relative}")
            elif _raw_sha256(path) != expected:
                failures.append(f"DIGEST:{relative}")
    return {
        "schema_version": VERIFY_SCHEMA,
        "status": "PASS" if not failures else "FAIL",
        "summary_digest": sha256_digest(summary),
        "verified_artifacts": len(digests) if isinstance(digests, dict) else 0,
        "failures": failures,
    }
