from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import venv
from pathlib import Path
from typing import Any

import pytest
import rfc8785

from oac.ctk_adapter_v2 import PROTOCOL_VERSION, handle

ROOT = Path(__file__).resolve().parents[1]
CAPSULE_ROOT = (
    ROOT / "experiments/supplier-v02-portability/v0.2-seed-2"
)
_HARNESS_SPEC = importlib.util.spec_from_file_location(
    "oac_supplier_v02_parity_harness",
    ROOT / "scripts/check_supplier_v02_parity.py",
)
assert _HARNESS_SPEC is not None and _HARNESS_SPEC.loader is not None
_HARNESS = importlib.util.module_from_spec(_HARNESS_SPEC)
_HARNESS_SPEC.loader.exec_module(_HARNESS)
_derive_observation = _HARNESS._derive_observation
_derive_request = _HARNESS._derive_request
_validate_observation = _HARNESS._validate_observation
build_validate_cases = _HARNESS.build_validate_cases
_command_digest = _HARNESS._command_digest
_invoke = _HARNESS._invoke
_mutant_script = _HARNESS._mutant_script
generate_variants = _HARNESS.generate_variants
load_capsule = _HARNESS.load_capsule
run_parity = _HARNESS.run_parity
HarnessFailure = _HARNESS.HarnessFailure
EXPECTED_REPORT_DIGESTS = {
    # These bind SealedResource/v1 raw-map roots.  The older 321068/2e7ffb/
    # 2fb3dd values bound legacy model-normalized Change digests and are not A1
    # portability coordinates.
    "SC-008": "sha256:e6fbff4c5bc58b11b71ef7663ae3b7f83a3230ce38efd237325dfffdcafce5cf",
    "SC-009": "sha256:331d886277809b8ba4ff684317d6fbb4889b37300e9a71fa752fe206acaa32ec",
    "SC-010": "sha256:be307dff07197cd3aeea809baf786f25cebbf2cec553cd7ebde1d2ca61760d1c",
}


def _request(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "requestId": "request-v2",
        "operation": operation,
        "payload": payload,
    }


def _derive_payload(snapshot: bytes, change: bytes) -> dict[str, str]:
    return {
        "snapshotBase64": base64.b64encode(snapshot).decode("ascii"),
        "changeBase64": base64.b64encode(change).decode("ascii"),
    }


def test_v2_capabilities_are_closed_and_track_specific() -> None:
    response = handle(_request("capabilities", {}))
    assert response["sutStatus"] == "COMPLETED"
    result = response["result"]
    assert result["adapterProtocolVersion"] == PROTOCOL_VERSION
    assert result["tracks"] == [
        {
            "role": "semantic-kernel",
            "operation": "validateResource",
            "profileId": "oac.core.sealed-resource",
            "profileVersion": "v1",
            "wireVersion": PROTOCOL_VERSION,
        },
        {
            "role": "semantic-kernel",
            "operation": "derive",
            "profileId": "oac.supplier.transfer.portability-capsule",
            "profileVersion": "v0.2-seed-2",
            "wireVersion": PROTOCOL_VERSION,
        },
    ]


def test_three_frozen_raw_resources_derive_exact_a1_reports() -> None:
    manifest, cases = load_capsule()
    assert manifest["capsuleDigest"] == _HARNESS.FROZEN_CAPSULE_DIGEST
    for item in cases:
        response = handle(_request("derive", _derive_payload(item["snapshot"], item["change"])))
        assert response["sutStatus"] == "COMPLETED", item["caseId"]
        result = response["result"]
        assert result["reportDigest"] == EXPECTED_REPORT_DIGESTS[item["caseId"]]
        assert rfc8785.dumps(result["report"]) == item["expectedJcs"]


