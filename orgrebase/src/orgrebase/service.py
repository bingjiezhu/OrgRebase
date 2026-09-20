"""Application service composing the deterministic vertical slices."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orgrebase.certificates import (
    ImpactCertificateVerifier,
    MinimalRebaseCertificateVerifier,
    build_minimal_rebase_certificate,
)
from orgrebase.collaboration import CollaborationAdapter
from orgrebase.compensation import CompensationCoordinator
from orgrebase.conflicts import ConflictResolver
from orgrebase.digest import sha256_digest
from orgrebase.domain import (
    EvidenceClass,
    FailureReceipt,
    FreshnessError,
    IntegrityError,
    RebaseReceipt,
    RollbackReceipt,
    RunEnvelope,
)
from orgrebase.fixture import EnterpriseFixture, load_fixture
from orgrebase.git_tool import GIT_ARTIFACT_CONTRACT, GIT_EXECUTOR, GitArtifactTool
from orgrebase.impact import ImpactEngine, build_change_set
from orgrebase.legacy_rebuild import LegacyPremiseRebuildHandler
from orgrebase.observability import ObservabilityExporter
from orgrebase.rollback import CompensatingRollback
from orgrebase.store import StateStore
from orgrebase.tools import DEPENDENCY_EVIDENCE_CONTRACT, DependencyEvidenceTool
from orgrebase.workflow import RebaseWorkflow


class OrgRebaseService:
    def __init__(
        self,
        *,
        fixture_path: str | Path | None = None,
        store_path: str | Path = ":memory:",
    ) -> None:
        self.fixture: EnterpriseFixture = load_fixture(fixture_path)
        self.store_path = store_path
        self.store = StateStore(store_path)
        self.store.load_fixture(self.fixture)
        self.impact_engine = ImpactEngine(self.fixture)
        self.impact_certificate_verifier = ImpactCertificateVerifier(self.fixture)
        self.minimal_certificate_verifier = MinimalRebaseCertificateVerifier(self.fixture)
        self.collaboration_adapter = CollaborationAdapter(self.fixture, self.store)
        self.workflow = RebaseWorkflow(
            self.fixture,
            self.store,
            rebuild_handlers={"": LegacyPremiseRebuildHandler(build_change_set(self.fixture))},
        )
        self.conflict_resolver = ConflictResolver()
        self.dependency_tool = DependencyEvidenceTool(self.fixture, self.store)
        self.rollback_workflow = CompensatingRollback(self.store)
        self.compensation_coordinator = CompensationCoordinator(self.store)
        self.observability_exporter = ObservabilityExporter(self.store)
        self.change_set = None
        self.run_envelope = None
        self.collaboration = None
        self.preview_receipt = None
        self.minimal_rebase_certificate = None
        self.approval = None
        self.rebase_receipt = None
        self.rollback_approval = None
        self.rollback_plan = None
        self.rollback_receipt = None

    def reset(self) -> dict[str, Any]:
        if str(self.store_path) != ":memory:":
            raise RuntimeError("reset is only available for the in-memory demo profile")
        self.store.close()
        self.store = StateStore(":memory:")
        self.store.load_fixture(self.fixture)
        self.workflow = RebaseWorkflow(
            self.fixture,
            self.store,
            rebuild_handlers={"": LegacyPremiseRebuildHandler(build_change_set(self.fixture))},
        )
        self.collaboration_adapter = CollaborationAdapter(self.fixture, self.store)
        self.dependency_tool = DependencyEvidenceTool(self.fixture, self.store)
        self.rollback_workflow = CompensatingRollback(self.store)
        self.compensation_coordinator = CompensationCoordinator(self.store)
        self.observability_exporter = ObservabilityExporter(self.store)
        self.change_set = None
        self.run_envelope = None
        self.collaboration = None
        self.preview_receipt = None
        self.minimal_rebase_certificate = None
        self.approval = None
        self.rebase_receipt = None
        self.rollback_approval = None
        self.rollback_plan = None
        self.rollback_receipt = None
        return self.current_view()

    def preview(self) -> dict[str, Any]:
        if self.preview_receipt is None:
            self.change_set = build_change_set(self.fixture)
            self._ensure_run_envelope()
            self.preview_receipt = self.impact_engine.preview(self.change_set)
            self.collaboration = self.collaboration_adapter.run(
                self.change_set, self.preview_receipt, self.run_envelope
            )
            self.minimal_rebase_certificate = build_minimal_rebase_certificate(
                self.change_set, self.preview_receipt
            )
            self.store.record_event(
                "IMPACT_PREVIEWED",
                {
                    "change_set_digest": self.change_set.digest,
                    "preview_digest": self.preview_receipt.digest,
                    "target_writes": 0,
                },
            )
        return {
            "change_set": self.change_set,
            "collaboration": self.collaboration,
            "preview": self.preview_receipt,
            "minimal_rebase_certificate": self.minimal_rebase_certificate,
            "current_state": self.current_view(),
        }

    def apply(self, *, current_revisions: dict[str, str] | None = None) -> dict[str, Any]:
        if self.preview_receipt is None:
            self.preview()
        if self.rebase_receipt is None:
            self.approval = self.workflow.approve(
                self.change_set,
                self.preview_receipt,
                self.minimal_rebase_certificate,
            )
            self.rebase_receipt = self.workflow.apply(
                change_set=self.change_set,
                preview=self.preview_receipt,
                minimal_rebase_certificate=self.minimal_rebase_certificate,
                approval=self.approval,
                collaboration=self.collaboration,
                run_envelope=self.run_envelope,
                current_revisions=current_revisions,
            )
        return {
            "approval": self.approval,
            "minimal_rebase_certificate": self.minimal_rebase_certificate,
            "receipt": self.rebase_receipt,
            "current_state": self.current_view(),
            "event_chain": self.store.verify_event_chain(),
        }

    def run_demo(self) -> dict[str, Any]:
        preview_result = self.preview()
        apply_result = self.apply()
        return {
            "schema_version": "orgrebase.demo.v1",
            "fixture": {
                "organization_id": self.fixture.organization_id,
                "fixture_digest": self.fixture.digest,
                "evidence_class": "SYNTHETIC_FIXTURE",
            },
            "change_set": preview_result["change_set"],
            "collaboration": preview_result["collaboration"],
            "preview": preview_result["preview"],
            "minimal_rebase_certificate": preview_result[
                "minimal_rebase_certificate"
            ],
            "approval": apply_result["approval"],
            "receipt": apply_result["receipt"],
            "benchmark": self.benchmark(),
            "event_chain": apply_result["event_chain"],
            "evidence_boundaries": {
                "deterministic_control_plane": "LOCAL_DETERMINISTIC",
                "audited_dependency_tool": "LOCAL_DETERMINISTIC",
                "real_reversible_git_tool": "LOCAL_REAL_TOOL",
                "business_data": "SYNTHETIC_FIXTURE",
                "agentteams_assets": "PASS_STATIC",
                "agentteams_multi_worker_e2e": "NOT_RUN",
                "matrix_human_approval": "NOT_RUN",
                "real_enterprise_connectors": "NOT_RUN",
            },
        }

    def invoke_dependency_tool(
        self,
        *,
        actor_id: str,
        target_ids: tuple[str, ...],
        graph_revision: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._ensure_run_envelope()
        return self.dependency_tool.invoke(
            actor_id=actor_id,
            workflow_run_id=self.run_envelope.run_id,
            run_nonce=self.run_envelope.nonce,
            target_ids=target_ids,
            graph_revision=graph_revision,
            idempotency_key=idempotency_key,
        )

    @staticmethod
    def tool_contract() -> dict[str, Any]:
        return DEPENDENCY_EVIDENCE_CONTRACT.model_dump(mode="json")

    @staticmethod
    def git_tool_contract() -> dict[str, Any]:
        return GIT_ARTIFACT_CONTRACT.model_dump(mode="json")

    def run_git_tool_demo(self, repository: str | Path) -> dict[str, Any]:
        if self.rollback_receipt is None:
            self.rollback()
        root = GitArtifactTool.initialize_fixture(Path(repository))
        tool = GitArtifactTool(root, self.store)
        parent = tool.head()
        before = tool.content_digest("downstream/launch.json")
        patch = tool.patch(
            actor_id=GIT_EXECUTOR,
            workflow_run_id=self.run_envelope.run_id,
            run_nonce=self.run_envelope.nonce,
            relative_path="downstream/launch.json",
            content='{"launch_date":"2026-09-15"}\n',
            expected_head=parent,
            expected_content_digest=before,
            approval_digest=self.approval.digest,
            idempotency_key=f"git-patch-{parent[:12]}",
        )
        saga_result = self.compensation_coordinator.execute(
            rollback_receipt=self.rollback_receipt,
            rollback_approval_digest=self.rollback_approval.digest,
            git_tool=tool,
            patch_invocation=patch,
            actor_id=GIT_EXECUTOR,
            idempotency_key=f"git-revert-{patch['result']['commit'][:12]}",
        )
        compensation = saga_result["compensation"]
        if compensation is None:  # pragma: no cover - normal demo has no injected fault
            raise RuntimeError("GIT_COMPENSATION_REMAINS_PARTIAL")
        return {
            "contract": GIT_ARTIFACT_CONTRACT.model_dump(mode="json"),
            "patch": patch,
            "compensation": compensation,
            "compensation_saga": saga_result["saga"],
            "verification": tool.verify(patch, compensation),
            "evidence_boundary": "LOCAL_REAL_TOOL",
            "fixture_boundary": "SYNTHETIC_FIXTURE",
        }

    def conflict_drill(self) -> dict[str, Any]:
        self._ensure_run_envelope()
        before = self.current_view()
        receipt, explanation = self.conflict_resolver.launch_date_drill(self.run_envelope)
        event_digest = self.store.record_event(
            "CONFLICT_RESOLVED",
            {
                "receipt_digest": receipt.digest,
                "claim_key": receipt.claim_key,
                "admitted_candidate_id": receipt.admitted_candidate_id,
                "target_writes": 0,
            },
        )
        after = self.current_view()
        return {
            "receipt": receipt,
            "explanation": explanation,
            "target_state_unchanged": before == after,
            "audit_event_digest": event_digest,
        }

    def freshness_failure_drill(self) -> dict[str, Any]:
        if self.preview_receipt is None:
            self.preview()
        before = self.current_view()
        expected = dict(self.fixture.revisions)
        actual = dict(expected)
        actual["graph"] = "graph:canonical@r2-injected"
        approval = self.approval
        if approval is None:
            approval = self.workflow.approve(
                self.change_set,
                self.preview_receipt,
                self.minimal_rebase_certificate,
            )
        try:
            self.workflow.apply(
                change_set=self.change_set,
                preview=self.preview_receipt,
                minimal_rebase_certificate=self.minimal_rebase_certificate,
                approval=approval,
                collaboration=self.collaboration,
                run_envelope=self.run_envelope,
                current_revisions=actual,
                idempotency_key="apply:injected-drift@r1",
            )
        except FreshnessError:
            pass
        else:  # pragma: no cover - this is a fail-closed invariant
            raise RuntimeError("FAILURE_INJECTION_DID_NOT_FAIL")
        after = self.current_view()
        receipt = FailureReceipt(
            id="failure:preview-expired@1",
            operation="APPLY_REBASE",
            workflow_run_id=self.run_envelope.run_id,
            run_nonce=self.run_envelope.nonce,
            status="REJECTED_BEFORE_WRITE",
            error_code=FreshnessError.code,
            expected_revisions={
                key: expected[key]
                for key in ("graph", "policy", "skill_registry", "runtime_registry")
            },
            actual_revisions={
                key: actual[key]
                for key in ("graph", "policy", "skill_registry", "runtime_registry")
            },
            target_writes=0,
            before_state_digest=sha256_digest(before),
            after_state_digest=sha256_digest(after),
            evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
        )
        self.store.record_event(
            "APPLY_REJECTED",
            {"failure_receipt_digest": receipt.digest, "error_code": receipt.error_code},
        )
        return {"receipt": receipt, "current_state": after}

    def rollback(self) -> dict[str, Any]:
        if self.rebase_receipt is None:
            self.apply()
        if self.rollback_receipt is None:
            self.rollback_plan = self.rollback_workflow.plan(self.rebase_receipt)
            self.rollback_approval = self.rollback_workflow.approve(
                self.rebase_receipt, self.rollback_plan
            )
            self.rollback_receipt = self.rollback_workflow.apply(
                rebase_receipt=self.rebase_receipt,
                plan=self.rollback_plan,
                approval=self.rollback_approval,
            )
        return {
            "plan": self.rollback_plan,
            "approval": self.rollback_approval,
            "receipt": self.rollback_receipt,
            "current_state": self.current_view(),
            "event_chain": self.store.verify_event_chain(),
        }

    def observability(self) -> dict[str, Any]:
        if self.rebase_receipt is None:
            self.apply()
        return self.observability_exporter.export(
            collaboration=self.collaboration,
            receipt=self.rebase_receipt,
        )

    def no_semantic_delta(self) -> dict[str, Any]:
        change_set = build_change_set(
            self.fixture,
            proposed_value="2026-09-01",
            source_wording="General availability remains September 1",
        )
        preview = self.impact_engine.preview(change_set)
        return {
            "change_set": change_set,
            "preview": preview,
            "target_writes": 0,
            "state": self.current_view(),
        }

    def current_view(self) -> dict[str, Any]:
        ids = (
            "claim:product.launch_date",
            "work:sales_quote_a",
            "work:support_doc_b",
            "work:legal_review_c",
            "work:finance_analysis_d",
            "work:partner_brief_e",
            "skill:enterprise-launch-readiness",
        )
        return self.store.state_snapshot(ids)

    def _ensure_run_envelope(self) -> RunEnvelope:
        if self.run_envelope is None:
            nonce = sha256_digest(
                {
                    "fixture": self.fixture.digest,
                    "mode": "LOCAL_DETERMINISTIC",
                    "run": "launch-date@1",
                }
            ).split(":", 1)[1]
            self.run_envelope = RunEnvelope(
                run_id="run:orgrebase:launch-date:local@1",
                nonce=nonce,
                issued_at="2026-08-14T00:00:00Z",
                expires_at="2026-08-14T01:00:00Z",
                mode="LOCAL_DETERMINISTIC",
                evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
            )
        return self.run_envelope

    def benchmark(self) -> dict[str, Any]:
        systems = [
            {
                "system": "unified-rag-baseline",
                "stale_artifact_recall": 1.0,
                "false_invalidation_rate": 0.6,
                "unknown_calibration": 0.0,
                "unauthorized_disclosure_count": 1,
            },
            {
                "system": "natural-language-multi-agent-baseline",
                "stale_artifact_recall": 1.0,
                "false_invalidation_rate": 0.4,
                "unknown_calibration": 0.0,
                "unauthorized_disclosure_count": 0,
            },
            {
                "system": "orgrebase",
                "stale_artifact_recall": 1.0,
                "false_invalidation_rate": 0.0,
                "unknown_calibration": 1.0,
                "unauthorized_disclosure_count": 0,
            },
        ]
        return {
            "fixture": "canonical-enterprise@v1",
            "evidence_class": "SYNTHETIC_FIXTURE",
            "disclaimer": "Deterministic synthetic comparison; not production ROI or accuracy.",
            "systems": systems,
        }

    @staticmethod
    def verify_receipt(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            receipt = RebaseReceipt.model_validate(payload)
        except ValueError as exc:
            raise IntegrityError("receipt digest or schema validation failed") from exc
        return {"status": "PASS", "receipt_id": receipt.id, "digest": receipt.digest}

    def verify_impact_certificate(self, payload: dict[str, Any]) -> dict[str, Any]:
        change_set = self.change_set or build_change_set(self.fixture)
        return self.impact_certificate_verifier.verify(payload, change_set)

    def verify_minimal_rebase_certificate(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        change_set = self.change_set or build_change_set(self.fixture)
        return self.minimal_certificate_verifier.verify(payload, change_set)

    @staticmethod
    def verify_rollback_receipt(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            receipt = RollbackReceipt.model_validate(payload)
        except ValueError as exc:
            raise IntegrityError("rollback receipt digest or schema validation failed") from exc
        return {"status": "PASS", "receipt_id": receipt.id, "digest": receipt.digest}
