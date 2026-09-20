"""Deterministic resource ceilings for the Supplier conformance profile.

Resource exhaustion is a protocol error, never a business-domain verdict and
never a reason to emit a partial closure.  The executable constants mirror the
published machine-readable profile.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .canonical import OACValidationError, canonical_bytes
from .models import OrganizationPlan, OrganizationSnapshot, SemanticChangeSet


class ResourceProfileExceeded(OACValidationError):
    """Raised before a partial result can escape the conformance boundary."""

    def __init__(self, dimension: str, observed: int | None, maximum: int) -> None:
        detail = (
            f"{dimension} exceeds profile maximum: {observed} > {maximum}"
            if observed is not None
            else f"{dimension} exhausted parser capacity under profile maximum {maximum}"
        )
        super().__init__(
            "CONFORMANCE_RESOURCE_PROFILE_EXCEEDED",
            detail,
        )
        self.dimension = dimension
        self.observed = observed
        self.maximum = maximum


@dataclass(frozen=True, slots=True)
class SupplierResourceProfile:
    profile_id: str = "oac.supplier.transfer/conformance-resource-profile/v1"
    max_bundle_files: int = 512
    max_bundle_bytes: int = 67_108_864
    max_case_bytes: int = 1_048_576
    max_request_bytes: int = 1_048_576
    max_resource_bytes: int = 8_388_608
    max_plan_bytes: int = 16_777_216
    max_json_depth: int = 64
    max_nodes: int = 256
    max_edges: int = 1_024
    max_rules: int = 512
    max_duties: int = 512
    max_semantic_depth: int = 32
    max_path_prefixes: int = 4_096
    max_evaluations: int = 2_048
    max_witness_refs_per_evaluation: int = 256
    max_total_witness_refs: int = 8_192
    adapter_timeout_ms: int = 5_000
    max_adapter_output_bytes: int = 1_048_576

    def as_json(self) -> dict[str, object]:
        value = asdict(self)
        return {
            "profileId": value["profile_id"],
            "exhaustion": {
                "sutStatus": "RESOURCE_EXHAUSTED",
                "errorCode": "CONFORMANCE_RESOURCE_PROFILE_EXCEEDED",
                "partialDomainResultPermitted": False,
            },
            "limits": {
                "maxResourceBytes": value["max_resource_bytes"],
                "maxBundleFiles": value["max_bundle_files"],
                "maxBundleBytes": value["max_bundle_bytes"],
                "maxCaseBytes": value["max_case_bytes"],
                "maxRequestBytes": value["max_request_bytes"],
                "maxPlanBytes": value["max_plan_bytes"],
                "maxJsonDepth": value["max_json_depth"],
                "maxNodes": value["max_nodes"],
                "maxEdges": value["max_edges"],
                "maxRules": value["max_rules"],
                "maxDuties": value["max_duties"],
                "maxSemanticDepth": value["max_semantic_depth"],
                "maxPathPrefixes": value["max_path_prefixes"],
                "maxEvaluations": value["max_evaluations"],
                "maxWitnessRefsPerEvaluation": value[
                    "max_witness_refs_per_evaluation"
                ],
                "maxTotalWitnessRefs": value["max_total_witness_refs"],
                "adapterTimeoutMs": value["adapter_timeout_ms"],
                "maxAdapterOutputBytes": value["max_adapter_output_bytes"],
            },
        }


SUPPLIER_RESOURCE_PROFILE = SupplierResourceProfile()


def _limit(name: str, observed: int, maximum: int) -> None:
    if observed > maximum:
        raise ResourceProfileExceeded(name, observed, maximum)


def enforce_supplier_inputs(
    snapshot: OrganizationSnapshot,
    change: SemanticChangeSet,
    *,
    profile: SupplierResourceProfile = SUPPLIER_RESOURCE_PROFILE,
) -> None:
    """Reject oversized roots before any closure is materialized."""

    _limit("snapshotBytes", len(canonical_bytes(snapshot)), profile.max_resource_bytes)
    _limit("changeBytes", len(canonical_bytes(change)), profile.max_resource_bytes)
    _limit("nodes", len(snapshot.spec.nodes), profile.max_nodes)
    _limit("edges", len(snapshot.spec.dependency_edges), profile.max_edges)
    _limit("rules", len(snapshot.spec.impact_rules), profile.max_rules)
    _limit("duties", len(snapshot.spec.unknown_transition_duties), profile.max_duties)
    _limit(
        "semanticMaxDepth",
        snapshot.spec.completeness.max_depth,
        profile.max_semantic_depth,
    )


def enforce_evaluation_budget(
    evaluation_witness_counts: tuple[int, ...],
    *,
    profile: SupplierResourceProfile = SUPPLIER_RESOURCE_PROFILE,
) -> None:
    _limit("evaluations", len(evaluation_witness_counts), profile.max_evaluations)
    for count in evaluation_witness_counts:
        _limit(
            "witnessRefsPerEvaluation",
            count,
            profile.max_witness_refs_per_evaluation,
        )
    _limit(
        "totalWitnessRefs",
        sum(evaluation_witness_counts),
        profile.max_total_witness_refs,
    )


def enforce_path_prefix_budget(
    count: int,
    *,
    profile: SupplierResourceProfile = SUPPLIER_RESOURCE_PROFILE,
) -> None:
    _limit("pathPrefixes", count, profile.max_path_prefixes)


def enforce_plan_output(
    plan: OrganizationPlan,
    *,
    profile: SupplierResourceProfile = SUPPLIER_RESOURCE_PROFILE,
) -> None:
    _limit("planBytes", len(canonical_bytes(plan)), profile.max_plan_bytes)
