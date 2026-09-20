"""OrgRebase-owned policy, approval, and local Formation admission."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError, ObjectState, VersionedObject
from orgrebase.store import StateStore
from orgrebase.workspace.models import (
    OACFormationControlRecord,
    OACFormationPreview,
    OACRuntimeApproval,
    OACRuntimeCapsule,
    RuntimeAdmissionReceipt,
)
from orgrebase.workspace.oac_wire import (
    _CHECKS,
    _OAC_PUBLIC_SOURCE_PATHS,
    _OAC_SOURCE_MANIFEST_SCHEMA,
    _SHA256_PATTERN,
    _ZERO_EFFECT,
    APPROVAL_MEDIA_TYPE,
    CAPSULE_MEDIA_TYPE,
    DEFAULT_POLICY_PATH,
    FORMATION_MEDIA_TYPE,
    PREVIEW_MEDIA_TYPE,
    RECEIPT_MEDIA_TYPE,
    _dict,
    _list,
    _obligation_contract_digest,
    _require_ref,
    _require_sources,
    _sorted,
    _str,
    _topological_order,
    _topology_digest,
    _verify_oac_resource,
    load_runtime_policy,
)


def _revalidate_model(value: Any, model_type: Any, error_code: str) -> Any:
    """Deeply reparse a content-addressed command object at a trust boundary."""

    try:
        return model_type.model_validate(value.model_dump(mode="json"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise IntegrityError(error_code) from exc


class OACRuntimeAdmissionVerifier:
    """Independent, fail-closed verifier over the public OAC JSON capsule."""

    def __init__(self, policy: Mapping[str, Any], policy_digest: str) -> None:
        self.policy = dict(policy)
        self.policy_digest = policy_digest

    def runtime_owner(self, namespace: str) -> str:
        mapping = _dict(self.policy.get("approver_by_namespace"), "OAC_POLICY_APPROVERS_INVALID")
        return _str(mapping.get(namespace), "OAC_POLICY_APPROVER_NOT_ADMITTED")

    def _verify_public_source(self, capsule: OACRuntimeCapsule) -> None:
        manifest = _dict(
            capsule.oac_source_manifest,
            "OAC_PUBLIC_SOURCE_MANIFEST_INVALID",
        )
        if set(manifest) != {"schema_version", "files"} or manifest.get(
            "schema_version"
        ) != _OAC_SOURCE_MANIFEST_SCHEMA:
            raise IntegrityError("OAC_PUBLIC_SOURCE_MANIFEST_INVALID")
        files = _list(manifest.get("files"), "OAC_PUBLIC_SOURCE_MANIFEST_INVALID")
        if len(files) != len(_OAC_PUBLIC_SOURCE_PATHS):
            raise IntegrityError("OAC_PUBLIC_SOURCE_MANIFEST_INVALID")
        for expected_path, raw_entry in zip(_OAC_PUBLIC_SOURCE_PATHS, files, strict=True):
            entry = _dict(raw_entry, "OAC_PUBLIC_SOURCE_MANIFEST_INVALID")
            if (
                set(entry) != {"path", "sha256", "size"}
                or entry.get("path") != expected_path
                or _SHA256_PATTERN.fullmatch(str(entry.get("sha256"))) is None
                or not isinstance(entry.get("size"), int)
                or entry["size"] < 0
            ):
                raise IntegrityError("OAC_PUBLIC_SOURCE_MANIFEST_INVALID")
        fingerprint = sha256_digest(manifest)
        allowed_fingerprints = _dict(
            self.policy.get("allowed_oac_source_fingerprints"),
            "OAC_PUBLIC_SOURCE_POLICY_INVALID",
        )
        if (
            capsule.oac_source_fingerprint != fingerprint
            or allowed_fingerprints.get(capsule.oac_cli_version) != fingerprint
        ):
            raise IntegrityError("OAC_PUBLIC_SOURCE_FINGERPRINT_DENIED")
        commitments = _dict(
            self.policy.get("plan_commitments_by_case"),
            "OAC_PLAN_SOURCE_POLICY_INVALID",
        )
        commitment = _dict(
            commitments.get(capsule.case_id),
            "OAC_PLAN_SOURCE_NOT_ADMITTED",
        )
        if commitment != {
            "plan_source": capsule.plan_source,
            "plan_digest": capsule.plan.get("digest"),
        }:
            raise IntegrityError("OAC_PLAN_SOURCE_COMMITMENT_MISMATCH")

    def verify(self, capsule: OACRuntimeCapsule) -> dict[str, Any]:
        capsule = _revalidate_model(
            capsule,
            OACRuntimeCapsule,
            "OAC_RUNTIME_CAPSULE_MODEL_INVALID",
        )
        resources = (
            (capsule.snapshot, "OrganizationSnapshot"),
            (capsule.change, "SemanticChangeSet"),
            (capsule.plan, "OrganizationPlan"),
            (capsule.plan_certificate, "PlanCertificate"),
            (capsule.runtime_binding, "RuntimeBinding"),
            (capsule.runtime_bundle, "ZeroEffectRuntimeBundle"),
            (capsule.runtime_lowering_receipt, "RuntimeLoweringReceipt"),
        )
        for resource, kind in resources:
            _verify_oac_resource(resource, kind)
        snapshot = capsule.snapshot
        change = capsule.change
        plan = capsule.plan
        certificate = capsule.plan_certificate
        binding = capsule.runtime_binding
        bundle = capsule.runtime_bundle
        receipt = capsule.runtime_lowering_receipt
        metadata = [_dict(item.get("metadata"), "OAC_METADATA_INVALID") for item, _ in resources]
        namespace = _str(metadata[0].get("namespace"), "OAC_NAMESPACE_INVALID")
        if any(item.get("namespace") != namespace for item in metadata):
            raise IntegrityError("OAC_NAMESPACE_MISMATCH")
        if namespace not in self.policy["allowed_namespaces"]:
            raise IntegrityError("OAC_RUNTIME_POLICY_NAMESPACE_DENIED")
        governance = metadata[0].get("governanceRef")
        if (
            any(item.get("governanceRef") != governance for item in metadata)
            or governance not in self.policy["allowed_governance_refs"]
        ):
            raise IntegrityError("OAC_RUNTIME_POLICY_GOVERNANCE_DENIED")

        plan_spec = _dict(plan.get("spec"), "OAC_PLAN_SPEC_INVALID")
        certificate_spec = _dict(certificate.get("spec"), "OAC_CERTIFICATE_SPEC_INVALID")
        binding_spec = _dict(binding.get("spec"), "OAC_BINDING_SPEC_INVALID")
        bundle_spec = _dict(bundle.get("spec"), "OAC_BUNDLE_SPEC_INVALID")
        receipt_spec = _dict(receipt.get("spec"), "OAC_LOWERING_RECEIPT_SPEC_INVALID")
        _require_ref(plan_spec.get("snapshotRef"), snapshot, "plan.snapshot")
        _require_ref(plan_spec.get("changeRef"), change, "plan.change")
        _require_sources(plan, (snapshot, change))
        _require_ref(certificate_spec.get("subjectPlanRef"), plan, "certificate.plan")
        _require_ref(certificate_spec.get("snapshotRef"), snapshot, "certificate.snapshot")
        _require_ref(certificate_spec.get("changeRef"), change, "certificate.change")
        _require_sources(certificate, (plan, snapshot, change))
        _require_ref(binding_spec.get("subjectPlanRef"), plan, "binding.plan")
        _require_ref(binding_spec.get("subjectCertificateRef"), certificate, "binding.certificate")
        _require_sources(binding, (plan, certificate))
        for field, resource in (
            ("snapshotRef", snapshot),
            ("changeRef", change),
            ("subjectPlanRef", plan),
            ("certificateRef", certificate),
            ("runtimeBindingRef", binding),
        ):
            _require_ref(bundle_spec.get(field), resource, f"bundle.{field}")
        _require_sources(bundle, (snapshot, change, plan, certificate, binding))
        for field, resource in (
            ("snapshotRef", snapshot),
            ("changeRef", change),
            ("subjectPlanRef", plan),
            ("suppliedCertificateRef", certificate),
            ("recomputedCertificateRef", certificate),
            ("runtimeBindingRef", binding),
            ("bundleRef", bundle),
        ):
            _require_ref(receipt_spec.get(field), resource, f"receipt.{field}")
        _require_sources(receipt, (snapshot, change, plan, certificate, certificate, binding))
        self._verify_public_source(capsule)

        if (
            plan_spec.get("status") != "planned"
            or plan_spec.get("effectCeiling") != _ZERO_EFFECT
            or certificate_spec.get("verdict") != "ACCEPT"
            or certificate_spec.get("reasonCodes") != []
            or certificate_spec.get("restrictions") != []
            or certificate_spec.get("unresolvedRefs") != []
        ):
            raise IntegrityError("OAC_ACCEPT_CERTIFICATE_INVALID")
        if (
            receipt_spec.get("status") != "PRODUCED"
            or receipt_spec.get("reasonCodes") != []
            or receipt_spec.get("proofScope") != "lowering_only"
            or receipt_spec.get("runtimeInvoked") is not False
            or receipt_spec.get("runtimeAdmissionPerformed") is not False
        ):
            raise IntegrityError("OAC_LOWERING_STATUS_INVALID")
        if (
            binding_spec.get("effectCeiling") != _ZERO_EFFECT
            or binding_spec.get("targetWrites") != 0
            or bundle_spec.get("effectCeiling") != _ZERO_EFFECT
            or bundle_spec.get("targetWrites") != 0
            or receipt_spec.get("targetWrites") != 0
            or bundle_spec.get("requiresRuntimeAdmission") is not True
        ):
            raise IntegrityError("OAC_ZERO_EFFECT_BOUNDARY_INVALID")
        if bundle_spec.get("loweringProfile") not in self.policy["allowed_lowering_profiles"]:
            raise IntegrityError("OAC_RUNTIME_POLICY_LOWERING_PROFILE_DENIED")

        obligations = {
            _str(item.get("obligationId"), "OAC_OBLIGATION_INVALID"): item
            for item in (
                _dict(raw, "OAC_OBLIGATION_INVALID")
                for raw in _list(plan_spec.get("obligations"), "OAC_OBLIGATIONS_INVALID")
            )
        }
        roles = {
            _str(item.get("roleInstanceId"), "OAC_ROLE_INSTANCE_INVALID"): item
            for item in (
                _dict(raw, "OAC_ROLE_INSTANCE_INVALID")
                for raw in _list(plan_spec.get("roleInstances"), "OAC_ROLE_INSTANCES_INVALID")
            )
        }
        work_units = {
            _str(item.get("workUnitId"), "OAC_WORK_UNIT_INVALID"): item
            for item in (
                _dict(raw, "OAC_WORK_UNIT_INVALID")
                for raw in _list(plan_spec.get("workUnits"), "OAC_WORK_UNITS_INVALID")
            )
        }
        role_bindings = {
            _str(item.get("roleInstanceRef"), "OAC_RUNTIME_ROLE_INVALID"): item
            for item in (
                _dict(raw, "OAC_RUNTIME_ROLE_INVALID")
                for raw in _list(binding_spec.get("roleBindings"), "OAC_RUNTIME_ROLES_INVALID")
            )
        }
        if set(role_bindings) != set(roles):
            raise IntegrityError("OAC_RUNTIME_ROLE_COVERAGE_MISMATCH")
        subject_principals: dict[str, set[str]] = {}
        prefix = _str(self.policy.get("runtime_subject_prefix"), "OAC_POLICY_SUBJECT_PREFIX_INVALID")
        for role_ref, role in roles.items():
            runtime_role = role_bindings[role_ref]
            principal = _str(role.get("principalRef"), "OAC_PRINCIPAL_REF_INVALID")
            subject = _str(runtime_role.get("runtimeSubject"), "OAC_RUNTIME_SUBJECT_INVALID")
            if runtime_role.get("principalRef") != principal or not subject.startswith(prefix):
                raise IntegrityError("OAC_ROLE_PRINCIPAL_SUBJECT_MISMATCH")
            subject_principals.setdefault(subject, set()).add(principal)
            expected_capabilities = {
                f"capability:{_str(obligations[_str(ref, 'OAC_ROLE_OBLIGATION_REF_INVALID')].get('obligationType'), 'OAC_OBLIGATION_INVALID')}"
                for ref in _list(role.get("obligationRefs"), "OAC_ROLE_OBLIGATIONS_INVALID")
            }
            actual_capabilities = _list(runtime_role.get("capabilityRefs"), "OAC_CAPABILITIES_INVALID")
            if actual_capabilities != list(_sorted(expected_capabilities)):
                raise IntegrityError("OAC_RUNTIME_CAPABILITY_SET_MISMATCH")
        if any(len(principals) > 1 for principals in subject_principals.values()):
            raise IntegrityError("OAC_RUNTIME_SUBJECT_COLLISION")

        evidence_by_type: dict[str, set[str]] = {}
        for obligation in obligations.values():
            obligation_type = _str(obligation.get("obligationType"), "OAC_OBLIGATION_INVALID")
            evidence_by_type.setdefault(obligation_type, set()).update(
                _str(item, "OAC_EVIDENCE_REF_INVALID")
                for item in _list(obligation.get("requiredEvidence"), "OAC_EVIDENCE_INVALID")
            )
        handler_bindings = {
            _str(item.get("obligationType"), "OAC_HANDLER_INVALID"): item
            for item in (
                _dict(raw, "OAC_HANDLER_INVALID")
                for raw in _list(binding_spec.get("handlerBindings"), "OAC_HANDLERS_INVALID")
            )
        }
        if set(handler_bindings) != set(evidence_by_type):
            raise IntegrityError("OAC_RUNTIME_HANDLER_COVERAGE_MISMATCH")
        policy_handlers = {
            _str(item.get("obligation_type"), "OAC_POLICY_HANDLER_INVALID"): item
            for item in (
                _dict(raw, "OAC_POLICY_HANDLER_INVALID")
                for raw in _list(self.policy.get("allowed_handler_bindings"), "OAC_POLICY_HANDLERS_INVALID")
            )
        }
        for obligation_type, handler in handler_bindings.items():
            expected = {
                "obligationType": obligation_type,
                "handlerRef": f"urn:oac:zero-effect-handler:{obligation_type}",
                "handlerDigest": "sha256:"
                + hashlib.sha256(f"handler:{obligation_type}".encode()).hexdigest(),
                "capabilityRef": f"capability:{obligation_type}",
                "evidenceOutputRefs": list(_sorted(evidence_by_type[obligation_type])),
                "effectCeiling": _ZERO_EFFECT,
            }
            if handler != expected:
                raise IntegrityError("OAC_RUNTIME_HANDLER_CONTRACT_MISMATCH")
            policy_handler = policy_handlers.get(obligation_type)
            if policy_handler is None or policy_handler != {
                "obligation_type": obligation_type,
                "handler_ref": expected["handlerRef"],
                "handler_digest": expected["handlerDigest"],
                "capability_ref": expected["capabilityRef"],
                "evidence_output_refs": expected["evidenceOutputRefs"],
            }:
                raise IntegrityError("OAC_RUNTIME_POLICY_HANDLER_DENIED")

        order = _topological_order(plan)
        steps = _list(bundle_spec.get("steps"), "OAC_RUNTIME_STEPS_INVALID")
        if len(steps) != len(order) or len(steps) > int(self.policy["max_steps"]):
            raise IntegrityError("OAC_RUNTIME_STEP_COUNT_INVALID")
        predecessor_map = {ref: set() for ref in order}
        for raw_edge in _list(plan_spec.get("happensBefore"), "OAC_DAG_INVALID"):
            edge = _dict(raw_edge, "OAC_DAG_EDGE_INVALID")
            predecessor_map[str(edge["successorRef"])].add(str(edge["predecessorRef"]))
        for index, raw_step in enumerate(steps):
            step = _dict(raw_step, "OAC_RUNTIME_STEP_INVALID")
            work_ref = order[index]
            work = work_units[work_ref]
            expected_roles = list(_sorted(set(work.get("roleInstanceRefs", []))))
            actual_roles = _list(step.get("roleBindings"), "OAC_STEP_ROLES_INVALID")
            if (
                step.get("stepIndex") != index
                or step.get("workUnitRef") != work_ref
                or step.get("predecessorRefs") != list(_sorted(predecessor_map[work_ref]))
                or step.get("roleBindings") != [
                    {
                        "roleInstanceRef": ref,
                        "principalRef": roles[ref]["principalRef"],
                        "runtimeSubject": role_bindings[ref]["runtimeSubject"],
                    }
                    for ref in expected_roles
                ]
                or step.get("accountable")
                != {
                    "roleInstanceRef": work["accountableRoleInstanceRef"],
                    "principalRef": roles[work["accountableRoleInstanceRef"]]["principalRef"],
                    "runtimeSubject": role_bindings[work["accountableRoleInstanceRef"]]["runtimeSubject"],
                }
                or step.get("obligationRefs")
                != list(_sorted(set(work.get("obligationRefs", []))))
                or step.get("evidenceOutputRefs")
                != list(_sorted(set(work.get("evidenceOutputs", []))))
                or step.get("effectCeiling") != _ZERO_EFFECT
                or step.get("targetWrites") != 0
            ):
                raise IntegrityError("OAC_RUNTIME_STEP_PROJECTION_MISMATCH")
            if len(actual_roles) != len(expected_roles):
                raise IntegrityError("OAC_RUNTIME_STEP_ROLE_COVERAGE_MISMATCH")
            expected_step_handlers = []
            for obligation_ref in _sorted(set(work.get("obligationRefs", []))):
                obligation = obligations[obligation_ref]
                obligation_type = str(obligation["obligationType"])
                handler = handler_bindings[obligation_type]
                expected_step_handlers.append(
                    {
                        "obligationRef": obligation_ref,
                        "obligationType": obligation_type,
                        "handlerRef": handler["handlerRef"],
                        "handlerDigest": handler["handlerDigest"],
                        "capabilityRef": handler["capabilityRef"],
                        "evidenceOutputRefs": list(
                            _sorted(set(obligation.get("requiredEvidence", [])))
                        ),
                        "effectCeiling": _ZERO_EFFECT,
                    }
                )
            if step.get("handlerBindings") != expected_step_handlers:
                raise IntegrityError("OAC_RUNTIME_STEP_HANDLER_MISMATCH")

        topology_digest = _topology_digest(plan)
        obligation_digest = _obligation_contract_digest(snapshot, change, plan)
        if (
            bundle_spec.get("topologyDigest") != topology_digest
            or bundle_spec.get("obligationContractDigest") != obligation_digest
        ):
            raise IntegrityError("OAC_RUNTIME_PROJECTION_DIGEST_MISMATCH")
        return {
            "status": "PASS",
            "checked": _CHECKS,
            "namespace": namespace,
            "runtime_owner_id": self.runtime_owner(namespace),
            "step_count": len(steps),
            "work_unit_refs": order,
            "topology_digest": topology_digest,
            "obligation_contract_digest": obligation_digest,
            "target_writes": 0,
        }


class OACRuntimeAdmissionBridge:
    def __init__(
        self,
        store: StateStore,
        *,
        policy_path: str | Path = DEFAULT_POLICY_PATH,
    ) -> None:
        self.store = store
        self.policy, self.policy_digest = load_runtime_policy(policy_path)
        self.verifier = OACRuntimeAdmissionVerifier(self.policy, self.policy_digest)

    def prepare(self, capsule: OACRuntimeCapsule) -> OACFormationPreview:
        capsule = _revalidate_model(
            capsule,
            OACRuntimeCapsule,
            "OAC_RUNTIME_CAPSULE_MODEL_INVALID",
        )
        verified = self.verifier.verify(capsule)
        bundle_spec = _dict(capsule.runtime_bundle["spec"], "OAC_BUNDLE_SPEC_INVALID")
        suffix = capsule.digest.removeprefix("sha256:")[:20]
        return OACFormationPreview(
            id=f"oac-formation-preview:{capsule.case_id.lower()}:{suffix}",
            capsule_digest=capsule.digest,
            policy_digest=self.policy_digest,
            plan_digest=str(capsule.plan["digest"]),
            certificate_digest=str(capsule.plan_certificate["digest"]),
            runtime_binding_digest=str(capsule.runtime_binding["digest"]),
            runtime_bundle_digest=str(capsule.runtime_bundle["digest"]),
            lowering_receipt_digest=str(capsule.runtime_lowering_receipt["digest"]),
            topology_digest=str(bundle_spec["topologyDigest"]),
            obligation_contract_digest=str(bundle_spec["obligationContractDigest"]),
            work_unit_refs=tuple(verified["work_unit_refs"]),
            step_count=int(verified["step_count"]),
            runtime_owner_id=str(verified["runtime_owner_id"]),
        )

    def approve(
        self,
        preview: OACFormationPreview,
        *,
        actor_id: str,
        preview_digest: str,
        command_id: str,
        approved_at: str,
    ) -> OACRuntimeApproval:
        preview = _revalidate_model(
            preview,
            OACFormationPreview,
            "OAC_RUNTIME_PREVIEW_MODEL_INVALID",
        )
        if preview_digest != preview.digest:
            raise IntegrityError("OAC_RUNTIME_APPROVAL_PREVIEW_DIGEST_MISMATCH")
        if actor_id != preview.runtime_owner_id:
            raise PermissionError("OAC_RUNTIME_APPROVER_MISMATCH")
        if not command_id:
            raise ValueError("OAC_RUNTIME_APPROVAL_COMMAND_ID_REQUIRED")
        return OACRuntimeApproval(
            id=f"oac-runtime-approval:{command_id}",
            preview_ref=preview.id,
            preview_digest=preview.digest,
            capsule_digest=preview.capsule_digest,
            runtime_binding_digest=preview.runtime_binding_digest,
            runtime_owner_id=actor_id,
            command_id=command_id,
            approved_at=approved_at,
        )

    def _recover(self, receipt: RuntimeAdmissionReceipt) -> RuntimeAdmissionReceipt:
        stored = self.store.load_artifact(receipt.id, RECEIPT_MEDIA_TYPE)
        recovered = RuntimeAdmissionReceipt.model_validate(stored.payload)
        formation_id, version = recovered.formation_ref.rsplit("@", 1)
        formation_object = self.store.get_object(formation_id, version)
        if (
            recovered.digest != receipt.digest
            or formation_object.state is not ObjectState.ACTIVE
            or formation_object.payload.get("digest") != recovered.formation_digest
        ):
            raise IntegrityError("OAC_RUNTIME_ADMISSION_RECOVERY_FAILED")
        return recovered

    def apply(
        self,
        capsule: OACRuntimeCapsule,
        preview: OACFormationPreview,
        approval: OACRuntimeApproval | None,
    ) -> RuntimeAdmissionReceipt:
        if approval is None:
            raise PermissionError("OAC_RUNTIME_APPROVAL_REQUIRED")
        capsule = _revalidate_model(
            capsule,
            OACRuntimeCapsule,
            "OAC_RUNTIME_CAPSULE_MODEL_INVALID",
        )
        preview = _revalidate_model(
            preview,
            OACFormationPreview,
            "OAC_RUNTIME_PREVIEW_MODEL_INVALID",
        )
        approval = _revalidate_model(
            approval,
            OACRuntimeApproval,
            "OAC_RUNTIME_APPROVAL_MODEL_INVALID",
        )
        expected_preview = self.prepare(capsule)
        if (
            expected_preview.digest != preview.digest
            or expected_preview.model_dump(mode="json")
            != preview.model_dump(mode="json")
        ):
            raise IntegrityError("OAC_RUNTIME_PREVIEW_STALE_OR_TAMPERED")
        if (
            approval.preview_ref != preview.id
            or approval.preview_digest != preview.digest
            or approval.capsule_digest != capsule.digest
            or approval.runtime_binding_digest != preview.runtime_binding_digest
            or approval.runtime_owner_id != preview.runtime_owner_id
            or preview.policy_digest != self.policy_digest
            or approval.scope != "LOCAL_FORMATION_CONTROL_PLANE_ONLY"
        ):
            raise IntegrityError("OAC_RUNTIME_APPROVAL_BINDING_INVALID")
        request_digest = sha256_digest(
            {
                "capsule_digest": capsule.digest,
                "preview_digest": preview.digest,
                "approval_digest": approval.digest,
            }
        )
        idempotency_key = f"oac-runtime-admission:{approval.command_id}"
        existing = self.store.get_idempotent(idempotency_key, request_digest)
        if existing is not None:
            return self._recover(RuntimeAdmissionReceipt.model_validate(existing))
        suffix = capsule.runtime_bundle["digest"].removeprefix("sha256:")[:20]
        formation = OACFormationControlRecord(
            id=f"oac-formation:{capsule.case_id.lower()}:{suffix}",
            capsule_digest=capsule.digest,
            preview_digest=preview.digest,
            approval_digest=approval.digest,
            plan_digest=preview.plan_digest,
            runtime_binding_digest=preview.runtime_binding_digest,
            runtime_bundle_digest=preview.runtime_bundle_digest,
            topology_digest=preview.topology_digest,
            work_unit_refs=preview.work_unit_refs,
            activated_at=approval.approved_at,
        )
        formation_object = VersionedObject(
            id=formation.id,
            version=formation.version,
            kind="OAC_FORMATION_CONTROL_PLANE",
            label=f"OAC {capsule.case_id} local Formation",
            domain="runtime-control-plane",
            state=ObjectState.ACTIVE,
            payload=formation.model_dump(mode="json"),
            source_refs=(capsule.digest, preview.digest, approval.digest),
        )
        receipt = RuntimeAdmissionReceipt(
            id=f"oac-runtime-admission-receipt:{capsule.case_id.lower()}:{suffix}",
            capsule_digest=capsule.digest,
            preview_digest=preview.digest,
            approval_digest=approval.digest,
            formation_ref=formation_object.ref,
            formation_digest=formation.digest,
            plan_digest=preview.plan_digest,
            certificate_digest=preview.certificate_digest,
            runtime_binding_digest=preview.runtime_binding_digest,
            runtime_bundle_digest=preview.runtime_bundle_digest,
            lowering_receipt_digest=preview.lowering_receipt_digest,
            policy_digest=self.policy_digest,
            checked=_CHECKS,
            admitted_at=approval.approved_at,
        )
        with self.store.transaction() as connection:
            self.store.insert_version(connection, formation_object, make_current=True)
            for artifact_id, media_type, payload in (
                (f"oac-runtime-capsule:{capsule.case_id.lower()}", CAPSULE_MEDIA_TYPE, capsule.model_dump(mode="json")),
                (preview.id, PREVIEW_MEDIA_TYPE, preview.model_dump(mode="json")),
                (approval.id, APPROVAL_MEDIA_TYPE, approval.model_dump(mode="json")),
                (formation.id, FORMATION_MEDIA_TYPE, formation.model_dump(mode="json")),
                (receipt.id, RECEIPT_MEDIA_TYPE, receipt.model_dump(mode="json")),
            ):
                self.store.save_artifact(connection, artifact_id, media_type, payload)
            self.store.append_event(
                connection,
                "OAC_RUNTIME_FORMATION_ACTIVATED",
                {
                    "case_id": capsule.case_id,
                    "capsule_digest": capsule.digest,
                    "preview_digest": preview.digest,
                    "approval_digest": approval.digest,
                    "formation_ref": formation_object.ref,
                    "formation_digest": formation.digest,
                    "runtime_admission_receipt_digest": receipt.digest,
                    "status": "LOCAL_RUNTIME_ADMISSION_PASS",
                    "target_writes": 0,
                    "handler_execution": "NOT_RUN",
                    "agent_execution": "NOT_RUN",
                },
            )
            self.store.save_idempotent(
                connection,
                idempotency_key,
                request_digest,
                receipt.model_dump(mode="json"),
            )
        return self._recover(receipt)
