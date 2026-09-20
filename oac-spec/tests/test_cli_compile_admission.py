from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import rfc8785

from oac.canonical import OACValidationError, calculate_digest, parse_resource
from oac.change_profiles import RETAIL_PROFILE, SUPPLIER_PROFILE
from oac.compiler import compile_change_from_admitted
from oac.sealed import admit_sealed_resource

ROOT = Path(__file__).resolve().parents[1]
SUPPLIER_INPUTS = ROOT / "profiles/supplier-change/inputs"


def _run(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-B", "-m", "oac", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def _inputs(tmp_path: Path, profile: str, *, sparse: bool) -> tuple[Path, Path]:
    if profile == SUPPLIER_PROFILE:
        paths = (
            SUPPLIER_INPUTS / "veracier-proc01.snapshot.json",
            SUPPLIER_INPUTS / "SC-001.change.json",
        )
    else:
        paths = tuple(ROOT / "tests/fixtures/retail-cancellation" / name for name in ("snapshot.json", "change.json"))
    outputs = []
    for name, path in zip(("snapshot.json", "change.json"), paths, strict=True):
        value = parse_resource(path.read_bytes(), verify_digest=True).model_dump(mode="json", by_alias=True)
        if sparse:
            del value["metadata"]["effectiveTo"]
        value["digest"] = calculate_digest(value)
        target = tmp_path / name
        target.write_bytes(rfc8785.dumps(value) + b"\n")
        outputs.append(target)
    return outputs[0], outputs[1]


@pytest.mark.parametrize("profile", (SUPPLIER_PROFILE, RETAIL_PROFILE))
@pytest.mark.parametrize("sparse", (False, True))
def test_compile_verify_preserve_exact_raw_roots(tmp_path: Path, profile: str, sparse: bool) -> None:
    snapshot, change = _inputs(tmp_path, profile, sparse=sparse)
    original = (snapshot.read_bytes(), change.read_bytes())
    plan, certificate = tmp_path / "plan.json", tmp_path / "certificate.json"
    flags = ("--profile", profile) if profile == RETAIL_PROFILE else ()
    compiled = _run(tmp_path, "compile", str(snapshot), str(change), *flags, "-o", str(plan))
    assert compiled.returncode == 0, compiled.stderr
    checked = _run(tmp_path, "verify", str(snapshot), str(change), str(plan), *flags, "-o", str(certificate))
    assert checked.returncode == 0, checked.stderr
    parsed_plan, parsed_certificate = json.loads(plan.read_bytes()), json.loads(certificate.read_bytes())
    assert parsed_certificate["spec"]["verdict"] == "ACCEPT"
    assert parsed_plan["spec"]["snapshotRef"]["digest"] == json.loads(original[0])["digest"]
    assert parsed_plan["spec"]["changeRef"]["digest"] == json.loads(original[1])["digest"]
    assert (snapshot.read_bytes(), change.read_bytes()) == original
    if sparse:
        assert "effectiveTo" not in json.loads(original[0])["metadata"]
        assert calculate_digest(parse_resource(original[0])) != json.loads(original[0])["digest"]


def test_compile_rejects_historical_typed_fixture_without_rewriting_it(tmp_path: Path) -> None:
    source = SUPPLIER_INPUTS / "veracier-proc01.snapshot.json"
    change = SUPPLIER_INPUTS / "SC-001.change.json"
    before = (source.read_bytes(), change.read_bytes())
    plan = tmp_path / "plan.json"
    result = _run(tmp_path, "compile", str(source), str(change), "-o", str(plan))
    assert result.returncode == 2
    assert json.loads(result.stderr)["reasonCode"] == "ROOT_DIGEST_MISMATCH"
    assert not plan.exists()
    assert (source.read_bytes(), change.read_bytes()) == before


@pytest.mark.parametrize("mutation,reason", (("digest", "ROOT_DIGEST_MISMATCH"), ("duplicate", "CORE_SCHEMA_INVALID")))
def test_compile_admission_rejects_invalid_roots_before_output(tmp_path: Path, mutation: str, reason: str) -> None:
    snapshot, change = _inputs(tmp_path, SUPPLIER_PROFILE, sparse=False)
    if mutation == "digest":
        value = json.loads(snapshot.read_bytes())
        value["digest"] = "sha256:" + "0" * 64
        snapshot.write_bytes(rfc8785.dumps(value))
    else:
        snapshot.write_bytes(snapshot.read_bytes().replace(b'"kind":', b'"kind":"OrganizationSnapshot","kind":'))
    plan = tmp_path / "plan.json"
    result = _run(tmp_path, "compile", str(snapshot), str(change), "-o", str(plan))
    assert result.returncode == 2
    assert json.loads(result.stderr)["reasonCode"] == reason
    assert not plan.exists()


def test_historical_demo_keeps_its_typed_resource_contract(tmp_path: Path) -> None:
    result = _run(tmp_path, "demo")
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["verdict"] == "ACCEPT"
    assert value["planDigest"] == "sha256:f66e1ad2b4086cc73ac78f6e965e7486acf2a183e52f689795c6282e2371836e"
    assert value["certificateDigest"] == "sha256:048d83e406dcfed7fa542e86df2d2ddc108bf3ff659bea668fcc296b2bf20570"


@pytest.mark.parametrize("mutation", ("stored_digest", "typed_resource"))
def test_admitted_compile_rechecks_untrusted_admission_records(tmp_path: Path, mutation: str) -> None:
    snapshot, change = _inputs(tmp_path, SUPPLIER_PROFILE, sparse=True)
    admitted_snapshot = admit_sealed_resource(snapshot.read_bytes(), "OrganizationSnapshot")
    admitted_change = admit_sealed_resource(change.read_bytes(), "SemanticChangeSet")
    if mutation == "stored_digest":
        attacked = replace(admitted_snapshot, resource_digest="sha256:" + "f" * 64)
    else:
        attacked = replace(
            admitted_snapshot,
            resource=admitted_snapshot.resource.model_copy(update={"digest": "sha256:" + "f" * 64}),
        )
    with pytest.raises(OACValidationError) as error:
        compile_change_from_admitted(attacked, admitted_change, profile=SUPPLIER_PROFILE)
    assert error.value.reason_code == "CANONICAL_ADMISSION_MISMATCH"
