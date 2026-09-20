"""Generic bundle-driven zero-effect executor; no case-name or Plan-digest dispatch."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.store import StateStore
from orgrebase.workspace.evolution_contracts import (
    ExecutedHandler,
    ExecutionEvidence,
    ExecutionStatus,
    ExecutionStepReceipt,
    OACExecutionApproval,
    OACExecutionReceipt,
)
from orgrebase.workspace.models import OACRuntimeCapsule, RuntimeAdmissionReceipt
from orgrebase.workspace.oac_admission import OACRuntimeAdmissionVerifier
from orgrebase.workspace.oac_wire import (
    DEFAULT_POLICY_PATH,
    _jcs_digest,
    _oac_projection,
    _resource_ref,
    load_runtime_policy,
)

EXECUTION_RECEIPT_MEDIA_TYPE = "application/vnd.orgrebase.oac-execution-receipt+json"
EXECUTION_EVIDENCE_MEDIA_TYPE = "application/vnd.orgrebase.oac-execution-evidence+json"
EXECUTOR_ID = "runtime:orgrebase-zero-effect"
EXECUTOR_BUILD = "orgrebase.oac-zero-effect/v1"


def _handler_digest(obligation_type: str) -> str:
    return "sha256:" + hashlib.sha256(f"handler:{obligation_type}".encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class HandlerDefinition:
    obligation_type: str
    assertion: str
    runtime_evidence_value: str
    program_version: str = "orgrebase.zero-effect-handler/v1"

    @property
    def handler_ref(self) -> str:
        return f"urn:oac:zero-effect-handler:{self.obligation_type}"

    @property
    def handler_digest(self) -> str:
        return _handler_digest(self.obligation_type)

    @property
    def program_digest(self) -> str:
        return sha256_digest(
            {
                "program_version": self.program_version,
                "obligation_type": self.obligation_type,
                "assertion": self.assertion,
                "runtime_evidence_value": self.runtime_evidence_value,
                "external_effects": "NONE",
                "target_writes": 0,
            }
        )


_HANDLER_DEFINITIONS = tuple(
    HandlerDefinition(*values)
    for values in (
        (
            "assess_dependency_impact",
            "dependency_impact_assessed",
            "zero_effect_handler_completed",
        ),
        (
            "commercial-switch-review",
            "commercial_switch_reviewed",
            "zero_effect_handler_completed",
        ),
        (
            "continuity-option-selection",
            "continuity_option_selected",
            "zero_effect_handler_completed",
        ),
        (
            "qualification-evidence-check",
            "qualification_evidence_checked",
            "zero_effect_handler_completed",
        ),
    )
)
HANDLER_REGISTRY: dict[str, HandlerDefinition] = {item.handler_ref: item for item in _HANDLER_DEFINITIONS}


def program_policy_digest() -> str:
    return sha256_digest(
        [
            {"handler_ref": ref, "program_digest": item.program_digest}
            for ref, item in sorted(HANDLER_REGISTRY.items())
        ]
    )


def _dict(value: object, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise IntegrityError(code)
    return cast(dict[str, Any], value)


def _list(value: object, code: str) -> list[Any]:
    if not isinstance(value, list):
        raise IntegrityError(code)
    return value


def _str(value: object, code: str) -> str:
    if not isinstance(value, str) or not value:
        raise IntegrityError(code)
    return value


def _sorted_pairs(values: list[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(values, key=lambda item: item[0].encode()))


def _resource_identity(resource: Mapping[str, Any]) -> tuple[str, str]:
    metadata = _dict(resource.get("metadata"), "OAC_EXECUTION_METADATA_INVALID")
    return (
        _str(metadata.get("id"), "OAC_EXECUTION_RESOURCE_ID_INVALID"),
        _str(resource.get("digest"), "OAC_EXECUTION_RESOURCE_DIGEST_INVALID"),
    )


class ZeroEffectOACExecutor:
    def __init__(
        self,
        store: StateStore,
        *,
        policy_path: str | Path = DEFAULT_POLICY_PATH,
    ) -> None:
        self.store = store
        policy, policy_digest = load_runtime_policy(policy_path)
        self._admission_verifier = OACRuntimeAdmissionVerifier(policy, policy_digest)

    def _verify_admission(
        self,
        capsule: OACRuntimeCapsule,
        admission: RuntimeAdmissionReceipt,
    ) -> dict[str, Any]:
        capsule = capsule.revalidated()
        admission = admission.revalidated()
        verification = self._admission_verifier.verify(capsule)
        expected = {
            "capsule_digest": capsule.digest,
            "plan_digest": capsule.plan["digest"],
            "certificate_digest": capsule.plan_certificate["digest"],
            "runtime_binding_digest": capsule.runtime_binding["digest"],
            "runtime_bundle_digest": capsule.runtime_bundle["digest"],
            "lowering_receipt_digest": capsule.runtime_lowering_receipt["digest"],
        }
        for field, value in expected.items():
            if getattr(admission, field) != value:
                raise IntegrityError(f"OAC_EXECUTION_ADMISSION_MISMATCH:{field}")
        if admission.status != "LOCAL_RUNTIME_ADMISSION_PASS":
            raise IntegrityError("OAC_EXECUTION_RUNTIME_NOT_ADMITTED")
        if admission.handler_execution != "NOT_RUN":
            raise IntegrityError("OAC_EXECUTION_ADMISSION_ALREADY_EXECUTED")
        return verification

    @staticmethod
    def _verify_execution_approval(
        capsule: OACRuntimeCapsule,
        source_admission_ref: str,
        source_admission_digest: str,
        demand_id: str,
        demand_digest: str,
        admission: RuntimeAdmissionReceipt,
        approval: object,
    ) -> OACExecutionApproval:
        try:
            payload = approval.model_dump(mode="json")  # type: ignore[attr-defined]
            approval = OACExecutionApproval.model_validate(payload)
        except (AttributeError, TypeError, ValueError) as exc:
            raise IntegrityError("OAC_EXECUTION_APPROVAL_MODEL_INVALID") from exc
        expected = {
            "capsule_digest": capsule.digest,
            "runtime_admission_digest": admission.digest,
            "source_admission_ref": source_admission_ref,
            "source_admission_digest": source_admission_digest,
            "demand_ref": demand_id,
            "demand_digest": demand_digest,
            "runtime_bundle_digest": capsule.runtime_bundle["digest"],
            "program_policy_digest": program_policy_digest(),
        }
        for field, value in expected.items():
            if getattr(approval, field) != value:
                raise IntegrityError(f"OAC_EXECUTION_APPROVAL_MISMATCH:{field}")
        return approval

    @staticmethod
    def _verify_source_admission(
        capsule: OACRuntimeCapsule,
        source_admission: Mapping[str, Any],
    ) -> tuple[str, str]:
        if source_admission.get("apiVersion") != "oac.dev/v0alpha1" or (
            source_admission.get("kind") != "SourceAdmissionReceipt"
        ):
            raise IntegrityError("OAC_EXECUTION_SOURCE_ADMISSION_KIND_INVALID")
        source_ref, source_digest = _resource_identity(source_admission)
        if source_digest != _jcs_digest(_oac_projection(dict(source_admission))):
            raise IntegrityError("OAC_EXECUTION_SOURCE_ADMISSION_DIGEST_INVALID")
        spec = _dict(
            source_admission.get("spec"),
            "OAC_EXECUTION_SOURCE_ADMISSION_SPEC_INVALID",
        )
        if spec.get("verdict") != "ADMITTED" or spec.get("unresolvedRefs") != []:
            raise IntegrityError("OAC_EXECUTION_SOURCE_NOT_ADMITTED")
        admitted = _list(
            spec.get("admittedSubjectRefs"),
            "OAC_EXECUTION_ADMITTED_SUBJECTS_INVALID",
        )
        if _resource_ref(capsule.snapshot) not in admitted:
            raise IntegrityError("OAC_EXECUTION_SOURCE_SNAPSHOT_MISMATCH")
        return source_ref, source_digest

    @staticmethod
    def _verify_demand(capsule: OACRuntimeCapsule, demand: Mapping[str, Any]) -> tuple[str, str]:
        if demand.get("apiVersion") != "oac.dev/v0alpha1" or demand.get("kind") != ("OrganizationalDemand"):
            raise IntegrityError("OAC_EXECUTION_DEMAND_KIND_INVALID")
        demand_id, demand_digest = _resource_identity(demand)
        spec = _dict(demand.get("spec"), "OAC_EXECUTION_DEMAND_SPEC_INVALID")
        snapshot_ref = _dict(spec.get("snapshotRef"), "OAC_EXECUTION_DEMAND_SNAPSHOT_MISSING")
        if snapshot_ref != _resource_ref(capsule.snapshot):
            raise IntegrityError("OAC_EXECUTION_DEMAND_SNAPSHOT_MISMATCH")
        subject_refs = _list(spec.get("subjectRefs"), "OAC_EXECUTION_DEMAND_SUBJECTS_MISSING")
        subject_ids = {
            _str(
                _dict(item, "OAC_EXECUTION_DEMAND_SUBJECT_INVALID").get("resourceId"),
                "OAC_EXECUTION_DEMAND_SUBJECT_INVALID",
            )
            for item in subject_refs
        }
        if capsule.change["spec"]["subjectRef"] not in subject_ids:
            raise IntegrityError("OAC_EXECUTION_DEMAND_SUBJECT_MISMATCH")
        trigger_refs = _list(spec.get("triggerRefs"), "OAC_EXECUTION_DEMAND_TRIGGERS_MISSING")
        if _resource_ref(capsule.change) not in trigger_refs:
            raise IntegrityError("OAC_EXECUTION_DEMAND_CHANGE_MISMATCH")
        if spec.get("effectCeiling") != "zero_effect":
            raise IntegrityError("OAC_EXECUTION_DEMAND_EFFECT_EXPANSION")
        if capsule.change["spec"].get("demandRef") != demand_id:
            raise IntegrityError("OAC_EXECUTION_CHANGE_DEMAND_MISMATCH")
        return demand_id, demand_digest

    @staticmethod
    def _evidence(
        *,
        run_id: str,
        subject_ref: str,
        plan_digest: str,
        step_index: int,
        work_unit_ref: str,
        obligation_ref: str,
        obligation_type: str,
        handler_ref: str,
        handler_digest: str,
        program_digest: str,
        evidence_type: str,
        observed_value: str,
        completed_at: str,
    ) -> ExecutionEvidence:
        suffix = hashlib.sha256(f"{work_unit_ref}\0{obligation_ref}\0{evidence_type}".encode()).hexdigest()[
            :20
        ]
        return ExecutionEvidence(
            evidence_id=f"execution-evidence:{run_id}:{step_index}:{suffix}",
            evidence_type=evidence_type,
            obligation_ref=obligation_ref,
            obligation_type=obligation_type,
            work_unit_ref=work_unit_ref,
            subject_ref=subject_ref,
            plan_digest=plan_digest,
            handler_ref=handler_ref,
            handler_digest=handler_digest,
            program_digest=program_digest,
            assertion=HANDLER_REGISTRY[handler_ref].assertion,
            observed_value=observed_value,
            source_refs=tuple(sorted({f"plan:{plan_digest}", obligation_ref, work_unit_ref}, key=str.encode)),
            producer_id=EXECUTOR_ID,
            produced_at=completed_at,
        )

    def _execute_fresh(
        self,
        capsule: OACRuntimeCapsule,
        demand: Mapping[str, Any],
        admission: RuntimeAdmissionReceipt,
        execution_approval: OACExecutionApproval,
        *,
        source_admission: Mapping[str, Any],
        run_id: str,
        started_at: str,
        completed_at: str,
    ) -> tuple[OACExecutionReceipt, tuple[ExecutionEvidence, ...]]:
        verification = self._verify_admission(capsule, admission)
        source_admission_ref, source_admission_digest = self._verify_source_admission(
            capsule,
            source_admission,
        )
        demand_id, demand_digest = self._verify_demand(capsule, demand)
        execution_approval = self._verify_execution_approval(
            capsule,
            source_admission_ref,
            source_admission_digest,
            demand_id,
            demand_digest,
            admission,
            execution_approval,
        )
        bundle_spec = _dict(capsule.runtime_bundle.get("spec"), "OAC_EXECUTION_BUNDLE_INVALID")
        binding_spec = _dict(capsule.runtime_binding.get("spec"), "OAC_EXECUTION_BINDING_INVALID")
        declared_handlers = {
            _str(item.get("handlerRef"), "OAC_EXECUTION_HANDLER_REF_INVALID"): _dict(
                item, "OAC_EXECUTION_HANDLER_INVALID"
            )
            for raw in _list(binding_spec.get("handlerBindings"), "OAC_EXECUTION_HANDLERS_INVALID")
            for item in (_dict(raw, "OAC_EXECUTION_HANDLER_INVALID"),)
        }
        evidence: list[ExecutionEvidence] = []
        step_receipts: list[ExecutionStepReceipt] = []
        completed_work: set[str] = set()
        subject_ref = _str(capsule.change["spec"].get("subjectRef"), "OAC_EXECUTION_SUBJECT_INVALID")
        plan_id, plan_digest = _resource_identity(capsule.plan)
        for index, raw_step in enumerate(_list(bundle_spec.get("steps"), "OAC_EXECUTION_STEPS_INVALID")):
            step = _dict(raw_step, "OAC_EXECUTION_STEP_INVALID")
            if step.get("stepIndex") != index:
                raise IntegrityError("OAC_EXECUTION_STEP_INDEX_INVALID")
            work_unit_ref = _str(step.get("workUnitRef"), "OAC_EXECUTION_WORK_UNIT_INVALID")
            predecessors = tuple(
                sorted(
                    (
                        _str(value, "OAC_EXECUTION_PREDECESSOR_INVALID")
                        for value in _list(
                            step.get("predecessorRefs"),
                            "OAC_EXECUTION_PREDECESSORS_INVALID",
                        )
                    ),
                    key=str.encode,
                )
            )
            if not set(predecessors).issubset(completed_work):
                raise IntegrityError("OAC_EXECUTION_PREDECESSOR_NOT_COMPLETED")
            executed_handlers: list[ExecutedHandler] = []
            step_evidence: list[tuple[str, str]] = []
            for raw_handler in _list(step.get("handlerBindings"), "OAC_EXECUTION_STEP_HANDLERS_INVALID"):
                handler = _dict(raw_handler, "OAC_EXECUTION_STEP_HANDLER_INVALID")
                handler_ref = _str(handler.get("handlerRef"), "OAC_EXECUTION_HANDLER_REF_INVALID")
                definition = HANDLER_REGISTRY.get(handler_ref)
                if definition is None:
                    raise IntegrityError("OAC_EXECUTION_HANDLER_NOT_REGISTERED")
                declared = declared_handlers.get(handler_ref)
                if declared is None or declared != {
                    key: value for key, value in handler.items() if key != "obligationRef"
                }:
                    raise IntegrityError("OAC_EXECUTION_HANDLER_BINDING_MISMATCH")
                if (
                    handler.get("handlerDigest") != definition.handler_digest
                    or handler.get("obligationType") != definition.obligation_type
                    or handler.get("effectCeiling") != "zero_effect"
                ):
                    raise IntegrityError("OAC_EXECUTION_HANDLER_CONTRACT_INVALID")
                obligation_ref = _str(handler.get("obligationRef"), "OAC_EXECUTION_OBLIGATION_REF_INVALID")
                evidence_pairs: list[tuple[str, str]] = []
                for evidence_type in _list(
                    handler.get("evidenceOutputRefs"),
                    "OAC_EXECUTION_EVIDENCE_TYPES_INVALID",
                ):
                    evidence_type = _str(evidence_type, "OAC_EXECUTION_EVIDENCE_TYPE_INVALID")
                    item = self._evidence(
                        run_id=run_id,
                        subject_ref=subject_ref,
                        plan_digest=plan_digest,
                        step_index=index,
                        work_unit_ref=work_unit_ref,
                        obligation_ref=obligation_ref,
                        obligation_type=definition.obligation_type,
                        handler_ref=handler_ref,
                        handler_digest=definition.handler_digest,
                        program_digest=definition.program_digest,
                        evidence_type=evidence_type,
                        observed_value=definition.runtime_evidence_value,
                        completed_at=completed_at,
                    )
                    evidence.append(item)
                    evidence_pairs.append((item.evidence_id, item.digest))
                    step_evidence.append((item.evidence_id, item.digest))
                bound = _sorted_pairs(evidence_pairs)
                executed_handlers.append(
                    ExecutedHandler(
                        obligation_ref=obligation_ref,
                        obligation_type=definition.obligation_type,
                        handler_ref=handler_ref,
                        handler_digest=definition.handler_digest,
                        program_digest=definition.program_digest,
                        capability_ref=_str(
                            handler.get("capabilityRef"),
                            "OAC_EXECUTION_CAPABILITY_INVALID",
                        ),
                        evidence_refs=tuple(ref for ref, _ in bound),
                        evidence_digests=tuple(digest for _, digest in bound),
                    )
                )
            step_bound = _sorted_pairs(step_evidence)
            step_receipts.append(
                ExecutionStepReceipt(
                    step_index=index,
                    work_unit_ref=work_unit_ref,
                    predecessor_refs=predecessors,
                    handlers=tuple(executed_handlers),
                    evidence_refs=tuple(ref for ref, _ in step_bound),
                    status=ExecutionStatus.COMPLETED,
                    started_at=started_at,
                    completed_at=completed_at,
                )
            )
            completed_work.add(work_unit_ref)
        all_bound = _sorted_pairs([(item.evidence_id, item.digest) for item in evidence])
        snapshot_id, snapshot_digest = _resource_identity(capsule.snapshot)
        change_id, change_digest = _resource_identity(capsule.change)
        root_closure = sha256_digest(
            {
                "schema_version": "orgrebase.oac-execution-root-closure.v1",
                "snapshot": _resource_ref(capsule.snapshot),
                "sourceAdmission": _resource_ref(source_admission),
                "demand": {"resourceId": demand_id, "digest": demand_digest},
                "change": _resource_ref(capsule.change),
                "plan": _resource_ref(capsule.plan),
                "certificate": _resource_ref(capsule.plan_certificate),
                "binding": _resource_ref(capsule.runtime_binding),
                "bundle": _resource_ref(capsule.runtime_bundle),
                "runtime_admission_digest": admission.digest,
            }
        )
        receipt = OACExecutionReceipt(
            receipt_id=f"execution-receipt:{run_id}",
            run_id=run_id,
            snapshot_ref=snapshot_id,
            snapshot_digest=snapshot_digest,
            source_admission_ref=source_admission_ref,
            source_admission_digest=source_admission_digest,
            demand_ref=demand_id,
            demand_digest=demand_digest,
            change_ref=change_id,
            change_digest=change_digest,
            plan_ref=plan_id,
            plan_digest=plan_digest,
            certificate_digest=_resource_identity(capsule.plan_certificate)[1],
            runtime_binding_digest=_resource_identity(capsule.runtime_binding)[1],
            runtime_bundle_digest=_resource_identity(capsule.runtime_bundle)[1],
            root_closure_digest=root_closure,
            obligation_contract_digest=_str(
                verification.get("obligation_contract_digest"),
                "OAC_EXECUTION_OBLIGATION_CONTRACT_MISSING",
            ),
            evidence_projection_digest=sha256_digest(
                [{"ref": ref, "digest": digest} for ref, digest in all_bound]
            ),
            runtime_owner_id=execution_approval.runtime_owner_id,
            executor_id=EXECUTOR_ID,
            executor_build=EXECUTOR_BUILD,
            status=ExecutionStatus.COMPLETED,
            steps=tuple(step_receipts),
            evidence_refs=tuple(ref for ref, _ in all_bound),
            evidence_digests=tuple(digest for _, digest in all_bound),
            started_at=started_at,
            completed_at=completed_at,
        )
        return receipt, tuple(evidence)

    def execute(
        self,
        capsule: OACRuntimeCapsule,
        demand: Mapping[str, Any],
        admission: RuntimeAdmissionReceipt,
        execution_approval: OACExecutionApproval,
        *,
        source_admission: Mapping[str, Any],
        run_id: str,
        started_at: str = "2026-08-26T00:01:00Z",
        completed_at: str = "2026-08-26T00:01:01Z",
    ) -> tuple[OACExecutionReceipt, tuple[ExecutionEvidence, ...]]:
        source_admission_ref, source_admission_digest = self._verify_source_admission(
            capsule,
            source_admission,
        )
        request_digest = sha256_digest(
            {
                "capsule_digest": capsule.digest,
                "demand_digest": demand.get("digest"),
                "admission_digest": admission.digest,
                "execution_approval_digest": execution_approval.digest,
                "source_admission_ref": source_admission_ref,
                "source_admission_digest": source_admission_digest,
                "run_id": run_id,
                "started_at": started_at,
                "completed_at": completed_at,
            }
        )
        key = f"oac-execution:{run_id}"
        existing = self.store.get_idempotent(key, request_digest)
        if existing is not None:
            receipt = OACExecutionReceipt.model_validate(existing["receipt"])
            evidence = tuple(ExecutionEvidence.model_validate(item) for item in existing["evidence"])
            return receipt, evidence
        receipt, evidence = self._execute_fresh(
            capsule,
            demand,
            admission,
            execution_approval,
            source_admission=source_admission,
            run_id=run_id,
            started_at=started_at,
            completed_at=completed_at,
        )
        result = {
            "receipt": receipt.model_dump(mode="json"),
            "evidence": [item.model_dump(mode="json") for item in evidence],
        }
        with self.store.transaction() as connection:
            for item in evidence:
                self.store.save_artifact(
                    connection,
                    item.evidence_id,
                    EXECUTION_EVIDENCE_MEDIA_TYPE,
                    item.model_dump(mode="json"),
                )
            self.store.save_artifact(
                connection,
                receipt.receipt_id,
                EXECUTION_RECEIPT_MEDIA_TYPE,
                receipt.model_dump(mode="json"),
            )
            self.store.append_event(
                connection,
                "OAC_EXECUTION_COMPLETED",
                {
                    "receipt_ref": receipt.receipt_id,
                    "receipt_digest": receipt.digest,
                    "plan_digest": receipt.plan_digest,
                    "target_writes": 0,
                },
            )
            self.store.save_idempotent(connection, key, request_digest, result)
        return receipt, evidence
