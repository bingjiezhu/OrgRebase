"""Machine-verifiable review-readiness checks for the Workspace release.

Each of the five submission-facing criteria must bind to existing documents,
code, tests, evidence, and executable commands. The checker also verifies the
current evidence values and external-boundary labels.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from orgrebase.digest import sha256_digest

_REQUIRED_CRITERIA = {
    "real_user_and_application_scenario",
    "agent_complete_task_closure",
    "core_function_verifiable_materials",
    "model_agent_architecture_tool_interfaces",
    "data_authorization_and_privacy_risk",
}


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def _nested(value: dict[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            raise KeyError(".".join(keys))
        current = current[key]
    return current


def _record(
    checks: list[dict[str, Any]],
    *,
    check_id: str,
    passed: bool,
    detail: str,
) -> None:
    checks.append({"id": check_id, "status": "PASS" if passed else "FAIL", "detail": detail})


def _path_checks(root: Path, manifest: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    criteria = manifest.get("criteria")
    if not isinstance(criteria, list):
        _record(checks, check_id="manifest.criteria", passed=False, detail="criteria must be a list")
        return
    seen: set[str] = set()
    for item in criteria:
        if not isinstance(item, dict):
            _record(
                checks,
                check_id="manifest.criterion_object",
                passed=False,
                detail="criterion must be an object",
            )
            continue
        criterion_id = str(item.get("id", ""))
        if not criterion_id or criterion_id in seen:
            _record(
                checks,
                check_id=f"criterion.{criterion_id or 'missing'}.identity",
                passed=False,
                detail="criterion ID is missing or duplicated",
            )
            continue
        seen.add(criterion_id)
        for field in ("documents", "code_paths", "tests", "evidence_paths", "commands"):
            values = item.get(field)
            if not isinstance(values, list) or not values:
                _record(
                    checks,
                    check_id=f"criterion.{criterion_id}.{field}",
                    passed=False,
                    detail=f"{field} must be a non-empty list",
                )
                continue
            if field == "commands":
                passed = all(isinstance(value, str) and value.strip() for value in values)
                _record(
                    checks,
                    check_id=f"criterion.{criterion_id}.commands",
                    passed=passed,
                    detail=f"{len(values)} executable command references",
                )
                continue
            missing = [str(value) for value in values if not (root / str(value)).is_file()]
            _record(
                checks,
                check_id=f"criterion.{criterion_id}.{field}",
                passed=not missing,
                detail=(f"{len(values)} paths present" if not missing else "missing: " + ", ".join(missing)),
            )
        boundary = item.get("claim_boundary")
        _record(
            checks,
            check_id=f"criterion.{criterion_id}.claim_boundary",
            passed=isinstance(boundary, str) and len(boundary.strip()) >= 40,
            detail="explicit claim boundary is present",
        )
    _record(
        checks,
        check_id="manifest.required_criteria",
        passed=seen == _REQUIRED_CRITERIA,
        detail=f"found={sorted(seen)}",
    )


def _evidence_checks(root: Path, checks: list[dict[str, Any]]) -> None:
    demo = _load_json(root / "evidence/workspace/latest/workspace-demo.json")
    quote = _nested(demo, "final_quote")
    payload = _nested(quote, "payload")
    _record(
        checks,
        check_id="evidence.workspace_loop",
        passed=(
            demo.get("primary_evaluation_status") == "PASS"
            and quote.get("version") == "v3"
            and payload.get("launch_date") == "2026-09-15"
            and payload.get("currency") == "EUR"
            and demo.get("skill_status") == "CANARY"
        ),
        detail="Quote v3, launch date, currency, benchmark, and Skill state",
    )

    index = _load_json(root / "evidence/workspace/latest/evidence-index.json")
    entries = index.get("entries")
    _record(
        checks,
        check_id="evidence.index",
        passed=index.get("status") == "PASS" and isinstance(entries, list) and len(entries) >= 39,
        detail=(
            f"status={index.get('status')} "
            f"entries={len(entries) if isinstance(entries, list) else 'invalid'}"
        ),
    )

    agentteams = _load_json(root / "evidence/workspace/latest/agentteams-check.json")
    live = agentteams.get("live", {})
    static = agentteams.get("static", {})
    live_status = live.get("status") if isinstance(live, dict) else None
    live_class = live.get("evidence_class") if isinstance(live, dict) else None
    honest_live = (
        (live_status == "NOT_RUN" and live_class == "NOT_RUN")
        or (live_status == "PASS" and live_class == "LIVE_AGENTTEAMS")
    )
    _record(
        checks,
        check_id="evidence.agentteams_boundary",
        passed=(
            isinstance(static, dict)
            and static.get("status") == "PASS"
            and honest_live
            and agentteams.get("target_writes") == 0
        ),
        detail=(
            f"static={static.get('status') if isinstance(static, dict) else None} "
            f"live={live_status}/{live_class}"
        ),
    )

    user = _load_json(root / "evidence/workspace/latest/user-validation.json")
    participant_count = user.get("participant_count")
    user_status = user.get("status")
    user_class = user.get("evidence_class")
    honest_user = (
        (participant_count == 0 and user_status == "NOT_RUN" and user_class == "NOT_RUN")
        or (
            isinstance(participant_count, int)
            and participant_count > 0
            and user_status in {"PASS", "FAIL"}
            and user_class == "USER_STUDY"
        )
    )
    _record(
        checks,
        check_id="evidence.user_validation_boundary",
        passed=honest_user,
        detail=f"participants={participant_count} status={user_status}/{user_class}",
    )

    dataset = _load_json(root / "benchmark/orgworkbench/dataset-manifest.json")
    _record(
        checks,
        check_id="data.synthetic_benchmark",
        passed=(
            dataset.get("case_count") == 192
            and dataset.get("organization_count") == 12
            and dataset.get("pii") == "NONE_SYNTHETIC"
            and dataset.get("license") == "PolyForm-Noncommercial-1.0.0"
        ),
        detail=(
            f"cases={dataset.get('case_count')} orgs={dataset.get('organization_count')} "
            f"pii={dataset.get('pii')} license={dataset.get('license')}"
        ),
    )

    licenses = _load_json(root / "benchmark/orgworkbench/license-manifest.json")
    assets = licenses.get("assets")
    canonical = None
    if isinstance(assets, list):
        canonical = next(
            (
                item
                for item in assets
                if isinstance(item, dict) and item.get("usage") == "CANONICAL_BENCHMARK"
            ),
            None,
        )
    optional_safe = bool(
        isinstance(assets, list)
        and all(
            not isinstance(item, dict)
            or item.get("usage") == "CANONICAL_BENCHMARK"
            or (
                item.get("redistribution")
                in {"ALLOWED", "SEPARATE_ATTRIBUTED_PACK_OR_LINK_ONLY"}
                and item.get("release_bundle") is False
                and item.get("version_or_commit") == "PIN_AT_IMPLEMENTATION"
                and item.get("content_checksum_policy") == "REQUIRED_AFTER_VERSION_PIN"
                and isinstance(item.get("source_url"), str)
                and item.get("source_url", "").startswith("https://")
                and isinstance(item.get("license_source_url"), str)
                and item.get("license_source_url", "").startswith("https://")
                and item.get("license_verified_at") == "2026-08-16"
            )
            for item in assets
        )
    )
    _record(
        checks,
        check_id="data.license_manifest",
        passed=(
            isinstance(canonical, dict)
            and canonical.get("license_spdx") == "PolyForm-Noncommercial-1.0.0"
            and canonical.get("pii_class") == "SYNTHETIC"
            and canonical.get("redistribution") == "ALLOWED"
            and optional_safe
        ),
        detail=f"assets={len(assets) if isinstance(assets, list) else 'invalid'}",
    )


def _documentation_checks(root: Path, checks: list[dict[str, Any]]) -> None:
    required_phrases = {
        "docs/USER-AND-APPLICATION-SCENARIO.md": (
            "Primary users and stakeholders",
            "Real-user validation protocol",
            "No production connector",
        ),
        "docs/AGENT-TASK-CLOSURE.md": (
            "End-to-end step matrix",
            "Failure and exception branches",
            "NOT_RUN",
        ),
        "docs/MODEL-AGENT-TOOL-INTERFACES.md": (
            "Runtime profiles",
            "Structured model contract",
            "Execution reference monitor",
        ),
        "docs/VERIFICATION-EVIDENCE-MAP.md": (
            "Workspace claims",
            "One-command verification",
            "Known limits",
        ),
        "docs/WORKSPACE-DATA-AND-PRIVACY.md": (
            "Canonical data inventory",
            "Privacy and data risk register",
            "Explicit non-claims",
        ),
    }
    for relative, phrases in required_phrases.items():
        path = root / relative
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        missing = [phrase for phrase in phrases if phrase not in text]
        _record(
            checks,
            check_id=f"docs.{path.stem.lower()}",
            passed=not missing,
            detail=("required sections present" if not missing else "missing phrases: " + ", ".join(missing)),
        )


def audit_release_tree(root: Path) -> dict[str, str]:
    """Check release-tree metadata without generating or changing evidence."""
    junk = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.name == ".DS_Store" or "__MACOSX" in path.parts
    )
    return {
        "id": "release.no_macos_metadata",
        "status": "PASS" if not junk else "FAIL",
        "detail": "clean" if not junk else "found: " + ", ".join(junk[:10]),
    }


def audit_repository(
    root: str | Path,
    *,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    path = (
        Path(manifest_path).resolve()
        if manifest_path is not None
        else root_path / "configs/workspace/review-readiness.json"
    )
    checks: list[dict[str, Any]] = []
    try:
        manifest = _load_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        manifest = {}
        _record(checks, check_id="manifest.load", passed=False, detail=str(exc))
    else:
        _record(
            checks,
            check_id="manifest.schema_version",
            passed=manifest.get("schema_version") == "orgrebase.review-readiness.v1",
            detail=str(manifest.get("schema_version")),
        )
        _path_checks(root_path, manifest, checks)
    try:
        _evidence_checks(root_path, checks)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        _record(checks, check_id="evidence.load", passed=False, detail=str(exc))
    _documentation_checks(root_path, checks)
    checks.append(audit_release_tree(root_path))
    failures = tuple(item for item in checks if item["status"] != "PASS")
    criteria = {
        str(item.get("id")): str(item.get("status"))
        for item in manifest.get("criteria", [])
        if isinstance(item, dict) and item.get("id")
    }
    report_payload = {
        "schema_version": "orgrebase.review-readiness-report.v1",
        "status": "PASS" if not failures else "FAIL",
        "criteria": criteria,
        "checks": checks,
        "failure_count": len(failures),
        "external_boundaries": manifest.get("current_external_boundaries", {}),
        "generated_at": "2026-08-16T00:00:00Z",
    }
    return {**report_payload, "digest": sha256_digest(report_payload)}
