from __future__ import annotations

import ast
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest
import rfc8785
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

ROOT = Path(__file__).resolve().parents[1]
RUNNER_SRC = ROOT / "ctk/runner/src"
sys.path.insert(0, str(RUNNER_SRC))

from oac_ctk_runner import bundle as bundle_module  # noqa: E402
from oac_ctk_runner.adapter import AdapterObservation, invoke  # noqa: E402
from oac_ctk_runner.bundle import Bundle, BundleError, load_bundle  # noqa: E402
from oac_ctk_runner.runner import (  # noqa: E402
    _score,
    _validate_capability_statement,
    run_bundle,
)

BUNDLE_PATH = ROOT / "ctk/bundles/phase-a-v0.1"
SCHEMA_PATH = ROOT / "ctk/schemas"
A0_SCHEMA_NAMES = {
    "CTKBundle",
    "CapabilityStatement",
    "ConformanceCase",
    "ConformanceResourceProfile",
    "DisagreementRecord",
    "RequirementSet",
    "RunResult",
}


def _reseal_bundle(bundle: Path) -> None:
    manifest_path = bundle / "bundle.json"
    manifest = json.loads(manifest_path.read_bytes())
    for entry in manifest["artifacts"]:
        raw = (bundle / entry["path"]).read_bytes()
        entry["size"] = len(raw)
        entry["digest"] = f"sha256:{hashlib.sha256(raw).hexdigest()}"
    projection = dict(manifest)
    projection.pop("bundleDigest", None)
    manifest["bundleDigest"] = f"sha256:{hashlib.sha256(rfc8785.dumps(projection)).hexdigest()}"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_runner_source_has_no_oac_implementation_imports() -> None:
    for path in (RUNNER_SRC / "oac_ctk_runner").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported |= {
            node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        }
        assert not any(name == "oac" or name.startswith("oac.") for name in imported)


def test_frozen_bundle_ledger_and_requirement_set_validate() -> None:
    bundle = load_bundle(BUNDLE_PATH)
    assert bundle.digest.startswith("sha256:")
    assert len(bundle.cases) == 24
    assert set(bundle.requirement_set["required"]) == {case["caseId"] for case in bundle.cases}
    assert bundle.requirement_set["notScored"] == []


def test_published_ctk_schemas_accept_every_frozen_bundle_resource() -> None:
    schemas = {
        name: json.loads((SCHEMA_PATH / f"{name}.schema.json").read_bytes())
        for name in A0_SCHEMA_NAMES
    }
    assert set(schemas) == A0_SCHEMA_NAMES
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)

    Draft202012Validator(schemas["CTKBundle"]).validate(
        json.loads((BUNDLE_PATH / "bundle.json").read_bytes())
    )
    Draft202012Validator(schemas["RequirementSet"]).validate(
        json.loads((BUNDLE_PATH / "requirements.json").read_bytes())
    )
    Draft202012Validator(schemas["ConformanceResourceProfile"]).validate(
        json.loads((BUNDLE_PATH / "resource-profile.json").read_bytes())
    )
    case_validator = Draft202012Validator(schemas["ConformanceCase"])
    for path in sorted((BUNDLE_PATH / "cases").glob("*.json")):
        case_validator.validate(json.loads(path.read_bytes()))


def test_bundle_pins_the_exact_public_contract_bytes() -> None:
    expected = {
        "contracts/standard/oac-conformance-v0.1.md": (ROOT / "standard/oac-conformance-v0.1.md"),
        "contracts/standard/oac-derived-identifiers-v0.1.md": (
            ROOT / "standard/oac-derived-identifiers-v0.1.md"
        ),
        "contracts/protocol/stdio-v1.md": ROOT / "ctk/protocol/stdio-v1.md",
        **{
            f"contracts/schemas/{name}.schema.json": SCHEMA_PATH / f"{name}.schema.json"
            for name in sorted(A0_SCHEMA_NAMES)
        },
    }
    manifest = json.loads((BUNDLE_PATH / "bundle.json").read_bytes())
    ledger_paths = {entry["path"] for entry in manifest["artifacts"]}
    for relative, source in expected.items():
        bundled = BUNDLE_PATH / relative
        assert relative in ledger_paths
        assert bundled.read_bytes() == source.read_bytes()


