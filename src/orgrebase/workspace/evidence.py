"""Content-addressed Workspace evidence export and independent index verification."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError, ToolInvocationReceipt
from orgrebase.workspace.models import EvidenceIndex, EvidenceIndexEntry, ToolCalledEvent

_CANARY_PREFIX = "ORGREBASE_CANARY_SECRET_"


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_payload(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _verify_tool_trace_binding(
    *, invocation: dict[str, Any], called_event: Any, run_id: str
) -> tuple[ToolInvocationReceipt, ToolCalledEvent]:
    try:
        contract = invocation["contract"]
        result = invocation["result"]
        receipt = ToolInvocationReceipt.model_validate(invocation["receipt"])
        event = ToolCalledEvent.model_validate(_safe_payload(called_event))
    except (KeyError, TypeError, ValueError) as exc:
        raise IntegrityError("WORKSPACE_TOOL_EVIDENCE_INVALID") from exc
    expected_tool_ref = f"{contract['id']}@{contract['version']}"
    valid = (
        receipt.workflow_run_id == run_id
        and event.run_id == run_id
        and event.invocation_receipt_ref == receipt.id
        and event.tool_ref == receipt.tool_ref == expected_tool_ref
        and event.request_digest == receipt.request_digest
        and event.result_digest == receipt.result_digest
        and sha256_digest(result) == receipt.result_digest
    )
    if not valid:
        raise IntegrityError("WORKSPACE_TOOL_EVIDENCE_BINDING_FAILED")
    return receipt, event


class WorkspaceEvidenceExporter:
    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.entries: list[EvidenceIndexEntry] = []

    def write_json(
        self,
        *,
        relative_path: str,
        value: Any,
        evidence_class: str,
        claim_supported: str,
        verifier_command: str,
        negative_test_ids: tuple[str, ...] = (),
        contains_sensitive_data: bool = False,
    ) -> Path:
        payload = _safe_payload(value)
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if _CANARY_PREFIX in text:
            raise IntegrityError(f"EVIDENCE_PRIVACY_CANARY_LEAK:{relative_path}")
        path = self.output_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        self.entries.append(
            EvidenceIndexEntry(
                artifact_ref=relative_path,
                media_type="application/json",
                sha256=_file_digest(path),
                evidence_class=evidence_class,
                claim_supported=claim_supported,
                verifier_command=verifier_command,
                negative_test_ids=negative_test_ids,
                contains_sensitive_data=contains_sensitive_data,
            )
        )
        return path

    def finalize(
        self,
        *,
        run_id: str,
        event_chain_head: str,
        generated_at: str = "2026-08-16T00:00:00Z",
    ) -> EvidenceIndex:
        index = EvidenceIndex(
            id=f"evidence-index:{run_id.replace(':', '-')}",
            run_id=run_id,
            entries=tuple(sorted(self.entries, key=lambda item: item.artifact_ref)),
            event_chain_head=event_chain_head,
            status="PASS",
            generated_at=generated_at,
        )
        path = self.output_dir / "evidence-index.json"
        path.write_text(
            json.dumps(index.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        return index


class EvidenceIndexVerifier:
    def verify(self, root: str | Path, index: EvidenceIndex | dict[str, Any]) -> dict[str, Any]:
        root_path = Path(root).resolve()
        parsed = index if isinstance(index, EvidenceIndex) else EvidenceIndex.model_validate(index)
        failures: list[str] = []
        seen: set[str] = set()
        for entry in parsed.entries:
            if entry.artifact_ref in seen:
                failures.append(f"DUPLICATE:{entry.artifact_ref}")
                continue
            seen.add(entry.artifact_ref)
            path = (root_path / entry.artifact_ref).resolve()
            if root_path not in path.parents:
                failures.append(f"PATH_ESCAPE:{entry.artifact_ref}")
                continue
            if not path.is_file():
                failures.append(f"MISSING:{entry.artifact_ref}")
                continue
            if _file_digest(path) != entry.sha256:
                failures.append(f"DIGEST:{entry.artifact_ref}")
            text = path.read_text(encoding="utf-8", errors="replace")
            if _CANARY_PREFIX in text:
                failures.append(f"CANARY_LEAK:{entry.artifact_ref}")
            if entry.evidence_class == "LIVE_AGENTTEAMS" and "provider_request_id" not in text:
                failures.append(f"LIVE_EVIDENCE_INCOMPLETE:{entry.artifact_ref}")
        if failures:
            raise IntegrityError("EVIDENCE_INDEX_VERIFICATION_FAILED:" + ",".join(failures))
        return {
            "status": "PASS",
            "entries": len(parsed.entries),
            "event_chain_head": parsed.event_chain_head,
            "index_digest": parsed.digest,
        }


def export_workspace_run(
    *,
    output_dir: str | Path,
    loop_result: dict[str, Any],
    evaluation: dict[str, Any] | None,
    skill_result: dict[str, Any] | None,
    agentteams_result: dict[str, Any],
    user_validation: Any | None = None,
) -> EvidenceIndex:
    exporter = WorkspaceEvidenceExporter(output_dir)
    run_id = str(loop_result["workflow_run_id"])
    if loop_result.get("run_id") != run_id:
        raise IntegrityError("WORKSPACE_RUN_ID_MISMATCH")
    tool_receipt, tool_event = _verify_tool_trace_binding(
        invocation=loop_result["tool_invocation"],
        called_event=loop_result["tool_called_event"],
        run_id=run_id,
    )
    formation = loop_result["formation"]
    launch = loop_result["launch_change"]
    currency = loop_result["currency_change"]
    exporter.write_json(
        relative_path="formation/task-receipt.json",
        value=formation,
        evidence_class="LOCAL_DETERMINISTIC",
        claim_supported="Quote v1, WorkTrace and graph snapshot were atomically formed.",
        verifier_command="make workspace-formation-check",
        negative_test_ids=("formation-rollback", "formation-idempotency"),
    )
    for name, value in sorted(loop_result.get("formation_artifacts", {}).items()):
        exporter.write_json(
            relative_path=f"formation/{name}.json",
            value=value,
            evidence_class="LOCAL_DETERMINISTIC",
            claim_supported=f"Atomic work-formation artifact: {name}.",
            verifier_command="make workspace-formation-check",
            negative_test_ids=("unmediated-read", "trace-tamper", "manifest-bijection"),
        )
    exporter.write_json(
        relative_path="tool/dependency-evidence-invocation.json",
        value=loop_result["tool_invocation"],
        evidence_class=tool_receipt.evidence_class.value,
        claim_supported=(
            "The main Workspace run executed the dependency-evidence tool and persisted "
            "its receipt."
        ),
        verifier_command="make workspace-evidence-check",
        negative_test_ids=("tool-authz-denied", "tool-idempotency-conflict"),
    )
    exporter.write_json(
        relative_path="tool/dependency-evidence-called-event.json",
        value=tool_event,
        evidence_class=tool_receipt.evidence_class.value,
        claim_supported="The tool event is bound to the same run, receipt, tool, request and result digests.",
        verifier_command="make workspace-evidence-check",
        negative_test_ids=("tool-run-mismatch", "tool-digest-mismatch"),
    )
    for label, result in (("launch", launch), ("currency", currency)):
        for key in (
            "change_set",
            "preview",
            "minimal_rebase_certificate",
            "approval",
            "rebase_receipt",
            "workspace_rebase_receipt",
            "quote",
            "graph_pointer",
        ):
            exporter.write_json(
                relative_path=f"rebase/{label}-{key.replace('_', '-')}.json",
                value=result.get(key),
                evidence_class="LOCAL_DETERMINISTIC",
                claim_supported=f"Certified {label} selective rebase artifact: {key}.",
                verifier_command="make workspace-rebase-check",
                negative_test_ids=("preview-drift-zero-write", "vmrc-tamper", "successor-atomicity"),
            )
    exporter.write_json(
        relative_path="repeatability/final-quote.json",
        value=loop_result["final_quote"],
        evidence_class="LOCAL_DETERMINISTIC",
        claim_supported="The restarted second change produced Quote v3 in EUR.",
        verifier_command="make workspace-repeatability-check",
    )
    exporter.write_json(
        relative_path="repeatability/final-graph-pointer.json",
        value=loop_result["final_graph_pointer"],
        evidence_class="LOCAL_DETERMINISTIC",
        claim_supported="Successor graph pointer advanced atomically to v3.",
        verifier_command="make workspace-repeatability-check",
    )
    if evaluation is not None:
        exporter.write_json(
            relative_path="evaluation/owb-results.json",
            value=evaluation,
            evidence_class="SYNTHETIC_FIXTURE",
            claim_supported="OWB v1.1 deterministic conformance, baselines and ablations.",
            verifier_command="make workspace-benchmark-check",
            negative_test_ids=("gold-isolation", "license-gate", "cross-org-isolation"),
        )
    if skill_result is not None:
        exporter.write_json(
            relative_path="skill/candidate.json",
            value=skill_result["candidate"],
            evidence_class="LOCAL_DETERMINISTIC",
            claim_supported="Curator candidate bytes are immutable and digest-bound.",
            verifier_command="make workspace-skill-check",
            negative_test_ids=("candidate-byte-mutation", "repository-substitute"),
        )
        exporter.write_json(
            relative_path="skill/evaluation-receipt.json",
            value=skill_result["evaluation"],
            evidence_class="LOCAL_DETERMINISTIC",
            claim_supported="The exact candidate passed replay, held-out, regression and safety gates.",
            verifier_command="make workspace-skill-check",
        )
    if user_validation is not None:
        exporter.write_json(
            relative_path="user-validation.json",
            value=user_validation,
            evidence_class=str(
                getattr(user_validation, "evidence_class", "NOT_RUN")
                if not isinstance(user_validation, dict)
                else user_validation.get("evidence_class", "NOT_RUN")
            ),
            claim_supported=(
                "Records participant count, evidence class, consent gate, and current "
                "validation status."
            ),
            verifier_command="make workspace-user-validation-check",
        )
    exporter.write_json(
        relative_path="agentteams/status.json",
        value=agentteams_result,
        evidence_class=str(agentteams_result.get("evidence_class", "NOT_RUN")),
        claim_supported=(
            "Records AgentTeams static/live status, missing prerequisites, and "
            "target-write boundary."
        ),
        verifier_command="make workspace-agentteams-check",
    )
    chain = loop_result["event_chain"]
    head = str(chain["head_digest"] if isinstance(chain, dict) else chain)
    index = exporter.finalize(run_id=run_id, event_chain_head=head)
    EvidenceIndexVerifier().verify(output_dir, index)
    return index


class WorkspaceEvidenceBuilder:
    """Execute the complete local profile and emit one independently verifiable pack."""

    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir)

    @staticmethod
    def _formation_artifacts(store: Any, receipt: Any) -> dict[str, Any]:
        artifact_refs = {
            "coalition-plan": receipt.coalition_plan_ref,
            "task-context": receipt.context_manifest_ref,
            "work-trace": receipt.trace_ref,
            "trace-coverage": receipt.coverage_receipt_ref,
            "runtime-dependency-manifest": receipt.dependency_manifest_ref,
            "workspace-graph-snapshot-v1": receipt.graph_snapshot_ref,
        }
        result: dict[str, Any] = {}
        for name, ref in artifact_refs.items():
            result[name] = store.load_artifact(ref).payload
        for index, ref in enumerate(receipt.admission_decision_refs, start=1):
            result[f"admission-decision-{index:02d}"] = store.load_artifact(ref).payload
        object_id, version = receipt.deliverable_ref.rsplit("@", 1)
        result["quote-v1"] = store.get_object(object_id, version).model_dump(mode="json")
        return result

    def build(self) -> EvidenceIndex:
        from orgrebase.workspace.benchmark import run_owb_evaluation
        from orgrebase.workspace.service import WorkspaceService
        from orgrebase.workspace.skill_foundry import SkillFoundryService
        from orgrebase.workspace.transport import agentteams_status
        from orgrebase.workspace.user_validation import UserValidationService

        self.output_dir.mkdir(parents=True, exist_ok=True)
        database = self.output_dir / "workspace.db"
        if database.exists():
            database.unlink()
        service = WorkspaceService(store_path=database)
        loop_result = service.run_local_loop()
        evaluation = run_owb_evaluation()

        reopened = WorkspaceService.reopen(database)
        try:
            loop_result["formation_artifacts"] = self._formation_artifacts(
                reopened.store, loop_result["formation"]
            )
            skill_result = SkillFoundryService(reopened.store).run()
            loop_result["event_chain"] = reopened.store.verify_event_chain()
        finally:
            reopened.close()

        user_validation = UserValidationService.summarize(())
        agentteams = agentteams_status()
        index = export_workspace_run(
            output_dir=self.output_dir,
            loop_result=loop_result,
            evaluation=evaluation,
            skill_result=skill_result,
            agentteams_result=agentteams,
            user_validation=user_validation,
        )
        summary = {
            "schema_version": "orgrebase.workspace-demo.v1",
            "workflow_run_id": loop_result["workflow_run_id"],
            "run_id": loop_result["run_id"],
            "stage_run_ids": loop_result["stage_run_ids"],
            "final_quote": _safe_payload(loop_result["final_quote"]),
            "final_graph_pointer": _safe_payload(loop_result["final_graph_pointer"]),
            "primary_evaluation_status": evaluation["primary_status"],
            "primary_evaluation_score": evaluation["primary_score"],
            "skill_status": skill_result["status"],
            "agentteams": agentteams,
            "user_validation": _safe_payload(user_validation),
            "evidence_index_ref": "evidence-index.json",
            "evidence_index_digest": index.digest,
            "event_chain": loop_result["event_chain"],
            "tool_evidence": {
                "invocation_receipt_ref": loop_result["tool_invocation"]["receipt"]["id"],
                "tool_called_event_ref": loop_result["tool_called_event"].event_id,
                "request_digest": loop_result["tool_invocation"]["receipt"][
                    "request_digest"
                ],
                "result_digest": loop_result["tool_invocation"]["receipt"]["result_digest"],
            },
        }
        (self.output_dir / "workspace-demo.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        # The SQLite file is an implementation scratchpad, not a public evidence
        # artifact. Every claim needed for verification is exported above as
        # content-addressed JSON. Removing it prevents accidental distribution of
        # future connector data when this builder is reused beyond the synthetic demo.
        if database.exists():
            database.unlink()
        return index


def verify_evidence_directory(directory: str | Path) -> dict[str, Any]:
    root = Path(directory)
    path = root / "evidence-index.json"
    if not path.is_file():
        path = root / "index.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    result = EvidenceIndexVerifier().verify(root, payload)
    invocation = json.loads(
        (root / "tool/dependency-evidence-invocation.json").read_text(encoding="utf-8")
    )
    called_event = json.loads(
        (root / "tool/dependency-evidence-called-event.json").read_text(encoding="utf-8")
    )
    _verify_tool_trace_binding(
        invocation=invocation,
        called_event=called_event,
        run_id=str(payload["run_id"]),
    )
    final_quote = json.loads((root / "repeatability/final-quote.json").read_text(encoding="utf-8"))
    final_pointer = json.loads(
        (root / "repeatability/final-graph-pointer.json").read_text(encoding="utf-8")
    )
    if (
        final_quote.get("version") != "v3"
        or final_quote.get("payload", {}).get("launch_date") != "2026-09-15"
        or final_quote.get("payload", {}).get("currency") != "EUR"
    ):
        raise IntegrityError("WORKSPACE_REPEATABILITY_EVIDENCE_FAILED")
    if final_pointer.get("version") != "v3":
        raise IntegrityError("WORKSPACE_SUCCESSOR_GRAPH_EVIDENCE_FAILED")
    return result
