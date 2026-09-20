from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import rfc8785
from test_plan_verification_capsule import _adapter
from test_runtime_lowering import _binding

from oac.benchmark import _graph_only_roles, _subject_owner
from oac.canonical import OACValidationError, canonical_bytes, parse_resource, seal_resource
from oac.change_profiles import (
    RETAIL_PROFILE,
    SUPPLIER_PROFILE,
    get_change_profile,
    parse_change_profile,
)
from oac.compiler import CompilationError, compile_change, compile_supplier_change
from oac.ctk_adapter_v2 import handle
from oac.derivation import profile_derivation_report
from oac.lowering import lower_plan
from oac.models import OrganizationPlan, OrganizationSnapshot, SemanticChangeSet, Verdict
from oac.sealed import admit_sealed_resource
from oac.supplier import derive_change_contract, derive_supplier_contract
from oac.verifier import verify_change, verify_change_from_admitted, verify_plan

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/retail-cancellation"
DESCRIPTOR = ROOT / "profiles/change-profiles/retail-cancellation-review-v0.1/profile.json"


def _roots() -> tuple[OrganizationSnapshot, SemanticChangeSet]:
    snapshot = parse_resource((FIXTURE / "snapshot.json").read_bytes(), verify_digest=True)
    change = parse_resource((FIXTURE / "change.json").read_bytes(), verify_digest=True)
    assert isinstance(snapshot, OrganizationSnapshot)
    assert isinstance(change, SemanticChangeSet)
    return snapshot, change


def _reseal(raw: dict) -> dict:
    value = copy.deepcopy(raw)
    value.pop("digest", None)
    value["digest"] = "sha256:" + hashlib.sha256(rfc8785.dumps(value)).hexdigest()
    return value


def test_retail_profile_uses_shared_core_and_binds_plan_and_certificate() -> None:
    snapshot, change = _roots()
    plan = compile_change(snapshot, change, profile=RETAIL_PROFILE)
    certificate = verify_change(snapshot, change, plan, profile=RETAIL_PROFILE)
    assert certificate.kind == "PlanCertificate"
    assert certificate.spec.verdict is Verdict.ACCEPT
    binding = get_change_profile(RETAIL_PROFILE).binding
    assert binding is not None
    assert plan.spec.profile_binding == certificate.spec.profile_binding == binding
    assert plan.spec.effect_ceiling.value == "zero_effect"
    assert len(plan.spec.obligations) == 3
    assert len(plan.spec.work_units) == 2
    assert {item.role_definition_ref for item in plan.spec.role_instances} == {
        "role:review",
        "role:cancel",
    }
    assert (
        _subject_owner(snapshot, change) == _graph_only_roles(snapshot, change) == {"role:review"}
    )
    assert not plan.spec.happens_before  # The proposed mapping declares no prerequisites.


def test_retail_plan_round_trips_through_exact_raw_admission() -> None:
    snapshot, change = _roots()
    plan = compile_change(snapshot, change, profile=RETAIL_PROFILE)
    admissions = tuple(
        admit_sealed_resource(value.model_dump_json(by_alias=True).encode(), value.kind)
        for value in (snapshot, change, plan)
    )
    certificate = verify_change_from_admitted(*admissions, profile=RETAIL_PROFILE)
    assert certificate.spec.verdict is Verdict.ACCEPT
    assert certificate.spec.subject_plan_ref.digest == plan.digest
    assert certificate.spec.profile_binding == plan.spec.profile_binding
    assert canonical_bytes(certificate) == canonical_bytes(
        verify_change(snapshot, change, plan, profile=RETAIL_PROFILE)
    )


