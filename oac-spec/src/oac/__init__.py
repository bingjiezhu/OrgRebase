"""OAC zero-effect reference kernel.

The package is deliberately small: models and registries define the public contract,
``compiler`` is one replaceable producer, and ``verifier`` is a separate consumer of
the frozen JSON resources.
"""

from .canonical import parse_resource, seal_resource, verify_resource_digest
from .compiler import compile_change, compile_supplier_change
from .evolution import (
    project_demand_root,
    project_evolution_root,
    project_execution_root,
    project_outcome_root,
    project_plan_root,
    project_source_root,
    verify_organizational_demand,
    verify_outcome_certificate,
    verify_source_admission_receipt,
    verify_successor_admission,
)
from .lowering import LoweringResult, lower_plan
from .models import (
    OrganizationalDemand,
    OrganizationPlan,
    OrganizationSnapshot,
    OrgChangeCase,
    OutcomeCertificate,
    PlanCertificate,
    RuntimeBinding,
    RuntimeLoweringReceipt,
    SemanticChangeSet,
    SourceAdmissionReceipt,
    ZeroEffectRuntimeBundle,
)
from .verifier import verify_change, verify_plan

__all__ = [
    "LoweringResult",
    "OrgChangeCase",
    "OrganizationPlan",
    "OrganizationSnapshot",
    "OrganizationalDemand",
    "OutcomeCertificate",
    "PlanCertificate",
    "RuntimeBinding",
    "RuntimeLoweringReceipt",
    "SemanticChangeSet",
    "SourceAdmissionReceipt",
    "ZeroEffectRuntimeBundle",
    "compile_change",
    "compile_supplier_change",
    "lower_plan",
    "parse_resource",
    "project_demand_root",
    "project_evolution_root",
    "project_execution_root",
    "project_outcome_root",
    "project_plan_root",
    "project_source_root",
    "seal_resource",
    "verify_change",
    "verify_organizational_demand",
    "verify_outcome_certificate",
    "verify_plan",
    "verify_resource_digest",
    "verify_source_admission_receipt",
    "verify_successor_admission",
]
