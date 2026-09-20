"""FastAPI surface and zero-build-dependency Change Console."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import AsyncIterator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from orgrebase import __version__
from orgrebase.agentteams_source import default_agentteams_checkout
from orgrebase.auth import (
    CONTROLLED_LOCAL_SESSION_IDENTITY,
    PRINCIPAL_IDENTITY_MODES,
    AuthenticationError,
    JWTAuthenticator,
    authorize,
    request_action,
    request_authorization,
    request_principal,
)
from orgrebase.browser_auth import SESSION_COOKIE, BrowserSessions
from orgrebase.browser_auth_http import require_browser_origin, require_csrf, session_router
from orgrebase.browser_session_store import BrowserSessionStore
from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, FreshnessError, IntegrityError
from orgrebase.http_errors import public_http_error as _workspace_error
from orgrebase.http_errors import request_incident_scope
from orgrebase.local_role_session import LOCAL_SESSION_COOKIE, LOCAL_SESSION_ISSUER, LocalRoleSessions
from orgrebase.resource_paths import runtime_asset_path
from orgrebase.runtime_config import (
    DeploymentSettings,
    configure_workspace_identity,
    open_workspace,
    validate_deployment_environment,
    validate_workspace,
)
from orgrebase.service import OrgRebaseService
from orgrebase.wire import WireJSONResponse
from orgrebase.workspace.completed_run_observability import (
    build_completed_run_observability_from_trusted_state,
)
from orgrebase.workspace.history_codec import business_is_complete
from orgrebase.workspace.native_taskflow import (
    NativeActionReceipt,
    _action_semantic_failures,
    _unwrap_call_tool,
)
from orgrebase.workspace.service import (
    CONTROLLED_LOCAL_HEADER_IDENTITY,
    WorkspaceService,
)
from orgrebase.workspace.skill_revision import SKILL_STEWARD_ACTOR
from orgrebase.workspace_catalog import WorkspaceDispatcher, load_catalog

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGED_CONSOLE_DIR = Path(__file__).resolve().parent / "static"
CONSOLE_DIR = PACKAGED_CONSOLE_DIR if PACKAGED_CONSOLE_DIR.is_dir() else PROJECT_ROOT / "demo" / "console"
SOURCE_RELEASE_FACTS_PATH = PROJECT_ROOT / "evidence" / "release-facts.json"
PACKAGED_RELEASE_FACTS_PATH = Path(__file__).resolve().parent / "_assets/evidence/release-facts.json"
RELEASE_FACTS_PATH = (
    SOURCE_RELEASE_FACTS_PATH if SOURCE_RELEASE_FACTS_PATH.is_file() else PACKAGED_RELEASE_FACTS_PATH
)
GOLDEN_EVIDENCE_ROOT = PROJECT_ROOT / "evidence" / "golden-competition" / "latest" / "pilot"
OPERATING_MODEL_ASSET = Path(
    "benchmark/quote-value-v0.1/public/current-process-baseline.json"
)
OPERATING_MODEL_STEP_IDS = (
    "quote-step:intake",
    "quote-step:product-confirmation",
    "quote-step:legal-confirmation",
    "quote-step:finance-confirmation",
    "quote-step:composition",
    "quote-step:delivery-acceptance",
    "quote-step:change-impact",
    "quote-step:selective-update",
)


def _active_golden_evidence_root() -> Path:
    """Resolve the one read-only Golden root shared by every API projection."""

    configured = os.environ.get("ORGREBASE_GOLDEN_EVIDENCE_ROOT", "").strip()
    return Path(configured).expanduser().resolve() if configured else GOLDEN_EVIDENCE_ROOT


def _run_oac_bound_shadow_independent_verifier(
    *,
    root: str | Path,
    pack: str | Path,
    frozen_golden: str | Path,
    fencing_reference: str | Path | None,
) -> dict[str, Any]:
    """Run the product-independent verifier as a separate Python process."""

    script = PROJECT_ROOT / "scripts" / "verify_oac_bound_shadow_execution.py"
    if not script.is_file() or fencing_reference is None:
        raise RuntimeError("OAC_SHADOW_INDEPENDENT_VERIFIER_UNAVAILABLE")
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(script),
                "--root",
                str(root),
                "--pack",
                str(pack),
                "--frozen-golden",
                str(frozen_golden),
                "--fencing-reference",
                str(fencing_reference),
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        result = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise RuntimeError("OAC_SHADOW_INDEPENDENT_VERIFIER_FAILED") from exc
    if completed.returncode != 0 or not isinstance(result, dict):
        raise RuntimeError("OAC_SHADOW_INDEPENDENT_VERIFIER_FAILED")
    return result


def _golden_evidence_status(
    root: Path,
    *,
    current_run_id: str,
) -> dict[str, Any]:
    """Project a frozen pack receipt without treating it as business truth."""

    result: dict[str, Any] = {
        "schema_version": "orgrebase.golden-evidence-status.v1",
        "status": "UNAVAILABLE",
        "current_run_id": current_run_id,
        "frozen_run_id": None,
        "same_run": False,
        "entry_count": None,
        "pack_digest": None,
        "verification_mode": None,
        "claim_boundary": ("READ_ONLY_FROZEN_EVIDENCE_STATUS_NOT_CANONICAL_BUSINESS_TRUTH"),
    }
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        verification = json.loads((root / "verification.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return result
    if not isinstance(manifest, dict) or not isinstance(verification, dict):
        return {**result, "status": "FAIL"}

    manifest_body = {key: value for key, value in manifest.items() if key != "digest"}
    files = manifest.get("files")
    frozen_run_id = manifest.get("run_id")
    valid = (
        manifest.get("digest") == sha256_digest(manifest_body)
        and isinstance(files, dict)
        and verification.get("status") == "PASS"
        and verification.get("failures") == []
        and verification.get("run_id") == frozen_run_id
        and verification.get("entry_count") == files.get("entry_count")
        and verification.get("pack_digest") == files.get("pack_digest")
        and verification.get("product_imports") == 0
    )
    same_run = bool(valid and frozen_run_id == current_run_id)
    return {
        **result,
        "status": "PASS" if same_run else ("STALE" if valid else "FAIL"),
        "frozen_run_id": frozen_run_id,
        "same_run": same_run,
        "entry_count": verification.get("entry_count") if valid else None,
        "pack_digest": verification.get("pack_digest") if valid else None,
        "verification_mode": verification.get("verification_mode") if valid else None,
    }


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _read_json(path: Path, expected_type: type) -> Any | None:
    """Read an atomically-published runtime record without weakening failures.

    The Golden runner replaces each file atomically, but this view is also used
    against partially populated or manually copied directories.  A malformed
    record therefore remains unobserved instead of becoming UI progress.
    """

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, expected_type) else None


def _digest_record_valid(record: Mapping[str, Any], *, digest_key: str = "digest") -> bool:
    digest = record.get(digest_key)
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        return False
    body = {key: value for key, value in record.items() if key != digest_key}
    return digest == sha256_digest(body)


def _run_evidence_root(workspace: Any, run_id: str) -> Path | None:
    """Resolve only the current run's private evidence directory.

    No path is returned to the API caller.  The execution envelope is a sealed
    same-run binding, so a newer or similarly named directory cannot be
    mistaken for the active task.
    """

    configured_root = getattr(workspace, "competition_evidence_root", None)
    if configured_root is None:
        return None
    root = Path(configured_root)
    if not root.is_dir():
        return None
    matches: list[Path] = []
    for candidate in root.glob("run-*"):
        if not candidate.is_dir():
            continue
        envelope = _read_json(candidate / "execution-envelope.json", dict)
        if (
            isinstance(envelope, dict)
            and envelope.get("run_id") == run_id
            and _digest_record_valid(envelope)
        ):
            matches.append(candidate)
    if not matches:
        return None
    return max(matches, key=lambda item: item.stat().st_mtime_ns)


def _candidate_output_evidence_root(workspace: Any, run_id: str) -> Path | None:
    """Resolve an exact same-run subprocess-output root for a read-only view."""

    candidates: list[Path] = []
    live_root = _run_evidence_root(workspace, run_id)
    if live_root is not None:
        candidates.append(live_root)
    configured_root = getattr(workspace, "competition_evidence_root", None)
    if configured_root is not None:
        candidates.append(Path(configured_root))
    frozen_root = _active_golden_evidence_root()
    candidates.extend((frozen_root, frozen_root / "golden-run"))
    observed: set[Path] = set()
    for candidate in candidates:
        selected = candidate.resolve()
        if selected in observed:
            continue
        observed.add(selected)
        envelope = _read_json(selected / "execution-envelope.json", dict)
        if (
            isinstance(envelope, dict)
            and envelope.get("run_id") == run_id
            and _digest_record_valid(envelope)
            and (selected / "process-outputs").is_dir()
            and (selected / "process-receipts.json").is_file()
        ):
            return selected
    return None


def _candidate_output_source(candidate: Mapping[str, Any]) -> dict[str, Any] | None:
    sources = candidate.get("source_refs")
    if not isinstance(sources, list) or len(sources) != 1:
        return None
    source = sources[0]
    if not isinstance(source, Mapping):
        return None
    source_id = source.get("source_id")
    source_version = source.get("source_version")
    if not (
        isinstance(source_id, str)
        and source_id
        and isinstance(source_version, str)
        and source_version
        and isinstance(source.get("digest"), str)
        and isinstance(source.get("locator_class"), str)
    ):
        return None
    return {
        "source_ref": f"{source_id}@{source_version}",
        "source_id": source_id,
        "source_version": source_version,
        "source_receipt_digest": source["digest"],
        "locator_class": source["locator_class"],
    }


def _candidate_output_version(candidate_id: str) -> str | None:
    _separator, marker, version = candidate_id.rpartition("@")
    return version if marker and version else None


_SAFE_CANDIDATE_OUTPUT_PREDICATES = {
    ("product", 1): {"product_plan", "launch_date", "data_residency"},
    ("legal", 1): {"notice_required"},
    ("finance", 1): {"currency"},
    ("finance", 2): {"currency", "price_band"},
    ("gtm", 1): {"partner_terms", "quote_compose_skill"},
}


def _priced_candidate_fields(root: Path, task: Mapping[str, Any], candidates: list[Any]) -> set[str] | None:
    """Admit only declared pricing fields from this task's sealed input projection."""
    domain = task.get("authority_domain")
    optional = {"product": "quote_basket", "finance": "pricing_policy"}.get(domain)
    if optional is None or not any(isinstance(c, Mapping) and c.get("predicate") == optional for c in candidates):
        return set()
    task_id = task.get("id")
    if not isinstance(task_id, str) or Path(task_id).name != task_id:
        return None
    payload = _read_json(root / "process-inputs" / f"{task_id}.json", dict)
    if not isinstance(payload, Mapping) or sha256_digest(payload) != task.get("input_digest"):
        return None
    sources = payload.get("source_values")
    source = sources.get(optional) if isinstance(sources, Mapping) else None
    projection = payload.get("projection")
    selected = [c for c in candidates if isinstance(c, Mapping) and c.get("predicate") == optional]
    if not (isinstance(source, Mapping) and isinstance(projection, Mapping) and len(selected) == 1
            and payload.get("task_id") == task_id and payload.get("domain") == domain
            and source.get("domain_id") == domain and source.get("slot_id") == optional
            and isinstance(projection.get("included_refs"), list)
            and source.get("object_ref") in projection["included_refs"]
            and source.get("sensitivity") in {"PUBLIC", "INTERNAL"}):
        return None
    candidate = selected[0]
    refs = candidate.get("source_refs")
    if not (candidate.get("value") == source.get("value")
            and candidate.get("subject_ref") == source.get("object_ref")
            and candidate.get("authority_ref") == source.get("authority_ref")
            and candidate.get("sensitivity") == source.get("sensitivity")
            and candidate.get("value_schema_ref") == f"schema:workspace.{optional}@v1"
            and isinstance(refs, list) and len(refs) == 1 and isinstance(refs[0], Mapping)
            and all(refs[0].get(k) == source.get(k) for k in ("source_id", "source_version", "source_digest"))):
        return None
    return {optional}