@pytest.mark.parametrize("case", ("SC-008", "SC-009", "SC-010"))
def test_selected_delta_reaches_edges_rules_and_unknown_duties(case: str) -> None:
    inputs = ROOT / "profiles/supplier-change/inputs"
    snapshot_name = (
        "veracier-proc01-truncated-contextual.snapshot.json"
        if case == "SC-010"
        else "veracier-proc01-contextual.snapshot.json"
    )
    snapshot = parse_resource((inputs / snapshot_name).read_bytes(), verify_digest=True)
    change = parse_resource((inputs / f"{case}.change.json").read_bytes(), verify_digest=True)
    assert isinstance(snapshot, OrganizationSnapshot)
    assert isinstance(change, SemanticChangeSet)
    baseline = derive_supplier_contract(snapshot, change)

    # A declared counterfactual tests path propagation, not a retail task mapping.
    def rebind(value: object) -> object:
        if isinstance(value, dict):
            return {
                key: "retail.cancel_requested" if key == "semanticType" else rebind(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [rebind(item) for item in value]
        return value

    snapshot_map = rebind(snapshot.model_dump(mode="json", by_alias=True))
    change_map = rebind(change.model_dump(mode="json", by_alias=True))
    assert isinstance(snapshot_map, dict) and isinstance(change_map, dict)
    change_map["spec"]["deltas"][0]["path"] = "/request"
    rebound_snapshot = parse_resource(_reseal(snapshot_map), verify_digest=True)
    rebound_change = parse_resource(_reseal(change_map), verify_digest=True)
    assert isinstance(rebound_snapshot, OrganizationSnapshot)
    assert isinstance(rebound_change, SemanticChangeSet)
    derived = derive_change_contract(rebound_snapshot, rebound_change, profile=RETAIL_PROFILE)
    assert sorted(
        (item.source_ref, item.result, item.reason_codes)
        for item in derived.applicability_evaluations
    ) == sorted(
        (item.source_ref, item.result, item.reason_codes)
        for item in baseline.applicability_evaluations
    )
    assert derived.root_applicability_unknown == baseline.root_applicability_unknown
    plan = compile_change(rebound_snapshot, rebound_change, profile=RETAIL_PROFILE)
    assert (
        verify_change(rebound_snapshot, rebound_change, plan, profile=RETAIL_PROFILE).spec.verdict
        == verify_plan(snapshot, change, compile_supplier_change(snapshot, change)).spec.verdict
    )


def test_unknown_retail_request_remains_guarded() -> None:
    snapshot, change = _roots()
    delta = change.spec.deltas[0]
    unknown_delta = delta.model_copy(
        update={
            "operation": "unknown_transition",
            "after": delta.after.model_copy(update={"state": "unknown", "value": None}),
        }
    )
    unknown_change = seal_resource(
        change.model_copy(
            update={"spec": change.spec.model_copy(update={"deltas": (unknown_delta,)})}
        )
    )
    plan = compile_change(snapshot, unknown_change, profile=RETAIL_PROFILE)
    certificate = verify_change(snapshot, unknown_change, plan, profile=RETAIL_PROFILE)
    assert plan.spec.status == "guarded_unresolved"
    assert certificate.spec.verdict is Verdict.UNKNOWN
    assert "activation_prohibited" in certificate.spec.restrictions
    assert plan.spec.effect_ceiling.value == "zero_effect"
    assert certificate.spec.profile_binding == plan.spec.profile_binding


@pytest.mark.parametrize(
    "field,value",
    (
        ("profileId", "oac.forged"),
        ("profileVersion", "v2"),
        ("semanticType", "supplier.status"),
        ("deltaPath", "/status"),
        ("effectCeiling", "write"),
        ("knownValueType", "any"),
        ("unknownValuePolicy", "assume-known"),
        ("validator", "eval"),
    ),
)
def test_descriptor_is_closed_and_cannot_rebind_semantics(field: str, value: str) -> None:
    raw = json.loads(DESCRIPTOR.read_bytes())
    raw[field] = value
    with pytest.raises(OACValidationError, match="invalid closed change profile") as error:
        parse_change_profile(json.dumps(raw).encode())
    assert error.value.reason_code == "CHANGE_PROFILE_INVALID"


def test_descriptor_digest_and_duplicate_key_boundary() -> None:
    raw = DESCRIPTOR.read_bytes()
    binding = get_change_profile(RETAIL_PROFILE).binding
    assert binding is not None
    descriptor = parse_change_profile(raw)
    assert (
        "sha256:" + hashlib.sha256(rfc8785.dumps(json.loads(raw))).hexdigest()
        == binding.profile_digest
    )
    assert descriptor.delta_path == "/request"
    with pytest.raises(OACValidationError) as error:
        parse_change_profile(raw.replace(b'"kind":', b'"kind":"ChangeProfile","kind":'))
    assert error.value.reason_code == "CHANGE_PROFILE_INVALID"


@pytest.mark.parametrize("profile", ("unknown", "", "oac.retail.cancellation-review/v0.2"))
def test_unknown_profiles_fail_closed(profile: str) -> None:
    snapshot, change = _roots()
    plan = compile_change(snapshot, change, profile=RETAIL_PROFILE)
    for operation in (
        lambda: compile_change(snapshot, change, profile=profile),
        lambda: verify_change(snapshot, change, plan, profile=profile),
    ):
        with pytest.raises(OACValidationError) as error:
            operation()
        assert error.value.reason_code == "CHANGE_PROFILE_UNKNOWN"


@pytest.mark.parametrize("mutation", ("remove", "id", "version", "digest"))
def test_resealed_plan_cannot_reuse_accept_across_profiles(mutation: str) -> None:
    snapshot, change = _roots()
    original = compile_change(snapshot, change, profile=RETAIL_PROFILE)
    binding = original.spec.profile_binding
    assert binding is not None
    if mutation == "remove":
        changed_binding = None
    else:
        field, value = {
            "id": ("profile_id", "oac.supplier.transfer"),
            "version": ("profile_version", "v0.2"),
            "digest": ("profile_digest", "sha256:" + "0" * 64),
        }[mutation]
        changed_binding = binding.model_copy(update={field: value})
    changed = seal_resource(
        original.model_copy(
            update={"spec": original.spec.model_copy(update={"profile_binding": changed_binding})}
        )
    )
    certificate = verify_change(snapshot, change, changed, profile=RETAIL_PROFILE)
    assert certificate.spec.verdict is Verdict.REJECT
    assert "CHANGE_PROFILE_BINDING_MISMATCH" in certificate.spec.reason_codes
    assert certificate.spec.profile_binding == original.spec.profile_binding
    assert (
        certificate.digest
        != verify_change(snapshot, change, original, profile=RETAIL_PROFILE).digest
    )


def test_default_supplier_api_cannot_accept_retail_or_relabel_supplier_report() -> None:
    snapshot, change = _roots()
    plan = compile_change(snapshot, change, profile=RETAIL_PROFILE)
    with pytest.raises(CompilationError) as error:
        compile_supplier_change(snapshot, change)
    assert error.value.reason_code == "UNSUPPORTED_SEMANTICS"
    certificate = verify_plan(snapshot, change, plan)
    assert certificate.spec.verdict is Verdict.REJECT
    assert "CHANGE_PROFILE_BINDING_MISMATCH" in certificate.spec.reason_codes
    assert "profileBinding" not in certificate.model_dump(mode="json", by_alias=True)["spec"]
    with pytest.raises(ValueError, match="cannot relabel"):
        profile_derivation_report(
            snapshot,
            change,
            derived=derive_change_contract(
                snapshot,
                change,
                profile=RETAIL_PROFILE,
            ),
        )


def test_retail_accept_does_not_expand_the_default_zero_effect_lowerer() -> None:
    snapshot, change = _roots()
    plan = compile_change(snapshot, change, profile=RETAIL_PROFILE)
    certificate = verify_change(snapshot, change, plan, profile=RETAIL_PROFILE)
    binding = _binding(snapshot, plan, certificate)
    result = lower_plan(
        snapshot, change, plan, certificate, binding, admitted_binding_digests={binding.digest}
    )
    assert result.bundle is None
    assert result.receipt.spec.status.value == "BLOCKED"


def test_frozen_adapter_checks_resource_depth_before_the_extension_field() -> None:
    snapshot, change = _roots()
    plan = compile_change(snapshot, change, profile=RETAIL_PROFILE)
    raw = plan.model_dump(mode="json", by_alias=True)
    nested: object = "leaf"
    for _ in range(70):
        nested = [nested]
    raw["spec"]["profileBinding"]["overdeep"] = nested
    encoded_plan = base64.b64encode(rfc8785.dumps(_reseal(raw))).decode()
    response = handle(
        {
            "protocolVersion": "oac.ctk.stdio/v2",
            "requestId": "profile-depth",
            "operation": "validateResource",
            "payload": {"expectedKind": "OrganizationPlan", "rawBase64": encoded_plan},
        }
    )
    assert response["sutStatus"] == "RESOURCE_EXHAUSTED"


@pytest.mark.parametrize(
    "mutation,code",
    (
        ("wrong-path", "CHANGE_PROFILE_DELTA_SET_INVALID"),
        ("extra-delta", "CHANGE_PROFILE_DELTA_SET_INVALID"),
        ("semantic-type", "UNSUPPORTED_SEMANTICS"),
        ("known-unknown", "SYNTHETIC_UNKNOWN_VALUE"),
        ("known-bool", "UNSUPPORTED_SEMANTICS"),
        ("source", "CORE_SCHEMA_INVALID"),
    ),
)
def test_retail_source_and_delta_boundaries(mutation: str, code: str) -> None:
    snapshot, change = _roots()
    raw = change.model_dump(mode="json", by_alias=True)
    if mutation == "wrong-path":
        raw["spec"]["deltas"][0]["path"] = "/status"
    elif mutation == "extra-delta":
        extra = copy.deepcopy(raw["spec"]["deltas"][0])
        extra["path"] = "/other"
        raw["spec"]["deltas"].append(extra)
    elif mutation == "semantic-type":
        raw["spec"]["semanticType"] = "supplier.status"
    elif mutation in {"known-unknown", "known-bool"}:
        raw["spec"]["deltas"][0]["after"]["value"] = (
            "unknown" if mutation == "known-unknown" else True
        )
    else:
        raw["spec"]["sourceRef"] = "source:replacement"
        with pytest.raises(OACValidationError) as schema_error:
            parse_resource(_reseal(raw), verify_digest=True)
        assert schema_error.value.reason_code == code
        return
    changed = parse_resource(_reseal(raw), verify_digest=True)
    assert isinstance(changed, SemanticChangeSet)
    with pytest.raises(CompilationError) as error:
        compile_change(snapshot, changed, profile=RETAIL_PROFILE)
    assert error.value.reason_code == code


def test_resealed_coherent_source_replacement_still_invalidates_old_plan() -> None:
    snapshot, change = _roots()
    plan = compile_change(snapshot, change, profile=RETAIL_PROFILE)
    replacement = "source:replacement"
    changed = seal_resource(
        change.model_copy(
            update={
                "metadata": change.metadata.model_copy(update={"source_refs": (replacement,)}),
                "spec": change.spec.model_copy(update={"source_ref": replacement}),
            }
        )
    )
    result = verify_change(snapshot, changed, plan, profile=RETAIL_PROFILE)
    assert result.spec.verdict is Verdict.REJECT
    assert "INPUT_ROOT_MISMATCH" in result.spec.reason_codes


@pytest.mark.parametrize("binding", ("retail", "null"))
def test_frozen_supplier_adapters_keep_original_wire_boundary(binding: str) -> None:
    snapshot, change = _roots()
    plan = compile_change(snapshot, change, profile=RETAIL_PROFILE)
    raw = plan.model_dump(mode="json", by_alias=True)
    if binding == "null":
        raw["spec"]["profileBinding"] = None
    encoded_plan = base64.b64encode(rfc8785.dumps(_reseal(raw))).decode()
    response = handle(
        {
            "protocolVersion": "oac.ctk.stdio/v2",
            "requestId": "profile-boundary",
            "operation": "validateResource",
            "payload": {"expectedKind": "OrganizationPlan", "rawBase64": encoded_plan},
        }
    )
    assert response["sutStatus"] == "ERROR"
    assert response["error"]["code"] == "CORE_SCHEMA_INVALID"
    response = _adapter().handle(
        {
            "protocolVersion": "oac.ctk.stdio/v3",
            "requestId": "profile-boundary",
            "operation": "verifyPlan",
            "payload": {
                "snapshotBase64": base64.b64encode(
                    snapshot.model_dump_json(by_alias=True).encode()
                ).decode(),
                "changeBase64": base64.b64encode(
                    change.model_dump_json(by_alias=True).encode()
                ).decode(),
                "planBase64": encoded_plan,
            },
        }
    )
    assert response["sutStatus"] == "ERROR"
    assert response["error"]["code"] == "CORE_SCHEMA_INVALID"


def test_cli_requires_explicit_profile_and_keeps_public_certificate_kind(tmp_path: Path) -> None:
    plan_path, certificate_path = tmp_path / "plan.json", tmp_path / "certificate.json"
    roots = [str(FIXTURE / "snapshot.json"), str(FIXTURE / "change.json")]
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "oac", *args], env=env, capture_output=True, check=False
        )

    default = run("compile", *roots, "-o", str(plan_path))
    assert default.returncode != 0 and not plan_path.exists()
    assert run("compile", *roots, "--profile", RETAIL_PROFILE, "-o", str(plan_path)).returncode == 0
    assert (
        run(
            "verify",
            *roots,
            str(plan_path),
            "--profile",
            RETAIL_PROFILE,
            "-o",
            str(certificate_path),
        ).returncode
        == 0
    )
    certificate = json.loads(certificate_path.read_bytes())
    assert certificate["kind"] == "PlanCertificate"
    assert certificate["spec"]["verdict"] == "ACCEPT"
    assert run("verify", *roots, str(plan_path), "-o", str(certificate_path)).returncode == 0
    assert json.loads(certificate_path.read_bytes())["spec"]["verdict"] == "REJECT"
    assert run("verify", *roots, str(plan_path), "--profile", "unknown").returncode == 2


