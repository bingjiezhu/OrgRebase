"""Explicit environment and file-descriptor boundaries for candidate subprocesses."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

_RUNTIME_VARIABLES = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "SYSTEMROOT", "WINDIR")
_VERTEX_VARIABLES = ("ORGREBASE_VERTEX_PROJECT_ID", "ORGREBASE_VERTEX_MODEL_ID", "ORGREBASE_VERTEX_ACCESS_TOKEN", "ORGREBASE_VERTEX_API_KEY")


def candidate_environment(
    inherited: Mapping[str, str], *, home: Path, model_provider: str | None = None,
) -> dict[str, str]:
    if model_provider not in {None, "ollama-local", "vertex-ai", "deepseek"}:
        raise ValueError("CANDIDATE_MODEL_PROVIDER_INVALID")
    environment = {name: inherited[name] for name in _RUNTIME_VARIABLES if name in inherited}
    environment.update(
        HOME=str(home), PYTHONNOUSERSITE="1", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
    )
    if model_provider == "vertex-ai":
        environment.update({name: inherited[name] for name in _VERTEX_VARIABLES if name in inherited})
    if model_provider == "deepseek" and "DEEPSEEK_API_KEY" in inherited:
        environment["DEEPSEEK_API_KEY"] = inherited["DEEPSEEK_API_KEY"]
    return environment
