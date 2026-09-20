from __future__ import annotations

import ast
import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from scripts.data_adapters.fetch_order_management_ocel import verify_source
from scripts.run_public_process_bridge import (
    PublicProcessBridgeError,
    _object_digest,
    _validate_mapping,
    _validate_overlay,
    _validate_projection,
    build_receipt,
)
from scripts.verify_public_process_bridge import verify

ROOT = Path(__file__).resolve().parents[2]
BENCHMARK = ROOT / "benchmark" / "quote-value-v0.3-public-process"
MAPPING = ROOT / "configs" / "workspace" / "public-process-mappings" / "order-management-v1.json"
RETAINED = BENCHMARK / "retained" / "public-process-bridge-receipt.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _seal(value: dict) -> dict:
    value = copy.deepcopy(value)
    value.pop("digest", None)
    value["digest"] = _object_digest(value)
    return value


def _build(benchmark: Path = BENCHMARK, mapping: Path = MAPPING) -> dict:
    return build_receipt(project_root=ROOT, benchmark_root=benchmark, mapping_path=mapping)


def _rewrite_manifest(benchmark: Path) -> None:
    relatives = [
        "LICENSES.json",
        "dataset-manifest.json",
        "overlay/policy-change-ground-truth.json",
        "projection/object-centric-projection.json",
    ]
    (benchmark / "MANIFEST.sha256").write_text(
        "".join(
            f"{hashlib.sha256((benchmark / relative).read_bytes()).hexdigest()}  {relative}\n"
            for relative in relatives
        ),
        encoding="utf-8",
    )


def test_offline_bridge_replays_retained_receipt() -> None:
    built = _build()
    retained = _load(RETAINED)

    assert built == retained
    assert built["status"] == "PASS"
    assert built["evidence_class"] == "PUBLIC_SOURCE_DERIVED_SYNTHETIC"
    assert built["claim_ceiling"] == "MECHANISM_VALIDATION_ONLY"
    assert verify(retained, ROOT) == []


def test_three_strategies_have_exact_conservative_metrics() -> None:
    receipt = _build()
    strategies = {row["strategy_id"]: row for row in receipt["strategies"]}

    assert set(strategies) == {"BROADCAST_ALL", "NAIVE_GRAPH", "OAC_TYPED_WITH_UNKNOWN"}
    for strategy_id in ("BROADCAST_ALL", "NAIVE_GRAPH"):
        assert strategies[strategy_id]["metrics"]["precision"]["value"] == 0.454545
        assert strategies[strategy_id]["metrics"]["recall"]["value"] == 1.0
        assert strategies[strategy_id]["metrics"]["false_invalidation_rate"]["value"] == 1.0
        assert strategies[strategy_id]["metrics"]["scope_reduction_rate"]["value"] == 0.0

    typed = strategies["OAC_TYPED_WITH_UNKNOWN"]
    assert typed["counts"] == {
        "true_positive": 5,
        "false_positive": 0,
        "true_negative": 4,
        "false_negative": 0,
        "conservative_action_scope": 7,
    }
    assert typed["metrics"]["precision"]["value"] == 1.0
    assert typed["metrics"]["recall"]["value"] == 1.0
    assert typed["metrics"]["false_invalidation_rate"]["value"] == 0.0
    assert typed["metrics"]["scope_reduction_rate"]["value"] == 0.363636
    assert typed["metrics"]["unsafe_false_unaffected_rate"]["value"] == 0.0
    assert len(typed["classifications"]["unknown"]) == 2


def test_public_data_never_promotes_enterprise_value_or_shadow() -> None:
    receipt = _build()

    assert receipt["claim_boundary"] == ("NOT_OBSERVED_ENTERPRISE_SHADOW_NOT_REALIZED_ROI_NOT_PRODUCTION_SLA")
    assert len(receipt["enterprise_value_metrics"]) == 7
    assert all(row["status"] == "NOT_RUN" for row in receipt["enterprise_value_metrics"])
    assert all(row["value"] is None for row in receipt["enterprise_value_metrics"])
    assert "Scope reduction" in receipt["limitations"][2]


def test_projection_preserves_object_centric_tables_and_provenance() -> None:
    projection = _load(BENCHMARK / "projection" / "object-centric-projection.json")
    required = {
        "objects",
        "events",
        "event_object_relations",
        "object_object_relations",
        "object_attribute_history",
    }

    assert required.issubset(projection)
    assert "cases" not in projection
    assert "case_id" not in projection
    assert projection["table_counts"] == {
        "objects": 96,
        "events": 22,
        "event_object_relations": 253,
        "object_object_relations": 133,
        "object_attribute_history": 191,
    }
    assert set(projection["provenance_legend"]) == {
        "OBSERVED",
        "DERIVED",
        "EXPERIMENTER_INJECTED",
        "MISSING",
    }
    _validate_projection(projection)


