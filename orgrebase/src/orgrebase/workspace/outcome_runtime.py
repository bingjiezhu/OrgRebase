"""The immutable execution package shared by approval, execution and outcome proof."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.workspace.evolution_evidence import _seal
from orgrebase.workspace.oac_wire import _resource_ref, _verify_oac_resource
from orgrebase.workspace.outcome_contracts import OutcomeTaskMapping

PROFILE_DIGEST = "sha256:73d922826b1eef350a13a81fb4626ccfff1aca68b426eacc79d9db84311d9e58"


def profile_binding() -> dict[str, str]:
    return {
        "profileId": "oac.outcome.disposable-local",
        "profileVersion": "v0.1",
        "profileDigest": PROFILE_DIGEST,
    }


def resource(
    kind: str,
    spec: dict[str, Any],
    metadata: Mapping[str, Any],
    created_at: str,
    sources: Sequence[str] = (),
) -> dict[str, Any]:
    identity = sha256_digest(
        {"kind": kind, "namespace": metadata["namespace"], "createdAt": created_at, "spec": spec}
    )
    return _seal(
        {
            "apiVersion": "oac.dev/v0alpha1",
            "kind": kind,
            "metadata": {
                "id": f"lab:{kind}:{identity.removeprefix('sha256:')}",
                "namespace": metadata["namespace"],
                "revision": 1,
                "ownerRef": metadata["ownerRef"],
                "governanceRef": metadata["governanceRef"],
                "createdAt": created_at,
                "effectiveFrom": None,
                "effectiveTo": None,
                "sourceRefs": list(sources),
            },
            "spec": spec,
        }
    )


def build_runtime_bundle(
    mapping: OutcomeTaskMapping,
    approval: Mapping[str, Any],
    *,
    plan_ref: Mapping[str, Any],
    certificate_ref: Mapping[str, Any],
    metadata: Mapping[str, Any],
    acting_authority: str,
) -> dict[str, Any]:
    """Create data only. The controller's one-use token is the execution authority."""
    mapping = mapping.revalidated()
    for ref in (plan_ref, certificate_ref):
        if (
            set(ref) != {"apiVersion", "kind", "namespace", "resourceId", "revision", "digest"}
            or ref["apiVersion"] != "oac.dev/v0alpha1"
            or ref["namespace"] != metadata["namespace"]
            or not isinstance(ref["resourceId"], str)
            or not ref["resourceId"]
            or type(ref["revision"]) is not int
            or ref["revision"] < 1
            or not isinstance(ref["digest"], str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", ref["digest"]) is None
        ):
            raise IntegrityError("LAB_RUNTIME_BUNDLE_REFERENCE_INVALID")
    if (
        plan_ref.get("kind") != "OrganizationPlan"
        or plan_ref.get("digest") != mapping.plan_digest
        or certificate_ref.get("kind") != "PlanCertificate"
        or certificate_ref.get("digest") != approval.get("plan_certificate_digest")
        or approval.get("mapping_digest") != mapping.digest
        or approval.get("budget_digest") != mapping.budget.digest
        or approval.get("seed_root") != mapping.seed_root
        or approval.get("system") not in mapping.system_roles
        or not acting_authority
        or acting_authority == approval.get("controller")
    ):
        raise IntegrityError("LAB_RUNTIME_BUNDLE_INPUT_MISMATCH")
    created_at = approval["recorded_at"]
    mapped = resource("DisposableTaskMapping", mapping.model_dump(mode="json"), metadata, created_at)
    grant = resource(
        "DisposableExecutionGrant",
        {
            "approvalReceipt": dict(approval),
            "mappingRef": _resource_ref(mapped),
            "planCertificateRef": dict(certificate_ref),
            "controllerAuthority": approval["controller"],
            "actingAuthority": acting_authority,
            "executionScope": "disposable_local_only",
            "productionAuthority": False,
        },
        metadata,
        created_at,
    )
    binding = resource(
        "DisposableRuntimeBinding",
        {
            "profileBinding": profile_binding(),
            "executionAuthorizationRef": _resource_ref(grant),
            "mappingRef": _resource_ref(mapped),
            "planRef": dict(plan_ref),
            "planCertificateRef": dict(certificate_ref),
            "actingAuthority": acting_authority,
            "planEffectCeiling": "zero_effect",
            "executionScope": "disposable_local_only",
            "productionAuthority": False,
        },
        metadata,
        created_at,
    )
    bundle = resource(
        "DisposableRuntimeBundle",
        {
            "profileBinding": profile_binding(),
            "runtimeBindingRef": _resource_ref(binding),
            "executionAuthorizationRef": _resource_ref(grant),
            "planRef": dict(plan_ref),
            "planEffectCeiling": "zero_effect",
            "executionScope": "disposable_local_only",
            "productionAuthority": False,
            "mappingRef": _resource_ref(mapped),
            "actingAuthority": acting_authority,
            "system": approval["system"],
            "systemRoles": list(mapping.system_roles[approval["system"]]),
            "budget": mapping.budget.model_dump(mode="json"),
            "tools": [item.model_dump(mode="json") for item in mapping.grants],
            "workUnitPredecessors": {
                key: list(value) for key, value in mapping.work_unit_predecessors.items()
            },
            "environmentBuildDigest": mapping.environment_build_digest,
            "seedRoot": mapping.seed_root,
            "changeProfile": mapping.oac_profile,
        },
        metadata,
        created_at,
    )
    package = {
        "schema_version": "orgrebase.disposable-runtime-package.v1",
        "mapping": mapped,
        "grant": grant,
        "binding": binding,
        "bundle": bundle,
    }
    for value in (mapped, grant, binding, bundle):
        _verify_oac_resource(value, value["kind"])
    return {**package, "digest": sha256_digest(package)}


def verify_runtime_bundle(
    package: Mapping[str, Any],
    mapping: OutcomeTaskMapping,
    approval: Mapping[str, Any],
    *,
    acting_authority: str,
) -> None:
    """Replay its closure; admission additionally compares the controller's saved package."""
    try:
        package = json.loads(json.dumps(package, allow_nan=False))
        binding = package["binding"]
        expected = build_runtime_bundle(
            mapping,
            approval,
            plan_ref=binding["spec"]["planRef"],
            certificate_ref=binding["spec"]["planCertificateRef"],
            metadata=binding["metadata"],
            acting_authority=acting_authority,
        )
        if sha256_digest(package) != sha256_digest(expected):
            raise IntegrityError("LAB_RUNTIME_BUNDLE_CLOSURE_MISMATCH")
    except (KeyError, TypeError, ValueError) as exc:
        raise IntegrityError("LAB_RUNTIME_BUNDLE_MALFORMED") from exc
