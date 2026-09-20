"""Permission-safe helpers for local runtime storage."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def require_safe_sqlite_wal_runtime() -> None:
    """Reject engines predating the WAL-reset fix, including its official backports."""
    version = sqlite3.sqlite_version_info
    if (
        version >= (3, 51, 3)
        or ((3, 44, 6) <= version < (3, 45, 0))
        or ((3, 50, 7) <= version < (3, 51, 0))
    ):
        return
    observed = ".".join(str(part) for part in version)
    raise RuntimeError(f"SQLITE_WAL_RUNTIME_UNSUPPORTED:{observed}")


def prepare_private_sqlite_path(path: str | Path) -> str:
    """Create or tighten one local SQLite file before SQLite opens it."""

    selected = str(path)
    if selected == ":memory:":
        return selected
    flags = os.O_CREAT | os.O_RDWR
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(selected, flags, 0o600)
    try:
        fchmod = getattr(os, "fchmod", None)
        if fchmod is not None:
            fchmod(descriptor, 0o600)
        else:  # pragma: no cover - Windows compatibility fallback
            os.chmod(selected, 0o600)
    finally:
        os.close(descriptor)
    return selected
