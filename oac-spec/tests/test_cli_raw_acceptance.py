from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import rfc8785
from test_evolution import _demand, _intake_receipt, _outcome_certificate, _snapshot

from oac import cli
from oac.sealed import _decode_raw_object, _jcs, admit_sealed_resource


def _resources():
    snapshot = _snapshot()
    demand = _demand(snapshot)
    intake = _intake_receipt(snapshot)
    return {
        "OrganizationSnapshot": snapshot,
        "OrganizationalDemand": demand,
        "SourceAdmissionReceipt": intake,
        "OutcomeCertificate": _outcome_certificate(snapshot, intake, demand),
    }


def _raw(resource, *, sparse=False, revision="1"):
    value = resource.model_dump(mode="json", by_alias=True)
    value.pop("digest", None)
    if sparse:
        value["metadata"].pop("effectiveTo")
    value["metadata"]["revision"] = "REVISION_TOKEN"
    raw = json.dumps(value).replace('"REVISION_TOKEN"', revision).encode()
    value["digest"] = "sha256:" + hashlib.sha256(_jcs(_decode_raw_object(raw))).hexdigest()
    return json.dumps(value).replace('"REVISION_TOKEN"', revision).encode()


def _invoke(args, capsys):
    code = cli.main(args)
    captured = capsys.readouterr()
    return code, json.loads(captured.out if code == 0 else captured.err)


@pytest.mark.parametrize("kind", tuple(_resources()))
@pytest.mark.parametrize("revision", ("1", "1.0", "1e0", "1e20"))
@pytest.mark.parametrize("sparse", (False, True))
def test_raw_commands_share_jcs_numeric_and_sparse_acceptance(
    tmp_path: Path, capsys, monkeypatch, kind, revision, sparse
):
    resources = _resources()
    snapshot_raw = _raw(resources["OrganizationSnapshot"], sparse=sparse, revision=revision)
    resource = resources[kind]
    if kind == "OrganizationalDemand":
        snapshot = admit_sealed_resource(snapshot_raw, "OrganizationSnapshot")
        resource = resource.model_copy(update={"spec": resource.spec.model_copy(update={
            "snapshot_ref": resource.spec.snapshot_ref.model_copy(update={
                "digest": snapshot.resource_digest,
                "revision": snapshot.resource.metadata.revision,
            }),
        })})
    raw = _raw(resource, sparse=sparse, revision=revision)
    expected = admit_sealed_resource(raw, kind).resource_digest
    path = tmp_path / "resource.json"
    snapshot_path = tmp_path / "snapshot.json"
    path.write_bytes(raw)
    snapshot_path.write_bytes(snapshot_raw)
    real_read = cli._read_bytes
    reads = []

    def read_once(value, **kwargs):
        reads.append(value)
        return real_read(value, **kwargs)

    monkeypatch.setattr(cli, "_read_bytes", read_once)
    commands = [
        ["validate", str(path)],
        ["validate", str(path), "--verify-digest"],
        ["digest", str(path)],
    ]
    if kind != "OrganizationSnapshot":
        commands.append(["validate-evolution", str(path)])
        if kind == "OrganizationalDemand":
            commands[-1].extend(["--snapshot", str(snapshot_path)])
    for command in commands:
        reads.clear()
        code, response = _invoke(command, capsys)
        assert code == 0, response
        assert response == ({"digest": expected} if command[0] == "digest"
                            else {"valid": True, "kind": kind})
        assert reads == ([str(path), str(snapshot_path)]
                         if "--snapshot" in command else [str(path)])
    assert path.read_bytes() == raw
    assert snapshot_path.read_bytes() == snapshot_raw


@pytest.mark.parametrize("digest_state", ("missing", "null", "stale"))
def test_draft_commands_do_not_require_or_repair_a_sealed_digest(tmp_path: Path, capsys, digest_state):
    value = json.loads(_raw(_resources()["SourceAdmissionReceipt"], sparse=True))
    expected = value["digest"]
    if digest_state == "missing":
        del value["digest"]
    else:
        value["digest"] = None if digest_state == "null" else "sha256:" + "0" * 64
    raw = rfc8785.dumps(value)
    path = tmp_path / "draft.json"
    path.write_bytes(raw)
    assert _invoke(["validate", str(path)], capsys)[0] == 0
    assert _invoke(["digest", str(path)], capsys) == (0, {"digest": expected})
    for command in (["validate", str(path), "--verify-digest"], ["validate-evolution", str(path)]):
        code, response = _invoke(command, capsys)
        assert code == 2
        assert response["reasonCode"] == ("ROOT_DIGEST_MISMATCH" if digest_state == "stale"
                                          else "CORE_SCHEMA_INVALID")
    assert path.read_bytes() == raw


