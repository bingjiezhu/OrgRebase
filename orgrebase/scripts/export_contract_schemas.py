"""Export strict Pydantic trust-boundary models as deterministic JSON Schema.

Legacy schema filenames are frozen. Workspace schemas are discovered from the
checked-in workspace model registry so Python remains the single contract source.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from orgrebase.domain import (
    AgentCandidateIngestionReceipt,
    DependencyManifest,
    ImpactCertificate,
    MinimalRebaseCertificate,
    ToolInvocationReceipt,
)

ROOT = Path(__file__).resolve().parents[1]
MODEL_REGISTRY = ROOT / "configs" / "workspace" / "model-registry.json"

LEGACY_MODELS: dict[str, type[BaseModel]] = {
    "agent-candidate-ingestion.schema.json": AgentCandidateIngestionReceipt,
    "dependency-manifest.schema.json": DependencyManifest,
    "impact-certificate.schema.json": ImpactCertificate,
    "minimal-rebase-certificate.schema.json": MinimalRebaseCertificate,
    "tool-invocation-receipt.schema.json": ToolInvocationReceipt,
}


def _kebab_case(name: str) -> str:
    value = re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()
    return value.replace("_-", "-").replace("_", "-")


def _workspace_models() -> dict[str, type[BaseModel]]:
    if not MODEL_REGISTRY.is_file():
        raise RuntimeError(f"WORKSPACE_MODEL_REGISTRY_MISSING:{MODEL_REGISTRY}")
    registry = json.loads(MODEL_REGISTRY.read_text(encoding="utf-8"))
    models: dict[str, type[BaseModel]] = {}
    for entry in registry.get("models", []):
        if not entry.get("export_schema"):
            continue
        module_name = str(entry["module"])
        model_name = str(entry["name"])
        model = getattr(importlib.import_module(module_name), model_name, None)
        if not isinstance(model, type) or not issubclass(model, BaseModel):
            raise RuntimeError(f"WORKSPACE_MODEL_NOT_PYDANTIC:{module_name}:{model_name}")
        filename = f"workspace-{_kebab_case(model_name)}.schema.json"
        if filename in models:
            raise RuntimeError(f"WORKSPACE_SCHEMA_FILENAME_COLLISION:{filename}")
        models[filename] = model
    return models


def registered_models() -> dict[str, type[BaseModel]]:
    return {**LEGACY_MODELS, **_workspace_models()}


def _encoded(name: str, model: type[BaseModel]) -> str:
    schema: dict[str, Any] = model.model_json_schema(mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"https://orgrebase.local/schemas/{name}"
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    failures: list[str] = []
    models = registered_models()
    for name, model in sorted(models.items()):
        path = ROOT / "schemas" / name
        expected = _encoded(name, model)
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != expected:
                failures.append(name)
        else:
            path.write_text(expected, encoding="utf-8")
    if failures:
        raise SystemExit(f"CONTRACT_SCHEMA_DRIFT:{','.join(failures)}")
    print(
        json.dumps(
            {
                "status": "PASS",
                "mode": "CHECK" if args.check else "WRITE",
                "schemas": len(models),
                "legacy_schemas": len(LEGACY_MODELS),
                "workspace_schemas": len(models) - len(LEGACY_MODELS),
            }
        )
    )


if __name__ == "__main__":
    main()
