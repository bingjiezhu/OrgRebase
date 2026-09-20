"""Small local CLI for the zero-effect OAC contract slice."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import Any

import rfc8785

from .canonical import (
    OACValidationError,
    canonical_projection,
    parse_resource,
    verify_resource_digest,
)
from .change_profiles import PROFILE_CHOICES, SUPPLIER_PROFILE
from .compiler import CompilationError, compile_change_from_admitted, compile_supplier_change
from .evolution import (
    evolution_semantic_validation_rules_json,
    verify_organizational_demand_from_admitted,
    verify_outcome_certificate_from_admitted,
    verify_source_admission_receipt_from_admitted,
)
from .json_types import JsonValue
from .lowering import lower_plan_from_admitted
from .models import (
    OrganizationalDemand,
    OrganizationPlan,
    OrganizationSnapshot,
    OutcomeCertificate,
    Resource,
    SemanticChangeSet,
    SourceAdmissionReceipt,
)
from .registry import (
    KIND_MODELS,
    kind_registry_json,
    predicate_version_registry_json,
    reason_registry_json,
)
from .sealed import AdmittedSealedResource, _decode_raw_object, _jcs, admit_sealed_resource
from .verifier import verify_change_from_admitted, verify_plan

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


_SEMANTIC_VALIDATION_RULES: tuple[dict[str, JsonValue], ...] = (
    {
        "ruleId": "OAC-SEM-001",
        "appliesToKinds": sorted(
            (
                "OrgChangeCase",
                "OrganizationalDemand",
                "OrganizationPlan",
                "OrganizationSnapshot",
                "OutcomeCertificate",
                "PlanCertificate",
                "SemanticChangeSet",
                "SourceAdmissionReceipt",
            )
        ),
        "scope": "cross_field",
        "jsonSchemaEnforced": False,
        "runtimeValidator": "oac.models.ResourceMetadata.valid_interval",
        "failureReasonCode": "CORE_SCHEMA_INVALID",
        "description": "effectiveTo must be later than effectiveFrom when both are present.",
    },
    {
        "ruleId": "OAC-SEM-002",
        "appliesToKinds": ["OrganizationSnapshot"],
        "scope": "cross_field",
        "jsonSchemaEnforced": False,
        "runtimeValidator": "oac.models.CompletenessManifest.complete_has_no_gaps",
        "failureReasonCode": "CORE_SCHEMA_INVALID",
        "description": "A complete boundary cannot declare known gaps; contextual discovery target, obligation type, and evidence are declared all-or-none.",
    },
    {
        "ruleId": "OAC-SEM-003",
        "appliesToKinds": ["OrganizationSnapshot"],
        "scope": "set_uniqueness",
        "jsonSchemaEnforced": False,
        "runtimeValidator": "oac.models.OrganizationSnapshotSpec.validate_references",
        "failureReasonCode": "CORE_SCHEMA_INVALID",
        "description": "Node, role, principal, edge, rule, unknown-transition-duty, applicability-source, and separation-constraint IDs are unique within their collections.",
    },
    {
        "ruleId": "OAC-SEM-004",
        "appliesToKinds": ["OrganizationSnapshot"],
        "scope": "referential_integrity",
        "jsonSchemaEnforced": False,
        "runtimeValidator": "oac.models.OrganizationSnapshotSpec.validate_references",
        "failureReasonCode": "CORE_SCHEMA_INVALID",
        "description": "Authority-like, graph, contextual-duty, and discovery-boundary references resolve to declared snapshot collections and represented domains; admission is checked separately.",
    },
    {
        "ruleId": "OAC-SEM-005",
        "appliesToKinds": ["SemanticChangeSet"],
        "scope": "cross_field",
        "jsonSchemaEnforced": False,
        "runtimeValidator": "oac.models.ObservedValue.state_matches_value",
        "failureReasonCode": "CORE_SCHEMA_INVALID",
        "description": "Known observations carry a value and non-known observations do not.",
    },
    {
        "ruleId": "OAC-SEM-006",
        "appliesToKinds": ["SemanticChangeSet"],
        "scope": "set_uniqueness",
        "jsonSchemaEnforced": False,
        "runtimeValidator": "oac.models.SemanticChangeSetSpec.has_unique_deltas",
        "failureReasonCode": "CORE_SCHEMA_INVALID",
        "description": "Delta paths are unique within one SemanticChangeSet.",
    },
    {
        "ruleId": "OAC-SEM-007",
        "appliesToKinds": ["OrganizationPlan"],
        "scope": "set_uniqueness",
        "jsonSchemaEnforced": False,
        "runtimeValidator": "oac.models.OrganizationPlanSpec.unique_plan_ids",
        "failureReasonCode": "CORE_SCHEMA_INVALID",
        "description": "Applicability-evaluation, impact-path, obligation, role-instance, work-unit, and decision IDs are unique; every path evaluationRef resolves inside the plan.",
    },
    {
        "ruleId": "OAC-SEM-008",
        "appliesToKinds": ["OrgChangeCase"],
        "scope": "provenance_state",
        "jsonSchemaEnforced": False,
        "runtimeValidator": "oac.models.OrgChangeCaseSpec.exploratory_oac_labels_are_candidates",
        "failureReasonCode": "CORE_SCHEMA_INVALID",
        "description": "Candidate or exploratory OAC annotations cannot be represented as source ground truth.",
    },
    {
        "ruleId": "OAC-SEM-009",
        "appliesToKinds": ["OrganizationSnapshot"],
        "scope": "authority_envelope",
        "jsonSchemaEnforced": False,
        "runtimeValidator": "oac.models.OrganizationSnapshot.has_single_enterprise_authority_envelope",
        "failureReasonCode": "CORE_SCHEMA_INVALID",
        "description": "An organization snapshot owner resolves to an admitted resource-custodian RoleDefinition; governance and source identifiers remain declared external pins.",
    },
    {
        "ruleId": "OAC-SEM-010",
        "appliesToKinds": ["SemanticChangeSet"],
        "scope": "cross_field",
        "jsonSchemaEnforced": False,
        "runtimeValidator": "oac.models.ChangeDelta.operation_matches_value_states",
        "failureReasonCode": "CORE_SCHEMA_INVALID",
        "description": "Each delta operation is consistent with its before and after observation states.",
    },
    {
        "ruleId": "OAC-SEM-011",
        "appliesToKinds": ["SemanticChangeSet"],
        "scope": "authority_envelope",
        "jsonSchemaEnforced": False,
        "runtimeValidator": "oac.models.SemanticChangeSet.has_single_enterprise_authority_envelope",
        "failureReasonCode": "CORE_SCHEMA_INVALID",
        "description": "A change declares owner and governance authority and its semantic source resolves through metadata.sourceRefs.",
    },
    {
        "ruleId": "OAC-SEM-012",
        "appliesToKinds": ["OrganizationPlan"],
        "scope": "graph_integrity",
        "jsonSchemaEnforced": False,
        "runtimeValidator": "oac.models.OrderConstraint.has_unique_reasons_and_distinct_endpoints",
        "failureReasonCode": "CORE_SCHEMA_INVALID",
        "description": "A happens-before edge is not a self-edge and carries unique reason references.",
    },
    {
        "ruleId": "OAC-SEM-013",
        "appliesToKinds": ["OrganizationPlan"],
        "scope": "authority_envelope",
        "jsonSchemaEnforced": False,
        "runtimeValidator": "oac.models.OrganizationPlan.roots_match_single_enterprise_envelope",
        "failureReasonCode": "CORE_SCHEMA_INVALID",
        "description": "Plan roots have the registered kinds, one namespace, declared authority, and exact ordered source provenance.",
    },
    {
        "ruleId": "OAC-SEM-014",
        "appliesToKinds": ["PlanCertificate"],
        "scope": "deterministic_evidence",
        "jsonSchemaEnforced": False,
        "runtimeValidator": "oac.models.VerificationDimension.witnesses_are_deterministic_and_bounded",
        "failureReasonCode": "CORE_SCHEMA_INVALID",
        "description": "Certificate witnesses are unique and deterministically sorted; the schema separately enforces the size bound.",
    },
    {
        "ruleId": "OAC-SEM-015",
        "appliesToKinds": ["PlanCertificate"],
        "scope": "authority_envelope",
        "jsonSchemaEnforced": False,
        "runtimeValidator": "oac.models.PlanCertificate.roots_match_single_enterprise_envelope",
        "failureReasonCode": "CORE_SCHEMA_INVALID",
        "description": "Certificate roots have registered kinds, one namespace, declared authority, and exact ordered source provenance.",
    },
)


_RUNTIME_LOWERING_SEMANTIC_RULES: tuple[dict[str, JsonValue], ...] = (
    {
        "ruleId": "OAC-RL-SEM-001",
        "appliesToKinds": [
            "RuntimeBinding",
            "RuntimeLoweringReceipt",
            "ZeroEffectRuntimeBundle",
        ],
        "scope": "cross_field",
        "jsonSchemaEnforced": False,
        "runtimeValidator": "oac.models.ResourceMetadata.valid_interval",
        "failureReasonCode": "CORE_SCHEMA_INVALID",
        "description": "effectiveTo must be later than effectiveFrom when both are present.",
    },
    {
        "ruleId": "OAC-RL-SEM-002",
        "appliesToKinds": ["RuntimeLoweringReceipt"],
        "scope": "cross_field",
        "jsonSchemaEnforced": False,
        "runtimeValidator": "oac.models.RuntimeLoweringReceiptSpec.status_matches_bundle",
        "failureReasonCode": "CORE_SCHEMA_INVALID",
        "description": "A lowering receipt has sorted unique reasons; PRODUCED binds exactly one bundle and no reasons, while BLOCKED binds no bundle and at least one stable reason.",
    },
    {
        "ruleId": "OAC-RL-SEM-003",
        "appliesToKinds": ["ZeroEffectRuntimeBundle"],
        "scope": "graph_integrity",
        "jsonSchemaEnforced": False,
        "runtimeValidator": "oac.models.ZeroEffectRuntimeBundleSpec.steps_are_closed_and_canonical",
        "failureReasonCode": "CORE_SCHEMA_INVALID",
        "description": "Runtime steps follow canonical UTF-8 Kahn order; role projections stay stable without authority collapse, handlers exactly cover sorted obligations and evidence, and predecessors resolve to prior steps.",
    },
    {
        "ruleId": "OAC-RL-SEM-004",
        "appliesToKinds": ["RuntimeBinding"],
        "scope": "authority_envelope",
        "jsonSchemaEnforced": False,
        "runtimeValidator": "oac.models.RuntimeBinding.roots_match_single_enterprise_envelope",
        "failureReasonCode": "CORE_SCHEMA_INVALID",
        "description": "A RuntimeBinding pins exact OrganizationPlan and PlanCertificate refs in one namespace with exact ordered source provenance.",
    },
    {
        "ruleId": "OAC-RL-SEM-005",
        "appliesToKinds": ["ZeroEffectRuntimeBundle"],
        "scope": "authority_envelope",
        "jsonSchemaEnforced": False,
        "runtimeValidator": "oac.models.ZeroEffectRuntimeBundle.roots_match_single_enterprise_envelope",
        "failureReasonCode": "CORE_SCHEMA_INVALID",
        "description": "A runtime bundle carries the registered root kinds in one namespace with exact ordered source provenance, including a PlanCertificate ref.",
    },
    {
        "ruleId": "OAC-RL-SEM-006",
        "appliesToKinds": ["RuntimeLoweringReceipt"],
        "scope": "authority_envelope",
        "jsonSchemaEnforced": False,
        "runtimeValidator": "oac.models.RuntimeLoweringReceipt.roots_match_single_enterprise_envelope",
        "failureReasonCode": "CORE_SCHEMA_INVALID",
        "description": "A lowering receipt carries the registered root kinds in one namespace with exact ordered source provenance; a produced bundleRef names ZeroEffectRuntimeBundle.",
    },
)


class TCKManifestError(ValueError):
    reason_code = "TCK_MANIFEST_INVALID"


def _load_unsealed(path: str) -> tuple[dict[str, Any], Resource]:
    try:
        decoded = _decode_raw_object(_read_bytes(path))
        return decoded, parse_resource(decoded)
    except OSError as exc:
        raise OACValidationError(
            "CORE_SCHEMA_INVALID", f"unreadable resource {path}: {exc}"
        ) from exc


def _load_admitted(path: str, kind: str | None = None) -> AdmittedSealedResource:
    try:
        raw = _read_bytes(path)
        if kind is None:
            kind = _decode_raw_object(raw).get("kind")
            if not isinstance(kind, str) or kind not in KIND_MODELS:
                raise OACValidationError("CORE_KIND_UNKNOWN", f"unknown resource kind: {kind!r}")
        return admit_sealed_resource(raw, kind)
    except OSError as exc:
        raise OACValidationError("CORE_SCHEMA_INVALID", f"unreadable resource {path}: {exc}") from exc


def _load_lowering_admitted(path: str, kind: str) -> AdmittedSealedResource:
    try:
        return _load_admitted(path, kind)
    except OACValidationError as exc:
        code = "LOWERING_BINDING_DIGEST_INVALID" if kind == "RuntimeBinding" else "LOWERING_INPUT_DIGEST_INVALID"
        raise OACValidationError(code, f"{kind} detached digest is missing or does not match its canonical projection") from exc


def _write(value: Any, output: str | None) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", by_alias=True)
    payload = rfc8785.dumps(value) + b"\n"
    if output is None:
        sys.stdout.buffer.write(payload)
    else:
        Path(output).write_bytes(payload)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="oac", description="OAC Shadow MVP local tools")
    subcommands = parser.add_subparsers(dest="command", required=True)

    validate = subcommands.add_parser("validate", help="validate one registered resource")
    validate.add_argument("resource")
    validate.add_argument("--verify-digest", action="store_true")

    validate_evolution = subcommands.add_parser(
        "validate-evolution",
        help="run the Spec 009 semantic gate for one lifecycle resource",
    )
    validate_evolution.add_argument("resource")
    validate_evolution.add_argument(
        "--snapshot",
        help="exact sealed OrganizationSnapshot required for OrganizationalDemand",
    )

    digest = subcommands.add_parser("digest", help="calculate a detached RFC 8785 digest")
    digest.add_argument("resource")

    compile_command = subcommands.add_parser("compile", help="compile a zero-effect change plan")
    compile_command.add_argument("snapshot")
    compile_command.add_argument("change")
    compile_command.add_argument("-o", "--output")
    compile_command.add_argument("--profile", choices=PROFILE_CHOICES, default=SUPPLIER_PROFILE)

    verify = subcommands.add_parser("verify", help="verify a plan without importing the compiler")
    verify.add_argument("snapshot")
    verify.add_argument("change")
    verify.add_argument("plan")
    verify.add_argument("-o", "--output")
    verify.add_argument("--profile", choices=PROFILE_CHOICES, default=SUPPLIER_PROFILE)

    lower = subcommands.add_parser(
        "lower", help="lower an exact ACCEPT plan into a local zero-effect bundle"
    )
    lower.add_argument("snapshot")
    lower.add_argument("change")
    lower.add_argument("plan")
    lower.add_argument("certificate")
    lower.add_argument("binding")
    lower.add_argument(
        "--admit-binding-digest",
        action="append",
        required=True,
        dest="admitted_binding_digests",
        help="runner-owned admitted RuntimeBinding digest (repeatable)",
    )
    lower.add_argument("-o", "--output")

    registry = subcommands.add_parser("registry", help="print a machine-readable registry")
    registry.add_argument(
        "name",
        choices=(
            "kinds",
            "predicate-versions",
            "reasons",
            "semantic-validation-rules",
            "normative-semantic-validation-rules",
            "runtime-lowering-semantic-validation-rules",
            "normative-runtime-lowering-semantic-validation-rules",
            "evolution-semantic-validation-rules",
        ),
    )

    tck = subcommands.add_parser("tck", help="run the manifest-driven local conformance kit")
    tck.add_argument(
        "--manifest",
        help="custom filesystem manifest (default: the package-owned reference TCK)",
    )

    subcommands.add_parser("demo", help="compile and verify the frozen SC-001 example")
    return parser


def semantic_validation_rules_json() -> dict[str, JsonValue]:
    """Describe invariants intentionally enforced beyond JSON Schema.

    The exported Draft 2020-12 schemas are structural interoperability
    artifacts.  The registry publishes the additional required behavior;
    Pydantic model validators are this package's non-normative reference
    enforcement of the cross-field, set, and referential rules listed here.
    """

    quantifiers = {
        "cross_field": "for_each_resource",
        "set_uniqueness": "for_each_declared_collection",
        "referential_integrity": "for_each_reference",
        "provenance_state": "for_each_annotation",
        "authority_envelope": "for_each_authority_envelope",
        "graph_integrity": "for_each_order_constraint",
        "deterministic_evidence": "for_each_verification_dimension",
    }
    rules: list[dict[str, JsonValue]] = []
    for source in _SEMANTIC_VALIDATION_RULES:
        rule = dict(source)
        validator = str(rule.pop("runtimeValidator"))
        scope = str(rule["scope"])
        rule["ruleVersion"] = "1.0.0"
        rule["normativeRef"] = "standard/oac-core-v0.1.md#semantic-validation-rules"
        rule["quantifier"] = quantifiers[scope]
        rule["implementationBindings"] = {
            "python": {"validator": validator},
        }
        rules.append(rule)

    return {
        "apiVersion": "oac.dev/v0alpha1",
        "registryKind": "SemanticValidationRuleRegistry",
        "jsonSchema": {
            "dialect": "https://json-schema.org/draft/2020-12/schema",
            "validationScope": "structural_only",
        },
        "semanticValidation": {
            "requiredAfterJsonSchema": True,
            "normativeProcedureRef": "standard/oac-core-v0.1.md#semantic-validation-rules",
        },
        "implementationBindings": {
            "python": {
                "cli": "oac validate RESOURCE",
                "library": "oac.canonical.parse_resource",
            }
        },
        "rules": rules,
    }


def normative_semantic_validation_rules_json() -> dict[str, JsonValue]:
    """Return the digest-bound, implementation-neutral rule projection.

    This is built from the development registry through an explicit field
    allowlist.  Adding a new binding-shaped field to that registry therefore
    cannot accidentally change or leak into the normative artifact.
    """

    source = semantic_validation_rules_json()
    allowed_fields = (
        "ruleId",
        "ruleVersion",
        "appliesToKinds",
        "scope",
        "jsonSchemaEnforced",
        "failureReasonCode",
        "description",
        "normativeRef",
        "quantifier",
    )
    source_rules = source["rules"]
    assert isinstance(source_rules, list)
    rules = [
        {field: rule[field] for field in allowed_fields}
        for rule in sorted(source_rules, key=lambda item: str(item["ruleId"]))
    ]
    projection: dict[str, JsonValue] = {
        "apiVersion": source["apiVersion"],
        "registryKind": "NormativeSemanticRuleRegistry",
        "rules": rules,
    }
    digest = f"sha256:{hashlib.sha256(rfc8785.dumps(projection)).hexdigest()}"
    return {**projection, "digest": digest}


def runtime_lowering_semantic_validation_rules_json() -> dict[str, JsonValue]:
    """Describe semantic validators owned only by the runtime-lowering extension."""

    rules: list[dict[str, JsonValue]] = []
    for source in _RUNTIME_LOWERING_SEMANTIC_RULES:
        rule = dict(source)
        validator = str(rule.pop("runtimeValidator"))
        rule["ruleVersion"] = "1.0.0"
        rule["normativeRef"] = "standard/oac-runtime-lowering-v0.1.md"
        rule["quantifier"] = (
            "for_each_runtime_step"
            if rule["scope"] == "graph_integrity"
            else "for_each_resource"
        )
        rule["implementationBindings"] = {
            "python": {"validator": validator},
        }
        rules.append(rule)
    return {
        "apiVersion": "oac.dev/v0alpha1",
        "registryKind": "RuntimeLoweringSemanticValidationRuleRegistry",
        "jsonSchema": {
            "dialect": "https://json-schema.org/draft/2020-12/schema",
            "validationScope": "structural_only",
        },
        "semanticValidation": {
            "requiredAfterJsonSchema": True,
            "normativeProcedureRef": "standard/oac-runtime-lowering-v0.1.md",
        },
        "implementationBindings": {
            "python": {
                "cli": "oac validate RESOURCE",
                "library": "oac.canonical.parse_resource",
            }
        },
        "rules": rules,
    }


def normative_runtime_lowering_semantic_validation_rules_json() -> dict[str, JsonValue]:
    """Return an implementation-neutral, digest-bound lowering-rule projection."""

    source = runtime_lowering_semantic_validation_rules_json()
    allowed_fields = (
        "ruleId",
        "ruleVersion",
        "appliesToKinds",
        "scope",
        "jsonSchemaEnforced",
        "failureReasonCode",
        "description",
        "normativeRef",
        "quantifier",
    )
    source_rules = source["rules"]
    assert isinstance(source_rules, list)
    rules = [
        {field: rule[field] for field in allowed_fields}
        for rule in sorted(source_rules, key=lambda item: str(item["ruleId"]))
    ]
    projection: dict[str, JsonValue] = {
        "apiVersion": source["apiVersion"],
        "registryKind": "NormativeRuntimeLoweringSemanticRuleRegistry",
        "rules": rules,
    }
    digest = f"sha256:{hashlib.sha256(rfc8785.dumps(projection)).hexdigest()}"
    return {**projection, "digest": digest}


def _filesystem_candidates(value: str) -> tuple[Path, ...]:
    path = Path(value)
    if path.is_absolute():
        return (path,)
    candidates = (Path.cwd() / path, REPOSITORY_ROOT / path)
    return tuple(dict.fromkeys(candidate.resolve(strict=False) for candidate in candidates))


def _packaged_bytes(value: str) -> bytes:
    resource_path = PurePosixPath(value)
    if resource_path.is_absolute() or ".." in resource_path.parts:
        raise FileNotFoundError(f"unsafe package resource path: {value}")
    resource = files("oac").joinpath("resources", *resource_path.parts)
    if not resource.is_file():
        raise FileNotFoundError(f"package resource does not exist: {value}")
    return resource.read_bytes()


def _read_bytes(value: str, *, package_first: bool = False) -> bytes:
    errors: list[OSError] = []
    if package_first:
        try:
            return _packaged_bytes(value)
        except OSError as exc:
            errors.append(exc)
    for candidate in _filesystem_candidates(value):
        try:
            return candidate.read_bytes()
        except OSError as exc:
            errors.append(exc)
    if not package_first:
        try:
            return _packaged_bytes(value)
        except OSError as exc:
            errors.append(exc)
    detail = errors[-1] if errors else FileNotFoundError(value)
    raise FileNotFoundError(f"no filesystem or packaged resource for {value}: {detail}")


def _read_tck_bytes(value: str, *, package_first: bool = False) -> bytes:
    try:
        return _read_bytes(value, package_first=package_first)
    except OSError as exc:
        raise TCKManifestError(f"unreadable TCK fixture {value}: {exc}") from exc


def _run_tck_case(case: dict[str, Any], *, package_first: bool) -> dict[str, Any]:
    expected = case["expect"]
    try:
        if case["operation"] == "validate":
            resource = parse_resource(
                _read_tck_bytes(case["resourceRef"], package_first=package_first)
            )
            if case.get("verifyDigest", False):
                verify_resource_digest(resource)
            actual_outcome = "PASS"
            actual_verdict = None
            actual_codes: set[str] = set()
        elif case["operation"] == "verify":
            snapshot = parse_resource(
                _read_tck_bytes(case["snapshotRef"], package_first=package_first)
            )
            change = parse_resource(_read_tck_bytes(case["changeRef"], package_first=package_first))
            plan = parse_resource(_read_tck_bytes(case["planRef"], package_first=package_first))
            if (
                not isinstance(snapshot, OrganizationSnapshot)
                or not isinstance(change, SemanticChangeSet)
                or not isinstance(plan, OrganizationPlan)
            ):
                raise OACValidationError("CORE_SCHEMA_INVALID", "invalid verify fixture kinds")
            certificate = verify_plan(snapshot, change, plan)
            actual_outcome = "PASS"
            actual_verdict = certificate.spec.verdict.value
            actual_codes = set(certificate.spec.reason_codes)
        else:
            raise TCKManifestError(f"unknown TCK operation: {case['operation']}")
    except OACValidationError as exc:
        actual_outcome = "ERROR"
        actual_verdict = None
        actual_codes = {exc.reason_code}

    expected_codes = set(expected.get("reasonCodes", []))
    code_match = (
        actual_codes == expected_codes
        if expected.get("match", "exact") == "exact"
        else expected_codes.issubset(actual_codes)
    )
    passed = (
        actual_outcome == expected["outcome"]
        and (expected.get("verdict") is None or actual_verdict == expected["verdict"])
        and code_match
    )
    return {
        "id": case["id"],
        "passed": passed,
        "actual": {
            "outcome": actual_outcome,
            "verdict": actual_verdict,
            "reasonCodes": sorted(actual_codes),
        },
    }


def _validate_tck_case_shape(case: object, *, index: int) -> None:
    if not isinstance(case, dict):
        raise TCKManifestError(f"TCK case {index} must be an object")
    case_id = case.get("id")
    operation = case.get("operation")
    expected = case.get("expect")
    if not isinstance(case_id, str) or not case_id:
        raise TCKManifestError(f"TCK case {index} has an invalid id")
    if operation not in {"validate", "verify"}:
        raise TCKManifestError(f"unknown TCK operation: {operation}")
    if not isinstance(expected, dict):
        raise TCKManifestError(f"TCK case {case_id} has an invalid expectation")
    if expected.get("outcome") not in {"PASS", "ERROR"}:
        raise TCKManifestError(f"TCK case {case_id} has an invalid outcome")
    reason_codes = expected.get("reasonCodes")
    if not isinstance(reason_codes, list) or not all(
        isinstance(item, str) for item in reason_codes
    ):
        raise TCKManifestError(f"TCK case {case_id} has invalid reasonCodes")
    if expected.get("match", "exact") not in {"exact", "contains"}:
        raise TCKManifestError(f"TCK case {case_id} has an invalid match mode")
    if "verdict" in expected and not isinstance(expected["verdict"], str):
        raise TCKManifestError(f"TCK case {case_id} has an invalid verdict")
    required_refs = (
        ("resourceRef",) if operation == "validate" else ("snapshotRef", "changeRef", "planRef")
    )
    if any(not isinstance(case.get(key), str) or not case[key] for key in required_refs):
        raise TCKManifestError(f"TCK case {case_id} has an invalid fixture reference")
    if (
        operation == "validate"
        and "verifyDigest" in case
        and not isinstance(case["verifyDigest"], bool)
    ):
        raise TCKManifestError(f"TCK case {case_id} has an invalid verifyDigest flag")


def _run_tck(manifest_path: str | None) -> tuple[dict[str, Any], int]:
    package_first = manifest_path is None
    manifest_path = manifest_path or "tck/manifest.json"
    try:
        manifest = json.loads(_read_tck_bytes(manifest_path, package_first=package_first))
        if (
            not isinstance(manifest, dict)
            or not isinstance(manifest.get("suiteId"), str)
            or not isinstance(manifest.get("claimLimit"), str)
            or not isinstance(manifest.get("cases"), list)
        ):
            raise TCKManifestError("TCK manifest is missing suiteId, claimLimit, or cases")
        for index, case in enumerate(manifest["cases"]):
            _validate_tck_case_shape(case, index=index)
    except TCKManifestError:
        raise
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise TCKManifestError(f"invalid TCK manifest: {exc}") from exc
    results = [_run_tck_case(case, package_first=package_first) for case in manifest["cases"]]
    passed = sum(result["passed"] for result in results)
    summary = {
        "suiteId": manifest["suiteId"],
        "claimLimit": manifest["claimLimit"],
        "passed": passed,
        "failed": len(results) - passed,
        "results": results,
    }
    return summary, 0 if passed == len(results) else 1


def _run_demo() -> dict[str, Any]:
    snapshot_path = "profiles/supplier-change/inputs/veracier-proc01.snapshot.json"
    change_path = "profiles/supplier-change/inputs/SC-001.change.json"
    snapshot = parse_resource(
        _read_tck_bytes(snapshot_path, package_first=True), verify_digest=True
    )
    change = parse_resource(_read_tck_bytes(change_path, package_first=True), verify_digest=True)
    if not isinstance(snapshot, OrganizationSnapshot) or not isinstance(change, SemanticChangeSet):
        raise OACValidationError("CORE_SCHEMA_INVALID", "SC-001 fixture kinds are invalid")
    plan = compile_supplier_change(snapshot, change)
    certificate = verify_plan(snapshot, change, plan)
    return {
        "caseId": "SC-001",
        "planDigest": plan.digest,
        "planStatus": plan.spec.status,
        "obligationCount": len(plan.spec.obligations),
        "certificateDigest": certificate.digest,
        "verdict": certificate.spec.verdict.value,
        "effectCeiling": plan.spec.effect_ceiling.value,
    }


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            if args.verify_digest:
                resource = _load_admitted(args.resource).resource
            else:
                _, resource = _load_unsealed(args.resource)
            _write({"valid": True, "kind": resource.kind}, None)
        elif args.command == "validate-evolution":
            admission = _load_admitted(args.resource)
            resource = admission.resource
            if isinstance(resource, OrganizationalDemand):
                if args.snapshot is None:
                    raise OACValidationError(
                        "EVOLUTION_ROOT_INCOMPLETE",
                        "OrganizationalDemand validation requires --snapshot",
                    )
                snapshot_admission = _load_admitted(args.snapshot)
                if not isinstance(snapshot_admission.resource, OrganizationSnapshot):
                    raise OACValidationError(
                        "EVOLUTION_REF_KIND_MISMATCH",
                        "--snapshot must be OrganizationSnapshot",
                    )
                verify_organizational_demand_from_admitted(
                    admission, snapshot_admission,
                )
            elif isinstance(resource, SourceAdmissionReceipt):
                verify_source_admission_receipt_from_admitted(admission)
            elif isinstance(resource, OutcomeCertificate):
                verify_outcome_certificate_from_admitted(admission)
            else:
                raise OACValidationError(
                    "EVOLUTION_REF_KIND_MISMATCH",
                    "validate-evolution accepts only Spec 009 kinds",
                )
            _write({"valid": True, "kind": resource.kind}, None)
        elif args.command == "digest":
            decoded, _ = _load_unsealed(args.resource)
            digest = "sha256:" + hashlib.sha256(_jcs(canonical_projection(decoded))).hexdigest()
            _write({"digest": digest}, None)
        elif args.command == "compile":
            _write(
                compile_change_from_admitted(
                    _load_admitted(args.snapshot, "OrganizationSnapshot"),
                    _load_admitted(args.change, "SemanticChangeSet"),
                    profile=args.profile,
                ),
                args.output,
            )
        elif args.command == "verify":
            _write(
                verify_change_from_admitted(
                    _load_admitted(args.snapshot, "OrganizationSnapshot"),
                    _load_admitted(args.change, "SemanticChangeSet"),
                    _load_admitted(args.plan, "OrganizationPlan"),
                    profile=args.profile,
                ),
                args.output,
            )
        elif args.command == "lower":
            result = lower_plan_from_admitted(
                _load_lowering_admitted(args.snapshot, "OrganizationSnapshot"),
                _load_lowering_admitted(args.change, "SemanticChangeSet"),
                _load_lowering_admitted(args.plan, "OrganizationPlan"),
                _load_lowering_admitted(args.certificate, "PlanCertificate"),
                _load_lowering_admitted(args.binding, "RuntimeBinding"),
                admitted_binding_digests=args.admitted_binding_digests,
            )
            _write(result.as_json(), args.output)
        elif args.command == "registry":
            registries = {
                "kinds": kind_registry_json,
                "predicate-versions": predicate_version_registry_json,
                "reasons": reason_registry_json,
                "semantic-validation-rules": semantic_validation_rules_json,
                "normative-semantic-validation-rules": (
                    normative_semantic_validation_rules_json
                ),
                "runtime-lowering-semantic-validation-rules": (
                    runtime_lowering_semantic_validation_rules_json
                ),
                "normative-runtime-lowering-semantic-validation-rules": (
                    normative_runtime_lowering_semantic_validation_rules_json
                ),
                "evolution-semantic-validation-rules": (
                    evolution_semantic_validation_rules_json
                ),
            }
            _write(registries[args.name](), None)
        elif args.command == "tck":
            summary, exit_code = _run_tck(args.manifest)
            _write(summary, None)
            return exit_code
        elif args.command == "demo":
            _write(_run_demo(), None)
        return 0
    except (OACValidationError, CompilationError, TCKManifestError) as exc:
        sys.stderr.write(
            json.dumps(
                {"ok": False, "reasonCode": exc.reason_code, "message": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
