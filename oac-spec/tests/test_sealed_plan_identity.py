from __future__ import annotations

import copy
import hashlib
from dataclasses import replace

import pytest
import rfc8785
from test_compiler_verifier import _contextual_roots
from test_plan_verification_capsule import _adapter

from oac.canonical import OACValidationError, calculate_digest
from oac.compiler import compile_supplier_change
from oac.models import OrganizationPlan, OrganizationSnapshot, SemanticChangeSet
from oac.sealed import admit_sealed_resource, validate_sealed_admission
from oac.supplier import derive_supplier_contract_from_admitted
from oac.verifier import verify_plan, verify_plan_from_admitted

KINDS = {"snapshot": "OrganizationSnapshot", "change": "SemanticChangeSet", "plan": "OrganizationPlan"}


def _seal(raw):
    value = copy.deepcopy(raw)
    value.pop("digest", None)
    value["digest"] = "sha256:" + hashlib.sha256(rfc8785.dumps(value)).hexdigest()
    return value


def _inputs():
    snapshot, change = _contextual_roots("SC-008")
    plan = compile_supplier_change(snapshot, change)
    return {name: value.model_dump(mode="json", by_alias=True)
            for name, value in zip(KINDS, (snapshot, change, plan), strict=True)}


def _admit(inputs):
    return tuple(admit_sealed_resource(rfc8785.dumps(inputs[name]), kind) for name, kind in KINDS.items())


def _with_omission(inputs, name, path):
    result = copy.deepcopy(inputs)
    parent = result[name]
    for part in path[:-1]:
        parent = parent[part]
    del parent[path[-1]]
    result[name] = _seal(result[name])
    if name != "plan":
        result["plan"]["spec"][name + "Ref"]["digest"] = result[name]["digest"]
        result["plan"] = _seal(result["plan"])
    return result


@pytest.mark.parametrize("name,path", (
    ("snapshot", ("metadata", "effectiveTo")),
    ("change", ("metadata", "effectiveTo")),
    ("change", ("spec", "deltas", 0, "beforeVersion")),
    ("plan", ("metadata", "effectiveFrom")),
    ("plan", ("metadata", "effectiveTo")),
))
def test_omitted_optional_fields_preserve_exact_admitted_identity_through_plan_kernel(name, path):
    inputs = _inputs()
    baseline = verify_plan_from_admitted(*_admit(inputs))
    omitted = _with_omission(inputs, name, path)
    admissions = _admit(omitted)
    result = verify_plan_from_admitted(*admissions)
    dimensions = {item.name: item for item in result.spec.dimensions}
    assert dimensions["integrity"].verdict.value == "PASS"
    assert dimensions["input_roots"].verdict.value == "PASS"
    if name == "plan":
        assert result.spec.verdict == baseline.spec.verdict
        assert result.spec.reason_codes == baseline.spec.reason_codes
    else:
        # Source digests also occur in applicability/witness identifiers. The
        # old plan must still fail semantic bindings until rebuilt for new roots.
        assert result.spec.verdict.value == "REJECT"
        assert {"APPLICABILITY_EVALUATION_MISMATCH", "OBLIGATION_SET_MISMATCH"}.issubset(result.spec.reason_codes)
    assert result.spec.snapshot_ref.digest == omitted["snapshot"]["digest"]
    assert result.spec.change_ref.digest == omitted["change"]["digest"]
    assert result.spec.subject_plan_ref.digest == omitted["plan"]["digest"]
    selected = admissions[list(KINDS).index(name)]
    assert calculate_digest(selected.resource) != selected.resource_digest
    assert "PLAN_DIGEST_MISMATCH" not in result.spec.reason_codes
    assert "ROOT_DIGEST_MISMATCH" not in result.spec.reason_codes
    # Explicit null and omitted members have different identities, even where
    # that metadata does not change the bounded semantic verdict.
    assert inputs[name]["digest"] != omitted[name]["digest"]


def test_existing_fully_materialized_typed_plan_keeps_exact_certificate_bytes():
    admissions = _admit(_inputs())
    snapshot, change, plan = (item.resource for item in admissions)
    assert isinstance(snapshot, OrganizationSnapshot)
    assert isinstance(change, SemanticChangeSet)
    assert isinstance(plan, OrganizationPlan)
    legacy = verify_plan(snapshot, change, plan)
    raw = verify_plan_from_admitted(*admissions)
    assert raw.model_dump_json(by_alias=True) == legacy.model_dump_json(by_alias=True)


@pytest.mark.parametrize("attack", ("claim", "raw", "typed", "defaults", "kind"))
def test_forged_admission_cannot_bypass_exact_identity(attack):
    inputs = _with_omission(_inputs(), "snapshot", ("metadata", "effectiveTo"))
    snapshot, change, plan = _admit(inputs)
    if attack == "claim":
        snapshot = replace(snapshot, resource_digest="sha256:" + "0" * 64)
    elif attack == "raw":
        raw = copy.deepcopy(inputs["snapshot"])
        raw["metadata"]["id"] = "snapshot:forged"
        snapshot = replace(snapshot, raw_map=raw)
    elif attack == "typed":
        altered = snapshot.resource.model_copy(update={"metadata": snapshot.resource.metadata.model_copy(update={"id": "snapshot:forged"})})
        snapshot = replace(snapshot, resource=altered)
    elif attack == "defaults":
        full = snapshot.resource.model_dump(mode="json", by_alias=True)
        snapshot = replace(snapshot, resource=OrganizationSnapshot.model_validate_json(rfc8785.dumps(full)))
    else:
        snapshot = replace(snapshot, resource=plan.resource)
    with pytest.raises(OACValidationError):
        verify_plan_from_admitted(snapshot, change, plan)
    with pytest.raises(OACValidationError):
        derive_supplier_contract_from_admitted(snapshot, change)