def test_bundle_rejects_unlisted_and_tampered_files(tmp_path: Path) -> None:
    import shutil

    copied = tmp_path / "bundle"
    shutil.copytree(BUNDLE_PATH, copied)
    (copied / "unlisted.txt").write_text("hidden input", encoding="utf-8")
    with pytest.raises(BundleError, match="inventory mismatch"):
        load_bundle(copied)

    (copied / "unlisted.txt").unlink()
    case = copied / "cases/C0-CANON-001.json"
    case.write_bytes(case.read_bytes() + b"\n")
    with pytest.raises(BundleError, match="size mismatch"):
        load_bundle(copied)


def test_bundle_rejects_resealed_invalid_cases_and_foreign_coordinates(
    tmp_path: Path,
) -> None:
    import shutil

    malformed = tmp_path / "malformed-case"
    shutil.copytree(BUNDLE_PATH, malformed)
    case_path = malformed / "cases/C0-CANON-001.json"
    case = json.loads(case_path.read_bytes())
    case.pop("requirementRefs")
    case["unknownTopLevel"] = True
    case_path.write_text(json.dumps(case), encoding="utf-8")
    _reseal_bundle(malformed)
    with pytest.raises(BundleError):
        load_bundle(malformed)

    foreign = tmp_path / "foreign-coordinate"
    shutil.copytree(BUNDLE_PATH, foreign)
    manifest_path = foreign / "bundle.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["standardVersion"] = "alien-standard"
    manifest["profileId"] = "alien.profile"
    manifest["profileVersion"] = "alien-v9"
    requirement_path = foreign / "requirements.json"
    requirement = json.loads(requirement_path.read_bytes())
    requirement["standardVersion"] = "alien-standard"
    requirement["profileVersion"] = "alien-v9"
    requirement_path.write_text(json.dumps(requirement), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _reseal_bundle(foreign)
    with pytest.raises(BundleError, match="unsupported frozen suite coordinate"):
        load_bundle(foreign)


def test_bundle_rejects_traversal_and_hardlinks(tmp_path: Path) -> None:
    import json
    import os
    import shutil

    traversal = tmp_path / "traversal"
    shutil.copytree(BUNDLE_PATH, traversal)
    manifest_path = traversal / "bundle.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["artifacts"][0]["path"] = "../outside.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(BundleError, match="unsafe artifact path"):
        load_bundle(traversal)

    linked = tmp_path / "hardlink"
    shutil.copytree(BUNDLE_PATH, linked)
    source = linked / "cases/C0-CANON-001.json"
    os.link(source, linked / "extra-hardlink.json")
    with pytest.raises(BundleError, match="hard-linked artifact"):
        load_bundle(linked)


def test_bundle_rejects_symlink_roots_and_non_regular_inventory(tmp_path: Path) -> None:
    import os
    import shutil

    copied = tmp_path / "real-bundle"
    shutil.copytree(BUNDLE_PATH, copied)
    linked_root = tmp_path / "linked-bundle"
    linked_root.symlink_to(copied, target_is_directory=True)
    with pytest.raises(BundleError, match="non-symlink directory"):
        load_bundle(linked_root)

    fifo_bundle = tmp_path / "fifo-bundle"
    shutil.copytree(BUNDLE_PATH, fifo_bundle)
    os.mkfifo(fifo_bundle / "hidden-fifo")
    with pytest.raises(BundleError, match="non-regular physical entry"):
        load_bundle(fifo_bundle)


def test_bundle_fd_anchor_rejects_intermediate_symlink_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    copied = tmp_path / "bundle"
    escaped = tmp_path / "escaped-cases"
    shutil.copytree(BUNDLE_PATH, copied)
    original_inventory = bundle_module._physical_inventory

    def swap_after_inventory(root_descriptor: int) -> set[str]:
        inventory = original_inventory(root_descriptor)
        (copied / "cases").rename(escaped)
        (copied / "cases").symlink_to(escaped, target_is_directory=True)
        return inventory

    monkeypatch.setattr(bundle_module, "_physical_inventory", swap_after_inventory)
    with pytest.raises(BundleError, match="non-symlink directory"):
        load_bundle(copied)


def test_bundle_rejects_linked_manifest_and_bootstrap_byte_exhaustion(
    tmp_path: Path,
) -> None:
    import os
    import shutil

    symlinked = tmp_path / "symlinked-manifest"
    shutil.copytree(BUNDLE_PATH, symlinked)
    manifest = symlinked / "bundle.json"
    external = tmp_path / "external-manifest.json"
    manifest.replace(external)
    manifest.symlink_to(external)
    with pytest.raises(BundleError, match="regular non-symlink"):
        load_bundle(symlinked)

    linked = tmp_path / "linked-manifest"
    shutil.copytree(BUNDLE_PATH, linked)
    os.link(linked / "bundle.json", tmp_path / "manifest-hardlink.json")
    with pytest.raises(BundleError, match="hard-linked bundle manifest"):
        load_bundle(linked)

    oversized = tmp_path / "oversized"
    shutil.copytree(BUNDLE_PATH, oversized)
    first_case = oversized / "cases/C0-CANON-001.json"
    first_case.write_bytes(b"")
    with first_case.open("r+b") as stream:
        stream.truncate(67_108_865)
    oversized_manifest = json.loads((oversized / "bundle.json").read_bytes())
    for entry in oversized_manifest["artifacts"]:
        if entry["path"] == "cases/C0-CANON-001.json":
            entry["size"] = 67_108_865
            break
    (oversized / "bundle.json").write_text(json.dumps(oversized_manifest), encoding="utf-8")
    with pytest.raises(BundleError, match="bootstrap byte ceiling"):
        load_bundle(oversized)


def test_bundle_builder_refuses_symlinked_write_targets_without_touching_sentinel(
    tmp_path: Path,
) -> None:
    import shutil
    import subprocess

    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("must remain unchanged", encoding="utf-8")

    contract_link = tmp_path / "contract-link"
    shutil.copytree(BUNDLE_PATH, contract_link)
    contract_target = contract_link / "contracts/standard/oac-conformance-v0.1.md"
    contract_target.unlink()
    contract_target.symlink_to(sentinel)
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "ctk/build_bundle.py"),
            "--bundle",
            str(contract_link),
        ],
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 2
    assert sentinel.read_text(encoding="utf-8") == "must remain unchanged"

    parent_link = tmp_path / "parent-link"
    shutil.copytree(BUNDLE_PATH, parent_link)
    protocol_parent = parent_link / "contracts/protocol"
    external_parent = tmp_path / "external-protocol"
    protocol_parent.rename(external_parent)
    protocol_parent.symlink_to(external_parent, target_is_directory=True)
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "ctk/build_bundle.py"),
            "--bundle",
            str(parent_link),
        ],
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 2
    assert sentinel.read_text(encoding="utf-8") == "must remain unchanged"

    manifest_link = tmp_path / "manifest-link"
    shutil.copytree(BUNDLE_PATH, manifest_link)
    manifest = manifest_link / "bundle.json"
    manifest.unlink()
    manifest.symlink_to(sentinel)
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "ctk/build_bundle.py"),
            "--bundle",
            str(manifest_link),
        ],
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 2
    assert sentinel.read_text(encoding="utf-8") == "must remain unchanged"


