from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import rfc8785
from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parents[1]
CHECKER_PATH = ROOT / "scripts/check_supplier_v02_evidence_manifest.py"
CAPSULE_BUILDER_PATH = ROOT / "scripts/build_supplier_v02_capsule.py"
MANIFEST_PATH = ROOT / "experiments/supplier-v02-portability/v0.2-seed-2/evidence-manifest.json"
SCHEMA_PATH = ROOT / "ctk/schemas/SupplierPortabilityEvidenceManifest.schema.json"
SOURCE_SUMMARY_PATH = ROOT / "experiments/supplier-v02-portability/v0.2-seed-2/parity-summary.json"
INSTALLED_SUMMARY_PATH = (
    ROOT
    / "experiments/supplier-v02-portability/v0.2-seed-2/replay"
    / "seed2-installed-parity-summary.json"
)
DISAGREEMENT_ROOT = ROOT / "experiments/supplier-v02-portability/v0.2-seed-2/disagreements"


def _load_module(path: Path, name: str) -> ModuleType:
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


CHECKER = _load_module(CHECKER_PATH, "_test_supplier_v02_evidence_manifest")
CAPSULE_BUILDER = _load_module(
    CAPSULE_BUILDER_PATH,
    "_test_supplier_v02_capsule_builder",
)


