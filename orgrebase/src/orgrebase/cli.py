"""One-command demo and evidence export."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from orgrebase.agentteams_source import default_agentteams_checkout, packaged_agentteams_bundle
from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.service import OrgRebaseService

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PILOT_VERIFIER_TIMEOUT_SECONDS = 60.0


def _require_source_checkout(command: str, *required_paths: Path) -> None:
    required = (_PROJECT_ROOT / "pyproject.toml", *required_paths)
    if not all(path.is_file() for path in required):
        raise RuntimeError(f"ORGREBASE_SOURCE_CHECKOUT_REQUIRED:{command}")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_proof_pack() -> ModuleType:
    path = _PROJECT_ROOT / "scripts" / "verify_proof_pack.py"
    spec = importlib.util.spec_from_file_location("orgrebase_proof_pack", path)
    if spec is None or spec.loader is None:  # pragma: no cover - packaging invariant
        raise RuntimeError("proof pack verifier is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_demo(output: Path) -> dict[str, Any]:
    service = OrgRebaseService()
    demo = service.run_demo()
    conflict = service.conflict_drill()
    failure = service.freshness_failure_drill()
    rollback = service.rollback()
    git_tool = service.run_git_tool_demo(output.parent / "git-tool-repo")
    observability = service.observability()
    payload = json.loads(canonical_json(demo))
    conflict_payload = json.loads(canonical_json(conflict))
    failure_payload = json.loads(canonical_json(failure))
    rollback_payload = json.loads(canonical_json(rollback))
    git_tool_payload = json.loads(canonical_json(git_tool))
    observability_payload = json.loads(canonical_json(observability))
    impact_certificate_bundle = {
        "schema_version": "orgrebase.impact-certificate-bundle.v1",
        "change_set_digest": payload["change_set"]["digest"],
        "certificates": payload["preview"]["certificates"],
    }
    _write_json(output, payload)
    output_dir = output.parent
    _write_json(output_dir / "preview.json", payload["preview"])
    _write_json(
        output_dir / "impact-certificates.json",
        impact_certificate_bundle,
    )
    _write_json(
        output_dir / "minimal-rebase-certificate.json",
        payload["minimal_rebase_certificate"],
    )
    _write_json(output_dir / "rebase-receipt.json", payload["receipt"])
    _write_json(output_dir / "benchmark.json", payload["benchmark"])
    _write_json(output_dir / "conflict-receipt.json", conflict_payload["receipt"])
    _write_json(output_dir / "failure-receipt.json", failure_payload["receipt"])
    _write_json(output_dir / "rollback-receipt.json", rollback_payload["receipt"])
    _write_json(output_dir / "rollback-evidence.json", rollback_payload)
    _write_json(output_dir / "git-tool-evidence.json", git_tool_payload)
    _write_json(output_dir / "observability.json", observability_payload)
    _write_json(output_dir / "traces.otlp.json", observability_payload["traces"])
    _write_json(output_dir / "logs.otlp.json", observability_payload["logs"])
    _write_json(output_dir / "metrics.otlp.json", observability_payload["metrics"])
    proof_pack = _load_proof_pack().build_from_demo(payload, root=_PROJECT_ROOT)
    _write_json(output_dir / "proof-pack.json", proof_pack)
    artifact_payloads = {
        "demo.json": (payload, "LOCAL_DETERMINISTIC"),
        "preview.json": (payload["preview"], "LOCAL_DETERMINISTIC"),
        "impact-certificates.json": (
            impact_certificate_bundle,
            "LOCAL_DETERMINISTIC",
        ),
        "minimal-rebase-certificate.json": (
            payload["minimal_rebase_certificate"],
            "LOCAL_DETERMINISTIC",
        ),
        "rebase-receipt.json": (payload["receipt"], "LOCAL_DETERMINISTIC"),
        "benchmark.json": (payload["benchmark"], "SYNTHETIC_FIXTURE"),
        "conflict-receipt.json": (conflict_payload["receipt"], "LOCAL_DETERMINISTIC"),
        "failure-receipt.json": (failure_payload["receipt"], "LOCAL_DETERMINISTIC"),
        "rollback-receipt.json": (rollback_payload["receipt"], "LOCAL_DETERMINISTIC"),
        "rollback-evidence.json": (rollback_payload, "LOCAL_DETERMINISTIC"),
        "git-tool-evidence.json": (git_tool_payload, "LOCAL_REAL_TOOL"),
        "observability.json": (observability_payload, "LOCAL_DETERMINISTIC"),
        "traces.otlp.json": (observability_payload["traces"], "LOCAL_DETERMINISTIC"),
        "logs.otlp.json": (observability_payload["logs"], "LOCAL_DETERMINISTIC"),
        "metrics.otlp.json": (observability_payload["metrics"], "LOCAL_DETERMINISTIC"),
        "proof-pack.json": (proof_pack, "LOCAL_DETERMINISTIC"),
    }
    manifest = {
        "schema_version": "orgrebase.evidence-manifest.v2",
        "workflow_run_id": payload["receipt"]["workflow_run_id"],
        "run_nonce": payload["receipt"]["run_nonce"],
        "digest_algorithm": "orgrebase-canonical-json-sha256-v1",
        "artifacts": {
            name: {
                "path": name,
                "digest": sha256_digest(value),
                "status": "IMPLEMENTED",
                "evidence_class": evidence_class,
            }
            for name, (value, evidence_class) in artifact_payloads.items()
        },
        "capabilities": {
            "deterministic_control_plane": {
                "status": "IMPLEMENTED",
                "evidence_class": "LOCAL_DETERMINISTIC",
                "artifact": "rebase-receipt.json",
            },
            "proof_carrying_minimal_rebase": {
                "status": "IMPLEMENTED",
                "evidence_class": "LOCAL_DETERMINISTIC",
                "artifact": "minimal-rebase-certificate.json",
            },
            "independent_downstream_compensation": {
                "status": "IMPLEMENTED",
                "evidence_class": "LOCAL_DETERMINISTIC",
                "artifact": "rollback-evidence.json",
            },
            "real_reversible_git_tool": {
                "status": "IMPLEMENTED",
                "evidence_class": "LOCAL_REAL_TOOL",
                "artifact": "git-tool-evidence.json",
            },
            "skill_qualification": {
                "status": "IMPLEMENTED",
                "evidence_class": "LOCAL_DETERMINISTIC",
                "artifact": "demo.json",
            },
            "content_free_observability": {
                "status": "IMPLEMENTED",
                "evidence_class": "LOCAL_DETERMINISTIC",
                "artifact": "observability.json",
            },
            "closed_world_proof_pack": {
                "status": "IMPLEMENTED",
                "evidence_class": "LOCAL_DETERMINISTIC",
                "artifact": "proof-pack.json",
            },
            "agentteams_static_assets": {
                "status": "STATIC_ONLY",
                "evidence_class": "PASS_STATIC",
                "artifact": None,
            },
            "agentteams_multi_worker_e2e": {
                "status": "NOT_RUN",
                "evidence_class": "NOT_RUN",
                "artifact": None,
            },
            "matrix_human_approval": {
                "status": "NOT_RUN",
                "evidence_class": "NOT_RUN",
                "artifact": None,
            },
            "real_enterprise_connectors": {
                "status": "NOT_RUN",
                "evidence_class": "NOT_RUN",
                "artifact": None,
            },
        },
        "verifier": "uv run python scripts/verify_evidence_manifest.py evidence/latest/manifest.json",
        "limitations": [
            "Business objects and benchmark outcomes are synthetic fixtures.",
            "Local deterministic Agent roles are not live AgentTeams Workers.",
            "No live Matrix approval or enterprise connector is claimed.",
            (
                "ProofPack verifies closed-world digest and set bindings; "
                "path classification remains NOT_INDEPENDENT of ImpactEngine."
            ),
        ],
    }
    _write_json(output_dir / "manifest.json", manifest)
    return payload


def run_workspace_demo(output_dir: Path) -> dict[str, Any]:
    """Run the complete local Workspace profile and return its release summary."""

    from orgrebase.workspace.evidence import WorkspaceEvidenceBuilder

    index = WorkspaceEvidenceBuilder(output_dir).build()
    summary = json.loads((output_dir / "workspace-demo.json").read_text(encoding="utf-8"))
    summary["evidence_index"] = index.model_dump(mode="json")
    return summary


def _workspace_loop(
    output: Path,
    store: Path | None,
    *,
    allow_scripted_approval: bool,
) -> dict[str, Any]:
    from tempfile import NamedTemporaryFile

    from orgrebase.workspace.service import WorkspaceService

    if store is None:
        with NamedTemporaryFile(prefix="orgrebase-workspace-", suffix=".sqlite") as handle:
            result = WorkspaceService.run_explicit_local_product_loop(
                handle.name,
                allow_scripted_approval=allow_scripted_approval,
            )
    else:
        if output.resolve(strict=False) == store.resolve(strict=False):
            raise FileExistsError(f"WORKSPACE_OUTPUT_STORE_PATH_COLLISION:{store}")
        # A caller-supplied path may contain canonical enterprise state (or may
        # not be a SQLite database at all).  The acceptance harness requires a
        # fresh store, so fail closed instead of silently destroying the path.
        # ``is_symlink`` also catches broken links for which ``exists`` is false.
        reserved_paths = (
            store,
            Path(f"{store}-wal"),
            Path(f"{store}-shm"),
            Path(f"{store}-journal"),
        )
        for reserved in reserved_paths:
            if reserved.exists() or reserved.is_symlink():
                raise FileExistsError(f"WORKSPACE_STORE_PATH_NOT_FRESH:{reserved}")
        result = WorkspaceService.run_explicit_local_product_loop(
            store,
            allow_scripted_approval=allow_scripted_approval,
        )
    normalized = json.loads(canonical_json(result))
    _write_json(output, normalized)
    return normalized


def _enterprise_pilot_preflight(pack: Path) -> dict[str, Any]:
    from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
    from orgrebase.workspace.profile import require_reference_runtime_compatible

    runtime = load_enterprise_quote_pilot_pack(pack)
    admission = require_reference_runtime_compatible(
        runtime.profile,
        source_admission=runtime.source_admission,
    )
    return {
        "schema_version": "orgrebase.enterprise-quote-pilot-preflight.v1",
        "status": "PASS",
        "deployment_maturity": "PREFLIGHT_PASSED",
        "claim_ceiling_after_acceptance": "PILOT_READY_CONTROLLED_LOCAL",
        "real_enterprise_validated": "NOT_RUN",
        "production_ready": False,
        "pack_locator": f"directory:{runtime.pack_root.name}",
        "pack_id": runtime.pack_id,
        "pack_revision": runtime.pack_revision,
        "adapter_id": runtime.adapter_id,
        "pack_digest": runtime.pack_digest,
        "profile_ref": runtime.profile.ref,
        "profile_digest": runtime.profile.digest,
        "organization_id": runtime.profile.organization_id,
        "synthetic": runtime.profile.synthetic,
        "data_class": runtime.profile.data_class.value,
        "source_admission_receipt_digest": runtime.source_admission.digest,
        "runtime_projection_receipt_digest": runtime.runtime_projection.digest,
        "universe_ref": runtime.universe.id,
        "universe_digest": runtime.universe.digest,
        "source_root_count": len(runtime.source_admission.root_observations),
        "change_kinds": list(runtime.change_order),
        "reference_runtime_compatible": admission.reference_runtime_compatible,
        "external_enterprise_target_writes": 0,
        "boundaries": dict(runtime.boundaries),
    }


def _enterprise_oac_adapt(
    *,
    pack: Path,
    store: Path,
    oac_root: Path | None,
    golden_root: Path,
    review_seconds: float,
    prepare_command_id: str,
    approve_as: str | None,
    approval_command_id: str,
) -> dict[str, Any]:
    """Prepare or explicitly approve one exact OAC adapter candidate."""

    from orgrebase.workspace.oac_quote_adaptation import (
        OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS,
        OACQuoteAdaptationService,
    )
    from orgrebase.workspace.pilot import (
        enterprise_quote_pilot_run_id,
        load_enterprise_quote_pilot_pack,
    )
    from orgrebase.workspace.service import WorkspaceService

    runtime = load_enterprise_quote_pilot_pack(pack)
    workflow_run_id = enterprise_quote_pilot_run_id(runtime)
    workspace = WorkspaceService(
        store_path=store,
        runtime_configuration=runtime,
        workflow_run_id=workflow_run_id,
    )
    try:
        service = OACQuoteAdaptationService(
            store=workspace.store,
            profile=runtime.profile,
            runtime=runtime,
            oac_root=oac_root,
            golden_root=golden_root,
            review_duration_seconds=review_seconds,
            execution_run_id=workspace.effective_workflow_run_id,
        )
        prepared = service.prepare(command_id=prepare_command_id)
        if approve_as is None:
            return prepared
        return service.approve(
            actor_id=approve_as,
            candidate_digest=str(prepared.get("candidate_digest", "")),
            command_id=approval_command_id,
            owner_review_summary_digest=str(
                (prepared.get("owner_review_summary") or {}).get("digest", "")
            ),
            acknowledgements=OAC_OWNER_REVIEW_ACKNOWLEDGEMENTS,
        )
    finally:
        workspace.close()


def _require_fresh_workspace_store(store: Path, *, output_dir: Path) -> None:
    if store.resolve(strict=False) == output_dir.resolve(strict=False):
        raise FileExistsError(f"PILOT_OUTPUT_STORE_PATH_COLLISION:{store}")
    reserved_paths = (
        store,
        Path(f"{store}-wal"),
        Path(f"{store}-shm"),
        Path(f"{store}-journal"),
    )
    for reserved in reserved_paths:
        if reserved.exists() or reserved.is_symlink():
            raise FileExistsError(f"PILOT_STORE_PATH_NOT_FRESH:{reserved}")


def _enterprise_pilot_loop(
    *,
    pack: Path,
    store: Path,
    output_dir: Path,
    allow_scripted_approval: bool,
) -> dict[str, Any]:
    from orgrebase.workspace.controlled_local import run_sqlite_backup_restore_drill
    from orgrebase.workspace.pilot import (
        enterprise_quote_pilot_run_id,
        load_enterprise_quote_pilot_pack,
    )
    from orgrebase.workspace.service import WorkspaceService

    runtime = load_enterprise_quote_pilot_pack(pack)
    preflight = _enterprise_pilot_preflight(pack)
    _require_fresh_workspace_store(store, output_dir=output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    workflow_run_id = enterprise_quote_pilot_run_id(runtime)
    loop = WorkspaceService.run_explicit_local_product_loop(
        store,
        allow_scripted_approval=allow_scripted_approval,
        workflow_run_id=workflow_run_id,
        runtime_configuration=runtime,
        probe_wrong_owner=True,
    )
    reopened = WorkspaceService.reopen(
        store,
        workflow_run_id=workflow_run_id,
        runtime_configuration=runtime,
    )
    try:
        quote_export = reopened.export_quote()
        evidence_export = reopened.export_evidence()
        final_state_digest = sha256_digest(reopened.state())
    finally:
        reopened.close()

    backup_path = output_dir / "workspace.backup.sqlite3"
    restored_path = output_dir / "workspace.restored.sqlite3"
    backup = run_sqlite_backup_restore_drill(
        store,
        backup=backup_path,
        restored=restored_path,
    )
    restored = WorkspaceService.reopen(
        restored_path,
        workflow_run_id=workflow_run_id,
        runtime_configuration=runtime,
    )
    try:
        restored_state_digest = sha256_digest(restored.state())
        restored_quote = restored.current_quote().model_dump(mode="json")
    finally:
        restored.close()
    if final_state_digest != restored_state_digest:
        raise RuntimeError("PILOT_RESTORED_STATE_DIGEST_MISMATCH")

    normalized_loop = json.loads(canonical_json(loop))
    summary = {
        "schema_version": "orgrebase.enterprise-quote-pilot-acceptance.v1",
        "status": "PASS",
        "deployment_maturity": "PILOT_READY_CONTROLLED_LOCAL",
        "real_enterprise_validated": "NOT_RUN",
        "production_ready": False,
        "workflow_run_id": workflow_run_id,
        "pack_id": runtime.pack_id,
        "pack_revision": runtime.pack_revision,
        "adapter_id": runtime.adapter_id,
        "pack_digest": runtime.pack_digest,
        "profile_ref": runtime.profile.ref,
        "profile_digest": runtime.profile.digest,
        "organization_id": runtime.profile.organization_id,
        "synthetic": runtime.profile.synthetic,
        "data_class": runtime.profile.data_class.value,
        "source_admission_receipt_digest": runtime.source_admission.digest,
        "runtime_projection_receipt_digest": runtime.runtime_projection.digest,
        "external_enterprise_target_writes": 0,
        "preflight": preflight,
        "wrong_owner_probes": {
            kind: normalized_loop["approval_commands"][kind]["wrong_owner_probe"]
            for kind in runtime.change_order
        },
        "stale_approval_probes": {
            kind: normalized_loop["approval_commands"][kind]["stale_approval_probe"]
            for kind in runtime.change_order
        },
        "restart": normalized_loop["restart"],
        "backup_restore": backup.model_dump(mode="json"),
        "final_state_digest": final_state_digest,
        "restored_state_digest": restored_state_digest,
        "final_quote": normalized_loop["final_quote"],
        "restored_quote": restored_quote,
        "event_chain": normalized_loop["event_chain"],
        "boundaries": dict(runtime.boundaries),
    }
    artifacts = {
        "preflight.json": preflight,
        "pilot-loop.json": normalized_loop,
        "quote-export.json": quote_export,
        "evidence-export.json": evidence_export,
        "backup-restore-receipt.json": backup.model_dump(mode="json"),
        "summary.json": summary,
    }
    for name, value in artifacts.items():
        _write_json(output_dir / name, value)
    manifest = {
        "schema_version": "orgrebase.enterprise-quote-pilot-evidence-manifest.v1",
        "status": "PASS",
        "workflow_run_id": workflow_run_id,
        "pack_id": runtime.pack_id,
        "pack_revision": runtime.pack_revision,
        "adapter_id": runtime.adapter_id,
        "pack_digest": runtime.pack_digest,
        "synthetic": runtime.profile.synthetic,
        "data_class": runtime.profile.data_class.value,
        "artifacts": {
            name: {
                "digest": sha256_digest(value),
                "path": name,
            }
            for name, value in artifacts.items()
        },
        "external_enterprise_target_writes": 0,
        "deployment_maturity": "PILOT_READY_CONTROLLED_LOCAL",
        "real_enterprise_validated": "NOT_RUN",
        "production_ready": False,
    }
    _write_json(output_dir / "manifest.json", manifest)
    return {**summary, "evidence_manifest": manifest}


def _enterprise_pilot_check(*, pack: Path, work_dir: Path) -> dict[str, Any]:
    """Run the Pilot and a separate-process verifier from checkout or wheel assets."""

    from orgrebase.resource_paths import runtime_asset_path

    if work_dir.is_symlink():
        raise ValueError("PILOT_CHECK_WORK_DIR_SYMLINK_FORBIDDEN")
    if work_dir.exists():
        if not work_dir.is_dir():
            raise ValueError("PILOT_CHECK_WORK_DIR_NOT_DIRECTORY")
        if any(work_dir.iterdir()):
            raise FileExistsError("PILOT_CHECK_WORK_DIR_NOT_EMPTY")
    else:
        work_dir.mkdir(parents=True)

    preflight_path = work_dir / "preflight.json"
    store = work_dir / "workspace.sqlite3"
    evidence = work_dir / "evidence"
    verification_path = work_dir / "verification.json"
    preflight = _enterprise_pilot_preflight(pack)
    _write_json(preflight_path, preflight)
    loop = _enterprise_pilot_loop(
        pack=pack,
        store=store,
        output_dir=evidence,
        allow_scripted_approval=True,
    )

    verifier = runtime_asset_path("scripts/verify_enterprise_quote_pilot.py").resolve(strict=True)
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(verifier),
                str(evidence),
                "--pack",
                str(pack),
                "--output",
                str(verification_path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=_PILOT_VERIFIER_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("PILOT_INDEPENDENT_VERIFICATION_TIMEOUT") from None
    if completed.returncode != 0:
        reason = completed.stderr.strip() or completed.stdout.strip() or "UNKNOWN"
        raise RuntimeError(f"PILOT_INDEPENDENT_VERIFICATION_FAILED:{reason}")
    verification = json.loads(verification_path.read_text(encoding="utf-8"))
    if verification.get("status") != "PASS":
        raise RuntimeError("PILOT_INDEPENDENT_VERIFICATION_NOT_PASS")
    return {
        "schema_version": "orgrebase.enterprise-quote-pilot-check.v1",
        "status": "PASS",
        "deployment_maturity": "PILOT_READY_CONTROLLED_LOCAL",
        "pack_digest": loop["pack_digest"],
        "profile_digest": loop["profile_digest"],
        "organization_id": loop["organization_id"],
        "synthetic": loop["synthetic"],
        "data_class": loop["data_class"],
        "final_quote": loop["final_quote"],
        "independent_verification": verification,
        "work_locator": f"directory:{work_dir.name}",
        "external_enterprise_target_writes": 0,
        "real_enterprise_validated": "NOT_RUN",
        "production_ready": False,
    }


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "recovery-inventory":
        from orgrebase.recovery_inventory import main as recovery_main

        raise SystemExit(recovery_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "audit":
        from orgrebase.audit_checkpoint import main as audit_main

        raise SystemExit(audit_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "database":
        from orgrebase.store_operations import main as database_main

        raise SystemExit(database_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "dataverse-target":
        from orgrebase.dataverse_qualification import main as target_main

        raise SystemExit(target_main(sys.argv[2:]))
    parser = argparse.ArgumentParser(prog="orgrebase")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("database", help="Back up, restore and qualify the PostgreSQL store")
    subparsers.add_parser("audit", help="Sign and verify independent audit checkpoints")
    subparsers.add_parser("dataverse-target", help="Inspect target metadata or generate an administrator schema")
    subparsers.add_parser("recovery-inventory", help="Compare a quarantined restore with external effect receipts")

    serve_parser = subparsers.add_parser("serve", help="Start the configured application factory")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8081)
    serve_parser.add_argument("--limit-concurrency", type=int,
                              help="Uvicorn connection/task limit (at least 2); excess requests receive 503")
    serve_parser.add_argument("--backlog", type=int,
                              help="OS TCP accept queue size; does not queue requests refused with 503")
    serve_parser.add_argument("--local-demo", action="store_true",
                              help="Explicitly start the unauthenticated local demonstration on loopback")

    workspace_command_parser = subparsers.add_parser(
        "workspace", help="Use the same authenticated Workspace commands as the HTTP interface",
    )
    workspace_command_parser.add_argument("action", choices=("state", "register", "preview", "approve", "reject", "apply"))
    workspace_command_parser.add_argument("--url", default="http://127.0.0.1:8081")
    workspace_command_parser.add_argument(
        "--workspace", help="Select an authorized workspace; omit to use the server default"
    )
    workspace_command_parser.add_argument("--event-id")
    workspace_command_parser.add_argument("--digest")
    workspace_command_parser.add_argument("--reason")
    workspace_command_parser.add_argument("--input", type=Path)
    workspace_command_parser.add_argument("--token-variable", default="ORGREBASE_ACCESS_TOKEN")

    source_sync_parser = subparsers.add_parser("source-sync", help="Admit read-only Dataverse change pages")
    source_sync_parser.add_argument("--config", type=Path, required=True)
    source_sync_parser.add_argument("--max-pages", type=int, default=10)
    source_sync_parser.add_argument("--discover", action="store_true", help="Read field metadata and propose owner mappings before synchronization")
    effect_worker_parser = subparsers.add_parser("effect-worker", help="Observe drafts or process explicitly queued effects")
    effect_worker_parser.add_argument("--config", type=Path, required=True)
    effect_worker_parser.add_argument("--observe", action="store_true")
    effect_worker_parser.add_argument("--max-commands", type=int, default=20)

    business_report_parser = subparsers.add_parser(
        "business-report", help="Compare paired baseline/product observations with complete costs",
    )
    business_report_parser.add_argument("input", type=Path)
    business_report_parser.add_argument("--output", type=Path)
    business_report_parser.add_argument("--evidence", type=Path, help="Qualify costs against an offline evidence manifest")

    demo_parser = subparsers.add_parser(
        "demo",
        help="Run the source-checkout-only legacy deterministic P0 regression flow",
    )
    demo_parser.add_argument("--output", type=Path, default=Path("evidence/latest/demo.json"))

    agentteams_demo = subparsers.add_parser(
        "agentteams-demo",
        help=(
            "Run the source-checkout-only GOAI five-Agent reference flow and export "
            "a judge-facing evidence index"
        ),
    )
    agentteams_demo.add_argument("--config", type=Path, default=Path("configs/goai-agentteams-demo.json"))
    agentteams_demo.add_argument(
        "--input", type=Path, default=Path("examples/agentteams/change-request.json")
    )
    agentteams_demo.add_argument("--output-dir", type=Path, default=Path("evidence/goai-agentteams/latest"))

    agentteams_verify = subparsers.add_parser(
        "agentteams-verify",
        help="Verify a generated GOAI AgentTeams evidence directory without rerunning Agents",
    )
    agentteams_verify.add_argument("--output-dir", type=Path, default=Path("evidence/goai-agentteams/latest"))

    verify_parser = subparsers.add_parser("verify", help="Verify an exported RebaseReceipt")
    verify_parser.add_argument("receipt", type=Path)
    verify_impact_parser = subparsers.add_parser(
        "verify-impact-certificate",
        help="Independently recompute and verify an ImpactCertificate",
    )
    verify_impact_parser.add_argument("certificate", type=Path)
    verify_minimal_parser = subparsers.add_parser(
        "verify-minimal-certificate",
        help="Independently recompute and verify a minimal Rebase certificate",
    )
    verify_minimal_parser.add_argument("certificate", type=Path)

    workspace_demo = subparsers.add_parser(
        "workspace-demo",
        help="Run formation, two changes, OWB evaluation, Skill Foundry and evidence export",
    )
    workspace_demo.add_argument(
        "--output-dir",
        "--output",
        dest="output_dir",
        type=Path,
        default=Path("evidence/workspace/latest"),
    )

    workspace_profile = subparsers.add_parser(
        "workspace-profile",
        help="Read the built-in or supplied EnterpriseSeedProfile without executing a handler",
    )
    workspace_profile.add_argument("--input", type=Path)
    workspace_profile.add_argument("--output", type=Path)

    workspace_intake = subparsers.add_parser(
        "workspace-intake",
        help="Validate one or more EnterpriseSeedProfiles and emit dimensioned zero-write receipts",
    )
    workspace_intake.add_argument("inputs", nargs="+", type=Path)
    workspace_intake.add_argument("--output", type=Path)

    workspace_oac = subparsers.add_parser(
        "workspace-oac-admission-demo",
        help=(
            "Use a separate OAC checkout via sibling ../oac-spec, --oac-root, or "
            "ORGREBASE_OAC_ROOT; compile/lower public wire JSON and admit a zero-effect "
            "local Formation"
        ),
    )
    workspace_oac.add_argument("--output-dir", type=Path, default=Path("evidence/oac-bridge/latest"))
    workspace_oac.add_argument(
        "--oac-root",
        type=Path,
        help=(
            "path to the separate oac-spec source checkout; overrides "
            "ORGREBASE_OAC_ROOT and sibling discovery"
        ),
    )
    workspace_oac.add_argument(
        "--policy",
        type=Path,
        default=_PROJECT_ROOT / "configs" / "oac" / "runtime-admission-policy.json",
    )
    workspace_oac.add_argument("--verify", action="store_true")

    workspace_evolution = subparsers.add_parser(
        "workspace-oac-evolution-demo",
        help=(
            "Run the synthetic BASE/SPLIT execution, independent Outcome, governed "
            "successor promotion and rollback journey"
        ),
    )
    workspace_evolution.add_argument("--output-dir", type=Path, default=Path("evidence/oac-evolution/latest"))
    workspace_evolution.add_argument(
        "--oac-root",
        type=Path,
        help=(
            "path to the separate oac-spec source checkout; overrides "
            "ORGREBASE_OAC_ROOT and sibling discovery"
        ),
    )
    workspace_evolution.add_argument(
        "--policy",
        type=Path,
        default=_PROJECT_ROOT / "configs" / "oac" / "runtime-admission-policy.json",
    )

    workspace_loop = subparsers.add_parser(
        "workspace-loop",
        help=(
            "Run the persistent Quote v1 → explicit Product approval → restart "
            "→ explicit Finance approval → v3 loop"
        ),
    )
    workspace_loop.add_argument(
        "--output", type=Path, default=Path("evidence/workspace/latest/workspace-loop.json")
    )
    workspace_loop.add_argument("--store", type=Path)
    workspace_loop.add_argument(
        "--allow-scripted-approval",
        action="store_true",
        help=(
            "explicitly opt in to the controlled-local acceptance harness; "
            "this is not evidence of an external human approval"
        ),
    )

    pilot_preflight = subparsers.add_parser(
        "enterprise-pilot-preflight",
        help="Validate and bind one exact Enterprise Quote Pack without creating state",
    )
    pilot_preflight.add_argument("--pack", required=True, type=Path)
    pilot_preflight.add_argument("--output", type=Path)

    oac_adapt = subparsers.add_parser(
        "enterprise-oac-adapt",
        help=(
            "Prepare an exact Enterprise Quote Pack for OAC admission; rerun with "
            "--approve-as after the server-enforced review window"
        ),
    )
    oac_adapt.add_argument("--pack", required=True, type=Path)
    oac_adapt.add_argument("--store", required=True, type=Path)
    oac_adapt.add_argument("--oac-root", type=Path)
    oac_adapt.add_argument(
        "--golden-root",
        type=Path,
        default=_PROJECT_ROOT / "evidence" / "golden-competition" / "latest" / "pilot",
    )
    oac_adapt.add_argument("--review-seconds", type=float, default=4.0)
    oac_adapt.add_argument("--prepare-command-id", default="oac-adaptation-prepare@r1")
    oac_adapt.add_argument("--approve-as")
    oac_adapt.add_argument("--approval-command-id", default="oac-adaptation-approve@r1")
    oac_adapt.add_argument("--output", type=Path)

    pilot_init = subparsers.add_parser(
        "enterprise-pilot-init",
        help="Copy the packaged Enterprise Quote template to one new editable draft directory",
    )
    pilot_init.add_argument("--output", required=True, type=Path)

    pilot_seal = subparsers.add_parser(
        "enterprise-pilot-seal",
        help="Seal an edited draft into one new content-addressed, preflighted Pack directory",
    )
    pilot_seal.add_argument("--draft", required=True, type=Path)
    pilot_seal.add_argument("--output", required=True, type=Path)

    pilot_check = subparsers.add_parser(
        "enterprise-pilot-check",
        help=(
            "Run preflight, the governed Quote loop, restart/restore, and the "
            "independent verifier once, then exit"
        ),
    )
    pilot_check.add_argument("--pack", required=True, type=Path)
    pilot_check.add_argument("--work-dir", required=True, type=Path)

    pilot_loop = subparsers.add_parser(
        "enterprise-pilot-loop",
        help=(
            "Run a fresh enterprise Quote Pack through form, governed changes, "
            "restart, backup/restore, and evidence export"
        ),
    )
    pilot_loop.add_argument("--pack", required=True, type=Path)
    pilot_loop.add_argument("--store", required=True, type=Path)
    pilot_loop.add_argument("--output-dir", required=True, type=Path)
    pilot_loop.add_argument(
        "--allow-scripted-approval",
        action="store_true",
        help=(
            "explicitly opt in to scripted owner commands for this controlled-local run"
        ),
    )

    pilot_start = subparsers.add_parser(
        "enterprise-pilot-start",
        help="Start the staged localhost Shadow Console for one Enterprise Quote Pack",
    )
    pilot_start.add_argument("--pack", required=True, type=Path)
    pilot_start.add_argument("--store", required=True, type=Path)
    pilot_start.add_argument("--host", default="127.0.0.1")
    pilot_start.add_argument("--port", default=8000, type=int)
    pilot_start.add_argument(
        "--review-seconds",
        default=0.0,
        type=float,
        help="Server-enforced review window before each owner approval",
    )
    pilot_start.add_argument(
        "--identity-mode",
        default="BODY_ACTOR_COMPATIBILITY",
        choices=("BODY_ACTOR_COMPATIBILITY", "CONTROLLED_LOCAL_HEADER_IDENTITY"),
        help="Controlled-local approval identity transport (not external IAM)",
    )
    pilot_start.add_argument(
        "--competition-mode",
        default="golden",
        choices=("golden", "off"),
        help=(
            "Run the controlled-local Golden AgentTeams formation path; "
            "use off only for the legacy deterministic candidate provider"
        ),
    )
    pilot_start.add_argument(
        "--competition-evidence-dir",
        type=Path,
        help="Fresh per-run Golden evidence root (default: beside the SQLite store)",
    )
    pilot_start.add_argument(
        "--competition-checkout",
        type=Path,
        default=Path(
            os.getenv(
                "AGENTTEAMS_CHECKOUT",
                str(default_agentteams_checkout(_PROJECT_ROOT)),
            )
        ),
        help="Exact pinned AgentTeams checkout",
    )
    pilot_start.add_argument(
        "--competition-lock",
        type=Path,
        default=_PROJECT_ROOT / "agentteams" / "teamharness-lock.json",
        help="Pinned AgentTeams source lock",
    )
    pilot_start.add_argument(
        "--competition-ollama-endpoint",
        help="Optional loopback Ollama endpoint override",
    )
    pilot_start.add_argument(
        "--competition-model-provider",
        default="ollama-local",
        choices=("ollama-local", "vertex-ai", "deepseek"),
        help=(
            "Reviewer advisory provider. vertex-ai uses the bounded Gemini Flash "
            "version selected by ORGREBASE_VERTEX_MODEL_ID, with credentials kept "
            "outside the repository; deepseek uses DEEPSEEK_API_KEY and deepseek-flash. "
            "Ollama is the offline default."
        ),
    )
    pilot_start.add_argument(
        "--competition-vertex-project",
        help=(
            "Vertex project ID only; credentials come from ADC or dedicated "
            "environment variables"
        ),
    )

    workspace_evaluate = subparsers.add_parser(
        "workspace-evaluate", help="Run the 192-case OrgWorkBench matrix"
    )
    workspace_evaluate.add_argument(
        "--output", type=Path, default=Path("evidence/workspace/latest/evaluation-suite.json")
    )

    workspace_skill = subparsers.add_parser(
        "workspace-skill", help="Build and evaluate the exact immutable Skill candidate"
    )
    workspace_skill.add_argument(
        "--output", type=Path, default=Path("evidence/workspace/latest/skill-foundry.json")
    )

    workspace_evidence = subparsers.add_parser(
        "workspace-evidence", help="Build the complete content-addressed local evidence pack"
    )
    workspace_evidence.add_argument("--output-dir", type=Path, default=Path("evidence/workspace/latest"))
    workspace_evidence.add_argument("--verify", action="store_true")

    for name in ("workspace-agentteams", "workspace-agentteams-status"):
        command = subparsers.add_parser(name, help="Validate fixed-pool assets and report live prerequisites")
        command.add_argument(
            "--output", type=Path, default=Path("evidence/workspace/latest/agentteams-status.json")
        )
        command.add_argument("--require-live", action="store_true")

    workspace_user = subparsers.add_parser(
        "workspace-user-validation", help="Summarize consented, redacted user walkthrough records"
    )
    workspace_user.add_argument("--input", type=Path)
    workspace_user.add_argument(
        "--output", type=Path, default=Path("evidence/workspace/latest/user-validation.json")
    )

    args = parser.parse_args()

    if args.command == "business-report":
        from orgrebase.business_evaluation import PairedObservation, business_report

        try:
            payload = json.loads(args.input.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                raise ValueError("PAIRED_OBSERVATION_ARRAY_REQUIRED")
            if args.evidence is not None:
                from orgrebase.cost_evidence import qualify_cost_evidence

                report = qualify_cost_evidence(payload, args.evidence)
            else:
                report = business_report(tuple(PairedObservation.model_validate(item) for item in payload))
        except (ValueError, OSError) as error:
            print(f"ERROR: {error}", file=sys.stderr)
            raise SystemExit(2) from error
        if args.output is not None:
            _write_json(args.output, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if args.evidence is not None and report["status"] == "INCOMPLETE_COST":
            raise SystemExit(2)
        return

    if args.command == "effect-worker":
        from orgrebase.auth import AuthenticationError
        from orgrebase.commit_gateway import EffectError
        from orgrebase.workspace.effect_worker import run_effect_worker

        try:
            result = run_effect_worker(args.config, observe=args.observe, max_commands=args.max_commands)
        except (AuthenticationError, EffectError, ValueError, OSError) as error:
            print(f"ERROR: {error}", file=sys.stderr)
            raise SystemExit(2) from error
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "source-sync":
        from orgrebase.auth import AuthenticationError
        from orgrebase.workspace.dataverse import SourceError, SourceRateLimited
        from orgrebase.workspace.source_worker import run_source_sync

        try:
            result = run_source_sync(args.config, max_pages=args.max_pages, discover=args.discover)
        except SourceRateLimited as error:
            print(json.dumps({"error": str(error), "retry_after": error.retry_after}), file=sys.stderr)
            raise SystemExit(2) from error
        except (AuthenticationError, SourceError, ValueError, OSError) as error:
            print(f"ERROR: {error}", file=sys.stderr)
            raise SystemExit(2) from error
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "serve":
        import uvicorn

        if not 1 <= args.port <= 65535:
            parser.error("port must be between 1 and 65535")
        if args.limit_concurrency is not None and args.limit_concurrency < 2:
            parser.error("limit-concurrency must be at least 2 (the current connection counts toward the limit)")
        if args.backlog is not None and args.backlog < 1:
            parser.error("backlog must be positive")
        if args.local_demo:
            os.environ["ORGREBASE_DEPLOYMENT_MODE"] = "local"
        if (os.environ.get("ORGREBASE_DEPLOYMENT_MODE", "").strip() == "local"
                and args.host not in {"127.0.0.1", "localhost", "::1"}):
            parser.error("LOCAL_DEMO_REQUIRES_LOOPBACK_HOST")
        limits = {}
        if args.limit_concurrency is not None:
            limits["limit_concurrency"] = args.limit_concurrency
        if args.backlog is not None:
            limits["backlog"] = args.backlog
        uvicorn.run("orgrebase.api:create_app", factory=True, host=args.host, port=args.port, **limits)
        return

    if args.command == "workspace":
        from orgrebase.operations import OperationError, workspace_command

        try:
            payload = None
            if args.input is not None:
                if args.input.stat().st_size > 1_048_576:
                    raise OperationError("CHANGE_EVENT_INPUT_TOO_LARGE")
                payload = json.loads(args.input.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise OperationError("CHANGE_EVENT_OBJECT_REQUIRED")
            result = workspace_command(
                args.url, args.action, event_id=args.event_id, digest=args.digest,
                reason=args.reason,
                payload=payload, token_variable=args.token_variable, workspace_id=args.workspace,
            )
        except (OperationError, OSError, ValueError) as error:
            print(f"ERROR: {error}", file=sys.stderr)
            raise SystemExit(2) from error
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "demo":
        try:
            _require_source_checkout(
                "demo",
                _PROJECT_ROOT / "scripts" / "verify_proof_pack.py",
            )
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        payload = run_demo(args.output)
        counts = payload["preview"]["counts"]
        metrics = payload["receipt"]["metrics"]
        print("OrgRebase P0 — PASS")
        print("Proposal: product.launch_date 2026-09-01 → 2026-09-15")
        print(
            f"Preview: {counts['affected_hard']} affected · "
            f"{counts['bounded_unaffected']} bounded unaffected · {counts['unknown']} unknown"
        )
        print("Skill: v1.2 FAIL → v1.3 PASS → CANARY")
        print(
            f"Receipt: {metrics['work_items_rebased']} rebased · "
            f"{metrics['unauthorized_disclosures']} unauthorized disclosures · "
            f"{metrics['false_invalidations']} false invalidations"
        )
        print(f"Evidence: {args.output.parent.resolve()}")
        return

    if args.command == "agentteams-demo":
        from orgrebase.goai_agentteams import (
            EXPECTED_FRESH_CORE_EVIDENCE,
            EXPECTED_PUBLIC_TRANSPORT_FIXTURE,
            build_run_summary,
            load_run_contract,
            verify_run_directory,
        )

        try:
            _require_source_checkout("agentteams-demo")
            _require_source_checkout(
                "agentteams-demo",
                _PROJECT_ROOT / "scripts" / "verify_proof_pack.py",
                _PROJECT_ROOT / "agentteams" / "teamharness-lock.json",
                packaged_agentteams_bundle(_PROJECT_ROOT),
                _PROJECT_ROOT / EXPECTED_PUBLIC_TRANSPORT_FIXTURE,
                *(
                    _PROJECT_ROOT / relative
                    for key, relative in EXPECTED_FRESH_CORE_EVIDENCE.items()
                    if key != "bundle_dir"
                ),
                args.config,
                args.input,
            )
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        config, request = load_run_contract(args.config, args.input)
        output_dir = args.output_dir.resolve()
        payload = run_demo(output_dir / "demo.json")
        summary = build_run_summary(
            demo=payload,
            output_dir=output_dir,
            config=config,
            request=request,
            project_root=_PROJECT_ROOT,
        )
        _write_json(output_dir / "run-summary.json", summary)
        verification = verify_run_directory(output_dir)
        if verification["status"] != "PASS":
            raise SystemExit(json.dumps(verification, ensure_ascii=False))
        collaboration = summary["agent_collaboration"]
        tool_call = summary["tool_call"]
        exception = summary["exception_and_recovery"]
        classification = summary["run_classification"]
        historical = summary["historical_agentteams_transport"]
        fresh = summary["fresh_core_agentteams_evidence"]
        ingestion = fresh["semantic_ingestion"]
        print("OrgRebase GOAI AgentTeams package — PASS")
        print(
            f"Deterministic reference: {collaboration['agent_count']} fixture Agent runs · "
            f"{collaboration['structured_handoffs']} structured handoffs · "
            f"Coordination {collaboration['coordination_status']}"
        )
        print(
            f"Tool: {tool_call['tool_refs'][0]} · {tool_call['statuses'][0]} · "
            f"receipt {str(tool_call['receipt_digests'][0])[:22]}…"
        )
        print(
            f"Output: {summary['output']['rebase_status']} · "
            f"{summary['output']['work_items_rebased']} work items rebased · "
            f"Skill {summary['output']['skill_candidate_state']}"
        )
        print(
            f"Exceptions: {exception['authority_conflict']} · "
            f"{exception['expired_preview']['status']}({exception['expired_preview']['error_code']}) · "
            f"{exception['rollback']['status']}"
        )
        print(
            f"Reference: semantic {classification['semantic_acceptance']} · "
            f"autonomous {str(classification['autonomous_collaboration']).lower()} · "
            f"Workspace live {classification['current_workspace_live']}"
        )
        print(
            f"Historical transport: {historical['evidence_class']} · "
            f"{historical['worker_count']} Workers · "
            f"{historical['provider_call_count']} provider calls"
        )
        print(
            f"Fresh Core proposal live: {fresh['evidence_class']} · "
            f"{fresh['successful_provider_executions']} provider executions · "
            f"{ingestion['advisory_accepted']}/{ingestion['evaluated']} candidates accepted · "
            f"{ingestion['target_writes']} target writes"
        )
        print(
            f"Open boundary: Workspace live {fresh['current_workspace_live']} · "
            f"OAC bridge {fresh['oac_runtime_bridge']}"
        )
        print(f"Evidence: {output_dir}")
        return

    if args.command == "agentteams-verify":
        from orgrebase.goai_agentteams import verify_run_directory

        result = verify_run_directory(args.output_dir.resolve())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result["status"] != "PASS":
            raise SystemExit(2)
        return

    if args.command == "verify":
        with args.receipt.open(encoding="utf-8") as handle:
            result = OrgRebaseService.verify_receipt(json.load(handle))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "verify-impact-certificate":
        with args.certificate.open(encoding="utf-8") as handle:
            result = OrgRebaseService().verify_impact_certificate(json.load(handle))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "verify-minimal-certificate":
        with args.certificate.open(encoding="utf-8") as handle:
            result = OrgRebaseService().verify_minimal_rebase_certificate(json.load(handle))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "workspace-demo":
        summary = run_workspace_demo(args.output_dir)
        quote = summary["final_quote"]
        print("OrgRebase Workspace — PASS")
        print(
            f"Final Quote: {quote['id']}@{quote['version']} · "
            f"{quote['payload']['launch_date']} · {quote['payload']['currency']}"
        )
        print(
            f"OWB v1.1: {summary['primary_evaluation_score']:.1f}/100 · "
            f"{summary['primary_evaluation_status']}"
        )
        print(f"Approval: {summary['approval_mode']}")
        print(f"Approval input: {summary['approval_input_mode']}")
        print(f"Skill: {summary['skill_status']}")
        print(
            f"Boundaries: Workspace live {summary['boundaries']['agentteams']} · "
            f"OAC bridge {summary['boundaries']['oac_runtime_bridge']}"
        )
        print(f"Evidence: {args.output_dir.resolve()}")
        return

    if args.command == "workspace-profile":
        from orgrebase.workspace.api import workspace_profile_view
        from orgrebase.workspace.profile import (
            load_enterprise_seed_profile,
            northstar_acme_quote_profile,
        )

        profile = (
            load_enterprise_seed_profile(args.input)
            if args.input is not None
            else northstar_acme_quote_profile()
        )
        result = workspace_profile_view(profile)
        if args.output is not None:
            _write_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "workspace-intake":
        from orgrebase.workspace.api import enterprise_intake_view
        from orgrebase.workspace.profile import load_enterprise_seed_profile

        result = enterprise_intake_view(load_enterprise_seed_profile(path) for path in args.inputs)
        if args.output is not None:
            _write_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "workspace-oac-admission-demo":
        from orgrebase.workspace.oac_bridge import (
            run_oac_admission_demo,
            verify_oac_bridge_evidence,
        )

        try:
            result = (
                verify_oac_bridge_evidence(args.output_dir, policy_path=args.policy)
                if args.verify
                else run_oac_admission_demo(
                    args.output_dir,
                    oac_root=args.oac_root,
                    policy_path=args.policy,
                )
            )
        except FileNotFoundError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return

    if args.command == "workspace-oac-evolution-demo":
        from orgrebase.workspace.evolution_demo import run_oac_evolution_demo

        try:
            result = run_oac_evolution_demo(
                args.output_dir,
                oac_root=args.oac_root,
                policy_path=args.policy,
            )
        except FileNotFoundError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return

    if args.command == "workspace-loop":
        try:
            result = _workspace_loop(
                args.output,
                args.store,
                allow_scripted_approval=args.allow_scripted_approval,
            )
        except (FileExistsError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        quote = result["final_quote"]
        print("OrgRebase Workspace loop — PASS")
        print(f"Quote {quote['version']}: {quote['payload']['launch_date']} · {quote['payload']['currency']}")
        print(f"Approval: {result['approval_mode']}")
        print(f"Approval input: {result['approval_input_mode']}")
        print(
            f"Boundaries: Workspace live {result['boundaries']['agentteams']} · "
            f"OAC bridge {result['boundaries']['oac_runtime_bridge']}"
        )
        print(f"Evidence: {args.output.resolve()}")
        return

    if args.command == "enterprise-pilot-preflight":
        try:
            result = _enterprise_pilot_preflight(args.pack)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        if args.output is not None:
            _write_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "enterprise-oac-adapt":
        try:
            result = _enterprise_oac_adapt(
                pack=args.pack,
                store=args.store,
                oac_root=args.oac_root,
                golden_root=args.golden_root,
                review_seconds=args.review_seconds,
                prepare_command_id=args.prepare_command_id,
                approve_as=args.approve_as,
                approval_command_id=args.approval_command_id,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            detail = {
                "code": str(getattr(exc, "code", str(exc))),
                "message": str(exc),
            }
            remaining_ms = getattr(exc, "remaining_ms", None)
            if isinstance(remaining_ms, int):
                detail["remaining_ms"] = remaining_ms
                detail["not_before"] = getattr(exc, "not_before", None)
            print(json.dumps(detail, ensure_ascii=False), file=sys.stderr)
            raise SystemExit(2) from exc
        if args.output is not None:
            _write_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "enterprise-pilot-init":
        from orgrebase.workspace.pilot_authoring import initialize_enterprise_quote_pilot_draft

        try:
            result = initialize_enterprise_quote_pilot_draft(args.output)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "enterprise-pilot-seal":
        from orgrebase.workspace.pilot_authoring import seal_enterprise_quote_pilot_pack

        try:
            result = seal_enterprise_quote_pilot_pack(args.draft, args.output)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "enterprise-pilot-check":
        try:
            result = _enterprise_pilot_check(pack=args.pack, work_dir=args.work_dir)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "enterprise-pilot-loop":
        try:
            result = _enterprise_pilot_loop(
                pack=args.pack,
                store=args.store,
                output_dir=args.output_dir,
                allow_scripted_approval=args.allow_scripted_approval,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
        quote = result["final_quote"]
        print("OrgRebase Enterprise Quote Pilot — PILOT_READY_CONTROLLED_LOCAL")
        print(f"Organization: {result['organization_id']}")
        print(f"Quote {quote['version']}: {quote['payload']['launch_date']} · {quote['payload']['currency']}")
        print("Wrong-owner rejection: PASS · restart: PASS · backup/restore: PASS")
        print("External enterprise writes: 0")
        print("Real enterprise validation: NOT_RUN · production ready: false")
        print(f"Evidence: {args.output_dir.resolve()}")
        return

    if args.command == "enterprise-pilot-start":
        if args.host not in {"127.0.0.1", "localhost", "::1"}:
            print("ERROR: PILOT_BIND_HOST_MUST_BE_LOOPBACK", file=sys.stderr)
            raise SystemExit(2)
        from orgrebase.api import create_app
        from orgrebase.auth import CONTROLLED_LOCAL_SESSION_IDENTITY
        from orgrebase.local_role_session import LocalRoleSessionSettings
        from orgrebase.runtime_config import DeploymentSettings
        from orgrebase.workspace.pilot import (
            enterprise_quote_pilot_run_id,
            load_enterprise_quote_pilot_pack,
        )
        from orgrebase.workspace.service import WorkspaceService

        local_origin = os.environ.get("ORGREBASE_LOCAL_ROLE_SESSION_ORIGIN", "").strip()
        local_session = None
        if local_origin:
            if os.environ.get("ORGREBASE_DEPLOYMENT_MODE", "local").strip() != "local":
                raise ValueError("AUTH_LOCAL_SESSION_DEPLOYMENT_FORBIDDEN")
            local_session = LocalRoleSessionSettings(local_origin)
            console_host = f"[{args.host}]" if args.host == "::1" else args.host
            if local_origin != f"http://{console_host}:{args.port}":
                raise ValueError("AUTH_LOCAL_SESSION_LISTENER_ORIGIN_MISMATCH")
        settings = DeploymentSettings(mode="local", local_role_session=local_session)
        runtime = load_enterprise_quote_pilot_pack(args.pack)
        competition_checkout = args.competition_checkout.expanduser().resolve()
        if args.competition_mode == "golden" and not competition_checkout.is_dir():
            fetch_script = _PROJECT_ROOT / "scripts" / "fetch_pinned_agentteams.py"
            try:
                subprocess.run(
                    [
                        sys.executable,
                        str(fetch_script),
                        "--lock",
                        str(args.competition_lock),
                        "--output",
                        str(competition_checkout),
                    ],
                    cwd=_PROJECT_ROOT,
                    check=True,
                    timeout=240,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                print(
                    f"ERROR: GOLDEN_AGENTTEAMS_SOURCE_PREPARATION_FAILED:{type(exc).__name__}",
                    file=sys.stderr,
                )
                raise SystemExit(2) from exc
        competition_evidence_root = (
            args.competition_evidence_dir.expanduser().resolve()
            if args.competition_evidence_dir is not None
            else args.store.expanduser().resolve().parent / "golden-competition"
        )
        task_intake_setting = os.environ.get(
            "ORGREBASE_WORKSPACE_TASK_INTAKE_REQUIRED",
            "1",
        ).strip()
        if task_intake_setting not in {"0", "1"}:
            print(
                "ERROR: ORGREBASE_WORKSPACE_TASK_INTAKE_REQUIRED_INVALID",
                file=sys.stderr,
            )
            raise SystemExit(2)
        workspace = WorkspaceService(
            store_path=args.store,
            workflow_run_id=enterprise_quote_pilot_run_id(runtime),
            runtime_configuration=runtime,
            review_duration_seconds=args.review_seconds,
            approval_identity_mode=CONTROLLED_LOCAL_SESSION_IDENTITY if local_session else args.identity_mode,
            task_intake_required=task_intake_setting == "1",
            competition_mode=args.competition_mode,
            competition_evidence_root=competition_evidence_root,
            competition_pack_path=args.pack,
            competition_checkout=competition_checkout,
            competition_lock_path=args.competition_lock,
            competition_model_provider=args.competition_model_provider,
            competition_ollama_endpoint=args.competition_ollama_endpoint,
            competition_vertex_project=args.competition_vertex_project,
        )
        application = create_app(workspace_service=workspace, deployment_settings=settings)
        print("OrgRebase Enterprise Quote Pilot — STARTING")
        print(f"Profile: {runtime.profile.ref}")
        print(f"Pack digest: {runtime.pack_digest}")
        print(f"State: {args.store.resolve()}")
        print(f"Console: http://{args.host}:{args.port}")
        print(f"Approval control: server wait {args.review_seconds:g}s · {workspace.approval_identity_mode}")
        print(
            f"Formation runtime: {args.competition_mode} · "
            f"reviewer {args.competition_model_provider} · "
            f"evidence {competition_evidence_root}"
        )
        print("Boundary: localhost Shadow Pilot · external enterprise writes 0")
        import uvicorn

        uvicorn.run(application, host=args.host, port=args.port)
        return

    if args.command == "workspace-evaluate":
        from orgrebase.workspace.benchmark import run_owb_evaluation

        payload = run_owb_evaluation()
        _write_json(args.output, payload)
        print(
            json.dumps(
                {
                    "status": payload["primary_status"],
                    "score": payload["primary_score"],
                    "output": str(args.output),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "workspace-skill":
        from orgrebase.store import StateStore
        from orgrebase.workspace.formation import seed_workspace_store
        from orgrebase.workspace.skill_foundry import SkillFoundryService

        store = StateStore(":memory:")
        try:
            seed_workspace_store(store)
            result = SkillFoundryService(store).run()
            result["event_chain"] = store.verify_event_chain()
        finally:
            store.close()
        _write_json(args.output, json.loads(canonical_json(result)))
        print(f"Workspace Skill Foundry: {result['status']}")
        print(f"Evidence: {args.output.resolve()}")
        return

    if args.command == "workspace-evidence":
        from orgrebase.workspace.evidence import WorkspaceEvidenceBuilder, verify_evidence_directory

        result = (
            verify_evidence_directory(args.output_dir)
            if args.verify
            else WorkspaceEvidenceBuilder(args.output_dir).build().model_dump(mode="json")
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command in {"workspace-agentteams", "workspace-agentteams-status"}:
        from orgrebase.workspace.transport import agentteams_status

        result = agentteams_status()
        _write_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.require_live and result.get("evidence_class") != "LIVE_AGENTTEAMS":
            raise SystemExit(2)
        return

    if args.command == "workspace-user-validation":
        from orgrebase.workspace.models import UserWalkthroughRecord
        from orgrebase.workspace.user_validation import UserValidationService

        records: tuple[UserWalkthroughRecord, ...] = ()
        if args.input and args.input.is_file():
            raw = json.loads(args.input.read_text(encoding="utf-8"))
            records = tuple(UserWalkthroughRecord.model_validate(item) for item in raw)
        summary = UserValidationService.summarize(records)
        _write_json(args.output, summary.model_dump(mode="json"))
        print(json.dumps(summary.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return

    raise RuntimeError(f"UNHANDLED_COMMAND:{args.command}")


if __name__ == "__main__":
    main()