def test_bundle_loader_rejects_non_json_constants_and_excessive_nesting(
    tmp_path: Path,
) -> None:
    import shutil

    non_json = tmp_path / "non-json"
    shutil.copytree(BUNDLE_PATH, non_json)
    requirement = non_json / "requirements.json"
    requirement.write_text(
        '{"requirementSetId":"x","standardVersion":"x",'
        '"profileVersion":"x","required":[NaN],"notScored":[]}',
        encoding="utf-8",
    )
    _reseal_bundle(non_json)
    with pytest.raises(BundleError, match="non-JSON numeric constant"):
        load_bundle(non_json)

    deep = tmp_path / "deep"
    shutil.copytree(BUNDLE_PATH, deep)
    case_path = deep / "cases/C0-CANON-001.json"
    case = json.loads(case_path.read_bytes())
    nested: object = "leaf"
    for _ in range(70):
        nested = [nested]
    case["input"] = {"nested": nested}
    case_path.write_text(json.dumps(case), encoding="utf-8")
    _reseal_bundle(deep)
    with pytest.raises(BundleError, match="exceeds bootstrap depth"):
        load_bundle(deep)


def test_reference_adapter_passes_the_exact_frozen_requirement_set() -> None:
    bundle = load_bundle(BUNDLE_PATH)
    result = run_bundle(
        bundle,
        (sys.executable, "-m", "oac.ctk_adapter"),
    )
    assert result["requiredPassed"] is True
    assert result["summary"] == {
        "required": 24,
        "passed": 24,
        "failed": 0,
        "notScored": 0,
    }
    run_result_schema = json.loads((SCHEMA_PATH / "RunResult.schema.json").read_bytes())
    Draft202012Validator(run_result_schema).validate(result)


