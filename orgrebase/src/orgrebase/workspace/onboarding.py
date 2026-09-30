"""Read-only enterprise onboarding and supported-profile projection."""

from __future__ import annotations

from typing import Any

from orgrebase.workspace.change_proposals import require_action
from orgrebase.workspace.profile_admission import enterprise_input_preflight
from orgrebase.workspace.profile_contracts import RuntimeCompatibilityMode
from orgrebase.workspace.templates import TemplateRegistry


def _template_support(
    workspace: Any,
    *,
    formation_complete: bool,
    governed_change_complete: bool,
) -> tuple[dict[str, Any], ...]:
    registry = TemplateRegistry()
    active_ref = workspace.profile.default_task.template_ref
    deliverables = getattr(workspace, "deliverable_set_profile", None)
    active_refs = {active_ref}
    if deliverables is not None:
        active_refs.update(item.template_ref for item in deliverables.members)
    executable = (
        workspace.profile.runtime_compatibility.mode == RuntimeCompatibilityMode.REFERENCE_HANDLER
        and workspace.profile_admission.reference_runtime_compatible
    )
    deployment = workspace.deployment_binding
    return tuple(
        {
            "template_ref": template.ref,
            "template_digest": template.digest,
            "deliverable_kind": template.deliverable_kind,
            "output_schema_ref": template.output_schema_ref,
            "output_schema_digest": None,
            "output_schema_digest_status": "BOUND_BY_TEMPLATE_DIGEST_ONLY",
            "schema_binding_digest": template.digest,
            "renderer_ref": f"{template.renderer_id}@{template.renderer_version}",
            "required_slots": [
                {
                    "slot_id": slot.slot_id,
                    "value_schema_ref": slot.value_schema_ref,
                    "slot_digest": slot.digest,
                }
                for slot in template.slots
                if slot.required
            ],
            "contract_status": "DEFINED",
            "runtime_status": (
                "CONFIGURED_RUNTIME_HANDLER"
                if executable and template.ref in active_refs
                else "CONTRACT_ONLY"
            ),
            "profile_binding": (
                {
                    "profile_ref": workspace.profile.ref,
                    "profile_digest": workspace.profile.digest,
                    "handler_profile": workspace.profile.runtime_compatibility.handler_profile,
                }
                if template.ref in active_refs
                else None
            ),
            "deliverable_set_binding": (
                {"profile_ref": deliverables.ref, "profile_digest": deliverables.digest}
                if deliverables is not None and template.ref in active_refs else None
            ),
            "adapter_binding": (
                {
                    "adapter_id": deployment.get("adapter_id"),
                    "adapter_digest": None,
                    "adapter_digest_status": "BOUND_BY_PACK_DIGEST_ONLY",
                    "bound_by_pack_digest": deployment.get("pack_digest"),
                }
                if template.ref in active_refs and deployment
                else None
            ),
            "formation_status": (
                "VERIFIED"
                if template.ref in active_refs and formation_complete
                else "NOT_RUN"
            ),
            "governed_change_status": (
                "VERIFIED"
                if template.ref in active_refs and governed_change_complete
                else "NOT_RUN"
            ),
            "customer_qualification": "NOT_RUN",
            "highest_verified_stage": (
                "GOVERNED_CHANGE_VERIFIED"
                if template.ref in active_refs and governed_change_complete
                else "FORMATION_VERIFIED"
                if template.ref in active_refs and formation_complete
                else "DEFINED"
            ),
        }
        for template in registry.templates
    )


