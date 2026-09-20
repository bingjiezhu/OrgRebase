from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
V01 = ROOT / "benchmark" / "product-path-v0.1"
V02 = ROOT / "benchmark" / "product-path-v0.2-source-bound"
V03 = ROOT / "benchmark" / "product-path-v0.3-task-intake-bound"


def _load_evaluator() -> ModuleType:
    path = ROOT / "scripts" / "workspace_product_path_evaluator.py"
    spec = importlib.util.spec_from_file_location("product_path_migration_evaluator", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_product_path_v01_corpus_is_byte_immutable() -> None:
    assert _sha256(V01 / "MANIFEST.sha256") == (
        "160ca15b84da88480a64b6051279de94fb96e78aec4cfe1a3dab7d617e365097"
    )
    assert _sha256(V01 / "public" / "cases.json") == (
        "5def5aea85fa91939f9bc9d777fe4304fdfb5ee859d06265ee88b515d1edca56"
    )
    assert _sha256(V01 / "evaluator" / "gold.json") == (
        "c7409ec93787dcdd1393661fc6eb7cf7ea3fcfbfa165929d95fef069fe11623a"
    )
    assert _sha256(V01 / "evaluator" / "mutations.json") == (
        "ecca799fc87345f35751a905115c48479309866c2ef5d9f7fcbf44e3a5047f9c"
    )
    assert _sha256(V01 / "README.md") == (
        "1ab06b1ab799fae6ce764cdaf8025e5d29a45794d81b5a896fa16f164199cbae"
    )


def test_product_path_v02_corpus_is_byte_immutable() -> None:
    expected = {
        "BENCHMARK-MIGRATION.json": (
            "68aff9d24af977ae502b15d98a5a87f5d3072f83e96aad9fe2f8e497d7c4ef59"
        ),
        "MANIFEST.sha256": (
            "230d2c20fb34ee2f7718abd24042fae360b57090a4373dce2c948d841af65ea7"
        ),
        "README.md": (
            "bf63dfc0ee06b75f60fb33a57366a3e27b383abbac15e507d55af9b557cb8c41"
        ),
        "public/cases.json": (
            "bf72b5149abbc25e338f4cf3ebd80dc1acf6eeca7f8aae1fd8f19dfd3c6e1d7b"
        ),
        "evaluator/gold.json": (
            "ab1a66d510e662c322585bf217296733ca5877ac9303c4d214e1d1950a7a2f70"
        ),
        "evaluator/mutations.json": (
            "fac06804b85c501a69e532f3ff024e9b9b437f19c9dd43f7d6ea01ce79d9a21d"
        ),
    }
    assert {
        path: _sha256(V02 / path)
        for path in expected
    } == expected


def test_source_bound_migration_delta_is_exact_and_predecessor_bound() -> None:
    evaluator = _load_evaluator()
    status = evaluator._migration_status(
        project_root=ROOT,
        benchmark_root=V02,
        benchmark_version="ProductPath-v0.2-source-bound",
    )

    assert status["status"] == "PASS", status
    assert status["failures"] == []
    assert status["predecessor_benchmark_version"] == "ProductPath-v0.1"
    assert status["predecessor_retained_wheel_sha256"] == (
        "sha256:444d00125c77d63c77b393730a8cf158a8045a6647b9773a99a53053bcb8e89e"
    )


def test_source_bound_mutation_set_extends_without_rewriting_v01() -> None:
    v01 = json.loads((V01 / "evaluator" / "mutations.json").read_text(encoding="utf-8"))
    v02 = json.loads((V02 / "evaluator" / "mutations.json").read_text(encoding="utf-8"))

    assert v02["mutations"][: len(v01["mutations"])] == v01["mutations"]
    assert [item["category"] for item in v02["mutations"][6:]] == [
        "source_root",
        "runtime_projection",
        "source_binding",
    ]


def test_task_intake_bound_migration_is_exact_and_predecessor_bound() -> None:
    evaluator = _load_evaluator()
    status = evaluator._migration_status(
        project_root=ROOT,
        benchmark_root=V03,
        benchmark_version="ProductPath-v0.3-task-intake-bound",
    )

    assert status["status"] == "PASS", status
    assert status["failures"] == []
    assert status["applicability"] == "TASK_INTAKE_BOUND_UPGRADE"
    assert status["predecessor_benchmark_version"] == (
        "ProductPath-v0.2-source-bound"
    )
    assert status["predecessor_retained_wheel_sha256"] is None
    assert status["task_intake_bound_contract_digest"].startswith("sha256:")


def test_task_intake_bound_gold_uses_run_local_dynamic_bindings() -> None:
    v02 = json.loads((V02 / "evaluator" / "gold.json").read_text(encoding="utf-8"))
    v03 = json.loads((V03 / "evaluator" / "gold.json").read_text(encoding="utf-8"))
    oracle = v03["product_oracle"]

    assert "event_chain_head" not in v03["cases"][0]["expected_facts"]
    assert "event_records" not in oracle
    assert oracle["stable_event_records"] == v02["product_oracle"]["event_records"][:2]
    assert len(oracle["event_types"]) == 12
    assert oracle["event_types"][2:4] == [
        "WORKSPACE_TASK_INTAKE_BOUND",
        "TOOL_INVOKED",
    ]
    assert oracle["tool_commitments"] == {
        "status": "SUCCEEDED",
        "request_digest": v02["product_oracle"]["tool_commitments"][
            "request_digest"
        ],
        "result_digest": v02["product_oracle"]["tool_commitments"][
            "result_digest"
        ],
        "called_event_digest": v02["product_oracle"]["tool_commitments"][
            "called_event_digest"
        ],
    }
    assert oracle["task_intake_commitments"]["run_status"] == (
        "FORMATION_COMPLETED"
    )
    assert oracle["approval_control_commitments"]["review_duration_ms"] == 4_000
    assert oracle["approval_control_commitments"]["identity_mode"] == (
        "CONTROLLED_LOCAL_HEADER_IDENTITY"
    )
    pp005 = next(item for item in v03["cases"] if item["id"] == "PP-005")
    assert pp005["expected_facts"]["error_code"] == "EVIDENCE_INTEGRITY_FAILED"
    assert pp005["expected_facts"]["error_reason_code"] == (
        "TASK_INTAKE_CANDIDATE_NOT_READY"
    )


def test_task_intake_bound_mutations_extend_v02_without_rewriting_it() -> None:
    v02 = json.loads((V02 / "evaluator" / "mutations.json").read_text(encoding="utf-8"))
    v03 = json.loads((V03 / "evaluator" / "mutations.json").read_text(encoding="utf-8"))

    assert v03["mutations"][: len(v02["mutations"])] == v02["mutations"]
    assert [item["category"] for item in v03["mutations"][9:]] == [
        "task_intake_binding",
        "approval_review_gate",
    ]


def test_migration_rejects_unlisted_business_oracle_change(tmp_path: Path) -> None:
    evaluator = _load_evaluator()
    benchmark_root = tmp_path / "benchmark"
    copied_v01 = benchmark_root / "product-path-v0.1"
    copied_v02 = benchmark_root / "product-path-v0.2-source-bound"
    shutil.copytree(V01, copied_v01)
    shutil.copytree(V02, copied_v02)
    gold_path = copied_v02 / "evaluator" / "gold.json"
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    gold["product_oracle"]["currency"] = "USD"
    gold_path.write_text(json.dumps(gold, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    status = evaluator._migration_status(
        project_root=tmp_path,
        benchmark_root=copied_v02,
        benchmark_version="ProductPath-v0.2-source-bound",
    )

    assert status["status"] == "FAIL"
    assert "MIGRATION_DELTA_NOT_EXACT:evaluator_gold" in status["failures"]
    assert "MIGRATION_REQUIRED_LOCK_CHANGED:/product_oracle/currency" in status["failures"]


def test_task_intake_migration_rejects_unlisted_business_oracle_change(
    tmp_path: Path,
) -> None:
    evaluator = _load_evaluator()
    benchmark_root = tmp_path / "benchmark"
    copied_v02 = benchmark_root / "product-path-v0.2-source-bound"
    copied_v03 = benchmark_root / "product-path-v0.3-task-intake-bound"
    shutil.copytree(V02, copied_v02)
    shutil.copytree(V03, copied_v03)
    gold_path = copied_v03 / "evaluator" / "gold.json"
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    gold["product_oracle"]["currency"] = "USD"
    gold_path.write_text(
        json.dumps(gold, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    status = evaluator._migration_status(
        project_root=tmp_path,
        benchmark_root=copied_v03,
        benchmark_version="ProductPath-v0.3-task-intake-bound",
    )

    assert status["status"] == "FAIL"
    assert "MIGRATION_DELTA_NOT_EXACT:evaluator_gold" in status["failures"]
    assert "MIGRATION_REQUIRED_LOCK_CHANGED:/product_oracle/currency" in status["failures"]
