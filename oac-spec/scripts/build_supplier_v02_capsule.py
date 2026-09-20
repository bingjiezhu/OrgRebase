#!/usr/bin/env python3
"""Build the deterministic Supplier v0.2 A1 portability capsule."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import rfc8785

from oac.derivation import profile_derivation_report
from oac.sealed import admit_sealed_resource
from oac.supplier import derive_supplier_contract_from_admitted

ROOT = Path(__file__).resolve().parents[1]
PORTABILITY_ROOT = ROOT / "experiments/supplier-v02-portability"
CAPSULE_ROOT = PORTABILITY_ROOT / "v0.2-seed-2"
INPUT_ROOT = ROOT / "profiles/supplier-change/inputs"
CAPABILITY_SET_SOURCE = "ctk/capabilities/supplier-v02-seed-2.capability-set.json"
GENERATOR_RECIPE_SOURCE = "ctk/generators/supplier-v02-seed-2.recipes.json"
GENERATOR_SOURCE = "scripts/supplier_v02_casegen.py"
PUBLISHED_CAPSULE_DIGEST = (
    "sha256:6efc45109628314823f078256546d67aae690b1c18d97fddab38dc23be904e79"
)

CASES = (
    (
        "SC-008",
        "veracier-proc01-contextual.snapshot.json",
        "SC-008.change.json",
    ),
    (
        "SC-009",
        "veracier-proc01-contextual.snapshot.json",
        "SC-009.change.json",
    ),
    (
        "SC-010",
        "veracier-proc01-truncated-contextual.snapshot.json",
        "SC-010.change.json",
    ),
)

CONTRACT_SOURCES = (
    "standard/oac-core-v0.1.md",
    "standard/oac-sealed-resource-v1.md",
    "standard/oac-derived-identifiers-v0.1.md",
    "profiles/supplier-change/profile.md",
    "profiles/supplier-change/semantic-rules-v0.2.json",
    "ctk/protocol/stdio-v2.md",
    "schemas/OrganizationSnapshot.schema.json",
    "schemas/SemanticChangeSet.schema.json",
    "schemas/ProfileDerivationReport.schema.json",
    "schemas/kind-registry.json",
    "schemas/predicate-version-registry.json",
    "schemas/reason-code-registry.json",
    "schemas/normative-semantic-validation-rules.json",
    "profiles/supplier-change/conformance-resource-profile-v1.json",
    "ctk/schemas/IndependenceStatement.schema.json",
    "ctk/schemas/NormativeSemanticRuleRegistry.schema.json",
    "ctk/schemas/ProfileSemanticRuleSet.schema.json",
    "ctk/schemas/ProtocolCapabilitySet.schema.json",
    "ctk/schemas/DeterministicMutationRecipeSet.schema.json",
    "ctk/schemas/DisagreementIncident.schema.json",
    "ctk/schemas/DisagreementResolution.schema.json",
)


def _digest(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _write_exact(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise RuntimeError(f"duplicate source key: {key}")
        value[key] = item
    return value


def _reseal_capsule_copy(source_raw: bytes) -> bytes:
    """Rebind a copied fixture to the SealedResource/v1 raw-map digest.

    Legacy fixtures use the model-projection digest.  A1 MUST NOT mutate those
    A0 inputs and MUST NOT carry their normalized digest into raw admission.
    """

    decoded = json.loads(source_raw, object_pairs_hook=_reject_duplicates)
    if not isinstance(decoded, dict):
        raise RuntimeError("capsule source must be a JSON object")
    projection = {key: item for key, item in decoded.items() if key != "digest"}
    decoded["digest"] = _digest(rfc8785.dumps(projection))
    return rfc8785.dumps(decoded) + b"\n"


def _derived_descriptor(path: str, raw: bytes) -> dict[str, object]:
    return {"path": path, "rawSha256": _digest(raw), "sizeBytes": len(raw)}


def _source_bound_descriptor(
    *,
    path: str,
    raw: bytes,
    source_path: str,
    source_raw: bytes,
    transform: str,
) -> dict[str, object]:
    return {
        **_derived_descriptor(path, raw),
        "sourcePath": source_path,
        "sourceRawSha256": _digest(source_raw),
        "sourceSizeBytes": len(source_raw),
        "transform": transform,
    }


def _copy_contracts(capsule_root: Path) -> list[dict[str, object]]:
    ledger: list[dict[str, object]] = []
    for source_name in CONTRACT_SOURCES:
        source_raw = (ROOT / source_name).read_bytes()
        capsule_path = f"contracts/{source_name}"
        _write_exact(capsule_root / capsule_path, source_raw)
        copied_raw = (capsule_root / capsule_path).read_bytes()
        if copied_raw != source_raw:
            raise RuntimeError(f"contract copy drifted while building: {source_name}")
        ledger.append(
            _source_bound_descriptor(
                path=capsule_path,
                raw=copied_raw,
                source_path=source_name,
                source_raw=source_raw,
                transform="exact-copy/v1",
            )
        )
    return ledger


def _copy_source_bound_asset(
    capsule_root: Path, *, source_name: str, capsule_path: str
) -> dict[str, object]:
    source_raw = (ROOT / source_name).read_bytes()
    _write_exact(capsule_root / capsule_path, source_raw)
    copied_raw = (capsule_root / capsule_path).read_bytes()
    if copied_raw != source_raw:
        raise RuntimeError(f"source-bound asset drifted while building: {source_name}")
    return _source_bound_descriptor(
        path=capsule_path,
        raw=copied_raw,
        source_path=source_name,
        source_raw=source_raw,
        transform="exact-copy/v1",
    )


def build(capsule_root: Path) -> dict[str, object]:
    artifacts = capsule_root / "artifacts"
    expected_root = capsule_root / "expected"
    cases: list[dict[str, object]] = []

    copied: dict[str, str] = {}
    for case_id, snapshot_name, change_name in CASES:
        snapshot_path = INPUT_ROOT / snapshot_name
        change_path = INPUT_ROOT / change_name
        snapshot_source_raw = snapshot_path.read_bytes()
        change_source_raw = change_path.read_bytes()
        snapshot_raw = _reseal_capsule_copy(snapshot_source_raw)
        change_raw = _reseal_capsule_copy(change_source_raw)

        for name, raw in ((snapshot_name, snapshot_raw), (change_name, change_raw)):
            previous = copied.get(name)
            if previous is not None and previous != _digest(raw):
                raise RuntimeError(f"artifact name collision with different bytes: {name}")
            _write_exact(artifacts / name, raw)
            copied[name] = _digest(raw)

        snapshot = admit_sealed_resource(snapshot_raw, "OrganizationSnapshot")
        change = admit_sealed_resource(change_raw, "SemanticChangeSet")
        derived = derive_supplier_contract_from_admitted(snapshot, change)
        report = profile_derivation_report(
            snapshot.resource,
            change.resource,
            derived=derived,
        ).model_dump(mode="json", by_alias=True)
        report_bytes = rfc8785.dumps(report)
        report_name = f"{case_id}.report.json"
        _write_exact(expected_root / report_name, report_bytes + b"\n")

        cases.append(
            {
                "caseId": case_id,
                "snapshot": _source_bound_descriptor(
                    path=f"artifacts/{snapshot_name}",
                    raw=snapshot_raw,
                    source_path=f"profiles/supplier-change/inputs/{snapshot_name}",
                    source_raw=snapshot_source_raw,
                    transform="raw-map-reseal-jcs-newline/v1",
                ),
                "change": _source_bound_descriptor(
                    path=f"artifacts/{change_name}",
                    raw=change_raw,
                    source_path=f"profiles/supplier-change/inputs/{change_name}",
                    source_raw=change_source_raw,
                    transform="raw-map-reseal-jcs-newline/v1",
                ),
                "expected": {
                    **_derived_descriptor(
                        f"expected/{report_name}", report_bytes + b"\n"
                    ),
                    "reportDigest": _digest(report_bytes),
                },
            }
        )

    contracts = _copy_contracts(capsule_root)
    capability_set = _copy_source_bound_asset(
        capsule_root,
        source_name=CAPABILITY_SET_SOURCE,
        capsule_path=f"capabilities/{Path(CAPABILITY_SET_SOURCE).name}",
    )
    generator = {
        "recipeSet": _copy_source_bound_asset(
            capsule_root,
            source_name=GENERATOR_RECIPE_SOURCE,
            capsule_path=f"generator/{Path(GENERATOR_RECIPE_SOURCE).name}",
        ),
        "source": _copy_source_bound_asset(
            capsule_root,
            source_name=GENERATOR_SOURCE,
            capsule_path=f"generator/{Path(GENERATOR_SOURCE).name}",
        ),
    }
    projection: dict[str, object] = {
        "apiVersion": "oac.portability.capsule/v0alpha1",
        "kind": "SupplierDerivationPortabilityCapsule",
        "capsuleId": "oac.supplier.transfer/v0.2/a1-seed-2",
        "protocolVersion": "oac.ctk.stdio/v2",
        "profileId": "oac.supplier.transfer",
        "profileVersion": "v0.2",
        "digestAlgorithm": "sha256-jcs-detached/v1",
        "contracts": contracts,
        "capabilitySet": capability_set,
        "generator": generator,
        "cases": cases,
    }
    capsule = {**projection, "capsuleDigest": _digest(rfc8785.dumps(projection))}
    _write_exact(capsule_root / "capsule.json", rfc8785.dumps(capsule) + b"\n")
    return capsule


def _read_published_member(relative: str, label: str) -> bytes:
    candidate = CAPSULE_ROOT / relative
    if (
        relative.startswith("/")
        or "\\" in relative
        or ".." in Path(relative).parts
        or candidate.is_symlink()
        or not candidate.is_file()
    ):
        raise RuntimeError(f"published capsule member is unsafe or missing: {label}")
    return candidate.read_bytes()


def _check_derived_descriptor(
    descriptor: object,
    *,
    expected_path: str,
    expected_fields: set[str],
    label: str,
) -> bytes:
    if not isinstance(descriptor, dict) or set(descriptor) != expected_fields:
        raise RuntimeError(f"published capsule descriptor is not closed: {label}")
    if descriptor["path"] != expected_path:
        raise RuntimeError(f"published capsule path drift: {label}")
    raw = _read_published_member(expected_path, label)
    if descriptor["rawSha256"] != _digest(raw) or descriptor["sizeBytes"] != len(raw):
        raise RuntimeError(f"published capsule member digest drift: {label}")
    return raw


def _check_source_descriptor(
    descriptor: object,
    *,
    expected_path: str,
    expected_source: str,
    expected_transform: str,
    label: str,
) -> bytes:
    fields = {
        "path",
        "rawSha256",
        "sizeBytes",
        "sourcePath",
        "sourceRawSha256",
        "sourceSizeBytes",
        "transform",
    }
    raw = _check_derived_descriptor(
        descriptor,
        expected_path=expected_path,
        expected_fields=fields,
        label=label,
    )
    assert isinstance(descriptor, dict)
    if (
        descriptor["sourcePath"] != expected_source
        or descriptor["transform"] != expected_transform
        or not isinstance(descriptor["sourceSizeBytes"], int)
        or descriptor["sourceSizeBytes"] < 0
        or not isinstance(descriptor["sourceRawSha256"], str)
        or len(descriptor["sourceRawSha256"]) != 71
        or not descriptor["sourceRawSha256"].startswith("sha256:")
    ):
        raise RuntimeError(f"published capsule source binding drift: {label}")
    try:
        bytes.fromhex(descriptor["sourceRawSha256"].removeprefix("sha256:"))
    except ValueError as exc:
        raise RuntimeError(f"published capsule source digest is invalid: {label}") from exc
    if expected_transform == "exact-copy/v1" and (
        descriptor["sourceRawSha256"] != _digest(raw)
        or descriptor["sourceSizeBytes"] != len(raw)
    ):
        raise RuntimeError(f"published exact copy is not internally source-bound: {label}")
    return raw


def check() -> dict[str, object]:
    """Admit the published capsule as an immutable historical evidence coordinate.

    The capsule binds the contract bytes that were published with seed-2. Later additions to
    repository-wide registries MUST NOT rewrite those bytes or the immutable disagreement records
    that reference them. This gate therefore checks the capsule's own detached digest, closed member
    ledger, exact-copy relations, sealed roots, and derived reports without comparing historical
    source labels to the mutable current checkout.
    """

    raw = _read_published_member("capsule.json", "capsule manifest")
    capsule = json.loads(raw, object_pairs_hook=_reject_duplicates)
    fields = {
        "apiVersion",
        "kind",
        "capsuleId",
        "protocolVersion",
        "profileId",
        "profileVersion",
        "digestAlgorithm",
        "contracts",
        "capabilitySet",
        "generator",
        "cases",
        "capsuleDigest",
    }
    if not isinstance(capsule, dict) or set(capsule) != fields:
        raise RuntimeError("published capsule manifest is not a closed object")
    if raw != rfc8785.dumps(capsule) + b"\n":
        raise RuntimeError("published capsule manifest is not exact JCS plus LF")
    projection = {key: item for key, item in capsule.items() if key != "capsuleDigest"}
    if (
        capsule["capsuleDigest"] != _digest(rfc8785.dumps(projection))
        or capsule["capsuleDigest"] != PUBLISHED_CAPSULE_DIGEST
    ):
        raise RuntimeError("published capsule digest differs from its external trust anchor")
    if (
        capsule["apiVersion"] != "oac.portability.capsule/v0alpha1"
        or capsule["kind"] != "SupplierDerivationPortabilityCapsule"
        or capsule["capsuleId"] != "oac.supplier.transfer/v0.2/a1-seed-2"
        or capsule["protocolVersion"] != "oac.ctk.stdio/v2"
        or capsule["profileId"] != "oac.supplier.transfer"
        or capsule["profileVersion"] != "v0.2"
        or capsule["digestAlgorithm"] != "sha256-jcs-detached/v1"
    ):
        raise RuntimeError("published capsule coordinate drift")

    contracts = capsule["contracts"]
    if not isinstance(contracts, list) or len(contracts) != len(CONTRACT_SOURCES):
        raise RuntimeError("published capsule contract inventory drift")
    for descriptor, source_name in zip(contracts, CONTRACT_SOURCES, strict=True):
        _check_source_descriptor(
            descriptor,
            expected_path=f"contracts/{source_name}",
            expected_source=source_name,
            expected_transform="exact-copy/v1",
            label=f"contract {source_name}",
        )

    _check_source_descriptor(
        capsule["capabilitySet"],
        expected_path=f"capabilities/{Path(CAPABILITY_SET_SOURCE).name}",
        expected_source=CAPABILITY_SET_SOURCE,
        expected_transform="exact-copy/v1",
        label="capability set",
    )
    generator = capsule["generator"]
    if not isinstance(generator, dict) or set(generator) != {"recipeSet", "source"}:
        raise RuntimeError("published capsule generator inventory drift")
    _check_source_descriptor(
        generator["recipeSet"],
        expected_path=f"generator/{Path(GENERATOR_RECIPE_SOURCE).name}",
        expected_source=GENERATOR_RECIPE_SOURCE,
        expected_transform="exact-copy/v1",
        label="generator recipe set",
    )
    _check_source_descriptor(
        generator["source"],
        expected_path=f"generator/{Path(GENERATOR_SOURCE).name}",
        expected_source=GENERATOR_SOURCE,
        expected_transform="exact-copy/v1",
        label="generator source",
    )

    cases = capsule["cases"]
    if not isinstance(cases, list) or len(cases) != len(CASES):
        raise RuntimeError("published capsule case inventory drift")
    for case, (case_id, snapshot_name, change_name) in zip(cases, CASES, strict=True):
        if not isinstance(case, dict) or set(case) != {
            "caseId",
            "snapshot",
            "change",
            "expected",
        }:
            raise RuntimeError(f"published capsule case is not closed: {case_id}")
        if case["caseId"] != case_id:
            raise RuntimeError(f"published capsule case order drift: {case_id}")
        snapshot_raw = _check_source_descriptor(
            case["snapshot"],
            expected_path=f"artifacts/{snapshot_name}",
            expected_source=f"profiles/supplier-change/inputs/{snapshot_name}",
            expected_transform="raw-map-reseal-jcs-newline/v1",
            label=f"{case_id} snapshot",
        )
        change_raw = _check_source_descriptor(
            case["change"],
            expected_path=f"artifacts/{change_name}",
            expected_source=f"profiles/supplier-change/inputs/{change_name}",
            expected_transform="raw-map-reseal-jcs-newline/v1",
            label=f"{case_id} change",
        )
        expected_raw = _check_derived_descriptor(
            case["expected"],
            expected_path=f"expected/{case_id}.report.json",
            expected_fields={"path", "rawSha256", "sizeBytes", "reportDigest"},
            label=f"{case_id} expected report",
        )
        snapshot = admit_sealed_resource(snapshot_raw, "OrganizationSnapshot")
        change = admit_sealed_resource(change_raw, "SemanticChangeSet")
        derived = derive_supplier_contract_from_admitted(snapshot, change)
        report = profile_derivation_report(
            snapshot.resource,
            change.resource,
            derived=derived,
        ).model_dump(mode="json", by_alias=True)
        report_bytes = rfc8785.dumps(report)
        if (
            expected_raw != report_bytes + b"\n"
            or case["expected"]["reportDigest"] != _digest(report_bytes)
        ):
            raise RuntimeError(f"published capsule derived report drift: {case_id}")
    return capsule


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--output-root",
        type=Path,
        help="mint bytes under a new, non-published capsule root",
    )
    args = parser.parse_args(argv)
    if args.check and args.output_root is not None:
        parser.error("--check and --output-root are mutually exclusive")
    if args.check:
        capsule = check()
    else:
        if args.output_root is None:
            parser.error(
                "the published seed-2 capsule is immutable; use --output-root for a new lineage"
            )
        if args.output_root.resolve() == CAPSULE_ROOT.resolve():
            parser.error("refusing to overwrite the published seed-2 capsule")
        capsule = build(args.output_root)
    print(capsule["capsuleDigest"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
