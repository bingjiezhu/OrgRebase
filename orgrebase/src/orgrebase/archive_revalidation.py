"""Current-code checks of separately identified historical mechanism archives."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from orgrebase.digest import sha256_digest

BASE = "evidence/archive-revalidation/current"
LANES = ("bpi", "formation", "skill", "owb")
MODES = {"bpi": "INDEPENDENT_RECOMPUTATION", "formation": "NEW_DETERMINISTIC_TASKFLOW",
         "skill": "EXACT_PREDECESSOR_REPLAY", "owb": "REFERENCE_SUT_REEXECUTION"}
SUMMARY_FILES = {"bpi": "bpi/receipt.json", "formation": "formation/probe-receipt.json",
                 "skill": "skill/summary.json"}


def _file(root: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != name:
        raise ValueError("ARCHIVE_REVALIDATION_PATH_INVALID")
    path = root / name
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("ARCHIVE_REVALIDATION_FILE_UNAVAILABLE")
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(root: Path, name: str) -> dict[str, Any]:
    value = json.loads(_file(root, name).read_text())
    if not isinstance(value, dict):
        raise ValueError("ARCHIVE_REVALIDATION_OBJECT_REQUIRED")
    return value


def _summary(bundle: Path, lane: str) -> tuple[dict[str, Any], str | None]:
    report = _load(bundle, lane + "/verification.json")
    if report.get("status") != "PASS" or report.get("failures", []):
        raise ValueError("ARCHIVE_REVALIDATION_CHECK_FAILED")
    if lane == "bpi":
        receipt = _load(bundle, SUMMARY_FILES[lane])
        if report.get("receipt_digest") != receipt.get("digest") or report.get("queries_replayed") != 128:
            raise ValueError("ARCHIVE_REVALIDATION_BPI_BINDING")
        return {"queries": report["queries_replayed"], "strategies": report["strategies_replayed"]}, None
    if lane == "formation":
        receipt = _load(bundle, SUMMARY_FILES[lane])
        if (report.get("verified_receipt_digest") != receipt.get("digest")
                or report.get("planned_domain_ids") != report.get("actual_domain_ids")
                or report.get("canonical_target_writes") != 0 or report.get("candidate_only") is not True):
            raise ValueError("ARCHIVE_REVALIDATION_FORMATION_BINDING")
        return {"domains": report["actual_domain_ids"], "task_bindings": report["task_binding_count"],
                "agentteams_actions": report["agentteams_actions"], "canonical_target_writes": 0}, report["run_id"]
    if lane == "skill":
        receipt = _load(bundle, SUMMARY_FILES[lane])
        if receipt.get("status") != "PASS" or receipt.get("target_writes") != 0 or report.get("failure_count") != 0:
            raise ValueError("ARCHIVE_REVALIDATION_SKILL_BINDING")
        return {"negative_probes": receipt["negative_probe_count"], "restoration_status": receipt["restoration_status"],
                "canonical_target_writes": 0, "process_restart_proven": False}, receipt["run_id"]
    return {"profiles": report["profiles"], "cases_per_profile": report["cases_per_profile"],
            "reference_score": report["reference_score"], "failed_strategy_profiles": report["failed_strategy_profiles"]}, None


def archive_revalidation_view(project_root: Path) -> dict[str, Any]:
    base = {"schema_version": "orgrebase.archive-revalidation-view.v1", "status": "UNAVAILABLE",
            "items": [], "current_business_run": False, "live_model_calls": 0}
    bundle = project_root / BASE
    try:
        manifest = _load(bundle, "manifest.json")
        if (manifest.get("schema_version") != "orgrebase.archive-revalidation.v1"
                or manifest.get("digest") != sha256_digest({k: v for k, v in manifest.items() if k != "digest"})
                or set(manifest.get("lanes", {})) != set(LANES)
                or not manifest.get("implementation_files") or manifest.get("live_model_calls") != 0):
            return base
        implementation_current = all(_sha(_file(project_root, name)) == digest
                                     for name, digest in manifest["implementation_files"].items())
        items = []
        for lane in LANES:
            definition = manifest["lanes"][lane]
            item = {"id": lane, "status": "UNAVAILABLE", "mode": MODES[lane], "summary": {},
                    "original_archive": definition["original_archive"], "execution_run_id": None,
                    "artifacts": [], "reason_codes": []}
            try:
                if not implementation_current:
                    item.update(status="STALE", reason_codes=["IMPLEMENTATION_CHANGED_AFTER_RECHECK"])
                elif not definition.get("files") or not definition.get("inputs"):
                    raise ValueError("ARCHIVE_REVALIDATION_CLOSURE_EMPTY")
                elif any(_sha(_file(project_root, name)) != digest for name, digest in definition["inputs"].items()):
                    item.update(status="STALE", reason_codes=["INPUT_CHANGED_AFTER_RECHECK"])
                else:
                    for name, digest in definition["files"].items():
                        if not name.startswith(lane + "/") or _sha(_file(bundle, name)) != digest:
                            raise ValueError("ARCHIVE_REVALIDATION_OUTPUT_CHANGED")
                    if lane + "/verification.json" not in definition["files"]:
                        raise ValueError("ARCHIVE_REVALIDATION_VERIFIER_MISSING")
                    if lane in SUMMARY_FILES and SUMMARY_FILES[lane] not in definition["files"]:
                        raise ValueError("ARCHIVE_REVALIDATION_SUMMARY_BINDING_MISSING")
                    summary, run_id = _summary(bundle, lane)
                    item.update(status="PASS", summary=summary, execution_run_id=run_id,
                                artifacts=[{"path": BASE + "/" + name, "sha256": digest}
                                           for name, digest in definition["files"].items() if name.endswith(("verification.json", "summary.json", "evaluation-suite.json"))])
            except (OSError, ValueError, KeyError, TypeError):
                item.update(status="INVALID", summary={}, reason_codes=["ARCHIVE_REVALIDATION_BINDING_INVALID"])
            items.append(item)
        return {**base, "status": "PASS" if all(item["status"] == "PASS" for item in items) else "PARTIAL",
                "checked_at": manifest["checked_at"], "items": items, "manifest_digest": manifest["digest"]}
    except (OSError, ValueError, KeyError, TypeError):
        return base