def test_validate_resource_binds_admitted_raw_identity() -> None:
    _, cases = load_capsule()
    raw = cases[0]["snapshot"]
    decoded = json.loads(raw)
    response = handle(
        _request(
            "validateResource",
            {
                "expectedKind": "OrganizationSnapshot",
                "rawBase64": base64.b64encode(raw).decode("ascii"),
            },
        )
    )
    assert response["sutStatus"] == "COMPLETED"
    assert response["result"] == {
        "kind": "OrganizationSnapshot",
        "resourceId": decoded["metadata"]["id"],
        "resourceDigest": decoded["digest"],
    }


def test_validate_resource_rejects_nonzero_base64_pad_bits() -> None:
    _, cases = load_capsule()
    change = next(item["change"] for item in cases if item["caseId"] == "SC-009")
    canonical = base64.b64encode(change).decode("ascii")
    assert canonical.endswith("g==")
    noncanonical = canonical[:-3] + "h=="
    assert base64.b64decode(noncanonical, validate=True) == change
    response = handle(
        _request(
            "validateResource",
            {"expectedKind": "SemanticChangeSet", "rawBase64": noncanonical},
        )
    )
    assert response["sutStatus"] == "ERROR"
    assert response["error"]["code"] == "CTK_INPUT_INVALID"


def test_validate_resource_scoring_set_has_five_roots_and_twenty_admission_probes() -> None:
    _, frozen = load_capsule()
    cases = build_validate_cases(frozen)
    assert sum(item["class"] == "frozen" for item in cases) == 5
    assert sum(item["class"] == "probe" for item in cases) == 20
    assert {item["caseId"] for item in cases if item["class"] == "probe"} == {
        "probe:missing-envelope",
        "probe:identifier-explicit-rewrite",
        "probe:timestamp-alias",
        "probe:timestamp-offset",
        "probe:timestamp-year-zero",
        "probe:timestamp-year-max",
        "probe:duplicate-key",
        "probe:noncanonical-base64",
        "probe:root-digest-mismatch",
        "probe:unknown-kind",
        "probe:finite-2pow53",
        "probe:blank-identifier",
        "probe:plain-business-string-whitespace",
        "probe:nan-token",
        "probe:invalid-utf8",
        "probe:equivalent-number-1e0",
        "probe:equivalent-number-1.0",
        "probe:equivalent-number-1.00e+0",
        "probe:json-depth-64",
        "probe:json-depth-65",
    }
    for index, item in enumerate(cases):
        observed = _validate_observation(
            [sys.executable, "-m", "oac.ctk_adapter_v2"],
            expected_kind=item["expectedKind"],
            raw_base64=item["rawBase64"],
            request_id=f"validate-python-{index}",
        )
        expectation = item["expect"]
        assert observed["status"] == expectation["status"], item["caseId"]
        if expectation["status"] == "COMPLETED":
            assert observed["response"]["result"] == expectation["result"]
        else:
            assert observed["response"]["error"]["code"] == expectation["errorCode"]


def test_raw_derive_request_discloses_no_harness_or_expectation_fields() -> None:
    _, cases = load_capsule()
    request = _derive_request(cases[0]["snapshot"], cases[0]["change"], "opaque-request")
    assert set(request) == {"protocolVersion", "requestId", "operation", "payload"}
    assert set(request["payload"]) == {"snapshotBase64", "changeBase64"}
    wire = rfc8785.dumps(request)
    for forbidden in (
        b"caseId",
        b"expected",
        b"reportDigest",
        b"capsuleDigest",
        b"fixture",
        b"mutation",
        b"path",
    ):
        assert forbidden not in wire


def test_harness_recomputes_report_digest_instead_of_trusting_sut(tmp_path: Path) -> None:
    _, cases = load_capsule()
    encoded_report = base64.b64encode(cases[0]["expectedJcs"]).decode("ascii")
    adapter = tmp_path / "lying_digest_adapter.py"
    adapter.write_text(
        "import base64,json,sys\n"
        "import rfc8785\n"
        "request=json.load(sys.stdin)\n"
        f"report=json.loads(base64.b64decode({encoded_report!r}))\n"
        "response={'protocolVersion':'oac.ctk.stdio/v2','requestId':request['requestId'],"
        "'sutStatus':'COMPLETED','result':{'report':report,'reportDigest':'sha256:'+'0'*64}}\n"
        "sys.stdout.buffer.write(rfc8785.dumps(response)+b'\\n')\n",
        encoding="utf-8",
    )
    with pytest.raises(Exception, match="runner recomputation"):
        _derive_observation(
            [sys.executable, str(adapter)],
            cases[0]["snapshot"],
            cases[0]["change"],
            "digest-lie",
        )


