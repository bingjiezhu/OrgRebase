"""OrgRebase deterministic enterprise change-consistency runtime."""

import tomllib
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orgrebase.service import OrgRebaseService


def _resolve_version() -> str:
    """Use this checkout's project metadata or the installed distribution."""
    module_path = Path(__file__).resolve()
    project_root = module_path.parents[2]
    # A source checkout can coexist with another installed release. Its own
    # pyproject is authoritative only when it owns this exact module path.
    if module_path == project_root / "src" / "orgrebase" / "__init__.py":
        project_file = project_root / "pyproject.toml"
        if project_file.is_file():
            with project_file.open("rb") as stream:
                project = tomllib.load(stream).get("project", {})
            if project.get("name") == "orgrebase":
                return str(project["version"])
    try:
        return metadata.version("orgrebase")
    except metadata.PackageNotFoundError:
        return "0+unknown"


__version__ = _resolve_version()

__all__ = ["OrgRebaseService"]


def __getattr__(name: str):
    if name == "OrgRebaseService":
        from orgrebase.service import OrgRebaseService

        globals()[name] = OrgRebaseService
        return OrgRebaseService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
