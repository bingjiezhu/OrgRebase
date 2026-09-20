from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import rfc8785
from test_evolution import _demand, _intake_receipt, _outcome_certificate, _snapshot

from oac.canonical import OACValidationError, parse_resource
from oac.cli import main
from oac.evolution import (
    outcome_certificate_source_refs,
    verify_outcome_certificate,
    verify_outcome_certificate_from_admitted,
)
from oac.models import OutcomeCertificate
from oac.sealed import admit_sealed_resource

DEFAULT_SHARED_OBSERVATION = (
    Path(__file__).parent / "fixtures/evolution/default-shared-observation.json"
)


def _sparse(resource):
    raw = resource.model_dump(mode="json", by_alias=True)
    del raw["metadata"]["effectiveTo"]
    return _seal(raw)


def _seal(raw):
    raw.pop("digest", None)
    raw["digest"] = "sha256:" + hashlib.sha256(rfc8785.dumps(raw)).hexdigest()
    return raw


@pytest.mark.parametrize("kind", ["OrganizationalDemand", "SourceAdmissionReceipt", "OutcomeCertificate"])
def test_evolution_cli_preserves_admitted_sparse_identity(tmp_path: Path, capsys, kind):
    snapshot = _snapshot()
    demand = _demand(snapshot)
    intake = _intake_receipt(snapshot)
    resource = {
        "OrganizationalDemand": demand,
        "SourceAdmissionReceipt": intake,
        "OutcomeCertificate": _outcome_certificate(snapshot, intake, demand),
    }[kind]
    raw = _sparse(resource)
    snapshot_raw = _sparse(snapshot)
    if kind == "OrganizationalDemand":
        raw["spec"]["snapshotRef"]["digest"] = snapshot_raw["digest"]
        _seal(raw)
    path = tmp_path / "resource.json"
    snapshot_path = tmp_path / "snapshot.json"
    path.write_bytes(rfc8785.dumps(raw))
    snapshot_path.write_bytes(rfc8785.dumps(snapshot_raw))
    original = path.read_bytes()
    assert admit_sealed_resource(original, kind).resource_digest == raw["digest"]
    assert main(["validate", str(path), "--verify-digest"]) == 0
    capsys.readouterr()
    args = ["validate-evolution", str(path)]
    if kind == "OrganizationalDemand":
        args.extend(["--snapshot", str(snapshot_path)])
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out) == {"valid": True, "kind": kind}
    assert path.read_bytes() == original

    raw["metadata"]["id"] += "-tampered"
    path.write_bytes(rfc8785.dumps(raw))
    assert main(args) == 2
    assert json.loads(capsys.readouterr().err)["reasonCode"] == "ROOT_DIGEST_MISMATCH"


def test_default_outcome_preserves_historical_role_order_and_bytes(capsys):
    original = DEFAULT_SHARED_OBSERVATION.read_bytes()
    assert hashlib.sha256(original).hexdigest() == (
        "393f2df03f5d493e4a65cb508a3b1c74bef607b5d3d07176862b3b0116f2ff54"
    )
    certificate = parse_resource(original, verify_digest=True)
    assert isinstance(certificate, OutcomeCertificate)
    assert certificate.spec.profile_binding is None
    observation = certificate.spec.observation_refs[0]
    assert certificate.spec.evidence_refs == (observation,)
    assert certificate.metadata.source_refs.count(observation.resource_id) == 2
    assert outcome_certificate_source_refs(certificate.spec) == certificate.metadata.source_refs
    verify_outcome_certificate(certificate)

    admission = admit_sealed_resource(original, "OutcomeCertificate")
    verify_outcome_certificate_from_admitted(admission)
    assert admission.resource_digest == certificate.digest
    assert main(["validate-evolution", str(DEFAULT_SHARED_OBSERVATION)]) == 0
    assert json.loads(capsys.readouterr().out) == {"valid": True, "kind": "OutcomeCertificate"}
    assert DEFAULT_SHARED_OBSERVATION.read_bytes() == original


@pytest.mark.parametrize("mutation", ["deduplicate", "reorder", "extra", "duplicate-role"])
def test_default_outcome_still_requires_exact_role_provenance(mutation):
    raw = json.loads(DEFAULT_SHARED_OBSERVATION.read_bytes())
    source_ids = raw["metadata"]["sourceRefs"]
    if mutation == "deduplicate":
        raw["metadata"]["sourceRefs"] = list(dict.fromkeys(source_ids))
    elif mutation == "reorder":
        source_ids[0], source_ids[1] = source_ids[1], source_ids[0]
    elif mutation == "extra":
        source_ids.append(source_ids[-1])
    else:
        raw["spec"]["observationRefs"].append(raw["spec"]["observationRefs"][0])
    admission = admit_sealed_resource(rfc8785.dumps(_seal(raw)), "OutcomeCertificate")
    with pytest.raises(OACValidationError) as error:
        verify_outcome_certificate_from_admitted(admission)
    assert error.value.reason_code == (
        "EVOLUTION_DUPLICATE_REF" if mutation == "duplicate-role"
        else "EVOLUTION_PROVENANCE_MISMATCH"
    )


@pytest.mark.parametrize("kind", ["OrganizationalDemand", "SourceAdmissionReceipt"])
def test_default_outcome_exception_does_not_relax_other_resource_provenance(kind):
    snapshot = _snapshot()
    resource = _demand(snapshot) if kind == "OrganizationalDemand" else _intake_receipt(snapshot)
    raw = resource.model_dump(mode="json", by_alias=True)
    raw["metadata"]["sourceRefs"].append(raw["metadata"]["sourceRefs"][0])
    with pytest.raises(OACValidationError) as error:
        admit_sealed_resource(rfc8785.dumps(_seal(raw)), kind)
    assert error.value.reason_code == "CORE_SCHEMA_INVALID"
