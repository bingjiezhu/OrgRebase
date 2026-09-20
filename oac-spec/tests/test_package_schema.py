from __future__ import annotations

import ast
import copy
import hashlib
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import rfc8785
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as JsonSchemaValidationError

from oac.canonical import OACValidationError, parse_resource
from oac.cli import (
    main,
    normative_runtime_lowering_semantic_validation_rules_json,
    normative_semantic_validation_rules_json,
    runtime_lowering_semantic_validation_rules_json,
    semantic_validation_rules_json,
)
from oac.evolution import evolution_semantic_validation_rules_json
from oac.models import CONTEXTUAL_PREDICATE_VERSION, LEGACY_PREDICATE_VERSION
from oac.registry import predicate_version_registry_json

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
SNAPSHOT_PATH = (
    ROOT / "profiles" / "supplier-change" / "inputs" / "veracier-proc01.snapshot.json"
)


@pytest.fixture(scope="module")
def generated_schemas(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output = tmp_path_factory.mktemp("exported-schemas")
    completed = subprocess.run(
        [sys.executable, "scripts/export_schemas.py", "--output", str(output)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return output


def _generated_json(output: Path, name: str) -> dict[str, object]:
    return json.loads((output / name).read_bytes())


def _snapshot_vector() -> dict[str, object]:
    return json.loads(SNAPSHOT_PATH.read_bytes())


def _assert_schema_accepts_runtime_rejects(
    generated_schemas: Path, vector: dict[str, object]
) -> None:
    schema = _generated_json(generated_schemas, "OrganizationSnapshot.schema.json")
    Draft202012Validator(schema).validate(vector)
    with pytest.raises(OACValidationError) as exc_info:
        parse_resource(json.dumps(vector, ensure_ascii=False))
    assert exc_info.value.reason_code == "CORE_SCHEMA_INVALID"


def test_every_exported_schema_declares_and_conforms_to_draft_2020_12(
    generated_schemas: Path,
) -> None:
    for path in generated_schemas.glob("*.schema.json"):
        schema = json.loads(path.read_bytes())
        assert schema["$schema"] == SCHEMA_DIALECT
        Draft202012Validator.check_schema(schema)


def test_semantic_registry_makes_the_validation_layer_boundary_machine_readable(
    generated_schemas: Path,
) -> None:
    registry = semantic_validation_rules_json()
    assert registry["jsonSchema"] == {
        "dialect": SCHEMA_DIALECT,
        "validationScope": "structural_only",
    }
    semantic = registry["semanticValidation"]
    assert semantic["requiredAfterJsonSchema"] is True  # type: ignore[index]
    rule_ids = {rule["ruleId"] for rule in registry["rules"]}  # type: ignore[union-attr]
    assert {"OAC-SEM-002", "OAC-SEM-003", "OAC-SEM-004"}.issubset(rule_ids)
    for rule in registry["rules"]:  # type: ignore[union-attr]
        assert "runtimeValidator" not in rule
        assert rule["ruleVersion"] == "1.0.0"
        assert rule["normativeRef"] == (
            "standard/oac-core-v0.1.md#semantic-validation-rules"
        )
        assert isinstance(rule["quantifier"], str)
        assert rule["implementationBindings"]["python"]["validator"].startswith(  # type: ignore[index]
            "oac.models."
        )
    assert registry["semanticValidation"] == {
        "requiredAfterJsonSchema": True,
        "normativeProcedureRef": "standard/oac-core-v0.1.md#semantic-validation-rules",
    }

    index = _generated_json(generated_schemas, "index.json")
    assert index["schemaDialect"] == SCHEMA_DIALECT
    assert "semantic-validation-rules.json" in index["registries"]  # type: ignore[operator]
    assert "normative-semantic-validation-rules.json" in index["registries"]  # type: ignore[operator]
    assert _generated_json(
        generated_schemas, "normative-semantic-validation-rules.json"
    ) == normative_semantic_validation_rules_json()


def test_predicate_versions_and_component_schemas_are_machine_readable(
    generated_schemas: Path,
) -> None:
    registry = predicate_version_registry_json()
    assert registry["registryKind"] == "PredicateVersionRegistry"
    versions = {
        entry["predicateVersion"]
        for entry in registry["entries"]  # type: ignore[union-attr]
    }
    assert versions == {LEGACY_PREDICATE_VERSION, CONTEXTUAL_PREDICATE_VERSION}

    exported_registry = _generated_json(
        generated_schemas, "predicate-version-registry.json"
    )
    assert exported_registry == registry
    index = _generated_json(generated_schemas, "index.json")
    assert "predicate-version-registry.json" in index["registries"]  # type: ignore[operator]
    assert {
        "ApplicabilityEvaluation.schema.json",
        "ContextualApplicabilityPredicate.schema.json",
        "ProfileDerivationReport.schema.json",
        "UnknownTransitionDuty.schema.json",
    }.issubset(index["schemas"])  # type: ignore[arg-type]


def test_runtime_lowering_kinds_and_schemas_are_machine_readable(
    generated_schemas: Path,
) -> None:
    kind_registry = _generated_json(generated_schemas, "kind-registry.json")
    entries = {item["kind"]: item for item in kind_registry["entries"]}  # type: ignore[union-attr]
    assert entries["RuntimeBinding"]["documentClass"] == "source"
    assert entries["ZeroEffectRuntimeBundle"]["documentClass"] == "plan"
    assert entries["RuntimeLoweringReceipt"]["documentClass"] == "evidence"
    index = _generated_json(generated_schemas, "index.json")
    assert {
        "RuntimeBinding.schema.json",
        "RuntimeLoweringReceipt.schema.json",
        "ZeroEffectRuntimeBundle.schema.json",
    }.issubset(index["schemas"])  # type: ignore[arg-type]


def test_contextual_predicate_schema_is_structural_and_strict(
    generated_schemas: Path,
) -> None:
    schema = _generated_json(
        generated_schemas, "ContextualApplicabilityPredicate.schema.json"
    )
    validator = Draft202012Validator(schema)
    validator.validate(
        {
            "predicateVersion": CONTEXTUAL_PREDICATE_VERSION,
            "semanticType": "supplier.status",
            "afterState": "known",
            "afterValues": ["qualified"],
            "subjectSelector": {
                "subjectRefs": ["alternative:acieries-savoie"],
                "nodeTypes": ["supplier-alternative"],
                "domainRefs": ["domain:quality"],
            },
            "scopeSelector": {
                "refs": ["production:aero"],
                "matchMode": "all",
                "missingBehavior": "unknown",
            },
            "relationTypes": ["business_dependency"],
        }
    )
    with pytest.raises(JsonSchemaValidationError):
        validator.validate(
            {
                "predicateVersion": CONTEXTUAL_PREDICATE_VERSION,
                "semanticType": "supplier.status",
                "afterState": "known",
                "tags": ["unversioned-extension"],
            }
        )


def test_semantic_registry_covers_every_model_validator() -> None:
    tree = ast.parse((ROOT / "src" / "oac" / "models.py").read_text(encoding="utf-8"))
    model_validators = {
        f"oac.models.{class_node.name}.{function.name}"
        for class_node in tree.body
        if isinstance(class_node, ast.ClassDef)
        for function in class_node.body
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Name)
            and decorator.func.id == "model_validator"
            for decorator in function.decorator_list
        )
    }
    registered = {
        rule["implementationBindings"]["python"]["validator"]
        for rule in semantic_validation_rules_json()["rules"]  # type: ignore[union-attr]
    }
    registered.update(
        rule["implementationBindings"]["python"]["validator"]
        for rule in runtime_lowering_semantic_validation_rules_json()["rules"]  # type: ignore[union-attr]
    )
    assert model_validators == registered


def test_runtime_lowering_semantic_registry_is_separate_and_digest_bound(
    generated_schemas: Path,
) -> None:
    development = runtime_lowering_semantic_validation_rules_json()
    normative = normative_runtime_lowering_semantic_validation_rules_json()
    assert development["registryKind"] == (
        "RuntimeLoweringSemanticValidationRuleRegistry"
    )
    assert {rule["ruleId"] for rule in development["rules"]} == {  # type: ignore[union-attr]
        "OAC-RL-SEM-001",
        "OAC-RL-SEM-002",
        "OAC-RL-SEM-003",
        "OAC-RL-SEM-004",
        "OAC-RL-SEM-005",
        "OAC-RL-SEM-006",
    }
    assert _generated_json(
        generated_schemas, "runtime-lowering-semantic-validation-rules.json"
    ) == development
    assert _generated_json(
        generated_schemas,
        "normative-runtime-lowering-semantic-validation-rules.json",
    ) == normative
    projection = {key: value for key, value in normative.items() if key != "digest"}
    assert normative["digest"] == (
        f"sha256:{hashlib.sha256(rfc8785.dumps(projection)).hexdigest()}"
    )
    index = _generated_json(generated_schemas, "index.json")
    assert index["validationScope"]["extensionSemanticValidation"] == [  # type: ignore[index]
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
            "normativeProcedureRef": "standard/oac-runtime-lowering-v0.1.md",
        },
        {
            "kinds": [
                "OrganizationalDemand",
                "OutcomeCertificate",
                "SourceAdmissionReceipt",
            ],
            "developmentRegistry": "evolution-semantic-validation-rules.json",
            "normativeProcedureRef": (
                "specs/009-proof-carrying-evolution-minimum-profile/spec.md"
            ),
        },
    ]
    assert _generated_json(
        generated_schemas, "evolution-semantic-validation-rules.json"
    ) == evolution_semantic_validation_rules_json()


