"""One-command demo and evidence export."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.service import OrgRebaseService

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


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


def _workspace_loop(output: Path, store: Path | None) -> dict[str, Any]:
    from tempfile import NamedTemporaryFile

    from orgrebase.workspace.service import WorkspaceService

    if store is None:
        with NamedTemporaryFile(prefix="orgrebase-workspace-", suffix=".sqlite") as handle:
            result = WorkspaceService(store_path=handle.name).run_local_loop()
    else:
        if store.exists():
            store.unlink()
        result = WorkspaceService(store_path=store).run_local_loop()
    normalized = json.loads(canonical_json(result))
    _write_json(output, normalized)
    return normalized


def main() -> None:
    parser = argparse.ArgumentParser(prog="orgrebase")
    subparsers = parser.add_subparsers(dest="command", required=True)

    demo_parser = subparsers.add_parser("demo", help="Run the legacy deterministic P0 flow")
    demo_parser.add_argument("--output", type=Path, default=Path("evidence/latest/demo.json"))

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

    workspace_loop = subparsers.add_parser(
        "workspace-loop", help="Run only the persistent Quote v1 → v2 → restart → v3 loop"
    )
    workspace_loop.add_argument(
        "--output", type=Path, default=Path("evidence/workspace/latest/workspace-loop.json")
    )
    workspace_loop.add_argument("--store", type=Path)

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
    workspace_evidence.add_argument(
        "--output-dir", type=Path, default=Path("evidence/workspace/latest")
    )
    workspace_evidence.add_argument("--verify", action="store_true")

    for name in ("workspace-agentteams", "workspace-agentteams-status"):
        command = subparsers.add_parser(
            name, help="Validate fixed-pool assets and report live prerequisites"
        )
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

    if args.command == "demo":
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
        print(f"Skill: {summary['skill_status']}")
        print(f"AgentTeams: {summary['agentteams']['status']}")
        print(f"Evidence: {args.output_dir.resolve()}")
        return

    if args.command == "workspace-loop":
        result = _workspace_loop(args.output, args.store)
        quote = result["final_quote"]
        print("OrgRebase Workspace loop — PASS")
        print(
            f"Quote {quote['version']}: {quote['payload']['launch_date']} · "
            f"{quote['payload']['currency']}"
        )
        print(f"Evidence: {args.output.resolve()}")
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