def test_targeted_reference_mutant_is_rejected_without_weakening_expectations() -> None:
    bundle = load_bundle(BUNDLE_PATH)
    result = run_bundle(
        bundle,
        (sys.executable, str(ROOT / "tests/fixtures/ctk_mutant_adapter.py")),
    )
    failed = {
        item["caseId"]: item for item in result["caseResults"] if item["caseOutcome"] != "PASS"
    }
    assert result["requiredPassed"] is False
    assert "C1-KLEENE-001" in failed
    assert "RESULT_MISMATCH" in failed["C1-KLEENE-001"]["reasonCodes"]


def test_runner_never_sends_case_metadata_or_expectations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = load_bundle(BUNDLE_PATH)
    observed_payloads: list[dict[str, Any]] = []
    operations = sorted({str(case["operation"]) for case in bundle.cases})

    def fake_invoke(
        command: tuple[str, ...],
        *,
        operation: str,
        payload: dict[str, Any],
        timeout_ms: int,
        max_output_bytes: int,
        max_json_depth: int,
        max_request_bytes: int,
    ) -> AdapterObservation:
        del command, timeout_ms, max_output_bytes, max_json_depth, max_request_bytes
        observed_payloads.append(copy.deepcopy(payload))
        if operation == "capabilities":
            tracks = [
                {
                    "role": "semantic-kernel",
                    "operation": item,
                    "profileId": "oac.phase-a",
                    "profileVersion": "v0.1",
                    "wireVersion": "oac.ctk.stdio/v1",
                }
                for item in operations
            ]
            result: dict[str, Any] = {
                "implementationId": "spy",
                "implementationVersion": "1",
                "adapterProtocolVersion": "oac.ctk.stdio/v1",
                "tracks": tracks,
            }
        else:
            result = {}
        return AdapterObservation(
            sut_status="COMPLETED",
            response={
                "protocolVersion": "oac.ctk.stdio/v1",
                "requestId": "request-1",
                "sutStatus": "COMPLETED",
                "result": result,
            },
            stage="VERDICT",
            elapsed_ms=0,
            stderr="",
        )

    monkeypatch.setattr("oac_ctk_runner.runner.invoke", fake_invoke)
    run_bundle(bundle, ("spy",))
    serialized = repr(observed_payloads)
    assert "caseId" not in serialized
    assert "expect" not in serialized
    assert "requirementRefs" not in serialized
    assert "fixtureClass" not in serialized