def _same_run_candidate_output_view(
    workspace: Any,
    state: Mapping[str, Any],
) -> dict[str, Any]:
    """Project verified worker outputs without relabelling baseline facts.

    Every value is read from the independent subprocess output, then bound to
    its sealed subprocess receipt, native task, Agent run, and result handoff.
    The projection owns no admission, approval, or canonical-write authority.
    """

    execution = state.get("execution")
    run_id = execution.get("run_id") if isinstance(execution, Mapping) else None
    base: dict[str, Any] = {
        "schema_version": "orgrebase.agent-candidate-output-view.v1",
        "status": "UNAVAILABLE",
        "run_id": run_id if isinstance(run_id, str) else None,
        "candidate_only": True,
        "target_writes": 0,
        "claim_boundary": (
            "VERIFIED_SUBPROCESS_CANDIDATE_SUMMARY_NOT_BASELINE_NOT_APPROVAL_NOT_CANONICAL_STATE"
        ),
        "outputs": [],
    }
    evidence = state.get("competition_evidence")
    collaboration = evidence.get("agent_collaboration") if isinstance(evidence, Mapping) else None
    plan = collaboration.get("orchestration_plan") if isinstance(collaboration, Mapping) else None
    tasks = plan.get("tasks") if isinstance(plan, Mapping) else None
    runs = collaboration.get("agent_runs") if isinstance(collaboration, Mapping) else None
    handoffs = collaboration.get("handoffs") if isinstance(collaboration, Mapping) else None
    reviewer_summary = collaboration.get("reviewer") if isinstance(collaboration, Mapping) else None
    if not (
        isinstance(run_id, str)
        and run_id
        and isinstance(evidence, Mapping)
        and evidence.get("status") == "PASS"
        and evidence.get("run_id") == run_id
        and isinstance(tasks, list)
        and isinstance(runs, list)
        and isinstance(handoffs, list)
    ):
        return base
    root = _candidate_output_evidence_root(workspace, run_id)
    if root is None:
        return base

    failures: list[str] = []
    raw_receipts = _read_json(root / "process-receipts.json", list)
    if not isinstance(raw_receipts, list):
        return {**base, "status": "FAIL", "failures": ["PROCESS_RECEIPTS_UNAVAILABLE"]}
    receipts: dict[str, Mapping[str, Any]] = {}
    for receipt in raw_receipts:
        if not (
            isinstance(receipt, Mapping)
            and _digest_record_valid(receipt)
            and receipt.get("run_id") == run_id
            and receipt.get("independent_process") is True
            and receipt.get("canonical_target_writes") == 0
            and receipt.get("exit_code") == 0
            and isinstance(receipt.get("task_id"), str)
            and isinstance(receipt.get("output_digest"), str)
        ):
            failures.append("PROCESS_RECEIPT_BINDING")
            continue
        task_id = str(receipt["task_id"])
        if task_id in receipts:
            failures.append(f"DUPLICATE_PROCESS_RECEIPT:{task_id}")
            continue
        receipts[task_id] = receipt

    task_by_id = {
        str(item.get("id")): item
        for item in tasks
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    candidate_tasks = {
        task_id: task
        for task_id, task in task_by_id.items()
        if task.get("role") in {"DOMAIN_WORKER", "REVIEWER"}
    }
    if set(receipts) != set(candidate_tasks):
        failures.append("EXPECTED_PROCESS_RECEIPT_SET")
    run_by_task = {
        str(item.get("task_id")): item
        for item in runs
        if isinstance(item, Mapping) and isinstance(item.get("task_id"), str)
    }
    handoff_by_task = {
        str(item.get("task_id")): item
        for item in handoffs
        if isinstance(item, Mapping) and isinstance(item.get("task_id"), str)
    }
    outputs_by_task: dict[str, Mapping[str, Any]] = {}
    for path in sorted((root / "process-outputs").glob("*.json")):
        output = _read_json(path, dict)
        if not isinstance(output, Mapping) or not isinstance(output.get("task_id"), str):
            failures.append("PROCESS_OUTPUT_LOAD")
            continue
        task_id = str(output["task_id"])
        receipt = receipts.get(task_id)
        task = candidate_tasks.get(task_id)
        run = run_by_task.get(task_id)
        handoff = handoff_by_task.get(task_id)
        handoff_payload = handoff.get("payload") if isinstance(handoff, Mapping) else None
        output_digest = sha256_digest(dict(output))
        role = str(task.get("role")) if task else ""
        expected_mode = "reviewer" if role == "REVIEWER" else "worker"
        expected_domain = None if role == "REVIEWER" else task.get("authority_domain") if task else None
        if not (
            receipt
            and task
            and isinstance(run, Mapping)
            and isinstance(handoff_payload, Mapping)
            and output.get("run_id") == run_id
            and output.get("candidate_only") is True
            and output.get("target_writes") == 0
            and output.get("input_digest") == task.get("input_digest") == run.get("input_digest")
            and receipt.get("input_digest") == output.get("input_digest")
            and receipt.get("mode") == expected_mode
            and receipt.get("domain") == expected_domain
            and receipt.get("attempt") == task.get("attempt")
            and run.get("agent_name") == task.get("agent_name")
            and handoff.get("from_agent") == task.get("agent_name")
            and (
                (output.get("status") == "ABSTAIN" and run.get("status") == "ABSTAIN")
                or (output.get("status") == "PASS" and run.get("status") == "TRUSTED_COMPLETE")
            )
            and receipt.get("output_digest")
            == output_digest
            == task.get("output_digest")
            == run.get("output_digest")
            == handoff_payload.get("output_digest")
            and handoff_payload.get("target_writes") == 0
        ):
            failures.append(f"OUTPUT_TASK_HANDOFF_BINDING:{task_id}")
            continue
        outputs_by_task[task_id] = output

    if set(outputs_by_task) != set(candidate_tasks):
        failures.append("EXPECTED_NATIVE_TASK_OUTPUT_SET")

    projected: list[dict[str, Any]] = []
    for task_id, task in candidate_tasks.items():
        output = outputs_by_task.get(task_id)
        receipt = receipts.get(task_id)
        if output is None or receipt is None:
            continue
        role = str(task.get("role"))
        actor_id = task.get("agent_name")
        executor_ref = output.get("worker_id") if role == "DOMAIN_WORKER" else output.get("reviewer_id")
        if not (
            isinstance(actor_id, str)
            and actor_id
            and isinstance(executor_ref, str)
            and executor_ref
            and (role == "REVIEWER" or executor_ref == actor_id)
        ):
            failures.append(f"OUTPUT_ACTOR_BINDING:{task_id}")
            continue
        item: dict[str, Any] = {
            "task_id": task_id,
            "actor_id": actor_id,
            "executor_ref": executor_ref,
            "role": role,
            "domain": task.get("authority_domain"),
            "attempt": task.get("attempt"),
            "status": output.get("status"),
            "candidate_only": True,
            "target_writes": 0,
            "input_digest": output.get("input_digest"),
            "output_digest": receipt.get("output_digest"),
            "process_receipt_digest": receipt.get("digest"),
            "reason_codes": output.get("reason_codes", []),
            "missing_fields": output.get("missing_fields", []),
            "candidate_outputs": [],
        }
        if role == "REVIEWER":
            decision = output.get("decision")
            expected_decision = (
                reviewer_summary.get(f"attempt_{task.get('attempt')}")
                if isinstance(reviewer_summary, Mapping)
                else None
            )
            if not (
                isinstance(decision, Mapping)
                and decision.get("verdict") in {"REPLAN", "PASS"}
                and isinstance(decision.get("reason_codes"), list)
                and isinstance(decision.get("missing_domains"), list)
                and isinstance(expected_decision, Mapping)
                and dict(decision) == dict(expected_decision)
            ):
                failures.append(f"REVIEWER_DECISION:{task_id}")
                continue
            item["decision"] = {
                "verdict": decision["verdict"],
                "reason_codes": list(decision["reason_codes"]),
                "missing_domains": list(decision["missing_domains"]),
            }
        else:
            bundle = output.get("candidate_bundle")
            candidates = output.get("claim_candidates")
            if not (
                isinstance(bundle, Mapping)
                and _digest_record_valid(bundle)
                and bundle.get("candidate_only") is True
                and bundle.get("delegation_task_ref") == task_id
                and bundle.get("worker_id") == actor_id
                and bundle.get("domain_id") == task.get("authority_domain")
                and isinstance(candidates, list)
            ):
                failures.append(f"CANDIDATE_BUNDLE:{task_id}")
                continue
            candidate_outputs: list[dict[str, Any]] = []
            for candidate in candidates:
                if not isinstance(candidate, Mapping) or not _digest_record_valid(candidate):
                    failures.append(f"CLAIM_CANDIDATE:{task_id}")
                    continue
                source = _candidate_output_source(candidate)
                candidate_id = candidate.get("candidate_id")
                if not (
                    source
                    and isinstance(candidate_id, str)
                    and isinstance(candidate.get("predicate"), str)
                    and isinstance(candidate.get("sensitivity"), str)
                    and candidate.get("issuer_domain_id") == task.get("authority_domain")
                ):
                    failures.append(f"CLAIM_CANDIDATE_FIELDS:{task_id}")
                    continue
                candidate_outputs.append(
                    {
                        "candidate_id": candidate_id,
                        "version": _candidate_output_version(candidate_id),
                        "predicate": candidate["predicate"],
                        "value": candidate.get("value"),
                        "sensitivity": candidate["sensitivity"],
                        "semantic_kind": candidate.get("semantic_kind"),
                        "subject_ref": candidate.get("subject_ref"),
                        "authority_ref": candidate.get("authority_ref"),
                        "governance_state": candidate.get("governance_state"),
                        "transformation_ref": candidate.get("transformation_ref"),
                        "candidate_digest": candidate.get("digest"),
                        **source,
                    }
                )
            candidate_digests = [item["candidate_digest"] for item in candidate_outputs]
            if candidate_digests != bundle.get("candidate_refs"):
                failures.append(f"CANDIDATE_REFERENCE_SET:{task_id}")
                continue
            exact_predicates = _SAFE_CANDIDATE_OUTPUT_PREDICATES.get(
                (str(task.get("authority_domain")), task.get("attempt"))
            )
            priced_fields = _priced_candidate_fields(root, task, candidates)
            if priced_fields is None:
                failures.append(f"PRICING_CANDIDATE_INPUT_BINDING:{task_id}")
                continue
            if exact_predicates is not None:
                exact_predicates = exact_predicates | priced_fields
            observed_predicates = [item["predicate"] for item in candidate_outputs]
            if not (
                exact_predicates
                and len(observed_predicates) == len(exact_predicates)
                and set(observed_predicates) == exact_predicates
            ):
                failures.append(f"CANDIDATE_SAFE_FIELD_SET:{task_id}")
                continue
            if task.get("authority_domain") == "legal" and not all(
                item.get("transformation_ref") == "transform:legal-minimal-disclosure@v1"
                for item in candidate_outputs
            ):
                failures.append(f"LEGAL_MINIMAL_DERIVATION:{task_id}")
                continue
            if task.get("authority_domain") == "gtm" and not any(
                item.get("predicate") == "quote_compose_skill" and item.get("semantic_kind") == "SKILL"
                for item in candidate_outputs
            ):
                failures.append(f"GTM_CAPABILITY_REQUIREMENT:{task_id}")
                continue
            item["candidate_bundle_digest"] = bundle.get("digest")
            item["candidate_set_digest"] = bundle.get("candidate_set_digest")
            item["candidate_outputs"] = candidate_outputs
        projected.append(item)

    if failures or len(projected) != len(candidate_tasks):
        return {
            **base,
            "status": "FAIL",
            "failures": sorted(set(failures or ["CANDIDATE_OUTPUT_PROJECTION_INCOMPLETE"])),
        }
    return {
        **base,
        "status": "PASS",
        "evidence_class": "VERIFIED_SAME_RUN_CONTROLLED_LOCAL_SUBPROCESS_OUTPUT",
        "outputs": projected,
    }


def _valid_progress_actions(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    actions: list[dict[str, Any]] = []
    for sequence, item in enumerate(raw, start=1):
        if (
            not isinstance(item, dict)
            or type(item.get("sequence")) is not int
            or item.get("sequence") != sequence
            or item.get("ok") is not True
            or not _digest_record_valid(item)
        ):
            return []
        actions.append(item)
    return actions


def _valid_partial_actions(root: Path | None) -> list[dict[str, Any]]:
    return _valid_progress_actions(
        _read_json(root / "agentteams" / "action-journal.json", list) if root else None
    )


def _stored_actions(state: Mapping[str, Any] | None, run_id: str) -> list[dict[str, Any]]:
    evidence = state.get("competition_evidence") if isinstance(state, Mapping) else None
    if not isinstance(evidence, Mapping) or evidence.get("run_id") != run_id:
        return []
    collaboration = evidence.get("agent_collaboration")
    raw = collaboration.get("actions") if isinstance(collaboration, Mapping) else None
    return _valid_progress_actions(raw)


def _progress_action_payloads(
    root: Path, action: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    """Bind a journal observation to its existing native request and response."""
    try:
        NativeActionReceipt.model_validate(action)
        base = (root / "agentteams").resolve()
        path = (base / action["raw_ref"]).resolve()
        if not base.is_relative_to(root.resolve()) or not path.is_relative_to(base):
            return None
        raw = _read_json(path, dict)
        if not isinstance(raw, dict):
            return None
        request, response = raw.get("request"), raw.get("response")
        if (
            not isinstance(request, dict) or not isinstance(response, dict)
            or raw.get("sequence") != action.get("sequence")
            or raw.get("key") != action.get("key")
            or sha256_digest(request) != action.get("request_digest")
            or sha256_digest(response) != action.get("response_digest")
        ):
            return None
        payload = _unwrap_call_tool(response)
        if _action_semantic_failures(action, raw, payload):
            return None
        parameters = request.get("payload")
        return (parameters, payload) if isinstance(parameters, Mapping) else None
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, AttributeError):
        return None


def _reviewer_progress_accept(
    actions: list[dict[str, Any]], root: Path | None,
    state: Mapping[str, Any] | None, run_id: str,
) -> dict[str, Any] | None:
    """Use explicit same-run task roles; task names carry no reviewer authority."""
    evidence = state.get("competition_evidence") if isinstance(state, Mapping) else None
    collaboration = evidence.get("agent_collaboration") if isinstance(evidence, Mapping) else None
    plan = collaboration.get("orchestration_plan") if isinstance(collaboration, Mapping) else None
    if (
        isinstance(evidence, Mapping) and evidence.get("run_id") == run_id
        and isinstance(plan, Mapping) and plan.get("run_id") == run_id
        and _digest_record_valid(plan) and isinstance(plan.get("tasks"), list)
    ):
        reviewer_keys = {
            f"{task['id']}:accept" for task in plan["tasks"]
            if isinstance(task, Mapping) and isinstance(task.get("id"), str)
            and task.get("role") == "REVIEWER" and _digest_record_valid(task)
        }
        return next((item for item in reversed(actions)
                     if item.get("action") == "accept_task_result"
                     and item.get("tool") == "projectflow"
                     and item.get("status") == "completed"
                     and isinstance(item.get("key"), str)
                     and item.get("key") in reviewer_keys), None)
    if root is None:
        return None
    reviewers: dict[str, str] = {}
    accepted = None
    for action in actions:
        if action.get("action") not in ("delegate_task", "accept_task_result"):
            continue
        verified = _progress_action_payloads(root, action)
        if verified is None:
            continue
        parameters, payload = verified
        task_id, project_id = parameters.get("taskId"), parameters.get("projectId")
        if not isinstance(task_id, str) or not isinstance(project_id, str):
            continue
        if action["action"] == "delegate_task":
            try:
                spec = json.loads(parameters.get("spec", ""))
            except (TypeError, ValueError):
                continue
            task = payload.get("task")
            if (
                isinstance(spec, Mapping) and isinstance(task, Mapping)
                and action.get("tool") == "taskflow"
                and spec.get("schema_version") == "orgrebase.golden-agentteams-task-spec.v1"
                and spec.get("run_id") == run_id and spec.get("role") == "REVIEWER"
                and spec.get("task_id") == task_id and spec.get("project_id") == project_id
                and spec.get("assignee") == parameters.get("assignedTo")
                and spec.get("candidate_only") is True and spec.get("target_writes") == 0
                and task.get("task_id") == task_id and task.get("project_id") == project_id
                and action.get("key") == f"{task_id}:delegate"
            ):
                reviewers[task_id] = project_id
        elif (
            reviewers.get(task_id) == project_id
            and action.get("tool") == "projectflow"
            and action.get("key") == f"{task_id}:accept"
            and parameters.get("accepted") is True and parameters.get("resultStatus") == "SUCCESS"
            and payload.get("taskId") == task_id
            and isinstance(payload.get("project"), Mapping)
            and payload["project"].get("project_id") == project_id
        ):
            accepted = action
    return accepted


def _progress_identifier(value: Any) -> str | None:
    """Expose bounded operation identifiers, never free text or file paths."""
    return (
        value
        if isinstance(value, str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}", value)
        else None
    )


def _reviewer_progress_usage(state: Mapping[str, Any] | None, run_id: str) -> list[dict[str, Any]]:
    """Read the verified state projection; legacy zero defaults are not usage."""
    evidence = state.get("competition_evidence") if isinstance(state, Mapping) else None
    if not isinstance(evidence, Mapping) or evidence.get("run_id") != run_id:
        return []
    collaboration = evidence.get("agent_collaboration")
    if not isinstance(collaboration, Mapping):
        return []
    reviewer = collaboration.get("reviewer")
    attempts = reviewer.get("model_attempts") if isinstance(reviewer, Mapping) else None
    plan = collaboration.get("orchestration_plan")
    tasks = plan.get("tasks") if isinstance(plan, Mapping) else None
    if not isinstance(attempts, list) or not isinstance(tasks, list):
        return []
    reviewer_tasks = {
        (task.get("id"), task.get("attempt"))
        for task in tasks
        if isinstance(task, Mapping)
        and isinstance(task.get("id"), str)
        and type(task.get("attempt")) is int
        and task.get("role") == "REVIEWER"
    }
    rows = []
    for attempt in attempts:
        if (
            not isinstance(attempt, Mapping)
            or attempt.get("run_id") != run_id
            or not isinstance(attempt.get("task_id"), str)
            or type(attempt.get("phase")) is not int
            or (attempt.get("task_id"), attempt.get("phase")) not in reviewer_tasks
            or not _digest_record_valid(attempt)
        ):
            continue
        durable = (
            attempt.get("schema_version") == "orgrebase.golden-model-attempt-evidence.v2"
            and attempt.get("observation_persistence") == "DURABLE"
            and re.fullmatch(r"sha256:[0-9a-f]{64}", str(attempt.get("model_attempt_observation_digest")))
        )
        usage = attempt.get("usage") if durable else None
        reported = (
            isinstance(usage, Mapping)
            and usage.get("basis") == "provider_response"
            and usage.get("status") in ("REPORTED", "PARTIAL")
        )
        tokens = {}
        for field in ("input_tokens", "output_tokens"):
            value = usage.get(field) if reported else None
            tokens[field] = value if type(value) is int and value >= 0 else None
        latency = attempt.get("latency_ms") if durable else None
        latency = latency if type(latency) is int and latency >= 0 else None
        rows.append({
            "task_id": _progress_identifier(attempt.get("task_id")),
            "phase": attempt["phase"],
            "provider": _progress_identifier(attempt.get("provider")),
            "model_id": _progress_identifier(attempt.get("requested_model_id")),
            **tokens,
            "latency_ms": latency,
            "usage_source": "provider_response" if any(value is not None for value in tokens.values()) else "unavailable",
            "latency_source": "client_receipt" if latency is not None else "unavailable",
        })
    return rows


def _same_run_receipt(
    path: Path | None,
    *,
    run_id: str,
    nested_key: str | None = None,
    success_field: str,
    success_value: str,
) -> dict[str, Any] | None:
    if path is None:
        return None
    raw = _read_json(path, dict)
    if not isinstance(raw, dict):
        return None
    selected = raw.get(nested_key) if nested_key else raw
    if (
        not isinstance(selected, dict)
        or selected.get("run_id") != run_id
        or selected.get(success_field) != success_value
        or selected.get("target_writes") != 0
        or not _digest_record_valid(selected)
    ):
        return None
    return selected


def _change_approval_count(state: Mapping[str, Any] | None) -> int:
    changes = state.get("changes") if isinstance(state, Mapping) else None
    if not isinstance(changes, Mapping):
        return 0
    count = 0
    kinds = tuple(changes) if (state or {}).get("schema_version") == "orgrebase.workspace-state.v2" else ("launch_date", "currency")
    for kind in kinds:
        change = changes.get(kind)
        approval = change.get("approval") if isinstance(change, Mapping) else None
        if not isinstance(approval, Mapping):
            continue
        status = str(
            approval.get("status")
            or approval.get("decision")
            or (approval.get("approval") or {}).get("decision")
            or ""
        ).upper()
        digest = approval.get("approval_digest") or approval.get("artifact_digest")
        nested_approval = approval.get("approval")
        digest_bound = bool(
            isinstance(digest, str)
            and isinstance(nested_approval, Mapping)
            and nested_approval.get("digest") == digest
        )
        if isinstance(digest, str) and (status in {"APPROVED", "ACCEPTED"} or digest_bound):
            count += 1
    return count


def _milestone(
    milestone_id: str,
    *,
    status: str,
    observed_count: int = 0,
    expected_count: int | None = None,
    evidence_digest: str | None = None,
) -> dict[str, Any]:
    return {
        "id": milestone_id,
        "status": status,
        "observed_count": observed_count,
        "expected_count": expected_count,
        "evidence_digest": evidence_digest,
    }


def _workspace_run_progress_view(
    workspace: Any,
    tracker: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project observed, same-run progress without owning canonical state.

    While Formation is executing, the Workspace service lock is intentionally
    not acquired.  Progress comes only from the runner's atomically persisted
    action journal and receipts.  Once the call returns, the canonical
    Workspace state becomes the authority for approvals and business terminal.
    """

    tracked = dict(tracker or {})
    run_id = str(
        tracked.get("run_id")
        or getattr(workspace, "effective_workflow_run_id", "")
        or ""
    )
    tracker_status = str(tracked.get("status") or "WAITING").upper()
    state: Mapping[str, Any] | None = None
    state_error = None
    if tracker_status != "RUNNING":
        try:
            projected = workspace.state()
            state = projected if isinstance(projected, Mapping) else None
        except (IntegrityError, RuntimeError, ValueError, KeyError, OSError) as exc:
            state_error = type(exc).__name__
    state_run_id = (
        ((state.get("execution") or {}).get("run_id"))
        if isinstance(state, Mapping)
        else None
    )
    if isinstance(state_run_id, str) and state_run_id:
        run_id = state_run_id

    evidence_root = _run_evidence_root(workspace, run_id) if run_id else None
    actions = _valid_partial_actions(evidence_root)
    if not actions and state is not None:
        actions = _stored_actions(state, run_id)
    observed_actions = [str(item.get("action")) for item in actions]
    action_digests = [str(item.get("digest")) for item in actions if item.get("digest")]
    count = lambda action: observed_actions.count(action)  # noqa: E731
    terminal_action = next(
        (item for item in reversed(actions) if item.get("action") == "complete_project"),
        None,
    )
    reviewer_accept = _reviewer_progress_accept(actions, evidence_root, state, run_id)
    handoff_count = sum(count(action) for action in ("submit_task", "check_task", "accept_task_result"))

    tool = _same_run_receipt(
        evidence_root / "tool" / "invocation.json" if evidence_root else None,
        run_id=run_id,
        nested_key="receipt",
        success_field="status",
        success_value="SUCCEEDED",
    )
    skill = _same_run_receipt(
        evidence_root / "skill" / "receipt.json" if evidence_root else None,
        run_id=run_id,
        success_field="outcome",
        success_value="SUCCESS",
    )
    evidence = state.get("competition_evidence") if isinstance(state, Mapping) else None
    if isinstance(evidence, Mapping) and evidence.get("run_id") == run_id:
        collaboration = evidence.get("agent_collaboration")
        if isinstance(collaboration, Mapping):
            stored_tool = collaboration.get("tool")
            stored_skill = collaboration.get("skill")
            if tool is None and isinstance(stored_tool, Mapping) and stored_tool.get("status") == "SUCCEEDED":
                tool = dict(stored_tool)
            if skill is None and isinstance(stored_skill, Mapping) and stored_skill.get("status") == "SUCCESS":
                skill = dict(stored_skill)

    stage = str((state or {}).get("stage") or tracked.get("stage") or "EMPTY")
    intake_observed = bool(
        (state or {}).get("task_intake")
        or tracker_status in {"RUNNING", "ACTIVE", "COMPLETED"}
    )
    approval_count = _change_approval_count(state)
    agentteams_terminal = terminal_action is not None
    business_terminal = business_is_complete(state or {})
    current_contract = (state or {}).get("schema_version") == "orgrebase.workspace-state.v2"
    approval_target = None if current_contract else 2
    approvals_complete = business_terminal if current_contract else approval_count == approval_target
    failed = tracker_status == "FAILED"
    status = (
        "FAILED"
        if failed
        else "RUNNING"
        if tracker_status == "RUNNING"
        else "COMPLETED"
        if business_terminal
        else "ACTIVE"
        if stage != "EMPTY" or agentteams_terminal
        else "WAITING"
    )

    def observed(value: bool) -> str:
        return "OBSERVED" if value else "WAITING"

    milestones = [
        _milestone("TASK_INTAKE", status=observed(intake_observed), observed_count=int(intake_observed), expected_count=1),
        _milestone("PROJECT_CREATED", status=observed(count("create_project") > 0), observed_count=count("create_project"), expected_count=1),
        _milestone("DELEGATED", status="OBSERVED" if count("delegate_task") > 0 else "WAITING", observed_count=count("delegate_task")),
        _milestone("ACKNOWLEDGED", status="OBSERVED" if count("ack_task") > 0 else "WAITING", observed_count=count("ack_task")),
        _milestone("CONTEXT_AND_RESULT_HANDOFF", status="OBSERVED" if handoff_count > 0 else "WAITING", observed_count=handoff_count),
        _milestone("SUPPLEMENTAL_TOOL_EVIDENCE", status=observed(tool is not None), observed_count=int(tool is not None), expected_count=1, evidence_digest=(tool or {}).get("digest") or (tool or {}).get("receipt_digest")),
        _milestone("SKILL_INVOKED", status=observed(skill is not None), observed_count=int(skill is not None), expected_count=1, evidence_digest=(skill or {}).get("digest") or (skill or {}).get("receipt_digest")),
        _milestone("REVIEWER_ACCEPTED", status=observed(reviewer_accept is not None), observed_count=int(reviewer_accept is not None), expected_count=1, evidence_digest=(reviewer_accept or {}).get("digest")),
        _milestone("AGENTTEAMS_TERMINAL", status=observed(agentteams_terminal), observed_count=int(agentteams_terminal), expected_count=1, evidence_digest=(terminal_action or {}).get("digest")),
        _milestone("HUMAN_APPROVALS", status="OBSERVED" if approvals_complete else "PARTIAL" if approval_count else "WAITING", observed_count=approval_count, expected_count=approval_target),
        _milestone("CANONICAL_BUSINESS_TERMINAL", status=observed(business_terminal), observed_count=int(business_terminal), expected_count=1),
    ]
    first_waiting = next((item["id"] for item in milestones if item["status"] != "OBSERVED"), None)
    return {
        "schema_version": "orgrebase.workspace-run-progress-view.v1",
        "status": status,
        "run_id": run_id or None,
        "stage": stage,
        "current_step": "COMPLETED" if business_terminal else first_waiting,
        "started_at": tracked.get("started_at"),
        "updated_at": tracked.get("updated_at"),
        "evidence_class": (
            "LIVE_THIS_RUN_CONTROLLED_LOCAL"
            if tracker_status == "RUNNING"
            else "VERIFIED_SAME_RUN_CONTROLLED_LOCAL"
            if actions
            else "CANONICAL_STATE_ONLY"
        ),
        "canonical_state_observed": tracker_status != "RUNNING" and state is not None,
        "canonical_stage_authority": "ORGREBASE_CONTROL_PLANE",
        "authority_boundary": (
            "AGENTTEAMS_RECEIVED_NE_CONTROL_ADMITTED_NE_HUMAN_APPROVED_NE_CANONICAL_APPLIED"
        ),
        "action_count": len(actions),
        "action_head_digest": action_digests[-1] if action_digests else None,
        "actions": [
            {
                "sequence": item["sequence"],
                **{field: _progress_identifier(item.get(field)) for field in ("tool", "action", "key", "status")},
            }
            for item in actions
        ],
        "reviewer_model_usage": _reviewer_progress_usage(state, run_id),
        "milestones": milestones,
        "failure_code": tracked.get("failure_code") if failed else state_error,
    }


def _workspace_current_run_archive_view(
    workspace: Any,
    *,
    projected_state: Mapping[str, Any] | None = None,
    include_history: bool = True,
) -> dict[str, Any]:
    """Project the current business run's persisted completion record.

    This is not a second execution and never borrows a frozen benchmark run.
    The record opens only when the canonical Workspace state, exact same-run
    approvals, selective-Rebase receipts, final Quote, and scoped event chain
    agree on one terminal ``run_id``.
    """

    if projected_state is None:
        try:
            projected = workspace.state()
        except (IntegrityError, RuntimeError, ValueError, KeyError, OSError) as exc:
            return {
                "schema_version": "orgrebase.workspace-current-run-archive-view.v1",
                "status": "UNAVAILABLE",
                "run_id": None,
                "stage": None,
                "record": None,
                "failures": [type(exc).__name__],
                "claim_boundary": "CURRENT_CANONICAL_STATE_ONLY_NO_ARCHIVE_BORROWING",
            }
    else:
        projected = projected_state
    state = dict(projected) if isinstance(projected, Mapping) else {}
    completion_history = getattr(workspace, "completion_history", None)
    if include_history and callable(completion_history):
        try:
            state.update(completion_history())
        except (IntegrityError, RuntimeError, ValueError, KeyError, OSError) as exc:
            return {
                "schema_version": "orgrebase.workspace-current-run-archive-view.v2",
                "status": "UNAVAILABLE", "run_id": None, "stage": state.get("stage"),
                "record": None, "failures": [type(exc).__name__],
                "claim_boundary": "CURRENT_CANONICAL_STATE_ONLY_NO_ARCHIVE_BORROWING",
            }
    current_schema = state.get("schema_version") == "orgrebase.workspace-state.v2"
    group_history = state.get("source_readmission_groups", []) if current_schema else []
    group_schema = isinstance(group_history, list) and any(item.get("state") == "APPLIED" for item in group_history if isinstance(item, Mapping))
    execution = state.get("execution") if isinstance(state.get("execution"), Mapping) else {}
    run_id = execution.get("run_id") if isinstance(execution.get("run_id"), str) else None
    stage = str(state.get("stage") or "EMPTY")
    base = {
        "schema_version": ("orgrebase.workspace-current-run-archive-view.v3" if group_schema else "orgrebase.workspace-current-run-archive-view.v2" if current_schema
                           else "orgrebase.workspace-current-run-archive-view.v1"),
        "run_id": run_id,
        "stage": stage,
        "record": None,
        "failures": [],
        "claim_boundary": "CURRENT_CANONICAL_STATE_ONLY_NO_ARCHIVE_BORROWING",
    }
    if current_schema:
        base["business_complete"] = business_is_complete(state)
    if not business_is_complete(state):
        return {**base, "status": "PENDING"}

    failures: list[str] = []

    def digest(value: Any) -> str | None:
        token = value if isinstance(value, str) else None
        if token and len(token) == 71 and token.startswith("sha256:"):
            try:
                int(token[7:], 16)
            except ValueError:
                return None
            return token
        return None

    evidence = state.get("competition_evidence")
    if not isinstance(evidence, Mapping) and not current_schema:
        failures.append("COMPETITION_EVIDENCE_MISSING")
    if isinstance(evidence, Mapping) or not current_schema:
        evidence = evidence if isinstance(evidence, Mapping) else {}
        if evidence.get("status") != "PASS" or evidence.get("run_id") != run_id:
            failures.append("COMPETITION_RUN_BINDING_INVALID")
        if str(evidence.get("project_terminal_state") or "").lower() != "completed":
            failures.append("AGENTTEAMS_TERMINAL_NOT_OBSERVED")

    intake = state.get("task_intake")
    if not isinstance(intake, Mapping) and not current_schema:
        failures.append("TASK_INTAKE_MISSING")
    if (isinstance(intake, Mapping) or not current_schema) and (
        not isinstance(intake, Mapping)
        or
        intake.get("status") != "FORMATION_COMPLETED"
        or intake.get("run_id") != run_id
        or intake.get("intake_persisted") is not True
        or intake.get("intake_canonical_target_writes") != 0
        or digest(intake.get("digest")) is None
    ):
        failures.append("TASK_INTAKE_BINDING_INVALID")
    if current_schema:
        formation = state.get("formation")
        if (not isinstance(run_id, str) or not run_id or not isinstance(formation, Mapping)
                or formation.get("status") != "COMPLETED" or digest(formation.get("digest")) is None):
            failures.append("FORMATION_BINDING_INVALID")

    quote = state.get("quote")
    if not isinstance(quote, Mapping):
        failures.append("FINAL_QUOTE_MISSING")
        quote = {}
    quote_digest = digest(quote.get("digest"))
    version_valid = (isinstance(quote.get("version"), str) and re.fullmatch(r"v[1-9][0-9]*", quote["version"])) if current_schema else quote.get("version") == "v3"
    if not version_valid or quote_digest is None or (current_schema and quote.get("state") != "CURRENT"):
        failures.append("FINAL_QUOTE_INVALID")

    event_scopes = state.get("event_scopes")
    quote_scope = (
        event_scopes.get("quote_business")
        if isinstance(event_scopes, Mapping)
        and isinstance(event_scopes.get("quote_business"), Mapping)
        else {}
    )
    if quote_scope.get("status") != "PASS" or not isinstance(quote_scope.get("events"), int) or quote_scope.get("events", 0) <= 0:
        failures.append("QUOTE_EVENT_SCOPE_INVALID")

    changes = state.get("changes") if isinstance(state.get("changes"), Mapping) else {}
    receipt_records: list[dict[str, Any]] = []
    expected_versions = {"launch_date": "v2", "currency": "v3"}
    event_metadata = state.get("change_events") if isinstance(state.get("change_events"), list) else []
    if current_schema:
        all_event_ids = [event.get("event_id") for event in event_metadata if isinstance(event, Mapping)]
        if (len(all_event_ids) != len(event_metadata) or any(not isinstance(item, str) or not item for item in all_event_ids)):
            return {**base, "status": "INVALID", "failures": ["CHANGE_HISTORY_INCOMPLETE"]}
        if (len(set(all_event_ids)) != len(all_event_ids)
                or set(all_event_ids) != set(changes)
                or any(event.get("status") not in {"APPLIED", "REJECTED", "GROUP_APPLIED"} for event in event_metadata)):
            failures.append("CHANGE_HISTORY_INCOMPLETE")
        kinds = tuple(event["event_id"] for event in event_metadata if event.get("status") == "APPLIED")
        owners = {event["event_id"]: event.get("owner_id") for event in event_metadata}
    else:
        kinds = ("launch_date", "currency")
        owners = {}
    final_quote_observed = False
    for kind in kinds:
        change = changes.get(kind) if isinstance(changes.get(kind), Mapping) else {}
        preview_envelope = change.get("preview") if isinstance(change.get("preview"), Mapping) else {}
        preview_digest = digest(preview_envelope.get("preview_digest"))
        approval_envelope = change.get("approval") if isinstance(change.get("approval"), Mapping) else {}
        approval = (
            approval_envelope.get("approval")
            if isinstance(approval_envelope.get("approval"), Mapping)
            else {}
        )
        approval_digest = digest(approval_envelope.get("approval_digest"))
        review = (
            approval_envelope.get("approval_review_evidence")
            if isinstance(approval_envelope.get("approval_review_evidence"), Mapping)
            else {}
        )
        outcome_envelope = change.get("outcome") if isinstance(change.get("outcome"), Mapping) else {}
        outcome = (
            outcome_envelope.get("outcome")
            if isinstance(outcome_envelope.get("outcome"), Mapping)
            else {}
        )
        rebase = outcome.get("rebase_receipt") if isinstance(outcome.get("rebase_receipt"), Mapping) else {}
        workspace_receipt = (
            outcome.get("workspace_rebase_receipt")
            if isinstance(outcome.get("workspace_rebase_receipt"), Mapping)
            else {}
        )
        outcome_quote = outcome.get("quote") if isinstance(outcome.get("quote"), Mapping) else {}
        rebase_digest = digest(rebase.get("digest"))
        workspace_digest = digest(workspace_receipt.get("digest"))
        valid = (
            preview_digest is not None
            and approval_digest is not None
            and approval.get("digest") == approval_digest
            and approval.get("preview_digest") == preview_digest
            and review.get("review_wait_satisfied") is True
            and outcome.get("approval_digest") == approval_digest
            and rebase.get("status") == "COMPLETED"
            and rebase.get("workflow_run_id") == run_id
            and rebase.get("approval_digest") == approval_digest
            and rebase_digest is not None
            and workspace_receipt.get("status") == "COMPLETED"
            and workspace_receipt.get("base_rebase_receipt_digest") == rebase_digest
            and workspace_digest is not None
            and (isinstance(outcome_quote.get("version"), str) and re.fullmatch(r"v[1-9][0-9]*", outcome_quote["version"]) if current_schema
                 else outcome_quote.get("version") == expected_versions[kind])
            and digest(outcome_quote.get("digest")) is not None
        )
        authority = approval_envelope.get("authority")
        if current_schema:
            if outcome_quote.get("id") != quote.get("id"):
                valid = False
            if authority is not None:
                from orgrebase.workspace.approval_authority import approval_evidence_valid
                event_digest = next(event.get("event_digest") for event in event_metadata if event["event_id"] == kind)
                valid = valid and approval_evidence_valid(
                    authority, event_id=kind, event_digest=event_digest, owner_id=owners[kind],
                    run_id=run_id, approval=approval,
                )
            elif approval.get("actor_id") != owners[kind]:
                valid = False
        if not current_schema and kind == "currency" and outcome_quote.get("digest") != quote_digest:
            valid = False
        if outcome_quote.get("digest") == quote_digest and outcome_quote.get("version") == quote.get("version"):
            final_quote_observed = True
        if not valid:
            failures.append(f"{kind.upper()}_COMPLETION_BINDING_INVALID")
            continue
        receipt_records.append(
            {
                "kind": kind,
                "owner_id": owners[kind] if current_schema else approval.get("actor_id"),
                **({"approval_actor_id": approval.get("actor_id"), "approval_authority": authority}
                   if current_schema and authority is not None else {}),
                "preview_digest": preview_digest,
                "approval_digest": approval_digest,
                "rebase_receipt_digest": rebase_digest,
                "workspace_receipt_digest": workspace_digest,
                "successor_quote_version": outcome_quote.get("version"),
                **({"successor_quote_digest": outcome_quote.get("digest")} if current_schema else {}),
            }
        )

    if current_schema:
        from orgrebase.workspace.archive_readmission import group_archive_record
        grouped_events = []
        group_ids = []
        if not isinstance(group_history, list):
            failures.append("SOURCE_GROUP_HISTORY_INVALID")
            group_history = []
        for group in group_history:
            if not isinstance(group, dict):
                failures.append("SOURCE_GROUP_HISTORY_INVALID")
                continue
            if group.get("state") != "APPLIED":
                continue
            try:
                record = group_archive_record(group, run_id=run_id, quote_id=quote.get("id"), event_metadata=event_metadata)
            except (IntegrityError, ValueError, KeyError, TypeError, AttributeError):
                failures.append("SOURCE_GROUP_COMPLETION_BINDING_INVALID")
                continue
            grouped_events.extend(record["event_ids"])
            group_ids.append(record["group_id"])
            receipt_records.append(record)
            if record["successor_quote_digest"] == quote_digest and record["successor_quote_version"] == quote.get("version"):
                final_quote_observed = True
        expected_group_events = {event["event_id"] for event in event_metadata if event.get("status") == "GROUP_APPLIED"}
        if (set(grouped_events) != expected_group_events or len(grouped_events) != len(set(grouped_events))
                or len(group_ids) != len(set(group_ids))):
            failures.append("SOURCE_GROUP_HISTORY_INCOMPLETE")
    if current_schema and receipt_records and not final_quote_observed:
        failures.append("FINAL_QUOTE_RECEIPT_MISSING")
    if current_schema and not failures:
        receipt_records.sort(key=lambda item: int(item["successor_quote_version"][1:]))
        initial_ref = str(state["formation"].get("deliverable_ref", ""))
        initial_version = initial_ref.rsplit("@", 1)[-1]
        if (not re.fullmatch(r"v[1-9][0-9]*", initial_version)
                or [item["successor_quote_version"] for item in receipt_records]
                != [f"v{number}" for number in range(int(initial_version[1:]) + 1, int(quote["version"][1:]) + 1)]):
            failures.append("QUOTE_SUCCESSION_INCOMPLETE")

    if failures:
        return {**base, "status": "INVALID", "failures": failures}
    payload = quote.get("payload") if isinstance(quote.get("payload"), Mapping) else {}
    return {
        **base,
        "status": "ARCHIVED",
        "record": {
            "archive_class": "CURRENT_BUSINESS_RUN_COMPLETION",
            "same_run_as_current_task": True,
            "terminal_status": "COMPLETED",
            "run_id": run_id,
            "quote": {
                "ref": f"{quote.get('id')}@{quote.get('version')}",
                "digest": quote_digest,
                "launch_date": payload.get("launch_date"),
                "currency": payload.get("currency"),
            },
            "human_approval_count": sum(item.get("human_approval_count", 1) for item in receipt_records),
            **({"business_complete": True, "change_dispositions": event_metadata} if current_schema else {}),
            "selective_rebase_receipts": receipt_records,
            "quote_event_count": quote_scope.get("events"),
            "canonical_authority": "ORGREBASE_CONTROL_PLANE",
            "evidence_class": ("VERIFIED_SAME_RUN_CANONICAL_STATE" if current_schema else "VERIFIED_SAME_RUN_CONTROLLED_LOCAL"),
        },
    }


def _operating_model_view(path: Path | None = None) -> dict[str, Any]:
    """Read the declared quote operating model without borrowing run evidence."""

    selected = path or runtime_asset_path(OPERATING_MODEL_ASSET)
    try:
        payload = json.loads(selected.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("OPERATING_MODEL_UNAVAILABLE") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("OPERATING_MODEL_INVALID")
    steps = payload.get("process_steps")
    if (
        payload.get("schema_version")
        != "orgrebase.workspace-current-process-baseline.v1"
        or payload.get("status") != "NOT_RUN"
        or payload.get("evidence_class") != "NOT_RUN"
        or payload.get("primary_user") != "Enterprise Quote Operator"
        or payload.get("primary_deliverable") != "Enterprise Quote"
        or not isinstance(steps, list)
        or tuple(step.get("id") for step in steps if isinstance(step, dict))
        != OPERATING_MODEL_STEP_IDS
    ):
        raise RuntimeError("OPERATING_MODEL_INVALID")

    projected_steps: list[dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict):
            raise RuntimeError("OPERATING_MODEL_INVALID")
        systems = step.get("systems")
        target = step.get("target_response")
        responsible = step.get("responsible")
        to_be_responsible = step.get("to_be_responsible")
        if (
            not isinstance(systems, dict)
            or not isinstance(target, dict)
            or not isinstance(responsible, list)
            or not responsible
            or not all(isinstance(role, str) and role for role in responsible)
            or not isinstance(to_be_responsible, list)
            or not to_be_responsible
            or not all(isinstance(role, str) and role for role in to_be_responsible)
            or not isinstance(step.get("accountable"), str)
            or not step.get("accountable")
            or systems.get("named_connector_status") != "NOT_RUN"
            or not isinstance(systems.get("as_is_interface_class"), str)
            or not systems.get("as_is_interface_class")
            or target.get("basis") != "DESIGN_TARGET_NOT_OBSERVED_BASELINE"
            or not isinstance(target.get("value"), (int, float))
            or not isinstance(target.get("unit"), str)
        ):
            raise RuntimeError("OPERATING_MODEL_INVALID")
        projected_steps.append(
            {
                "id": step["id"],
                "label": step.get("label"),
                "as_is_interface_class": systems["as_is_interface_class"],
                "named_connector_status": systems["named_connector_status"],
                "responsible": responsible,
                "to_be_responsible": to_be_responsible,
                "accountable": step["accountable"],
                "target_response": {
                    "value": target["value"],
                    "unit": target["unit"],
                    "basis": target["basis"],
                },
            }
        )

    return {
        "schema_version": "orgrebase.workspace-operating-model-view.v1",
        "status": "PASS",
        "source": "DECLARED_ENTERPRISE_QUOTE_OPERATING_MODEL",
        "read_model_target_writes": 0,
        "claim_boundary": (
            "DECLARED_REFERENCE_PROCESS_AND_DESIGN_TARGETS_NOT_CURRENT_RUN_RESULTS"
        ),
        "value_and_responsibility": {
            "primary_user": payload["primary_user"],
            "primary_deliverable": payload["primary_deliverable"],
            "process_status": "NOT_RUN",
            "process_steps": projected_steps,
        },
    }


def _oac_adaptation_review_duration_seconds() -> float:
    raw = os.environ.get("ORGREBASE_OAC_ADAPTATION_REVIEW_SECONDS", "4").strip()
    try:
        selected = float(raw or "4")
    except ValueError as exc:
        raise RuntimeError("ORGREBASE_OAC_ADAPTATION_REVIEW_SECONDS_INVALID") from exc
    if selected < 4:
        raise RuntimeError("ORGREBASE_OAC_ADAPTATION_REVIEW_SECONDS_UNDER_FOUR")
    return selected


def _oac_adaptation_mode() -> str:
    selected = os.environ.get("ORGREBASE_OAC_ADAPTATION_MODE", "optional").strip().lower()
    if selected not in {"off", "optional", "required"}:
        raise RuntimeError("ORGREBASE_OAC_ADAPTATION_MODE_INVALID")
    return selected


def _oac_execution_mode(*, model_provider: str) -> str:
    configured = os.environ.get("ORGREBASE_OAC_EXECUTION_MODE", "").strip()
    if configured:
        selected = configured.upper()
        if selected not in {"FROZEN_REPLAY", "OFFLINE_LOCAL", "LIVE_VERTEX"}:
            raise RuntimeError("ORGREBASE_OAC_EXECUTION_MODE_INVALID")
        return selected
    return "LIVE_VERTEX" if model_provider == "vertex-ai" else "OFFLINE_LOCAL"


def _default_workspace_service(settings: DeploymentSettings | None = None) -> WorkspaceService:
    return open_workspace(settings or DeploymentSettings.from_environment())


def _workspace_staged_commands_required() -> None:
    raise HTTPException(
        status_code=410,
        detail={
            "code": "WORKSPACE_STAGED_COMMANDS_REQUIRED",
            "message": (
                "one-shot Workspace execution is retired because it cannot represent "
                "an external Runtime Owner decision"
            ),
            "next_endpoints": [
                "POST /api/workspace/task-intake/prepare",
                "POST /api/workspace/task-intake/admit",
                "POST /api/workspace/task-intake/run",
                "POST /api/workspace/preview/{change_kind}",
                "POST /api/workspace/approve/{change_kind}",
                "POST /api/workspace/apply/{change_kind}",
            ],
            "target_writes": 0,
        },
    )


def create_app(
    service: OrgRebaseService | None = None,
    workspace_service: WorkspaceService | None = None,
    oac_agentic_runtime: Any | None = None,
    *,
    enable_legacy_demo: bool = False,
    deployment_settings: DeploymentSettings | None = None,
    _workspace_child: bool = False,
) -> FastAPI:
    settings = deployment_settings or DeploymentSettings.from_environment()
    settings = settings.for_workspace(settings.workspace_id)
    validate_deployment_environment(settings)
    production = settings.mode == "production"
    if production and enable_legacy_demo:
        raise ValueError("PRODUCTION_DEMO_FORBIDDEN")
    authenticator = JWTAuthenticator(settings.identity) if settings.identity is not None else None
    if settings.browser_session is not None:
        settings.browser_session.validate_private_files()
    runtime = service or (None if production else OrgRebaseService())
    owns_workspace_runtime = workspace_service is None
    workspace_runtime = workspace_service
    workspace_runtime_lock = Lock()
    oac_adaptation_runtime = None
    oac_adaptation_runtime_lock = Lock()
    oac_agentic_runtime_instance = oac_agentic_runtime
    oac_agentic_runtime_lock = Lock()
    oac_agentic_operation_lock = Lock()
    oac_agentic_tempdir: tempfile.TemporaryDirectory[str] | None = None
    workspace_run_progress_lock = Lock()
    workspace_run_progress: dict[str, Any] = {
        "status": "WAITING",
        "run_id": None,
        "stage": "EMPTY",
        "started_at": None,
        "updated_at": None,
        "failure_code": None,
    }
    workspace_applications: dict[str, FastAPI] = {}

    def update_workspace_run_progress(**updates: Any) -> None:
        with workspace_run_progress_lock:
            workspace_run_progress.update(updates)
            workspace_run_progress["updated_at"] = _utc_now()

    def workspace_run_progress_snapshot() -> dict[str, Any]:
        with workspace_run_progress_lock:
            return dict(workspace_run_progress)

    def get_workspace_runtime() -> WorkspaceService:
        nonlocal workspace_runtime
        if workspace_runtime is None:
            with workspace_runtime_lock:
                if workspace_runtime is None:
                    workspace_runtime = _default_workspace_service(settings)
                    application.state.workspace_service = workspace_runtime
                    application.state.workspace_store_path = workspace_runtime.store_path
        return workspace_runtime

    def get_oac_adaptation_runtime():
        """Use the active Workspace store; never create a second control plane."""

        nonlocal oac_adaptation_runtime
        if oac_adaptation_runtime is None:
            with oac_adaptation_runtime_lock:
                if oac_adaptation_runtime is None:
                    from orgrebase.workspace.oac_quote_adaptation import (
                        OACQuoteAdaptationService,
                    )

                    workspace = get_workspace_runtime()
                    oac_root = os.environ.get("ORGREBASE_OAC_ROOT", "").strip() or None
                    kwargs: dict[str, Any] = {
                        "golden_root": _active_golden_evidence_root(),
                    }
                    service_kwargs: dict[str, Any] = {}
                    if _oac_adaptation_mode() == "required":
                        # Required mode gates this exact Workspace run.  Optional
                        # mode is an independent pre-deployment shadow and must
                        # never reuse the frozen/current Golden run identity.
                        service_kwargs["execution_run_id"] = workspace.effective_workflow_run_id
                    oac_adaptation_runtime = OACQuoteAdaptationService(
                        store=workspace.store,
                        profile=workspace.profile,
                        runtime=workspace.runtime_configuration,
                        oac_root=oac_root,
                        review_duration_seconds=_oac_adaptation_review_duration_seconds(),
                        **service_kwargs,
                        **kwargs,
                    )
                    application.state.oac_adaptation_service = oac_adaptation_runtime
        return oac_adaptation_runtime

    def get_oac_agentic_runtime():
        """Join the live mapper, owner gate, context compiler, and shadow proof.

        This coordinator owns no business state.  It reuses the active Workspace
        store and persists only content-addressed, candidate-only evidence next
        to that store (or in an app-scoped temporary directory for in-memory
        tests).
        """

        nonlocal oac_agentic_runtime_instance, oac_agentic_tempdir
        if oac_agentic_runtime_instance is None:
            with oac_agentic_runtime_lock:
                if oac_agentic_runtime_instance is None:
                    from orgrebase.workspace.oac_agentic_runtime import (
                        OACAgenticRuntime,
                    )

                    workspace = get_workspace_runtime()
                    deployment = workspace.runtime_configuration
                    if deployment is None:
                        raise RuntimeError("OAC_AGENTIC_RUNTIME_EXACT_PACK_REQUIRED")
                    configured_root = os.environ.get("ORGREBASE_OAC_AGENTIC_RUNTIME_ROOT", "").strip()
                    if configured_root:
                        runtime_root = Path(configured_root).expanduser().resolve()
                    elif workspace.store_path != ":memory:":
                        runtime_root = (
                            Path(workspace.store_path).expanduser().resolve().parent
                            / "oac-agentic-adaptation"
                        )
                    else:
                        oac_agentic_tempdir = tempfile.TemporaryDirectory(prefix="orgrebase-oac-agentic-")
                        runtime_root = Path(oac_agentic_tempdir.name)
                    checkout = workspace.competition_checkout or Path(
                        os.environ.get(
                            "AGENTTEAMS_CHECKOUT",
                            str(default_agentteams_checkout(PROJECT_ROOT)),
                        )
                    )
                    lock_path = workspace.competition_lock_path or (
                        PROJECT_ROOT / "agentteams" / "teamharness-lock.json"
                    )
                    golden_root = _active_golden_evidence_root()
                    model_provider = workspace.competition_model_provider
                    oac_agentic_runtime_instance = OACAgenticRuntime(
                        runtime_root=runtime_root,
                        repo_root=PROJECT_ROOT,
                        checkout=checkout,
                        lock_path=lock_path,
                        pack_path=deployment.pack_root,
                        frozen_golden_root=golden_root,
                        adaptation_service=get_oac_adaptation_runtime(),
                        workspace_service=workspace,
                        shadow_verifier=(_run_oac_bound_shadow_independent_verifier),
                        shadow_model_provider=model_provider,
                        execution_mode=_oac_execution_mode(
                            model_provider=model_provider,
                        ),
                        vertex_project=workspace.competition_vertex_project,
                        ollama_endpoint=workspace.competition_ollama_endpoint,
                        late_attempt_fencing_receipt=(
                            PROJECT_ROOT
                            / "evidence"
                            / "semifinal-closure"
                            / "latest"
                            / "agentteams"
                            / "lifecycle-receipt.json"
                        ),
                    )
                    application.state.oac_agentic_runtime = oac_agentic_runtime_instance
        return oac_agentic_runtime_instance

    def workspace_oac_gate_view() -> dict[str, Any]:
        """Expose the deploy-time OAC gate without paths or provider secrets."""

        workspace = get_workspace_runtime()
        mode = _oac_adaptation_mode()
        activation = workspace.oac_activation_state()
        required = mode == "required"
        exact_binding_ready = False
        binding_digest = activation.get("activation_binding_digest")
        reason_code = None
        if required:
            deployment = workspace.runtime_configuration
            if deployment is None:
                reason_code = "OAC_ADAPTATION_EXACT_ENTERPRISE_PACK_REQUIRED"
            else:
                try:
                    binding = get_oac_adaptation_runtime().require_activation_binding(
                        profile_digest=workspace.profile_digest,
                        pack_digest=deployment.pack_digest,
                        execution_run_id=workspace.effective_workflow_run_id,
                    )
                except (IntegrityError, RuntimeError, ValueError, KeyError) as exc:
                    # A consumed activation remains historical fact even when
                    # its implementation can no longer authorize new formation.
                    reason_code = (
                        str(exc) if str(exc) in {
                            "OAC_ADAPTATION_IMPLEMENTATION_REPLAN_REQUIRED",
                            "OAC_ADAPTATION_IMPLEMENTATION_BINDING_MISSING",
                            "OAC_AGENTTEAMS_SOURCE_REPLAN_REQUIRED",
                        } else "OAC_ADAPTATION_ACTIVATION_BINDING_REQUIRED"
                    )
                else:
                    exact_binding_ready = True
                    binding_digest = binding.digest
        consumed = activation["status"] == "CONSUMED_BY_QUOTE_FORMATION"
        form_allowed = not required or exact_binding_ready
        return {
            "schema_version": "orgrebase.workspace-oac-gate.v1",
            "mode": mode,
            "requires_oac_admission": required,
            "form_allowed": form_allowed,
            "status": (
                "CONSUMED_BY_QUOTE_FORMATION"
                if consumed
                else "READY_TO_FORM"
                if form_allowed
                else "BLOCKED_PENDING_OAC"
            ),
            "execution_run_id": workspace.effective_workflow_run_id,
            "activation_binding_digest": binding_digest,
            "consumption_receipt_digest": activation.get(
                "consumption_receipt_digest"
            ),
            "reason_code": reason_code,
            "canonical_target_writes": 0,
        }

    def with_workspace_oac_gate(view: Any) -> dict[str, Any]:
        result = dict(view)
        result["workspace_gate"] = workspace_oac_gate_view()
        return result

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            async with AsyncExitStack() as stack:
                for child in workspace_applications.values():
                    await stack.enter_async_context(child.router.lifespan_context(child))
                yield
        finally:
            if owns_workspace_runtime and workspace_runtime is not None:
                workspace_runtime.close()
            if oac_agentic_tempdir is not None:
                oac_agentic_tempdir.cleanup()

    application = FastAPI(
        title="OrgRebase",
        version=__version__,
        description="Deterministic enterprise change-consistency runtime",
        lifespan=lifespan,
        default_response_class=WireJSONResponse,
    )
    application.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=list(settings.allowed_hosts),
    )
    browser_sessions = (
        BrowserSessions(settings.browser_session, authenticator, BrowserSessionStore(get_workspace_runtime().store))
        if settings.browser_session is not None else None
    )
    application.state.browser_sessions = browser_sessions
    local_sessions = (
        LocalRoleSessions(settings.local_role_session, get_workspace_runtime,
                          skill_steward_actor=SKILL_STEWARD_ACTOR)
        if settings.local_role_session else None
    )
    application.state.local_role_sessions = local_sessions
    if local_sessions is not None:
        workspace = get_workspace_runtime()
        workspace.approval_identity_mode = CONTROLLED_LOCAL_SESSION_IDENTITY
        workspace.verify_membership = local_sessions.verify_membership
        workspace.members_for_action = local_sessions.members_for_action
        workspace.identity_issuer = LOCAL_SESSION_ISSUER
        workspace.authorize_workspace_subject = settings.authorize_workspace

    @application.exception_handler(AuthenticationError)
    async def authentication_error_handler(request, exc):
        return JSONResponse({"detail": {"code": exc.code}}, status_code=exc.status_code,
                            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    @application.middleware("http")
    async def authenticate_request(request, call_next):
        if (not production and browser_sessions is None and authenticator is None and local_sessions is None) or request.url.path in {"/api/health", "/readyz"}:
            return await call_next(request)
        token = None
        authorization_token = None
        try:
            if browser_sessions is not None:
                require_browser_origin(request, browser_sessions)
            if local_sessions is not None:
                local_sessions.require_request(request)
            if request.url.path == "/api/session" or request.url.path.startswith("/api/session/"):
                return await call_next(request)
            if (browser_sessions is not None or local_sessions is not None) and (request.url.path == "/" or request.url.path.startswith("/assets/")):
                response = await call_next(request)
                response.headers["Referrer-Policy"] = "no-referrer"
                response.headers["X-Frame-Options"] = "DENY"
                return response
            local_evidence_read = local_sessions is not None and request.method in {"GET", "HEAD"} and request.url.path in {
                "/api/release-facts", "/api/platform/evidence", "/api/semifinal/evidence",
                "/api/public-real-process/validation", "/api/golden-evidence/status",
                "/api/demo/workspace/agentteams-status",
            }
            if not request.url.path.startswith("/api/workspace/") and request.url.path != "/api/workspaces" and not local_evidence_read:
                return JSONResponse({"detail": {"code": "PRODUCTION_ROUTE_UNAVAILABLE"}}, status_code=404)
            credential = request.headers.get("authorization")
            cookie = request.cookies.get(LOCAL_SESSION_COOKIE if local_sessions else SESSION_COOKIE)
            if (cookie and credential) or (local_sessions is not None and (credential or request.cookies.get(SESSION_COOKIE))):
                raise AuthenticationError("AUTH_CREDENTIALS_AMBIGUOUS")
            if local_sessions is not None:
                principal, csrf, _ = await run_in_threadpool(local_sessions.authenticate, cookie)
                if request.method not in {"GET", "HEAD", "OPTIONS"}:
                    require_csrf(request, local_sessions, csrf)
            elif browser_sessions is not None and cookie:
                principal, csrf, _ = await run_in_threadpool(browser_sessions.authenticate, cookie)
                if request.method not in {"GET", "HEAD", "OPTIONS"}:
                    require_csrf(request, browser_sessions, csrf)
            else:
                principal = await run_in_threadpool(authenticator.authenticate, credential)
            tenant_id = get_workspace_runtime().profile.organization_id if local_sessions else settings.identity.tenant_id
            authorize(principal, request_action(request.method, request.url.path), tenant_id)
            if request.url.path != "/api/workspaces":
                if request.scope.get("orgrebase.workspace_selection_denied"):
                    raise AuthenticationError("AUTH_WORKSPACE_DENIED", 403)
                await run_in_threadpool(settings.authorize_workspace, principal.subject)
            token = request_principal.set(principal)
            def reauthorize_commit():
                current = (local_sessions.authenticate(cookie)[0] if local_sessions else
                           browser_sessions.authenticate(cookie)[0] if browser_sessions is not None and cookie else
                           authenticator.authenticate(credential))
                authorize(current, request_action(request.method, request.url.path), tenant_id)
                if current.actor_id != principal.actor_id or current.subject != principal.subject:
                    raise AuthenticationError("AUTH_IDENTITY_CHANGED", 403)
                if request.url.path != "/api/workspaces":
                    settings.authorize_workspace(current.subject)
                request_principal.set(current)
            authorization_token = request_authorization.set(reauthorize_commit)
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            response.headers["Vary"] = "Cookie, Authorization, X-OrgRebase-Workspace"
            return response
        except AuthenticationError as exc:
            response = JSONResponse(
                {"detail": {"code": exc.code}}, status_code=exc.status_code,
                headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
            )
            if browser_sessions is not None and exc.status_code == 401:
                response.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="lax", path="/")
            return response
        finally:
            if token is not None:
                request_principal.reset(token)
            if authorization_token is not None:
                request_authorization.reset(authorization_token)

    @application.middleware("http")
    async def response_policy(request, call_next):
        with request_incident_scope() as incident:
            try:
                response = await call_next(request)
            except Exception as exc:
                # Unhandled exceptions have no admitted business-code contract.
                # Even uppercase text or an arbitrary .code may be private input.
                detail = {"code": "WORKSPACE_REQUEST_FAILED", "message": "WORKSPACE_REQUEST_FAILED"}
                detail["incident_id"] = incident.capture(exc, detail["code"])
                response = JSONResponse(status_code=500, content={"detail": detail})
            if incident.incident_id is not None:
                response.headers["X-OrgRebase-Incident"] = incident.incident_id
                incident.log(status=response.status_code, route=getattr(request.scope.get("route"), "path", None))
        response.headers["X-Content-Type-Options"] = "nosniff"
        path = request.url.path
        if response.status_code < 400 and (path == "/" or path.startswith("/assets/")):
            response.headers["Cache-Control"] = "no-cache"
        else:
            directives = {part.strip().lower() for part in response.headers.get("Cache-Control", "").split(",")}
            if "no-store" not in directives:
                response.headers["Cache-Control"] = "no-store"
        return response

    def caller_actor(workspace, *, declared_actor_id="", header_actor_id=None):
        principal = request_principal.get()
        if principal is not None:
            if principal.tenant_id != workspace.profile.organization_id:
                raise HTTPException(status_code=403, detail={"code": "AUTH_TENANT_DENIED"})
            return principal.actor_id
        if production or workspace.approval_identity_mode in PRINCIPAL_IDENTITY_MODES:
            raise HTTPException(status_code=401, detail={"code": "AUTH_BEARER_REQUIRED"})
        if workspace.approval_identity_mode == CONTROLLED_LOCAL_HEADER_IDENTITY:
            actor = (header_actor_id or "").strip()
            if not actor:
                raise HTTPException(status_code=403, detail={
                    "code": "WORKSPACE_ACTOR_HEADER_REQUIRED",
                    "message": "X-OrgRebase-Actor is required by the controlled-local identity mode",
                    "identity_mode": CONTROLLED_LOCAL_HEADER_IDENTITY,
                    "identity_claim_boundary": "CONTROLLED_LOCAL_HEADER_IDENTITY_NOT_EXTERNAL_IAM",
                    "target_writes": 0,
                })
            return actor
        return declared_actor_id.strip()

    application.state.service = runtime
    application.state.workspace_service = workspace_runtime
    application.state.oac_adaptation_service = None
    application.state.oac_agentic_runtime = oac_agentic_runtime_instance
    application.state.workspace_store_path = (
        workspace_runtime.store_path if workspace_runtime is not None else None
    )
    application.state.workspace_run_progress = workspace_run_progress
    application.state.deployment_settings = settings
    application.state.workspace_applications = workspace_applications
    application.mount("/assets", StaticFiles(directory=CONSOLE_DIR), name="assets")

    @application.get("/", include_in_schema=False)
    def console() -> FileResponse:
        return FileResponse(CONSOLE_DIR / "index.html")

    @application.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "orgrebase",
            "version": __version__,
            "profile": "AUTHENTICATED_SINGLE_TENANT" if production else "LOCAL_DETERMINISTIC",
        }

    @application.get("/readyz")
    def readiness() -> dict[str, Any]:
        """Check the active profile and the configured database adapter."""

        workspace = get_workspace_runtime()
        workspace.store.check_health()
        if production:
            return {"status": "ready", "service": "orgrebase", "version": __version__}
        runtime = workspace.runtime_configuration
        return {
            "status": "ready",
            "service": "orgrebase",
            "version": __version__,
            "workspace_store": "READY",
            "profile_ref": workspace.profile.ref,
            "profile_digest": workspace.profile_digest,
            "source_admission": workspace.source_admission.verdict,
            "runtime_projection": "MATCH",
            "enterprise_pack_digest": (runtime.pack_digest if runtime is not None else None),
            "deployment_maturity": (workspace.boundaries.get("deployment_maturity", "REFERENCE_RUNTIME")),
            "production_ready": False,
        }

    @application.get("/api/demo/state", include_in_schema=False)
    def state() -> dict[str, Any]:
        return runtime.current_view()

    @application.get("/api/release-facts")
    def release_facts() -> dict[str, Any]:
        try:
            value = json.loads(RELEASE_FACTS_PATH.read_text(encoding="utf-8"))
        except OSError:
            raise HTTPException(status_code=503, detail="RELEASE_FACTS_UNAVAILABLE") from None
        except (UnicodeError, json.JSONDecodeError):
            raise HTTPException(status_code=503, detail="RELEASE_FACTS_INVALID") from None
        if not isinstance(value, dict):
            raise HTTPException(status_code=503, detail="RELEASE_FACTS_INVALID")
        return value

    @application.get("/api/platform/evidence")
    @application.get("/api/semifinal/evidence", include_in_schema=False)
    def semifinal_evidence() -> dict[str, Any]:
        from orgrebase.semifinal_view import semifinal_evidence_view

        return semifinal_evidence_view(PROJECT_ROOT)

    @application.get("/api/public-real-process/validation")
    def public_real_process_validation() -> dict[str, Any]:
        from orgrebase.semifinal_view import public_real_process_validation_view

        return public_real_process_validation_view(PROJECT_ROOT)

    @application.get("/api/golden-evidence/status")
    def golden_evidence_status() -> dict[str, Any]:
        workspace = get_workspace_runtime()
        return _golden_evidence_status(
            _active_golden_evidence_root(),
            current_run_id=workspace.effective_workflow_run_id,
        )

    @application.post("/api/demo/reset", include_in_schema=False)
    def reset() -> dict[str, Any]:
        return runtime.reset()

    @application.post("/api/demo/preview", include_in_schema=False)
    def preview() -> dict[str, Any]:
        return runtime.preview()

    @application.post("/api/demo/apply", include_in_schema=False)
    def apply() -> dict[str, Any]:
        try:
            return runtime.apply()
        except FreshnessError as exc:
            raise HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc)}) from exc

    @application.post("/api/demo/run", include_in_schema=False)
    def run() -> dict[str, Any]:
        return runtime.run_demo()

    @application.post("/api/demo/workspace/quote-to-rebase", include_in_schema=False)
    def workspace_quote_to_rebase() -> dict[str, Any]:
        _workspace_staged_commands_required()

    @application.get("/api/demo/workspace/agentteams-status", include_in_schema=False)
    def workspace_agentteams_status() -> dict[str, Any]:
        from orgrebase.workspace.transport import agentteams_status

        return agentteams_status()

    @application.get("/api/workspace/operating-model")
    def workspace_operating_model() -> dict[str, Any]:
        try:
            return _operating_model_view()
        except (RuntimeError, OSError, ValueError, KeyError) as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "OPERATING_MODEL_UNAVAILABLE",
                    "message": str(exc),
                },
            ) from exc

    @application.get("/api/workspace/skills/enterprise-quote-compose/validation")
    def workspace_skill_validation() -> dict[str, Any]:
        from orgrebase.workspace.skill_validation import validation_view
        try:
            return validation_view(get_workspace_runtime())
        except (IntegrityError, RuntimeError, ValueError, KeyError, OSError) as exc:
            raise HTTPException(status_code=409, detail=_workspace_error(exc)) from exc

    @application.post("/api/workspace/skills/enterprise-quote-compose/validation")
    def workspace_execute_skill_validation(
        payload: dict[str, Any],
        x_orgrebase_actor: str | None = Header(default=None, alias="X-OrgRebase-Actor"),
    ) -> dict[str, Any]:
        from orgrebase.workspace.skill_validation import execute_validation
        try:
            if (set(payload) != {"actor_id", "expected_package_digest", "expected_predecessor_digest", "expected_catalog_digest"}
                    or not all(isinstance(value, str) for value in payload.values())):
                raise IntegrityError("SKILL_VALIDATION_REQUEST_INVALID")
            workspace = get_workspace_runtime()
            actor_id = caller_actor(workspace, declared_actor_id=payload["actor_id"],
                                    header_actor_id=x_orgrebase_actor)
            return execute_validation(workspace, actor_id=actor_id,
                expected_package_digest=payload["expected_package_digest"],
                expected_predecessor_digest=payload["expected_predecessor_digest"],
                expected_catalog_digest=payload["expected_catalog_digest"])
        except HTTPException:
            raise
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=_workspace_error(exc)) from exc
        except (IntegrityError, RuntimeError, ValueError, KeyError, OSError) as exc:
            raise HTTPException(status_code=409, detail=_workspace_error(exc)) from exc

    @application.get("/api/workspace/skills")
    def workspace_skill_sources() -> dict[str, Any]:
        from orgrebase.workspace.skill_revision import skill_source_catalog

        try:
            return skill_source_catalog(get_workspace_runtime().store)
        except (IntegrityError, RuntimeError, ValueError, KeyError, OSError) as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "SKILL_SOURCE_CATALOG_UNAVAILABLE",
                    "message": str(exc),
                },
            ) from exc

    @application.post("/api/workspace/skills/{skill_name}/drafts")
    def workspace_skill_revision_draft(
        skill_name: str,
        payload: dict[str, Any],
        x_orgrebase_actor: str | None = Header(
            default=None,
            alias="X-OrgRebase-Actor",
        ),
    ) -> dict[str, Any]:
        from orgrebase.workspace.skill_revision import create_skill_revision_draft

        try:
            workspace = get_workspace_runtime()
            actor_id = caller_actor(
                workspace, declared_actor_id=str(payload.get("actor_id", "")),
                header_actor_id=x_orgrebase_actor,
            )
            return create_skill_revision_draft(
                workspace.store,
                name=skill_name,
                actor_id=actor_id,
                source_package_digest=str(payload.get("source_package_digest", "")),
                source_skill_digest=str(payload.get("source_skill_digest", "")),
                proposed_version=str(payload.get("proposed_version", "")),
                content=payload.get("content") if isinstance(payload.get("content"), str) else "",
            )
        except HTTPException:
            raise
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=_workspace_error(exc)) from exc
        except (IntegrityError, RuntimeError, ValueError, KeyError, OSError) as exc:
            raise HTTPException(status_code=409, detail=_workspace_error(exc)) from exc

    @application.get("/api/workspace/state")
    def workspace_state(history_limit: int = Query(50, ge=1, le=100)) -> dict[str, Any]:
        try:
            workspace = get_workspace_runtime()
            state = workspace.state(history_limit=history_limit)
            projected = dict(state)
            projected["agent_candidate_outputs"] = _same_run_candidate_output_view(
                workspace,
                state,
            )
            projected["workspace_gate"] = workspace_oac_gate_view()
            projected["workspace_id"] = settings.workspace_id
            from orgrebase.workspace.matrix_workspace import observation_view

            projected["agentteams_operations"] = {
                **state.get("agentteams_operations", {}),
                "element_observation": observation_view(workspace, state),
            }
            return projected
        except (IntegrityError, RuntimeError, ValueError, KeyError, OSError) as exc:
            raise HTTPException(status_code=409, detail=_workspace_error(exc)) from exc

    @application.get("/api/workspace/agentteams-operations")
    def workspace_agentteams_operations() -> dict[str, Any]:
        """Serve the read-only AT back-office projection for Element or dashboards."""

        try:
            from orgrebase.workspace.matrix_workspace import observation_view

            workspace = get_workspace_runtime()
            state = workspace.state()
            return {
                **state.get("agentteams_operations", {}),
                "element_observation": observation_view(workspace, state),
            }
        except (IntegrityError, RuntimeError, ValueError, KeyError, OSError) as exc:
            raise HTTPException(status_code=409, detail=_workspace_error(exc)) from exc

    @application.post("/api/workspace/agentteams-observation")
    def workspace_publish_agentteams_observation(
        payload: dict[str, Any],
        x_orgrebase_actor: str | None = Header(default=None, alias="X-OrgRebase-Actor"),
    ) -> dict[str, Any]:
        from orgrebase.workspace.matrix_observation import MatrixObservationError
        from orgrebase.workspace.matrix_workspace import publish_workspace_observation

        if set(payload) - {"actor_id", "run_id"} or not isinstance(payload.get("run_id"), str):
            raise HTTPException(status_code=422, detail={"code": "MATRIX_OBSERVATION_REQUEST_INVALID"})
        workspace = get_workspace_runtime()
        actor_id = caller_actor(
            workspace, declared_actor_id=str(payload.get("actor_id", "")),
            header_actor_id=x_orgrebase_actor,
        )
        try:
            return publish_workspace_observation(
                workspace, actor_id=actor_id, expected_run_id=payload["run_id"],
            )
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=_workspace_error(exc)) from exc
        except MatrixObservationError as exc:
            raise HTTPException(status_code=503, detail={"code": str(exc)}) from exc
        except (IntegrityError, ValueError, KeyError, OSError) as exc:
            raise HTTPException(status_code=409, detail=_workspace_error(exc)) from exc

    @application.get("/api/workspace/run-progress")
    def workspace_run_progress_view() -> dict[str, Any]:
        """Read observed same-run progress without changing Workspace state."""

        return _workspace_run_progress_view(
            get_workspace_runtime(),
            workspace_run_progress_snapshot(),
        )

    @application.get("/api/workspace/run-archive")
    def workspace_current_run_archive_view() -> dict[str, Any]:
        """Read the fail-closed completion record for the current business run."""

        return _workspace_current_run_archive_view(get_workspace_runtime())

    @application.get("/api/workspace/run-observability")
    def workspace_completed_run_observability_view() -> dict[str, Any]:
        """Project one validated Workspace snapshot into controlled-local OTLP."""

        workspace = get_workspace_runtime()
        try:
            projected = workspace.state()
            if projected.get("schema_version") == "orgrebase.workspace-state.v2":
                projected.update(workspace.completion_history())
        except (IntegrityError, RuntimeError, ValueError, KeyError, OSError) as exc:
            return build_completed_run_observability_from_trusted_state(
                {},
                {
                    "status": "UNAVAILABLE",
                    "failures": [type(exc).__name__],
                },
            )
        archive = _workspace_current_run_archive_view(
            workspace,
            projected_state=projected,
            include_history=False,
        )
        return build_completed_run_observability_from_trusted_state(projected, archive)

    @application.get("/api/workspace/operations")
    def workspace_operations(after: int = Query(default=0, ge=0),
                             limit: int = Query(default=100, ge=1, le=100)) -> dict[str, Any]:
        from orgrebase.workspace.operations_view import operations_snapshot
        try:
            return operations_snapshot(get_workspace_runtime(), after=after, limit=limit)
        except (IntegrityError, RuntimeError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=409, detail=_workspace_error(exc)) from exc

    @application.get("/api/workspace/experience")
    def workspace_experience() -> dict[str, Any]:
        try:
            return get_workspace_runtime().experience_view()
        except (IntegrityError, RuntimeError, ValueError, KeyError) as exc:
            raise HTTPException(
                status_code=409,
                detail=_workspace_error(exc),
            ) from exc

    @application.post("/api/workspace/experience/{decision}")
    def workspace_experience_decision(
        decision: str,
        payload: dict[str, Any],
        x_orgrebase_actor: str | None = Header(
            default=None,
            alias="X-OrgRebase-Actor",
        ),
    ) -> dict[str, Any]:
        try:
            workspace = get_workspace_runtime()
            actor_id = caller_actor(
                workspace, declared_actor_id=str(payload.get("actor_id", "")),
                header_actor_id=x_orgrebase_actor,
            )
            return workspace.decide_experience(
                run_id=str(payload.get("run_id", "")),
                decision=decision,
                actor_id=actor_id,
                candidate_digest=str(payload.get("candidate_digest", "")),
                evaluation_digest=str(payload.get("evaluation_digest", "")),
                observed_skill_head_digest=str(payload.get("observed_skill_head_digest", "")),
            )
        except HTTPException:
            raise
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=_workspace_error(exc)) from exc
        except (IntegrityError, RuntimeError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=409, detail=_workspace_error(exc)) from exc

    @application.get("/api/workspace/profile")
    def workspace_profile() -> dict[str, Any]:
        from orgrebase.workspace.api import workspace_profile_view
        from orgrebase.workspace.profile import northstar_acme_quote_profile

        if workspace_runtime is not None:
            selected = workspace_runtime.profile
        else:
            pack_root = os.environ.get("ORGREBASE_ENTERPRISE_PACK", "").strip()
            if pack_root:
                from orgrebase.workspace.pilot import (
                    load_enterprise_quote_pilot_pack,
                )

                selected = load_enterprise_quote_pilot_pack(pack_root).profile
            else:
                selected = northstar_acme_quote_profile()
        return workspace_profile_view(selected)

    @application.post("/api/workspace/intake")
    def workspace_intake(payload: dict[str, Any]) -> dict[str, Any]:
        from orgrebase.workspace.api import enterprise_intake_view

        try:
            return enterprise_intake_view((payload,))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=_workspace_error(exc)) from exc

    def task_intake_identity(
        *,
        workspace: WorkspaceService,
        declared_actor_id: str,
        header_actor_id: str | None,
    ) -> str:
        """Resolve the local caller label without treating request text as IAM."""

        actor_id = caller_actor(
            workspace, declared_actor_id=declared_actor_id, header_actor_id=header_actor_id,
        )
        expected = workspace.profile.default_task.actor_id
        if actor_id != expected:
            raise AuthorizationError(
                f"TASK_INTAKE_ACTOR_DENIED:expected={expected},actual={actor_id}"
            )
        return actor_id

    def task_intake_contract() -> tuple[Any, Any, str | None, bool, str | None]:
        """Resolve the exact Profile/Template/OAC pre-execution boundary."""

        workspace = get_workspace_runtime()
        profile = workspace.profile
        template = workspace.formation.registry.get(profile.default_task.template_ref)
        gate = workspace_oac_gate_view()
        required = bool(gate["requires_oac_admission"])
        binding_digest = (
            str(gate["activation_binding_digest"])
            if required
            and gate["form_allowed"]
            and isinstance(gate.get("activation_binding_digest"), str)
            else None
        )
        reason_code = (
            str(gate["reason_code"])
            if isinstance(gate.get("reason_code"), str)
            else None
        )
        return profile, template, binding_digest, required, reason_code

    @application.post("/api/workspace/task-intake/prepare")
    def workspace_task_intake_prepare(
        payload: dict[str, Any],
        x_orgrebase_actor: str | None = Header(
            default=None,
            alias="X-OrgRebase-Actor",
        ),
    ) -> dict[str, Any]:
        from orgrebase.workspace.task_intake import prepare_task_intake_candidate

        declared_actor = payload.get("actor_id")
        actor_id = declared_actor if isinstance(declared_actor, str) else ""
        try:
            workspace = get_workspace_runtime()
            identity = task_intake_identity(
                workspace=workspace,
                declared_actor_id=actor_id,
                header_actor_id=x_orgrebase_actor,
            )
            profile, template, binding_digest, required, reason_code = (
                task_intake_contract()
            )
            raw_prompt = payload.get("prompt")
            raw_customer = payload.get("customer_id")
            raw_deliverable = payload.get("deliverable_kind")
            candidate = prepare_task_intake_candidate(
                prompt=raw_prompt if isinstance(raw_prompt, str) else "",
                actor_id=identity,
                customer_id=(raw_customer if isinstance(raw_customer, str) else None),
                deliverable_kind=(
                    raw_deliverable if isinstance(raw_deliverable, str) else ""
                ),
                profile=profile,
                template=template,
                intended_run_id=workspace.effective_workflow_run_id,
                workspace_instance_nonce=(
                    workspace.task_intake_workspace_instance_nonce()
                ),
                oac_activation_binding_digest=binding_digest,
                oac_required=required,
                oac_reason_code=reason_code,
            )
            return candidate.model_dump(mode="json")
        except HTTPException:
            raise
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=_workspace_error(exc)) from exc
        except (IntegrityError, RuntimeError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=409, detail=_workspace_error(exc)) from exc

    @application.post("/api/workspace/task-intake/admit")
    def workspace_task_intake_admit(
        payload: dict[str, Any],
        x_orgrebase_actor: str | None = Header(
            default=None,
            alias="X-OrgRebase-Actor",
        ),
    ) -> dict[str, Any]:
        from orgrebase.workspace.task_intake import admit_task_intake_candidate

        declared_actor = payload.get("actor_id")
        actor_id = declared_actor if isinstance(declared_actor, str) else ""
        try:
            workspace = get_workspace_runtime()
            identity = task_intake_identity(
                workspace=workspace,
                declared_actor_id=actor_id,
                header_actor_id=x_orgrebase_actor,
            )
            profile, template, binding_digest, required, _ = task_intake_contract()
            candidate_payload = payload.get("candidate_receipt")
            if not isinstance(candidate_payload, dict):
                raise IntegrityError("TASK_INTAKE_CANDIDATE_RECEIPT_REQUIRED")
            candidate_digest = payload.get("candidate_digest")
            approval = admit_task_intake_candidate(
                candidate_payload,
                candidate_digest=(
                    candidate_digest if isinstance(candidate_digest, str) else ""
                ),
                actor_id=identity,
                profile=profile,
                template=template,
                expected_run_id=workspace.effective_workflow_run_id,
                expected_workspace_instance_nonce=(
                    workspace.task_intake_workspace_instance_nonce()
                ),
                expected_oac_activation_binding_digest=binding_digest,
                oac_required=required,
            )
            return approval.model_dump(mode="json")
        except HTTPException:
            raise
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=_workspace_error(exc)) from exc
        except (IntegrityError, RuntimeError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=409, detail=_workspace_error(exc)) from exc

    @application.post("/api/workspace/task-intake/run")
    def workspace_task_intake_run(
        payload: dict[str, Any],
        x_orgrebase_actor: str | None = Header(
            default=None,
            alias="X-OrgRebase-Actor",
        ),
    ) -> dict[str, Any]:
        from orgrebase.workspace.task_intake import (
            validate_task_intake_work_description,
            verify_task_intake_approval,
            verify_task_intake_candidate,
        )

        declared_actor = payload.get("actor_id")
        actor_id = declared_actor if isinstance(declared_actor, str) else ""
        progress_started = False
        try:
            workspace = get_workspace_runtime()
            identity = task_intake_identity(
                workspace=workspace,
                declared_actor_id=actor_id,
                header_actor_id=x_orgrebase_actor,
            )
            profile, template, binding_digest, required, _ = task_intake_contract()
            candidate_payload = payload.get("candidate_receipt")
            approval_payload = payload.get("approval_receipt")
            if not isinstance(candidate_payload, dict):
                raise IntegrityError("TASK_INTAKE_CANDIDATE_RECEIPT_REQUIRED")
            if not isinstance(approval_payload, dict):
                raise IntegrityError("TASK_INTAKE_APPROVAL_RECEIPT_REQUIRED")
            candidate = verify_task_intake_candidate(
                candidate_payload,
                profile=profile,
                template=template,
                expected_run_id=workspace.effective_workflow_run_id,
                expected_workspace_instance_nonce=(
                    workspace.task_intake_workspace_instance_nonce()
                ),
                expected_oac_activation_binding_digest=binding_digest,
                oac_required=required,
            )
            work_description = validate_task_intake_work_description(
                payload.get("work_description")
            )
            if (
                sha256_digest(work_description) != candidate.prompt_digest
                or len(work_description) != candidate.prompt_length
            ):
                raise IntegrityError("TASK_INTAKE_WORK_DESCRIPTION_MISMATCH")
            candidate_digest = payload.get("candidate_digest")
            if not isinstance(candidate_digest, str) or candidate_digest != candidate.digest:
                raise IntegrityError("TASK_INTAKE_CANDIDATE_DIGEST_MISMATCH")
            approval = verify_task_intake_approval(
                approval_payload,
                candidate=candidate,
                actor_id=identity,
                profile=profile,
                template=template,
                expected_run_id=workspace.effective_workflow_run_id,
                expected_workspace_instance_nonce=(
                    workspace.task_intake_workspace_instance_nonce()
                ),
                expected_oac_activation_binding_digest=binding_digest,
                oac_required=required,
            )
            approval_digest = payload.get("approval_digest")
            if not isinstance(approval_digest, str) or approval_digest != approval.digest:
                raise IntegrityError("TASK_INTAKE_APPROVAL_DIGEST_MISMATCH")

            progress_started = True
            update_workspace_run_progress(
                status="RUNNING",
                run_id=workspace.effective_workflow_run_id,
                stage="TASK_INTAKE_VERIFIED",
                started_at=_utc_now(),
                failure_code=None,
            )

            activation_binding_payload = None
            formation_decision_payload = None
            context_envelope_payload = None
            if required:
                update_workspace_run_progress(stage="OAC_EXECUTION_ROOTS")
                deployment = workspace.runtime_configuration
                if deployment is None:
                    raise IntegrityError("OAC_ADAPTATION_EXACT_ENTERPRISE_PACK_REQUIRED")
                with oac_agentic_operation_lock:
                    binding = get_oac_adaptation_runtime().require_activation_binding(
                        profile_digest=workspace.profile_digest,
                        pack_digest=deployment.pack_digest,
                        execution_run_id=workspace.effective_workflow_run_id,
                    )
                    if binding.digest != binding_digest:
                        raise IntegrityError("TASK_INTAKE_OAC_BINDING_CHANGED")
                    formation_decision, context_envelope = (
                        get_oac_agentic_runtime().formation_roots_for_current_task(
                            expected_activation_binding_digest=binding.digest,
                        )
                    )
                activation_binding_payload = binding.model_dump(mode="json")
                formation_decision_payload = formation_decision.model_dump(mode="json")
                context_envelope_payload = context_envelope.model_dump(mode="json")

            update_workspace_run_progress(stage="AGENTTEAMS_EXECUTION")
            result = workspace.form_quote_with_dependency_evidence(
                candidate.task_request,
                oac_activation_binding=activation_binding_payload,
                task_formation_decision_receipt=formation_decision_payload,
                context_envelope=context_envelope_payload,
                task_intake_candidate=candidate.model_dump(mode="json"),
                task_intake_approval=approval.model_dump(mode="json"),
                task_intake_work_description=work_description,
            )
            if result.get("task_intake") is None:
                raise IntegrityError("WORKSPACE_TASK_INTAKE_PERSISTENCE_MISSING")
            result_stage = str(result.get("stage") or "CURRENT")
            update_workspace_run_progress(
                status="COMPLETED" if result.get("business_complete") else "ACTIVE",
                stage=result_stage,
            )
            return result
        except HTTPException:
            if progress_started:
                update_workspace_run_progress(status="FAILED", failure_code="HTTP_EXCEPTION")
            raise
        except AuthorizationError as exc:
            if progress_started:
                update_workspace_run_progress(
                    status="FAILED",
                    failure_code=str(getattr(exc, "code", type(exc).__name__)),
                )
            raise HTTPException(status_code=403, detail=_workspace_error(exc)) from exc
        except (IntegrityError, RuntimeError, ValueError, KeyError) as exc:
            if progress_started:
                update_workspace_run_progress(
                    status="FAILED",
                    failure_code=str(getattr(exc, "code", type(exc).__name__)),
                )
            raise HTTPException(status_code=409, detail=_workspace_error(exc)) from exc

    @application.get("/api/workspace/task-intake/work-description")
    def workspace_task_intake_work_description(
        response: Response,
        x_orgrebase_actor: str | None = Header(
            default=None,
            alias="X-OrgRebase-Actor",
        ),
    ) -> dict[str, Any]:
        """Read private task text for the exact current task actor only."""

        try:
            workspace = get_workspace_runtime()
            declared_actor = (x_orgrebase_actor or "").strip()
            if not declared_actor and request_principal.get() is None:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code": "WORKSPACE_ACTOR_HEADER_REQUIRED",
                        "message": "X-OrgRebase-Actor is required for private task text",
                        "target_writes": 0,
                    },
                )
            identity = task_intake_identity(
                workspace=workspace,
                declared_actor_id=declared_actor,
                header_actor_id=x_orgrebase_actor,
            )
            record = workspace.task_intake_work_description(actor_id=identity)
            if record is None:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "code": "TASK_INTAKE_WORK_DESCRIPTION_NOT_RETAINED",
                        "message": (
                            "This run has no retained private work description; "
                            "the server will not reconstruct one from a digest"
                        ),
                        "target_writes": 0,
                    },
                )
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["Pragma"] = "no-cache"
            response.headers["Vary"] = "X-OrgRebase-Actor"
            return record
        except HTTPException:
            raise
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=_workspace_error(exc)) from exc
        except (IntegrityError, RuntimeError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=409, detail=_workspace_error(exc)) from exc

    @application.delete("/api/workspace/task-intake/work-description")
    def workspace_delete_work_description(
        x_orgrebase_actor: str | None = Header(default=None, alias="X-OrgRebase-Actor"),
    ) -> dict[str, Any]:
        from orgrebase.workspace.service import TASK_INTAKE_WORK_DESCRIPTION_ARTIFACT_ID
        workspace = get_workspace_runtime()
        actor_id = caller_actor(workspace, header_actor_id=x_orgrebase_actor)
        try:
            deleted = workspace.private_records.erase(TASK_INTAKE_WORK_DESCRIPTION_ARTIFACT_ID, actor_id=actor_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail={"code": "PRIVATE_RECORD_OWNER_REQUIRED"}) from exc
        return {"deleted": deleted, "business_state_changed": False}

    @application.post("/api/workspace/privacy/purge")
    def workspace_purge_private_records() -> dict[str, Any]:
        return {"deleted": get_workspace_runtime().private_records.purge_expired(), "limit": 1000}

    @application.get("/api/workspace/privacy/deletion-ledger")
    def workspace_deletion_ledger() -> dict[str, Any]:
        return {"records": get_workspace_runtime().private_records.deletion_ledger()}

    @application.post("/api/workspace/privacy/reapply-deletions")
    def workspace_reapply_deletions(payload: dict[str, Any]) -> dict[str, Any]:
        records = payload.get("records")
        if not isinstance(records, list) or len(records) > 1000 or not all(isinstance(item, dict) for item in records):
            raise HTTPException(status_code=422, detail={"code": "PRIVATE_DELETION_LEDGER_INVALID"})
        try:
            deleted = get_workspace_runtime().private_records.reapply_deletions(records)
        except (IntegrityError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=_workspace_error(exc)) from exc
        return {"deleted": deleted, "business_state_changed": False}

    @application.get("/api/workspace/oac-adaptation")
    def workspace_oac_adaptation() -> dict[str, Any]:
        if _oac_adaptation_mode() == "off":
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "OAC_ADAPTATION_DISABLED",
                    "message": "OAC enterprise adaptation is disabled for this deployment",
                    "canonical_target_writes": 0,
                },
            )
        try:
            return get_oac_adaptation_runtime().view()
        except (IntegrityError, RuntimeError, ValueError, KeyError, OSError) as exc:
            raise HTTPException(status_code=409, detail=_workspace_error(exc)) from exc

    @application.get("/api/workspace/oac-adaptation/agentic")
    def workspace_oac_agentic_adaptation() -> dict[str, Any]:
        if _oac_adaptation_mode() == "off":
            raise HTTPException(
                status_code=503,
                detail={"code": "OAC_ADAPTATION_DISABLED"},
            )
        try:
            with oac_agentic_operation_lock:
                return with_workspace_oac_gate(get_oac_agentic_runtime().view())
        except (IntegrityError, RuntimeError, ValueError, KeyError, OSError) as exc:
            raise HTTPException(status_code=409, detail=_workspace_error(exc)) from exc

    @application.post("/api/workspace/oac-adaptation/agent-prepare")
    def workspace_oac_agentic_prepare(payload: dict[str, Any]) -> dict[str, Any]:
        if _oac_adaptation_mode() == "off":
            raise HTTPException(
                status_code=503,
                detail={"code": "OAC_ADAPTATION_DISABLED"},
            )
        try:
            with oac_agentic_operation_lock:
                return with_workspace_oac_gate(
                    get_oac_agentic_runtime().agent_prepare(
                        command_id=str(payload.get("command_id", "")),
                    )
                )
        except (IntegrityError, RuntimeError, ValueError, KeyError, OSError) as exc:
            raise HTTPException(status_code=409, detail=_workspace_error(exc)) from exc

    @application.post("/api/workspace/oac-adaptation/execute-shadow")
    def workspace_oac_agentic_execute_shadow() -> dict[str, Any]:
        if _oac_adaptation_mode() == "off":
            raise HTTPException(
                status_code=503,
                detail={"code": "OAC_ADAPTATION_DISABLED"},
            )
        try:
            with oac_agentic_operation_lock:
                return with_workspace_oac_gate(
                    get_oac_agentic_runtime().execute_shadow()
                )
        except (IntegrityError, RuntimeError, ValueError, KeyError, OSError) as exc:
            raise HTTPException(status_code=409, detail=_workspace_error(exc)) from exc

    @application.post("/api/workspace/oac-adaptation/prepare")
    def workspace_oac_adaptation_prepare(payload: dict[str, Any]) -> dict[str, Any]:
        if _oac_adaptation_mode() == "off":
            raise HTTPException(status_code=503, detail={"code": "OAC_ADAPTATION_DISABLED"})
        try:
            with oac_agentic_operation_lock:
                return get_oac_adaptation_runtime().prepare(
                    command_id=str(payload.get("command_id", "")),
                )
        except (IntegrityError, RuntimeError, ValueError, KeyError, OSError) as exc:
            raise HTTPException(status_code=409, detail=_workspace_error(exc)) from exc

    @application.post("/api/workspace/oac-adaptation/approve")
    def workspace_oac_adaptation_approve(
        payload: dict[str, Any],
        x_orgrebase_actor: str | None = Header(
            default=None,
            alias="X-OrgRebase-Actor",
        ),
    ) -> dict[str, Any]:
        if _oac_adaptation_mode() == "off":
            raise HTTPException(status_code=503, detail={"code": "OAC_ADAPTATION_DISABLED"})
        try:
            workspace = get_workspace_runtime()
            actor_id = caller_actor(
                workspace, declared_actor_id=str(payload.get("actor_id", "")),
                header_actor_id=x_orgrebase_actor,
            )
            with oac_agentic_operation_lock:
                return get_oac_adaptation_runtime().approve(
                    actor_id=actor_id,
                    candidate_digest=str(payload.get("candidate_digest", "")),
                    command_id=str(payload.get("command_id", "")),
                    owner_review_summary_digest=str(
                        payload.get("owner_review_summary_digest", "")
                    ),
                    acknowledgements=(
                        tuple(str(item) for item in payload["acknowledgements"])
                        if isinstance(payload.get("acknowledgements"), list)
                        else ()
                    ),
                )
        except HTTPException:
            raise
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=_workspace_error(exc)) from exc
        except (IntegrityError, RuntimeError, ValueError, KeyError, OSError) as exc:
            raise HTTPException(status_code=409, detail=_workspace_error(exc)) from exc

    @application.post("/api/workspace/form")
    def workspace_form(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        from orgrebase.workspace.models import TaskRequest

        try:
            request = TaskRequest.model_validate(payload) if payload else None
            workspace = get_workspace_runtime()
            if production or local_sessions is not None:
                actor_id = caller_actor(workspace)
                request = request or workspace.formation.default_request(workspace.profile)
                if request.actor_id != actor_id:
                    raise HTTPException(status_code=403, detail={"code": "TASK_INTAKE_ACTOR_DENIED"})
            if workspace.task_intake_required:
                raise HTTPException(
                    status_code=410,
                    detail={
                        "code": "WORKSPACE_TASK_INTAKE_REQUIRED",
                        "message": (
                            "Direct Quote formation is retired for this product runtime; "
                            "submit, confirm, and run an employee task intake instead"
                        ),
                        "next_endpoints": [
                            "POST /api/workspace/task-intake/prepare",
                            "POST /api/workspace/task-intake/admit",
                            "POST /api/workspace/task-intake/run",
                        ],
                        "canonical_target_writes": 0,
                    },
                )
            activation_binding = None
            formation_decision_payload = None
            context_envelope_payload = None
            if _oac_adaptation_mode() == "required":
                deployment = workspace.runtime_configuration
                if deployment is None:
                    raise RuntimeError("OAC_ADAPTATION_EXACT_ENTERPRISE_PACK_REQUIRED")
                with oac_agentic_operation_lock:
                    activation_binding = (
                        get_oac_adaptation_runtime().require_activation_binding(
                            profile_digest=workspace.profile_digest,
                            pack_digest=deployment.pack_digest,
                            execution_run_id=workspace.effective_workflow_run_id,
                        )
                    )
                    formation_decision, context_envelope = (
                        get_oac_agentic_runtime().formation_roots_for_current_task(
                            expected_activation_binding_digest=activation_binding.digest,
                        )
                    )
                formation_decision_payload = formation_decision.model_dump(mode="json")
                context_envelope_payload = context_envelope.model_dump(mode="json")
            return workspace.form_quote_with_dependency_evidence(
                request,
                oac_activation_binding=(
                    activation_binding.model_dump(mode="json")
                    if activation_binding is not None
                    else None
                ),
                task_formation_decision_receipt=formation_decision_payload,
                context_envelope=context_envelope_payload,
            )
        except HTTPException:
            raise
        except (IntegrityError, RuntimeError, ValueError, KeyError) as exc:
            raise HTTPException(
                status_code=409,
                detail=_workspace_error(exc),
            ) from exc

    @application.post("/api/workspace/changes")
    def workspace_register_change(payload: dict[str, Any]) -> dict[str, Any]:
        from orgrebase.workspace.models import ChangeEvent
        try:
            return get_workspace_runtime().register_change(ChangeEvent.model_validate(payload))
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=_workspace_error(exc)) from exc
        except (IntegrityError, RuntimeError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=409, detail=_workspace_error(exc)) from exc

    @application.get("/api/workspace/changes")
    def workspace_changes(after: int = 0, limit: int = 50) -> dict[str, Any]:
        try:
            return get_workspace_runtime().change_history(after=after, limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=_workspace_error(exc)) from exc

    @application.post("/api/workspace/changes/{event_id}/reject")
    def workspace_reject_change(
        event_id: str, payload: dict[str, Any],
        x_orgrebase_actor: str | None = Header(default=None, alias="X-OrgRebase-Actor"),
    ) -> dict[str, Any]:
        reason = payload.get("reason")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
            raise HTTPException(status_code=422, detail={"code": "CHANGE_REJECTION_REASON_REQUIRED"})
        try:
            workspace = get_workspace_runtime()
            actor_id = caller_actor(workspace, declared_actor_id=str(payload.get("actor_id", "")),
                                    header_actor_id=x_orgrebase_actor)
            return workspace.reject_change(event_id, actor_id=actor_id, reason=reason)
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=_workspace_error(exc)) from exc
        except (IntegrityError, RuntimeError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=409, detail=_workspace_error(exc)) from exc

    @application.post("/api/workspace/preview/{change_kind}")
    def workspace_preview(change_kind: str) -> dict[str, Any]:
        try:
            return get_workspace_runtime().preview_command(change_kind)
        except (IntegrityError, RuntimeError, ValueError, KeyError) as exc:
            raise HTTPException(
                status_code=409,
                detail=_workspace_error(exc),
            ) from exc

    @application.post("/api/workspace/approve/{change_kind}")
    def workspace_approve(
        change_kind: str,
        payload: dict[str, Any],
        x_orgrebase_actor: str | None = Header(
            default=None,
            alias="X-OrgRebase-Actor",
        ),
    ) -> dict[str, Any]:
        try:
            workspace = get_workspace_runtime()
            actor_id = caller_actor(
                workspace, declared_actor_id=str(payload.get("actor_id", "")),
                header_actor_id=x_orgrebase_actor,
            )
            return workspace.approve_change(
                change_kind,
                actor_id=actor_id,
                preview_digest=str(payload.get("preview_digest", "")),
                **({"recovery_digest": payload["recovery_digest"]} if "recovery_digest" in payload else {}),
            )
        except HTTPException:
            raise
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=_workspace_error(exc)) from exc
        except (IntegrityError, RuntimeError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=409, detail=_workspace_error(exc)) from exc

    @application.post("/api/workspace/apply/{change_kind}")
    def workspace_apply(change_kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return get_workspace_runtime().apply_approved_change(
                change_kind,
                approval_digest=str(payload.get("approval_digest", "")),
            )
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=_workspace_error(exc)) from exc
        except (FreshnessError, IntegrityError, RuntimeError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=409, detail=_workspace_error(exc)) from exc

    @application.post("/api/workspace/reset")
    def workspace_reset() -> dict[str, Any]:
        if os.environ.get("ORGREBASE_ALLOW_DESTRUCTIVE_RESET", "").strip() != "1":
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "WORKSPACE_DESTRUCTIVE_RESET_DISABLED",
                    "message": (
                        "workspace reset is a maintenance-only destructive operation; "
                        "set ORGREBASE_ALLOW_DESTRUCTIVE_RESET=1 explicitly"
                    ),
                    "target_writes": 0,
                },
            )
        try:
            return get_workspace_runtime().reset()
        except (IntegrityError, RuntimeError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=409, detail=_workspace_error(exc)) from exc

    @application.get("/api/workspace/export/quote")
    def workspace_export_quote() -> JSONResponse:
        try:
            payload = get_workspace_runtime().export_quote()
        except (IntegrityError, RuntimeError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=409, detail=_workspace_error(exc)) from exc
        return WireJSONResponse(
            content=jsonable_encoder(payload),
            headers={"Content-Disposition": 'attachment; filename="orgrebase-quote.json"'},
        )

    @application.get("/api/workspace/export/evidence")
    def workspace_export_evidence() -> JSONResponse:
        try:
            payload = get_workspace_runtime().export_evidence()
        except (IntegrityError, RuntimeError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=409, detail=_workspace_error(exc)) from exc
        return WireJSONResponse(
            content=jsonable_encoder(payload),
            headers={"Content-Disposition": 'attachment; filename="orgrebase-evidence.json"'},
        )

    @application.post("/api/workspace/run")
    def workspace_run() -> dict[str, Any]:
        _workspace_staged_commands_required()

    @application.post("/api/demo/conflict", include_in_schema=False)
    def conflict_drill() -> dict[str, Any]:
        return runtime.conflict_drill()

    @application.post("/api/demo/failure", include_in_schema=False)
    def failure_drill() -> dict[str, Any]:
        return runtime.freshness_failure_drill()

    @application.post("/api/demo/rollback", include_in_schema=False)
    def rollback() -> dict[str, Any]:
        try:
            return runtime.rollback()
        except FreshnessError as exc:
            raise HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc)}) from exc

    @application.get("/api/demo/observability", include_in_schema=False)
    def observability() -> dict[str, Any]:
        return runtime.observability()

    @application.get("/api/tools/v1/dependency-evidence/contract")
    def dependency_tool_contract() -> dict[str, Any]:
        return runtime.tool_contract()

    @application.get("/api/tools/v1/git-artifact/contract")
    def git_tool_contract() -> dict[str, Any]:
        return runtime.git_tool_contract()

    @application.post("/api/tools/v1/dependency-evidence")
    def dependency_tool(
        payload: dict[str, Any],
        x_orgrebase_actor: str | None = Header(default=None, alias="X-OrgRebase-Actor"),
    ) -> dict[str, Any]:
        raw_targets = payload.get("target_ids", ())
        if not isinstance(raw_targets, list):
            raise HTTPException(status_code=400, detail="INVALID_TARGET")
        try:
            return runtime.invoke_dependency_tool(
                actor_id=x_orgrebase_actor,
                target_ids=tuple(raw_targets),
                graph_revision=str(payload.get("graph_revision", "")),
                idempotency_key=str(payload.get("idempotency_key", "")),
            )
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail={"code": exc.code, "message": str(exc)}) from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail={"code": str(exc), "message": str(exc)}) from exc

    @application.get("/api/demo/no-semantic-delta", include_in_schema=False)
    def no_semantic_delta() -> dict[str, Any]:
        return runtime.no_semantic_delta()

    @application.post("/api/receipts/verify")
    def verify_receipt(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return runtime.verify_receipt(payload)
        except IntegrityError as exc:
            raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc

    @application.post("/api/impact-certificates/verify")
    def verify_impact_certificate(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return runtime.verify_impact_certificate(payload)
        except IntegrityError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc

    @application.post("/api/minimal-rebase-certificates/verify")
    def verify_minimal_rebase_certificate(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return runtime.verify_minimal_rebase_certificate(payload)
        except IntegrityError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc

    @application.post("/api/receipts/rollback/verify")
    def verify_rollback_receipt(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return runtime.verify_rollback_receipt(payload)
        except IntegrityError as exc:
            raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc

    from orgrebase.workspace.routes import change_router
    from orgrebase.workspace.source_binding_routes import source_binding_router

    if production:
        workspace = get_workspace_runtime()
        validate_workspace(workspace, settings)
        configure_workspace_identity(workspace, settings, authenticator)
        application.router.routes = [
            route for route in application.router.routes
            if (str(getattr(route, "path", "")).startswith("/api/workspace/")
            and str(getattr(route, "path", "")) not in {"/api/workspace/reset", "/api/workspace/run"}
            and not any(str(getattr(route, "path", "")).startswith(prefix) for prefix in (
                "/api/workspace/skills", "/api/workspace/experience", "/api/workspace/oac-adaptation",
            )))
            or str(getattr(route, "path", "")) in {"/api/health", "/readyz"}
            or str(getattr(route, "path", "")) == "/api/session"
            or (browser_sessions is not None and (
                str(getattr(route, "path", "")).startswith("/api/session/")
                or str(getattr(route, "path", "")) in {"/", "/assets"}
            ))
        ]

    if not enable_legacy_demo:
        safe_legacy_compatibility_routes = {
            "/api/demo/workspace/agentteams-status",
            "/api/demo/workspace/quote-to-rebase",
        }
        application.router.routes = [
            route
            for route in application.router.routes
            if (
                not str(getattr(route, "path", "")).startswith("/api/demo/")
                or str(getattr(route, "path", "")) in safe_legacy_compatibility_routes
            )
        ]

    application.include_router(session_router(production=production, sessions=browser_sessions, authenticator=authenticator,
                                             local_sessions=local_sessions))
    application.include_router(change_router(get_workspace_runtime))
    application.include_router(source_binding_router(get_workspace_runtime))

    @application.get("/api/workspaces")
    def accessible_workspaces() -> dict[str, Any]:
        if settings.workspace_catalog is None:
            items = [{"workspace_id": settings.workspace_id, "label": "工作区"}]
        else:
            principal = request_principal.get()
            if principal is None:
                raise AuthenticationError("AUTH_BEARER_REQUIRED")
            active = {settings.workspace_id, *workspace_applications}
            items = [{"workspace_id": entry.workspace_id, "label": entry.label}
                     for entry in load_catalog(settings.workspace_catalog).workspaces
                     if entry.workspace_id in active and principal.subject in entry.allowed_subjects]
        available = [item["workspace_id"] for item in items]
        default = settings.workspace_id if settings.workspace_id in available else available[0] if available else None
        return {"items": items, "default_workspace_id": default}

    if not _workspace_child:
        try:
            if settings.workspace_catalog is not None:
                for entry in load_catalog(settings.workspace_catalog).workspaces:
                    if entry.workspace_id != settings.workspace_id:
                        workspace_applications[entry.workspace_id] = create_app(
                            deployment_settings=settings.for_workspace(entry.workspace_id), _workspace_child=True,
                        )
            application.add_middleware(WorkspaceDispatcher, applications=workspace_applications,
                                       default_workspace_id=settings.workspace_id)
        except BaseException:
            for child in workspace_applications.values():
                child.state.workspace_service.close()
            if owns_workspace_runtime and workspace_runtime is not None:
                workspace_runtime.close()
            raise
    return application
