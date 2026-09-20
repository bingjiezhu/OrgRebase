#!/usr/bin/env python3
"""Export deterministic Draft 2020-12 JSON Schemas and registries."""

from __future__ import annotations

import argparse
import json
import runpy
from pathlib import Path

from oac.annotation import REASON_CODES as ANNOTATION_REASON_CODES
from oac.annotation_models import ANNOTATION_MODELS, AnnotationArchive
from oac.change_profiles import (
    ChangeProfileDescriptor,
    change_profile_semantic_validation_rules_json,
)
from oac.cli import (
    normative_runtime_lowering_semantic_validation_rules_json,
    normative_semantic_validation_rules_json,
    runtime_lowering_semantic_validation_rules_json,
    semantic_validation_rules_json,
)
from oac.enterprise_intake import (
    IntakeAuthoritySpec,
    IntakeExtensionSpec,
    IntakeManifest,
    IntakeProfileSpec,
    _Envelope,
    intake_admission_rules,
)
from oac.evolution import evolution_semantic_validation_rules_json
from oac.models import (
    ApplicabilityEvaluation,
    ContextualApplicabilityPredicate,
    ProfileDerivationReport,
    UnknownTransitionDuty,
)
from oac.outcome_profiles import (
    OutcomeProfileDescriptor,
    outcome_profile_semantic_validation_rules_json,
)
from oac.registry import (
    KIND_MODELS,
    KIND_REGISTRY,
    kind_registry_json,
    predicate_version_registry_json,
    reason_registry_json,
)

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
AUXILIARY_MODELS = {
    "OutcomeProfileDescriptor.schema.json": OutcomeProfileDescriptor,
    "ChangeProfileDescriptor.schema.json": ChangeProfileDescriptor,
    "ApplicabilityEvaluation.schema.json": ApplicabilityEvaluation,
    "ContextualApplicabilityPredicate.schema.json": ContextualApplicabilityPredicate,
    "ProfileDerivationReport.schema.json": ProfileDerivationReport,
    "UnknownTransitionDuty.schema.json": UnknownTransitionDuty,
}


