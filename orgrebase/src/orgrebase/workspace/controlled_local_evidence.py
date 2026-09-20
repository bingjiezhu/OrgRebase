"""Retained evidence producer for the controlled-local operations envelope.

The producer consumes already-verified AgentTeams and Skill receipt roots.  It
does not turn them into canonical business authority; it proves that Source,
Tool and OTLP protocol boundaries can be correlated with those roots in one
controlled-local run.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.domain import ContentAddressedModel, IntegrityError
from orgrebase.store import StateStore
from orgrebase.workspace.controlled_local import (
    CAUSAL_LAYER_ORDER,
    CONTROLLED_TIMING_CLASS,
    ControlledEnterpriseClient,
    ControlledEnterpriseServer,
    OtlpHTTPExporter,
    OtlpHTTPReceiver,
    TelemetryStore,
    build_joint_otlp,
    evaluate_run_alerts,
    run_capacity_smoke,
    run_sqlite_backup_restore_drill,
)

REQUIRED_LAYERS = CAUSAL_LAYER_ORDER
EVIDENCE_CLASS = "CONTROLLED_LOCAL_INTEGRATED_OPERATIONS"


class ControlledLocalEvidenceError(RuntimeError):
    """Fail-closed evidence generation error."""


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _dump(value: ContentAddressedModel) -> dict[str, Any]:
    return value.model_dump(mode="json")


def _file_digest(path: Path) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _record(value: dict[str, Any]) -> dict[str, Any]:
    return {**value, "digest": sha256_digest(value)}


def _build_sbom(
    *, lock_path: Path, pyproject_path: Path, artifact_paths: tuple[Path, ...]
) -> dict[str, Any]:
    # Keep the reusable generator as the single CycloneDX implementation.
    try:
        from scripts.generate_sbom import build_sbom
    except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
        from generate_sbom import build_sbom

    return build_sbom(
        lock_path=lock_path,
        pyproject_path=pyproject_path,
        artifacts=artifact_paths,
    )


def run_controlled_local_evidence(
    *,
    output_dir: str | Path,
    run_id: str,
    task_id: str,
    delegation_id: str,
    native_receipt_digest: str,
    skill_package_digest: str,
    skill_invocation_receipt_digest: str,
    graph_digest: str,
    artifact_paths: tuple[str | Path, ...],
    deployment_profile_path: str | Path,
    lock_path: str | Path,
    pyproject_path: str | Path,
    coalition_result_binding_digest: str | None = None,
    native_nonce: str | None = None,
    agentteams_project_id: str | None = None,
    agentteams_attempt_id: str | None = None,
    agentteams_attempt_number: int | None = None,
    agentteams_retry_count: int | None = None,
    agentteams_reassign_count: int | None = None,
    skill_name: str = "enterprise-quote-compose",
    skill_version: str | None = None,
) -> dict[str, Any]:
    """Generate one correlated controlled-local HTTP/OTLP/operations pack."""

    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ControlledLocalEvidenceError("OUTPUT_EVIDENCE_DIR_NOT_EMPTY")
    for name, value in {
        "run_id": run_id,
        "task_id": task_id,
        "delegation_id": delegation_id,
    }.items():
        if not isinstance(value, str) or not value.strip():
            raise ControlledLocalEvidenceError(f"IDENTIFIER_REQUIRED:{name}")
    digests = {
        "native_receipt_digest": native_receipt_digest,
        "skill_package_digest": skill_package_digest,
        "skill_invocation_receipt_digest": skill_invocation_receipt_digest,
        "graph_digest": graph_digest,
    }
    if any(
        not isinstance(value, str)
        or not value.startswith("sha256:")
        or len(value) != 71
        or value == "sha256:" + "0" * 64
        for value in digests.values()
    ):
        raise ControlledLocalEvidenceError("NONZERO_SHA256_DIGESTS_REQUIRED")
    if coalition_result_binding_digest is not None and (
        not coalition_result_binding_digest.startswith("sha256:")
        or len(coalition_result_binding_digest) != 71
        or any(
            character not in "0123456789abcdef"
            for character in coalition_result_binding_digest.removeprefix("sha256:")
        )
        or coalition_result_binding_digest == "sha256:" + "0" * 64
    ):
        raise ControlledLocalEvidenceError("COALITION_RESULT_BINDING_DIGEST_INVALID")
    for name, value in {
        "native_nonce": native_nonce,
        "agentteams_project_id": agentteams_project_id,
        "agentteams_attempt_id": agentteams_attempt_id,
        "skill_name": skill_name,
        "skill_version": skill_version,
    }.items():
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ControlledLocalEvidenceError(f"CORRELATION_FACT_INVALID:{name}")
    if native_nonce is not None and (
        len(native_nonce) != 64
        or any(character not in "0123456789abcdef" for character in native_nonce)
    ):
        raise ControlledLocalEvidenceError("NATIVE_NONCE_INVALID")
    if agentteams_attempt_number is not None and agentteams_attempt_number < 1:
        raise ControlledLocalEvidenceError("AGENTTEAMS_ATTEMPT_NUMBER_INVALID")
    if (agentteams_attempt_id is None) != (agentteams_attempt_number is None):
        raise ControlledLocalEvidenceError("AGENTTEAMS_ATTEMPT_BINDING_INCOMPLETE")
    if any(
        value is not None and value < 0
        for value in (agentteams_retry_count, agentteams_reassign_count)
    ):
        raise ControlledLocalEvidenceError("AGENTTEAMS_COUNT_INVALID")

    artifacts = tuple(Path(path).expanduser().resolve() for path in artifact_paths)
    if not artifacts or any(not path.is_file() for path in artifacts):
        raise ControlledLocalEvidenceError("SBOM_ARTIFACT_MISSING")
    deployment_path = Path(deployment_profile_path).expanduser().resolve()
    deployment = json.loads(deployment_path.read_text(encoding="utf-8"))
    if (
        not isinstance(deployment, dict)
        or deployment.get("profile_id") != "controlled-local@v1"
        or deployment.get("production_ready") is not False
    ):
        raise ControlledLocalEvidenceError("DEPLOYMENT_PROFILE_INVALID")

    correlation_root = sha256_digest(
        {
            "run_id": run_id,
            "task_id": task_id,
            "delegation_id": delegation_id,
            "coalition_result_binding_digest": (
                coalition_result_binding_digest or "NOT_BOUND"
            ),
            **digests,
        }
    )
    with ControlledEnterpriseServer() as server:
        client = ControlledEnterpriseClient(server.base_url, token=server.token)
        health = client.health()
        source, source_receipt = client.read_source(run_id=run_id)
        if source is None:
            raise ControlledLocalEvidenceError("SOURCE_PAYLOAD_REQUIRED")
        source_304, not_modified_receipt = client.read_source(
            run_id=run_id,
            etag=source_receipt.etag,
        )
        if source_304 is not None or not_modified_receipt.status != "NOT_MODIFIED":
            raise ControlledLocalEvidenceError("SOURCE_ETAG_REVALIDATION_FAILED")
        tool_result, tool_receipt = client.call_dependency_tool(
            run_id=run_id,
            task_id=task_id,
            target_id="work:enterprise-quote",
            graph_digest=graph_digest,
        )
        capacity = run_capacity_smoke(
            "controlled-enterprise-health",
            (client.health for _ in range(25)),
        )

    _write(output / "source" / "health.json", health)
    _write(output / "source" / "payload.json", source)
    _write(output / "source" / "receipt.json", _dump(source_receipt))
    _write(
        output / "source" / "not-modified-receipt.json",
        _dump(not_modified_receipt),
    )
    _write(output / "tool" / "result.json", tool_result)
    _write(output / "tool" / "receipt.json", _dump(tool_receipt))

    layers = {
        "SOURCE": "SUCCEEDED",
        "AGENTTEAMS": "ACCEPT",
        "TOOL": "SUCCEEDED",
        "SKILL": "CANARY",
        "TERMINAL": "COMPLETED",
    }
    tool_result_digest = sha256_digest(tool_result)
    correlation_facts: dict[str, str | int | bool] = {
        "orgrebase.native.receipt.digest": native_receipt_digest,
        "orgrebase.skill.invocation.receipt.digest": skill_invocation_receipt_digest,
        "orgrebase.skill.name": skill_name,
        "orgrebase.tool.receipt.digest": tool_receipt.digest,
        "orgrebase.tool.result.digest": tool_result_digest,
        "orgrebase.tool.http.attempts": tool_receipt.attempts,
        "orgrebase.tool.retry.count": tool_receipt.attempts - 1,
        "orgrebase.target.write.count": 0,
    }
    if delegation_id.startswith("sha256:"):
        correlation_facts["orgrebase.agentteams.delegation.digest"] = delegation_id
    else:
        correlation_facts["orgrebase.agentteams.delegation.id"] = delegation_id
    optional_facts: tuple[tuple[str, str | int | None], ...] = (
        ("orgrebase.workflow.nonce", native_nonce),
        ("orgrebase.agentteams.project.id", agentteams_project_id),
        ("orgrebase.agentteams.attempt.id", agentteams_attempt_id),
        ("orgrebase.agentteams.attempt.number", agentteams_attempt_number),
        ("orgrebase.agentteams.retry.count", agentteams_retry_count),
        ("orgrebase.agentteams.reassign.count", agentteams_reassign_count),
        ("orgrebase.skill.version", skill_version),
        (
            "orgrebase.coalition.result_binding.digest",
            coalition_result_binding_digest,
        ),
    )
    correlation_facts.update(
        {key: value for key, value in optional_facts if value is not None}
    )
    bundle = build_joint_otlp(
        run_id=run_id,
        organization_id=str(source["organization_id"]),
        receipt_digest=correlation_root,
        task_id=task_id,
        skill_digest=skill_package_digest,
        layers=layers,
        correlation=correlation_facts,
        evidence_class=EVIDENCE_CLASS,
    )
    for signal, payload in bundle.items():
        _write(output / "observability" / f"{signal}.otlp.json", payload)

    telemetry_path = output / "observability" / "telemetry.sqlite"
    store = TelemetryStore(telemetry_path)
    try:
        with OtlpHTTPReceiver(store) as receiver:
            exporter = OtlpHTTPExporter(
                receiver.base_url,
                token=receiver.token,
                retries=1,
            )
            export_receipts = [
                _dump(exporter.export(run_id=run_id, signal=signal, payload=payload))
                for signal, payload in bundle.items()
            ]
            privacy_probe_run = f"{run_id}:privacy-probe"
            privacy_payload = build_joint_otlp(
                run_id=privacy_probe_run,
                organization_id="org:northstar",
                receipt_digest=correlation_root,
                task_id=task_id,
                skill_digest=skill_package_digest,
                layers={"TERMINAL": "COMPLETED"},
            )["traces"]
            privacy_payload["resourceSpans"][0]["secret"] = (
                "ORGREBASE_CANARY_SECRET_CONTROLLED_LOCAL"
            )
            try:
                exporter.export(
                    run_id=privacy_probe_run,
                    signal="traces",
                    payload=privacy_payload,
                )
            except IntegrityError as exc:
                privacy_probe = {
                    "status": "PASS",
                    "reason_code": "PRIVACY_PAYLOAD_REJECTED",
                    "exception_class": type(exc).__name__,
                    "raw_payload_retained": False,
                }
            else:  # pragma: no cover - fail-closed postcondition
                raise ControlledLocalEvidenceError("PRIVACY_PROBE_NOT_REJECTED")

        records, query_receipt = store.query(
            run_id=run_id,
            skill_digest=skill_package_digest,
        )
        alert_receipt = evaluate_run_alerts(
            store,
            run_id=run_id,
            required_layers=REQUIRED_LAYERS,
        )
        if len(records) != 3 or query_receipt.count != 3 or alert_receipt.status != "PASS":
            raise ControlledLocalEvidenceError("SAME_RUN_TELEMETRY_CHAIN_INCOMPLETE")

        broken_run = f"{run_id}:negative-alert-probe"
        broken = build_joint_otlp(
            run_id=broken_run,
            organization_id="org:northstar",
            receipt_digest=correlation_root,
            task_id=task_id,
            skill_digest=skill_package_digest,
            layers={"TOOL": "FAILED"},
        )["traces"]
        store.ingest("traces", broken)
        negative_alert_receipt = evaluate_run_alerts(
            store,
            run_id=broken_run,
            required_layers=REQUIRED_LAYERS,
        )
        if negative_alert_receipt.status != "ALERT":
            raise ControlledLocalEvidenceError("NEGATIVE_ALERT_PROBE_NOT_DETECTED")

        now = int(time.time())
        expired = build_joint_otlp(
            run_id=f"{run_id}:expired-retention-probe",
            organization_id="org:northstar",
            receipt_digest=correlation_root,
            task_id=task_id,
            skill_digest=skill_package_digest,
            layers={"TERMINAL": "COMPLETED"},
        )["logs"]
        expired_id, _expired_digest = store.ingest(
            "logs",
            expired,
            ingested_at=now - 100,
        )
        retention_receipt = store.prune(cutoff_epoch_seconds=now - 10)
        if expired_id not in retention_receipt.deleted_ingestion_ids:
            raise ControlledLocalEvidenceError("RETENTION_PROBE_NOT_PRUNED")
        rejection = store.connection.execute(
            "SELECT signal,run_id,payload_digest,reason_code FROM otlp_rejections "
            "WHERE run_id=? ORDER BY id DESC LIMIT 1",
            (privacy_probe_run,),
        ).fetchone()
        if rejection is None:
            raise ControlledLocalEvidenceError("PRIVACY_REJECTION_RECEIPT_MISSING")
        rejection_record = {
            "signal": str(rejection[0]),
            "run_id": str(rejection[1]),
            "payload_digest": str(rejection[2]),
            "reason_code": str(rejection[3]),
            "raw_payload_retained": False,
        }
    finally:
        store.close()
    if b"ORGREBASE_CANARY_SECRET_" in telemetry_path.read_bytes():
        raise ControlledLocalEvidenceError("PRIVACY_CANARY_PERSISTED")

    _write(output / "observability" / "export-receipts.json", export_receipts)
    _write(output / "observability" / "query-receipt.json", _dump(query_receipt))
    _write(output / "observability" / "alert-receipt.json", _dump(alert_receipt))
    _write(
        output / "observability" / "negative-alert-probe.json",
        _dump(negative_alert_receipt),
    )
    _write(output / "observability" / "privacy-probe.json", privacy_probe)
    _write(output / "observability" / "privacy-rejection.json", rejection_record)
    _write(
        output / "observability" / "retention-receipt.json",
        _dump(retention_receipt),
    )

    operations_dir = output / "operations"
    operations_dir.mkdir(parents=True, exist_ok=True)
    state_path = operations_dir / "canonical-state.sqlite"
    with StateStore(state_path) as state, state.transaction() as connection:
        state.save_artifact(
            connection,
            "artifact:controlled-local-correlation-root",
            "application/json",
            {
                "run_id": run_id,
                "correlation_root": correlation_root,
                "canonical_target_writes": 0,
            },
        )
        state.append_event(
            connection,
            "CONTROLLED_LOCAL_CANDIDATE_ACCEPTED",
            {
                "run_id": run_id,
                "task_id": task_id,
                "canonical_target_writes": 0,
            },
        )
    restore_receipt = run_sqlite_backup_restore_drill(
        state_path,
        backup=output / "operations" / "canonical-state.backup.sqlite",
        restored=output / "operations" / "canonical-state.restored.sqlite",
    )
    if restore_receipt.status != "PASS":
        raise ControlledLocalEvidenceError("BACKUP_RESTORE_FAILED")
    _write(output / "operations" / "backup-restore-receipt.json", _dump(restore_receipt))
    _write(output / "operations" / "capacity-smoke-receipt.json", _dump(capacity))
    _write(output / "operations" / "deployment-profile.json", deployment)
    _write(
        output / "operations" / "sbom.cdx.json",
        _build_sbom(
            lock_path=Path(lock_path).expanduser().resolve(),
            pyproject_path=Path(pyproject_path).expanduser().resolve(),
            artifact_paths=artifacts,
        ),
    )

    summary_base = {
        "schema_version": "orgrebase.controlled-local-evidence-summary.v1",
        "status": "PASS",
        "evidence_class": EVIDENCE_CLASS,
        "run_id": run_id,
        "task_id": task_id,
        "delegation_id": delegation_id,
        "correlation_root": correlation_root,
        **digests,
        "source_receipt_digest": source_receipt.digest,
        "source_revalidation_receipt_digest": not_modified_receipt.digest,
        "tool_receipt_digest": tool_receipt.digest,
        "tool_result_digest": tool_result_digest,
        "telemetry_query_receipt_digest": query_receipt.digest,
        "alert_receipt_digest": alert_receipt.digest,
        "retention_receipt_digest": retention_receipt.digest,
        "backup_restore_receipt_digest": restore_receipt.digest,
        "capacity_receipt_digest": capacity.digest,
        "required_layers": list(REQUIRED_LAYERS),
        "causal_chain": list(REQUIRED_LAYERS),
        "telemetry_timing_class": CONTROLLED_TIMING_CLASS,
        "native_nonce": native_nonce or "NOT_BOUND",
        "native_nonce_binding": "BOUND" if native_nonce is not None else "NOT_BOUND",
        "agentteams_project_id": agentteams_project_id or "NOT_BOUND",
        "agentteams_project_binding": (
            "BOUND" if agentteams_project_id is not None else "NOT_BOUND"
        ),
        "agentteams_attempt_id": agentteams_attempt_id or "NOT_BOUND",
        "agentteams_attempt_number": agentteams_attempt_number or "NOT_BOUND",
        "agentteams_attempt_binding": (
            "BOUND" if agentteams_attempt_id is not None else "NOT_BOUND"
        ),
        "agentteams_retry_count": (
            agentteams_retry_count
            if agentteams_retry_count is not None
            else "NOT_BOUND"
        ),
        "agentteams_reassign_count": (
            agentteams_reassign_count
            if agentteams_reassign_count is not None
            else "NOT_BOUND"
        ),
        "skill_name": skill_name,
        "skill_version": skill_version or "NOT_BOUND",
        "skill_version_binding": "BOUND" if skill_version is not None else "NOT_BOUND",
        "coalition_result_binding_digest": (
            coalition_result_binding_digest or "NOT_BOUND"
        ),
        "canonical_target_writes": 0,
        "approval_apply_status": "NOT_RUN",
        "external_enterprise_connectors": "NOT_RUN",
        "production_readiness": False,
    }
    summary = _record(summary_base)
    _write(output / "summary.json", summary)
    entries = [
        {
            "path": path.relative_to(output).as_posix(),
            "sha256": _file_digest(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(output.rglob("*"))
        if path.is_file() and path.name != "evidence-index.json"
    ]
    index = _record(
        {
            "schema_version": "orgrebase.controlled-local-evidence-index.v1",
            "entry_count": len(entries),
            "entries": entries,
            "pack_digest": sha256_digest(entries),
        }
    )
    _write(output / "evidence-index.json", index)
    return summary