@pytest.mark.parametrize(
    ("mutation", "case_index", "message"),
    (
        ("extra-field", 0, "pinned schema"),
        ("wrong-roots", 1, "requested profile and raw roots"),
    ),
)
def test_harness_binds_report_schema_profile_and_raw_roots(
    tmp_path: Path, mutation: str, case_index: int, message: str
) -> None:
    _, cases = load_capsule()
    report = json.loads(cases[0]["expectedJcs"])
    if mutation == "extra-field":
        report["extra"] = True
    report_jcs = rfc8785.dumps(report)
    encoded_report = base64.b64encode(report_jcs).decode("ascii")
    report_digest = "sha256:" + hashlib.sha256(report_jcs).hexdigest()
    adapter = tmp_path / f"report_{mutation}.py"
    adapter.write_text(
        "import base64,json,sys\n"
        "import rfc8785\n"
        "request=json.load(sys.stdin)\n"
        f"report=json.loads(base64.b64decode({encoded_report!r}))\n"
        "response={'protocolVersion':'oac.ctk.stdio/v2','requestId':request['requestId'],"
        f"'sutStatus':'COMPLETED','result':{{'report':report,'reportDigest':{report_digest!r}}}}}\n"
        "sys.stdout.buffer.write(rfc8785.dumps(response)+b'\\n')\n",
        encoding="utf-8",
    )
    item = cases[case_index]
    with pytest.raises(HarnessFailure, match=message):
        _derive_observation(
            [sys.executable, str(adapter)],
            item["snapshot"],
            item["change"],
            f"report-{mutation}",
        )


def test_generated_variants_reject_a_mutant_that_cans_all_three_frozen_reports(
    tmp_path: Path,
) -> None:
    _, cases = load_capsule()
    variants = generate_variants(cases)
    assert len(variants) >= 5
    mutant = tmp_path / "canned_three.py"
    mutant.write_text(_mutant_script(cases), encoding="utf-8")
    command = [sys.executable, str(mutant)]

    for index, item in enumerate(cases):
        observed = _derive_observation(
            command,
            item["snapshot"],
            item["change"],
            f"frozen-mutant-{index}",
        )
        assert observed["jcs"] == item["expectedJcs"]

    killed_by = []
    for index, item in enumerate(variants):
        reference = _derive_observation(
            [sys.executable, "-m", "oac.ctk_adapter_v2"],
            item["snapshot"],
            item["change"],
            f"generated-reference-{index}",
        )
        try:
            mutant_result = _derive_observation(
                command,
                item["snapshot"],
                item["change"],
                f"generated-mutant-{index}",
            )
            rejected = mutant_result["jcs"] != reference["jcs"]
        except HarnessFailure:
            rejected = True
        if rejected:
            killed_by.append(item["caseId"])
    assert killed_by


@pytest.mark.parametrize(
    ("raw_factory", "error_code"),
    (
        (
            lambda raw: raw.replace(b'"digest":"sha256:', b'"digest":"sha256:0', 1),
            "CORE_SCHEMA_INVALID",
        ),
        (
            lambda raw: b'{"apiVersion":"duplicate",' + raw.lstrip()[1:],
            "CORE_SCHEMA_INVALID",
        ),
    ),
)
def test_invalid_raw_resource_admission_has_stable_error_classification(
    raw_factory: Any, error_code: str
) -> None:
    _, cases = load_capsule()
    raw = raw_factory(cases[0]["snapshot"])
    response = handle(
        _request(
            "validateResource",
            {
                "expectedKind": "OrganizationSnapshot",
                "rawBase64": base64.b64encode(raw).decode("ascii"),
            },
        )
    )
    assert response["sutStatus"] == "ERROR"
    assert response["error"]["code"] == error_code
    assert "result" not in response


