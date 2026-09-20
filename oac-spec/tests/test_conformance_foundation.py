from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from urllib.parse import quote

import pytest

from oac.canonical import OACValidationError, parse_resource, seal_resource
from oac.compiler import compile_supplier_change
from oac.derivation import profile_derivation_report
from oac.identifiers import (
    coverage_obligation_identifier,
    evaluation_identifier,
    impact_path_identifier,
    instance_identifier,
)
from oac.models import OrganizationSnapshot, SemanticChangeSet
from oac.resource_profile import (
    SUPPLIER_RESOURCE_PROFILE,
    ResourceProfileExceeded,
    enforce_path_prefix_budget,
)
from oac.supplier import derive_supplier_contract
from oac.witness import format_witness_ref, parse_witness_ref

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "profiles/supplier-change/inputs/veracier-proc01-contextual.snapshot.json"
CHANGE = ROOT / "profiles/supplier-change/inputs/SC-008.change.json"
OAC_WHITE_SPACE = tuple(
    chr(code_point)
    for code_point in (
        *range(0x0009, 0x000E),
        0x0020,
        0x0085,
        0x00A0,
        0x1680,
        *range(0x2000, 0x200B),
        0x2028,
        0x2029,
        0x202F,
        0x205F,
        0x3000,
    )
)


def _roots() -> tuple[OrganizationSnapshot, SemanticChangeSet]:
    snapshot = parse_resource(SNAPSHOT.read_bytes(), verify_digest=True)
    change = parse_resource(CHANGE.read_bytes(), verify_digest=True)
    assert isinstance(snapshot, OrganizationSnapshot)
    assert isinstance(change, SemanticChangeSet)
    return snapshot, change


def test_legacy_identifier_golden_vectors_freeze_complete_preimages() -> None:
    root_one = "sha256:" + ("1" * 64)
    root_two = "sha256:" + ("2" * 64)
    assert (
        evaluation_identifier(
            snapshot_digest=root_one,
            change_digest=root_two,
            change_subject_ref="urn:node:s",
            source_ref="urn:edge:e",
            predicate_version="oac.supplier.applicability/v0.2",
            result="TRUE",
            reason_codes=(),
            witness_refs=("urn:snapshot:a#/digest",),
        )
        == "urn:oac:mvp:evaluation:d524b0634a87729776af5d48"
    )
    assert (
        impact_path_identifier(
            snapshot_digest=root_one,
            change_digest=root_two,
            target_ref="urn:node:t",
            state="affected",
            edge_refs=("urn:edge:e",),
            rule_refs=(),
            evaluation_refs=("urn:oac:mvp:evaluation:abc",),
            duty_refs=(),
            origin="dependency",
            truncated=False,
        )
        == "urn:oac:mvp:path:99d177ea82e8882a9c9b802c"
    )
    assert (
        coverage_obligation_identifier(
            snapshot_digest=root_one,
            change_digest=root_two,
            origin="dependency_path",
            target_ref="urn:node:t",
            required_role_ref="urn:role:r",
            obligation_type="assess",
            resolution_state="affected",
            path_refs=("urn:oac:mvp:path:abc",),
        )
        == "urn:oac:mvp:obligation:8ac3bf19985c4df2e3d05429"
    )


def test_full_instance_ids_are_kind_separated_and_suffix_collision_resistant() -> None:
    roots = {
        "snapshot_digest": "sha256:" + ("1" * 64),
        "change_digest": "sha256:" + ("2" * 64),
    }
    left = instance_identifier(
        "role-instance",
        **roots,
        body={
            "roleDefinitionRef": "urn:a:reviewer",
            "principalRef": "urn:p:one",
            "obligationRefs": ["urn:o:1"],
        },
    )
    right = instance_identifier(
        "role-instance",
        **roots,
        body={
            "roleDefinitionRef": "urn:b:reviewer",
            "principalRef": "urn:p:one",
            "obligationRefs": ["urn:o:1"],
        },
    )
    work = instance_identifier(
        "work-unit",
        **roots,
        body={
            "roleInstanceRefs": [left],
            "accountableRoleInstanceRef": left,
            "obligationRefs": ["urn:o:1"],
        },
    )
    assert left != right != work
    assert left == (
        "urn:oac:id:sha256:v1:role-instance:"
        "3032a9b81c1fb0704fb0079f1fd4a0bc6d4b21b10e52de746d5145bae490b33e"
    )