def test_ground_truth_and_dependencies_are_explicitly_injected_or_missing() -> None:
    projection = _load(BENCHMARK / "projection" / "object-centric-projection.json")
    overlay = _load(BENCHMARK / "overlay" / "policy-change-ground-truth.json")
    _validate_overlay(overlay, projection)

    assert overlay["ground_truth"]["origin"] == "EXPERIMENTER_INJECTED"
    assert overlay["policy_change"]["source_attribute_origin"] == "OBSERVED"
    assert overlay["policy_change"]["policy_interpretation_origin"] == "EXPERIMENTER_INJECTED"
    counts = {
        kind: sum(1 for row in overlay["dependency_claims"] if row["dependency_kind"] == kind)
        for kind in ("ACTUAL_READ", "EXPLICIT_NON_DEPENDENCY", "UNKNOWN")
    }
    assert counts == {"ACTUAL_READ": 5, "EXPLICIT_NON_DEPENDENCY": 4, "UNKNOWN": 2}
    assert all(
        row["origin"] == "MISSING"
        for row in overlay["dependency_claims"]
        if row["dependency_kind"] == "UNKNOWN"
    )


def test_exact_upstream_version_license_and_checksums_are_pinned() -> None:
    manifest = _load(BENCHMARK / "dataset-manifest.json")
    licenses = _load(BENCHMARK / "LICENSES.json")

    assert manifest["doi"] == "10.5281/zenodo.8337464"
    assert manifest["artifact"] == {
        "file_name": "order-management.sqlite",
        "download_url": "https://zenodo.org/api/records/8337464/files/order-management.sqlite/content",
        "size_bytes": 9592832,
        "zenodo_md5": "md5:5a1f2c98beabe0f647cb0b0b83f07844",
        "sha256": "sha256:a71ee17ea394fad6fa6ac3c5e661c309cd8ca362cde838bf38fee62496bf6f40",
        "raw_bytes_in_repository": False,
    }
    assert manifest["dataset_kind"] == "ARTIFICIAL_PUBLIC_OCEL_2_0_SIMULATION"
    assert licenses["upstream"]["spdx"] == "CC-BY-4.0"
    assert licenses["pm4py"]["used"] is False
    assert "raw SQLite bytes are not redistributed" in licenses["upstream"]["included_material"]