def test_missing_core_capability_fails_instead_of_skipping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = load_bundle(BUNDLE_PATH)

    def capabilities_only(*args: Any, **kwargs: Any) -> AdapterObservation:
        del args, kwargs
        return AdapterObservation(
            sut_status="COMPLETED",
            response={
                "protocolVersion": "oac.ctk.stdio/v1",
                "requestId": "request-1",
                "sutStatus": "COMPLETED",
                "result": {
                    "implementationId": "empty",
                    "implementationVersion": "1",
                    "adapterProtocolVersion": "oac.ctk.stdio/v1",
                    "tracks": [],
                },
            },
            stage="VERDICT",
            elapsed_ms=0,
            stderr="",
        )

    monkeypatch.setattr("oac_ctk_runner.runner.invoke", capabilities_only)
    result = run_bundle(bundle, ("empty",))
    assert result["requiredPassed"] is False
    assert all(item["caseOutcome"] == "FAIL" for item in result["caseResults"])
    assert all(item["sutStatus"] == "UNSUPPORTED" for item in result["caseResults"])


def test_embedded_resource_profile_cannot_relax_runner_owned_process_ceilings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = load_bundle(BUNDLE_PATH)
    resource_profile = copy.deepcopy(original.resource_profile)
    resource_profile["limits"]["adapterTimeoutMs"] = 30_001
    altered = Bundle(
        root=original.root,
        manifest=original.manifest,
        requirement_set=original.requirement_set,
        resource_profile=resource_profile,
        cases=original.cases,
        artifact_digests=original.artifact_digests,
    )

    def must_not_start(*args: Any, **kwargs: Any) -> AdapterObservation:
        raise AssertionError("SUT started before resource-profile admission")

    monkeypatch.setattr("oac_ctk_runner.runner.invoke", must_not_start)
    with pytest.raises(ValueError, match="runner-owned ceiling"):
        run_bundle(altered, ("never-start",))


def test_structurally_invalid_capability_cannot_self_award_a_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = load_bundle(BUNDLE_PATH)
    operations = sorted({str(case["operation"]) for case in bundle.cases})

    def invalid_capability(*args: Any, **kwargs: Any) -> AdapterObservation:
        operation = kwargs["operation"]
        result: dict[str, Any] = {}
        if operation == "capabilities":
            result = {
                "tracks": [
                    {
                        "role": "semantic-kernel",
                        "operation": item,
                        "profileId": "oac.phase-a",
                        "profileVersion": "v0.1",
                        "wireVersion": "oac.ctk.stdio/v1",
                    }
                    for item in operations
                ]
            }
        return AdapterObservation(
            sut_status="COMPLETED",
            response={
                "protocolVersion": "oac.ctk.stdio/v1",
                "requestId": "request-1",
                "sutStatus": "COMPLETED",
                "result": result,
            },
            stage="VERDICT",
            elapsed_ms=0,
            stderr="",
        )

    monkeypatch.setattr("oac_ctk_runner.runner.invoke", invalid_capability)
    result = run_bundle(bundle, ("invalid",))
    assert result["requiredPassed"] is False
    assert result["capabilityStatement"]["tracks"] == []
    assert result["capabilityStatement"]["observation"] == {
        "sutStatus": "ERROR",
        "errorCode": "CAPABILITY_STATEMENT_FIELDS_INVALID",
    }
    assert all(item["caseOutcome"] == "FAIL" for item in result["caseResults"])


