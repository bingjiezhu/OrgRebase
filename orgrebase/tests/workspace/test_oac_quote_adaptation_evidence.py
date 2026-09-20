from __future__ import annotations

import io
import json
import runpy
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "evidence" / "oac-quote-adaptation" / "latest"
OAC_ROOT = ROOT.parent / "oac-spec"
VERIFIER = ROOT / "scripts" / "verify_oac_quote_adaptation.py"


def test_independent_adaptation_verifier_has_no_product_imports() -> None:
    source = VERIFIER.read_text(encoding="utf-8")

    assert "import orgrebase" not in source
    assert "from orgrebase" not in source
    assert "OACQuoteAdaptationService" not in source
    assert "run_oac_quote_adaptation" not in source


def test_retained_oac_quote_adaptation_lane_passes_independent_verifier() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(VERIFIER),
            "--root",
            str(EVIDENCE),
            "--retained-build",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    result = json.loads(completed.stdout)
    assert result["status"] == "PASS"
    assert result["evergreen_status"] == "READY_FOR_ORGREBASE"
    assert result["veracier_status"] == "HOLD"
    assert result["real_review_wait_ms"] >= 4000
    assert result["canonical_target_writes"] == 0
    assert result["failure_codes"] == []
    assert result["verification_scope"] == "RETAINED_ARTIFACT"
    assert result["current_release_qualified"] is False
    assert result["oac_reader"]["digest_projection"] == "LEGACY_TYPED_PROJECTION"
    assert result["oac_reader"]["wheel_sha256"] == "aead48d80e921ddd8ea9505e4723d716e35ac351bb4237b7a88ad38a83e7e13b"
    assert result["mutation_rejections"] == {
        "CROSS_PROFILE_CAPSULE": "CAPSULE_PROFILE_BINDING_MISMATCH",
        "DIGEST_SUBSTITUTION": "CAPSULE_MAPPING_BINDING_MISMATCH",
        "PARITY_OMISSION": "PARITY_TASK_COUNT_INVALID",
        "SELF_APPROVAL": "EVOLUTION_SELF_ADMISSION_FORBIDDEN",
        "STALE_APPROVAL": "APPROVAL_BEFORE_NOT_BEFORE",
        "UNKNOWN_ERASURE": "HOLD_GAPS_REQUIRED",
    }


def test_current_oac_verifier_does_not_fall_back_to_legacy_digest() -> None:
    completed = subprocess.run(
        [sys.executable, str(VERIFIER), "--root", str(EVIDENCE), "--oac-root", str(OAC_ROOT)],
        cwd=ROOT, capture_output=True, text=True, timeout=180, check=False,
    )
    assert completed.returncode == 1
    result = json.loads(completed.stdout)
    assert result["failure_codes"] == [
        "OAC_PUBLIC_CLI_FAILED:DEMAND_EVOLUTION",
        "OAC_PUBLIC_CLI_FAILED:SNAPSHOT_VALIDATE",
    ]
    assert result["verification_scope"] == "CURRENT_OAC_VALIDATION"
    assert result["oac_reader"]["digest_projection"] == "CURRENT_PUBLIC_CLI"


@pytest.mark.parametrize("present", [False, True])
def test_retained_oac_reader_requires_its_pinned_bytes(tmp_path: Path, present: bool) -> None:
    wheel = tmp_path / "evidence/oac-evolution/wheel-check/wheels/oac_contract-0.3.0a0-py3-none-any.whl"
    if present:
        wheel.parent.mkdir(parents=True)
        wheel.write_bytes(b"untrusted wheel substitute")
    completed = subprocess.run(
        [sys.executable, str(VERIFIER), "--root", str(EVIDENCE), "--project-root", str(tmp_path),
         "--retained-build"], cwd=ROOT, capture_output=True, text=True, timeout=30, check=False,
    )
    assert completed.returncode == 1
    assert "RETAINED_OAC_READER_UNAVAILABLE_OR_CHANGED" in completed.stderr