def test_evaluator_and_verifier_are_product_independent() -> None:
    evaluator = ast.parse((ROOT / "scripts" / "run_public_process_bridge.py").read_text(encoding="utf-8"))
    verifier = ast.parse((ROOT / "scripts" / "verify_public_process_bridge.py").read_text(encoding="utf-8"))

    for tree in (evaluator, verifier):
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        assert not any(name == "orgrebase" or name.startswith("orgrebase.") for name in imports)
    verifier_imports = {
        node.module for node in ast.walk(verifier) if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "scripts.run_public_process_bridge" not in verifier_imports


def test_relabelling_public_projection_as_enterprise_shadow_fails() -> None:
    projection = _load(BENCHMARK / "projection" / "object-centric-projection.json")
    projection["evidence_class"] = "OBSERVED_ENTERPRISE_SHADOW"
    projection = _seal(projection)

    with pytest.raises(PublicProcessBridgeError, match="PUBLIC_EVIDENCE_CLASS_INVALID"):
        _validate_projection(projection)


def test_flattening_object_centric_projection_fails() -> None:
    projection = _load(BENCHMARK / "projection" / "object-centric-projection.json")
    projection.pop("object_object_relations")
    projection["cases"] = [{"case_id": "o-990599"}]
    projection = _seal(projection)

    with pytest.raises(PublicProcessBridgeError, match="OBJECT_CENTRIC_TABLE_MISSING"):
        _validate_projection(projection)


@pytest.mark.parametrize(
    ("dependency_kind", "origin"),
    [
        ("EXPLICIT_NON_DEPENDENCY", "MISSING"),
        ("ACTUAL_READ", "DERIVED"),
        ("UNKNOWN", "EXPERIMENTER_INJECTED"),
    ],
)
def test_invalid_dependency_semantics_fail_closed(
    dependency_kind: str,
    origin: str,
) -> None:
    projection = _load(BENCHMARK / "projection" / "object-centric-projection.json")
    overlay = _load(BENCHMARK / "overlay" / "policy-change-ground-truth.json")
    overlay["dependency_claims"][-1].update(
        dependency_kind=dependency_kind,
        origin=origin,
    )
    overlay = _seal(overlay)

    with pytest.raises(PublicProcessBridgeError, match="DEPENDENCY_SEMANTICS_INVALID"):
        _validate_overlay(overlay, projection)


def test_organizational_context_cannot_be_promoted_to_causal_traversal() -> None:
    mapping = _load(MAPPING)
    next(row for row in mapping["relation_rules"] if row["source_qualifier"] == "places")[
        "causal_traversal"
    ] = True
    mapping = _seal(mapping)

    with pytest.raises(PublicProcessBridgeError, match="CONTEXT_RULE_UNSAFE:places"):
        _validate_mapping(mapping)


def test_manifest_tamper_fails_before_evaluation(tmp_path: Path) -> None:
    copied = tmp_path / "quote-value-v0.3-public-process"
    shutil.copytree(BENCHMARK, copied)
    projection_path = copied / "projection" / "object-centric-projection.json"
    projection_path.write_text(projection_path.read_text(encoding="utf-8") + " ", encoding="utf-8")

    with pytest.raises(PublicProcessBridgeError, match="BENCHMARK_DIGEST_MISMATCH"):
        _build(copied)


def test_missing_actual_read_becomes_unknown_and_fails_recall_gate(tmp_path: Path) -> None:
    copied = tmp_path / "quote-value-v0.3-public-process"
    shutil.copytree(BENCHMARK, copied)
    overlay_path = copied / "overlay" / "policy-change-ground-truth.json"
    overlay = _load(overlay_path)
    affected_ref = overlay["ground_truth"]["expected_affected"][0]
    claim = next(row for row in overlay["dependency_claims"] if row["target_ref"] == affected_ref)
    claim.update(dependency_kind="UNKNOWN", origin="MISSING", observed_support_path=[])
    overlay = _seal(overlay)
    overlay_path.write_text(json.dumps(overlay, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    dataset_path = copied / "dataset-manifest.json"
    dataset = _load(dataset_path)
    dataset["overlay_digest"] = overlay["digest"]
    dataset = _seal(dataset)
    dataset_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _rewrite_manifest(copied)

    _validate_overlay(overlay, _load(copied / "projection" / "object-centric-projection.json"))
    with pytest.raises(PublicProcessBridgeError, match="OAC_RECALL_GATE_FAILED"):
        _build(copied)


def test_receipt_mutation_and_enterprise_metric_promotion_are_rejected() -> None:
    receipt = _load(RETAINED)
    receipt["strategies"][-1]["metrics"]["scope_reduction_rate"]["value"] = 0.99
    receipt["enterprise_value_metrics"][0].update(
        status="CALCULATED",
        value=0.5,
        evidence_class="PUBLIC_SOURCE_DERIVED_SYNTHETIC",
    )
    receipt = _seal(receipt)

    failures = verify(receipt, ROOT)
    assert "STRATEGY_RECOMPUTATION_MISMATCH" in failures
    assert "PUBLIC_DATA_ENTERPRISE_VALUE_PROMOTION" in failures


def test_wrong_source_bytes_are_rejected_without_network(tmp_path: Path) -> None:
    fake = tmp_path / "order-management.sqlite"
    fake.write_bytes(b"not-the-pinned-ocel-source")

    with pytest.raises(ValueError, match="SOURCE_SIZE_MISMATCH"):
        verify_source(fake)


def test_static_schemas_are_parseable_and_pin_evidence_boundaries() -> None:
    schema_names = (
        "workspace-public-process-dataset-manifest.schema.json",
        "workspace-public-process-projection.schema.json",
        "workspace-public-causal-overlay.schema.json",
        "workspace-public-process-mapping.schema.json",
        "workspace-public-process-bridge-receipt.schema.json",
    )
    schemas = [_load(ROOT / "schemas" / name) for name in schema_names]

    assert all(schema["$schema"] == "https://json-schema.org/draft/2020-12/schema" for schema in schemas)
    assert schemas[0]["properties"]["evidence_class"]["const"] == "PUBLIC_SOURCE_DERIVED_SYNTHETIC"
    assert schemas[1]["properties"]["evidence_class"]["const"] == "PUBLIC_PROCESS_OBSERVED_DERIVED"
    assert schemas[2]["properties"]["ground_truth"]["properties"]["origin"]["const"] == (
        "EXPERIMENTER_INJECTED"
    )
    assert schemas[3]["properties"]["missing_semantics"]["const"] == "UNKNOWN"
    assert schemas[4]["properties"]["claim_ceiling"]["const"] == "MECHANISM_VALIDATION_ONLY"


def test_retained_receipt_contains_no_machine_specific_absolute_path() -> None:
    text = RETAINED.read_text(encoding="utf-8")
    assert "/Users/" not in text
    assert "/tmp/" not in text
    assert "file://" not in text
