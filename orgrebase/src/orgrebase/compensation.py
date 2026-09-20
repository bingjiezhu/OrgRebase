"""Durable coordinator for compensation that spans SQLite state and real Git."""

from __future__ import annotations

from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.domain import (
    CompensationSagaReceipt,
    EvidenceClass,
    FreshnessError,
    RollbackReceipt,
    ToolInvocationReceipt,
)
from orgrebase.git_tool import GitArtifactTool
from orgrebase.store import StateStore


def _operational_error_code(exc: BaseException) -> str:
    token = str(exc).split(":", 1)[0].strip()
    if token.isidentifier() and token == token.upper():
        return token
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code
    return type(exc).__name__


class CompensationCoordinator:
    """Expose partial rollback honestly and make the remaining action retryable."""

    def __init__(self, store: StateStore) -> None:
        self.store = store

    def execute(
        self,
        *,
        rollback_receipt: RollbackReceipt,
        rollback_approval_digest: str,
        git_tool: GitArtifactTool,
        patch_invocation: dict[str, Any],
        actor_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        patch_receipt = ToolInvocationReceipt.model_validate(patch_invocation["receipt"])
        self._validate_run(rollback_receipt, patch_receipt)
        request_digest = sha256_digest(
            {
                "rollback_receipt_digest": rollback_receipt.digest,
                "rollback_approval_digest": rollback_approval_digest,
                "git_patch_receipt_digest": patch_receipt.digest,
                "repository_id": git_tool.repository_id,
                "actor_id": actor_id,
            }
        )
        saga_key = f"compensation:{idempotency_key}"
        existing = self.store.get_compensation_saga(saga_key, request_digest)
        if existing and existing["status"] == "ROLLED_BACK_PENDING_REBASE":
            receipt = CompensationSagaReceipt.model_validate(existing["receipt"])
            return {
                "saga": receipt.model_dump(mode="json"),
                "compensation": receipt.external_compensation["invocation"],
            }
        attempt = int(existing["attempt"]) + 1 if existing else 1
        compensation_key = f"{idempotency_key}:git"
        try:
            compensation = git_tool.compensate(
                actor_id=actor_id,
                workflow_run_id=rollback_receipt.workflow_run_id,
                run_nonce=rollback_receipt.run_nonce,
                patch_invocation=patch_invocation,
                approval_digest=rollback_approval_digest,
                idempotency_key=compensation_key,
            )
        except BaseException as exc:
            error_code = _operational_error_code(exc)
            patch_commit = patch_receipt.external_operation_id
            # HEAD drift or an unavailable repository does not prove that the
            # patch was reverted. Retain the exact obligation for reconciliation.
            residual_effects = (
                {
                    "kind": "GIT_COMMIT",
                    "repository_id": git_tool.repository_id,
                    "commit": patch_commit,
                    "patch_receipt_digest": patch_receipt.digest,
                    "status": "RECONCILIATION_REQUIRED",
                },
            )
            receipt = CompensationSagaReceipt(
                id=f"compensation-saga:{idempotency_key}@attempt-{attempt}",
                status="ROLLBACK_PARTIAL",
                workflow_run_id=rollback_receipt.workflow_run_id,
                run_nonce=rollback_receipt.run_nonce,
                rollback_receipt_digest=rollback_receipt.digest,
                rollback_approval_digest=rollback_approval_digest,
                git_patch_receipt_digest=patch_receipt.digest,
                attempt=attempt,
                internal_compensation={
                    "status": "SUCCEEDED",
                    "receipt_digest": rollback_receipt.digest,
                    "authoritative_claim_unchanged": (
                        rollback_receipt.authoritative_claim_unchanged
                    ),
                },
                external_compensation={
                    "status": "PENDING_RETRY",
                    "repository_id": git_tool.repository_id,
                    "error_code": error_code,
                },
                residual_effects=residual_effects,
                next_action="RETRY_EXACT_GIT_COMPENSATION",
                evidence_class=EvidenceClass.LOCAL_REAL_TOOL,
            )
            try:
                self._persist(saga_key, request_digest, receipt)
            except BaseException as persistence_error:
                if not isinstance(exc, Exception):
                    # Preserve cancellation; its cause exposes the failed recovery
                    # write rather than claiming that the obligation was recorded.
                    raise exc from persistence_error
                raise
            if not isinstance(exc, Exception):
                raise
            return {"saga": receipt.model_dump(mode="json"), "compensation": None}

        compensation_receipt = ToolInvocationReceipt.model_validate(compensation["receipt"])
        receipt = CompensationSagaReceipt(
            id=f"compensation-saga:{idempotency_key}@attempt-{attempt}",
            status="ROLLED_BACK_PENDING_REBASE",
            workflow_run_id=rollback_receipt.workflow_run_id,
            run_nonce=rollback_receipt.run_nonce,
            rollback_receipt_digest=rollback_receipt.digest,
            rollback_approval_digest=rollback_approval_digest,
            git_patch_receipt_digest=patch_receipt.digest,
            attempt=attempt,
            internal_compensation={
                "status": "SUCCEEDED",
                "receipt_digest": rollback_receipt.digest,
                "authoritative_claim_unchanged": (
                    rollback_receipt.authoritative_claim_unchanged
                ),
            },
            external_compensation={
                "status": "SUCCEEDED",
                "receipt_digest": compensation_receipt.digest,
                "external_operation_id": compensation_receipt.external_operation_id,
                "invocation": compensation,
            },
            residual_effects=(),
            next_action=None,
            evidence_class=EvidenceClass.LOCAL_REAL_TOOL,
        )
        self._persist(saga_key, request_digest, receipt)
        return {"saga": receipt.model_dump(mode="json"), "compensation": compensation}

    def _persist(
        self,
        saga_key: str,
        request_digest: str,
        receipt: CompensationSagaReceipt,
    ) -> None:
        with self.store.transaction() as connection:
            self.store.append_event(
                connection,
                "COMPENSATION_SAGA_UPDATED",
                {
                    "saga_key": saga_key,
                    "status": receipt.status,
                    "attempt": receipt.attempt,
                    "receipt_digest": receipt.digest,
                    "residual_effect_count": len(receipt.residual_effects),
                },
            )
            self.store.save_artifact(
                connection,
                receipt.id,
                "application/vnd.orgrebase.compensation-saga-receipt+json",
                receipt.model_dump(mode="json"),
            )
            self.store.save_compensation_saga(
                connection,
                saga_key=saga_key,
                request_digest=request_digest,
                status=receipt.status,
                attempt=receipt.attempt,
                receipt=receipt.model_dump(mode="json"),
            )

    @staticmethod
    def _validate_run(
        rollback_receipt: RollbackReceipt,
        patch_receipt: ToolInvocationReceipt,
    ) -> None:
        if rollback_receipt.status != "ROLLED_BACK_PENDING_REBASE":
            raise FreshnessError("INTERNAL_COMPENSATION_NOT_COMPLETE")
        if (
            patch_receipt.workflow_run_id != rollback_receipt.workflow_run_id
            or patch_receipt.run_nonce != rollback_receipt.run_nonce
        ):
            raise FreshnessError("CROSS_RUN_COMPENSATION")
        if patch_receipt.evidence_class != EvidenceClass.LOCAL_REAL_TOOL:
            raise FreshnessError("PATCH_IS_NOT_REAL_TOOL_EVIDENCE")