def test_reference_compiler_emits_v1_instance_identifiers() -> None:
    snapshot, change = _roots()
    plan = compile_supplier_change(snapshot, change)
    ids = (
        *(item.role_instance_id for item in plan.spec.role_instances),
        *(item.work_unit_id for item in plan.spec.work_units),
        *(item.decision_id for item in plan.spec.decisions),
    )
    assert ids
    assert all(value.startswith("urn:oac:id:sha256:v1:") for value in ids)


def test_witness_grammar_round_trips_reserved_and_unicode_values() -> None:
    resource_id = "urn:oac:knowledge#quality/%/供应商"
    pointer = "/spec/a~1b/~0token/#/%/值"
    encoded = format_witness_ref(resource_id, pointer)
    assert encoded == (
        "urn:oac:knowledge%23quality/%25/%E4%BE%9B%E5%BA%94%E5%95%86#"
        "/spec/a~1b/~0token/%23/%25/%E5%80%BC"
    )
    assert parse_witness_ref(encoded) == (resource_id, pointer)
    assert format_witness_ref("urn:snapshot:a", "/spec/nodes/0") == ("urn:snapshot:a#/spec/nodes/0")


def test_witness_non_blank_semantics_use_frozen_unicode_white_space() -> None:
    for value in (*OAC_WHITE_SPACE, "".join(OAC_WHITE_SPACE)):
        with pytest.raises(OACValidationError) as encoded:
            format_witness_ref(value, "")
        assert encoded.value.reason_code == "APPLICABILITY_WITNESS_MISMATCH"
        with pytest.raises(OACValidationError) as decoded:
            parse_witness_ref(f"{quote(value, safe='')}#")
        assert decoded.value.reason_code == "APPLICABILITY_WITNESS_MISMATCH"

    for discriminator in ("\u001c", "\u001d", "\u001e", "\u001f", "\ufeff"):
        witness_ref = format_witness_ref(discriminator, "")
        assert parse_witness_ref(witness_ref) == (discriminator, "")


@pytest.mark.parametrize(
    "value",
    (
        "urn:a%2fb#/spec",
        "urn:a%2Fb#/bad~2token",
        "urn:a#fragment#/spec",
        "urn:a#not-a-pointer",
    ),
)
def test_witness_parser_rejects_ambiguous_or_noncanonical_spellings(value: str) -> None:
    with pytest.raises(OACValidationError) as exc_info:
        parse_witness_ref(value)
    assert exc_info.value.reason_code == "APPLICABILITY_WITNESS_MISMATCH"


def test_resource_profile_is_machine_readable_and_exhaustion_is_not_domain_unknown() -> None:
    published = json.loads(
        (ROOT / "profiles/supplier-change/conformance-resource-profile-v1.json").read_bytes()
    )
    assert published == SUPPLIER_RESOURCE_PROFILE.as_json()
    with pytest.raises(ResourceProfileExceeded) as exc_info:
        enforce_path_prefix_budget(SUPPLIER_RESOURCE_PROFILE.max_path_prefixes + 1)
    assert exc_info.value.reason_code == "CONFORMANCE_RESOURCE_PROFILE_EXCEEDED"

    snapshot, change = _roots()
    completeness = snapshot.spec.completeness.model_copy(
        update={"max_depth": SUPPLIER_RESOURCE_PROFILE.max_semantic_depth + 1}
    )
    oversized = seal_resource(
        snapshot.model_copy(
            update={
                "digest": None,
                "spec": snapshot.spec.model_copy(update={"completeness": completeness}),
            }
        )
    )
    with pytest.raises(ResourceProfileExceeded):
        derive_supplier_contract(oversized, change)


def test_derivation_report_is_deterministic_and_topology_free() -> None:
    snapshot, change = _roots()
    first = profile_derivation_report(snapshot, change)
    second = profile_derivation_report(snapshot, change)
    assert first == second
    wire = first.model_dump(mode="json", by_alias=True)
    assert wire["snapshotDigest"] == snapshot.digest
    assert wire["changeDigest"] == change.digest
    assert "roleInstances" not in wire
    assert "workUnits" not in wire
    assert "decisions" not in wire
    assert wire["applicabilityEvaluations"]
    assert wire["impactPaths"]


def test_derivation_report_rejects_a_contract_from_different_roots() -> None:
    snapshot, change = _roots()
    derived = derive_supplier_contract(snapshot, change)
    mismatched = replace(derived, snapshot_digest="sha256:" + ("0" * 64))
    with pytest.raises(ValueError, match="roots do not match"):
        profile_derivation_report(snapshot, change, derived=mismatched)