@pytest.mark.parametrize(
    "case,snapshot_file,plan_hash,certificate_hash",
    (
        (
            "SC-001",
            "veracier-proc01.snapshot.json",
            "f66e1ad2b4086cc73ac78f6e965e7486acf2a183e52f689795c6282e2371836e",
            "048d83e406dcfed7fa542e86df2d2ddc108bf3ff659bea668fcc296b2bf20570",
        ),
        (
            "SC-008",
            "veracier-proc01-contextual.snapshot.json",
            "cc9948ef3a869b279924ca5f1e0161145aad51b3ea470fa21659fd5ef2176dea",
            "3f0be0947d7cef81ba58df012f8ada2a017aa913186357bb75656c6a6ada90a5",
        ),
        (
            "SC-009",
            "veracier-proc01-contextual.snapshot.json",
            "ea53dfa7169be30e581f0783d040a0fd5d49c491b311eda27c4d693fca55f9f1",
            "e213aa5d82bda2e606ee08a1564d249244d3aa08a2068b89f066fed4b1088af4",
        ),
        (
            "SC-010",
            "veracier-proc01-truncated-contextual.snapshot.json",
            "fe137cfc0e407cab0fc64d582873ce24337f139fb251554ec9c3f92383198b16",
            "08d7fc706065c000c7faf4d151ea4c0747cd174741dd3a3b1564270d585908e6",
        ),
    ),
)
def test_original_supplier_plan_and_certificate_bytes_remain_identical(
    case: str,
    snapshot_file: str,
    plan_hash: str,
    certificate_hash: str,
) -> None:
    inputs = ROOT / "profiles/supplier-change/inputs"
    snapshot = parse_resource((inputs / snapshot_file).read_bytes(), verify_digest=True)
    change = parse_resource((inputs / f"{case}.change.json").read_bytes(), verify_digest=True)
    assert isinstance(snapshot, OrganizationSnapshot)
    assert isinstance(change, SemanticChangeSet)
    plan = compile_supplier_change(snapshot, change)
    assert isinstance(plan, OrganizationPlan)
    certificate = verify_plan(snapshot, change, plan)
    assert plan.digest == "sha256:" + plan_hash
    assert certificate.digest == "sha256:" + certificate_hash
    assert canonical_bytes(
        compile_change(snapshot, change, profile=SUPPLIER_PROFILE)
    ) == canonical_bytes(plan)
    for value in (plan, certificate):
        assert "profileBinding" not in value.model_dump(mode="json", by_alias=True)["spec"]
