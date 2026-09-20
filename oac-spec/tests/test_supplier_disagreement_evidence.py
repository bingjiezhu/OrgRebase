from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import rfc8785
from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_SCRIPT = ROOT / "scripts/capture_supplier_v02_disagreements.py"
INCIDENT_SCHEMA = ROOT / "ctk/schemas/DisagreementIncident.schema.json"
RESOLUTION_SCHEMA = ROOT / "ctk/schemas/DisagreementResolution.schema.json"
EVIDENCE_ROOT = ROOT / "experiments/supplier-v02-portability/v0.2-seed-2/disagreements"


def _load_evidence_module() -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "_test_supplier_disagreement_evidence", EVIDENCE_SCRIPT
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    # Direct script loading exercises its documented fallback import for the
    # adjacent frozen case generator without making `scripts` a package.
    sys.path.insert(0, str(EVIDENCE_SCRIPT.parent))
    try:
        specification.loader.exec_module(module)
    finally:
        sys.path.remove(str(EVIDENCE_SCRIPT.parent))
    return module


evidence = _load_evidence_module()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    assert isinstance(value, dict)
    return value


def _detached_digest(value: dict[str, Any]) -> str:
    projection = {key: item for key, item in value.items() if key != "digest"}
    return "sha256:" + hashlib.sha256(rfc8785.dumps(projection)).hexdigest()


def _incident_paths() -> list[Path]:
    return sorted(EVIDENCE_ROOT.glob("DIS-*.incident.json"))


def _resolution_paths() -> list[Path]:
    return sorted(EVIDENCE_ROOT.glob("DIS-*.resolution.json"))


def test_disagreement_schemas_are_closed_and_valid() -> None:
    for path in (INCIDENT_SCHEMA, RESOLUTION_SCHEMA):
        schema = _load(path)
        Draft202012Validator.check_schema(schema)
        assert schema["additionalProperties"] is False


def test_incident_matrix_has_unique_durable_classes() -> None:
    specs = evidence.INCIDENT_SPECS
    assert [item["incidentId"] for item in specs] == [
        "DIS-002",
        "DIS-003",
        "DIS-004",
        "DIS-005",
        "DIS-006",
        "DIS-007",
        "DIS-008",
    ]
    assert len({item["caseId"] for item in specs}) == len(specs)
    assert all(item["ruleIds"] for item in specs)
    with pytest.raises(SystemExit) as missing_command:
        evidence.main([])
    assert missing_command.value.code == 2


def test_opaque_request_ids_do_not_encode_case_metadata() -> None:
    first = evidence._opaque_request_id("derive", b"same material")
    second = evidence._opaque_request_id("derive", b"same material")
    assert first == second
    assert first.startswith("req-") and len(first) == 28
    assert "DIS" not in first and "generated" not in first and "seed" not in first


def test_frozen_incidents_validate_and_bind_all_embedded_content() -> None:
    paths = _incident_paths()
    assert len(paths) == len(evidence.INCIDENT_SPECS)
    checked = evidence.check(EVIDENCE_ROOT)
    assert set(paths).issubset(checked)

    validator = Draft202012Validator(_load(INCIDENT_SCHEMA))
    for path in paths:
        raw = path.read_bytes()
        incident = _load(path)
        validator.validate(incident)
        assert raw == rfc8785.dumps(incident) + b"\n"
        assert incident["digest"] == _detached_digest(incident)
        assert incident["status"] == "HISTORICAL"
        assert incident["difference"]["observed"] is True
        assert (
            incident["difference"]["leftProjectionDigest"]
            != incident["difference"]["rightProjectionDigest"]
        )
        request = evidence._artifact_bytes(incident["request"])
        assert incident["incidentId"].encode() not in request
        assert incident["recipeCase"]["caseId"].encode() not in request
        for resource in ("snapshot", "change"):
            exact = incident["input"][resource]["exactJcs"]
            decoded = evidence._artifact_bytes(exact)
            assert decoded == rfc8785.dumps(json.loads(decoded))


