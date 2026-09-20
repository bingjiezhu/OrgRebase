#!/usr/bin/env python3
"""Independent closed-world verifier for Agent-assisted OAC intake evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
SUPPORTED_VERTEX_MODEL_IDS = frozenset(
    {"gemini-3.7-flash", "gemini-3.8-flash"}
)
EXPECTED_ACTIONS = (
    "create_project",
    "plan_dag",
    "ready_nodes",
    "delegate_task",
    "ack_task",
    "submit_task",
    "check_task",
    "accept_task_result",
    "complete_project",
)
SECRET_MARKERS = (
    b"/" + b"Users/",
    b"/private/tmp/",
    b"/var/folders/",
    b"Authorization: Bearer ",
    b'"Authorization":"Bearer ',
    b"ya29.",
    b"-----BEGIN PRIVATE KEY-----",
)


class VerificationError(RuntimeError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"JSON_INVALID:{path.name}") from exc
    if not isinstance(value, dict):
        raise VerificationError(f"JSON_OBJECT_REQUIRED:{path.name}")
    return value


def _verify_content_record(value: dict[str, Any], name: str) -> None:
    observed = value.get("digest")
    if not isinstance(observed, str) or not DIGEST.fullmatch(observed):
        raise VerificationError(f"DIGEST_MISSING:{name}")
    payload = dict(value)
    payload.pop("digest", None)
    if _digest(payload) != observed:
        raise VerificationError(f"DIGEST_MISMATCH:{name}")


def _verify_manifest(root: Path) -> dict[str, Any]:
    manifest = _load(root / "manifest.json")
    _verify_content_record(manifest, "manifest")
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise VerificationError("MANIFEST_ENTRIES_INVALID")
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path != root / "manifest.json"
    }
    declared_paths = {str(item.get("path")) for item in entries if isinstance(item, dict)}
    if actual_paths != declared_paths or manifest.get("entry_count") != len(entries):
        raise VerificationError("MANIFEST_CLOSED_WORLD_MISMATCH")
    for item in entries:
        if not isinstance(item, dict):
            raise VerificationError("MANIFEST_ENTRY_INVALID")
        path = root / str(item.get("path"))
        if _file_digest(path) != item.get("sha256") or path.stat().st_size != item.get("bytes"):
            raise VerificationError(f"MANIFEST_ENTRY_MISMATCH:{path.name}")
    if manifest.get("pack_digest") != _digest(entries):
        raise VerificationError("MANIFEST_PACK_DIGEST_MISMATCH")
    return manifest


def _verify_raw_actions(root: Path, actions: list[dict[str, Any]]) -> None:
    if tuple(item.get("action") for item in actions) != EXPECTED_ACTIONS:
        raise VerificationError("AGENTTEAMS_ACTION_SEQUENCE_MISMATCH")
    if [item.get("sequence") for item in actions] != list(range(1, 10)):
        raise VerificationError("AGENTTEAMS_ACTION_SEQUENCE_NUMBER_MISMATCH")
    for action in actions:
        _verify_content_record(action, f"action:{action.get('sequence')}")
        raw_ref = action.get("raw_ref")
        if not isinstance(raw_ref, str) or not raw_ref.startswith("raw-mcp/"):
            raise VerificationError("AGENTTEAMS_RAW_REF_INVALID")
        raw = _load(root / raw_ref)
        if raw.get("action") != action.get("action"):
            raise VerificationError("AGENTTEAMS_RAW_ACTION_MISMATCH")
        request = raw.get("request")
        response = raw.get("response")
        if not isinstance(request, dict) or not isinstance(response, dict):
            raise VerificationError("AGENTTEAMS_RAW_REQUEST_RESPONSE_INVALID")
        if _digest(request) != action.get("request_digest"):
            raise VerificationError("AGENTTEAMS_REQUEST_DIGEST_MISMATCH")
        if _digest(response) != action.get("response_digest"):
            raise VerificationError("AGENTTEAMS_RESPONSE_DIGEST_MISMATCH")
        try:
            payload = json.loads(response["content"][0]["text"])
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise VerificationError("AGENTTEAMS_PAYLOAD_INVALID") from exc
        if _digest(payload) != action.get("payload_digest"):
            raise VerificationError("AGENTTEAMS_PAYLOAD_DIGEST_MISMATCH")
        if payload.get("ok") is not True:
            raise VerificationError("AGENTTEAMS_ACTION_NOT_OK")


def _verify_live_model_observation(observation: dict[str, Any]) -> None:
    if observation.get("provider") != "vertex-ai":
        raise VerificationError("LIVE_MODEL_PROVIDER_INVALID")
    model_id = observation.get("model_id")
    if model_id not in SUPPORTED_VERTEX_MODEL_IDS:
        raise VerificationError("LIVE_MODEL_ID_UNSUPPORTED")
    if observation.get("model_version") != model_id:
        raise VerificationError("LIVE_MODEL_VERSION_MISMATCH")
    if observation.get("status") != "VALID":
        raise VerificationError("LIVE_MODEL_STATUS_INVALID")
    if observation.get("evidence_class") != "LIVE_MODEL":
        raise VerificationError("LIVE_MODEL_EVIDENCE_CLASS_INVALID")
    request_id = observation.get("provider_request_id")
    if not isinstance(request_id, str) or not request_id.strip():
        raise VerificationError("LIVE_MODEL_PROVIDER_REQUEST_ID_INVALID")


def _agentteams_source(lock_path: Path | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Check the same release files without importing the receipt producer."""
    assets = Path(__file__).resolve().parents[1] / "agentteams"
    lock = _load(lock_path if lock_path is not None else assets / "teamharness-lock.json")
    source = (
        _load(assets / "source-lock.json")
        if lock_path is None
        else {"schema_version": "orgrebase.agentteams-source-lock.v1",
              "repository": lock.get("upstream"), "tag": lock.get("tag"), "commit": lock.get("commit")}
    )
    if (
        source.get("schema_version") != "orgrebase.agentteams-source-lock.v1"
        or source.get("repository") != "https://github.com/agentscope-ai/AgentTeams"
        or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", str(source.get("tag", "")))
        or not re.fullmatch(r"[0-9a-f]{40}", str(source.get("commit", "")))
        or lock.get("schema_version") != "orgrebase.teamharness-source-lock.v1"
        or (source.get("repository"), source.get("tag"), source.get("commit"))
        != (lock.get("upstream"), lock.get("tag"), lock.get("commit"))
    ):
        raise VerificationError("AGENTTEAMS_SOURCE_IDENTITY_MISMATCH")
    return source, lock


