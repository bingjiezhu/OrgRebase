#!/usr/bin/env python3
"""Freeze one complete Golden Pilot run as a content-addressed evidence pack."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.workspace.quote_skill_qualification import has_complete_case_identity

EXCLUDED_INDEX_FILES = {"manifest.json", "verification.json"}
OLLAMA_EXPECTED_MODEL_VERSION = (
    "qwen2.5:3b@ollama-manifest:"
    "357c53fb659c5076de1d65ccb0b397446227b71a42be9d1603d46168015c9e4b"
)
OLLAMA_EXPECTED_RECEIPT_VERSION = OLLAMA_EXPECTED_MODEL_VERSION.split("@", 1)[1]
SUPPORTED_VERTEX_MODEL_IDS = frozenset(
    {"gemini-3.7-flash", "gemini-3.8-flash"}
)


def _configured_vertex_model_id() -> str | None:
    configured = os.environ.get("ORGREBASE_VERTEX_MODEL_ID")
    if configured is None:
        return None
    model_id = configured.strip()
    if model_id not in SUPPORTED_VERTEX_MODEL_IDS:
        supported = ",".join(sorted(SUPPORTED_VERTEX_MODEL_IDS))
        raise RuntimeError(
            f"UNSUPPORTED_VERTEX_MODEL_ID:{model_id or '<empty>'};supported={supported}"
        )
    return model_id


VERTEX_MODEL_ID = _configured_vertex_model_id()
MODEL_AUTHORITY = "ADVISORY_ONLY_DETERMINISTIC_REVIEWER_AUTHORITATIVE"
MODEL_ADVISORY_MATCHED = "MODEL_ADVISORY_MATCHED_DETERMINISTIC_VERIFIER"
MODEL_ADVISORY_OVERRIDDEN = "MODEL_ADVISORY_OVERRIDDEN_BY_DETERMINISTIC_VERIFIER"
EXPECTED_REVIEWER_DECISIONS = {
    1: {
        "verdict": "REPLAN",
        "missing_domains": ["finance"],
        "reason_codes": [
            "DOMAIN_NOT_PASS:finance",
            "DOMAIN_SLOT_SET_MISMATCH:finance",
            "SLOT_CARDINALITY_MISMATCH:finance:price_band",
        ],
    },
    2: {
        "verdict": "PASS",
        "missing_domains": [],
        "reason_codes": ["EXACT_SLOT_PROVENANCE_AND_EFFECT_BOUNDARY_PASS"],
    },
}
VERTEX_CLAIM_BOUNDARY = (
    "LIVE_VERTEX_STRUCTURED_ADVISORY_NOT_DETERMINISTIC_AUTHORITY_"
    "NOT_CANONICAL_WRITE"
)
EXPERIENCE_TARGET_SKILL = "structured-domain-handoff"
EXPERIENCE_STEWARD_ID = "human:skill-steward"
EXPERIENCE_PARTITIONS = {
    "REPLAY",
    "HELD_OUT",
    "NEGATIVE_TRANSFER",
    "PERMISSION",
    "INJECTION",
    "MALFORMED",
    "RESOURCE_OR_DEADLINE",
    "CANARY",
}
FORBIDDEN_RUNTIME_DATABASE_NAMES = {
    "workspace.sqlite3",
    "workspace.sqlite3-wal",
    "workspace.sqlite3-shm",
}
PRIVATE_TASK_INTAKE_MARKERS = (
    b'"work_description"',
    b"task-intake-private-work-description:",
    b"workspace-task-intake-work-description-record",
)
PRIVATE_JSON_FIELD_PATTERN = re.compile(
    rb'"(?:prompt|raw_prompt|task_text|customer_email|authorization_header|api_key|'
    rb'access_token|refresh_token|client_secret|password|secret)"\s*:'
)
MAX_PUBLIC_EVIDENCE_FILE_BYTES = 64 * 1024 * 1024
FORBIDDEN_PUBLIC_SECRET_PATTERNS = (
    re.compile(rb"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(rb"\bAIza[0-9A-Za-z_-]{35}\b"),
    re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(rb"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"),
)
LEGACY_PUBLIC_EVENT_TYPES = (
    "WORKSPACE_SEED_LOADED",
    "WORKSPACE_TASK_COMMITTED",
    "GOLDEN_COMPETITION_ACCEPTED",
    "TOOL_INVOKED",
    "WORKSPACE_CHANGE_PREVIEWED",
    "WORKSPACE_CHANGE_APPROVED",
    "REBASE_APPLIED",
    "WORKSPACE_CHANGE_OUTCOME_RECORDED",
    "WORKSPACE_CHANGE_PREVIEWED",
    "WORKSPACE_CHANGE_APPROVED",
    "REBASE_APPLIED",
    "WORKSPACE_CHANGE_OUTCOME_RECORDED",
)
OAC_BOUND_PUBLIC_EVENT_TYPES = (
    "WORKSPACE_SEED_LOADED",
    "OAC_QUOTE_ADAPTATION_PREPARED",
    "OAC_QUOTE_ADAPTER_ADMITTED",
    "OAC_ADAPTER_ACTIVATION_CONSUMED",
    "WORKSPACE_TASK_COMMITTED",
    "GOLDEN_COMPETITION_ACCEPTED",
    "WORKSPACE_TASK_INTAKE_BOUND",
    "TOOL_INVOKED",
    "WORKSPACE_CHANGE_PREVIEWED",
    "WORKSPACE_CHANGE_APPROVED",
    "REBASE_APPLIED",
    "WORKSPACE_CHANGE_OUTCOME_RECORDED",
    "WORKSPACE_CHANGE_PREVIEWED",
    "WORKSPACE_CHANGE_APPROVED",
    "REBASE_APPLIED",
    "WORKSPACE_CHANGE_OUTCOME_RECORDED",
)


class GoldenPilotEvidenceError(RuntimeError):
    """Raised when a directory cannot prove the declared Pilot run."""


def _pack_tree_failures(root: Path) -> list[str]:
    """Reject links, special files, escapes, and unbounded public evidence."""

    candidate = root.absolute()
    if candidate.is_symlink():
        return ["PACK_ROOT_SYMLINK_FORBIDDEN"]
    try:
        root_stat = candidate.lstat()
    except OSError:
        return ["PACK_ROOT_MISSING"]
    if not stat.S_ISDIR(root_stat.st_mode):
        return ["PACK_ROOT_NOT_DIRECTORY"]
    resolved_root = candidate.resolve()
    failures: list[str] = []
    for current, directory_names, file_names in os.walk(candidate, followlinks=False):
        current_path = Path(current)
        for name in sorted((*directory_names, *file_names), key=str.encode):
            path = current_path / name
            relative = path.relative_to(candidate).as_posix()
            try:
                item_stat = path.lstat()
            except OSError:
                failures.append(f"PACK_PATH_UNREADABLE:{relative}")
                continue
            if stat.S_ISLNK(item_stat.st_mode):
                failures.append(f"PACK_SYMLINK_FORBIDDEN:{relative}")
                if name in directory_names:
                    directory_names.remove(name)
                continue
            if not path.resolve().is_relative_to(resolved_root):
                failures.append(f"PACK_PATH_ESCAPE:{relative}")
            if name in directory_names:
                if not stat.S_ISDIR(item_stat.st_mode):
                    failures.append(f"PACK_DIRECTORY_INVALID:{relative}")
            elif not stat.S_ISREG(item_stat.st_mode):
                failures.append(f"PACK_SPECIAL_FILE_FORBIDDEN:{relative}")
            elif item_stat.st_size > MAX_PUBLIC_EVIDENCE_FILE_BYTES:
                failures.append(f"PACK_FILE_TOO_LARGE:{relative}")
    return failures


def _assert_pack_tree_safe(root: Path) -> None:
    failures = _pack_tree_failures(root)
    if failures:
        raise GoldenPilotEvidenceError(failures[0])


def _read_regular_bytes(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        item_stat = os.fstat(descriptor)
        if not stat.S_ISREG(item_stat.st_mode):
            raise GoldenPilotEvidenceError(f"PACK_SPECIAL_FILE_FORBIDDEN:{path.name}")
        if item_stat.st_size > MAX_PUBLIC_EVIDENCE_FILE_BYTES:
            raise GoldenPilotEvidenceError(f"PACK_FILE_TOO_LARGE:{path.name}")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            payload = stream.read(MAX_PUBLIC_EVIDENCE_FILE_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(payload) > MAX_PUBLIC_EVIDENCE_FILE_BYTES:
        raise GoldenPilotEvidenceError(f"PACK_FILE_TOO_LARGE:{path.name}")
    return payload


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(_read_regular_bytes(path).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GoldenPilotEvidenceError(f"INVALID_JSON:{path.name}") from exc
    if not isinstance(value, dict):
        raise GoldenPilotEvidenceError(f"INVALID_OBJECT:{path.name}")
    return value


def _load_array(path: Path) -> list[Any]:
    try:
        value = json.loads(_read_regular_bytes(path).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GoldenPilotEvidenceError(f"INVALID_JSON:{path.name}") from exc
    if not isinstance(value, list):
        raise GoldenPilotEvidenceError(f"INVALID_ARRAY:{path.name}")
    return value


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise GoldenPilotEvidenceError(code)


def _record_ok(value: dict[str, Any]) -> bool:
    body = {key: item for key, item in value.items() if key != "digest"}
    return value.get("digest") == sha256_digest(body)


def _digest_ok(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("sha256:")
        and len(value) == 71
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def _reviewer_runs(runs: list[Any]) -> list[dict[str, Any]]:
    """Resolve reviewer attempts by semantics, with strict legacy fallback."""

    records = [item for item in runs if isinstance(item, dict)]
    semantic = [item for item in records if item.get("role") == "REVIEWER"]
    if semantic:
        resolved = [
            [item for item in semantic if item.get("attempt") == attempt]
            for attempt in (1, 2)
        ]
    else:
        resolved = [
            [
                item
                for item in records
                if str(item.get("task_id", "")).endswith(
                    f"-reviewer-a{attempt}"
                )
            ]
            for attempt in (1, 2)
        ]
    if any(len(matches) != 1 for matches in resolved):
        return []
    return [matches[0] for matches in resolved]


def _contains_key(value: Any, names: set[str]) -> bool:
    if isinstance(value, dict):
        return any(
            str(key).lower() in names or _contains_key(item, names)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_key(item, names) for item in value)
    return False


def _vertex_model_id_from_summary(summary: dict[str, Any]) -> str | None:
    """Resolve one supported Vertex model from the sealed attempt records.

    The evidence pack is authoritative.  The environment variable, when set,
    is only an explicit constraint used by a caller that expects one model.
    """

    if summary.get("model_provider") != "vertex-ai":
        return None
    attempts = summary.get("reviewer_model_attempts")
    if (
        not isinstance(attempts, list)
        or len(attempts) != 2
        or not all(isinstance(item, dict) and _record_ok(item) for item in attempts)
        or {item.get("phase") for item in attempts} != {1, 2}
    ):
        return None
    model_ids: set[str] = set()
    for attempt in attempts:
        requested = attempt.get("requested_model_id")
        observed = attempt.get("observed_model_version")
        if (
            attempt.get("provider") != "vertex-ai"
            or not isinstance(requested, str)
            or requested != observed
            or requested not in SUPPORTED_VERTEX_MODEL_IDS
        ):
            return None
        model_ids.add(requested)
    if len(model_ids) != 1:
        return None
    model_id = next(iter(model_ids))
    if VERTEX_MODEL_ID is not None and model_id != VERTEX_MODEL_ID:
        return None
    return model_id


def _model_advisory_authority_valid(
    output: dict[str, Any],
    *,
    expected_decision: Any,
    phase: int,
) -> bool:
    """Keep model output advisory while sealing deterministic Reviewer authority."""

    advisory = output.get("model_advisory")
    receipt = output.get("model_receipt")
    decision = output.get("decision")
    expected_for_phase = EXPECTED_REVIEWER_DECISIONS.get(phase)
    if (
        not isinstance(advisory, dict)
        or not isinstance(receipt, dict)
        or not isinstance(decision, dict)
        or decision != expected_decision
        or decision != expected_for_phase
        or advisory != receipt.get("value")
    ):
        return False
    advisory_missing = advisory.get("missing_domains")
    advisory_reasons = advisory.get("reason_codes")
    if (
        advisory.get("verdict") not in {"PASS", "REPLAN"}
        or not isinstance(advisory_missing, list)
        or any(not isinstance(item, str) for item in advisory_missing)
        or len(advisory_missing) != len(set(advisory_missing))
        or not isinstance(advisory_reasons, list)
        or any(not isinstance(item, str) for item in advisory_reasons)
    ):
        return False
    accepted = (
        advisory.get("verdict") == decision.get("verdict")
        and set(advisory_missing) == set(decision.get("missing_domains", []))
    )
    expected_disposition = (
        MODEL_ADVISORY_MATCHED if accepted else MODEL_ADVISORY_OVERRIDDEN
    )
    return (
        output.get("model_advisory_accepted") is accepted
        and output.get("model_advisory_disposition_reason") == expected_disposition
    )


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(_read_regular_bytes(path)).hexdigest()


def _entries(root: Path) -> list[dict[str, Any]]:
    _assert_pack_tree_safe(root)
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if path.name in EXCLUDED_INDEX_FILES or not path.is_file():
            continue
        payload = _read_regular_bytes(path)
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            }
        )
    return entries


def _approval_review(state: dict[str, Any], kind: str) -> dict[str, Any]:
    try:
        value = state["changes"][kind]["approval"]["approval_review_evidence"]
    except (KeyError, TypeError) as exc:
        raise GoldenPilotEvidenceError(f"APPROVAL_REVIEW_EVIDENCE_MISSING:{kind}") from exc
    _require(isinstance(value, dict), f"APPROVAL_REVIEW_EVIDENCE_INVALID:{kind}")
    _require(value.get("review_wait_satisfied") is True, f"APPROVAL_WAIT_NOT_SATISFIED:{kind}")
    _require(value.get("review_duration_ms", 0) >= 4_000, f"APPROVAL_WAIT_TOO_SHORT:{kind}")
    _require(
        isinstance(value.get("approval_observed_at_epoch_ms"), int)
        and isinstance(value.get("review_not_before_epoch_ms"), int)
        and value["approval_observed_at_epoch_ms"] >= value["review_not_before_epoch_ms"],
        f"APPROVAL_OBSERVED_BEFORE_GATE:{kind}",
    )
    _require(str(value.get("event_digest", "")).startswith("sha256:"), f"APPROVAL_EVENT_MISSING:{kind}")
    return value



def _deepseek_binding_valid(receipt, runtime, attempt):
    if not all(isinstance(value, dict) for value in (receipt, runtime, attempt)):
        return False
    request_id = receipt.get("provider_request_id")
    digest_fields = ("request_payload_digest", "response_observation_digest")
    return (
        receipt.get("provider") == "deepseek" and receipt.get("evidence_class") == "LIVE_MODEL"
        and receipt.get("model_id") == receipt.get("model_version") == "deepseek-flash"
        and receipt.get("finish_reason") == "stop"
        and isinstance(request_id, str) and bool(request_id.strip())
        and runtime.get("provider") == "deepseek" and runtime.get("status") == "OBSERVED"
        and runtime.get("model_binding") == "UNPINNED_PROVIDER_ALIAS"
        and runtime.get("provider_evidence_class") == "LIVE_DEEPSEEK_MODEL"
        and runtime.get("observed_model_version") == "deepseek-flash"
        and runtime.get("credentials_disclosed") is False
        and runtime.get("request_or_response_content_disclosed") is False
        and runtime.get("provider_request_id_present") is True
        and runtime.get("max_output_tokens") == 2048
        and _digest_ok(runtime.get("response_schema_digest"))
        and all(_digest_ok(runtime.get(key)) and attempt.get(key) == runtime[key] for key in digest_fields)
        and attempt.get("provider_request_id") == request_id
        and attempt.get("requested_model_id") == "deepseek-flash"
        and attempt.get("observed_model_version") == runtime["observed_model_version"]
        and attempt.get("finish_reason") == "stop" and attempt.get("max_output_tokens") == 2048
        and not _contains_key(runtime, {"api_key", "access_token", "authorization", "credential", "secret"})
    )

def _vertex_transport_chain_valid(output, attempt):
    runtime = output.get("model_runtime_binding", {})
    if not isinstance(runtime, dict) or not isinstance(attempt, dict):
        return False
    retry = runtime.get("transport_retry")
    legacy = "transport_retry" not in runtime
    # Only absence of the entire collection denotes the retained pre-observer
    # format. A present but malformed collection is never a legacy escape hatch.
    if "model_attempt_observations" not in output:
        return (legacy and attempt.get("provider_attempt_count", 1) == 1
                and attempt.get("failed_provider_attempt_count", 0) == 0)
    collection = output["model_attempt_observations"]
    if not isinstance(collection, dict):
        return False
    records = collection.get("records")
    receipt = output.get("model_receipt")
    if (not isinstance(records, list) or not 1 <= len(records) <= 3
            or not all(isinstance(record, dict) for record in records)
            or not isinstance(receipt, dict)):
        return False
    if legacy:
        if len(records) != 1:
            return False
    else:
        if not isinstance(retry, dict):
            return False
        dispatches = retry.get("attempts")
        if (not isinstance(dispatches, list)
                or not all(isinstance(item, dict) for item in dispatches)
                or retry.get("max_attempts") != 3
                or retry.get("provider_attempts") != len(records)
                or retry.get("successful_calls") != 1
                or [r.get("dispatch_id") for r in dispatches] != [r.get("dispatch_id") for r in records]):
            return False
    dispatch_ids = [r.get("dispatch_id") for r in records]
    if (not all(isinstance(value, str) and value for value in dispatch_ids)
            or len(set(dispatch_ids)) != len(records)):
        return False
    for index, record in enumerate(records):
        if (not _record_ok(record) or record.get("request_digest") != receipt.get("request_digest")
                or record.get("run_ref") != output.get("run_id") or record.get("task_ref") != output.get("task_id")
                or record.get("provider") != "vertex-ai" or record.get("requested_model") != receipt.get("model_id")
                or record.get("phase") != "RESULT" or record.get("dispatch_state") != "RESPONSE_RECEIVED"
                or record.get("persistence") != "DURABLE"
                or record.get("request_body_digest") != records[-1].get("request_body_digest")):
            return False
        if index < len(records) - 1:
            usage = record.get("usage")
            if (record.get("response_state") != "PROVIDER_ERROR"
                    or record.get("error_code") != "VERTEX_RATE_LIMITED"
                    or not isinstance(usage, dict)
                    or usage.get("status") not in {"UNAVAILABLE", "PARTIAL", "REPORTED"}):
                return False
    return (records[-1].get("response_receipt_digest") == receipt.get("digest")
            and records[-1].get("response_state") == "VALID"
            and records[-1].get("provider_request_id") == receipt.get("provider_request_id")
            and attempt.get("provider_attempt_count", 1 if legacy else None) == len(records)
            and attempt.get("failed_provider_attempt_count", 0 if legacy else None) == len(records) - 1)


def _model_facts(root: Path, summary: dict[str, Any]) -> dict[str, Any]:
    """Verify two exact Reviewer/model attempts and return public-safe facts."""

    provider = summary.get("model_provider")
    _require(provider in {"ollama-local", "vertex-ai", "deepseek"}, "MODEL_PROVIDER_INVALID")
    vertex_model_id = _vertex_model_id_from_summary(summary)
    if provider == "vertex-ai":
        _require(vertex_model_id is not None, "VERTEX_MODEL_ID_INVALID")
    bindings = [
        item
        for item in summary.get("task_bindings", [])
        if isinstance(item, dict) and item.get("role") == "REVIEWER"
    ]
    bindings.sort(key=lambda item: item.get("attempt", 0))
    attempts = summary.get("reviewer_model_attempts")
    _require(
        len(bindings) == 2
        and isinstance(attempts, list)
        and len(attempts) == 2,
        "REVIEWER_MODEL_ATTEMPT_COUNT_INVALID",
    )
    outputs: list[dict[str, Any]] = []
    response_ids: list[str] = []
    response_schema_digests: set[str] = set()
    for phase, (binding, attempt) in enumerate(zip(bindings, attempts, strict=True), start=1):
        task_id = binding.get("task_id")
        _require(isinstance(task_id, str), "REVIEWER_TASK_ID_INVALID")
        output = _load(root / "golden-run" / "process-outputs" / f"{task_id}.json")
        inputs = _load(root / "golden-run" / "process-inputs" / f"{task_id}.json")
        receipt = output.get("model_receipt")
        runtime = output.get("model_runtime_binding")
        _require(
            isinstance(receipt, dict)
            and isinstance(runtime, dict)
            and isinstance(attempt, dict)
            and _record_ok(receipt)
            and _record_ok(attempt),
            "REVIEWER_MODEL_RECORD_DIGEST_INVALID",
        )
        _require(
            output.get("run_id") == summary.get("run_id")
            and output.get("task_id") == task_id
            and output.get("phase") == phase
            and output.get("model_provider") == provider
            and output.get("model_authority") == MODEL_AUTHORITY
            and output.get("candidate_only") is True
            and output.get("target_writes") == 0
            and output.get("input_digest") == sha256_digest(inputs)
            and receipt.get("provider") == provider
            and receipt.get("status") == "VALID"
            and receipt.get("schema_valid") is True
            and receipt.get("output_digest") == sha256_digest(receipt.get("value"))
            and receipt.get("digest") == attempt.get("model_response_receipt_digest")
            and receipt.get("request_digest") == attempt.get("model_request_digest")
            and receipt.get("output_digest") == attempt.get("output_digest")
            and attempt.get("run_id") == summary.get("run_id")
            and attempt.get("task_id") == task_id
            and attempt.get("phase") == phase
            and attempt.get("provider") == provider
            and attempt.get("reviewer_input_digest") == sha256_digest(inputs)
            and attempt.get("reviewer_prompt_payload_digest")
            == output.get("prompt_payload_digest")
            and attempt.get("model_authority") == MODEL_AUTHORITY
            and attempt.get("advisory_accepted")
            == output.get("model_advisory_accepted")
            and attempt.get("advisory_disposition")
            == output.get("model_advisory_disposition_reason")
            and attempt.get("candidate_only") is True
            and attempt.get("target_writes") == 0,
            "REVIEWER_MODEL_ATTEMPT_BINDING_INVALID",
        )
        _require(
            output.get("decision")
            == summary.get(f"reviewer_attempt_{phase}"),
            "DETERMINISTIC_REVIEWER_AUTHORITY_INVALID",
        )
        _require(
            _model_advisory_authority_valid(
                output,
                expected_decision=summary.get(f"reviewer_attempt_{phase}"),
                phase=phase,
            )
            and summary.get(
                f"reviewer_attempt_{phase}_model_advisory_accepted"
            )
            == output.get("model_advisory_accepted")
            and summary.get(
                f"reviewer_attempt_{phase}_model_advisory_disposition"
            )
            == output.get("model_advisory_disposition_reason"),
            "REVIEWER_MODEL_ADVISORY_AUTHORITY_INVALID",
        )
        if provider == "ollama-local":
            _require(
                receipt.get("evidence_class") == "LOCAL_OLLAMA_MODEL"
                and receipt.get("model_id") == "qwen2.5:3b"
                and receipt.get("model_version") == OLLAMA_EXPECTED_RECEIPT_VERSION
                and receipt.get("provider_request_id") is None
                and runtime.get("status") == "BOUND"
                and runtime.get("observed_model_digest")
                == runtime.get("expected_model_digest")
                and receipt.get("model_version")
                == f"ollama-manifest:{runtime.get('observed_model_digest')}"
                and runtime.get("claim_boundary")
                == "LOCAL_LOOPBACK_INFERENCE_NOT_PRODUCTION_PROVIDER"
                and attempt.get("requested_model_id") == "qwen2.5:3b"
                and attempt.get("observed_model_version")
                == OLLAMA_EXPECTED_RECEIPT_VERSION,
                "OLLAMA_REVIEWER_MODEL_BINDING_INVALID",
            )
        elif provider == "deepseek":
            _require(_deepseek_binding_valid(receipt, runtime, attempt), "DEEPSEEK_REVIEWER_MODEL_BINDING_INVALID")
            response_ids.append(receipt["provider_request_id"])
            response_schema_digests.add(runtime["response_schema_digest"])
        else:
            response_id = receipt.get("provider_request_id")
            response_schema_digest = runtime.get("response_schema_digest")
            _require(
                receipt.get("evidence_class") == "LIVE_MODEL"
                and receipt.get("model_id") == vertex_model_id
                and receipt.get("model_version") == vertex_model_id
                and isinstance(response_id, str)
                and bool(response_id.strip())
                and receipt.get("finish_reason") == "STOP"
                and runtime.get("status") == "OBSERVED"
                and runtime.get("provider") == "vertex-ai"
                and runtime.get("provider_evidence_class") == "LIVE_VERTEX_MODEL"
                and runtime.get("model_id") == vertex_model_id
                and runtime.get("observed_model_version") == vertex_model_id
                and runtime.get("location") == "global"
                and runtime.get("thinking_level") == "LOW"
                and runtime.get("max_output_tokens") == 2048
                and runtime.get("provider_request_id_present") is True
                and runtime.get("credentials_disclosed") is False
                and runtime.get("request_or_response_content_disclosed") is False
                and runtime.get("claim_boundary") == VERTEX_CLAIM_BOUNDARY
                and _digest_ok(runtime.get("project_id_digest"))
                and _digest_ok(runtime.get("request_payload_digest"))
                and _digest_ok(runtime.get("response_observation_digest"))
                and _digest_ok(response_schema_digest)
                and attempt.get("provider_request_id") == response_id
                and attempt.get("requested_model_id") == vertex_model_id
                and attempt.get("observed_model_version") == vertex_model_id
                and attempt.get("finish_reason") == "STOP"
                and attempt.get("thinking_level") == "LOW"
                and attempt.get("max_output_tokens") == 2048
                and attempt.get("request_payload_digest")
                == runtime.get("request_payload_digest")
                and attempt.get("response_observation_digest")
                == runtime.get("response_observation_digest")
                and not _contains_key(
                    runtime,
                    {"api_key", "access_token", "authorization", "credential", "secret"},
                ),
                "VERTEX_REVIEWER_MODEL_BINDING_INVALID",
            )
            response_ids.append(response_id)
            response_schema_digests.add(response_schema_digest)
        if provider == "vertex-ai":
            _require(_vertex_transport_chain_valid(output, attempt), "VERTEX_TRANSPORT_RETRY_BINDING_INVALID")
        outputs.append(output)
    if provider in {"vertex-ai", "deepseek"}:
        _require(
            len(set(response_ids)) == 2,
            "VERTEX_RESPONSE_IDS_NOT_DISTINCT",
        )
        _require(
            len(response_schema_digests) == 1,
            "VERTEX_RESPONSE_SCHEMA_DRIFT",
        )
    return {
        "provider": provider,
        "model_id": (
            "deepseek-flash" if provider == "deepseek" else vertex_model_id if provider == "vertex-ai" else "qwen2.5:3b"
        ),
        "model_version": (
            "deepseek-flash" if provider == "deepseek" else (
                vertex_model_id if provider == "vertex-ai" else OLLAMA_EXPECTED_RECEIPT_VERSION
            )
        ),
        "attempt_count": len(outputs),
        "evidence_class": (
            "LIVE_MODEL" if provider in {"vertex-ai", "deepseek"} else "LOCAL_OLLAMA_MODEL"
        ),
        "provider_evidence_class": (
            "LIVE_DEEPSEEK_MODEL" if provider == "deepseek" else "LIVE_VERTEX_MODEL" if provider == "vertex-ai" else "LOCAL_OLLAMA_MODEL"
        ),
        "finish_reasons": [
            item.get("model_receipt", {}).get("finish_reason") for item in outputs
        ],
        "provider_request_ids_distinct": (
            len(set(response_ids)) == 2 if provider in {"vertex-ai", "deepseek"} else None
        ),
        "thinking_level": "LOW" if provider == "vertex-ai" else None,
        "schema_valid": True,
        "model_authority": MODEL_AUTHORITY,
        "canonical_target_writes": 0,
        "advisory_dispositions": [
            {
                "phase": item.get("phase"),
                "accepted": item.get("model_advisory_accepted"),
                "disposition": item.get("model_advisory_disposition_reason"),
            }
            for item in outputs
        ],
    }


def _public_state_facts(
    state: dict[str, Any],
    evidence: dict[str, Any],
    quote: dict[str, Any],
) -> dict[str, Any]:
    """Verify content-addressed public closure without shipping runtime storage."""

    event_chain = state.get("event_chain")
    _require(
        isinstance(event_chain, dict)
        and event_chain == evidence.get("event_chain")
        and event_chain.get("status") == "PASS",
        "PUBLIC_EVENT_CHAIN_PROJECTION_INVALID",
    )
    records = event_chain.get("records")
    execution = state.get("execution")
    execution = execution if isinstance(execution, dict) else {}
    oac_activation = execution.get("oac_activation")
    task_intake = state.get("task_intake")
    oac_bound = isinstance(oac_activation, dict) or isinstance(task_intake, dict)
    if oac_bound:
        run_id = execution.get("run_id")
        competition = state.get("competition_evidence")
        competition = competition if isinstance(competition, dict) else {}
        _require(
            isinstance(oac_activation, dict)
            and isinstance(task_intake, dict)
            and oac_activation == evidence.get("oac_activation_consumption")
            and task_intake == evidence.get("task_intake")
            and oac_activation.get("status") == "CONSUMED_BY_QUOTE_FORMATION"
            and task_intake.get("status") == "FORMATION_COMPLETED"
            and isinstance(run_id, str)
            and run_id.startswith("run:golden-competition:")
            and competition.get("run_id") == run_id
            and oac_activation.get("execution_run_id") == run_id
            and task_intake.get("run_id") == run_id
            and _digest_ok(oac_activation.get("activation_binding_digest"))
            and task_intake.get("oac_activation_binding_digest")
            == oac_activation.get("activation_binding_digest")
            and _digest_ok(task_intake.get("event_digest"))
            and task_intake.get("intake_persisted") is True
            and task_intake.get("intake_canonical_target_writes") == 0,
            "PUBLIC_OAC_TASK_BINDING_INVALID",
        )
        expected_types = OAC_BOUND_PUBLIC_EVENT_TYPES
        approval_sequences = {"launch_date": 10, "currency": 14}
        event_profile = "OAC_BOUND_TASK_INTAKE_V1"
    else:
        expected_types = LEGACY_PUBLIC_EVENT_TYPES
        approval_sequences = {"launch_date": 6, "currency": 10}
        event_profile = "LEGACY_QUOTE_ONLY_V1"
    _require(
        isinstance(records, list)
        and event_chain.get("events") == len(expected_types)
        and len(records) == len(expected_types),
        "PUBLIC_EVENT_CHAIN_COUNT_INVALID",
    )
    previous = "sha256:" + "0" * 64
    for sequence_no, (record, event_type) in enumerate(
        zip(records, expected_types, strict=True), start=1
    ):
        _require(
            isinstance(record, dict)
            and record.get("sequence_no") == sequence_no
            and record.get("event_type") == event_type
            and record.get("previous_digest") == previous
            and _digest_ok(record.get("event_digest")),
            "PUBLIC_EVENT_CHAIN_LINK_INVALID",
        )
        previous = record["event_digest"]
    _require(
        event_chain.get("head_digest") == previous,
        "PUBLIC_EVENT_CHAIN_HEAD_INVALID",
    )
    if oac_bound:
        assert isinstance(records, list)
        scopes = state.get("event_scopes")
        by_sequence = {item.get("sequence_no"): item for item in records}
        _require(
            isinstance(scopes, dict)
            and scopes == evidence.get("event_scopes")
            and scopes.get("schema_version") == "orgrebase.workspace-event-scopes.v2"
            and scopes.get("layout") == "OAC_PREFIX_QUOTE_SUFFIX"
            and scopes.get("workspace_global")
            == {
                "status": "PASS",
                "events": 16,
                "head_digest": previous,
                "definition": "FULL_APPEND_ONLY_WORKSPACE_CHAIN",
            }
            and scopes.get("workspace_prelude")
            == {
                "status": "PASS",
                "events": 1,
                "first_sequence_no": 1,
                "last_sequence_no": 1,
                "head_digest": by_sequence[1]["event_digest"],
                "definition": "LEADING_WORKSPACE_INITIALIZATION_NOT_QUOTE_BUSINESS",
            }
            and scopes.get("oac_adaptation")
            == {
                "status": "PASS",
                "events": 3,
                "first_sequence_no": 2,
                "last_sequence_no": 4,
                "start_anchor_digest": by_sequence[1]["event_digest"],
                "head_digest": by_sequence[4]["event_digest"],
                "definition": "OAC_GOVERNANCE_EVENTS_ZERO_QUOTE_MUTATION_AUTHORITY",
            }
            and scopes.get("quote_business")
            == {
                "status": "PASS",
                "events": 12,
                "head_digest": previous,
                "first_sequence_no": 5,
                "last_sequence_no": 16,
                "start_anchor_digest": by_sequence[4]["event_digest"],
                "definition": "CONTIGUOUS_NON_OAC_SUFFIX",
            }
            and scopes.get("relationship")
            == {
                "event_layout": "OAC_PREFIX_QUOTE_SUFFIX",
                "honest_contiguous_windows_published": True,
                "quote_is_contiguous_prefix": False,
                "quote_is_contiguous_suffix": True,
                "oac_events_are_prefix": True,
                "oac_events_are_suffix": False,
            }
            and task_intake.get("event_digest")
            == by_sequence[7]["event_digest"],
            "PUBLIC_OAC_EVENT_SCOPES_INVALID",
        )

    current_quote = state.get("quote")
    _require(
        isinstance(current_quote, dict)
        and current_quote == quote.get("quote")
        and current_quote.get("id") == "work:quote-blue-harbor"
        and current_quote.get("version") == "v3",
        "PUBLIC_CURRENT_QUOTE_INVALID",
    )
    changes = state.get("changes")
    _require(
        isinstance(changes, dict)
        and set(changes) == {"launch_date", "currency"},
        "PUBLIC_CHANGE_PROJECTION_INVALID",
    )
    quote_versions = ["v1"]
    approval_records = {
        item["sequence_no"]: item
        for item in records
        if item.get("event_type") == "WORKSPACE_CHANGE_APPROVED"
    }
    for kind, expected_version, expected_sequence in (
        ("launch_date", "v2", approval_sequences["launch_date"]),
        ("currency", "v3", approval_sequences["currency"]),
    ):
        change = changes.get(kind)
        _require(isinstance(change, dict), f"PUBLIC_CHANGE_INVALID:{kind}")
        projected_quote = (
            change.get("outcome", {}).get("outcome", {}).get("quote")
        )
        approval = change.get("approval", {}).get("approval_review_evidence")
        _require(
            isinstance(projected_quote, dict)
            and projected_quote.get("version") == expected_version
            and isinstance(approval, dict)
            and approval.get("review_wait_satisfied") is True
            and approval.get("review_duration_ms", 0) >= 4_000
            and approval.get("approval_observed_at_epoch_ms", -1)
            >= approval.get("review_not_before_epoch_ms", 0)
            and approval.get("event_sequence_no") == expected_sequence
            and approval.get("event_digest")
            == approval_records.get(expected_sequence, {}).get("event_digest"),
            f"PUBLIC_CHANGE_CLOSURE_INVALID:{kind}",
        )
        quote_versions.append(expected_version)
    _require(
        quote_versions == ["v1", "v2", "v3"],
        "PUBLIC_QUOTE_LINEAGE_INVALID",
    )
    return {
        "source": "CONTENT_ADDRESSED_PUBLIC_JSON_PROJECTIONS",
        "event_profile": event_profile,
        "oac_bound": oac_bound,
        "task_intake_event_bound": oac_bound,
        "event_chain_verified": True,
        "event_count": len(records),
        "event_head_digest": previous,
        "quote_versions": quote_versions,
        "approval_count": len(approval_records),
        "runtime_database_included": False,
    }


def _remove_runtime_database_files(root: Path) -> None:
    """Remove private runtime persistence before the public pack is indexed."""

    for name in sorted(FORBIDDEN_RUNTIME_DATABASE_NAMES):
        path = root / name
        _require(not path.is_symlink(), f"RUNTIME_DATABASE_SYMLINK_FORBIDDEN:{name}")
        if path.exists():
            _require(path.is_file(), f"RUNTIME_DATABASE_PATH_INVALID:{name}")
            path.unlink()
    _require(
        not any((root / name).exists() for name in FORBIDDEN_RUNTIME_DATABASE_NAMES),
        "RUNTIME_DATABASE_EXCLUSION_FAILED",
    )


def _require_no_private_task_intake_projection(root: Path) -> None:
    leaked: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name in EXCLUDED_INDEX_FILES:
            continue
        payload = _read_regular_bytes(path)
        if (
            any(marker in payload for marker in PRIVATE_TASK_INTAKE_MARKERS)
            or PRIVATE_JSON_FIELD_PATTERN.search(payload)
            or any(pattern.search(payload) for pattern in FORBIDDEN_PUBLIC_SECRET_PATTERNS)
        ):
            leaked.append(path.relative_to(root).as_posix())
    _require(
        not leaked,
        "PRIVATE_TASK_INTAKE_EXPOSED:" + ",".join(leaked),
    )


def _experience_facts(
    root: Path,
    *,
    run_id: str,
    correlation_id: str,
    summary: dict[str, Any],
    state: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """Verify the persisted single-run experience-to-Skill approval chain."""

    state_view = state.get("experience_governance")
    evidence_view = evidence.get("experience_governance")
    _require(
        isinstance(state_view, dict)
        and isinstance(evidence_view, dict)
        and state_view == evidence_view
        and _record_ok(state_view),
        "EXPERIENCE_STATE_EVIDENCE_PROJECTION_INVALID",
    )
    _require(
        state_view.get("run_id") == run_id
        and state_view.get("status") == "APPROVED_CANARY"
        and state_view.get("owner_id") == EXPERIENCE_STEWARD_ID
        and state_view.get("discoverable") is True
        and state_view.get("loadable") is True
        and state_view.get("callable") is True
        and state_view.get("head_fresh") is True
        and state_view.get("current_quote_consumed_candidate") is False
        and state_view.get("candidate_only") is True
        and state_view.get("target_writes") == 0,
        "EXPERIENCE_APPROVED_VIEW_INVALID",
    )

    candidate = state_view.get("candidate")
    evaluation = state_view.get("evaluation")
    gate = state_view.get("review_gate")
    decision = state_view.get("decision")
    release = state_view.get("release")
    _require(
        all(
            isinstance(item, dict) and _record_ok(item)
            for item in (candidate, evaluation, gate, decision, release)
        ),
        "EXPERIENCE_PUBLIC_ARTIFACT_DIGEST_INVALID",
    )
    candidate_id = candidate.get("id")
    _require(
        isinstance(candidate_id, str)
        and candidate_id.startswith("experience-candidate:")
        and candidate_id.endswith("@v1"),
        "EXPERIENCE_PUBLIC_ARTIFACT_ID_INVALID",
    )
    token = candidate_id.removeprefix("experience-candidate:").removesuffix("@v1")
    _require(
        gate.get("id") == f"experience-review-gate:{token}@v1"
        and decision.get("id") == f"experience-decision:{token}@v1",
        "EXPERIENCE_PUBLIC_ARTIFACT_ID_INVALID",
    )

    source = candidate.get("source")
    required_evidence = candidate.get("required_evidence")
    _require(
        candidate.get("outcome") == "IMPROVE"
        and candidate.get("maturity") == "SINGLE_RUN_SEED"
        and candidate.get("source_run_id") == run_id
        and candidate.get("target_skill_name") == EXPERIENCE_TARGET_SKILL
        and _digest_ok(candidate.get("base_package_digest"))
        and isinstance(source, dict)
        and source.get("run_id") == run_id
        and source.get("correlation_id") == correlation_id
        and source.get("summary_digest") == summary.get("digest")
        and source.get("competition_evidence_digest")
        == state.get("competition_evidence", {}).get("digest")
        and isinstance(required_evidence, dict)
        and set(candidate.get("proposed_evaluation_partitions", []))
        == EXPERIENCE_PARTITIONS
        and candidate.get("permissions", {}).get("human_approval_required") is True
        and candidate.get("permissions", {}).get("effect_ceiling") == "CANDIDATE_ONLY"
        and candidate.get("permissions", {}).get("target_writes") == 0
        and candidate.get("candidate_only") is True
        and candidate.get("target_writes") == 0,
        "EXPERIENCE_CANDIDATE_INVALID",
    )

    task_bindings = summary.get("task_bindings", [])
    finance_a1 = next(
        (
            item
            for item in task_bindings
            if item.get("domain") == "finance" and item.get("attempt") == 1
        ),
        None,
    )
    finance_a2 = next(
        (
            item
            for item in task_bindings
            if item.get("domain") == "finance" and item.get("attempt") == 2
        ),
        None,
    )
    reviewer_bindings = sorted(
        (item for item in task_bindings if item.get("role") == "REVIEWER"),
        key=lambda item: item.get("attempt", 0),
    )
    _require(
        isinstance(finance_a1, dict)
        and isinstance(finance_a2, dict)
        and len(reviewer_bindings) == 2,
        "EXPERIENCE_SOURCE_TASKS_INVALID",
    )
    tool = _load(root / "golden-run" / "tool" / "invocation.json")
    expected_required = {
        "finance_a1_output_digest": finance_a1.get("observed_result_digest"),
        "reviewer_a1_output_digest": reviewer_bindings[0].get("observed_result_digest"),
        "tool_receipt_digest": tool.get("receipt", {}).get("digest"),
        "finance_a2_output_digest": finance_a2.get("observed_result_digest"),
        "reviewer_a2_output_digest": reviewer_bindings[1].get("observed_result_digest"),
    }
    _require(
        required_evidence == expected_required
        and all(_digest_ok(value) for value in expected_required.values()),
        "EXPERIENCE_SOURCE_EVIDENCE_BINDING_INVALID",
    )
    for item in (finance_a1, finance_a2, *reviewer_bindings):
        output = _load(
            root / "golden-run" / "process-outputs" / f"{item['task_id']}.json"
        )
        _require(
            sha256_digest(output) == item.get("observed_result_digest"),
            "EXPERIENCE_SOURCE_OUTPUT_DIGEST_INVALID",
        )

    cases = evaluation.get("case_results")
    gates = evaluation.get("gate_results")
    premise = evaluation.get("premise_lock")
    _require(
        evaluation.get("candidate_ref") == candidate.get("id")
        and evaluation.get("candidate_digest") == candidate.get("digest")
        and evaluation.get("verdict") == "CANARY"
        and isinstance(cases, list)
        and len(cases) == 8
        and {item.get("partition") for item in cases} == EXPERIENCE_PARTITIONS
        and all(item.get("passed") is True for item in cases)
        and isinstance(gates, list)
        and all(item.get("passed") is True for item in gates)
        and isinstance(premise, dict)
        and premise.get("candidate") == candidate.get("digest")
        and premise.get("source_run") == run_id
        and premise.get("gold_boundary") == "single-run-seed:evaluator-only",
        "EXPERIENCE_EVALUATION_INVALID",
    )
    _require(
        gate.get("run_id") == run_id
        and gate.get("owner_id") == EXPERIENCE_STEWARD_ID
        and gate.get("candidate_digest") == candidate.get("digest")
        and gate.get("evaluation_digest") == evaluation.get("digest")
        and gate.get("evaluation_verdict") == "CANARY"
        and gate.get("evaluation_partition_count") == 8
        and gate.get("observed_skill_head_digest")
        == candidate.get("base_package_digest")
        and gate.get("review_duration_ms", 0) >= 4_000
        and gate.get("not_before_epoch_ms", -1)
        - gate.get("review_started_at_epoch_ms", 0)
        >= 4_000
        and gate.get("candidate_only") is True
        and gate.get("target_writes") == 0,
        "EXPERIENCE_REVIEW_GATE_INVALID",
    )
    _require(
        decision.get("run_id") == run_id
        and decision.get("decision") == "APPROVE"
        and decision.get("actor_id") == EXPERIENCE_STEWARD_ID
        and decision.get("candidate_digest") == candidate.get("digest")
        and decision.get("evaluation_digest") == evaluation.get("digest")
        and decision.get("review_gate_digest") == gate.get("digest")
        and decision.get("observed_skill_head_digest")
        == candidate.get("base_package_digest")
        and decision.get("review_wait_satisfied") is True
        and decision.get("decided_at_epoch_ms", -1)
        >= gate.get("not_before_epoch_ms", 0)
        and decision.get("candidate_only") is True
        and decision.get("target_writes") == 0,
        "EXPERIENCE_DECISION_INVALID",
    )

    history = release.get("release_history")
    dry_call = release.get("dry_call")
    _require(
        isinstance(history, list)
        and len(history) == 3
        and [item.get("to_state") for item in history]
        == ["EVALUATED", "SHADOW", "CANARY"]
        and all(isinstance(item, dict) and _record_ok(item) for item in history),
        "EXPERIENCE_RELEASE_HISTORY_INVALID",
    )
    previous_digest: str | None = None
    for item in history:
        _require(
            item.get("previous_receipt_digest") == previous_digest
            and item.get("skill_name") == EXPERIENCE_TARGET_SKILL
            and item.get("source_candidate_ref") == candidate.get("id")
            and item.get("source_candidate_digest") == candidate.get("digest")
            and item.get("evaluation_receipt_digest") == evaluation.get("digest")
            and item.get("package_digest") == release.get("package_digest")
            and item.get("effective_package_digest") == release.get("package_digest")
            and item.get("actor_id") == "authority:skill-registry"
            and item.get("source_candidate_executable") is False
            and item.get("predecessor_package_digest")
            == candidate.get("base_package_digest"),
            "EXPERIENCE_RELEASE_HISTORY_BINDING_INVALID",
        )
        previous_digest = item.get("digest")
    _require(
        release.get("run_id") == run_id
        and release.get("candidate_digest") == candidate.get("digest")
        and release.get("evaluation_digest") == evaluation.get("digest")
        and release.get("approval_digest") == decision.get("digest")
        and release.get("skill_name") == EXPERIENCE_TARGET_SKILL
        and release.get("predecessor_package_digest")
        == candidate.get("base_package_digest")
        and release.get("release_state") == "CANARY"
        and release.get("release_head_digest") == previous_digest
        and release.get("authorization_mode") == "RELEASE"
        and release.get("claim_boundary")
        == "CONTROLLED_LOCAL_SINGLE_RUN_SEED_NOT_PRODUCTION_GENERALIZATION"
        and release.get("candidate_only") is True
        and release.get("target_writes") == 0
        and isinstance(dry_call, dict)
        and dry_call.get("purpose")
        == "CONTROLLED_LOCAL_RELEASE_AUTHORIZATION_PROOF"
        and dry_call.get("current_quote_consumed") is False,
        "EXPERIENCE_RELEASE_INVALID",
    )
    dry_receipt = dry_call.get("receipt")
    dry_result = dry_call.get("result")
    _require(
        isinstance(dry_receipt, dict)
        and isinstance(dry_result, dict)
        and _record_ok(dry_receipt)
        and dry_receipt.get("run_id") == run_id
        and dry_receipt.get("authorization_mode") == "RELEASE"
        and dry_receipt.get("release_receipt_digest") == release.get("release_head_digest")
        and dry_receipt.get("package_digest") == release.get("package_digest")
        and dry_receipt.get("outcome") == "SUCCESS"
        and dry_receipt.get("candidate_only") is True
        and dry_receipt.get("target_writes") == 0
        and dry_receipt.get("output_digest") == sha256_digest(dry_result)
        and dry_result.get("run_id") == run_id
        and dry_result.get("action") == "HANDOFF"
        and dry_result.get("package_digest") == release.get("package_digest")
        and dry_result.get("candidate_only") is True
        and dry_result.get("target_writes") == 0,
        "EXPERIENCE_RELEASE_DRY_CALL_INVALID",
    )
    return {
        "status": "APPROVED_CANARY",
        "maturity": "SINGLE_RUN_SEED",
        "outcome": "IMPROVE",
        "target_skill": EXPERIENCE_TARGET_SKILL,
        "evaluation_partition_count": len(cases),
        "evaluation_pass_count": sum(
            item.get("passed") is True for item in cases
        ),
        "review_owner": EXPERIENCE_STEWARD_ID,
        "review_duration_ms": gate.get("review_duration_ms"),
        "release_state": "CANARY",
        "authorization_mode": "RELEASE",
        "dry_call_outcome": dry_receipt.get("outcome"),
        "discoverable": True,
        "loadable": True,
        "callable": True,
        "current_quote_consumed_candidate": False,
        "candidate_only": True,
        "canonical_target_writes": 0,
        "candidate_digest": candidate.get("digest"),
        "evaluation_digest": evaluation.get("digest"),
        "decision_digest": decision.get("digest"),
        "release_digest": release.get("digest"),
    }


def build_manifest(root: Path) -> dict[str, Any]:
    _assert_pack_tree_safe(root)
    root = root.resolve()
    summary = _load(root / "golden-run" / "summary.json")
    state = _load(root / "state.json")
    evidence = _load(root / "evidence-export.json")
    quote = _load(root / "quote-export.json")
    skill_package = _load(root / "golden-run" / "skill" / "package.json")
    skill_evaluation = _load(root / "golden-run" / "skill" / "evaluation.json")
    skill_release_ledger = _load_array(
        root / "golden-run" / "skill" / "release-ledger.json"
    )
    skill_invocation = _load(root / "golden-run" / "skill" / "receipt.json")

    _require(_record_ok(summary), "SUMMARY_DIGEST_INVALID")
    _require(_record_ok(evidence), "EVIDENCE_EXPORT_DIGEST_INVALID")
    _require(_record_ok(quote), "QUOTE_EXPORT_DIGEST_INVALID")
    _require(summary.get("status") == "PASS", "GOLDEN_RUN_NOT_PASS")
    _require(
        summary.get("evidence_class") == "CONTROLLED_LOCAL_GOLDEN_COMPETITION",
        "GOLDEN_EVIDENCE_CLASS_INVALID",
    )
    run_id = summary.get("run_id")
    correlation_id = summary.get("correlation_id")
    _require(isinstance(run_id, str) and run_id.startswith("run:golden-competition:"), "RUN_ID_INVALID")
    _require(isinstance(correlation_id, str) and run_id != correlation_id, "CORRELATION_ID_INVALID")
    _require(state.get("stage") == "QUOTE_V3", "PILOT_NOT_AT_QUOTE_V3")
    _require(state.get("execution", {}).get("run_id") == run_id, "STATE_RUN_BINDING_INVALID")
    _require(evidence.get("stage") == "QUOTE_V3", "EVIDENCE_EXPORT_STAGE_INVALID")
    _require(evidence.get("competition_evidence", {}).get("run_id") == run_id, "EVIDENCE_RUN_INVALID")
    _require(quote.get("quote", {}).get("version") == "v3", "QUOTE_EXPORT_VERSION_INVALID")
    _require(summary.get("canonical_target_writes") == 0, "CANDIDATE_RUNTIME_WROTE_CANONICAL_STATE")
    model_facts = _model_facts(root, summary)
    skill_package_digest = sha256_digest(
        {
            key: value
            for key, value in skill_package.items()
            if key != "manifest_digest"
        }
    )
    _require(
        skill_package.get("name") == "enterprise-quote-compose"
        and skill_package.get("manifest_digest") == skill_package_digest,
        "SKILL_PACKAGE_INVALID",
    )
    _require(
        _record_ok(skill_evaluation)
        and skill_evaluation.get("verdict") == "CANARY"
        and isinstance(skill_evaluation.get("case_results"), list)
        and has_complete_case_identity(skill_evaluation, run_id)
        and summary.get("skill_evaluation_partition_count") == 8
        and summary.get("skill_evaluation_case_count", 8) == len(skill_evaluation["case_results"])
        and all(item.get("passed") is True for item in skill_evaluation["case_results"])
        and all(item.get("passed") is True for item in skill_evaluation.get("gate_results", []))
        and summary.get("skill_evaluation_receipt_digest")
        == skill_evaluation.get("digest"),
        "SKILL_EVALUATION_INVALID",
    )
    _require(
        len(skill_release_ledger) == 3
        and all(isinstance(item, dict) and _record_ok(item) for item in skill_release_ledger)
        and [item.get("to_state") for item in skill_release_ledger]
        == ["EVALUATED", "SHADOW", "CANARY"]
        and summary.get("skill_release_receipt_digest")
        == skill_release_ledger[-1].get("digest"),
        "SKILL_RELEASE_LEDGER_INVALID",
    )
    _require(
        _record_ok(skill_invocation)
        and skill_invocation.get("authorization_mode") == "RELEASE"
        and skill_invocation.get("release_receipt_digest")
        == skill_release_ledger[-1].get("digest")
        and skill_invocation.get("package_digest") == skill_package_digest
        and summary.get("skill_authorization_mode") == "RELEASE",
        "SKILL_RELEASE_AUTHORIZATION_INVALID",
    )

    collaboration = state.get("competition_evidence", {}).get("agent_collaboration", {})
    orchestration_nodes = collaboration.get("orchestration_plan", {}).get("tasks", [])
    native_task_bindings = summary.get("task_bindings", [])
    runs = collaboration.get("agent_runs", [])
    reviewer_runs = _reviewer_runs(runs)
    _require(
        isinstance(native_task_bindings, list)
        and len(orchestration_nodes) == 8
        and len(native_task_bindings) == 7
        and len(orchestration_nodes) == len(native_task_bindings) + 1
        and len(runs) == 8,
        "AGENT_TASKFLOW_CARDINALITY_INVALID",
    )
    _require(
        orchestration_nodes[0].get("agent_name") == "change-coordinator"
        and orchestration_nodes[0].get("id") == summary.get("project_id")
        and {item.get("id") for item in orchestration_nodes[1:]}
        == {item.get("task_id") for item in native_task_bindings},
        "ORCHESTRATION_NODE_TASK_BINDING_INVALID",
    )
    _require(all(item.get("target_writes") == 0 for item in runs), "AGENT_CANONICAL_WRITE_OBSERVED")
    _require(len(reviewer_runs) == 2, "REVIEWER_PROCESS_COUNT_INVALID")
    expected_advisory_acceptance = {
        item["phase"]: item["accepted"]
        for item in model_facts["advisory_dispositions"]
    }
    _require(
        all(
            item.get("model_provider") == model_facts["provider"]
            and item.get("model_version")
            == (
                model_facts["model_id"]
                if model_facts["provider"] == "vertex-ai"
                else OLLAMA_EXPECTED_MODEL_VERSION
            )
            and item.get("model_evidence_class") == model_facts["evidence_class"]
            and item.get("model_claim_boundary")
            == (
                VERTEX_CLAIM_BOUNDARY
                if model_facts["provider"] == "vertex-ai"
                else "LOCAL_LOOPBACK_INFERENCE_NOT_PRODUCTION_PROVIDER"
            )
            and item.get("attempt") in expected_advisory_acceptance
            and item.get("model_advisory_accepted")
            is expected_advisory_acceptance[item.get("attempt")]
            for item in reviewer_runs
        ),
        "REVIEWER_MODEL_BINDING_INVALID",
    )
    reviewer_projection = collaboration.get("reviewer", {})
    _require(
        state.get("competition_evidence", {}).get("model_provider")
        == model_facts["provider"]
        and reviewer_projection.get("model_provider") == model_facts["provider"]
        and reviewer_projection.get("model_version") == model_facts["model_version"]
        and reviewer_projection.get("model_attempts")
        == summary.get("reviewer_model_attempts")
        and reviewer_projection.get("target_writes") == 0,
        "REVIEWER_MODEL_PROJECTION_INVALID",
    )
    _require(
        collaboration.get("reviewer", {}).get("attempt_1", {}).get("verdict") == "REPLAN"
        and collaboration.get("reviewer", {}).get("attempt_2", {}).get("verdict") == "PASS",
        "REVIEW_TRANSITION_INVALID",
    )
    _require(collaboration.get("tool", {}).get("status") == "SUCCEEDED", "TOOL_RECOVERY_NOT_PROVEN")
    _require(collaboration.get("skill", {}).get("action") == "APPLY_QUOTE", "SKILL_NOT_PROVEN")

    approvals = {
        kind: _approval_review(state, kind) for kind in ("launch_date", "currency")
    }
    public_state_facts = _public_state_facts(state, evidence, quote)
    experience_facts = _experience_facts(
        root,
        run_id=run_id,
        correlation_id=correlation_id,
        summary=summary,
        state=state,
        evidence=evidence,
    )

    # SQLite deletion is insufficient redaction unless followed by VACUUM.
    # Public evidence therefore excludes the entire runtime database and its
    # sidecars before the content-addressed file index is computed.
    _remove_runtime_database_files(root)
    _require_no_private_task_intake_projection(root)
    entries = _entries(root)
    body = {
        "schema_version": "orgrebase.golden-pilot-evidence-manifest.v3",
        "status": "PASS",
        "evidence_class": "CONTROLLED_LOCAL_GOLDEN_COMPETITION",
        "maturity": "VALIDATED_CONTROLLED_LOCAL_PILOT",
        "run_id": run_id,
        "correlation_id": correlation_id,
        "scenario_id": state.get("scenario", {}).get("id"),
        "quote_ref": f"{quote['quote']['id']}@{quote['quote']['version']}",
        "workflow": {
            "agentteams_action_count": summary.get("agentteams_action_count"),
            "orchestration_node_count": len(orchestration_nodes),
            "native_task_binding_count": len(native_task_bindings),
            "independent_domain_worker_processes": summary.get(
                "independent_domain_worker_processes"
            ),
            "independent_reviewer_processes": summary.get("independent_reviewer_processes"),
            "review_transition": ["REPLAN", "PASS"],
            "model": model_facts,
            "tool_status": "SUCCEEDED",
            "skill_action": "APPLY_QUOTE",
            "skill_authorization_mode": "RELEASE",
            "skill_release_state": "CANARY",
            "skill_evaluation_partition_count": 8,
            **({"skill_evaluation_case_count": len(skill_evaluation["case_results"])}
               if "skill_evaluation_case_count" in summary else {}),
            "human_approval_count": 2,
            "approval_review_evidence": approvals,
            "public_state_closure": public_state_facts,
            "experience_governance": experience_facts,
        },
        "privacy": {
            "runtime_database_included": False,
            "forbidden_runtime_database_names": sorted(
                FORBIDDEN_RUNTIME_DATABASE_NAMES
            ),
            "verification_source": "CONTENT_ADDRESSED_PUBLIC_JSON_PROJECTIONS",
        },
        "authority": {
            "agents_reviewer_tool_skill": "CANDIDATE_ONLY_ZERO_CANONICAL_WRITES",
            "canonical_writer": "OrgRebase StateStore and RebaseWorkflow",
            "canonical_target_writes_before_control_commit": 0,
        },
        "claim_boundary": summary.get("claim_boundary"),
        "promotion_blockers": [
            "AUTONOMOUS_OR_DISTRIBUTED_AGENTTEAMS_WORKERS",
            "GOLDEN_KILLED_PROCESS_DURABLE_RESUME",
            "FULL_REBUILD_EXECUTOR_AND_REALIZED_SAVING_RATE",
            "REAL_ENTERPRISE_CONNECTORS_DATA_AND_ROI",
            "EXTERNAL_IAM_AND_HUMAN_UAT",
            "PRODUCTION_SLA_HA_GEO_DR_AND_MULTITENANCY",
        ],
        "files": {
            "entry_count": len(entries),
            "pack_digest": sha256_digest(entries),
            "entries": entries,
        },
    }
    return {**body, "digest": sha256_digest(body)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_manifest(args.root)
    output = args.root.resolve() / "manifest.json"
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: manifest[key] for key in ("status", "run_id", "quote_ref", "digest")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