def _render(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def expected_files() -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for kind, model in sorted(KIND_MODELS.items()):
        schema = model.model_json_schema(by_alias=True, mode="validation")
        schema["$schema"] = SCHEMA_DIALECT
        schema["$id"] = f"https://oac.dev/schemas/v0alpha1/{KIND_REGISTRY[kind].schema_file}"
        files[KIND_REGISTRY[kind].schema_file] = _render(schema)
    for name, model in sorted(AUXILIARY_MODELS.items()):
        schema = model.model_json_schema(by_alias=True, mode="validation")
        schema["$schema"] = SCHEMA_DIALECT
        schema["$id"] = f"https://oac.dev/schemas/v0alpha1/{name}"
        files[name] = _render(schema)
    files["kind-registry.json"] = _render(kind_registry_json())
    files["predicate-version-registry.json"] = _render(
        predicate_version_registry_json()
    )
    files["reason-code-registry.json"] = _render(reason_registry_json())
    files["semantic-validation-rules.json"] = _render(semantic_validation_rules_json())
    files["normative-semantic-validation-rules.json"] = _render(
        normative_semantic_validation_rules_json()
    )
    files["runtime-lowering-semantic-validation-rules.json"] = _render(
        runtime_lowering_semantic_validation_rules_json()
    )
    files["normative-runtime-lowering-semantic-validation-rules.json"] = _render(
        normative_runtime_lowering_semantic_validation_rules_json()
    )
    files["evolution-semantic-validation-rules.json"] = _render(
        evolution_semantic_validation_rules_json()
    )
    files["change-profile-semantic-validation-rules.json"] = _render(
        change_profile_semantic_validation_rules_json()
    )
    files["outcome-profile-semantic-validation-rules.json"] = _render(
        outcome_profile_semantic_validation_rules_json()
    )
    files["index.json"] = _render(
        {
            "apiVersion": "oac.dev/v0alpha1",
            "schemaDialect": SCHEMA_DIALECT,
            "validationScope": {
                "jsonSchema": "structural_only",
                "semanticValidationRequired": True,
                "normativeProcedureRef": "standard/oac-core-v0.1.md#semantic-validation-rules",
                "extensionSemanticValidation": [
                    {
                        "kinds": ["OutcomeCertificate"],
                        "appliesWhen": "spec.profileBinding is present",
                        "developmentRegistry": "outcome-profile-semantic-validation-rules.json",
                        "normativeProcedureRef": "profiles/outcome-profiles/disposable-local-v0.1/README.md",
                    },
                    {
                        "kinds": ["OrganizationPlan", "PlanCertificate"],
                        "appliesWhen": "spec.profileBinding is present",
                        "developmentRegistry": "change-profile-semantic-validation-rules.json",
                        "normativeProcedureRef": "profiles/change-profiles/retail-cancellation-review-v0.1/README.md",
                    },
                    {
                        "kinds": [
                            "RuntimeBinding",
                            "RuntimeLoweringReceipt",
                            "ZeroEffectRuntimeBundle",
                        ],
                        "developmentRegistry": (
                            "runtime-lowering-semantic-validation-rules.json"
                        ),
                        "normativeRegistry": (
                            "normative-runtime-lowering-semantic-validation-rules.json"
                        ),
                        "normativeProcedureRef": (
                            "standard/oac-runtime-lowering-v0.1.md"
                        ),
                    },
                    {
                        "kinds": [
                            "OrganizationalDemand",
                            "OutcomeCertificate",
                            "SourceAdmissionReceipt",
                        ],
                        "developmentRegistry": (
                            "evolution-semantic-validation-rules.json"
                        ),
                        "normativeProcedureRef": (
                            "specs/009-proof-carrying-evolution-minimum-profile/spec.md"
                        ),
                    },
                ],
            },
            "implementationBindings": {
                "python": {
                    "cli": "oac validate RESOURCE",
                    "library": "oac.canonical.parse_resource",
                }
            },
            "schemas": [
                *[KIND_REGISTRY[kind].schema_file for kind in sorted(KIND_REGISTRY)],
                *sorted(AUXILIARY_MODELS),
            ],
            "registries": [
                "kind-registry.json",
                "predicate-version-registry.json",
                "reason-code-registry.json",
                "semantic-validation-rules.json",
                "normative-semantic-validation-rules.json",
                "runtime-lowering-semantic-validation-rules.json",
                "normative-runtime-lowering-semantic-validation-rules.json",
                "evolution-semantic-validation-rules.json",
                "change-profile-semantic-validation-rules.json",
                "outcome-profile-semantic-validation-rules.json",
            ],
        }
    )
    return files


def intake_schemas() -> dict[str, dict]:
    """Export transport schemas separately from registered OAC resource Kinds."""
    schemas = {"IntakeManifest": IntakeManifest.model_json_schema(by_alias=True)}
    for name, kind, spec_model in (
        ("IntakeProfile", "IntakeProfile", IntakeProfileSpec),
        ("IntakeAuthority", "IntakeAuthority", IntakeAuthoritySpec),
        ("IntakeExtension", None, IntakeExtensionSpec),
    ):
        schema = _Envelope.model_json_schema(by_alias=True)
        spec = spec_model.model_json_schema(by_alias=True)
        for key, definition in spec.pop("$defs", {}).items():
            existing = schema.setdefault("$defs", {}).get(key)
            if existing is not None and existing != definition:
                raise ValueError(f"conflicting intake schema definition: {key}")
            schema["$defs"][key] = definition
        schema["properties"]["spec"] = spec
        schema["title"] = name
        if kind is not None:
            schema["properties"]["kind"]["const"] = kind
        schemas[name] = schema
    for name, schema in schemas.items():
        schema["$schema"] = SCHEMA_DIALECT
        schema["$id"] = f"https://oac.dev/schemas/enterprise-intake/v1/{name}.schema.json"
    return schemas


def annotation_files() -> dict[str, bytes]:
    """The annotation profile owns versioned extension documents, not core Kinds."""
    files = {}
    for kind, model in {**ANNOTATION_MODELS, "AnnotationArchive": AnnotationArchive}.items():
        schema = model.model_json_schema(by_alias=True)
        schema["$schema"] = SCHEMA_DIALECT
        schema["$id"] = f"https://oac.dev/schemas/annotation/v1/{kind}.schema.json"
        if kind != "AnnotationArchive":
            schema["properties"]["kind"]["const"] = kind
        files[f"{kind}.schema.json"] = _render(schema)
    files["registry.json"] = _render(
        {
            "protocol": "oac.annotation-document/v1",
            "owner": "Spec004/HumanAnnotationEvidence/v1",
            "canonicalization": "SealedResource RFC8785 decoded raw-map minus top-level digest",
            "kinds": sorted(ANNOTATION_MODELS),
            "structuralAdmissionIsAuthority": False,
            "authority": "separately caller-pinned governance, qualification bytes, authenticated subject and archive head",
            "reasonCodes": ANNOTATION_REASON_CODES,
            "semanticEntryPoint": "oac.annotation.AnnotationLedger",
            "archiveEntryPoint": "oac.annotation.restore_annotation_archive",
        }
    )
    return files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--output", type=Path, default=Path(__file__).resolve().parents[1] / "schemas"
    )
    args = parser.parse_args()
    expected = expected_files()
    stale: list[str] = []
    for name, payload in expected.items():
        path = args.output / name
        if args.check:
            if not path.exists() or path.read_bytes() != payload:
                stale.append(name)
        else:
            args.output.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
    repository = Path(__file__).resolve().parents[1]
    if args.output.resolve() == repository / "schemas":
        for name, payload in annotation_files().items():
            path = repository / "schemas/annotation/v1" / name
            if args.check:
                if not path.exists() or path.read_bytes() != payload:
                    stale.append(f"annotation/v1/{name}")
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
        rules_path = repository / "schemas/enterprise-intake/v1/admission-rules.json"
        rules_payload = _render(intake_admission_rules())
        if args.check:
            if not rules_path.exists() or rules_path.read_bytes() != rules_payload:
                stale.append("enterprise-intake/v1/admission-rules")
        else:
            rules_path.parent.mkdir(parents=True, exist_ok=True)
            rules_path.write_bytes(rules_payload)
        for name, schema in intake_schemas().items():
            path = repository / "schemas/enterprise-intake/v1" / f"{name}.schema.json"
            payload = _render(schema)
            if args.check:
                if not path.exists() or path.read_bytes() != payload:
                    stale.append(f"enterprise-intake/v1/{name}")
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
        namespace = runpy.run_path(str(repository / "ctk/runner/src/oac_ctk_runner/contracts.py"))
        previous = {path.name.removesuffix(".schema.json"): json.loads(path.read_bytes())
                    for path in (repository / "ctk/schemas").glob("*.schema.json")
                    if path.name.removesuffix(".schema.json") in {
                        "CTKBundle", "CapabilityStatement", "ConformanceCase", "ConformanceResourceProfile",
                        "DisagreementRecord", "RequirementSet", "RunResult"}}
        for name, schema in namespace["successor_schemas"](previous).items():
            path = repository / "ctk/schemas/v0alpha2" / f"{name}.schema.json"
            payload = _render(schema)
            if args.check:
                if not path.exists() or path.read_bytes() != payload:
                    stale.append(f"ctk/v0alpha2/{name}")
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
    if args.check and stale:
        parser.error(f"schema drift: {', '.join(stale)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