def test_capability_validator_rejects_wrong_protocol_extra_duplicate_and_unknown_tracks() -> None:
    track = {
        "role": "semantic-kernel",
        "operation": "canonicalize",
        "profileId": "oac.phase-a",
        "profileVersion": "v0.1",
        "wireVersion": "oac.ctk.stdio/v1",
    }
    base = {
        "implementationId": "test",
        "implementationVersion": "1",
        "adapterProtocolVersion": "oac.ctk.stdio/v1",
        "tracks": [track],
    }
    wrong_protocol = copy.deepcopy(base)
    wrong_protocol["adapterProtocolVersion"] = "wrong"
    extra = copy.deepcopy(base)
    extra["selfCertified"] = True
    duplicate = copy.deepcopy(base)
    duplicate["tracks"] = [track, copy.deepcopy(track)]
    unknown = copy.deepcopy(base)
    unknown["tracks"][0]["operation"] = "future"
    whitespace_identity = copy.deepcopy(base)
    whitespace_identity["implementationId"] = "   "
    long_profile = copy.deepcopy(base)
    long_profile["tracks"][0]["profileId"] = "x" * 513
    empty_extension_key = copy.deepcopy(base)
    empty_extension_key["extensions"] = {"": True}
    assert _validate_capability_statement(wrong_protocol) == ("CAPABILITY_PROTOCOL_VERSION_INVALID")
    assert _validate_capability_statement(extra) == "CAPABILITY_STATEMENT_FIELDS_INVALID"
    assert _validate_capability_statement(duplicate) == "CAPABILITY_TRACK_DUPLICATE"
    assert _validate_capability_statement(unknown) == "CAPABILITY_TRACK_COORDINATE_INVALID"
    assert _validate_capability_statement(whitespace_identity) == (
        "CAPABILITY_STATEMENT_IDENTITY_INVALID"
    )
    assert _validate_capability_statement(long_profile) == ("CAPABILITY_TRACK_COORDINATE_INVALID")
    assert _validate_capability_statement(empty_extension_key) == ("CAPABILITY_EXTENSIONS_INVALID")


def test_non_blank_schema_and_runner_share_the_frozen_white_space_set() -> None:
    whitespace = tuple(
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
    schema = json.loads((SCHEMA_PATH / "CapabilityStatement.schema.json").read_bytes())
    validator = Draft202012Validator(schema)
    base = {
        "implementationId": "test",
        "implementationVersion": "1",
        "adapterProtocolVersion": "oac.ctk.stdio/v1",
        "tracks": [],
    }
    for value in (*whitespace, "".join(whitespace)):
        candidate = {**base, "implementationId": value}
        assert _validate_capability_statement(candidate) == (
            "CAPABILITY_STATEMENT_IDENTITY_INVALID"
        )
        with pytest.raises(ValidationError):
            validator.validate(candidate)

    for discriminator in ("\u001c", "\u001f", "\ufeff"):
        candidate = {**base, "implementationId": discriminator}
        assert _validate_capability_statement(candidate) is None
        validator.validate(candidate)


@pytest.mark.parametrize(
    "raw_response",
    (
        (
            '{"protocolVersion":"oac.ctk.stdio/v1","requestId":"request-1",'
            '"sutStatus":"COMPLETED","result":{},"result":{}}'
        ),
        (
            '{"protocolVersion":"oac.ctk.stdio/v1","requestId":"request-1",'
            '"sutStatus":"COMPLETED","result":{"value":NaN}}'
        ),
    ),
)
def test_adapter_response_parser_rejects_duplicate_keys_and_non_json_numbers(
    raw_response: str,
) -> None:
    code = f"import sys;sys.stdout.write({raw_response!r})"
    observation = invoke(
        (sys.executable, "-c", code),
        operation="capabilities",
        payload={},
        timeout_ms=1_000,
        max_output_bytes=4_096,
        max_json_depth=64,
    )
    assert observation.sut_status == "ERROR"
    assert observation.stage == "PARSE"
    assert observation.error_code is not None
    assert observation.error_code.startswith("ADAPTER_RESPONSE_INVALID:")


def test_adapter_process_gets_an_empty_working_directory_and_allowlisted_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "src"))
    monkeypatch.setenv("OAC_CTK_HIDDEN_EXPECTATION", "must-not-leak")
    code = """
import json, os
print(json.dumps({
    "protocolVersion": "oac.ctk.stdio/v1",
    "requestId": "request-1",
    "sutStatus": "COMPLETED",
    "result": {
        "cwd": os.getcwd(),
        "pythonPath": os.environ.get("PYTHONPATH"),
        "hidden": os.environ.get("OAC_CTK_HIDDEN_EXPECTATION"),
        "files": os.listdir("."),
    },
}))
"""
    observation = invoke(
        (sys.executable, "-c", code),
        operation="capabilities",
        payload={},
        timeout_ms=1_000,
        max_output_bytes=4_096,
        max_json_depth=64,
    )
    assert observation.sut_status == "COMPLETED"
    assert observation.response is not None
    result = observation.response["result"]
    assert result["pythonPath"] is None
    assert result["hidden"] is None
    assert result["files"] == []
    assert not str(result["cwd"]).startswith(str(ROOT))