def test_historical_go_observation_does_not_impersonate_current_source() -> None:
    for path in _incident_paths():
        incident = _load(path)
        go = next(
            item
            for item in incident["observations"]
            if item["implementationId"] == "oac.supplier.go.internal"
        )
        assert go["executable"]["rawSha256"] == evidence.PREFX_GO_DIGEST
        assert go["sourceIdentity"]["identityKind"] == ("historical-source-unavailable/v1")
        assert go["sourceIdentity"]["sourceDigest"] is None
        assert go["sourceIdentity"]["files"] == []
        assert go["sourceIdentity"]["buildInfo"] is not None


def test_incident_detached_digest_and_immutability_reject_tampering(
    tmp_path: Path,
) -> None:
    source = _incident_paths()[0]
    incident = _load(source)
    incident["difference"]["summary"] += " tampered"
    tampered = tmp_path / source.name
    tampered.write_bytes(rfc8785.dumps(incident) + b"\n")
    validator = Draft202012Validator(_load(INCIDENT_SCHEMA))
    with pytest.raises(evidence.EvidenceError, match="digest mismatch"):
        evidence._load_incident(tampered, validator)

    immutable = tmp_path / "immutable.json"
    evidence._write_immutable(immutable, b"first\n")
    evidence._write_immutable(immutable, b"first\n")
    with pytest.raises(evidence.EvidenceError, match="immutable evidence"):
        evidence._write_immutable(immutable, b"second\n")


def test_incident_binding_cannot_be_resealed_around_other_content(tmp_path: Path) -> None:
    source = _incident_paths()[0]
    incident = _load(source)
    incident["bindings"]["generatorSource"]["rawSha256"] = "sha256:" + "0" * 64
    tampered = tmp_path / source.name
    tampered.write_bytes(evidence._seal_document(incident))
    validator = Draft202012Validator(_load(INCIDENT_SCHEMA))
    with pytest.raises(evidence.EvidenceError, match="content-binding digest drift"):
        evidence._load_incident(tampered, validator)


def test_closed_incident_schema_rejects_unbound_fields() -> None:
    incident = _load(_incident_paths()[0])
    mutated = copy.deepcopy(incident)
    mutated["resolution"] = {"status": "RESOLVED"}
    validator = Draft202012Validator(_load(INCIDENT_SCHEMA))
    with pytest.raises(ValidationError):
        validator.validate(mutated)


def test_resolutions_are_separate_and_bind_immutable_incidents() -> None:
    resolutions = _resolution_paths()
    assert len(resolutions) in {0, len(evidence.INCIDENT_SPECS)}
    validator = Draft202012Validator(_load(RESOLUTION_SCHEMA))
    incidents = {path.name: _load(path) for path in _incident_paths()}
    for path in resolutions:
        raw = path.read_bytes()
        resolution = _load(path)
        validator.validate(resolution)
        assert raw == rfc8785.dumps(resolution) + b"\n"
        assert resolution["digest"] == _detached_digest(resolution)
        incident_name = resolution["incidentRef"]["path"].rsplit("/", 1)[-1]
        incident = incidents[incident_name]
        assert resolution["incidentRef"]["digest"] == incident["digest"]
        assert resolution["incidentRef"]["incidentId"] == incident["incidentId"]
        projections = [
            item["semanticProjection"]["projectionDigest"]
            for item in resolution["fixedObservations"]
        ]
        assert projections[0] == projections[1]
        assert resolution["equivalence"]["projectionDigest"] == projections[0]


def test_resolution_schema_cannot_claim_unresolved_equivalence() -> None:
    if not _resolution_paths():
        pytest.skip("fixed observations are not stable yet")
    resolution = _load(_resolution_paths()[0])
    mutated = copy.deepcopy(resolution)
    mutated["equivalence"]["exactSemanticMatch"] = False
    validator = Draft202012Validator(_load(RESOLUTION_SCHEMA))
    with pytest.raises(ValidationError):
        validator.validate(mutated)
