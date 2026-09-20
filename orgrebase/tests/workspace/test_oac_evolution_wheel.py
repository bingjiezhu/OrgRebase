from __future__ import annotations

import json
from pathlib import Path

from scripts.run_oac_evolution_wheel_check import run

OAC_ROOT = Path(__file__).resolve().parents[3] / "oac-spec"


def test_dual_wheel_replays_complete_evolution_with_independent_evaluator(tmp_path: Path) -> None:
    output = tmp_path / "wheel-check"

    result = run(output, OAC_ROOT)

    assert result["status"] == "PASS"
    assert result["offline_build"] is True
    assert result["clean_temporary_working_directory"] is True
    index = json.loads((output / "pack/evidence-index.json").read_text(encoding="utf-8"))
    assert result["pack_digest"] == index["pack_digest"]
    assert result["independent_evaluation"] == {
        "schema_version": "orgrebase.oac-evolution-independent-verification.v1",
        "status": "PASS",
        "artifact_count": 51,
        "event_count": 13,
        "pack_digest": result["pack_digest"],
        "product_imports": 0,
        "failures": [],
    }
    assert all(item["module_loaded_from_wheel"] for item in result["probe"].values())
    assert {path.name for path in (output / "wheels").glob("*.whl")} == {
        result["wheels"]["orgrebase"]["file"],
        result["wheels"]["oac"]["file"],
    }
    assert json.loads((output / "wheel-check.json").read_text(encoding="utf-8")) == result
