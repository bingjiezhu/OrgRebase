"""Independently approved compensation of downstream effects.

Rollback never rewrites an authoritative Claim by default. It restores downstream
artifacts as new immutable versions and marks them pending rebase against the
still-current Claim.
"""

from __future__ import annotations

from orgrebase.digest import sha256_digest
from orgrebase.domain import (
    AuthorizationError,
    CoverageBasis,
    EvidenceClass,
    FreshnessError,
    ObjectState,
    RebaseReceipt,
    RollbackApproval,
    RollbackPlan,
    RollbackReceipt,
    VersionedObject,
)
from orgrebase.store import StateStore

ROLLBACK_SCOPE = "rollback:downstream-compensation"


class CompensatingRollback:
    def __init__(self, store: StateStore) -> None:
        self.store = store

    def plan(self, receipt: RebaseReceipt) -> RollbackPlan:
        if receipt.status != "COMPLETED":
            raise FreshnessError("only a completed RebaseReceipt can be compensated")

        claim_change = receipt.applied_claims[0]
        claim_id = str(claim_change["object_id"])
        authoritative_claim_ref = f"{claim_id}@{claim_change['to']}"
        tracked_ids = (
            claim_id,
            *(str(item["object_id"]) for item in receipt.transitions),
            *(str(item["object_id"]) for item in receipt.unknown),
        )
        expected_current_state: dict[str, dict[str, str]] = {}
        for object_id in dict.fromkeys(tracked_ids):
            current = self.store.get_object(object_id)
            expected_current_state[object_id] = {
                "ref": current.ref,
                "state": current.state.value,
                "digest": current.digest,
            }

        if expected_current_state[claim_id]["ref"] != authoritative_claim_ref:
            raise FreshnessError("authoritative Claim no longer matches the RebaseReceipt")

        restore_actions = tuple(
            {
                "action": "RESTORE_DOWNSTREAM_CONTENT_AS_PENDING_REBASE",
                "object_id": str(transition["object_id"]),
                "current_ref": str(transition["to"]),
                "restore_from_ref": str(transition["from"]),
                "next_version": self._successor_version(
                    self.store.get_object(str(transition["object_id"])).version
                ),
            }
            for transition in receipt.transitions
            if transition.get("to_state") == "CURRENT"
        )
        quarantine_actions = tuple(
            {
                "action": "QUARANTINE_CANARY_AND_REQUIRE_REQUALIFICATION",
                "object_id": str(transition["object_id"]),
                "current_ref": str(transition["to"]),
                "restore_pointer_ref": str(transition["from"]),
            }
            for transition in receipt.transitions
            if transition.get("to_state") == "CANARY"
        )
        actions = (*restore_actions, *quarantine_actions)
        preserved_states = (
            {
                "object_id": claim_id,
                "ref": authoritative_claim_ref,
                "reason": "AUTHORITATIVE_FACT_IS_NOT_A_DOWNSTREAM_SIDE_EFFECT",
            },
            *(
                {
                    "object_id": str(item["object_id"]),
                    "ref": expected_current_state[str(item["object_id"])]["ref"],
                    "state": "REVIEW_REQUIRED",
                    "reason": "UNKNOWN_REMAINS_UNKNOWN_AFTER_COMPENSATION",
                }
                for item in receipt.unknown
            ),
        )
        return RollbackPlan(
            id="rollback-plan:launch-date@1",
            rebase_receipt_digest=receipt.digest,
            workflow_run_id=receipt.workflow_run_id,
            run_nonce=receipt.run_nonce,
            authoritative_claim_ref=authoritative_claim_ref,
            expected_current_state=expected_current_state,
            actions=actions,
            preserved_states=preserved_states,
            target_status="ROLLED_BACK_PENDING_REBASE",
        )

    @staticmethod
    def approve(
        receipt: RebaseReceipt,
        plan: RollbackPlan,
        *,
        actor_id: str = "human:rollback-approver",
        reason: str = "downstream effects must be compensated without changing authority",
    ) -> RollbackApproval:
        if plan.rebase_receipt_digest != receipt.digest:
            raise FreshnessError("rollback plan is not bound to the exact RebaseReceipt")
        if actor_id == receipt.approval_actor_id:
            raise AuthorizationError("rollback approver must be independent from Apply approver")
        return RollbackApproval(
            id="rollback-approval:launch-date@1",
            actor_id=actor_id,
            workflow_run_id=receipt.workflow_run_id,
            run_nonce=receipt.run_nonce,
            actor_role="independent-rollback-approver",
            authority_scope=(ROLLBACK_SCOPE,),
            rebase_receipt_digest=receipt.digest,
            rollback_plan_digest=plan.digest,
            apply_approval_digest=receipt.approval_digest,
            apply_approver_id=receipt.approval_actor_id,
            reason=reason,
            approved_at="2026-08-14T00:06:00Z",
            method="LOCAL_EXPLICIT_COMPENSATION_APPROVAL",
        )

    def apply(
        self,
        *,
        rebase_receipt: RebaseReceipt,
        plan: RollbackPlan,
        approval: RollbackApproval,
        idempotency_key: str = "rollback:launch-date@r1",
    ) -> RollbackReceipt:
        self._validate_bindings(rebase_receipt, plan, approval)
        request_digest = sha256_digest(
            {
                "rebase_receipt": rebase_receipt.digest,
                "plan": plan.digest,
                "approval": approval.digest,
            }
        )
        existing = self.store.get_idempotent(idempotency_key, request_digest)
        if existing is not None:
            return RollbackReceipt.model_validate(existing)

        self._assert_expected_state(plan)
        claim_id, claim_version = plan.authoritative_claim_ref.rsplit("@", 1)
        authoritative_before = self.store.get_object(claim_id)
        if authoritative_before.version != claim_version:
            raise FreshnessError("authoritative Claim drifted before compensation")

        transitions: list[dict[str, str]] = []
        results: list[dict[str, str]] = []
        with self.store.transaction() as connection:
            for action in plan.actions:
                if action["action"] == "RESTORE_DOWNSTREAM_CONTENT_AS_PENDING_REBASE":
                    object_id = str(action["object_id"])
                    current = self.store.get_object(object_id)
                    baseline_version = str(action["restore_from_ref"]).rsplit("@", 1)[1]
                    baseline = self.store.get_object(object_id, baseline_version)
                    self.store.transition_current(connection, object_id, ObjectState.SUPERSEDED)
                    compensated = VersionedObject(
                        id=baseline.id,
                        version=str(action["next_version"]),
                        kind=baseline.kind,
                        label=baseline.label,
                        domain=baseline.domain,
                        state=ObjectState.ROLLED_BACK_PENDING_REBASE,
                        payload={
                            **baseline.payload,
                            "compensates": current.ref,
                            "rollback_plan": plan.digest,
                            "authoritative_claim_ref": plan.authoritative_claim_ref,
                            "pending_rebase_reason": (
                                "DOWNSTREAM_CONTENT_RESTORED_BUT_AUTHORITY_UNCHANGED"
                            ),
                        },
                        source_refs=baseline.source_refs,
                        valid_from="2026-08-14T00:07:00Z",
                        sensitivity=baseline.sensitivity,
                        allowed_purposes=baseline.allowed_purposes,
                        coverage_complete=False,
                        coverage_basis=(CoverageBasis.RUNTIME_OBSERVED,),
                    )
                    self.store.insert_version(connection, compensated, make_current=True)
                    transitions.append(
                        {
                            "object_id": object_id,
                            "from": current.ref,
                            "to": compensated.ref,
                            "to_state": ObjectState.ROLLED_BACK_PENDING_REBASE.value,
                        }
                    )
                    results.append(
                        {
                            "action": str(action["action"]),
                            "object_id": object_id,
                            "status": "SUCCEEDED",
                        }
                    )
                elif action["action"] == "QUARANTINE_CANARY_AND_REQUIRE_REQUALIFICATION":
                    current_version = str(action["current_ref"]).rsplit("@", 1)[1]
                    restore_version = str(action["restore_pointer_ref"]).rsplit("@", 1)[1]
                    self.store.activate_version(
                        connection,
                        str(action["object_id"]),
                        current_version,
                        restore_version,
                        base_state=ObjectState.QUARANTINED,
                        proposed_state=ObjectState.REQUALIFICATION_REQUIRED,
                    )
                    transitions.append(
                        {
                            "object_id": str(action["object_id"]),
                            "from": str(action["current_ref"]),
                            "to": str(action["restore_pointer_ref"]),
                            "to_state": ObjectState.REQUALIFICATION_REQUIRED.value,
                        }
                    )
                    results.append(
                        {
                            "action": str(action["action"]),
                            "object_id": str(action["object_id"]),
                            "status": "SUCCEEDED",
                        }
                    )
                else:  # pragma: no cover - closed action vocabulary
                    raise RuntimeError(f"UNKNOWN_COMPENSATION_ACTION:{action['action']}")

            authoritative_during = self.store.get_object(claim_id)
            if authoritative_during.ref != plan.authoritative_claim_ref:
                raise RuntimeError("AUTHORITATIVE_CLAIM_MUTATED_DURING_ROLLBACK")
            event_head = self.store.append_event(
                connection,
                "DOWNSTREAM_EFFECTS_COMPENSATED",
                {
                    "rebase_receipt_digest": rebase_receipt.digest,
                    "rollback_plan_digest": plan.digest,
                    "approval_digest": approval.digest,
                    "authoritative_claim_ref": plan.authoritative_claim_ref,
                    "authoritative_claim_unchanged": True,
                    "transition_count": len(transitions),
                    "target_status": plan.target_status,
                },
            )
            receipt = RollbackReceipt(
                id="rollback:launch-date@1",
                status=plan.target_status,
                compensates_rebase_receipt=rebase_receipt.digest,
                workflow_run_id=rebase_receipt.workflow_run_id,
                run_nonce=rebase_receipt.run_nonce,
                rollback_plan_digest=plan.digest,
                approval_ref=approval.id,
                apply_approval_ref=rebase_receipt.approval_ref,
                authoritative_claim_ref=plan.authoritative_claim_ref,
                authoritative_claim_unchanged=True,
                transitions=tuple(transitions),
                preserved_states=plan.preserved_states,
                compensation_results=tuple(results),
                metrics={
                    "authoritative_claims_changed": 0,
                    "work_items_compensated": sum(
                        1
                        for item in results
                        if item["action"] == "RESTORE_DOWNSTREAM_CONTENT_AS_PENDING_REBASE"
                    ),
                    "work_items_pending_rebase": sum(
                        1
                        for item in results
                        if item["action"] == "RESTORE_DOWNSTREAM_CONTENT_AS_PENDING_REBASE"
                    ),
                    "unknown_items_preserved": len(rebase_receipt.unknown),
                    "skills_quarantined": sum(
                        1
                        for item in results
                        if item["action"] == "QUARANTINE_CANARY_AND_REQUIRE_REQUALIFICATION"
                    ),
                    "history_rows_deleted": 0,
                },
                event_chain_head=event_head,
                evidence_class=EvidenceClass.LOCAL_DETERMINISTIC,
            )
            self.store.save_artifact(
                connection,
                receipt.id,
                "application/vnd.orgrebase.rollback-receipt+json",
                receipt.model_dump(mode="json"),
            )
            self.store.save_idempotent(
                connection,
                idempotency_key,
                request_digest,
                receipt.model_dump(mode="json"),
            )
        return receipt

    @staticmethod
    def _validate_bindings(
        receipt: RebaseReceipt,
        plan: RollbackPlan,
        approval: RollbackApproval,
    ) -> None:
        if plan.rebase_receipt_digest != receipt.digest:
            raise FreshnessError("rollback plan is not bound to the exact RebaseReceipt")
        if (
            plan.workflow_run_id != receipt.workflow_run_id
            or plan.run_nonce != receipt.run_nonce
        ):
            raise FreshnessError("rollback plan crosses workflow run boundaries")
        if approval.rebase_receipt_digest != receipt.digest:
            raise FreshnessError("rollback approval is not bound to the exact RebaseReceipt")
        if (
            approval.workflow_run_id != receipt.workflow_run_id
            or approval.run_nonce != receipt.run_nonce
        ):
            raise FreshnessError("rollback approval crosses workflow run boundaries")
        if approval.rollback_plan_digest != plan.digest:
            raise FreshnessError("rollback approval is not bound to the exact RollbackPlan")
        if approval.apply_approval_digest != receipt.approval_digest:
            raise FreshnessError("rollback approval references a different Apply approval")
        if approval.apply_approver_id != receipt.approval_actor_id:
            raise FreshnessError("Apply approver identity changed after rollback approval")
        if approval.actor_id == receipt.approval_actor_id:
            raise AuthorizationError("rollback approver must be independent from Apply approver")
        if ROLLBACK_SCOPE not in approval.authority_scope:
            raise AuthorizationError("rollback approver lacks downstream compensation authority")

    @staticmethod
    def _successor_version(version: str) -> str:
        if version.startswith("v") and version[1:].isdigit():
            return f"v{int(version[1:]) + 1}"
        raise RuntimeError(f"UNSUPPORTED_VERSION_SCHEME:{version}")

    def _assert_expected_state(self, plan: RollbackPlan) -> None:
        actual: dict[str, dict[str, str]] = {}
        for object_id in plan.expected_current_state:
            current = self.store.get_object(object_id)
            actual[object_id] = {
                "ref": current.ref,
                "state": current.state.value,
                "digest": current.digest,
            }
        if actual != plan.expected_current_state:
            raise FreshnessError("rollback target state drifted from the approved RollbackPlan")
