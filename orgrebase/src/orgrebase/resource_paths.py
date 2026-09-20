"""Resolve source-checkout assets with installed-wheel fallbacks.

The project deliberately keeps benchmark and configuration assets outside the Python
package in a source checkout. Hatch force-includes the runtime subset under
``orgrebase/_assets`` so the same deterministic paths remain available after a
wheel install.
"""

from __future__ import annotations

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parents[1]
PACKAGED_ASSET_ROOT = PACKAGE_ROOT / "_assets"


def runtime_asset_path(relative: str | Path) -> Path:
    """Return an existing checkout asset or its installed-package copy."""

    relative_path = Path(relative)
    checkout = PROJECT_ROOT / relative_path
    if checkout.exists():
        return checkout
    packaged = PACKAGED_ASSET_ROOT / relative_path
    if packaged.exists():
        return packaged
    raise FileNotFoundError(f"ORGREBASE_RUNTIME_ASSET_MISSING:{relative_path.as_posix()}")
