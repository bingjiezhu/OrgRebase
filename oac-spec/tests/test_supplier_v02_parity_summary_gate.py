from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest
import rfc8785

ROOT = Path(__file__).resolve().parents[1]
HARNESS_PATH = ROOT / "scripts/check_supplier_v02_parity.py"


def _load_harness():
    specification = importlib.util.spec_from_file_location(
        "oac_supplier_v02_parity_summary_gate",
        HARNESS_PATH,
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


HARNESS = _load_harness()


def _summary() -> dict[str, object]:
    return {
        "apiVersion": "oac.portability.parity-summary/v0alpha1",
        "kind": "SupplierPortabilityParitySummary",
        "passedCases": 52,
        "status": "PASS",
    }


def _portable_summary() -> dict[str, object]:
    return {
        "apiVersion": "oac.portability.parity-summary/v0alpha1",
        "generatorRuntimeEvidence": {
            "language": "Python",
            "runtimeImplementation": "CPython",
            "runtimeVersion": "3.12.13",
            "dependencies": [{"name": "rfc8785", "version": "0.1.4"}],
        },
        "implementations": [
            {
                "implementationId": "python",
                "implementationVersion": "v1",
                "commandDigest": "sha256:" + "1" * 64,
                "sourceClosureDigest": "sha256:" + "2" * 64,
                "runtime": {
                    "descriptor": "Python 3.12.13",
                    "evidenceDigest": "sha256:" + "3" * 64,
                },
                "languageEvidence": {
                    "language": "Python",
                    "verified": True,
                    "method": "probe",
                },
            },
            {
                "implementationId": "go",
                "implementationVersion": "v1",
                "commandDigest": "sha256:" + "4" * 64,
                "sourceClosureDigest": "sha256:" + "5" * 64,
                "runtime": {
                    "descriptor": "go1.25.6",
                    "evidenceDigest": "sha256:" + "6" * 64,
                },
                "languageEvidence": {
                    "language": "Go",
                    "verified": True,
                    "method": "build-info",
                },
            },
        ],
        "observationManifest": {
            "implementationMaterials": [
                {
                    "implementationId": "python",
                    "implementationVersion": "v1",
                    "commandDigest": "sha256:" + "1" * 64,
                    "sourceClosureDigest": "sha256:" + "2" * 64,
                },
                {
                    "implementationId": "go",
                    "implementationVersion": "v1",
                    "commandDigest": "sha256:" + "4" * 64,
                    "sourceClosureDigest": "sha256:" + "5" * 64,
                },
            ],
            "observations": [
                {
                    "caseId": "case-1",
                    "inputDigest": "sha256:" + "7" * 64,
                    "passed": True,
                }
            ],
        },
        "observationManifestDigest": "sha256:" + "8" * 64,
        "mutants": [{"mutantId": "m1", "rejected": True}],
        "status": "PASS",
    }


def test_check_stored_summary_accepts_exact_jcs_plus_newline(tmp_path: Path) -> None:
    summary = _summary()
    expected = rfc8785.dumps(summary) + b"\n"
    stored = tmp_path / "summary.json"
    stored.write_bytes(expected)

    assert HARNESS.check_stored_summary(summary, stored) == expected


def test_check_stored_summary_rejects_single_byte_drift(tmp_path: Path) -> None:
    summary = _summary()
    stored = tmp_path / "summary.json"
    stored.write_bytes((rfc8785.dumps(summary) + b"\n").replace(b"52", b"51", 1))

    with pytest.raises(
        HARNESS.HarnessFailure,
        match=r"^stored parity summary differs from live parity summary$",
    ):
        HARNESS.check_stored_summary(summary, stored)


def test_check_stored_summary_rejects_missing_final_newline(tmp_path: Path) -> None:
    summary = _summary()
    stored = tmp_path / "summary.json"
    stored.write_bytes(rfc8785.dumps(summary))

    with pytest.raises(
        HARNESS.HarnessFailure,
        match=r"^stored parity summary is not exact JCS plus newline$",
    ):
        HARNESS.check_stored_summary(summary, stored)


def test_check_stored_summary_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(
        HARNESS.HarnessFailure,
        match=r"^stored parity summary is missing$",
    ):
        HARNESS.check_stored_summary(_summary(), tmp_path / "missing.json")


def test_portable_gate_allows_only_declared_host_identity_drift(
    tmp_path: Path,
) -> None:
    stored_summary = _portable_summary()
    live_summary = copy.deepcopy(stored_summary)
    for index, implementation in enumerate(live_summary["implementations"]):
        implementation["commandDigest"] = "sha256:" + str(index + 9) * 64
        implementation["runtime"] = {
            "descriptor": f"other-host-{index}",
            "evidenceDigest": "sha256:" + "a" * 64,
        }
        live_summary["observationManifest"]["implementationMaterials"][index][
            "commandDigest"
        ] = implementation["commandDigest"]
    live_summary["generatorRuntimeEvidence"]["runtimeVersion"] = "3.12.99"
    live_summary["observationManifestDigest"] = "sha256:" + "b" * 64
    stored = tmp_path / "summary.json"
    stored.write_bytes(rfc8785.dumps(stored_summary) + b"\n")

    assert HARNESS.check_stored_summary_portable(live_summary, stored) == (
        rfc8785.dumps(live_summary) + b"\n"
    )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("implementations", 0, "sourceClosureDigest"), "sha256:" + "c" * 64),
        (
            ("observationManifest", "observations", 0, "inputDigest"),
            "sha256:" + "d" * 64,
        ),
        (("mutants", 0, "rejected"), False),
    ],
)
def test_portable_gate_rejects_semantic_material_or_mutant_drift(
    tmp_path: Path,
    path: tuple[str | int, ...],
    value: object,
) -> None:
    stored_summary = _portable_summary()
    live_summary = copy.deepcopy(stored_summary)
    cursor = live_summary
    for part in path[:-1]:
        cursor = cursor[part]
    cursor[path[-1]] = value
    stored = tmp_path / "summary.json"
    stored.write_bytes(rfc8785.dumps(stored_summary) + b"\n")

    with pytest.raises(
        HARNESS.HarnessFailure,
        match=r"^stored parity summary differs from the live portable projection$",
    ):
        HARNESS.check_stored_summary_portable(live_summary, stored)