def test_oac_quote_adaptation_manifest_is_exactly_closed_world() -> None:
    manifest = json.loads((EVIDENCE / "manifest.json").read_text(encoding="utf-8"))
    indexed = {item["path"] for item in manifest["entries"]}
    observed = {
        path.relative_to(EVIDENCE).as_posix()
        for path in EVIDENCE.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }

    assert indexed == observed
    assert "artifacts/evergreen/owner-review-summary.json" in observed
    assert manifest["entry_count"] == len(observed) == 21
    assert manifest["status"] == "CLOSED_WORLD"


@pytest.mark.parametrize("arguments,expected_returncode", [(["--help"], 0), (["--unknown-option"], 2)])
def test_retained_oac_bootstrap_keeps_verified_private_bytes_through_cli_exit(
    tmp_path: Path, arguments: list[str], expected_returncode: int,
) -> None:
    verifier = runpy.run_path(str(VERIFIER))
    wheel = tmp_path / "original.whl"
    shutil.copyfile(ROOT / verifier["RETAINED_OAC_WHEEL"], wheel)
    marker = tmp_path / "replacement-executed"
    replacement = tmp_path / "replacement.whl"
    changed_bytes = io.BytesIO()
    with zipfile.ZipFile(wheel) as original, zipfile.ZipFile(changed_bytes, "w") as changed:
        for member in original.infolist():
            content = original.read(member)
            if member.filename == "oac/__init__.py":
                content += f"\nfrom pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n".encode()
            changed.writestr(member, content)
    replacement.write_bytes(changed_bytes.getvalue())
    receipt_path = tmp_path / "bootstrap-lifecycle.json"
    probe = f"""
import json, pathlib, runpy, sys
wheel = pathlib.Path(sys.argv[1]).resolve(strict=True)
replacement = pathlib.Path({str(replacement)!r})
receipt_path = pathlib.Path({str(receipt_path)!r})
read_bytes = pathlib.Path.read_bytes
run_module = runpy.run_module
receipt = {{"source_reads": 0}}
def replace_after_read(path):
    raw = read_bytes(path)
    if path == wheel:
        receipt["source_reads"] += 1
        wheel.write_bytes(read_bytes(replacement))
    return raw
def observe_cli(*args, **kwargs):
    reader = pathlib.Path(sys.modules["oac"].__file__.split("/oac/", 1)[0])
    receipt["reader"] = str(reader)
    receipt["exists_during_cli"] = reader.is_file()
    try:
        return run_module(*args, **kwargs)
    finally:
        receipt["exists_at_cli_exit"] = reader.is_file()
pathlib.Path.read_bytes = replace_after_read
runpy.run_module = observe_cli
try:
    exec(compile({verifier['RETAINED_OAC_BOOTSTRAP']!r}, "retained-oac-bootstrap", "exec"))
finally:
    receipt["module_origins"] = {{name: module.__file__ for name, module in sys.modules.items()
        if (name == "oac" or name.startswith("oac.")) and getattr(module, "__file__", None)}}
    receipt_path.write_text(json.dumps(receipt))
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", probe, str(wheel), verifier["RETAINED_OAC_SHA256"], *arguments],
        cwd=tmp_path, capture_output=True, text=True, timeout=60, check=False,
    )
    receipt = json.loads(receipt_path.read_bytes())
    assert completed.returncode == expected_returncode, completed.stderr
    assert receipt["source_reads"] == 1
    assert zipfile.is_zipfile(wheel)
    assert wheel.read_bytes() == replacement.read_bytes()
    assert not marker.exists(), "the replacement wheel was executed after the pinned-byte read"
    reader = Path(receipt["reader"])
    assert reader != wheel
    assert receipt["exists_during_cli"] and receipt["exists_at_cli_exit"]
    assert receipt["module_origins"]
    assert all(origin.startswith(str(reader) + "/oac/") for origin in receipt["module_origins"].values())
    assert not reader.exists()
    assert not reader.parent.exists()
