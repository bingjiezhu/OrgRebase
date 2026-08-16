"""Machine-verifiable GOAI stage gates; evidence labels are never inferred."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from orgrebase.agentteams_ingest import ingest_local_proposals
from orgrebase.collaboration import OrchestrationCompiler
from orgrebase.digest import sha256_digest
from orgrebase.domain import (
    AgentCandidateIngestionReceipt,
    AgentRun,
    ChangeSetRevision,
    CompilationReceipt,
    CoordinationReceipt,
    ImpactPreview,
    OrchestrationPlan,
    QualificationReport,
    RunEnvelope,
    StructuredHandoff,
)
from orgrebase.fixture import load_fixture
from orgrebase.service import OrgRebaseService
from orgrebase.skills import load_skill_contract

ROOT = Path(__file__).resolve().parents[1]
AGENTTEAMS_SOURCE_COMMIT = "849182af8e017168a5a200a87b1062142caf462d"
COMMIT = re.compile(r"^[0-9a-f]{40}$")


def _json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _load_manifest_verifier() -> ModuleType:
    path = Path(__file__).with_name("verify_evidence_manifest.py")
    spec = importlib.util.spec_from_file_location("orgrebase_manifest_verifier", path)
    if spec is None or spec.loader is None:  # pragma: no cover - packaging invariant
        raise RuntimeError("manifest verifier is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifest_valid(path: Path) -> tuple[bool, dict[str, Any] | None]:
    try:
        _load_manifest_verifier().verify(path)
    except (RuntimeError, OSError, ValueError):
        return False, None
    return True, _json(path)


def _receipt_valid(receipt: dict[str, Any] | None) -> bool:
    if not receipt or receipt.get("status") != "COMPLETED":
        return False
    try:
        OrgRebaseService.verify_receipt(receipt)
    except RuntimeError:
        return False
    return True


def _rollback_valid(payload: dict[str, Any] | None) -> bool:
    if not payload:
        return False
    plan = payload.get("plan", {})
    approval = payload.get("approval", {})
    receipt = payload.get("receipt", {})
    metrics = receipt.get("metrics", {})
    try:
        OrgRebaseService.verify_rollback_receipt(receipt)
    except RuntimeError:
        return False
    return bool(
        receipt.get("status") == "ROLLED_BACK_PENDING_REBASE"
        and receipt.get("authoritative_claim_unchanged") is True
        and metrics.get("authoritative_claims_changed") == 0
        and metrics.get("history_rows_deleted") == 0
        and metrics.get("work_items_pending_rebase", 0) >= 1
        and plan.get("target_status") == receipt.get("status")
        and approval.get("actor_id") != approval.get("apply_approver_id")
        and "rollback:downstream-compensation" in approval.get("authority_scope", [])
        and approval.get("rollback_plan_digest") == plan.get("digest")
        and all(
            item.get("status") == "SUCCEEDED"
            for item in receipt.get("compensation_results", [])
        )
        and all(
            item.get("to_state")
            in {"ROLLED_BACK_PENDING_REBASE", "REQUALIFICATION_REQUIRED"}
            for item in receipt.get("transitions", [])
        )
    )


def _git_valid(payload: dict[str, Any] | None, run_id: str, nonce: str) -> bool:
    if not payload:
        return False
    patch = payload.get("patch", {})
    compensation = payload.get("compensation", {})
    patch_receipt = patch.get("receipt", {})
    compensation_receipt = compensation.get("receipt", {})
    patch_result = patch.get("result", {})
    compensation_result = compensation.get("result", {})
    verification = payload.get("verification", {})
    saga = payload.get("compensation_saga", {})
    return bool(
        payload.get("evidence_boundary") == "LOCAL_REAL_TOOL"
        and verification.get("status") == "PASS"
        and verification.get("worktree_clean") is True
        and verification.get("git_fsck") == "PASS"
        and COMMIT.fullmatch(str(verification.get("patch_commit", "")))
        and COMMIT.fullmatch(str(verification.get("compensation_commit", "")))
        and patch_receipt.get("workflow_run_id") == run_id
        and patch_receipt.get("run_nonce") == nonce
        and compensation_receipt.get("workflow_run_id") == run_id
        and compensation_receipt.get("run_nonce") == nonce
        and patch_receipt.get("evidence_class") == "LOCAL_REAL_TOOL"
        and compensation_receipt.get("evidence_class") == "LOCAL_REAL_TOOL"
        and compensation_receipt.get("compensation_ref") == patch_receipt.get("digest")
        and compensation_result.get("compensates_commit") == patch_result.get("commit")
        and verification.get("postcondition_digest")
        == patch_receipt.get("before_state_digest")
        and saga.get("status") == "ROLLED_BACK_PENDING_REBASE"
        and saga.get("workflow_run_id") == run_id
        and saga.get("run_nonce") == nonce
        and saga.get("git_patch_receipt_digest") == patch_receipt.get("digest")
        and saga.get("external_compensation", {}).get("receipt_digest")
        == compensation_receipt.get("digest")
        and saga.get("residual_effects") == []
        and saga.get("next_action") is None
    )


def _attributes(items: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if not isinstance(items, list):
        return result
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("value"), dict):
            continue
        value = item["value"]
        encoded = next(iter(value.values()), None)
        result[str(item.get("key"))] = encoded
    return result


def _observability_valid(
    payload: dict[str, Any] | None,
    run_id: str,
    nonce: str,
    receipt_digest: str,
    demo: dict[str, Any] | None,
) -> bool:
    if not payload or not demo:
        return False
    conventions = payload.get("semantic_conventions", {})
    correlation = payload.get("correlation", {})
    privacy = payload.get("privacy", {})
    try:
        spans = payload["traces"]["resourceSpans"][0]["scopeSpans"][0]["spans"]
    except (KeyError, IndexError, TypeError):
        return False
    if not isinstance(spans, list):
        return False
    by_name = {span.get("name"): span for span in spans if isinstance(span, dict)}
    control = by_name.get("control.apply_rebase", {})
    control_id = control.get("spanId")
    agent_spans = [span for span in spans if str(span.get("name", "")).startswith("agent.run/")]
    tool_spans = [span for span in spans if str(span.get("name", "")).startswith("tool.call/")]
    every_bound = all(
        _attributes(span.get("attributes")).get("orgrebase.workflow.run_id") == run_id
        and _attributes(span.get("attributes")).get("orgrebase.run.nonce") == nonce
        for span in spans
    )
    try:
        plan = demo["collaboration"]["orchestration_plan"]
        tasks = plan["tasks"]
    except (KeyError, TypeError):
        return False
    task_by_id = {task["id"]: task for task in tasks}
    span_by_agent = {
        str(span.get("name", "")).removeprefix("agent.run/"): span
        for span in agent_spans
    }
    agent_by_task = {task["id"]: task["agent_name"] for task in tasks}
    orchestration_bound = True
    for task in tasks:
        span = span_by_agent.get(task["agent_name"], {})
        attributes = _attributes(span.get("attributes"))
        dependencies = task.get("depends_on", [])
        expected_parent = (
            span_by_agent[agent_by_task[dependencies[0]]].get("spanId")
            if dependencies
            else control_id
        )
        expected_links = {
            span_by_agent[agent_by_task[dependency]].get("spanId")
            for dependency in dependencies[1:]
        }
        actual_links = {
            link.get("spanId")
            for link in span.get("links", [])
            if _attributes(link.get("attributes")).get("orgrebase.link.kind")
            == "orchestration_predecessor"
        }
        orchestration_bound = orchestration_bound and bool(
            span.get("parentSpanId") == expected_parent
            and actual_links == expected_links
            and attributes.get("orgrebase.orchestration.plan.digest")
            == plan.get("digest")
            and attributes.get("orgrebase.orchestration.task.digest")
            == task.get("digest")
            and attributes.get("orgrebase.orchestration.predecessor.count")
            == str(len(dependencies))
        )
    standard_operations = all(
        _attributes(span.get("attributes")).get("gen_ai.operation.name")
        == "invoke_agent"
        for span in agent_spans
    ) and all(
        _attributes(span.get("attributes")).get("gen_ai.operation.name")
        == "execute_tool"
        for span in tool_spans
    )
    return bool(
        conventions.get("release") == "opentelemetry-semantic-conventions@v1.43.0"
        and conventions.get("source_commit") == "89aae43"
        and correlation.get("workflow_run_id") == run_id
        and correlation.get("run_nonce") == nonce
        and correlation.get("receipt_digest") == receipt_digest
        and privacy.get("content_capture") is False
        and privacy.get("prompt_capture") is False
        and privacy.get("output_capture") is False
        and len(agent_spans) == 5
        and len(tool_spans) >= 1
        and control_id
        and not control.get("parentSpanId")
        and set(span_by_agent) == {task["agent_name"] for task in task_by_id.values()}
        and orchestration_bound
        and standard_operations
        and every_bound
    )


def _live_valid(payload: dict[str, Any] | None) -> bool:
    if not payload:
        return False
    evidence = payload.get("evidence", {})
    agentteams = evidence.get("agentteams", {})
    kubernetes = evidence.get("kubernetes", {})
    matrix = evidence.get("matrix", {})
    artifacts = evidence.get("artifacts", {})
    models = evidence.get("model_calls", {})
    return bool(
        payload.get("schema_version") == "orgrebase.agentteams-live-evidence.v2"
        and payload.get("status") == "PASS"
        and payload.get("evidence_class") == "LIVE_AGENTTEAMS"
        and sha256_digest(evidence) == payload.get("receipt_digest")
        and isinstance(evidence.get("run_id"), str)
        and evidence["run_id"].startswith("run:orgrebase:live:")
        and re.fullmatch(r"[0-9a-f]{64}", str(evidence.get("nonce", "")))
        and agentteams.get("version") == "v1.2.2"
        and agentteams.get("source_commit") == AGENTTEAMS_SOURCE_COMMIT
        and len(kubernetes.get("workers", [])) == 5
        and len(kubernetes.get("pods", [])) >= 3
        and len(matrix.get("candidate_senders", [])) >= 3
        and len(set(matrix.get("candidate_senders", []))) >= 3
        and re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            str(matrix.get("orchestration_plan_digest", "")),
        )
        and len(matrix.get("candidate_task_bindings", [])) >= 3
        and artifacts.get("skill", {}).get("loaded_event_id")
        and len(artifacts.get("candidate_artifacts", [])) >= 3
        and models.get("successful_calls", 0) >= 3
        and len(set(models.get("successful_workers", []))) >= 3
        and len(set(models.get("provider_request_ids", []))) >= 3
        and len(models.get("candidate_bindings", [])) >= 3
        and evidence.get("source_bundle_digest")
    )


def _minimal_certificate_valid(
    payload: dict[str, Any] | None,
    receipt: dict[str, Any] | None,
) -> bool:
    if not payload or not receipt:
        return False
    service = OrgRebaseService()
    try:
        verification = service.verify_minimal_rebase_certificate(payload)
    except RuntimeError:
        return False
    finally:
        service.store.close()
    return bool(
        verification.get("status") == "PASS"
        and receipt.get("minimal_rebase_certificate_digest") == payload.get("digest")
    )


def _candidate_ingestion_valid(
    payload: dict[str, Any] | None,
    live: dict[str, Any] | None,
    demo: dict[str, Any] | None,
) -> bool:
    if not payload or not live or not demo:
        return False
    try:
        receipt = AgentCandidateIngestionReceipt.model_validate(payload)
    except ValueError:
        return False
    live_digests = {
        item.get("digest")
        for item in live.get("evidence", {})
        .get("artifacts", {})
        .get("candidate_artifacts", [])
        if item.get("ref") != "result:summary"
    }
    decision_digests = {item.artifact_digest for item in receipt.decisions}
    return bool(
        receipt.live_receipt_digest == live.get("receipt_digest")
        and receipt.change_set_digest == demo.get("change_set", {}).get("digest")
        and receipt.preview_digest == demo.get("preview", {}).get("digest")
        and receipt.orchestration_plan_digest
        == demo.get("collaboration", {}).get("orchestration_plan", {}).get("digest")
        and live.get("evidence", {}).get("matrix", {}).get(
            "orchestration_plan_digest"
        )
        == receipt.orchestration_plan_digest
        and decision_digests == live_digests
        and receipt.target_writes == 0
        and all(not item.admitted_effects for item in receipt.decisions)
    )


def _orchestration_valid(demo: dict[str, Any] | None) -> bool:
    if not demo:
        return False
    try:
        collaboration = demo["collaboration"]
        plan = OrchestrationPlan.model_validate(collaboration["orchestration_plan"])
        handoffs = tuple(
            StructuredHandoff.model_validate(item) for item in collaboration["handoffs"]
        )
        runs = tuple(AgentRun.model_validate(item) for item in collaboration["agent_runs"])
        envelope = RunEnvelope.model_validate(collaboration["run_envelope"])
        stored = CoordinationReceipt.model_validate(collaboration["coordination_receipt"])
        compiler = OrchestrationCompiler(load_fixture())
        verified = compiler.verify_execution(
            plan=plan,
            handoffs=handoffs,
            runs=runs,
            run_envelope=envelope,
        )
        ingestion = AgentCandidateIngestionReceipt.model_validate(
            collaboration["candidate_ingestion"]
        )
        change_set = ChangeSetRevision.model_validate(demo["change_set"])
        preview = ImpactPreview.model_validate(demo["preview"])
        recomputed = ingest_local_proposals(
            change_set=change_set,
            preview=preview,
            plan=plan,
            handoffs=handoffs,
            coordination_receipt=stored,
            run_envelope=envelope,
        )
        compilation = CompilationReceipt.model_validate(
            collaboration["compilation_receipt"]
        )
        recomputed_compilation = compiler.compilation_receipt(plan, change_set, preview)
    except (KeyError, TypeError, ValueError, RuntimeError):
        return False
    return bool(
        verified.digest == stored.digest
        and stored.status == "PASS"
        and len(plan.tasks) == 5
        and len(handoffs) == 5
        and len(runs) == 5
        and ingestion.digest == recomputed.digest
        and ingestion.target_writes == 0
        and not ingestion.rejected_candidate_digests
        and all(not item.admitted_effects for item in ingestion.decisions)
        and demo.get("receipt", {}).get("candidate_ingestion_digest") == ingestion.digest
        and compilation.digest == recomputed_compilation.digest
        and compilation.orchestration_plan_digest == plan.digest
        and demo.get("receipt", {}).get("compilation_receipt_digest") == compilation.digest
    )


def _proof_pack_valid(path: Path) -> bool:
    if not path.is_file():
        return False
    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("verify_proof_pack.py")),
            "--root",
            str(ROOT),
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0


def _skill_governance_valid(demo: dict[str, Any] | None) -> bool:
    if not demo:
        return False
    try:
        report = QualificationReport.model_validate(
            demo["receipt"]["qualification_report"]
        )
        contract = load_skill_contract()
        fixture = load_fixture()
        candidate = next(score for score in report.scores if score.version == "1.3")
        baselines = tuple(score for score in report.scores if score.version != "1.3")
    except (KeyError, StopIteration, TypeError, ValueError, RuntimeError):
        return False
    return bool(
        report.skill_contract_digest == contract.get("content_digest")
        and report.evaluation_set_digest == sha256_digest(fixture.evaluation_cases)
        and report.candidate_adapter_version == "enterprise-launch-readiness@1.3"
        and report.candidate_state.value == "CANARY"
        and candidate.outcome == "PASS"
        and candidate.passed == candidate.total
        and all(score.outcome == "FAIL" for score in baselines)
        and all(
            re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                str(item.get("action_candidate_digest", "")),
            )
            for item in candidate.case_results
        )
        and contract.get("permissions", {}).get("side_effects") == []
    )


def evaluate(stage: str) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def project_path(path: Path) -> str:
        return path.relative_to(ROOT).as_posix()

    def require(check_id: str, passed: bool, evidence: str) -> None:
        checks.append(
            {"id": check_id, "status": "PASS" if passed else "FAIL", "evidence": evidence}
        )

    intro_path = ROOT / "submission" / "INTRO.md"
    intro = intro_path.read_text(encoding="utf-8").strip() if intro_path.exists() else ""
    intro_body = "\n".join(
        line for line in intro.splitlines() if not line.startswith("#")
    ).strip()
    require(
        "preliminary.intro",
        bool(intro_body) and len(intro_body) <= 500,
        project_path(intro_path),
    )
    decks = sorted((ROOT / "submission").glob("*.pptx")) + sorted(
        (ROOT / "submission").glob("*.pdf")
    )
    require(
        "preliminary.deck",
        any(path.stat().st_size > 0 for path in decks),
        ", ".join(project_path(path) for path in decks) or "missing",
    )
    identity_path = ROOT / "submission" / "AGENT-IDENTITIES.md"
    identity_text = (
        identity_path.read_text(encoding="utf-8") if identity_path.exists() else ""
    )
    identity_names = {
        path.stem for path in (ROOT / "agentteams" / "identities").glob("*.json")
    }
    require(
        "preliminary.agent_identity_list",
        len(identity_names) == 5
        and all(f"`{name}`" in identity_text for name in identity_names),
        project_path(identity_path),
    )
    require("opensource.license", (ROOT / "LICENSE").is_file(), "LICENSE")
    require("opensource.readme", (ROOT / "README.md").is_file(), "README.md")

    if stage in {"semifinal", "final"}:
        manifest_path = ROOT / "evidence" / "latest" / "manifest.json"
        manifest_ok, manifest = _manifest_valid(manifest_path)
        require("semifinal.evidence_manifest", manifest_ok, project_path(manifest_path))
        run_id = str((manifest or {}).get("workflow_run_id", ""))
        nonce = str((manifest or {}).get("run_nonce", ""))
        receipt = _json(ROOT / "evidence" / "latest" / "rebase-receipt.json")
        demo = _json(ROOT / "evidence" / "latest" / "demo.json")
        require(
            "semifinal.executable_demo",
            _receipt_valid(receipt),
            "evidence/latest/rebase-receipt.json",
        )
        require(
            "semifinal.authority_aware_agent_orchestration",
            _orchestration_valid(demo),
            "evidence/latest/demo.json#collaboration",
        )
        require(
            "semifinal.governed_skill_lifecycle",
            _skill_governance_valid(demo),
            "evidence/latest/demo.json#receipt.qualification_report",
        )
        minimal_path = ROOT / "evidence" / "latest" / "minimal-rebase-certificate.json"
        require(
            "semifinal.proof_carrying_minimal_rebase",
            _minimal_certificate_valid(_json(minimal_path), receipt),
            project_path(minimal_path),
        )
        require(
            "semifinal.rollback_semantics",
            _rollback_valid(_json(ROOT / "evidence" / "latest" / "rollback-evidence.json")),
            "evidence/latest/rollback-evidence.json",
        )
        require(
            "semifinal.real_reversible_tool",
            _git_valid(
                _json(ROOT / "evidence" / "latest" / "git-tool-evidence.json"),
                run_id,
                nonce,
            ),
            "evidence/latest/git-tool-evidence.json",
        )
        require(
            "semifinal.correlated_observability",
            _observability_valid(
                _json(ROOT / "evidence" / "latest" / "observability.json"),
                run_id,
                nonce,
                str((receipt or {}).get("digest", "")),
                demo,
            ),
            "evidence/latest/observability.json",
        )
        live_path = ROOT / "evidence" / "agentteams" / "live-receipt.json"
        live = _json(live_path)
        require(
            "semifinal.live_agentteams",
            _live_valid(live),
            project_path(live_path),
        )
        candidate_path = (
            ROOT / "evidence" / "agentteams" / "candidate-ingestion-receipt.json"
        )
        require(
            "semifinal.agent_candidate_control_plane_ingestion",
            _candidate_ingestion_valid(_json(candidate_path), live, demo),
            project_path(candidate_path),
        )
        proof_pack_path = ROOT / "evidence" / "latest" / "proof-pack.json"
        require(
            "semifinal.structural_proof_pack",
            _proof_pack_valid(proof_pack_path),
            project_path(proof_pack_path),
        )
        videos = tuple((ROOT / "submission").glob("demo.*"))
        require(
            "semifinal.demo_video",
            any(
                path.suffix.lower() in {".mp4", ".mov", ".webm"}
                and path.stat().st_size > 0
                for path in videos
            ),
            "submission/demo.{mp4,mov,webm}",
        )

    if stage == "final":
        for name in ("CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md"):
            require(f"final.{name.lower()}", (ROOT / name).is_file(), name)
        require(
            "final.live_demo_runbook",
            (ROOT / "docs" / "DEMO-RUNBOOK.md").is_file(),
            "docs/DEMO-RUNBOOK.md",
        )

    failures = [item for item in checks if item["status"] == "FAIL"]
    return {
        "schema_version": "orgrebase.goai-gate.v2",
        "stage": stage,
        "decision": "GO" if not failures else "NO_GO",
        "checks": checks,
        "blocking_failures": [item["id"] for item in failures],
        "claim_boundary": (
            "This gate verifies submitted artifacts and evidence bindings; it does not "
            "predict the judges' decision."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stage", choices=("preliminary", "semifinal", "final"), required=True
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-go", action="store_true")
    args = parser.parse_args()
    result = evaluate(args.stage)
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    if args.require_go and result["decision"] != "GO":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
