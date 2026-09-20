#!/usr/bin/env python3
"""Write or drift-check deterministic mechanics-only benchmark manifests."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import rfc8785

from oac.benchmark import build_run_manifests


def _render(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(rfc8785.dumps(value)).hexdigest()}"


def _verify_closure(value: object, label: str) -> None:
    if not isinstance(value, dict) or not isinstance(value.get("entries"), list):
        raise ValueError(f"{label} is not a closed file ledger")
    if value.get("digest") != _digest(value["entries"]):
        raise ValueError(f"{label} detached digest mismatch")
    paths = [item.get("path") for item in value["entries"]]
    if (
        not all(isinstance(path, str) and path for path in paths)
        or paths != sorted(paths)
        or len(paths) != len(set(paths))
    ):
        raise ValueError(f"{label} paths are not sorted and unique")


def _behavioral_projection(manifest: dict[str, object]) -> dict[str, object]:
    """Exclude only the revision-scoped implementation byte identity.

    Published run manifests remain immutable evidence for the implementation
    that produced them.  A later, behavior-compatible source tree is checked
    against every frozen input, output, metric, and claim field without
    pretending that its bytes are the historical bytes.
    """

    return {
        key: value
        for key, value in manifest.items()
        if key not in {"digest", "implementationClosure", "implementationDigest"}
    }


def verify_frozen_run(
    frozen: dict[str, object], current: dict[str, object]
) -> None:
    detached = {key: value for key, value in frozen.items() if key != "digest"}
    if frozen.get("digest") != _digest(detached):
        raise ValueError("frozen run-manifest detached digest mismatch")
    _verify_closure(frozen.get("inputClosure"), "frozen input closure")
    _verify_closure(
        frozen.get("implementationClosure"), "frozen implementation closure"
    )
    if frozen.get("inputClosureDigest") != frozen["inputClosure"]["digest"]:  # type: ignore[index]
        raise ValueError("frozen input closure binding mismatch")
    if frozen.get("implementationDigest") != frozen["implementationClosure"]["digest"]:  # type: ignore[index]
        raise ValueError("frozen implementation closure binding mismatch")
    if _behavioral_projection(frozen) != _behavioral_projection(current):
        raise ValueError("current benchmark behavior differs from the frozen run")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, help="New evidence coordinate for publication")
    args = parser.parse_args()
    output = args.output or args.root / "benchmark" / "runs"
    if not args.check and output.exists():
        parser.error("benchmark output already exists; use --output with a new coordinate")
    manifests = build_run_manifests(args.root)
    if not args.check:
        try:
            # Claim the whole coordinate before writing its first manifest.
            output.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            parser.error("benchmark output already exists; use --output with a new coordinate")
    stale: list[str] = []
    for system_id, manifest in manifests.items():
        path = output / f"{system_id}.run.json"
        payload = _render(manifest)
        if args.check:
            if not path.exists():
                stale.append(path.name)
                continue
            try:
                frozen = json.loads(path.read_bytes())
                if not isinstance(frozen, dict):
                    raise ValueError("run manifest root is not an object")
                verify_frozen_run(frozen, manifest)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                stale.append(path.name)
        else:
            with path.open("xb") as stream:
                stream.write(payload)
    if stale:
        parser.error(f"benchmark run-manifest drift: {', '.join(stale)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
