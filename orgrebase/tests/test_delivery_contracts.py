from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.export_contract_schemas import ROOT, _encoded, registered_models


def test_exported_contract_schemas_match_current_models() -> None:
    drift = []
    for name, model in registered_models().items():
        path = ROOT / "schemas" / name
        if not path.is_file() or path.read_text(encoding="utf-8") != _encoded(name, model):
            drift.append(name)
    assert not drift, "Run scripts/export_contract_schemas.py: " + ", ".join(sorted(drift))


@pytest.mark.parametrize("target", ["release-hygiene", "test", "check"])
def test_release_checks_respect_explicit_python(tmp_path: Path, target: str) -> None:
    launcher = tmp_path / "configured-python"
    log = tmp_path / "commands.jsonl"
    launcher.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['DELIVERY_COMMAND_LOG'], 'a') as output:\n"
        "    output.write(json.dumps(sys.argv[1:]) + '\\n')\n",
        encoding="utf-8",
    )
    launcher.chmod(0o700)
    result = subprocess.run(
        ["make", "--no-print-directory", "-f", str(ROOT / "Makefile"),
         f"PYTHON={launcher}", target],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "DELIVERY_COMMAND_LOG": str(log)},
    )
    assert result.returncode == 0, result.stderr
    commands = [json.loads(line) for line in log.read_text().splitlines()]
    assert commands[0] == ["scripts/verify_review_readiness.py", "--hygiene-only"]
    if target != "release-hygiene":
        assert commands[-1][:2] == ["-m", "pytest"]
    if target == "check":
        assert ["scripts/export_contract_schemas.py", "--check"] in commands
        assert ["-m", "ruff", "check", "src", "tests", "scripts"] in commands
