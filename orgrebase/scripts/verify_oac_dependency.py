"""Check an explicitly supplied OAC checkout against the product admission policy."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tomllib
from pathlib import Path

from orgrebase.domain import IntegrityError
from orgrebase.workspace.oac_wire import build_oac_public_source_manifest, load_runtime_policy


def verify_dependency(root: Path, *, revision: str | None = None) -> dict[str, object]:
    root = root.resolve(strict=True)
    if revision is not None:
        if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
            raise ValueError("OAC_DEPENDENCY_COMMIT_REQUIRED")
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        )
        if result.stdout.strip() != revision:
            raise ValueError("OAC_DEPENDENCY_COMMIT_MISMATCH")
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
            check=True, capture_output=True, text=True,
        )
        if result.stdout:
            raise ValueError("OAC_DEPENDENCY_CHECKOUT_DIRTY")
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if project.get("name") != "oac-contract":
        raise ValueError("OAC_DEPENDENCY_PROJECT_MISMATCH")
    manifest, fingerprint = build_oac_public_source_manifest(root)
    policy, policy_digest = load_runtime_policy()
    version = project["version"]
    if policy["allowed_oac_source_fingerprints"].get(version) != fingerprint:
        raise ValueError("OAC_DEPENDENCY_NOT_ADMITTED")
    return {
        "status": "PASS", "version": version,
        "source_fingerprint": fingerprint, "source_files": len(manifest["files"]),
        "policy_digest": policy_digest, "verified_git_revision": revision,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--revision", help="Require this exact Git commit and a clean checkout")
    args = parser.parse_args()
    try:
        result = verify_dependency(args.root, revision=args.revision)
    except (OSError, ValueError, KeyError, IntegrityError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"OAC dependency verification failed: {exc}\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