def _digest(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _manifest() -> dict[str, Any]:
    assert MANIFEST_PATH.is_file(), "the release evidence manifest must be committed"
    value = json.loads(MANIFEST_PATH.read_bytes())
    assert isinstance(value, dict)
    return value


def _reseal(value: dict[str, Any]) -> bytes:
    projection = {key: item for key, item in value.items() if key != "digest"}
    value["digest"] = _digest(rfc8785.dumps(projection))
    return rfc8785.dumps(value) + b"\n"


def _write_jcs(path: Path, value: dict[str, Any]) -> None:
    path.write_bytes(rfc8785.dumps(value) + b"\n")


def _object_schemas(
    value: Any, path: tuple[str | int, ...] = ()
) -> Iterator[tuple[tuple[str | int, ...], Mapping[str, Any]]]:
    if isinstance(value, Mapping):
        if value.get("type") == "object":
            yield path, value
        for key, item in value.items():
            yield from _object_schemas(item, (*path, key))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            yield from _object_schemas(item, (*path, index))


def _difference_paths(
    left: Any, right: Any, path: tuple[str | int, ...] = ()
) -> Iterator[tuple[str | int, ...]]:
    if type(left) is not type(right):
        yield path
        return
    if isinstance(left, dict):
        for key in sorted(set(left) | set(right)):
            if key not in left or key not in right:
                yield (*path, key)
            else:
                yield from _difference_paths(left[key], right[key], (*path, key))
        return
    if isinstance(left, list):
        if len(left) != len(right):
            yield (*path, "length")
            return
        for index, (left_item, right_item) in enumerate(zip(left, right, strict=True)):
            yield from _difference_paths(left_item, right_item, (*path, index))
        return
    if left != right:
        yield path


def test_schema_is_valid_and_every_declared_object_is_closed() -> None:
    schema = json.loads(SCHEMA_PATH.read_bytes())
    Draft202012Validator.check_schema(schema)

    object_schemas = list(_object_schemas(schema))
    assert object_schemas
    assert [
        path for path, value in object_schemas if value.get("additionalProperties") is not False
    ] == []

    validator = Draft202012Validator(schema)
    manifest = _manifest()
    assert list(validator.iter_errors(manifest)) == []

    root_extension = copy.deepcopy(manifest)
    root_extension["unboundEvidence"] = True
    assert list(validator.iter_errors(root_extension))

    nested_extension = copy.deepcopy(manifest)
    nested_extension["summaries"]["relationship"]["unboundEvidence"] = True
    assert list(validator.iter_errors(nested_extension))


def test_checked_manifest_is_exact_jcs_lf_and_equals_repository_evidence() -> None:
    manifest = _manifest()
    raw = MANIFEST_PATH.read_bytes()

    assert raw == rfc8785.dumps(manifest) + b"\n"
    projection = {key: item for key, item in manifest.items() if key != "digest"}
    assert manifest["digest"] == _digest(rfc8785.dumps(projection))
    assert CHECKER.check(MANIFEST_PATH) == manifest
    assert CHECKER.build_manifest() == manifest


def test_published_capsule_is_admitted_without_rewriting_later_global_registries() -> None:
    capsule = CAPSULE_BUILDER.check()
    assert capsule["capsuleDigest"] == CAPSULE_BUILDER.PUBLISHED_CAPSULE_DIGEST
    archived_registry = (
        MANIFEST_PATH.parent / "contracts/schemas/kind-registry.json"
    ).read_bytes()
    current_registry = (ROOT / "schemas/kind-registry.json").read_bytes()
    assert archived_registry != current_registry
    descriptor = next(
        item
        for item in capsule["contracts"]
        if item["sourcePath"] == "schemas/kind-registry.json"
    )
    assert descriptor["rawSha256"] == _digest(archived_registry)


def test_checker_rejects_noncanonical_or_missing_lf(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    invalid_encodings = (
        rfc8785.dumps(manifest),
        json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8") + b"\n",
    )

    for index, raw in enumerate(invalid_encodings):
        candidate = tmp_path / f"noncanonical-{index}.json"
        candidate.write_bytes(raw)
        with pytest.raises(CHECKER.EvidenceManifestError, match="not exact JCS plus LF"):
            CHECKER.check(candidate)


def test_resealed_schema_valid_semantic_tamper_is_still_rejected(
    tmp_path: Path,
) -> None:
    tampered = copy.deepcopy(_manifest())
    python = next(
        item for item in tampered["implementations"]["production"] if item["language"] == "Python"
    )
    python["implementationVersion"] = "forged-but-schema-valid"
    raw = _reseal(tampered)

    schema = json.loads(SCHEMA_PATH.read_bytes())
    Draft202012Validator(schema).validate(tampered)
    candidate = tmp_path / "resealed-semantic-tamper.json"
    candidate.write_bytes(raw)

    with pytest.raises(
        CHECKER.EvidenceManifestError,
        match="does not equal repository evidence",
    ):
        CHECKER.check(candidate)


def test_checker_rejects_duplicate_keys_and_repository_path_escape(
    tmp_path: Path,
) -> None:
    raw = MANIFEST_PATH.read_bytes()
    assert raw.startswith(b'{"apiVersion":')
    duplicate = raw.replace(
        b'{"apiVersion":',
        b'{"apiVersion":"duplicate","apiVersion":',
        1,
    )
    duplicate_path = tmp_path / "duplicate-key.json"
    duplicate_path.write_bytes(duplicate)
    with pytest.raises(CHECKER.EvidenceManifestError, match="duplicate JSON key"):
        CHECKER.check(duplicate_path)

    escaped = copy.deepcopy(_manifest())
    escaped["schema"]["path"] = "experiments/../../outside.json"
    escaped_path = tmp_path / "path-escape.json"
    escaped_path.write_bytes(_reseal(escaped))
    with pytest.raises(ValidationError):
        CHECKER.check(escaped_path)


def test_manifest_binds_exactly_seven_content_addressed_disagreement_pairs() -> None:
    pairs = _manifest()["disagreements"]
    assert [pair["incidentId"] for pair in pairs] == [f"DIS-{index:03d}" for index in range(2, 9)]
    assert len({pair["incident"]["path"] for pair in pairs}) == 7
    assert len({pair["resolution"]["path"] for pair in pairs}) == 7

    for pair in pairs:
        incident_id = pair["incidentId"]
        incident_descriptor = pair["incident"]
        resolution_descriptor = pair["resolution"]
        assert incident_descriptor["detachedField"] == "digest"
        assert resolution_descriptor["detachedField"] == "digest"

        incident_path = ROOT / incident_descriptor["path"]
        resolution_path = ROOT / resolution_descriptor["path"]
        assert incident_path.parent == DISAGREEMENT_ROOT
        assert resolution_path.parent == DISAGREEMENT_ROOT

        incident_raw = incident_path.read_bytes()
        resolution_raw = resolution_path.read_bytes()
        assert incident_descriptor["rawSha256"] == _digest(incident_raw)
        assert resolution_descriptor["rawSha256"] == _digest(resolution_raw)

        incident = json.loads(incident_raw)
        resolution = json.loads(resolution_raw)
        incident_projection = {key: item for key, item in incident.items() if key != "digest"}
        resolution_projection = {key: item for key, item in resolution.items() if key != "digest"}
        assert incident_descriptor["detachedDigest"] == _digest(rfc8785.dumps(incident_projection))
        assert resolution_descriptor["detachedDigest"] == _digest(
            rfc8785.dumps(resolution_projection)
        )
        assert incident["incidentId"] == incident_id
        assert resolution["incidentRef"] == {
            "incidentId": incident_id,
            "digest": incident_descriptor["detachedDigest"],
            "path": incident_descriptor["path"],
        }


def test_installed_replay_diff_is_limited_to_python_command_identity() -> None:
    source = json.loads(SOURCE_SUMMARY_PATH.read_bytes())
    installed = json.loads(INSTALLED_SUMMARY_PATH.read_bytes())

    assert set(_difference_paths(source, installed)) == {
        ("implementations", 0, "commandDigest"),
        (
            "observationManifest",
            "implementationMaterials",
            0,
            "commandDigest",
        ),
        ("observationManifestDigest",),
    }
    assert source["implementations"][1] == installed["implementations"][1]
    assert _manifest()["summaries"]["relationship"] == {
        "model": "same-evidence-except-python-command-identity/v1",
        "rawSummariesDiffer": True,
        "observationManifestDigestsDiffer": True,
        "pythonCommandDigestsDiffer": True,
        "goCommandDigestsEqual": True,
        "allOtherSummaryFieldsEqual": True,
    }


def test_standalone_checker_live_replays_generated_observations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    summary = json.loads(SOURCE_SUMMARY_PATH.read_bytes())
    observation = next(
        item
        for item in summary["observationManifest"]["observations"]
        if item["caseId"] == "generated:scope-order"
    )
    forged_digest = "sha256:" + "0" * 64
    observation["leftObservationDigest"] = forged_digest
    observation["rightObservationDigest"] = forged_digest
    summary["observationManifestDigest"] = _digest(rfc8785.dumps(summary["observationManifest"]))
    _write_jcs(tmp_path / "parity-summary.json", summary)

    monkeypatch.setattr(CHECKER, "SEED_ROOT", tmp_path)
    CHECKER._validate_live_source_parity.cache_clear()
    with pytest.raises(
        CHECKER.EvidenceManifestError,
        match="live Supplier seed-2 parity does not match",
    ):
        CHECKER._validate_live_source_parity()
    CHECKER._validate_live_source_parity.cache_clear()


def test_legacy_replay_aggregate_is_derived_from_case_results(tmp_path: Path) -> None:
    replay = json.loads((MANIFEST_PATH.parent / "replay/installed-legacy-tck.json").read_bytes())
    replay["results"][0]["passed"] = False
    candidate = tmp_path / "legacy.json"
    _write_jcs(candidate, replay)

    with pytest.raises(
        CHECKER.EvidenceManifestError,
        match="legacy TCK result structure drift",
    ):
        CHECKER._validate_legacy_tck_replay(candidate)


def test_phase_a_replay_aggregate_is_derived_from_required_cases(tmp_path: Path) -> None:
    replay = json.loads((MANIFEST_PATH.parent / "replay/installed-phase-a-ctk.json").read_bytes())
    replay["caseResults"][0]["caseOutcome"] = "FAIL"
    candidate = tmp_path / "phase-a.json"
    _write_jcs(candidate, replay)
    bundle_root = ROOT / "ctk/bundles/phase-a-v0.1"

    with pytest.raises(
        CHECKER.EvidenceManifestError,
        match="Phase-A case result drift",
    ):
        CHECKER._validate_phase_a_replay(
            candidate,
            bundle_path=bundle_root / "bundle.json",
            requirement_path=bundle_root / "requirements.json",
            resource_profile_path=bundle_root / "resource-profile.json",
        )


def test_installed_capability_requires_the_exact_protocol_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability_path = MANIFEST_PATH.parent / "replay/installed-capability.json"
    capability = json.loads(capability_path.read_bytes())
    capability["requestId"] = "forged-request"
    original_load = CHECKER._load_json

    def load(path: Path, **kwargs: Any) -> dict[str, Any]:
        if path == capability_path:
            return copy.deepcopy(capability)
        return original_load(path, **kwargs)

    monkeypatch.setattr(CHECKER, "_load_json", load)
    capability_set = original_load(
        ROOT / "ctk/capabilities/supplier-v02-seed-2.capability-set.json"
    )
    with pytest.raises(
        CHECKER.EvidenceManifestError,
        match="installed capability does not equal",
    ):
        CHECKER._validate_installed_capability(capability_set)


def test_installed_ledger_cannot_substitute_a_self_consistent_module_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger_path = MANIFEST_PATH.parent / "replay/installed-python-ledger.json"
    ledger = json.loads(ledger_path.read_bytes())
    forged_digest = "sha256:" + "0" * 64
    wheel_path = "oac/ctk_adapter_v2.py"
    wheel_entry = next(item for item in ledger["wheel"]["entries"] if item["path"] == wheel_path)
    wheel_entry["rawSha256"] = forged_digest
    ledger["wheel"]["entryPreimageDigest"] = _digest(rfc8785.dumps(ledger["wheel"]["entries"]))

    payload_item = next(
        item for item in ledger["installedPayload"]["files"] if item["wheelPath"] == wheel_path
    )
    payload_item["wheelRawSha256"] = forged_digest
    payload_item["installedRawSha256"] = forged_digest
    ledger["installedPayload"]["payloadClosureDigest"] = _digest(
        rfc8785.dumps(ledger["installedPayload"]["files"])
    )

    distribution = next(
        item
        for item in ledger["installedEnvironment"]["distributions"]
        if item["normalizedName"] == "oac-contract"
    )
    installed_file = next(
        item for item in distribution["files"] if item["path"] == payload_item["installedPath"]
    )
    installed_file["rawSha256"] = forged_digest
    distribution["fileLedgerDigest"] = _digest(rfc8785.dumps(distribution["files"]))
    ledger["installedEnvironment"]["environmentClosureDigest"] = _digest(
        rfc8785.dumps(ledger["installedEnvironment"]["distributions"])
    )
    runtime_projection = {
        "wheelRawSha256": ledger["wheel"]["rawSha256"],
        "wheelEntryPreimageDigest": ledger["wheel"]["entryPreimageDigest"],
        "installedPayloadClosureDigest": ledger["installedPayload"]["payloadClosureDigest"],
        "targetPython": ledger["targetPython"],
        "environmentClosureDigest": ledger["installedEnvironment"]["environmentClosureDigest"],
        "repositoryInputClosureDigest": ledger["repositoryInputs"]["closureDigest"],
        "semanticProductionSourceClosureDigest": ledger["semanticProduction"]["closureDigest"],
    }
    ledger["runtimeBindingDigest"] = _digest(rfc8785.dumps(runtime_projection))
    ledger["digest"] = _digest(
        rfc8785.dumps({key: value for key, value in ledger.items() if key != "digest"})
    )
    original_load = CHECKER._load_json

    def load(path: Path, **kwargs: Any) -> dict[str, Any]:
        if path == ledger_path:
            return copy.deepcopy(ledger)
        return original_load(path, **kwargs)

    monkeypatch.setattr(CHECKER, "_load_json", load)
    with pytest.raises(
        CHECKER.EvidenceManifestError,
        match="semantic production is not byte-bound",
    ):
        CHECKER._installed_material()


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("recipe", "build recipe drift"),
        ("sbom", "module declaration drift"),
    ],
)
def test_go_statement_recipe_and_module_declaration_are_recomputed(
    field: str,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statement_path = ROOT / "implementations/go-supplier-v02-internal/independence-statement.json"
    statement = json.loads(statement_path.read_bytes())
    if field == "recipe":
        statement["build"]["recipeDigest"] = "sha256:" + "0" * 64
    else:
        statement["sbom"]["documentDigest"] = "sha256:" + "0" * 64
    statement["digest"] = _digest(
        rfc8785.dumps({key: value for key, value in statement.items() if key != "digest"})
    )
    original_load = CHECKER._load_json

    def load(path: Path, **kwargs: Any) -> dict[str, Any]:
        if path == statement_path:
            return copy.deepcopy(statement)
        return original_load(path, **kwargs)

    monkeypatch.setattr(CHECKER, "_load_json", load)
    with pytest.raises(CHECKER.EvidenceManifestError, match=message):
        CHECKER.build_manifest()
