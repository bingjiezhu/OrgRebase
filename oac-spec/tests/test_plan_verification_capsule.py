from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest
import rfc8785
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "experiments/plan-verification-portability/v0.1-seed-1"


def _digest(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    assert isinstance(value, dict)
    return value


def _verify_detached(path: Path, field: str) -> dict[str, Any]:
    value = _load(path)
    projection = {key: item for key, item in value.items() if key != field}
    assert value[field] == _digest(rfc8785.dumps(projection))
    return value


def _adapter() -> Any:
    path = ROOT / "implementations/python-plan-verifier-reference/adapter.py"
    specification = importlib.util.spec_from_file_location(
        "_oac_plan_verifier_adapter_test", path
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def _harness() -> Any:
    path = ROOT / "scripts/check_plan_verification_parity.py"
    specification = importlib.util.spec_from_file_location(
        "_oac_plan_verification_harness_test", path
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def _capsule_builder() -> Any:
    path = ROOT / "scripts/build_plan_verification_capsule.py"
    specification = importlib.util.spec_from_file_location(
        "_oac_plan_capsule_builder_test", path
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def test_plan_verification_contract_ledgers_are_detached_digest_bound() -> None:
    capability = _verify_detached(
        ROOT / "ctk/capabilities/plan-verification-v01-seed-1.capability-set.json",
        "capabilitySetDigest",
    )
    recipes = _verify_detached(
        ROOT / "ctk/generators/plan-verification-v01-seed-1.recipes.json",
        "recipeSetDigest",
    )
    requirements = _verify_detached(
        ROOT / "ctk/bundles/plan-verification-v01-seed-1.requirement-set.json",
        "requirementSetDigest",
    )
    assert capability["tracks"] == [
        {
            "role": "plan-verifier",
            "operation": "verifyPlan",
            "profileId": "oac.supplier.plan-verification.plural-capsule",
            "profileVersion": "v0.1-seed-1",
            "wireVersion": "oac.ctk.stdio/v3",
        }
    ]
    assert [item["caseId"] for item in recipes["cases"]] == [
        item["caseId"] for item in requirements["cases"]
    ]
    assert len(recipes["cases"]) == 39


def test_published_plan_capsule_is_admitted_without_global_reason_registry_rewrite() -> None:
    capsule = _capsule_builder().check()
    assert capsule["capsuleDigest"] == (
        "sha256:e9ca42382e0793fd108361c103d815c23cdb64385b6eccdac31dbe82676a17f5"
    )
    archived = (EXPERIMENT / "contracts/schemas/reason-code-registry.json").read_bytes()
    current = (ROOT / "schemas/reason-code-registry.json").read_bytes()
    assert archived != current


def test_capsule_proves_material_plurality_without_case_metadata_leak() -> None:
    capsule_path = EXPERIMENT / "capsule.json"
    raw = capsule_path.read_bytes()
    capsule = _verify_detached(capsule_path, "capsuleDigest")
    assert raw == rfc8785.dumps(capsule) + b"\n"
    plans = {
        item["caseId"]: _load(EXPERIMENT / item["plan"]["path"])
        for item in capsule["cases"]
    }
    base = plans["PV-POS-SC008-BASE"]
    split = plans["PV-POS-SC008-SPLIT"]
    base_partition = sorted(
        sorted(item["obligationRefs"]) for item in base["spec"]["workUnits"]
    )
    split_partition = sorted(
        sorted(item["obligationRefs"]) for item in split["spec"]["workUnits"]
    )
    assert base["spec"]["obligations"] == split["spec"]["obligations"]
    assert base_partition != split_partition
    assert len(base["spec"]["workUnits"]) == 3
    assert len(split["spec"]["workUnits"]) == 4
    recipe = _load(
        ROOT / "ctk/generators/plan-verification-v01-seed-1.recipes.json"
    )
    recipe_by_id = {item["caseId"]: item for item in recipe["cases"]}
    for case in capsule["cases"]:
        decoded = (EXPERIMENT / case["plan"]["path"]).read_text().lower()
        source = recipe_by_id[case["caseId"]]
        for forbidden in (
            case["caseId"],
            case["mutationClass"],
            source["transform"],
            source["sourcePlan"],
        ):
            assert forbidden.lower() not in decoded

        plan = plans[case["caseId"]]
        if plan["metadata"]["id"].startswith("urn:oac:fixture:plan-verification:"):
            old_case_derived_id = (
                "urn:oac:fixture:plan-verification:"
                + hashlib.sha256(f"20260824:{case['caseId']}".encode()).hexdigest()
            )
            assert plan["metadata"]["id"] != old_case_derived_id


def test_harness_rejects_unregistered_reason_codes() -> None:
    harness = _harness()
    schema = _load(ROOT / "ctk/schemas/PlanVerificationResult.schema.json")
    validator = Draft202012Validator(schema)
    digest = "sha256:" + "0" * 64
    response = {
        "protocolVersion": "oac.ctk.stdio/v3",
        "requestId": "opaque-unregistered-reason",
        "sutStatus": "COMPLETED",
        "result": {
            "snapshotDigest": digest,
            "changeDigest": digest,
            "planDigest": digest,
            "verdict": "REJECT",
            "reasonCodes": ["INPUT_ROOT_MISMATCH", "ZZZ_NOT_REGISTERED"],
        },
    }
    with pytest.raises(harness.HarnessFailure, match="unregistered reasonCodes"):
        harness._admit_response(
            response,
            "opaque-unregistered-reason",
            {
                "snapshotDigest": digest,
                "changeDigest": digest,
                "planDigest": digest,
            },
            validator,
            frozenset({"INPUT_ROOT_MISMATCH"}),
        )


def test_reference_black_box_satisfies_frozen_verdict_and_reason_policy() -> None:
    adapter = _adapter()
    capsule = _load(EXPERIMENT / "capsule.json")
    requirements = _load(
        ROOT / "ctk/bundles/plan-verification-v01-seed-1.requirement-set.json"
    )
    expected = {item["caseId"]: item for item in requirements["cases"]}
    for index, case in enumerate(capsule["cases"]):
        roots = capsule["rootSets"][case["rootSet"]]
        request = {
            "protocolVersion": "oac.ctk.stdio/v3",
            "requestId": f"opaque-test-{index}",
            "operation": "verifyPlan",
            "payload": {
                "snapshotBase64": base64.b64encode(
                    (EXPERIMENT / roots["snapshot"]["path"]).read_bytes()
                ).decode("ascii"),
                "changeBase64": base64.b64encode(
                    (EXPERIMENT / roots["change"]["path"]).read_bytes()
                ).decode("ascii"),
                "planBase64": base64.b64encode(
                    (EXPERIMENT / case["plan"]["path"]).read_bytes()
                ).decode("ascii"),
            },
        }
        response = adapter.handle(request)
        policy = expected[case["caseId"]]
        assert response["sutStatus"] == policy["sutStatus"]
        if policy["sutStatus"] == "COMPLETED":
            result = response["result"]
            assert result["verdict"] == policy["verdict"]
            reasons = set(result["reasonCodes"])
            assert set(policy["requiredReasonCodes"]) <= reasons
            assert not set(policy["forbiddenReasonCodes"]) & reasons
            if result["verdict"] == "ACCEPT":
                assert not reasons
        else:
            assert response["error"]["code"] == policy["errorCode"]


def test_plan_verification_rule_mutation_summary_is_exact_and_complete() -> None:
    path = EXPERIMENT / "rule-mutation-summary.json"
    raw = path.read_bytes()
    summary = _verify_detached(path, "summaryDigest")
    capsule = _load(EXPERIMENT / "capsule.json")
    requirements = _load(
        ROOT / "ctk/bundles/plan-verification-v01-seed-1.requirement-set.json"
    )
    assert raw == rfc8785.dumps(summary) + b"\n"
    assert summary["capsuleDigest"] == capsule["capsuleDigest"]
    assert summary["requirementSetDigest"] == requirements["requirementSetDigest"]
    assert summary["requiredCases"] == 39
    assert summary["baseline"] == {"required": 39, "passed": 39, "failed": 0}
    assert summary["sourceMutantsRequired"] == 14
    assert summary["sourceMutantsKilled"] == 14
    assert len(summary["mutants"]) == 14
    assert len({item["mutantId"] for item in summary["mutants"]}) == 14
    for mutant in summary["mutants"]:
        assert mutant["killed"] is True
        assert mutant["required"] == 39
        assert mutant["passed"] == 38
        assert mutant["failed"] == 1
        assert len(mutant["dedicatedKillCases"]) == 1
        assert mutant["failedCaseIds"] == mutant["dedicatedKillCases"]
        assert mutant["dedicatedKillVector"] == {
            mutant["dedicatedKillCases"][0]: True
        }
        assert mutant["changedSources"]
        for source in mutant["changedSources"]:
            assert source["beforeRawSha256"].startswith("sha256:")
            assert source["afterRawSha256"].startswith("sha256:")
            assert source["beforeRawSha256"] != source["afterRawSha256"]
    assert summary["excludedFromKillDenominator"] == [
        {
            "rule": "selected RoleDefinition admissionStatus is admitted",
            "classification": "EQUIVALENT_OR_UNREACHABLE_UNDER_SEED1",
            "reason": (
                "the Supplier seed-1 derivation requires an affected owner role to be admitted, "
                "and fixed-Plan qualification also binds every RoleInstance roleDefinitionRef "
                "to the derived obligation requiredRoleRef; no admitted coherent root isolates "
                "this conjunct from obligation projection and role matching"
            ),
        }
    ]


def test_plan_verification_evidence_manifest_is_frozen_and_self_consistent() -> None:
    path = ROOT / "scripts/build_plan_verification_evidence.py"
    specification = importlib.util.spec_from_file_location(
        "_oac_plan_evidence_test", path
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    manifest = module.check()
    assert manifest["matrix"] == {
        "requiredCases": 39,
        "acceptedTopologies": 2,
        "provisionalControls": 1,
        "unknownControls": 1,
        "completedRejectControls": 28,
        "admissionErrorControls": 7,
        "negativeControls": 35,
        "pythonPassed": 39,
        "goPassed": 39,
        "exactCrossImplementationObservations": 36,
        "permittedDiagnosticVariance": 3,
        "scoredIndeterminate": 0,
        "mutantsKilled": 3,
        "isolatedSourceMutantsKilled": 14,
        "sourceInstalledPortableProjectionEqual": True,
    }

    # The ordinary portable gate must permit only host command/interpreter
    # identity drift and must never rewrite its frozen comparison coordinate.
    harness = _harness()
    stored_path = EXPERIMENT / "parity-summary.json"
    frozen_raw = (EXPERIMENT / "parity-summary.json").read_bytes()
    live = copy.deepcopy(_load(stored_path))
    live["commands"]["python"] = {
        "argv": ["<external>/python3.99", "<external>/adapter.py"],
        "executableRawSha256": "sha256:" + "1" * 64,
        "entrypointRawSha256": "sha256:" + "2" * 64,
    }
    live["commands"]["go"] = {
        "argv": ["<fresh-go-build>/oac-go-plan-verifier"],
        "executableRawSha256": "sha256:" + "3" * 64,
        "entrypointRawSha256": None,
    }
    live["summaryDigest"] = harness._digest(rfc8785.dumps({
        key: value for key, value in live.items() if key != "summaryDigest"
    }))

    harness.check_stored_summary_portable(live, stored_path)

    assert stored_path.read_bytes() == frozen_raw

    # The same gate remains fail-closed for any field in the evidence builder's
    # portable semantic projection.
    live = copy.deepcopy(_load(stored_path))
    live["observations"]["exact"] -= 1

    with pytest.raises(
        harness.HarnessFailure,
        match="differs from the live portable projection",
    ):
        harness.check_stored_summary_portable(live, stored_path)