def test_digest_mismatch_is_not_mislabeled_as_ctk_input_invalid() -> None:
    _, cases = load_capsule()
    decoded = json.loads(cases[0]["snapshot"])
    decoded["digest"] = "sha256:" + "0" * 64
    raw = rfc8785.dumps(decoded)
    response = handle(
        _request(
            "validateResource",
            {
                "expectedKind": "OrganizationSnapshot",
                "rawBase64": base64.b64encode(raw).decode("ascii"),
            },
        )
    )
    assert response["sutStatus"] == "ERROR"
    assert response["error"]["code"] == "ROOT_DIGEST_MISMATCH"


def test_v2_payload_shapes_are_closed() -> None:
    _, cases = load_capsule()
    payload = _derive_payload(cases[0]["snapshot"], cases[0]["change"])
    payload["caseId"] = "SC-008"
    response = handle(_request("derive", payload))
    assert response["sutStatus"] == "ERROR"
    assert response["error"]["code"] == "CTK_INPUT_INVALID"


def test_capsule_runtime_uses_external_digest_anchor_not_repo_source_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staged = tmp_path / "capsule"
    shutil.copytree(CAPSULE_ROOT, staged)
    monkeypatch.setattr(_HARNESS, "ROOT", tmp_path / "source-tree-does-not-exist")
    manifest, cases = load_capsule(staged / "capsule.json")
    assert manifest["capsuleDigest"] == _HARNESS.FROZEN_CAPSULE_DIGEST
    assert len(cases) == 3
    with pytest.raises(HarnessFailure, match="source is missing"):
        load_capsule(staged / "capsule.json", verify_source_drift=True)


def test_self_resealed_capsule_cannot_replace_runner_trust_anchor(tmp_path: Path) -> None:
    staged = tmp_path / "capsule"
    shutil.copytree(CAPSULE_ROOT, staged)
    manifest_path = staged / "capsule.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["contracts"].reverse()
    projection = {key: value for key, value in manifest.items() if key != "capsuleDigest"}
    manifest["capsuleDigest"] = "sha256:" + hashlib.sha256(rfc8785.dumps(projection)).hexdigest()
    manifest_path.write_bytes(rfc8785.dumps(manifest) + b"\n")
    with pytest.raises(HarnessFailure, match="runner trust anchor"):
        load_capsule(manifest_path)


def test_fd_anchored_capsule_loader_rejects_intermediate_symlink(tmp_path: Path) -> None:
    staged = tmp_path / "capsule"
    shutil.copytree(CAPSULE_ROOT, staged)
    artifacts = staged / "artifacts"
    relocated = staged / "relocated-artifacts"
    artifacts.rename(relocated)
    artifacts.symlink_to(relocated, target_is_directory=True)
    with pytest.raises(HarnessFailure, match="non-symlink directory"):
        load_capsule(staged / "capsule.json")


def test_sut_process_gets_empty_cwd_and_allowlisted_environment_only(tmp_path: Path) -> None:
    adapter = tmp_path / "environment_probe.py"
    adapter.write_text(
        "import json,os,sys\n"
        "request=json.load(sys.stdin)\n"
        "result={'cwdEntries':sorted(os.listdir('.')),'leaked':sorted(k for k in os.environ "
        "if k in {'PYTHONPATH','HOME','CODEX_HOME','AWS_SECRET_ACCESS_KEY'})}\n"
        "response={'protocolVersion':'oac.ctk.stdio/v2','requestId':request['requestId'],"
        "'sutStatus':'COMPLETED','result':result}\n"
        "sys.stdout.write(json.dumps(response))\n",
        encoding="utf-8",
    )
    response = _invoke(
        [sys.executable, str(adapter)],
        {
            "protocolVersion": PROTOCOL_VERSION,
            "requestId": "environment-probe",
            "operation": "capabilities",
            "payload": {},
        },
    )
    assert response["result"] == {"cwdEntries": [], "leaked": []}


