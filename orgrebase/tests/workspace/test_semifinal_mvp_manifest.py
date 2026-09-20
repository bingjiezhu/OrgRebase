from __future__ import annotations

import ast
import hashlib
import json
import shutil
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.build_semifinal_mvp_manifest import build_manifest
from scripts.verify_semifinal_mvp_manifest import verify

ROOT = Path(__file__).resolve().parents[2]


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _redigest(summary: dict[str, object]) -> None:
    body = {key: value for key, value in summary.items() if key != "digest"}
    summary["digest"] = "sha256:" + hashlib.sha256(_canonical(body)).hexdigest()


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_builder_unifies_four_sources_without_inflating_claims(tmp_path: Path) -> None:
    output = tmp_path / "manifest" / "summary.json"
    first = build_manifest(output_path=output)
    second = build_manifest()

    assert first == second == json.loads(output.read_text(encoding="utf-8"))
    assert first["status"] == "PASS"
    assert first["manifest_meaning"] == (
        "PASS_MEANS_EVIDENCE_INVENTORY_INTEGRITY_NOT_PRODUCTION_READINESS"
    )
    assert first["production_ready"] is False
    assert first["maturity"] == "SEMIFINAL_MVP_CONTROLLED_LOCAL_NOT_PRODUCTION"
    assert [item["evidence_id"] for item in first["evidence_packs"]] == [
        "semifinal-closure",
        "semifinal-governed",
        "public-process",
        "skill-predecessor-rollback",
    ]
    assert first["completion_totals"] == {
        "recommendation_count": 28,
        "validated_count": 19,
        "not_run_count": 9,
    }
    matrix = {item["recommendation_id"]: item for item in first["completion_matrix"]}
    assert matrix["VALUE_SELECTIVE_REBASE_COST"]["maturity"] == "MODELLED_COUNTERFACTUAL"
    assert "not time, money or realized ROI" in matrix["VALUE_SELECTIVE_REBASE_COST"][
        "claim_boundary"
    ]
    assert matrix["PUBLIC_PROCESS_MECHANISM_BENCHMARK"]["maturity"] == (
        "PUBLIC_SOURCE_DERIVED_SYNTHETIC_MECHANISM_VALIDATION"
    )
    assert matrix["EXTERNAL_HUMAN_APPROVAL"]["result"] == "NOT_RUN"
    assert matrix["REAL_ENTERPRISE_CONNECTORS_AND_QUOTES"]["result"] == "NOT_RUN"
    assert matrix["PRODUCTION_SLA_HA_DR_MULTITENANCY"]["result"] == "NOT_RUN"


def test_independent_verifier_recomputes_all_source_bindings(tmp_path: Path) -> None:
    manifest_root = tmp_path / "manifest"
    build_manifest(output_path=manifest_root / "summary.json")
    verification = verify(manifest_root, output_path=manifest_root / "verification.json")

    assert verification["status"] == "PASS"
    assert verification["failure_count"] == 0
    assert verification["source_pack_count"] == 4
    assert verification["production_ready"] is False
    assert verification == json.loads(
        (manifest_root / "verification.json").read_text(encoding="utf-8")
    )


@pytest.mark.parametrize(
    ("mutation", "expected_failure"),
    (
        ("authority", "AUTHORITY_BOUNDARY"),
        ("run_binding", "CONTROLLED_RUN_BINDING"),
        ("pack_digest", "SOURCE_PACK_DIGEST_BINDING:semifinal-closure"),
        ("unsealed_claim", "SUMMARY_DIGEST"),
    ),
)
def test_verifier_rejects_redigested_authority_run_or_pack_tamper(
    tmp_path: Path,
    mutation: str,
    expected_failure: str,
) -> None:
    manifest_root = tmp_path / mutation
    original = build_manifest()
    summary = deepcopy(original)
    if mutation == "authority":
        summary["authority_boundaries"]["canonical_business_state"] = "AGENTTEAMS"
        _redigest(summary)
    elif mutation == "run_binding":
        summary["cross_pack_bindings"]["controlled_local_run_id"] = "run:tampered"
        _redigest(summary)
    elif mutation == "pack_digest":
        summary["evidence_packs"][0]["content_address"]["pack_digest"] = "sha256:" + "0" * 64
        _redigest(summary)
    else:
        summary["maturity"] = "PRODUCTION_READY"
    _write(manifest_root / "summary.json", summary)

    verification = verify(manifest_root)

    assert verification["status"] == "FAIL"
    assert expected_failure in verification["failures"]


