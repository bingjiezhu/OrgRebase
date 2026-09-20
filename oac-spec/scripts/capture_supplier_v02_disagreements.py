#!/usr/bin/env python3
"""Capture immutable Supplier seed-2 disagreement incidents and later resolutions."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import rfc8785
from jsonschema import Draft202012Validator

try:
    from scripts.supplier_v02_casegen import admit_recipe_set, generate_cases
except ModuleNotFoundError:  # Direct `python scripts/...` execution.
    from supplier_v02_casegen import admit_recipe_set, generate_cases

ROOT = Path(__file__).resolve().parents[1]
CAPSULE_PATH = ROOT / "experiments/supplier-v02-portability/v0.2-seed-2/capsule.json"
OUTPUT_ROOT = CAPSULE_PATH.parent / "disagreements"
DEFAULT_PYTHON = ROOT / ".venv/bin/python"
INCIDENT_SCHEMA_PATH = ROOT / "ctk/schemas/DisagreementIncident.schema.json"
RESOLUTION_SCHEMA_PATH = ROOT / "ctk/schemas/DisagreementResolution.schema.json"
PROTOCOL_VERSION = "oac.ctk.stdio/v2"
PREFX_GO_DIGEST = "sha256:4b565e45f89769a9c72fac46534d7756c85bce18138c502cd738522ed5fba11c"
MAX_OUTPUT_BYTES = 16_777_216

_ENV_ALLOWLIST = {
    "COMSPEC",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
}

# DIS-008 is intentionally included: DIS-007 freezes changed-root authority,
# while DIS-008 keeps the independent residual-proof class falsifiable.
INCIDENT_SPECS: tuple[dict[str, object], ...] = (
    {
        "incidentId": "DIS-002",
        "caseId": "generated:retracted-root",
        "ruleIds": ["OAC-SUP-SEM-001"],
        "summary": "Retracted changed-root admission differed before the semantic-rule freeze.",
        "regressionRefs": [
            "tests/test_supplier_semantic_matrix.py::test_retracted_changed_subject_fails_before_closure"
        ],
    },
    {
        "incidentId": "DIS-003",
        "caseId": "generated:candidate-edge",
        "ruleIds": ["OAC-SUP-SEM-003"],
        "summary": "Candidate-edge predicate and closure authority reasons differed.",
        "regressionRefs": [
            "tests/test_supplier_semantic_matrix.py::test_candidate_edge_separates_predicate_and_closure_authority_reasons"
        ],
    },
    {
        "incidentId": "DIS-004",
        "caseId": "generated:retracted-edge",
        "ruleIds": ["OAC-SUP-SEM-002"],
        "summary": "Retracted-edge applicability-ledger retention differed.",
        "regressionRefs": [
            "tests/test_supplier_semantic_matrix.py::test_retracted_edge_remains_in_ledger_but_cannot_materialize_a_path"
        ],
    },
    {
        "incidentId": "DIS-005",
        "caseId": "generated:max-depth-one",
        "ruleIds": ["OAC-SUP-SEM-004", "OAC-SUP-SEM-007"],
        "summary": "Depth-one frontier and legacy discovery projection differed.",
        "regressionRefs": [
            "tests/test_supplier_semantic_matrix.py::test_legacy_discovery_and_implicit_boundary_identifiers_are_frozen"
        ],
    },
    {
        "incidentId": "DIS-006",
        "caseId": "generated:partial-implicit-boundary",
        "ruleIds": ["OAC-SUP-SEM-008"],
        "summary": "The implicit partial-boundary target identifier differed.",
        "regressionRefs": [
            "tests/test_supplier_semantic_matrix.py::test_legacy_discovery_and_implicit_boundary_identifiers_are_frozen"
        ],
    },
    {
        "incidentId": "DIS-007",
        "caseId": "generated:candidate-root",
        "ruleIds": ["OAC-SUP-SEM-001"],
        "summary": "Candidate changed-root Unknown authority projection differed.",
        "regressionRefs": [
            "tests/test_supplier_semantic_matrix.py::test_candidate_or_disputed_subject_is_an_unknown_root"
        ],
    },
    {
        "incidentId": "DIS-008",
        "caseId": "generated:candidate-residual-no-cut",
        "ruleIds": ["OAC-SUP-SEM-005", "OAC-SUP-SEM-006"],
        "summary": "Candidate residual non-impact proof differed without an authoritative FALSE cut.",
        "regressionRefs": [
            "tests/test_supplier_semantic_matrix.py::test_complete_boundary_residuals_distinguish_cut_unknown_and_retracted"
        ],
    },
)


class EvidenceError(RuntimeError):
    """The requested evidence cannot be captured without weakening its binding."""


def _digest(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise EvidenceError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _loads(raw: bytes, label: str) -> Any:
    try:
        return json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise EvidenceError(f"{label} is not valid JSON: {exc}") from exc


def _artifact(raw: bytes, serialization: str) -> dict[str, object]:
    if not raw:
        raise EvidenceError("an exact artifact cannot be empty")
    return {
        "serialization": serialization,
        "base64": base64.b64encode(raw).decode("ascii"),
        "rawSha256": _digest(raw),
        "sizeBytes": len(raw),
    }


def _artifact_bytes(artifact: Mapping[str, Any]) -> bytes:
    try:
        raw = base64.b64decode(artifact["base64"], validate=True)
    except (KeyError, ValueError) as exc:
        raise EvidenceError("exact artifact Base64 is invalid") from exc
    if artifact.get("sizeBytes") != len(raw) or artifact.get("rawSha256") != _digest(raw):
        raise EvidenceError("exact artifact content binding is invalid")
    return raw


def _detached_document(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    projection = {key: item for key, item in value.items() if key != field}
    expected = _digest(rfc8785.dumps(projection))
    if value.get(field) != expected:
        raise EvidenceError(f"{field} mismatch")
    return dict(value)


def _load_schema(path: Path) -> Draft202012Validator:
    value = _loads(path.read_bytes(), str(path))
    if not isinstance(value, dict):
        raise EvidenceError(f"schema is not an object: {path}")
    Draft202012Validator.check_schema(value)
    return Draft202012Validator(value)


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise EvidenceError(f"path is outside the repository: {path}") from exc


def _read_descriptor(capsule_root: Path, descriptor: Mapping[str, Any]) -> bytes:
    relative = descriptor.get("path")
    if not isinstance(relative, str) or not relative:
        raise EvidenceError("capsule descriptor path is invalid")
    path = capsule_root / relative
    raw = path.read_bytes()
    if descriptor.get("rawSha256") != _digest(raw) or descriptor.get("sizeBytes") != len(raw):
        raise EvidenceError(f"capsule descriptor content drift: {relative}")
    return raw


def _content_binding(path: Path, raw: bytes, detached_digest: str | None) -> dict[str, object]:
    return {
        "path": _relative(path),
        "rawSha256": _digest(raw),
        "detachedDigest": detached_digest,
    }


def _validate_content_bindings(bindings: Mapping[str, Any]) -> None:
    detached_fields = {
        "capsule": "capsuleDigest",
        "semanticRuleSet": "digest",
        "capabilitySet": "capabilitySetDigest",
        "recipeSet": "recipeSetDigest",
    }
    expected_names = {
        "capsule",
        "profile",
        "protocol",
        "semanticRuleSet",
        "capabilitySet",
        "recipeSet",
        "generatorSource",
    }
    if set(bindings) != expected_names:
        raise EvidenceError("incident content-binding set is invalid")
    for name, binding in bindings.items():
        if not isinstance(binding, dict):
            raise EvidenceError(f"incident content binding is invalid: {name}")
        relative = binding.get("path")
        if not isinstance(relative, str) or not relative:
            raise EvidenceError(f"incident content-binding path is invalid: {name}")
        candidate = (ROOT / relative).resolve()
        try:
            candidate.relative_to(ROOT.resolve())
        except ValueError as exc:
            raise EvidenceError(f"incident content binding escapes repository: {name}") from exc
        raw = candidate.read_bytes()
        if binding.get("rawSha256") != _digest(raw):
            raise EvidenceError(f"incident content-binding digest drift: {name}")
        detached_field = detached_fields.get(name)
        if detached_field is None:
            if binding.get("detachedDigest") is not None:
                raise EvidenceError(f"unexpected detached digest on content binding: {name}")
            continue
        value = _loads(raw, f"{name} content binding")
        if not isinstance(value, dict):
            raise EvidenceError(f"bound detached document is not an object: {name}")
        admitted = _detached_document(value, detached_field)
        if binding.get("detachedDigest") != admitted[detached_field]:
            raise EvidenceError(f"incident detached content-binding drift: {name}")


def _contract_descriptor(capsule: Mapping[str, Any], source_path: str) -> Mapping[str, Any]:
    contracts = capsule.get("contracts")
    if not isinstance(contracts, list):
        raise EvidenceError("capsule contracts are invalid")
    matches = [
        item
        for item in contracts
        if isinstance(item, dict) and item.get("sourcePath") == source_path
    ]
    if len(matches) != 1:
        raise EvidenceError(f"capsule contract binding is not unique: {source_path}")
    return matches[0]


def _load_capsule(
    capsule_path: Path,
) -> tuple[dict[str, Any], dict[str, object], list[dict[str, Any]]]:
    raw = capsule_path.read_bytes()
    value = _loads(raw, "capsule")
    if not isinstance(value, dict) or raw != rfc8785.dumps(value) + b"\n":
        raise EvidenceError("capsule must be an exact JCS object plus newline")
    capsule = _detached_document(value, "capsuleDigest")
    if (
        capsule.get("capsuleId") != "oac.supplier.transfer/v0.2/a1-seed-2"
        or capsule.get("protocolVersion") != PROTOCOL_VERSION
    ):
        raise EvidenceError("unsupported successor capsule coordinate")
    capsule_root = capsule_path.parent

    profile_descriptor = _contract_descriptor(capsule, "profiles/supplier-change/profile.md")
    protocol_descriptor = _contract_descriptor(capsule, "ctk/protocol/stdio-v2.md")
    rule_descriptor = _contract_descriptor(
        capsule, "profiles/supplier-change/semantic-rules-v0.2.json"
    )
    capability_descriptor = capsule.get("capabilitySet")
    generator = capsule.get("generator")
    if not isinstance(capability_descriptor, dict) or not isinstance(generator, dict):
        raise EvidenceError("capsule capability/generator bindings are invalid")
    recipe_descriptor = generator.get("recipeSet")
    generator_descriptor = generator.get("source")
    if not isinstance(recipe_descriptor, dict) or not isinstance(generator_descriptor, dict):
        raise EvidenceError("capsule generator descriptors are invalid")

    profile_raw = _read_descriptor(capsule_root, profile_descriptor)
    protocol_raw = _read_descriptor(capsule_root, protocol_descriptor)
    rule_raw = _read_descriptor(capsule_root, rule_descriptor)
    capability_raw = _read_descriptor(capsule_root, capability_descriptor)
    recipe_raw = _read_descriptor(capsule_root, recipe_descriptor)
    generator_raw = _read_descriptor(capsule_root, generator_descriptor)
    rule_set = _loads(rule_raw, "semantic rule set")
    capability_set = _loads(capability_raw, "capability set")
    if not isinstance(rule_set, dict) or not isinstance(capability_set, dict):
        raise EvidenceError("semantic/capability binding is not an object")
    _detached_document(rule_set, "digest")
    _detached_document(capability_set, "capabilitySetDigest")
    recipe_set = admit_recipe_set(recipe_raw)

    bindings = {
        "capsule": _content_binding(capsule_path, raw, capsule["capsuleDigest"]),
        "profile": _content_binding(capsule_root / profile_descriptor["path"], profile_raw, None),
        "protocol": _content_binding(
            capsule_root / protocol_descriptor["path"], protocol_raw, None
        ),
        "semanticRuleSet": _content_binding(
            capsule_root / rule_descriptor["path"], rule_raw, rule_set["digest"]
        ),
        "capabilitySet": _content_binding(
            capsule_root / capability_descriptor["path"],
            capability_raw,
            capability_set["capabilitySetDigest"],
        ),
        "recipeSet": _content_binding(
            capsule_root / recipe_descriptor["path"],
            recipe_raw,
            recipe_set["recipeSetDigest"],
        ),
        "generatorSource": _content_binding(
            capsule_root / generator_descriptor["path"], generator_raw, None
        ),
    }

    frozen_cases: list[dict[str, Any]] = []
    raw_cases = capsule.get("cases")
    if not isinstance(raw_cases, list):
        raise EvidenceError("capsule cases are invalid")
    for item in raw_cases:
        if not isinstance(item, dict):
            raise EvidenceError("capsule case is not an object")
        snapshot_descriptor = item.get("snapshot")
        change_descriptor = item.get("change")
        if not isinstance(snapshot_descriptor, dict) or not isinstance(change_descriptor, dict):
            raise EvidenceError("capsule root descriptor is invalid")
        frozen_cases.append(
            {
                "caseId": item.get("caseId"),
                "snapshot": _read_descriptor(capsule_root, snapshot_descriptor),
                "change": _read_descriptor(capsule_root, change_descriptor),
            }
        )
    generated = generate_cases(recipe_set, frozen_cases)
    return capsule, bindings, generated


def _parse_command(value: str) -> list[str]:
    command = shlex.split(value)
    if not command:
        raise EvidenceError("adapter command cannot be empty")
    executable = Path(command[0]).expanduser()
    selected = (
        executable.absolute()
        if executable.is_absolute() or "/" in command[0]
        else Path(shutil.which(command[0]) or "").absolute()
    )
    if not selected.is_file():
        raise EvidenceError(f"adapter executable is missing: {selected}")
    # Preserve a venv launcher/symlink in the command: resolving it to the base
    # interpreter would silently change Python's import environment.
    command[0] = str(selected)
    return command


def _executable(command: Sequence[str]) -> dict[str, object]:
    path = Path(command[0]).absolute()
    if not path.is_file():
        raise EvidenceError(f"adapter executable is missing: {path}")
    raw = path.read_bytes()
    return {"path": str(path), "rawSha256": _digest(raw), "sizeBytes": len(raw)}


def _source_ledger(source_root: Path, suffixes: set[str]) -> dict[str, object]:
    files: list[dict[str, object]] = []
    for path in sorted(item for item in source_root.rglob("*") if item.is_file()):
        if path.name == "go.mod" or path.suffix in suffixes:
            raw = path.read_bytes()
            files.append(
                {
                    "path": _relative(path),
                    "rawSha256": _digest(raw),
                    "sizeBytes": len(raw),
                }
            )
    if not files:
        raise EvidenceError(f"source ledger is empty: {source_root}")
    return {
        "identityKind": "sha256-jcs-source-ledger/v1",
        "rootRef": _relative(source_root),
        "sourceDigest": _digest(rfc8785.dumps(files)),
        "files": files,
        "buildInfo": None,
        "attestation": "Exact repository-relative source ledger captured with the observation.",
    }


def _historical_go_source(command: Sequence[str]) -> dict[str, object]:
    completed = subprocess.run(
        ["go", "version", "-m", command[0]],
        capture_output=True,
        check=False,
        timeout=10,
    )
    build_info = completed.stdout + completed.stderr
    if completed.returncode != 0 or not build_info:
        raise EvidenceError("cannot capture historical Go build information")
    return {
        "identityKind": "historical-source-unavailable/v1",
        "rootRef": "implementations/go-supplier-v02-internal",
        "sourceDigest": None,
        "files": [],
        "buildInfo": _artifact(build_info, "raw-bytes/v1"),
        "attestation": (
            "The exact source closure for this pre-fix binary was not retained. "
            "Current repository source must not be substituted for it."
        ),
    }


def _request(value: Mapping[str, Any]) -> bytes:
    return rfc8785.dumps(value) + b"\n"


def _opaque_request_id(domain: str, material: bytes) -> str:
    value = hashlib.sha256(domain.encode("ascii") + b"\x00" + material).hexdigest()
    return f"req-{value[:24]}"


def _invoke(
    command: Sequence[str], request: bytes, expected_request_id: str
) -> tuple[dict[str, Any], dict[str, object], dict[str, object]]:
    environment = {key: value for key, value in os.environ.items() if key in _ENV_ALLOWLIST}
    environment["PYTHONHASHSEED"] = "0"
    with tempfile.TemporaryDirectory(prefix="oac-disagreement-sut-") as temporary:
        completed = subprocess.run(
            list(command),
            input=request,
            capture_output=True,
            check=False,
            cwd=temporary,
            env=environment,
            timeout=10,
        )
    if completed.returncode != 0:
        raise EvidenceError(
            f"adapter exited {completed.returncode}: "
            f"{completed.stderr.decode('utf-8', errors='replace')}"
        )
    if not completed.stdout or len(completed.stdout) > MAX_OUTPUT_BYTES:
        raise EvidenceError("adapter output is empty or over the evidence ceiling")
    value = _loads(completed.stdout, "adapter response")
    if not isinstance(value, dict):
        raise EvidenceError("adapter response is not an object")
    if (
        value.get("protocolVersion") != PROTOCOL_VERSION
        or value.get("requestId") != expected_request_id
    ):
        raise EvidenceError("adapter response does not bind the request")
    canonical = rfc8785.dumps(value)
    return (
        value,
        _artifact(completed.stdout, "json-wire/v1"),
        _artifact(canonical, "rfc8785+jcs/v1"),
    )


def _semantic_projection(response: Mapping[str, Any]) -> dict[str, object]:
    status = response.get("sutStatus")
    if status not in {"COMPLETED", "ERROR", "UNSUPPORTED", "RESOURCE_EXHAUSTED"}:
        raise EvidenceError("adapter response has an invalid sutStatus")
    error_code: str | None = None
    report_digest: str | None = None
    if status == "COMPLETED":
        result = response.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("report"), dict):
            raise EvidenceError("completed derive response has no report")
        report_digest = _digest(rfc8785.dumps(result["report"]))
        if result.get("reportDigest") != report_digest:
            raise EvidenceError("SUT reportDigest does not bind returned report")
    else:
        error = response.get("error")
        if not isinstance(error, dict) or not isinstance(error.get("code"), str):
            raise EvidenceError("incomplete response has no error code")
        error_code = error["code"]
    projection: dict[str, object] = {
        "sutStatus": status,
        "errorCode": error_code,
        "reportDigest": report_digest,
    }
    projection["projectionDigest"] = _digest(rfc8785.dumps(projection))
    return projection


def _capability_material(
    command: Sequence[str], executable: Mapping[str, Any]
) -> tuple[dict[str, object], str, str, dict[str, object], dict[str, object]]:
    executable_digest = executable["rawSha256"]
    if not isinstance(executable_digest, str):
        raise EvidenceError("executable digest is invalid")
    request_id = _opaque_request_id("capabilities", executable_digest.encode("ascii"))
    request = _request(
        {
            "protocolVersion": PROTOCOL_VERSION,
            "requestId": request_id,
            "operation": "capabilities",
            "payload": {},
        }
    )
    response, wire, canonical = _invoke(command, request, request_id)
    if response.get("sutStatus") != "COMPLETED" or not isinstance(response.get("result"), dict):
        raise EvidenceError("capabilities did not complete")
    result = response["result"]
    implementation_id = result.get("implementationId")
    implementation_version = result.get("implementationVersion")
    if not isinstance(implementation_id, str) or not isinstance(implementation_version, str):
        raise EvidenceError("capability implementation identity is invalid")
    capability_digest = _digest(rfc8785.dumps(result))
    return (
        _artifact(request, "rfc8785+jcs+lf/v1"),
        implementation_id,
        implementation_version,
        wire,
        {**canonical, "capabilityStatementDigest": capability_digest},
    )


def _observation(
    command: Sequence[str],
    source_identity: Mapping[str, Any],
    derive_request: bytes,
    derive_request_id: str,
) -> dict[str, object]:
    executable = _executable(command)
    (
        capability_request,
        implementation_id,
        implementation_version,
        capability_wire,
        capability_canonical,
    ) = _capability_material(command, executable)
    capability_statement_digest = capability_canonical.pop("capabilityStatementDigest")
    response, wire_response, canonical_response = _invoke(
        command, derive_request, derive_request_id
    )
    return {
        "implementationId": implementation_id,
        "implementationVersion": implementation_version,
        "command": list(command),
        "commandDigest": _digest(rfc8785.dumps(list(command))),
        "executable": executable,
        "sourceIdentity": dict(source_identity),
        "capabilityRequest": capability_request,
        "capabilityWireResponse": capability_wire,
        "capabilityResponseJcs": capability_canonical,
        "capabilityStatementDigest": capability_statement_digest,
        "deriveWireResponse": wire_response,
        "deriveResponseJcs": canonical_response,
        "semanticProjection": _semantic_projection(response),
    }


def _validate_exact_json_artifact(
    artifact: Mapping[str, Any], *, line_feed: bool
) -> dict[str, Any]:
    raw = _artifact_bytes(artifact)
    value = _loads(raw, "embedded exact JSON artifact")
    if not isinstance(value, dict):
        raise EvidenceError("embedded exact JSON artifact is not an object")
    expected = rfc8785.dumps(value) + (b"\n" if line_feed else b"")
    if raw != expected:
        raise EvidenceError("embedded exact JSON artifact is not exact JCS")
    return value


def _validate_observation(observation: Mapping[str, Any], derive_request_id: str) -> None:
    command = observation.get("command")
    if not isinstance(command, list) or observation.get("commandDigest") != _digest(
        rfc8785.dumps(command)
    ):
        raise EvidenceError("observation command binding is invalid")
    source = observation.get("sourceIdentity")
    if not isinstance(source, dict):
        raise EvidenceError("observation source identity is invalid")
    if source.get("identityKind") == "sha256-jcs-source-ledger/v1":
        files = source.get("files")
        if not isinstance(files, list) or source.get("sourceDigest") != _digest(
            rfc8785.dumps(files)
        ):
            raise EvidenceError("source ledger digest is invalid")
    else:
        build_info = source.get("buildInfo")
        if not isinstance(build_info, dict):
            raise EvidenceError("historical source gap has no build information")
        _artifact_bytes(build_info)

    capability_request = _validate_exact_json_artifact(
        observation["capabilityRequest"], line_feed=True
    )
    capability_request_id = capability_request.get("requestId")
    if not isinstance(capability_request_id, str):
        raise EvidenceError("capability requestId is invalid")
    capability_wire = _artifact_bytes(observation["capabilityWireResponse"])
    capability_value = _loads(capability_wire, "capability wire response")
    if not isinstance(capability_value, dict):
        raise EvidenceError("capability wire response is not an object")
    capability_jcs = _validate_exact_json_artifact(
        observation["capabilityResponseJcs"], line_feed=False
    )
    if (
        capability_value != capability_jcs
        or capability_value.get("requestId") != capability_request_id
    ):
        raise EvidenceError("capability wire/JCS/request binding is invalid")
    capability_result = capability_value.get("result")
    if not isinstance(capability_result, dict) or observation.get(
        "capabilityStatementDigest"
    ) != _digest(rfc8785.dumps(capability_result)):
        raise EvidenceError("capability statement binding is invalid")
    if capability_result.get("implementationId") != observation.get(
        "implementationId"
    ) or capability_result.get("implementationVersion") != observation.get("implementationVersion"):
        raise EvidenceError("capability and observation identities differ")

    derive_wire = _artifact_bytes(observation["deriveWireResponse"])
    derive_value = _loads(derive_wire, "derive wire response")
    if not isinstance(derive_value, dict):
        raise EvidenceError("derive wire response is not an object")
    derive_jcs = _validate_exact_json_artifact(observation["deriveResponseJcs"], line_feed=False)
    if derive_value != derive_jcs or derive_value.get("requestId") != derive_request_id:
        raise EvidenceError("derive wire/JCS/request binding is invalid")
    if observation.get("semanticProjection") != _semantic_projection(derive_value):
        raise EvidenceError("derive semantic projection binding is invalid")


def _resource(value: bytes, expected_kind: str) -> dict[str, object]:
    decoded = _loads(value, expected_kind)
    if not isinstance(decoded, dict) or decoded.get("kind") != expected_kind:
        raise EvidenceError(f"generated {expected_kind} is invalid")
    resource_digest = decoded.get("digest")
    if not isinstance(resource_digest, str):
        raise EvidenceError(f"generated {expected_kind} has no digest")
    if value != rfc8785.dumps(decoded):
        raise EvidenceError(f"generated {expected_kind} is not exact JCS")
    return {
        "kind": expected_kind,
        "resourceDigest": resource_digest,
        "exactJcs": _artifact(value, "rfc8785+jcs/v1"),
    }


def _seal_document(value: dict[str, object]) -> bytes:
    projection = {key: item for key, item in value.items() if key != "digest"}
    value["digest"] = _digest(rfc8785.dumps(projection))
    return rfc8785.dumps(value) + b"\n"


def _write_immutable(path: Path, raw: bytes) -> None:
    if path.exists():
        if path.read_bytes() != raw:
            raise EvidenceError(f"immutable evidence already exists with other bytes: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def _incident_filename(spec: Mapping[str, object]) -> str:
    case_id = str(spec["caseId"]).removeprefix("generated:")
    return f"{spec['incidentId']}-{case_id}.incident.json"


def capture(
    *,
    capsule_path: Path,
    output_root: Path,
    python_command: Sequence[str],
    go_command: Sequence[str],
    expected_go_digest: str,
) -> list[Path]:
    _, bindings, generated_cases = _load_capsule(capsule_path)
    generated = {item["caseId"]: item for item in generated_cases}
    python_source = _source_ledger(ROOT / "src/oac", {".py"})
    go_source = _historical_go_source(go_command)
    go_executable = _executable(go_command)
    if go_executable["rawSha256"] != expected_go_digest:
        raise EvidenceError("capture Go binary does not match the declared pre-fix trust anchor")
    validator = _load_schema(INCIDENT_SCHEMA_PATH)
    written: list[Path] = []
    for spec in INCIDENT_SPECS:
        case_id = spec["caseId"]
        case = generated.get(case_id)
        if case is None:
            raise EvidenceError(f"required incident recipe is absent: {case_id}")
        snapshot = case["snapshot"]
        change = case["change"]
        input_digest = _digest(snapshot + b"\x00" + change)
        if case.get("inputDigest") != input_digest:
            raise EvidenceError(f"generated input digest drift: {case_id}")
        request_id = _opaque_request_id("derive", snapshot + b"\x00" + change)
        request = _request(
            {
                "protocolVersion": PROTOCOL_VERSION,
                "requestId": request_id,
                "operation": "derive",
                "payload": {
                    "snapshotBase64": base64.b64encode(snapshot).decode("ascii"),
                    "changeBase64": base64.b64encode(change).decode("ascii"),
                },
            }
        )
        if str(case_id).encode() in request or str(spec["incidentId"]).encode() in request:
            raise EvidenceError("request ID or payload leaks evidence metadata")
        observations = [
            _observation(python_command, python_source, request, request_id),
            _observation(go_command, go_source, request, request_id),
        ]
        left = observations[0]["semanticProjection"]["projectionDigest"]
        right = observations[1]["semanticProjection"]["projectionDigest"]
        if left == right:
            raise EvidenceError(
                f"historical disagreement is not reproduced; refusing incident: {case_id}"
            )
        incident: dict[str, object] = {
            "apiVersion": "oac.ctk.disagreement-incident/v0.1",
            "kind": "DisagreementIncident",
            "incidentId": spec["incidentId"],
            "status": "HISTORICAL",
            "stage": "DERIVATION",
            "classification": "CONTESTED_SEMANTICS",
            "semanticClass": case["semanticClass"],
            "recipeCase": {
                "caseId": case_id,
                "publicSeed": case["publicSeed"],
                "recipeIndex": case["recipeIndex"],
                "inputDigest": input_digest,
            },
            "bindings": bindings,
            "input": {
                "snapshot": _resource(snapshot, "OrganizationSnapshot"),
                "change": _resource(change, "SemanticChangeSet"),
                "pairDigest": input_digest,
            },
            "request": _artifact(request, "rfc8785+jcs+lf/v1"),
            "observations": observations,
            "difference": {
                "observed": True,
                "leftProjectionDigest": left,
                "rightProjectionDigest": right,
                "summary": spec["summary"],
            },
        }
        raw = _seal_document(incident)
        admitted = _loads(raw, str(spec["incidentId"]))
        validator.validate(admitted)
        path = output_root / _incident_filename(spec)
        _write_immutable(path, raw)
        written.append(path)
    return written


def _load_incident(path: Path, validator: Draft202012Validator) -> dict[str, Any]:
    raw = path.read_bytes()
    value = _loads(raw, str(path))
    if not isinstance(value, dict) or raw != rfc8785.dumps(value) + b"\n":
        raise EvidenceError(f"incident is not exact JCS plus newline: {path}")
    validator.validate(value)
    _detached_document(value, "digest")
    _validate_content_bindings(value["bindings"])
    for resource_name in ("snapshot", "change"):
        exact = value["input"][resource_name]["exactJcs"]
        _artifact_bytes(exact)
    request = _artifact_bytes(value["request"])
    request_value = _loads(request, f"{path} request")
    if not isinstance(request_value, dict):
        raise EvidenceError("incident request is not an object")
    if request != rfc8785.dumps(request_value) + b"\n":
        raise EvidenceError("incident request is not exact JCS plus newline")
    request_id = request_value.get("requestId")
    if not isinstance(request_id, str):
        raise EvidenceError("incident requestId is invalid")
    case_id = value["recipeCase"]["caseId"]
    if case_id.encode() in request or value["incidentId"].encode() in request:
        raise EvidenceError("incident request leaks evidence metadata")
    for observation in value["observations"]:
        _validate_observation(observation, request_id)
    left = value["observations"][0]["semanticProjection"]["projectionDigest"]
    right = value["observations"][1]["semanticProjection"]["projectionDigest"]
    if (
        left == right
        or value["difference"]["leftProjectionDigest"] != left
        or value["difference"]["rightProjectionDigest"] != right
    ):
        raise EvidenceError("incident does not bind an observed semantic difference")
    return value


def resolve(
    *,
    output_root: Path,
    python_command: Sequence[str],
    go_command: Sequence[str],
) -> list[Path]:
    incident_validator = _load_schema(INCIDENT_SCHEMA_PATH)
    resolution_validator = _load_schema(RESOLUTION_SCHEMA_PATH)
    python_source = _source_ledger(ROOT / "src/oac", {".py"})
    go_source = _source_ledger(ROOT / "implementations/go-supplier-v02-internal", {".go"})
    pending: list[tuple[Path, bytes]] = []
    for spec in INCIDENT_SPECS:
        incident_path = output_root / _incident_filename(spec)
        incident = _load_incident(incident_path, incident_validator)
        if incident["incidentId"] != spec["incidentId"]:
            raise EvidenceError("incident filename and identity differ")
        request = _artifact_bytes(incident["request"])
        request_value = _loads(request, f"{incident_path} request")
        request_id = request_value.get("requestId")
        if not isinstance(request_id, str):
            raise EvidenceError("incident requestId is invalid")
        observations = [
            _observation(python_command, python_source, request, request_id),
            _observation(go_command, go_source, request, request_id),
        ]
        left = observations[0]["semanticProjection"]["projectionDigest"]
        right = observations[1]["semanticProjection"]["projectionDigest"]
        if left != right:
            raise EvidenceError(
                f"{incident['incidentId']} remains unresolved; no resolution was written"
            )
        incident_relative = _relative(incident_path)
        resolution: dict[str, object] = {
            "apiVersion": "oac.ctk.disagreement-resolution/v0.1",
            "kind": "DisagreementResolution",
            "resolutionId": f"RES-{incident['incidentId']}",
            "incidentRef": {
                "incidentId": incident["incidentId"],
                "path": incident_relative,
                "digest": incident["digest"],
            },
            "status": "RESOLVED",
            "ruleIds": spec["ruleIds"],
            "ruleSetDigest": incident["bindings"]["semanticRuleSet"]["detachedDigest"],
            "fixedObservations": observations,
            "equivalence": {
                "exactSemanticMatch": True,
                "leftProjectionDigest": left,
                "rightProjectionDigest": right,
                "projectionDigest": left,
            },
            "regressionRefs": spec["regressionRefs"],
        }
        raw = _seal_document(resolution)
        admitted = _loads(raw, str(resolution["resolutionId"]))
        resolution_validator.validate(admitted)
        resolution_name = _incident_filename(spec).replace(".incident.json", ".resolution.json")
        pending.append((output_root / resolution_name, raw))
    for path, raw in pending:
        _write_immutable(path, raw)
    return [path for path, _ in pending]


def check(output_root: Path) -> list[Path]:
    incident_validator = _load_schema(INCIDENT_SCHEMA_PATH)
    resolution_validator = _load_schema(RESOLUTION_SCHEMA_PATH)
    checked: list[Path] = []
    for spec in INCIDENT_SPECS:
        incident_path = output_root / _incident_filename(spec)
        incident = _load_incident(incident_path, incident_validator)
        checked.append(incident_path)
        resolution_path = output_root / _incident_filename(spec).replace(
            ".incident.json", ".resolution.json"
        )
        if resolution_path.exists():
            raw = resolution_path.read_bytes()
            value = _loads(raw, str(resolution_path))
            if not isinstance(value, dict) or raw != rfc8785.dumps(value) + b"\n":
                raise EvidenceError("resolution is not exact JCS plus newline")
            resolution_validator.validate(value)
            _detached_document(value, "digest")
            if (
                value["incidentRef"]["path"] != _relative(incident_path)
                or value["incidentRef"]["digest"] != incident["digest"]
                or value["incidentRef"]["incidentId"] != incident["incidentId"]
            ):
                raise EvidenceError("resolution does not bind its immutable incident")
            request = _artifact_bytes(incident["request"])
            request_value = _loads(request, f"{incident_path} request")
            request_id = request_value.get("requestId")
            if not isinstance(request_id, str):
                raise EvidenceError("incident requestId is invalid")
            for observation in value["fixedObservations"]:
                _validate_observation(observation, request_id)
            left = value["fixedObservations"][0]["semanticProjection"]["projectionDigest"]
            right = value["fixedObservations"][1]["semanticProjection"]["projectionDigest"]
            if (
                left != right
                or value["equivalence"]["leftProjectionDigest"] != left
                or value["equivalence"]["rightProjectionDigest"] != right
                or value["equivalence"]["projectionDigest"] != left
                or value["ruleSetDigest"]
                != incident["bindings"]["semanticRuleSet"]["detachedDigest"]
            ):
                raise EvidenceError("resolution equivalence binding is invalid")
            checked.append(resolution_path)
    return checked


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resolve", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--capsule", type=Path, default=CAPSULE_PATH)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_ROOT)
    parser.add_argument(
        "--python-command",
        default=(
            f"{shlex.quote(str(DEFAULT_PYTHON if DEFAULT_PYTHON.is_file() else sys.executable))} "
            "-m oac.ctk_adapter_v2"
        ),
    )
    parser.add_argument(
        "--go-command",
        help="required for capture/resolve; ignored by --check",
    )
    parser.add_argument("--expected-go-digest", default=PREFX_GO_DIGEST)
    args = parser.parse_args(argv)
    if args.check and args.resolve:
        parser.error("--check and --resolve are mutually exclusive")
    if not args.check and args.go_command is None:
        parser.error("--go-command is required unless --check is used")
    try:
        if args.check:
            paths = check(args.output_dir)
        elif args.resolve:
            paths = resolve(
                output_root=args.output_dir,
                python_command=_parse_command(args.python_command),
                go_command=_parse_command(args.go_command),
            )
        else:
            paths = capture(
                capsule_path=args.capsule,
                output_root=args.output_dir,
                python_command=_parse_command(args.python_command),
                go_command=_parse_command(args.go_command),
                expected_go_digest=args.expected_go_digest,
            )
        for path in paths:
            try:
                display = _relative(path)
            except EvidenceError:
                display = str(path.absolute())
            print(display)
        return 0
    except (EvidenceError, OSError, subprocess.SubprocessError) as exc:
        print(f"evidence capture failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
