#!/usr/bin/env python3
"""Replay public evolution checks against an installed wheel outside its checkout."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MATERIALS = (
    "tests/test_evolution.py",
    "tck/fixtures/evolution/root-vector.json",
    "tck/fixtures/evolution/mutations.json",
    "profiles/supplier-change/inputs/veracier-proc01.snapshot.json",
)


def digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def replay(wheel: Path) -> dict[str, Any]:
    wheel = wheel.resolve(strict=True)
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "UV_CACHE_DIR"}
    }
    with tempfile.TemporaryDirectory(prefix="oac-installed-evolution-") as directory:
        root = Path(directory)
        installation = root / "installation"
        isolated = root / "replay"
        isolated.mkdir()

        def run(command: list[str]) -> str:
            result = subprocess.run(
                command, cwd=isolated, env=environment, capture_output=True, text=True, timeout=180
            )
            if result.returncode:
                raise RuntimeError(
                    f"installed replay failed ({result.returncode}): {result.stdout}\n{result.stderr}"
                )
            return result.stdout

        run(["uv", "venv", "--python", sys.executable, str(installation)])
        python = str(installation / "bin/python")
        run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                python,
                str(wheel),
                f"pytest=={importlib.metadata.version('pytest')}",
            ]
        )
        for relative in MATERIALS:
            target = isolated / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        imported = json.loads(
            run(
                [
                    python,
                    "-I",
                    "-c",
                    "import importlib.metadata,json,oac,platform; "
                    "print(json.dumps({'module':oac.__file__,'python':platform.python_version(),"
                    "'package':importlib.metadata.version('oac-contract')}))",
                ]
            )
        )
        if not Path(imported["module"]).resolve().is_relative_to(installation.resolve()):
            raise RuntimeError("installed replay imported outside the fresh installation")
        tests = run([python, "-I", "-m", "pytest", "-W", "error", "-q", "tests/test_evolution.py"])
        tck = json.loads(run([python, "-I", "-m", "oac.cli", "tck"]))
        if tck["failed"] != 0 or tck["passed"] != 33:
            raise RuntimeError("installed package TCK does not satisfy the frozen 33-case gate")
        registry = json.loads(
            run([python, "-I", "-m", "oac.cli", "registry", "evolution-semantic-validation-rules"])
        )
        source_registry = json.loads(
            (ROOT / "schemas/evolution-semantic-validation-rules.json").read_bytes()
        )
        if registry != source_registry:
            raise RuntimeError("installed evolution registry differs from the frozen source schema")
        return {
            "schema_version": "oac.installed-evolution-replay.v1",
            "status": "PASS",
            "completed_at": datetime.now(UTC).isoformat(),
            "wheel_name": wheel.name,
            "wheel_sha256": digest(wheel),
            "installed": imported,
            "isolated_import_verified": True,
            "tests": tests.strip(),
            "packaged_tck": tck,
            "evolution_registry_matches_source": True,
            "materials": [
                {"path": relative, "sha256": digest(ROOT / relative)} for relative in MATERIALS
            ],
            "claim_boundary": "same-reference built-wheel replay, zero target effects; not clean Git archive, independent implementation, or enterprise outcome evidence",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = replay(args.wheel)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
        output.write("\n")
    print(
        json.dumps(
            {
                "status": result["status"],
                "wheel_sha256": result["wheel_sha256"],
                "tests": result["tests"],
                "tck_passed": result["packaged_tck"]["passed"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
