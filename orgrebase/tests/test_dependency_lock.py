from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _requirements(raw: str) -> list[str]:
    return [line.strip() for line in raw.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def test_pip_installation_requirements_match_frozen_runtime_lock() -> None:
    uv = shutil.which("uv")
    assert uv, "The contributor environment requires uv to verify the dependency lock"
    result = subprocess.run(
        [uv, "export", "--frozen", "--no-dev", "--no-emit-project", "--no-hashes"],
        cwd=ROOT,
        env={**os.environ, "UV_PYTHON_DOWNLOADS": "never"},
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert _requirements((ROOT / "requirements.txt").read_text()) == _requirements(result.stdout), (
        "Regenerate requirements.txt from uv.lock with "
        "uv export --frozen --no-dev --no-emit-project --no-hashes -o requirements.txt"
    )