def onboarding_status(workspace: Any) -> dict[str, Any]:
    """Describe current admission stages without creating facts or authority."""

    require_action(workspace, "read")
    profile = workspace.profile
    preflight = enterprise_input_preflight(
        profile,
        receipt=workspace.profile_admission,
        source_admission=workspace.source_admission,
    )
    formation = workspace._formation_record()
    governed_change_complete = any(
        workspace._change_status(event_id) == "APPLIED"
        for event_id in workspace.change_order
    )
    source = {
        "state": "NOT_CONFIGURED",
        "binding_digest": None,
        "coverage": "NOT_OBSERVED",
    }
    source_path = getattr(workspace, "source_config_path", None)
    if source_path is not None:
        try:
            from orgrebase.workspace.source_bindings import (
                active_binding,
                load_source_config,
                source_coverage,
            )

            config = load_source_config(source_path)
            binding = active_binding(workspace, config)
            coverage = source_coverage(workspace, config)
            source = {
                "state": "OWNER_CONFIRMED",
                "binding_digest": binding["binding_digest"],
                "coverage": coverage["status"],
            }
        except (KeyError, OSError, RuntimeError, ValueError):
            source = {
                "state": "CONFIGURED_NOT_ADMITTED",
                "binding_digest": None,
                "coverage": "UNKNOWN",
            }
    stages = (
        {
            "stage": "PROFILE_PARSED",
            "state": "COMPLETE",
            "authority": "DETERMINISTIC_VALIDATOR",
            "receipt_digest": profile.digest,
        },
        {
            "stage": "SOURCE_COMPONENTS_ADMITTED",
            "state": (
                "COMPLETE"
                if workspace.source_admission.verdict == "ADMITTED"
                else "HOLD"
            ),
            "authority": "ENTERPRISE_CONTRACT_OWNER_RECEIPT",
            "receipt_digest": workspace.source_admission.digest,
        },
        {
            "stage": "PACK_SEALED",
            "state": "COMPLETE" if workspace.deployment_binding.get("pack_digest") else "NOT_APPLICABLE_LOCAL",
            "authority": "PACK_VALIDATOR",
            "receipt_digest": workspace.deployment_binding.get("pack_digest"),
        },
        {
            "stage": "PROFILE_ADMITTED",
            "state": (
                "COMPLETE"
                if workspace.profile_admission.reference_runtime_compatible
                else "HOLD"
            ),
            "authority": "PROFILE_ADMISSION_CONTROLLER",
            "receipt_digest": workspace.profile_admission.digest,
        },
        {
            "stage": "WORKSPACE_ACTIVATED",
            "state": (
                "COMPLETE"
                if workspace.deployment_binding.get("pack_digest")
                else "NOT_APPLICABLE_LOCAL"
            ),
            "authority": "SERVER_BINDING",
            "receipt_digest": workspace.deployment_binding.get("pack_digest"),
        },
        {
            "stage": "FORMATION_COMMITTED",
            "state": "COMPLETE" if formation is not None else "PENDING",
            "authority": "WORKSPACE_CONTROL_PLANE",
            "receipt_digest": formation.digest if formation is not None else None,
        },
        {
            "stage": "GOVERNED_CHANGE_VERIFIED",
            "state": "COMPLETE" if governed_change_complete else "NOT_RUN",
            "authority": "WORKSPACE_CONTROL_PLANE",
            "receipt_digest": None,
        },
        {
            "stage": "CUSTOMER_QUALIFIED",
            "state": "NOT_RUN",
            "authority": "EXTERNAL_ACCEPTANCE",
            "receipt_digest": None,
        },
    )
    return {
        "schema_version": "orgrebase.enterprise-onboarding-status.v1",
        "organization_id": profile.organization_id,
        "workspace_id": workspace.store.workspace_id,
        "profile_ref": profile.ref,
        "profile_digest": profile.digest,
        "pack_digest": workspace.deployment_binding.get("pack_digest"),
        "synthetic": profile.synthetic,
        "default_task": {
            "task_id": profile.default_task.id,
            "template_ref": profile.default_task.template_ref,
            "deliverable_kind": profile.default_task.deliverable_kind,
        },
        "required_inputs": preflight["inputs"],
        "input_preflight_status": preflight["status"],
        "profile_admission_receipt_digest": workspace.profile_admission.digest,
        "source_admission_receipt_digest": workspace.source_admission.digest,
        "declared_gaps": tuple(
            {
                "gap_id": gap.id,
                "component_kind": gap.component.value,
                "status": gap.status,
                "blocks": [gate.value for gate in gap.blocks],
            }
            for gap in profile.gaps
        ),
        "templates": _template_support(
            workspace,
            formation_complete=formation is not None,
            governed_change_complete=governed_change_complete,
        ),
        "source_binding": source,
        "stages": stages,
        "runtime_handler": {
            "mode": profile.runtime_compatibility.mode.value,
            "handler_profile": profile.runtime_compatibility.handler_profile,
        },
        "authority_created_by_projection": False,
        "canonical_writes": 0,
        "target_writes": 0,
    }