def test_duplicate_ids_pass_structure_but_fail_runtime_semantics(
    generated_schemas: Path,
) -> None:
    vector = _snapshot_vector()
    nodes = vector["spec"]["nodes"]  # type: ignore[index]
    nodes.append(copy.deepcopy(nodes[0]))  # type: ignore[union-attr,index]
    _assert_schema_accepts_runtime_rejects(generated_schemas, vector)


def test_dangling_reference_passes_structure_but_fails_runtime_semantics(
    generated_schemas: Path,
) -> None:
    vector = _snapshot_vector()
    vector["spec"]["nodes"][0]["ownerRoleRef"] = "role:not-declared"  # type: ignore[index]
    _assert_schema_accepts_runtime_rejects(generated_schemas, vector)


def test_complete_with_known_gaps_passes_structure_but_fails_runtime_semantics(
    generated_schemas: Path,
) -> None:
    vector = _snapshot_vector()
    completeness = vector["spec"]["completeness"]  # type: ignore[index]
    completeness["status"] = "complete"  # type: ignore[index]
    completeness["knownGaps"] = ["gap:unresolved"]  # type: ignore[index]
    _assert_schema_accepts_runtime_rejects(generated_schemas, vector)


def test_cli_exposes_registry_and_enforces_semantics(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    assert main(["registry", "predicate-versions"]) == 0
    predicate_registry = json.loads(capfd.readouterr().out)
    assert predicate_registry == predicate_version_registry_json()

    assert main(["registry", "semantic-validation-rules"]) == 0
    registry = json.loads(capfd.readouterr().out)
    assert registry["registryKind"] == "SemanticValidationRuleRegistry"

    vector = _snapshot_vector()
    nodes = vector["spec"]["nodes"]  # type: ignore[index]
    nodes.append(copy.deepcopy(nodes[0]))  # type: ignore[union-attr,index]
    resource = tmp_path / "duplicate-node.snapshot.json"
    resource.write_text(json.dumps(vector), encoding="utf-8")
    assert main(["validate", str(resource)]) == 2
    error = json.loads(capfd.readouterr().err)
    assert error["reasonCode"] == "CORE_SCHEMA_INVALID"


def test_wheel_configuration_owns_runtime_assets_and_full_apache_text() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["project"]["license-files"] == ["LICENSES/Apache-2.0.txt"]
    force_include = config["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert force_include == {
        "LICENSES/Apache-2.0.txt": "oac/resources/LICENSES/Apache-2.0.txt",
        "schemas": "oac/resources/schemas",
        "profiles/enterprise-intake": "oac/resources/profiles/enterprise-intake",
        "profiles/change-profiles": "oac/resources/profiles/change-profiles",
        "profiles/human-annotation": "oac/resources/profiles/human-annotation",
        "profiles/outcome-profiles": "oac/resources/profiles/outcome-profiles",
        "profiles/supplier-change/annotations": "oac/resources/profiles/supplier-change/annotations",
        "profiles/supplier-change/cases": "oac/resources/profiles/supplier-change/cases",
        "profiles/supplier-change/datasets": "oac/resources/profiles/supplier-change/datasets",
        "profiles/supplier-change/inputs": "oac/resources/profiles/supplier-change/inputs",
        "profiles/supplier-change/source-labels": "oac/resources/profiles/supplier-change/source-labels",
        "profiles/supplier-change/witnesses": "oac/resources/profiles/supplier-change/witnesses",
        "profiles/supplier-change/conformance-resource-profile-v1.json": "oac/resources/profiles/supplier-change/conformance-resource-profile-v1.json",
        "tck": "oac/resources/tck",
    }
