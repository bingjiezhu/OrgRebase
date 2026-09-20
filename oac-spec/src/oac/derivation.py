"""Public construction of the topology-neutral Profile derivation artifact."""

from __future__ import annotations

from .models import (
    DERIVATION_REPORT_VERSION,
    DerivationOrderRequirement,
    OrganizationSnapshot,
    ProfileDerivationReport,
    SemanticChangeSet,
)
from .supplier import DerivedContract, derive_supplier_contract


def profile_derivation_report(
    snapshot: OrganizationSnapshot,
    change: SemanticChangeSet,
    *,
    derived: DerivedContract | None = None,
) -> ProfileDerivationReport:
    """Return the normative semantic projection before topology selection."""

    contract = derived or derive_supplier_contract(snapshot, change)
    if contract.profile_binding is not None:
        raise ValueError("Supplier report cannot relabel another change profile")
    if snapshot.digest is None or change.digest is None:
        raise ValueError("ProfileDerivationReport requires sealed source roots")
    if (
        contract.snapshot_digest != snapshot.digest
        or contract.change_digest != change.digest
    ):
        raise ValueError("DerivedContract roots do not match ProfileDerivationReport roots")
    return ProfileDerivationReport(
        apiVersion=DERIVATION_REPORT_VERSION,
        kind="ProfileDerivationReport",
        profileId="oac.supplier.transfer",
        profileVersion="v0.2",
        snapshotDigest=snapshot.digest,
        changeDigest=change.digest,
        applicabilityEvaluations=tuple(
            sorted(
                contract.applicability_evaluations,
                key=lambda item: item.evaluation_id,
            )
        ),
        impactPaths=tuple(
            sorted(contract.impact_paths, key=lambda item: item.path_id)
        ),
        obligations=tuple(
            sorted(contract.obligations, key=lambda item: item.obligation_id)
        ),
        requiredOrders=tuple(
            sorted(
                (
                    DerivationOrderRequirement(
                        predecessorRoleRef=order.predecessor_role_ref,
                        successorRoleRef=order.successor_role_ref,
                        reasonRefs=tuple(sorted(set(order.reason_refs))),
                        roleWide=order.role_wide,
                        dependencyReasonRefs=tuple(
                            sorted(set(order.dependency_reason_refs))
                        ),
                        prerequisiteReasonGroups=tuple(
                            sorted(set(order.prerequisite_reason_groups))
                        ),
                    )
                    for order in contract.required_orders
                ),
                key=lambda item: (
                    item.predecessor_role_ref,
                    item.successor_role_ref,
                    item.role_wide,
                    item.reason_refs,
                    item.dependency_reason_refs,
                    item.prerequisite_reason_groups,
                ),
            )
        ),
        unresolvedRefs=tuple(sorted(set(contract.unresolved_refs))),
        rootApplicabilityUnknown=contract.root_applicability_unknown,
    )
