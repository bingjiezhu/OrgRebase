#!/usr/bin/env python3
"""Run OrgWorkBench reference, baseline and ablation evaluations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from orgrebase.workspace.benchmark import (
    ABLATION_PROFILES,
    BASELINE_PROFILES,
    BaselineSystem,
    OWBBenchmarkRepository,
    OWBEvaluator,
    ReferenceWorkspaceBenchmarkSUT,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("evidence/workspace/latest/evaluation-suite.json"))
    args = parser.parse_args()
    repository = OWBBenchmarkRepository()
    reference = ReferenceWorkspaceBenchmarkSUT(repository)
    evaluator = OWBEvaluator(repository)
    report = evaluator.evaluate_system(reference, profile="orgrebase-workspace")
    payload = {
        "reference": report.model_dump(mode="json"),
        "baselines": {
            name: evaluator.evaluate_system(BaselineSystem(reference, name), profile=name).model_dump(mode="json")
            for name in BASELINE_PROFILES
        },
        "ablations": {
            name: evaluator.evaluate_system(BaselineSystem(reference, name), profile=name).model_dump(mode="json")
            for name in ABLATION_PROFILES
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report.status, "score": report.core_score.weighted_score, "output": str(args.output)}))


if __name__ == "__main__":
    main()