def verify(root: Path, *, lock_path: Path | None = None) -> dict[str, Any]:
    root = root.expanduser().resolve()
    manifest = _verify_manifest(root)
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        raw = path.read_bytes()
        if any(marker in raw for marker in SECRET_MARKERS):
            raise VerificationError(f"SECRET_OR_PRIVATE_PATH_FOUND:{path.name}")

    receipt = _load(root / "mapping-receipt.json")
    observation = _load(root / "model-observation.json")
    validation = _load(root / "validation.json")
    baseline = _load(root / "deterministic-baseline.json")
    draft = _load(root / "quote-adaptation-draft.json")
    for value, name in (
        (receipt, "mapping-receipt"),
        (observation, "model-observation"),
        (validation, "validation"),
    ):
        _verify_content_record(value, name)
    if receipt.get("schema_version") != "orgrebase.oac-agent-mapping-receipt.v1":
        raise VerificationError("RECEIPT_SCHEMA_VERSION_INVALID")
    if receipt.get("candidate_only") is not True or receipt.get("canonical_target_writes") != 0:
        raise VerificationError("RECEIPT_EFFECT_BOUNDARY_INVALID")
    if receipt.get("human_approval_granted") is not False or receipt.get("oac_source_admitted") is not False:
        raise VerificationError("RECEIPT_AUTHORITY_BOUNDARY_INVALID")
    if receipt.get("model_observation") != observation or receipt.get("validation") != validation:
        raise VerificationError("RECEIPT_NESTED_RECORD_SUBSTITUTION")

    mappings = baseline.get("mappings")
    if not isinstance(mappings, list) or len(mappings) != 5:
        raise VerificationError("BASELINE_MAPPING_SET_INVALID")
    for index, mapping in enumerate(mappings):
        if not isinstance(mapping, dict):
            raise VerificationError("BASELINE_MAPPING_INVALID")
        _verify_content_record(mapping, f"baseline-mapping:{index}")
    baseline_set_digest = _digest([item["digest"] for item in mappings])
    if (
        baseline.get("mapping_set_digest") != baseline_set_digest
        or receipt.get("baseline_mapping_set_digest") != baseline_set_digest
        or baseline.get("claim_boundary") != "KEYLESS_BASELINE_NOT_LIVE_AGENT_EVIDENCE"
    ):
        raise VerificationError("BASELINE_BINDING_INVALID")

    lifecycle = receipt.get("native_agentteams")
    if not isinstance(lifecycle, dict):
        raise VerificationError("AGENTTEAMS_LIFECYCLE_MISSING")
    _verify_content_record(lifecycle, "agentteams-lifecycle")
    source, source_lock = _agentteams_source(lock_path)
    source_verification = _load(root / "source-verification.json")
    if (
        lifecycle.get("run_id") != receipt.get("adaptation_run_id")
        or lifecycle.get("agentteams_version") != source["tag"]
        or lifecycle.get("agentteams_commit") != source["commit"]
        or lifecycle.get("source_lock_digest") != _digest(source_lock)
        or source_verification.get("source_lock_digest") != _digest(source_lock)
        or source_verification.get("commit") != source["commit"]
        or source_verification.get("origin") != source["repository"]
        or source_verification.get("files") != source_lock.get("source_files")
        or lifecycle.get("project_terminal_state") != "completed"
        or lifecycle.get("submitted_result_digest") != lifecycle.get("observed_result_digest")
        or lifecycle.get("canonical_target_writes") != 0
    ):
        raise VerificationError("AGENTTEAMS_LIFECYCLE_BINDING_INVALID")
    actions = lifecycle.get("actions")
    if not isinstance(actions, list):
        raise VerificationError("AGENTTEAMS_ACTIONS_INVALID")
    if tuple(lifecycle.get("action_sequence") or ()) != EXPECTED_ACTIONS:
        raise VerificationError("AGENTTEAMS_LIFECYCLE_SEQUENCE_INVALID")
    _verify_raw_actions(root, actions)

    status = receipt.get("status")
    accepted = receipt.get("accepted_mappings")
    if not isinstance(accepted, list):
        raise VerificationError("ACCEPTED_MAPPINGS_INVALID")
    if status == "VALIDATED_CANDIDATE":
        _verify_live_model_observation(observation)
        if (
            validation.get("verdict") != "PASS"
            or len(accepted) != 5
            or draft.get("agent_mapping", {}).get("receipt_digest") != receipt.get("digest")
        ):
            raise VerificationError("LIVE_VALIDATED_RECEIPT_SHAPE_INVALID")
        for index, mapping in enumerate(accepted):
            if not isinstance(mapping, dict):
                raise VerificationError("ACCEPTED_MAPPING_INVALID")
            _verify_content_record(mapping, f"accepted-mapping:{index}")
            if mapping.get("canonical_target_writes") != 0:
                raise VerificationError("ACCEPTED_MAPPING_WRITE_INVALID")
        mapping_set_digest = _digest([item["digest"] for item in accepted])
        if (
            receipt.get("accepted_mapping_set_digest") != mapping_set_digest
            or validation.get("accepted_mapping_set_digest") != mapping_set_digest
            or draft.get("mapping_set_digest") != mapping_set_digest
        ):
            raise VerificationError("ACCEPTED_MAPPING_SET_BINDING_INVALID")
    elif status == "HOLD":
        if (
            validation.get("verdict") != "HOLD"
            or accepted
            or receipt.get("accepted_mapping_set_digest") is not None
            or draft.get("agent_mapping") is not None
        ):
            raise VerificationError("HOLD_RECEIPT_SHAPE_INVALID")
    else:
        raise VerificationError("RECEIPT_STATUS_INVALID")
    if draft.get("canonical_target_writes") != 0:
        raise VerificationError("DRAFT_WRITE_BOUNDARY_INVALID")
    gate = draft.get("review_gate")
    if gate is not None and (
        gate.get("review_duration_ms", 0) < 4000
        or gate.get("not_before_epoch_ms", 0) - gate.get("prepared_at_epoch_ms", 0) < 4000
    ):
        raise VerificationError("FOUR_SECOND_REVIEW_GATE_INVALID")
    return {
        "status": "PASS",
        "mapping_status": status,
        "adaptation_run_id": receipt.get("adaptation_run_id"),
        "receipt_digest": receipt.get("digest"),
        "model_status": observation.get("status"),
        "model_provider": observation.get("provider"),
        "model_id": observation.get("model_id"),
        "model_version": observation.get("model_version"),
        "model_evidence_class": observation.get("evidence_class"),
        "provider_request_id_present": bool(observation.get("provider_request_id")),
        "native_action_count": len(actions),
        "canonical_target_writes": receipt.get("canonical_target_writes"),
        "manifest_digest": manifest.get("digest"),
        "agentteams_version": source["tag"],
        "agentteams_commit": source["commit"],
        "source_lock_digest": _digest(source_lock),
        "source_verification_scope": "EXPLICIT_RETAINED_LOCK" if lock_path is not None else "ACTIVE_SOURCE",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--lock", type=Path, help="Exact retained source lock for historical evidence; defaults to the active lock")
    args = parser.parse_args()
    try:
        result = verify(args.evidence, lock_path=args.lock)
    except VerificationError as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
