#!/usr/bin/env python3
"""Independently verify one Enterprise Quote Pilot evidence directory.

The verifier deliberately uses only the Python standard library for evidence
replay.  It does not call the Pilot runner, trust ``summary.status``, or import
the canonical StateStore.  When ``--pack`` is supplied, the separately loaded
Pack must bind to the same exact digests as the evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

EXPECTED_ARTIFACTS = {
    "backup-restore-receipt.json",
    "evidence-export.json",
    "pilot-loop.json",
    "preflight.json",
    "quote-export.json",
    "summary.json",
}
EXPECTED_MATURITY = "PILOT_READY_CONTROLLED_LOCAL"
MAX_JSON_BYTES = 64 * 1024 * 1024
GENESIS_DIGEST = "sha256:" + "0" * 64


class PilotEvidenceError(ValueError):
    """Stable, human-readable evidence verification failure."""


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


def _raw_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise PilotEvidenceError(code)


def _load_json(path: Path) -> dict[str, Any]:
    _require(path.is_file() and not path.is_symlink(), f"EVIDENCE_FILE_INVALID:{path.name}")
    raw = path.read_bytes()
    _require(len(raw) <= MAX_JSON_BYTES, f"EVIDENCE_JSON_TOO_LARGE:{path.name}")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise PilotEvidenceError(f"EVIDENCE_JSON_DUPLICATE_KEY:{path.name}:{key}")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=reject_duplicates)
    except PilotEvidenceError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PilotEvidenceError(f"EVIDENCE_JSON_INVALID:{path.name}") from exc
    _require(isinstance(value, dict), f"EVIDENCE_JSON_ROOT_NOT_OBJECT:{path.name}")
    return value


def _verify_record_digest(value: dict[str, Any], code: str, *, ignore_state: bool = False) -> None:
    expected = value.get("digest")
    payload = {
        key: item
        for key, item in value.items()
        if key != "digest" and (not ignore_state or key != "state")
    }
    _require(isinstance(expected, str) and expected == _digest(payload), code)


def _safe_manifest_artifacts(root: Path, manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    artifacts = manifest.get("artifacts")
    _require(isinstance(artifacts, dict), "MANIFEST_ARTIFACTS_INVALID")
    _require(set(artifacts) == EXPECTED_ARTIFACTS, "MANIFEST_ARTIFACT_SET_INVALID")
    normalized: dict[str, dict[str, Any]] = {}
    for name in sorted(EXPECTED_ARTIFACTS):
        record = artifacts.get(name)
        _require(isinstance(record, dict), f"MANIFEST_ARTIFACT_INVALID:{name}")
        _require(set(record) == {"digest", "path"}, f"MANIFEST_ARTIFACT_FIELDS_INVALID:{name}")
        _require(record.get("path") == name, f"MANIFEST_ARTIFACT_PATH_INVALID:{name}")
        selected = root / name
        _require(selected.parent == root, f"MANIFEST_ARTIFACT_TRAVERSAL:{name}")
        value = _load_json(selected)
        _require(record.get("digest") == _digest(value), f"MANIFEST_ARTIFACT_DIGEST:{name}")
        normalized[name] = value
    return normalized


def _verify_change(
    kind: str,
    change: dict[str, Any],
    command: dict[str, Any],
    summary: dict[str, Any],
    workflow_run_id: str,
) -> None:
    required = {
        "change_spec",
        "change_set",
        "preview",
        "minimal_rebase_certificate",
        "approval",
        "rebase_receipt",
        "workspace_rebase_receipt",
        "quote",
        "graph_pointer",
    }
    _require(required <= set(change), f"CHANGE_RECORD_INCOMPLETE:{kind}")
    for key in (
        "change_spec",
        "change_set",
        "preview",
        "minimal_rebase_certificate",
        "approval",
        "rebase_receipt",
        "workspace_rebase_receipt",
    ):
        selected = change[key]
        _require(isinstance(selected, dict), f"CHANGE_RECORD_INVALID:{kind}:{key}")
        _verify_record_digest(selected, f"CHANGE_RECORD_DIGEST:{kind}:{key}")
    for key in ("quote", "graph_pointer"):
        selected = change[key]
        _require(isinstance(selected, dict), f"CHANGE_RECORD_INVALID:{kind}:{key}")
        _verify_record_digest(selected, f"CHANGE_RECORD_DIGEST:{kind}:{key}", ignore_state=True)

    spec = change["change_spec"]
    change_set = change["change_set"]
    preview = change["preview"]
    certificate = change["minimal_rebase_certificate"]
    approval = change["approval"]
    receipt = change["rebase_receipt"]
    _require(command.get("kind") == kind, f"COMMAND_KIND:{kind}")
    _require(command.get("mode") == "EXPLICIT_OWNER_COMMAND", f"COMMAND_MODE:{kind}")
    _require(command.get("apply_status") == "COMPLETED", f"COMMAND_APPLY_STATUS:{kind}")
    _require(spec.get("owner_id") == change_set.get("owner_id"), f"OWNER_SPEC_SET:{kind}")
    _require(approval.get("actor_id") == spec.get("owner_id"), f"OWNER_APPROVAL:{kind}")
    _require(command.get("actor_id") == approval.get("actor_id"), f"OWNER_COMMAND:{kind}")
    _require(receipt.get("approval_actor_id") == approval.get("actor_id"), f"OWNER_RECEIPT:{kind}")
    _require(command.get("preview_digest") == preview.get("digest"), f"PREVIEW_COMMAND:{kind}")
    _require(approval.get("preview_digest") == preview.get("digest"), f"PREVIEW_APPROVAL:{kind}")
    _require(certificate.get("preview_digest") == preview.get("digest"), f"PREVIEW_CERTIFICATE:{kind}")
    _require(approval.get("change_set_digest") == change_set.get("digest"), f"CHANGESET_APPROVAL:{kind}")
    _require(certificate.get("change_set_digest") == change_set.get("digest"), f"CHANGESET_CERTIFICATE:{kind}")
    _require(command.get("approval_digest") == approval.get("digest"), f"APPROVAL_COMMAND:{kind}")
    _require(receipt.get("approval_digest") == approval.get("digest"), f"APPROVAL_RECEIPT:{kind}")
    _require(receipt.get("workflow_run_id") == workflow_run_id, f"CHANGE_RUN_ID:{kind}")
    _require(receipt.get("status") == "COMPLETED", f"CHANGE_RECEIPT_STATUS:{kind}")

    wrong = command.get("wrong_owner_probe")
    _require(isinstance(wrong, dict), f"WRONG_OWNER_PROBE_MISSING:{kind}")
    _require(wrong == summary.get("wrong_owner_probes", {}).get(kind), f"WRONG_OWNER_SUMMARY:{kind}")
    _require(wrong.get("status") == "REJECTED", f"WRONG_OWNER_STATUS:{kind}")
    _require(wrong.get("canonical_target_writes") == 0, f"WRONG_OWNER_WROTE:{kind}")
    _require(wrong.get("actor_id") != approval.get("actor_id"), f"WRONG_OWNER_ACTOR:{kind}")
    _require(
        str(wrong.get("error_code", "")).split(":", 1)[0] == "WORKSPACE_APPROVER_MISMATCH",
        f"WRONG_OWNER_ERROR:{kind}",
    )

    stale = command.get("stale_approval_probe")
    _require(isinstance(stale, dict), f"STALE_APPROVAL_PROBE_MISSING:{kind}")
    _require(stale == summary.get("stale_approval_probes", {}).get(kind), f"STALE_SUMMARY:{kind}")
    _require(stale.get("status") == "REJECTED", f"STALE_STATUS:{kind}")
    _require(stale.get("canonical_target_writes") == 0, f"STALE_WROTE:{kind}")
    _require(stale.get("error_code") == "WORKSPACE_APPROVAL_DIGEST_MISMATCH", f"STALE_ERROR:{kind}")
    _require(stale.get("before_counts") == stale.get("after_counts"), f"STALE_COUNTS_CHANGED:{kind}")
    _require(stale.get("rejected_digest") != approval.get("digest"), f"STALE_DIGEST_ACCEPTED:{kind}")


def _verify_data_classification(
    *,
    manifest: dict[str, Any],
    summary: dict[str, Any],
    preflight: dict[str, Any],
    quote_export: dict[str, Any],
    evidence_export: dict[str, Any],
) -> None:
    """Bind synthetic/enterprise-shaped input claims without assuming either one."""

    scenario = quote_export.get("scenario")
    profile = quote_export.get("enterprise_seed_profile")
    runtime_binding = quote_export.get("enterprise_seed_runtime_binding")
    _require(isinstance(scenario, dict), "DATA_SCENARIO_INVALID")
    _require(isinstance(profile, dict), "DATA_PROFILE_INVALID")
    _require(isinstance(runtime_binding, dict), "DATA_RUNTIME_BINDING_INVALID")
    _require(evidence_export.get("scenario") == scenario, "DATA_SCENARIO_EXPORT_MISMATCH")
    _require(
        evidence_export.get("enterprise_seed_profile") == profile,
        "DATA_PROFILE_EXPORT_MISMATCH",
    )

    data_class = profile.get("data_class")
    synthetic = scenario.get("synthetic")
    _require(
        data_class in {"SYNTHETIC_FIXTURE", "PUBLIC", "INTERNAL", "CONFIDENTIAL"},
        "DATA_CLASS_INVALID",
    )
    _require(isinstance(synthetic, bool), "DATA_SYNTHETIC_FLAG_INVALID")
    _require(
        synthetic == (data_class == "SYNTHETIC_FIXTURE"),
        "DATA_SYNTHETIC_CLASS_MISMATCH",
    )
    limitations = profile.get("limitations")
    _require(isinstance(limitations, list), "DATA_PROFILE_LIMITATIONS_INVALID")
    _require(
        ("SYNTHETIC_DATA_ONLY" in limitations) == synthetic,
        "DATA_SYNTHETIC_LIMITATION_MISMATCH",
    )
    _require(
        "NO_EXTERNAL_ENTERPRISE_VALIDATION" in limitations,
        "DATA_ENTERPRISE_VALIDATION_LIMITATION_MISSING",
    )

    organization_id = profile.get("organization_id")
    _require(isinstance(organization_id, str) and organization_id, "DATA_ORGANIZATION_INVALID")
    _require(scenario.get("organization_id") == organization_id, "DATA_ORGANIZATION_SCENARIO")
    _require(summary.get("organization_id") == organization_id, "DATA_ORGANIZATION_SUMMARY")
    _require(preflight.get("organization_id") == organization_id, "DATA_ORGANIZATION_PREFLIGHT")
    _require(profile.get("profile_ref") == summary.get("profile_ref"), "DATA_PROFILE_REF_SUMMARY")
    _require(profile.get("digest") == summary.get("profile_digest"), "DATA_PROFILE_DIGEST_SUMMARY")
    _require(scenario.get("pack_digest") == summary.get("pack_digest"), "DATA_PACK_SCENARIO")

    for record, label in (
        (manifest, "MANIFEST"),
        (summary, "SUMMARY"),
        (preflight, "PREFLIGHT"),
    ):
        _require(record.get("data_class") == data_class, f"DATA_CLASS_{label}")
        _require(record.get("synthetic") is synthetic, f"DATA_SYNTHETIC_{label}")
        boundaries = record.get("boundaries") if label != "MANIFEST" else None
        if boundaries is not None:
            _require(
                isinstance(boundaries, dict) and boundaries.get("data_profile") == data_class,
                f"DATA_BOUNDARY_{label}",
            )

    binding_profile = runtime_binding.get("binding", {}).get("profile", {})
    _require(
        isinstance(binding_profile, dict)
        and binding_profile.get("data_class") == data_class
        and binding_profile.get("organization_id") == organization_id,
        "DATA_RUNTIME_BINDING_MISMATCH",
    )


def _sqlite_projection(connection: sqlite3.Connection) -> dict[str, list[list[Any]]]:
    tables = [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    projection: dict[str, list[list[Any]]] = {}
    for table in tables:
        quoted = '"' + table.replace('"', '""') + '"'
        projection[table] = [
            list(row) for row in connection.execute(f"SELECT * FROM {quoted} ORDER BY rowid")
        ]
    return projection


def _verify_sqlite(
    path: Path,
    *,
    expected_quote: dict[str, Any],
    expected_event_chain: dict[str, Any],
    expected_changes: dict[str, tuple[dict[str, Any], dict[str, Any]]],
) -> tuple[str, int, int]:
    _require(path.is_file() and not path.is_symlink(), f"SQLITE_FILE_INVALID:{path.name}")
    try:
        connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro&immutable=1", uri=True)
        connection.row_factory = sqlite3.Row
        integrity = connection.execute("PRAGMA integrity_check").fetchall()
        _require([row[0] for row in integrity] == ["ok"], f"SQLITE_INTEGRITY:{path.name}")
        projection = _sqlite_projection(connection)

        event_columns = {row[1] for row in connection.execute('PRAGMA table_info("domain_events")')}
        old_event_columns = {"sequence_no", "event_type", "payload_json", "previous_digest", "event_digest"}
        scoped = "workspace_id" in event_columns
        _require(event_columns == (old_event_columns | {"workspace_id", "subject_key"} if scoped else old_event_columns),
                 f"SQLITE_EVENT_SCHEMA_UNSUPPORTED:{path.name}")
        if scoped:
            versions = connection.execute("SELECT schema_version FROM store_metadata").fetchall()
            # Versions 3/4/5 share the scoped evidence layout. Version 4 changes
            # effect target projections and version 5 adds non-unique effect
            # indexes; neither changes these event/object content hashes.
            declared = [row[0] for row in versions]
            _require(declared in ([3], [4], [5]), f"SQLITE_SCHEMA_VERSION_UNSUPPORTED:{path.name}")
            user_version = connection.execute("PRAGMA user_version").fetchone()[0]
            _require(user_version == declared[0], f"SQLITE_SCHEMA_VERSION_MISMATCH:{path.name}")
            for table in ("domain_events", "artifacts", "object_versions", "current_pointers", "version_states", "workspace_registry"):
                _require(connection.execute(f'SELECT count(*) FROM "{table}" WHERE workspace_id != ?', ("default",)).fetchone()[0] == 0,
                         f"SQLITE_WORKSPACE_SCOPE_UNSUPPORTED:{path.name}")
        scope_filter = " WHERE workspace_id = 'default'" if scoped else ""

        previous = GENESIS_DIGEST
        events = connection.execute("SELECT sequence_no, event_type, payload_json, previous_digest, event_digest "
                                    "FROM domain_events" + scope_filter + " ORDER BY sequence_no").fetchall()
        for expected_sequence, row in enumerate(events, start=1):
            sequence_no, event_type, payload_json, previous_digest, event_digest = row
            _require(sequence_no == expected_sequence, f"SQLITE_EVENT_SEQUENCE:{path.name}")
            _require(previous_digest == previous, f"SQLITE_EVENT_PREDECESSOR:{path.name}")
            envelope = {
                "sequence_no": expected_sequence,
                "event_type": event_type,
                "payload": json.loads(payload_json),
                "previous_digest": previous,
            }
            _require(event_digest == _digest(envelope), f"SQLITE_EVENT_DIGEST:{path.name}")
            previous = event_digest
        _require(len(events) == expected_event_chain.get("events"), f"SQLITE_EVENT_COUNT:{path.name}")
        _require(previous == expected_event_chain.get("head_digest"), f"SQLITE_EVENT_HEAD:{path.name}")
        if scoped:
            heads = connection.execute("SELECT audit_sequence, audit_head FROM workspace_registry WHERE workspace_id = 'default'").fetchall()
            _require(len(heads) == 1 and tuple(heads[0]) == (len(events), previous), f"SQLITE_DERIVED_EVENT_HEAD:{path.name}")

        artifact_rows = {str(row[0]): row for row in connection.execute(
            "SELECT artifact_id, media_type, payload_json, payload_digest FROM artifacts" + scope_filter)}
        for row in artifact_rows.values():
            _artifact_id, _media_type, payload_json, payload_digest = row
            _require(payload_digest == _digest(json.loads(payload_json)), f"SQLITE_ARTIFACT_DIGEST:{path.name}")
        for row in connection.execute("SELECT version_key, object_id, version, payload_json, payload_digest FROM object_versions" + scope_filter):
            _version_key, _object_id, _version, payload_json, payload_digest = row
            payload = json.loads(payload_json)
            object_payload = {key: item for key, item in payload.items() if key not in {"digest", "state"}}
            _require(payload.get("digest") == _digest(object_payload), f"SQLITE_OBJECT_DIGEST:{path.name}")
            _require(payload_digest == payload.get("digest"), f"SQLITE_OBJECT_ROW_DIGEST:{path.name}")

        for kind, (change, command) in expected_changes.items():
            preview_row = artifact_rows.get(str(command.get("preview_artifact_ref")))
            approval_row = artifact_rows.get(str(command.get("approval_artifact_ref")))
            outcome_row = artifact_rows.get(str(command.get("outcome_artifact_ref")))
            _require(preview_row is not None, f"SQLITE_PREVIEW_ARTIFACT_MISSING:{path.name}:{kind}")
            _require(approval_row is not None, f"SQLITE_APPROVAL_ARTIFACT_MISSING:{path.name}:{kind}")
            _require(outcome_row is not None, f"SQLITE_OUTCOME_ARTIFACT_MISSING:{path.name}:{kind}")
            _require(
                preview_row[3] == command.get("preview_artifact_digest"),
                f"SQLITE_PREVIEW_ARTIFACT_DIGEST:{path.name}:{kind}",
            )
            _require(
                approval_row[3] == command.get("approval_artifact_digest"),
                f"SQLITE_APPROVAL_ARTIFACT_DIGEST:{path.name}:{kind}",
            )
            _require(
                outcome_row[3] == command.get("outcome_artifact_digest"),
                f"SQLITE_OUTCOME_ARTIFACT_DIGEST:{path.name}:{kind}",
            )
            preview_payload = json.loads(preview_row[2])
            approval_payload = json.loads(approval_row[2])
            outcome_payload = json.loads(outcome_row[2])
            _require(preview_payload.get("preview") == change.get("preview"), f"SQLITE_PREVIEW:{path.name}:{kind}")
            _require(approval_payload == change.get("approval"), f"SQLITE_APPROVAL:{path.name}:{kind}")
            _require(outcome_payload.get("kind") == kind, f"SQLITE_OUTCOME_KIND:{path.name}:{kind}")
            _require(outcome_payload.get("quote") == change.get("quote"), f"SQLITE_OUTCOME_QUOTE:{path.name}:{kind}")
            _require(
                outcome_payload.get("approval_digest") == change.get("approval", {}).get("digest"),
                f"SQLITE_OUTCOME_APPROVAL:{path.name}:{kind}",
            )
            _require(
                outcome_payload.get("rebase_receipt") == change.get("rebase_receipt"),
                f"SQLITE_OUTCOME_RECEIPT:{path.name}:{kind}",
            )

        quote_id = expected_quote.get("id")
        join_scope = " AND o.workspace_id=p.workspace_id AND s.workspace_id=p.workspace_id" if scoped else ""
        pointer_scope = " AND p.workspace_id='default'" if scoped else ""
        row = connection.execute(
            "SELECT o.payload_json, s.state FROM current_pointers p "
            "JOIN object_versions o ON o.version_key=p.version_key "
            "JOIN version_states s ON s.version_key=p.version_key" + join_scope + " WHERE p.object_id=?" + pointer_scope,
            (quote_id,),
        ).fetchone()
        _require(row is not None, f"SQLITE_CURRENT_QUOTE_MISSING:{path.name}")
        persisted_quote = json.loads(row["payload_json"])
        persisted_quote["state"] = row["state"]
        _require(persisted_quote == expected_quote, f"SQLITE_CURRENT_QUOTE_MISMATCH:{path.name}")
        return (
            _digest(projection),
            len(projection.get("object_versions", [])),
            len(projection.get("artifacts", [])),
        )
    except (json.JSONDecodeError, sqlite3.DatabaseError) as exc:
        raise PilotEvidenceError(f"SQLITE_REPLAY_FAILED:{path.name}") from exc
    finally:
        if "connection" in locals():
            connection.close()


def _verify_pack(pack_root: Path, summary: dict[str, Any], preflight: dict[str, Any]) -> dict[str, Any]:
    _require(pack_root.is_dir() and not pack_root.is_symlink(), "PACK_ROOT_INVALID")
    for path in pack_root.rglob("*"):
        _require(not path.is_symlink(), f"PACK_SYMLINK_FORBIDDEN:{path.relative_to(pack_root)}")
    try:
        from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack

        runtime = load_enterprise_quote_pilot_pack(pack_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise PilotEvidenceError(f"PACK_STRICT_ADMISSION_FAILED:{exc}") from exc
    _require(runtime.pack_digest == summary.get("pack_digest"), "PACK_SUMMARY_DIGEST")
    _require(runtime.pack_digest == preflight.get("pack_digest"), "PACK_PREFLIGHT_DIGEST")
    _require(runtime.profile.digest == summary.get("profile_digest"), "PACK_PROFILE_DIGEST")
    _require(runtime.profile.ref == summary.get("profile_ref"), "PACK_PROFILE_REF")
    _require(runtime.profile.organization_id == summary.get("organization_id"), "PACK_ORGANIZATION")
    return {
        "pack_digest": runtime.pack_digest,
        "profile_digest": runtime.profile.digest,
        "source_root_count": len(runtime.source_admission.root_observations),
    }


def verify_evidence(evidence_root: Path, *, pack_root: Path | None = None) -> dict[str, Any]:
    root = evidence_root.resolve(strict=True)
    _require(root.is_dir() and not evidence_root.is_symlink(), "EVIDENCE_ROOT_INVALID")
    manifest = _load_json(root / "manifest.json")
    _require(
        manifest.get("schema_version") == "orgrebase.enterprise-quote-pilot-evidence-manifest.v1",
        "MANIFEST_SCHEMA_VERSION",
    )
    artifacts = _safe_manifest_artifacts(root, manifest)
    summary = artifacts["summary.json"]
    loop = artifacts["pilot-loop.json"]
    preflight = artifacts["preflight.json"]
    quote_export = artifacts["quote-export.json"]
    evidence_export = artifacts["evidence-export.json"]
    backup_receipt = artifacts["backup-restore-receipt.json"]

    for name, value in (
        ("quote-export.json", quote_export),
        ("evidence-export.json", evidence_export),
        ("backup-restore-receipt.json", backup_receipt),
    ):
        _verify_record_digest(value, f"CONTENT_RECORD_DIGEST:{name}")

    workflow_run_id = manifest.get("workflow_run_id")
    _require(isinstance(workflow_run_id, str) and workflow_run_id, "WORKFLOW_RUN_ID_INVALID")
    _require(summary.get("workflow_run_id") == workflow_run_id, "RUN_ID_SUMMARY")
    _require(loop.get("workflow_run_id") == workflow_run_id, "RUN_ID_LOOP")
    _require(loop.get("run_id") == workflow_run_id, "RUN_ID_LOOP_ALIAS")
    _require(manifest.get("pack_digest") == summary.get("pack_digest"), "PACK_DIGEST_MANIFEST")
    _require(preflight.get("pack_digest") == summary.get("pack_digest"), "PACK_DIGEST_PREFLIGHT")
    _require(summary.get("status") == "PASS" and manifest.get("status") == "PASS", "STATUS_NOT_PASS")

    _require(summary.get("deployment_maturity") == EXPECTED_MATURITY, "SUMMARY_CLAIM_CEILING")
    _require(manifest.get("deployment_maturity") == EXPECTED_MATURITY, "MANIFEST_CLAIM_CEILING")
    _require(preflight.get("deployment_maturity") == "PREFLIGHT_PASSED", "PREFLIGHT_MATURITY")
    _require(
        preflight.get("claim_ceiling_after_acceptance") == EXPECTED_MATURITY,
        "PREFLIGHT_CLAIM_CEILING",
    )
    for claim_record, label in ((summary, "SUMMARY"), (manifest, "MANIFEST"), (preflight, "PREFLIGHT")):
        _require(claim_record.get("real_enterprise_validated") == "NOT_RUN", f"{label}_ENTERPRISE_CLAIM")
        _require(claim_record.get("production_ready") is False, f"{label}_PRODUCTION_CLAIM")
        _require(
            claim_record.get("external_enterprise_target_writes") == 0,
            f"{label}_EXTERNAL_WRITES",
        )
    boundaries = summary.get("boundaries", {})
    loop_boundaries = loop.get("boundaries", {})
    _require(
        all(loop_boundaries.get(key) == value for key, value in boundaries.items()),
        "BOUNDARIES_LOOP",
    )
    _require(loop_boundaries == quote_export.get("boundaries"), "BOUNDARIES_QUOTE_EXPORT")
    _require(loop_boundaries == evidence_export.get("boundaries"), "BOUNDARIES_EVIDENCE_EXPORT")
    _require(boundaries.get("external_writes") == "DISABLED", "BOUNDARY_EXTERNAL_WRITES")
    _require(boundaries.get("agents_are_candidate_only") is True, "BOUNDARY_CANDIDATE_ONLY")
    _require(
        boundaries.get("canonical_state_owner") == "OrgRebase StateStore and RebaseWorkflow",
        "BOUNDARY_CANONICAL_OWNER",
    )
    _verify_data_classification(
        manifest=manifest,
        summary=summary,
        preflight=preflight,
        quote_export=quote_export,
        evidence_export=evidence_export,
    )
    _require(preflight.get("source_root_count") == 5, "PREFLIGHT_SOURCE_ROOT_COUNT")
    _require(preflight.get("reference_runtime_compatible") is True, "PREFLIGHT_RUNTIME_COMPATIBILITY")

    commands = loop.get("approval_commands")
    _require(isinstance(commands, dict) and len(commands) == 2, "APPROVAL_COMMAND_SET")
    change_order = tuple(preflight.get("change_kinds", []))
    _require(set(commands) == set(change_order), "CHANGE_ORDER_PREFLIGHT")
    _require(change_order == ("launch_date", "currency"), "CHANGE_ORDER_UNSUPPORTED")
    _verify_change(
        "launch_date",
        loop["launch_change"],
        commands["launch_date"],
        summary,
        workflow_run_id,
    )
    _verify_change(
        "currency",
        loop["currency_change"],
        commands["currency"],
        summary,
        workflow_run_id,
    )

    quote_v2 = loop["launch_change"]["quote"]
    quote_v3 = loop["currency_change"]["quote"]
    final_quote = loop.get("final_quote")
    _require(loop.get("formation", {}).get("deliverable_ref") == f"{quote_v2.get('id')}@v1", "QUOTE_V1_FORMATION")
    _require(quote_v2.get("version") == "v2", "QUOTE_V2_VERSION")
    _require(quote_v2.get("payload", {}).get("rebased_from") == f"{quote_v2.get('id')}@v1", "QUOTE_V2_LINEAGE")
    _require(quote_v3.get("version") == "v3", "QUOTE_V3_VERSION")
    _require(quote_v3.get("payload", {}).get("rebased_from") == f"{quote_v3.get('id')}@v2", "QUOTE_V3_LINEAGE")
    _require(final_quote == quote_v3, "FINAL_QUOTE_LOOP")
    _require(summary.get("final_quote") == final_quote, "FINAL_QUOTE_SUMMARY")
    _require(summary.get("restored_quote") == final_quote, "FINAL_QUOTE_RESTORED")
    _require(quote_export.get("quote") == final_quote, "FINAL_QUOTE_EXPORT")
    _require(evidence_export.get("quote") == final_quote, "FINAL_QUOTE_EVIDENCE")

    restart = loop.get("restart", {})
    _require(restart == summary.get("restart"), "RESTART_SUMMARY")
    _require(restart.get("store_profile") == "FILE_BACKED_SQLITE", "RESTART_STORE_PROFILE")
    expected_stage = "CURRENT" if restart.get("workspace_state_schema_version") == "orgrebase.workspace-state.v2" else "QUOTE_V2"
    _require(restart.get("closed_stage") == expected_stage, "RESTART_CLOSED_STAGE")
    _require(restart.get("reopened_stage") == expected_stage, "RESTART_REOPENED_STAGE")
    _require(
        restart.get("state_digest_before_close") == restart.get("state_digest_after_reopen"),
        "RESTART_STATE_DIGEST",
    )
    _require(summary.get("final_state_digest") == summary.get("restored_state_digest"), "RESTORE_STATE_DIGEST")
    _require(backup_receipt == summary.get("backup_restore"), "BACKUP_SUMMARY_BINDING")
    _require(backup_receipt.get("status") == "PASS", "BACKUP_STATUS")
    _require(backup_receipt.get("production_sla_claimed") is False, "BACKUP_PRODUCTION_CLAIM")
    _require(
        backup_receipt.get("source_digest")
        == backup_receipt.get("backup_digest")
        == backup_receipt.get("restored_digest"),
        "BACKUP_LOGICAL_DIGESTS",
    )
    _require(
        backup_receipt.get("source_event_head") == backup_receipt.get("restored_event_head"),
        "BACKUP_EVENT_HEAD",
    )

    event_chain = loop.get("event_chain", {})
    _require(event_chain.get("status") == "PASS", "EVENT_CHAIN_STATUS")
    _require(event_chain == summary.get("event_chain"), "EVENT_CHAIN_SUMMARY")
    exported_chain = evidence_export.get("event_chain", {})
    _require(
        all(exported_chain.get(key) == value for key, value in event_chain.items()),
        "EVENT_CHAIN_EXPORT",
    )
    records = exported_chain.get("records", [])
    _require(isinstance(records, list) and len(records) == event_chain.get("events"), "EVENT_RECORD_COUNT")
    previous = GENESIS_DIGEST
    for expected_sequence, record in enumerate(records, start=1):
        _require(record.get("sequence_no") == expected_sequence, "EVENT_RECORD_SEQUENCE")
        _require(record.get("previous_digest") == previous, "EVENT_RECORD_PREDECESSOR")
        previous = record.get("event_digest")
    _require(previous == event_chain.get("head_digest"), "EVENT_RECORD_HEAD")
    backup_db = root / "workspace.backup.sqlite3"
    restored_db = root / "workspace.restored.sqlite3"
    _require(_raw_digest(backup_db) == _raw_digest(restored_db), "BACKUP_RESTORED_RAW_MISMATCH")
    backup_logical, backup_objects, backup_artifacts = _verify_sqlite(
        backup_db,
        expected_quote=final_quote,
        expected_event_chain=event_chain,
        expected_changes={
            "launch_date": (loop["launch_change"], commands["launch_date"]),
            "currency": (loop["currency_change"], commands["currency"]),
        },
    )
    restored_logical, restored_objects, restored_artifacts = _verify_sqlite(
        restored_db,
        expected_quote=final_quote,
        expected_event_chain=event_chain,
        expected_changes={
            "launch_date": (loop["launch_change"], commands["launch_date"]),
            "currency": (loop["currency_change"], commands["currency"]),
        },
    )
    _require(backup_logical == restored_logical == backup_receipt.get("backup_digest"), "SQLITE_LOGICAL_DIGEST")
    _require(
        backup_objects == restored_objects == backup_receipt.get("object_count"),
        "SQLITE_OBJECT_COUNT",
    )
    _require(
        backup_artifacts == restored_artifacts == backup_receipt.get("artifact_count"),
        "SQLITE_ARTIFACT_COUNT",
    )

    pack_verification = (
        _verify_pack(pack_root.resolve(strict=True), summary, preflight)
        if pack_root is not None
        else {"status": "NOT_REQUESTED"}
    )
    return {
        "schema_version": "orgrebase.enterprise-quote-pilot-verification.v1",
        "status": "PASS",
        "deployment_maturity": EXPECTED_MATURITY,
        "workflow_run_id": workflow_run_id,
        "pack_digest": summary.get("pack_digest"),
        "final_quote_ref": f"{final_quote.get('id')}@{final_quote.get('version')}",
        "wrong_owner_rejections": len(summary.get("wrong_owner_probes", {})),
        "stale_approval_rejections": len(summary.get("stale_approval_probes", {})),
        "restart_count": 1,
        "backup_restore": "PASS",
        "external_enterprise_target_writes": 0,
        "real_enterprise_validated": "NOT_RUN",
        "production_ready": False,
        "pack_verification": pack_verification,
        "verified_artifact_digests": {
            name: manifest["artifacts"][name]["digest"] for name in sorted(EXPECTED_ARTIFACTS)
        },
        "database_logical_digest": restored_logical,
    }


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Independently verify an Enterprise Quote Pilot evidence directory",
    )
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--pack", type=Path, help="also re-admit and bind the exact Enterprise Pack")
    parser.add_argument("--output", type=Path, help="write the verification receipt")
    args = parser.parse_args()
    try:
        result = verify_evidence(args.evidence, pack_root=args.pack)
    except (OSError, PilotEvidenceError, ValueError) as exc:
        result = {
            "schema_version": "orgrebase.enterprise-quote-pilot-verification.v1",
            "status": "FAIL",
            "reason": str(exc),
        }
        if args.output is not None:
            _write_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(2) from exc
    if args.output is not None:
        _write_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
