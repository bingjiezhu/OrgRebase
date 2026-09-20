#!/usr/bin/env python3
"""Fetch and verify the exact AgentTeams source needed by the native adapter."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from orgrebase.agentteams_source import default_agentteams_checkout, load_agentteams_source
from orgrebase.workspace.native_taskflow import verify_teamharness_checkout

ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _offline_bundle(lock: dict[str, object]) -> Path | None:
    descriptor = lock.get("offline_bundle")
    if descriptor is None:
        return None
    if not isinstance(descriptor, dict):
        raise SystemExit("AGENTTEAMS_BUNDLE_DESCRIPTOR_INVALID")
    relative = descriptor.get("path")
    expected = descriptor.get("sha256")
    if not isinstance(relative, str) or not relative or not isinstance(expected, str):
        raise SystemExit("AGENTTEAMS_BUNDLE_DESCRIPTOR_INVALID")
    bundle = (ROOT / relative).resolve()
    try:
        bundle.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise SystemExit("AGENTTEAMS_BUNDLE_PATH_ESCAPE") from exc
    if not bundle.exists():
        return None
    if not bundle.is_file() or _sha256(bundle) != expected:
        raise SystemExit("AGENTTEAMS_BUNDLE_DIGEST_MISMATCH")
    return bundle


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, default=ROOT / "agentteams/teamharness-lock.json")
    parser.add_argument("--output", type=Path, default=default_agentteams_checkout(ROOT))
    args = parser.parse_args()
    lock = json.loads(args.lock.read_text(encoding="utf-8"))
    load_agentteams_source(ROOT).require_teamharness_identity(lock)
    output = args.output.expanduser().resolve()
    if output.exists():
        result = verify_teamharness_checkout(output, args.lock)
        print(json.dumps({**result, "mode": "REUSED"}, sort_keys=True))
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(tempfile.mkdtemp(prefix=".agentteams-fetch-", dir=output.parent))
    checkout = temporary_root / "checkout"
    bundle = _offline_bundle(lock)
    mode = "PACKAGED_BUNDLE" if bundle is not None else "NETWORK_FETCH"
    try:
        if bundle is not None:
            subprocess.run(
                ["git", "clone", "--no-checkout", str(bundle), str(checkout)],
                check=True,
                timeout=180,
            )
            subprocess.run(
                ["git", "remote", "set-url", "origin", str(lock["upstream"])],
                cwd=checkout,
                check=True,
                timeout=20,
            )
        else:
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--filter=blob:none",
                    "--no-checkout",
                    str(lock["upstream"]),
                    str(checkout),
                ],
                check=True,
                timeout=180,
            )
        subprocess.run(
            ["git", "checkout", "--detach", str(lock["commit"])],
            cwd=checkout,
            check=True,
            timeout=120,
        )
        result = verify_teamharness_checkout(checkout, args.lock)
        os.replace(checkout, output)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"AGENTTEAMS_FETCH_FAILED:{type(exc).__name__}") from exc
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)
    print(json.dumps({**result, "checkout": str(output), "mode": mode}, sort_keys=True))


if __name__ == "__main__":
    main()