def test_draft_defaults_do_not_enter_raw_digest(tmp_path: Path, capsys):
    value = json.loads(_raw(_resources()["SourceAdmissionReceipt"], sparse=True))
    value.pop("apiVersion")
    value.pop("digest")
    expected = "sha256:" + hashlib.sha256(rfc8785.dumps(value)).hexdigest()
    path = tmp_path / "draft.json"
    path.write_bytes(rfc8785.dumps(value))
    assert _invoke(["validate", str(path)], capsys)[0] == 0
    assert _invoke(["digest", str(path)], capsys) == (0, {"digest": expected})
    value["digest"] = expected
    path.write_bytes(rfc8785.dumps(value))
    assert _invoke(["validate", str(path), "--verify-digest"], capsys)[0] == 2


@pytest.mark.parametrize("mutation", ("fraction", "boolean", "string", "digest-type", "digest-format",
                                       "duplicate-key", "nonfinite", "malformed", "unknown-kind"))
def test_all_raw_commands_reject_invalid_structure(tmp_path: Path, capsys, mutation):
    value = json.loads(_raw(_resources()["SourceAdmissionReceipt"]))
    if mutation in ("fraction", "boolean", "string"):
        value["metadata"]["revision"] = {"fraction": 1.25, "boolean": True, "string": "1"}[mutation]
    elif mutation in ("digest-type", "digest-format"):
        value["digest"] = 1 if mutation == "digest-type" else "invalid"
    elif mutation == "unknown-kind":
        value["kind"] = "UnknownResource"
    if mutation in ("fraction", "boolean", "string"):
        value.pop("digest")
        value["digest"] = "sha256:" + hashlib.sha256(rfc8785.dumps(value)).hexdigest()
    raw = json.dumps(value).encode()
    if mutation == "duplicate-key":
        raw = raw.replace(b'"revision": 1', b'"revision": 1, "revision": 1')
    elif mutation == "nonfinite":
        raw = raw.replace(b'"revision": 1', b'"revision": NaN')
    elif mutation == "malformed":
        raw = raw[:-1]
    path = tmp_path / "invalid.json"
    path.write_bytes(raw)
    for command in (["validate", str(path)], ["digest", str(path)],
                    ["validate", str(path), "--verify-digest"], ["validate-evolution", str(path)]):
        code, response = _invoke(command, capsys)
        assert code == 2, response
        assert response["reasonCode"] == ("CORE_KIND_UNKNOWN" if mutation == "unknown-kind"
                                          else "CORE_SCHEMA_INVALID")
    assert path.read_bytes() == raw


@pytest.mark.parametrize("kind", (None, 1, "Unregistered"))
def test_unknown_raw_kind_precedes_incomplete_envelope(tmp_path: Path, capsys, kind):
    path = tmp_path / "unknown.json"
    path.write_bytes(rfc8785.dumps({"kind": kind}))
    for args in (["validate", str(path)], ["digest", str(path)],
                 ["validate", str(path), "--verify-digest"], ["validate-evolution", str(path)]):
        code, response = _invoke(args, capsys)
        assert code == 2
        assert response["reasonCode"] == "CORE_KIND_UNKNOWN"


def test_evolution_routing_retains_specific_root_errors(tmp_path: Path, capsys):
    resources = _resources()
    snapshot_path = tmp_path / "snapshot.json"
    demand_path = tmp_path / "demand.json"
    snapshot_path.write_bytes(_raw(resources["OrganizationSnapshot"]))
    demand_path.write_bytes(_raw(resources["OrganizationalDemand"]))
    for args, reason in (
        (["validate-evolution", str(snapshot_path)], "EVOLUTION_REF_KIND_MISMATCH"),
        (["validate-evolution", str(demand_path)], "EVOLUTION_ROOT_INCOMPLETE"),
        (["validate-evolution", str(demand_path), "--snapshot", str(demand_path)], "EVOLUTION_REF_KIND_MISMATCH"),
    ):
        code, response = _invoke(args, capsys)
        assert code == 2
        assert response["reasonCode"] == reason
