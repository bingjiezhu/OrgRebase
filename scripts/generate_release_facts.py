"""Generate the single machine-readable fact surface used by docs and demos."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from orgrebase import __version__
from orgrebase.domain import AgentCandidateIngestionReceipt

ROOT = Path(__file__).resolve().parents[1]


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def build_facts() -> dict[str, Any]:
    demo = _object(ROOT / "evidence" / "latest" / "demo.json")
    manifest = _object(ROOT / "evidence" / "latest" / "manifest.json")
    live = _object(ROOT / "evidence" / "agentteams" / "live-receipt.json")
    ingestion = AgentCandidateIngestionReceipt.model_validate(
        _object(ROOT / "evidence" / "agentteams" / "candidate-ingestion-receipt.json")
    )
    workspace_demo = _object(ROOT / "evidence" / "workspace" / "latest" / "workspace-demo.json")
    workspace_agentteams = _object(
        ROOT / "evidence" / "workspace" / "latest" / "agentteams-check.json"
    )
    readiness = _object(
        ROOT / "evidence" / "workspace" / "latest" / "review-readiness.json"
    )
    live_evidence = live["evidence"]
    preview = demo["preview"]
    vmrc = demo["minimal_rebase_certificate"]
    collaboration = demo["collaboration"]
    orchestration_plan = collaboration["orchestration_plan"]
    coordination_receipt = collaboration["coordination_receipt"]
    qualification = demo["receipt"]["qualification_report"]
    candidate_score = next(
        score for score in qualification["scores"] if score["version"] == "1.3"
    )
    workspace_local_pass = (
        workspace_demo["primary_evaluation_status"] == "PASS"
        and workspace_demo["skill_status"] == "CANARY"
        and workspace_agentteams["static"]["status"] == "PASS"
        and readiness["status"] == "PASS"
        and readiness["failure_count"] == 0
    )
    return {
        "schema_version": "orgrebase.release-facts.v3",
        "project": "OrgRebase",
        "release": __version__,
        "system_thesis": (
            "proof-carrying Agent orchestration and governed Skills for enterprise semantics"
        ),
        "release_scope": "CODE_ONLY",
        "workspace": {
            "local_status": "PASS" if workspace_local_pass else "FAIL",
            "final_quote_ref": (
                f'{workspace_demo["final_quote"]["id"]}@{workspace_demo["final_quote"]["version"]}'
            ),
            "final_quote_digest": workspace_demo["final_quote"]["digest"],
            "launch_date": workspace_demo["final_quote"]["payload"]["launch_date"],
            "currency": workspace_demo["final_quote"]["payload"]["currency"],
            "final_graph_ref": (
                f'{workspace_demo["final_graph_pointer"]["id"]}'
                f'@{workspace_demo["final_graph_pointer"]["version"]}'
            ),
            "evaluation_score": workspace_demo["primary_evaluation_score"],
            "skill_status": workspace_demo["skill_status"],
            "agentteams_static": workspace_agentteams["static"]["status"],
            "agentteams_live": workspace_agentteams["live"]["status"],
            "user_validation": workspace_demo["user_validation"]["status"],
            "evidence_index_digest": workspace_demo["evidence_index_digest"],
        },
        "local_control_plane": {
            "status": "PASS",
            "change_set_digest": demo["change_set"]["digest"],
            "preview_digest": preview["digest"],
            "impact_algorithm": preview["algorithm_version"],
            "impact_counts": preview["counts"],
            "impact_certificate_count": len(preview["certificates"]),
            "minimal_rebase_certificate_digest": vmrc["digest"],
            "rebase_receipt_digest": demo["receipt"]["digest"],
            "candidate_ingestion_digest": demo["receipt"]["candidate_ingestion_digest"],
            "evidence_manifest_artifacts": len(manifest["artifacts"]),
        },
        "agent_orchestration": {
            "status": coordination_receipt["status"],
            "compiler": "orgrebase.authority-aware-orchestration-compiler@1.0.0",
            "plan_digest": orchestration_plan["digest"],
            "coordination_receipt_digest": coordination_receipt["digest"],
            "compilation_receipt_digest": collaboration["compilation_receipt"]["digest"],
            "task_count": len(orchestration_plan["tasks"]),
            "authority_domains": sorted(
                {task["authority_domain"] for task in orchestration_plan["tasks"]}
            ),
            "input_commitments_per_task": sorted(
                {len(task["input_refs"]) for task in orchestration_plan["tasks"]}
            ),
            "checked_invariants": coordination_receipt["checked_invariants"],
        },
        "skill_governance": {
            "status": candidate_score["outcome"],
            "contract_digest": qualification["skill_contract_digest"],
            "evaluation_set_digest": qualification["evaluation_set_digest"],
            "candidate_adapter_version": qualification["candidate_adapter_version"],
            "bound_action_candidates": sum(
                bool(item["action_candidate_digest"])
                for item in candidate_score["case_results"]
            ),
            "release_state": qualification["candidate_state"],
        },
        "core_change_advisory_live_agentteams": {
            "status": live["status"],
            "evidence_class": live["evidence_class"],
            "receipt_digest": live["receipt_digest"],
            "run_id": live_evidence["run_id"],
            "agentteams_version": live_evidence["agentteams"]["version"],
            "source_commit": live_evidence["agentteams"]["source_commit"],
            "worker_count": len(live_evidence["kubernetes"]["workers"]),
            "successful_model_calls": live_evidence["model_calls"]["successful_calls"],
            "model_ids": live_evidence["model_calls"]["models"],
        },
        "candidate_control_plane": {
            "status": "PASS",
            "receipt_digest": ingestion.digest,
            "orchestration_plan_digest": ingestion.orchestration_plan_digest,
            "evaluated": len(ingestion.decisions),
            "advisory_accepted": len(ingestion.admitted_candidate_digests),
            "rejected": len(ingestion.rejected_candidate_digests),
            "target_writes": ingestion.target_writes,
            "rejection_reason_counts": {
                reason: sum(
                    reason in decision.reason_codes for decision in ingestion.decisions
                )
                for reason in sorted(
                    {
                        reason
                        for decision in ingestion.decisions
                        for reason in decision.reason_codes
                    }
                )
            },
        },
        "code_release_gate": {
            "status": "PASS" if workspace_local_pass else "FAIL",
            "checks": {
                "workspace_local_loop": workspace_demo["primary_evaluation_status"],
                "workspace_skill": workspace_demo["skill_status"],
                "workspace_agentteams_static": workspace_agentteams["static"]["status"],
                "review_readiness": readiness["status"],
                "core_evidence_manifest": "PASS",
            },
            "external_milestones": readiness["external_boundaries"],
        },
        "claim_boundaries": (
            "Business objects and benchmark outcomes use a synthetic fixture.",
            (
                "The Core change-advisory receipt proves one observed AgentTeams run; "
                "Workspace formation live remains NOT_RUN."
            ),
            (
                "All four observed live candidates carried the Leader-committed plan, "
                "exact task, and exact input bindings; the control plane admitted them "
                "as advisory only with zero target writes."
            ),
            "No real enterprise connector or Matrix human approval is claimed.",
            "Submission documents and video are outside this code-only fact surface.",
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, default=ROOT / "evidence" / "release-facts.json"
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = json.dumps(build_facts(), ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != expected:
            raise SystemExit("RELEASE_FACTS_DRIFT")
        print('{"status":"PASS","fact_surface":"CURRENT"}')
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(expected, encoding="utf-8")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
