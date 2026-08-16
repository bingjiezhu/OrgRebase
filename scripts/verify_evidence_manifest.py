"""Recompute every local evidence digest and reject capability overclaiming."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from orgrebase.digest import sha256_digest

RUN_NONCE = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_CAPABILITIES = {
    "IMPLEMENTED": {"LOCAL_DETERMINISTIC", "LOCAL_REAL_TOOL"},
    "DESIGNED": {"PASS_STATIC"},
    "STATIC_ONLY": {"PASS_STATIC"},
    "REPLAY_ONLY": {"SYNTHETIC_FIXTURE"},
    "NOT_RUN": {"NOT_RUN"},
}


class ManifestError(RuntimeError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ManifestError(message)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"MANIFEST_JSON_INVALID:{path.name}") from exc
    _require(isinstance(value, dict), f"JSON_OBJECT_REQUIRED:{path.name}")
    return value


def verify(manifest_path: Path) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    manifest = _read_json(manifest_path)
    _require(
        manifest.get("schema_version") == "orgrebase.evidence-manifest.v2",
        "MANIFEST_SCHEMA_MISMATCH",
    )
    run_id = manifest.get("workflow_run_id")
    nonce = manifest.get("run_nonce")
    _require(isinstance(run_id, str) and run_id, "RUN_BINDING_MISSING")
    _require(
        isinstance(nonce, str) and bool(RUN_NONCE.fullmatch(nonce)),
        "RUN_BINDING_MISSING",
    )
    _require(
        manifest.get("digest_algorithm") == "orgrebase-canonical-json-sha256-v1",
        "DIGEST_ALGORITHM_UNSUPPORTED",
    )
    artifacts = manifest.get("artifacts")
    _require(isinstance(artifacts, dict) and artifacts, "ARTIFACTS_MISSING")
    base = manifest_path.parent
    verified: list[str] = []
    payloads: dict[str, dict[str, Any]] = {}
    for name, record in artifacts.items():
        _require(isinstance(name, str) and isinstance(record, dict), "ARTIFACT_RECORD_INVALID")
        relative = record.get("path")
        _require(isinstance(relative, str) and relative == name, f"ARTIFACT_PATH_INVALID:{name}")
        path = (base / relative).resolve()
        _require(path.is_relative_to(base) and path.is_file(), f"ARTIFACT_MISSING:{name}")
        _require(not path.is_symlink(), f"ARTIFACT_SYMLINK_REJECTED:{name}")
        payload = _read_json(path)
        _require(sha256_digest(payload) == record.get("digest"), f"ARTIFACT_DIGEST_MISMATCH:{name}")
        _require(record.get("status") == "IMPLEMENTED", f"ARTIFACT_STATUS_INVALID:{name}")
        _require(
            record.get("evidence_class")
            in {"LOCAL_DETERMINISTIC", "LOCAL_REAL_TOOL", "SYNTHETIC_FIXTURE"},
            f"ARTIFACT_EVIDENCE_CLASS_INVALID:{name}",
        )
        payloads[name] = payload
        verified.append(name)

    capabilities = manifest.get("capabilities")
    _require(isinstance(capabilities, dict) and capabilities, "CAPABILITIES_MISSING")
    for name, capability in capabilities.items():
        _require(isinstance(capability, dict), f"CAPABILITY_INVALID:{name}")
        status = capability.get("status")
        evidence_class = capability.get("evidence_class")
        _require(status in ALLOWED_CAPABILITIES, f"CAPABILITY_STATUS_INVALID:{name}")
        _require(
            evidence_class in ALLOWED_CAPABILITIES[status],
            f"CAPABILITY_OVERCLAIM:{name}",
        )
        artifact = capability.get("artifact")
        if artifact is None:
            _require(status != "IMPLEMENTED", f"IMPLEMENTED_WITHOUT_ARTIFACT:{name}")
        else:
            _require(artifact in artifacts, f"CAPABILITY_ARTIFACT_MISSING:{name}")
            _require(
                artifacts[artifact]["evidence_class"] == evidence_class,
                f"CAPABILITY_ARTIFACT_CLASS_MISMATCH:{name}",
            )

    run_bound = {
        "rebase-receipt.json": payloads.get("rebase-receipt.json", {}),
        "conflict-receipt.json": payloads.get("conflict-receipt.json", {}),
        "failure-receipt.json": payloads.get("failure-receipt.json", {}),
        "rollback-receipt.json": payloads.get("rollback-receipt.json", {}),
    }
    for name, payload in run_bound.items():
        if name not in payloads:
            continue
        _require(
            payload.get("workflow_run_id") == run_id and payload.get("run_nonce") == nonce,
            f"CROSS_RUN_ARTIFACT:{name}",
        )
    git = payloads.get("git-tool-evidence.json", {})
    if git:
        for phase in ("patch", "compensation"):
            receipt = git.get(phase, {}).get("receipt", {})
            _require(
                receipt.get("workflow_run_id") == run_id
                and receipt.get("run_nonce") == nonce,
                f"CROSS_RUN_ARTIFACT:git-tool-evidence.json:{phase}",
            )
    rollback_evidence = payloads.get("rollback-evidence.json", {})
    if rollback_evidence:
        rollback_receipt = rollback_evidence.get("receipt", {})
        _require(
            rollback_receipt.get("workflow_run_id") == run_id
            and rollback_receipt.get("run_nonce") == nonce,
            "CROSS_RUN_ARTIFACT:rollback-evidence.json",
        )
    observability = payloads.get("observability.json", {})
    if observability:
        correlation = observability.get("correlation", {})
        _require(
            correlation.get("workflow_run_id") == run_id
            and correlation.get("run_nonce") == nonce,
            "CROSS_RUN_ARTIFACT:observability.json",
        )

    return {
        "schema_version": "orgrebase.evidence-verification.v1",
        "status": "PASS",
        "manifest": str(manifest_path),
        "workflow_run_id": run_id,
        "verified_artifacts": sorted(verified),
        "artifact_count": len(verified),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        report = verify(args.manifest)
    except ManifestError as exc:
        report = {
            "schema_version": "orgrebase.evidence-verification.v1",
            "status": "FAIL",
            "error": str(exc),
        }
        exit_code = 2
    else:
        exit_code = 0
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