def test_adapter_per_stream_output_ceiling_prevents_result_admission() -> None:
    code = """
import json, sys
sys.stderr.write("x" * 257)
print(json.dumps({
    "protocolVersion": "oac.ctk.stdio/v1",
    "requestId": "request-1",
    "sutStatus": "COMPLETED",
    "result": {},
}))
"""
    observation = invoke(
        (sys.executable, "-c", code),
        operation="capabilities",
        payload={},
        timeout_ms=1_000,
        max_output_bytes=256,
        max_json_depth=64,
    )
    assert observation.sut_status == "RESOURCE_EXHAUSTED"
    assert observation.stage == "RESOURCE_EXHAUSTION"
    assert observation.response is None


@pytest.mark.parametrize(
    "malformed_result",
    (
        {"reasonCodes": "NOT_AN_ARRAY"},
        {"reasonCodes": [[]]},
        {"reasonCodes": ["DUPLICATE", "DUPLICATE"]},
        {"domainVerdict": {"not": "a verdict"}},
        {"unexpected": True},
    ),
)
def test_malformed_adapter_result_is_a_case_failure_never_a_runner_crash(
    malformed_result: dict[str, Any],
) -> None:
    observation = AdapterObservation(
        sut_status="COMPLETED",
        response={
            "protocolVersion": "oac.ctk.stdio/v1",
            "requestId": "request-1",
            "sutStatus": "COMPLETED",
            "result": malformed_result,
        },
        stage="VERDICT",
        elapsed_ms=0,
        stderr="",
    )
    result = _score(
        "malformed",
        {"sutStatus": "COMPLETED", "result": {}},
        observation,
    )
    assert result["caseOutcome"] == "FAIL"
    assert result["domainVerdict"] is None


def test_malformed_expectation_is_harness_error_not_domain_reject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = load_bundle(BUNDLE_PATH)
    case = copy.deepcopy(original.cases[0])
    case["expect"]["unknownExpectation"] = True
    minimal = Bundle(
        root=original.root,
        manifest=original.manifest,
        requirement_set={
            "requirementSetId": "test",
            "standardVersion": "test",
            "profileVersion": "test",
            "required": [case["caseId"]],
            "notScored": [],
        },
        resource_profile=original.resource_profile,
        cases=(case,),
        artifact_digests=original.artifact_digests,
    )

    def completed(*args: Any, **kwargs: Any) -> AdapterObservation:
        operation = kwargs["operation"]
        result: dict[str, Any] = {}
        if operation == "capabilities":
            result = {
                "implementationId": "test",
                "implementationVersion": "1",
                "adapterProtocolVersion": "oac.ctk.stdio/v1",
                "tracks": [
                    {
                        "role": "semantic-kernel",
                        "operation": "canonicalize",
                        "profileId": "oac.phase-a",
                        "profileVersion": "v0.1",
                        "wireVersion": "oac.ctk.stdio/v1",
                    }
                ],
            }
        return AdapterObservation(
            sut_status="COMPLETED",
            response={
                "protocolVersion": "oac.ctk.stdio/v1",
                "requestId": "request-1",
                "sutStatus": "COMPLETED",
                "result": result,
            },
            stage="VERDICT",
            elapsed_ms=0,
            stderr="",
        )

    monkeypatch.setattr("oac_ctk_runner.runner.invoke", completed)
    result = run_bundle(minimal, ("test",))
    only = result["caseResults"][0]
    assert only["caseOutcome"] == "HARNESS_ERROR"
    assert only["domainVerdict"] is None
