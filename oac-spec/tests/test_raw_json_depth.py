from __future__ import annotations

import base64
import json
import subprocess
import sys
from datetime import UTC, datetime

import pytest

from oac.annotation import admit_annotation, restore_annotation_archive
from oac.canonical import OACValidationError
from oac.cli import main
from oac.ctk_adapter import PROTOCOL_VERSION as PROTOCOL_V1
from oac.ctk_adapter import handle as handle_v1
from oac.ctk_adapter_v2 import PROTOCOL_VERSION, handle
from oac.resource_profile import ResourceProfileExceeded
from oac.sealed import _decode_raw_object, admit_sealed_resource


def _nested(depth: int) -> bytes:
    return b'{"nest":' + b'[' * depth + b'0' + b']' * depth + b'}'


@pytest.mark.parametrize("depth", [65, 400, 1200])
def test_sealed_depth_errors_are_classified_before_normalization(depth):
    with pytest.raises(OACValidationError) as error:
        admit_sealed_resource(_nested(depth), "OrganizationSnapshot", max_json_depth=64)
    assert error.value.reason_code == "CONFORMANCE_RESOURCE_PROFILE_EXCEEDED"


@pytest.mark.parametrize("depth", [65, 400, 1200])
def test_annotation_depth_errors_keep_entrypoint_reason_codes(depth):
    with pytest.raises(OACValidationError) as error:
        admit_annotation(_nested(depth), "AnnotationAuthority")
    assert error.value.reason_code == "ANNOTATION_DOCUMENT_INVALID"
    with pytest.raises(OACValidationError) as error:
        restore_annotation_archive(
            _nested(depth), archive_digest="sha256:" + "0" * 64,
            authority_ref=None, packet_ref=None, clock=lambda: datetime.now(UTC),
        )
    assert error.value.reason_code == "ANNOTATION_ARCHIVE_INVALID"


def test_unbounded_decode_also_classifies_decoder_recursion():
    with pytest.raises(OACValidationError) as error:
        _decode_raw_object(_nested(1200))
    assert error.value.reason_code == "CORE_SCHEMA_INVALID"


def test_decoder_exhaustion_retains_resource_error_type_without_inventing_depth():
    with pytest.raises(ResourceProfileExceeded) as error:
        _decode_raw_object(_nested(10000), max_json_depth=64)
    assert error.value.dimension == "jsonDepth"
    assert error.value.maximum == 64
    assert error.value.observed is None


@pytest.mark.parametrize("kind", ["OrganizationSnapshot", "SemanticChangeSet", "OrganizationPlan"])
@pytest.mark.parametrize("depth", [65, 1200, 10000])
def test_ctk_resource_exhaustion_has_the_frozen_status_and_code(kind, depth):
    request = {
        "protocolVersion": PROTOCOL_VERSION, "requestId": "overdeep",
        "operation": "validateResource",
        "payload": {"expectedKind": kind, "rawBase64": base64.b64encode(_nested(depth)).decode()},
    }
    response = handle(request)
    assert response["sutStatus"] == "RESOURCE_EXHAUSTED"
    assert response["error"] == {"code": "CONFORMANCE_RESOURCE_PROFILE_EXCEEDED"}


@pytest.mark.parametrize("kind", ["OrganizationSnapshot", "SemanticChangeSet", "OrganizationPlan"])
def test_ctk_stdio_emits_resource_exhaustion_without_traceback(kind):
    request = {
        "protocolVersion": PROTOCOL_VERSION, "requestId": "overdeep",
        "operation": "validateResource",
        "payload": {"expectedKind": kind, "rawBase64": base64.b64encode(_nested(10000)).decode()},
    }
    result = subprocess.run(
        [sys.executable, "-m", "oac.ctk_adapter_v2"], input=json.dumps(request).encode(),
        capture_output=True, check=False,
    )
    assert result.returncode == 0
    assert not result.stderr
    response = json.loads(result.stdout)
    assert response["sutStatus"] == "RESOURCE_EXHAUSTED"
    assert response["error"] == {"code": "CONFORMANCE_RESOURCE_PROFILE_EXCEEDED"}


@pytest.mark.parametrize("command", ["validate", "validate-evolution", "digest", "compile", "verify", "lower"])
def test_cli_raw_entrypoints_classify_overdeep_inputs(tmp_path, capsys, command):
    path = tmp_path / "overdeep.json"
    path.write_bytes(_nested(10000))
    arguments = [command, str(path)]
    if command == "compile":
        arguments += [str(path)]
    elif command == "verify":
        arguments += [str(path), str(path)]
    elif command == "lower":
        arguments += [str(path)] * 4 + ["--admit-binding-digest", "sha256:" + "0" * 64]
    assert main(arguments) == 2
    expected = "LOWERING_INPUT_DIGEST_INVALID" if command == "lower" else "CORE_SCHEMA_INVALID"
    assert json.loads(capsys.readouterr().err)["reasonCode"] == expected


@pytest.mark.parametrize("depth", [65, 1200, 10000])
def test_ctk_v1_canonicalize_raw_json_exhaustion_preserves_protocol(depth):
    request = {
        "protocolVersion": PROTOCOL_V1, "requestId": "overdeep",
        "operation": "canonicalize", "payload": {"rawBase64": base64.b64encode(_nested(depth)).decode()},
    }
    response = handle_v1(request)
    assert response["sutStatus"] == "RESOURCE_EXHAUSTED"
    assert response["error"] == {"code": "CONFORMANCE_RESOURCE_PROFILE_EXCEEDED"}
    result = subprocess.run(
        [sys.executable, "-m", "oac.ctk_adapter"], input=json.dumps(request).encode(),
        capture_output=True, check=False,
    )
    assert result.returncode == 0
    assert not result.stderr
    assert json.loads(result.stdout) == response


@pytest.mark.parametrize("module,protocol", [("oac.ctk_adapter", PROTOCOL_V1), ("oac.ctk_adapter_v2", PROTOCOL_VERSION)])
@pytest.mark.parametrize("depth", [400, 10000])
def test_ctk_envelope_depth_is_checked_before_jcs_without_guessing_request_identity(module, protocol, depth):
    raw = (
        b'{"protocolVersion":' + json.dumps(protocol).encode()
        + b',"requestId":"overdeep","operation":"capabilities","payload":'
        + _nested(depth) + b'}'
    )
    result = subprocess.run([sys.executable, "-m", module], input=raw, capture_output=True, check=False)
    if depth == 400:
        assert result.returncode == 0
        assert not result.stderr
        response = json.loads(result.stdout)
        assert response["sutStatus"] == "RESOURCE_EXHAUSTED"
        assert response["error"] == {"code": "CONFORMANCE_RESOURCE_PROFILE_EXCEEDED"}
    else:
        assert result.returncode == 2
        assert not result.stdout
        assert result.stderr == b"adapter failure: request nesting exceeds parser capacity\n"


def test_exact_json_depth_boundary_preserves_binary64_and_string_brackets():
    # Root object, 62 lists, then a scalar are exactly 64 levels.
    decoded = _decode_raw_object(_nested(62), max_json_depth=64)
    assert isinstance(decoded["nest"], list)
    assert _decode_raw_object(b'{"literal":"[[[[\\\"","number":1e0}', max_json_depth=2) == {
        "literal": '[[[["', "number": 1,
    }
    with pytest.raises(OACValidationError) as error:
        _decode_raw_object(_nested(63), max_json_depth=64)
    assert error.value.reason_code == "CONFORMANCE_RESOURCE_PROFILE_EXCEEDED"