def test_sut_output_is_killed_at_real_time_ceiling(tmp_path: Path) -> None:
    adapter = tmp_path / "output_bomb.py"
    adapter.write_text(
        "import sys,time\n"
        f"sys.stdout.buffer.write(b'x'*({_HARNESS.MAX_OUTPUT_BYTES}+8192))\n"
        "sys.stdout.buffer.flush()\n"
        "time.sleep(10)\n",
        encoding="utf-8",
    )
    with pytest.raises(HarnessFailure, match="maxAdapterOutputBytes"):
        _invoke(
            [sys.executable, str(adapter)],
            {
                "protocolVersion": PROTOCOL_VERSION,
                "requestId": "output-bomb",
                "operation": "capabilities",
                "payload": {},
            },
        )


def test_python_build_digest_binds_script_or_package_closure(tmp_path: Path) -> None:
    left = tmp_path / "left.py"
    right = tmp_path / "right.py"
    left.write_text("VALUE = 1\n", encoding="utf-8")
    right.write_text("VALUE = 2\n", encoding="utf-8")
    assert _command_digest([sys.executable, str(left)]) != _command_digest(
        [sys.executable, str(right)]
    )
    module_digest = _command_digest([sys.executable, "-m", "oac.ctk_adapter_v2"])
    interpreter_only = "sha256:" + hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()
    assert module_digest != interpreter_only


def test_python_module_command_digest_resolves_target_environment_not_harness(
    tmp_path: Path,
) -> None:
    commands: list[list[str]] = []
    for index, value in enumerate(("SOURCE = 'left'\n", "SOURCE = 'right'\n")):
        environment = tmp_path / f"venv-{index}"
        venv.EnvBuilder(with_pip=False).create(environment)
        executable = environment / "bin/python"
        purelib = Path(
            subprocess.check_output(
                [str(executable), "-I", "-c", "import sysconfig;print(sysconfig.get_path('purelib'))"],
                text=True,
            ).strip()
        )
        package = purelib / "target_digest_adapter"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "runner.py").write_text(value, encoding="utf-8")
        commands.append([str(executable), "-m", "target_digest_adapter.runner"])

    assert _command_digest(commands[0]) != _command_digest(commands[1])


def test_parity_summary_separates_validate_and_derive_evidence(tmp_path: Path) -> None:
    alias = tmp_path / "python_alias_adapter.py"
    alias.write_text(
        "import json,sys\n"
        "import rfc8785\n"
        "from oac.ctk_adapter_v2 import handle\n"
        "request=json.load(sys.stdin)\n"
        "response=handle(request)\n"
        "if request['operation']=='capabilities':\n"
        " response['result']['implementationId']='oac.test.python-alias'\n"
        "sys.stdout.buffer.write(rfc8785.dumps(response)+b'\\n')\n",
        encoding="utf-8",
    )
    summary = run_parity(
        [sys.executable, "-m", "oac.ctk_adapter_v2"],
        [sys.executable, str(alias)],
        mutant_directory=tmp_path / "mutant",
    )
    assert summary["status"] == "PASS"
    assert summary["validateFrozenCases"] == 5
    assert summary["validateProbeCases"] == 20
    assert summary["validateTotalCases"] == summary["validatePassedCases"] == 25
    assert summary["deriveFrozenCases"] == 3
    assert summary["deriveGeneratedCases"] == 24
    assert summary["deriveTotalCases"] == summary["derivePassedCases"] == 27
    assert summary["totalCases"] == summary["passedCases"] == 52
    assert [item["conclusion"] for item in summary["mutants"]] == [
        "canned-three-rejected",
        "root-unknown-forgery-rejected",
    ]
    assert summary["claim"]["established"] == (
        "internal-bounded-two-implementation-parity"
    )
    assert "complete OAC conformance" in summary["claim"]["excludes"]
