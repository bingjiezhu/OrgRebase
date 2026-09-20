"""OrgRebase deterministic enterprise change-consistency runtime."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orgrebase.service import OrgRebaseService

__version__ = "0.4.0"

__all__ = ["OrgRebaseService"]


def __getattr__(name: str):
    if name == "OrgRebaseService":
        from orgrebase.service import OrgRebaseService

        globals()[name] = OrgRebaseService
        return OrgRebaseService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
