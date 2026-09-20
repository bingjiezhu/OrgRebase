#!/usr/bin/env python3
"""Build both projects offline and replay the evolution journey from wheel bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OAC_ROOT = ROOT.parent / "oac-spec"
EVALUATOR = ROOT / "scripts" / "verify_oac_evolution_evidence.py"


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> str:
    return subprocess.run(command, cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout


def _digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _build(root: Path, destination: Path, pattern: str) -> Path:
    destination.mkdir(parents=True)
    _run(
        ["uv", "build", "--wheel", "--offline", "--out-dir", str(destination)],
        cwd=root,
    )
    wheels = sorted(destination.glob(pattern))
    if len(wheels) != 1:
        raise RuntimeError(f"expected one {pattern} wheel, found {len(wheels)}")
    return wheels[0]


def _unpack(wheel: Path, destination: Path) -> None:
    destination.mkdir()
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(destination)


def _probe(python_path: str, cwd: Path, env: dict[str, str]) -> dict[str, Any]:
    code = (
        "import importlib.metadata,json,oac,orgrebase;"
        "print(json.dumps({'orgrebase':{'module':orgrebase.__file__,"
        "'version':importlib.metadata.version('orgrebase')},'oac':{'module':oac.__file__,"
        "'version':importlib.metadata.version('oac-contract')}}))"
    )
    return json.loads(_run([python_path, "-c", code], cwd=cwd, env=env))


def run(output_root: Path, oac_root: Path) -> dict[str, Any]:
    output_root, oac_root = output_root.resolve(), oac_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="orgrebase-dual-wheel-") as raw:
        work = Path(raw)
        org_wheel = _build(ROOT, work / "dist-org", "orgrebase-*.whl")
        oac_wheel = _build(oac_root, work / "dist-oac", "oac_contract-*.whl")
        org_unpacked, oac_unpacked = work / "org-wheel", work / "oac-wheel"
        _unpack(org_wheel, org_unpacked)
        _unpack(oac_wheel, oac_unpacked)
        policy = work / "runtime-admission-policy.json"
        shutil.copy2(ROOT / "configs" / "oac" / policy.name, policy)
        env = os.environ.copy()
        env.update(
            PYTHONPATH=os.pathsep.join((str(org_unpacked), str(oac_unpacked))),
            PYTHONNOUSERSITE="1",
            ORGREBASE_OAC_PYTHON=sys.executable,
        )
        probe = _probe(sys.executable, work, env)
        for name, unpacked in (("orgrebase", org_unpacked), ("oac", oac_unpacked)):
            module = Path(probe[name].pop("module")).resolve()
            if not module.is_relative_to(unpacked.resolve()):
                raise RuntimeError(f"{name} was not imported from unpacked wheel bytes")
            probe[name]["module_loaded_from_wheel"] = True
            probe[name]["module_relative_path"] = module.relative_to(unpacked.resolve()).as_posix()
        pack = output_root / "pack"
        summary = json.loads(
            _run(
                [
                    sys.executable,
                    "-m",
                    "orgrebase.cli",
                    "workspace-oac-evolution-demo",
                    "--output-dir",
                    str(pack),
                    "--oac-root",
                    str(oac_root),
                    "--policy",
                    str(policy),
                ],
                cwd=work,
                env=env,
            )
        )
        evaluator_env = {**os.environ, "PYTHONNOUSERSITE": "1"}
        evaluator_env.pop("PYTHONPATH", None)
        evaluation = json.loads(
            _run([sys.executable, str(EVALUATOR), str(pack)], cwd=work, env=evaluator_env)
        )
        retained = output_root / "wheels"
        retained.mkdir(exist_ok=True)
        shutil.copy2(org_wheel, retained / org_wheel.name)
        shutil.copy2(oac_wheel, retained / oac_wheel.name)
        result = {
            "schema_version": "orgrebase.oac-evolution-dual-wheel-check.v1",
            "status": "PASS" if summary["status"] == evaluation["status"] == "PASS" else "FAIL",
            "offline_build": True,
            "clean_temporary_working_directory": True,
            "oac_source_material_mode": "READ_ONLY_PUBLIC_SOURCE_COMMITMENT",
            "probe": probe,
            "wheels": {
                "orgrebase": {"file": org_wheel.name, "sha256": _digest(org_wheel)},
                "oac": {"file": oac_wheel.name, "sha256": _digest(oac_wheel)},
            },
            "pack_digest": summary["evidence_index"]["pack_digest"],
            "independent_evaluation": evaluation,
        }
    (output_root / "wheel-check.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=Path("evidence/oac-evolution/wheel-check"))
    parser.add_argument("--oac-root", type=Path, default=DEFAULT_OAC_ROOT)
    arguments = parser.parse_args()
    result = run(arguments.output_root, arguments.oac_root)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