def test_admission_revalidation_detaches_typed_mutable_views():
    snapshot = _admit(_inputs())[0]
    checked = validate_sealed_admission(snapshot, "OrganizationSnapshot")
    assert checked.resource is not snapshot.resource
    assert checked.resource_digest == snapshot.resource_digest


def test_real_stdio_v3_adapter_keeps_raw_omission_through_shared_plan_verifier():
    import base64

    inputs = _with_omission(_inputs(), "plan", ("metadata", "effectiveFrom"))
    response = _adapter().handle({"protocolVersion": "oac.ctk.stdio/v3", "requestId": "raw-identity",
        "operation": "verifyPlan", "payload": {
            name + "Base64": base64.b64encode(rfc8785.dumps(value)).decode("ascii")
            for name, value in inputs.items()}})
    assert response["sutStatus"] == "COMPLETED"
    expected = verify_plan_from_admitted(*_admit(inputs))
    assert response["result"]["verdict"] == expected.spec.verdict.value
    assert response["result"]["reasonCodes"] == list(expected.spec.reason_codes)
    assert response["result"]["planDigest"] == inputs["plan"]["digest"]


def _write_inputs(tmp_path, inputs):
    paths = []
    for name, value in inputs.items():
        path = tmp_path / f"{name}.json"
        path.write_bytes(rfc8785.dumps(value))
        paths.append(str(path))
    return paths


def _cli(*args):
    import json
    import subprocess
    import sys

    completed = subprocess.run([sys.executable, "-c", "from oac.cli import main; raise SystemExit(main())", *args],
                               capture_output=True, check=False, timeout=30)
    assert completed.returncode == 0, completed.stderr.decode()
    return json.loads(completed.stdout)


def test_public_cli_verify_validate_digest_preserve_exact_raw_plan(tmp_path):
    inputs = _with_omission(_inputs(), "plan", ("metadata", "effectiveFrom"))
    paths = _write_inputs(tmp_path, inputs)
    certificate = _cli("verify", *paths)
    assert certificate["spec"]["verdict"] == "ACCEPT"
    assert certificate["spec"]["subjectPlanRef"]["digest"] == inputs["plan"]["digest"]
    assert _cli("validate", paths[-1], "--verify-digest") == {"valid": True, "kind": "OrganizationPlan"}
    assert _cli("digest", paths[-1]) == {"digest": inputs["plan"]["digest"]}


@pytest.mark.parametrize("fresh_certificate", (True, False))
def test_public_cli_lower_admits_raw_plan_and_binding_but_requires_exact_certificate(tmp_path, fresh_certificate):
    from test_runtime_lowering import _binding

    from oac.canonical import resource_ref, seal_resource

    initial = _inputs()
    original = _admit(initial)
    original_certificate = verify_plan_from_admitted(*original)
    original_binding = _binding(original[0].resource, original[2].resource, original_certificate)
    inputs = _with_omission(initial, "plan", ("metadata", "effectiveFrom"))
    certificate = verify_plan_from_admitted(*_admit(inputs)) if fresh_certificate else original_certificate
    plan_ref = original_binding.spec.subject_plan_ref.model_copy(update={"digest": inputs["plan"]["digest"]})
    binding = seal_resource(original_binding.model_copy(update={"spec": original_binding.spec.model_copy(
        update={"subject_plan_ref": plan_ref, "subject_certificate_ref": resource_ref(certificate)})}))
    inputs["certificate"] = certificate.model_dump(mode="json", by_alias=True)
    raw_binding = binding.model_dump(mode="json", by_alias=True)
    del raw_binding["metadata"]["effectiveTo"]
    inputs["binding"] = _seal(raw_binding)
    paths = _write_inputs(tmp_path, inputs)
    result = _cli("lower", *paths, "--admit-binding-digest", inputs["binding"]["digest"])
    assert result["receipt"]["spec"]["subjectPlanRef"]["digest"] == inputs["plan"]["digest"]
    assert result["receipt"]["spec"]["runtimeBindingRef"]["digest"] == inputs["binding"]["digest"]
    assert result["receipt"]["spec"]["runtimeInvoked"] is False
    if fresh_certificate:
        assert result["receipt"]["spec"]["status"] == "PRODUCED"
        assert result["bundle"]["spec"]["targetWrites"] == 0
    else:
        assert result["receipt"]["spec"]["status"] == "BLOCKED"
        assert "LOWERING_CERTIFICATE_MISMATCH" in result["receipt"]["spec"]["reasonCodes"]
        assert "bundle" not in result