def test_verifier_has_only_stdlib_imports() -> None:
    source = (ROOT / "scripts/verify_semifinal_mvp_manifest.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module != "__future__"
    )
    assert imported <= {"argparse", "hashlib", "json", "pathlib", "typing"}


def _retained_checkout(tmp_path: Path) -> tuple[Path, Path]:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    for name in ("quote-value-v0.1", "quote-value-v0.3-public-process"):
        shutil.copytree(ROOT / "benchmark" / name, checkout / "benchmark" / name)
    mapping = Path("configs/workspace/public-process-mappings/order-management-v1.json")
    (checkout / mapping).parent.mkdir(parents=True)
    shutil.copy2(ROOT / mapping, checkout / mapping)
    evidence = checkout / "evidence"
    evidence.mkdir()
    for name in ("semifinal-closure", "semifinal-governed", "public-process", "skill-predecessor-rollback"):
        shutil.copytree(ROOT / "evidence" / name / "latest", evidence / name / "latest")
    manifest = evidence / "semifinal-mvp/latest"
    manifest.mkdir(parents=True)
    (manifest / "summary.json").write_bytes(
        (ROOT / "evidence/semifinal-mvp/latest/summary.json").read_bytes()
    )
    retained = evidence / "semifinal-mvp/supporting/CONTRIBUTING.md"
    retained.parent.mkdir()
    retained.write_bytes((ROOT / "evidence/semifinal-mvp/supporting/CONTRIBUTING.md").read_bytes())
    (checkout / "CONTRIBUTING.md").write_text("Current contributor instructions.\n")
    return checkout, manifest


def test_retained_manifest_uses_original_guide_without_certifying_current_guide(tmp_path: Path) -> None:
    checkout, manifest = _retained_checkout(tmp_path)
    result = verify(manifest, checkout_root=checkout)
    assert result["status"] == "PASS", result["failures"]
    assert result["contributing_guide_binding"] == {
        "logical_path": "CONTRIBUTING.md",
        "verified_source_path": "evidence/semifinal-mvp/supporting/CONTRIBUTING.md",
        "scope": "RETAINED_HISTORICAL_GUIDE",
        "certifies_current_guide": False,
    }


@pytest.mark.parametrize("mutation", ["missing", "tampered", "different_manifest", "pack"])
def test_retained_guide_is_not_a_general_fallback(tmp_path: Path, mutation: str) -> None:
    checkout, manifest = _retained_checkout(tmp_path)
    retained = checkout / "evidence/semifinal-mvp/supporting/CONTRIBUTING.md"
    if mutation == "missing":
        retained.unlink()
    elif mutation == "tampered":
        retained.write_text("Altered archived guide.\n")
    else:
        summary = json.loads((manifest / "summary.json").read_text())
        if mutation == "pack":
            summary["evidence_packs"][0]["content_address"]["pack_digest"] = "sha256:" + "0" * 64
        else:
            summary["claim_boundary"] += " Other inventory."
        _redigest(summary)
        _write(manifest / "summary.json", summary)
    result = verify(manifest, checkout_root=checkout)
    assert result["status"] == "FAIL"
    if mutation in {"different_manifest", "pack"}:
        assert result["contributing_guide_binding"]["scope"] == "CURRENT_GUIDE"
    if mutation == "pack":
        assert "SOURCE_PACK_DIGEST_BINDING:semifinal-closure" in result["failures"]
