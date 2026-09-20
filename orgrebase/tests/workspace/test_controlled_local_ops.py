from __future__ import annotations

import re
import stat
import time
from pathlib import Path

import pytest

from orgrebase.domain import IntegrityError
from orgrebase.store import StateStore
from orgrebase.workspace.controlled_local import (
    CONTROLLED_HTTP_EVIDENCE_CLASS,
    CONTROLLED_OPS_EVIDENCE_CLASS,
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

RUN_ID = "run:controlled-local:quote-001"
TASK_ID = "task:quote-compose:001"
SKILL_DIGEST = "sha256:" + "2" * 64
RECEIPT_DIGEST = "sha256:" + "3" * 64


def _attributes(value: list[dict[str, object]]) -> dict[str, object]:
    decoded: dict[str, object] = {}
    for item in value:
        encoded = item["value"]
        assert isinstance(encoded, dict)
        decoded[str(item["key"])] = next(iter(encoded.values()))
    return decoded


def test_controlled_source_and_tool_cross_real_http_with_contract_checks() -> None:
    with ControlledEnterpriseServer() as server:
        client = ControlledEnterpriseClient(server.base_url, token=server.token)
        assert client.health() == {"profile": "controlled-local", "status": "ok"}

        source, source_receipt = client.read_source(run_id=RUN_ID)
        cached, cached_receipt = client.read_source(run_id=RUN_ID, etag=source_receipt.etag)
        tool_result, tool_receipt = client.call_dependency_tool(
            run_id=RUN_ID,
            task_id=TASK_ID,
            target_id="work:enterprise_quote_c",
            graph_digest="sha256:" + "1" * 64,
        )

        assert source and source["synthetic"] is True
        assert cached is None
        assert cached_receipt.status == "NOT_MODIFIED"
        assert source_receipt.evidence_class == CONTROLLED_HTTP_EVIDENCE_CLASS
        assert source_receipt.endpoint_class == "LOOPBACK_TCP"
        assert tool_result["run_id"] == RUN_ID
        assert tool_result["target_writes"] == tool_receipt.target_writes == 0
        assert tool_receipt.connector_kind == "TOOL"

        with pytest.raises(IntegrityError, match="CONTROLLED_SOURCE_HTTP_FAILED:401"):
            ControlledEnterpriseClient(server.base_url, token="wrong").read_source(run_id=RUN_ID)


def test_controlled_source_rejects_schema_or_etag_drift() -> None:
    with ControlledEnterpriseServer() as server:
        server._server.source_payload["unexpected"] = "drift"
        client = ControlledEnterpriseClient(server.base_url, token=server.token)
        with pytest.raises(IntegrityError, match="CONTROLLED_SOURCE_SCHEMA_OR_ETAG_MISMATCH"):
            client.read_source(run_id=RUN_ID)


def _complete_bundle(run_id: str = RUN_ID) -> dict[str, dict[str, object]]:
    return build_joint_otlp(
        run_id=run_id,
        organization_id="org:northstar",
        receipt_digest=RECEIPT_DIGEST,
        task_id=TASK_ID,
        skill_digest=SKILL_DIGEST,
        layers={
            "AGENTTEAMS": "ACCEPT",
            "SKILL": "SUCCEEDED",
            "TOOL": "SUCCEEDED",
            "APPROVAL": "APPROVED",
            "APPLY": "SUCCEEDED",
            "TERMINAL": "COMPLETED",
        },
        correlation={
            "orgrebase.agentteams.attempt.id": "attempt:1",
            "orgrebase.agentteams.delegation.digest": "sha256:" + "4" * 64,
            "orgrebase.approval.digest": "sha256:" + "5" * 64,
            "orgrebase.effect.digest": "sha256:" + "6" * 64,
            "orgrebase.oac.plan.root": "sha256:" + "7" * 64,
            "orgrebase.skill.invocation.digest": "sha256:" + "8" * 64,
            "orgrebase.skill.name": "enterprise-quote-compose",
            "orgrebase.skill.version": "1.0.0",
            "orgrebase.stage.run_id": run_id,
            "orgrebase.target.write.count": 0,
            "orgrebase.tool.request.digest": "sha256:" + "9" * 64,
            "orgrebase.tool.result.digest": "sha256:" + "a" * 64,
        },
    )


def test_joint_otlp_is_one_ordered_parent_chain_with_log_span_bindings() -> None:
    bundle = build_joint_otlp(
        run_id=RUN_ID,
        organization_id="org:northstar",
        receipt_digest=RECEIPT_DIGEST,
        task_id=TASK_ID,
        skill_digest=SKILL_DIGEST,
        layers={
            "SKILL": "CANARY",
            "TERMINAL": "COMPLETED",
            "SOURCE": "SUCCEEDED",
            "TOOL": "SUCCEEDED",
            "AGENTTEAMS": "ACCEPT",
        },
    )
    spans = bundle["traces"]["resourceSpans"][0]["scopeSpans"][0]["spans"]
    records = bundle["logs"]["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
    expected_layers = ["SOURCE", "AGENTTEAMS", "TOOL", "SKILL", "TERMINAL"]

    assert [
        _attributes(span["attributes"])["orgrebase.chain.layer"] for span in spans
    ] == expected_layers
    assert [
        _attributes(span["attributes"])["orgrebase.chain.sequence"] for span in spans
    ] == ["1", "2", "3", "4", "5"]
    assert "parentSpanId" not in spans[0]
    assert [span["parentSpanId"] for span in spans[1:]] == [
        span["spanId"] for span in spans[:-1]
    ]
    assert len({span["traceId"] for span in spans}) == 1
    assert len({span["spanId"] for span in spans}) == 5
    assert [record["traceId"] for record in records] == [
        span["traceId"] for span in spans
    ]
    assert [record["spanId"] for record in records] == [
        span["spanId"] for span in spans
    ]
    assert [
        _attributes(record["attributes"])["orgrebase.chain.sequence"]
        for record in records
    ] == ["1", "2", "3", "4", "5"]
    assert all(
        _attributes(span["attributes"])["orgrebase.telemetry.timing.class"]
        == "SYNTHETIC_DETERMINISTIC_PROJECTION"
        for span in spans
    )


def test_telemetry_store_tightens_file_permissions(tmp_path: Path) -> None:
    database = tmp_path / "telemetry.sqlite"
    database.touch(mode=0o644)
    database.chmod(0o644)

    store = TelemetryStore(database)
    try:
        assert database.stat().st_mode & 0o777 == 0o600
    finally:
        store.close()


def test_otlp_http_export_query_retention_and_alerts(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite")
    try:
        with OtlpHTTPReceiver(store) as receiver:
            exporter = OtlpHTTPExporter(receiver.base_url, token=receiver.token)
            export_receipts = [
                exporter.export(run_id=RUN_ID, signal=signal, payload=payload)
                for signal, payload in _complete_bundle().items()
            ]
            with pytest.raises(IntegrityError, match="OTLP_HTTP_EXPORT_FAILED:traces:401"):
                OtlpHTTPExporter(
                    receiver.base_url,
                    token="wrong",
                    timeout_seconds=0.05,
                    retries=1,
                ).export(run_id=RUN_ID, signal="traces", payload=_complete_bundle()["traces"])

        assert [item.operation for item in export_receipts] == [
            "EXPORT_TRACES",
            "EXPORT_LOGS",
            "EXPORT_METRICS",
        ]
        assert all(item.attempts == 1 for item in export_receipts)
        now = int(time.time())
        records, query = store.query(
            run_id=RUN_ID,
            skill_digest=SKILL_DIGEST,
            start_epoch_seconds=now - 5,
            end_epoch_seconds=now + 5,
        )
        assert len(records) == query.count == 3
        assert query.evidence_class == CONTROLLED_OPS_EVIDENCE_CLASS
        assert evaluate_run_alerts(
            store,
            run_id=RUN_ID,
            required_layers=("AGENTTEAMS", "SKILL", "TOOL", "APPROVAL", "APPLY", "TERMINAL"),
        ).status == "PASS"

        old_payload = build_joint_otlp(
            run_id="run:expired",
            organization_id="org:northstar",
            receipt_digest=RECEIPT_DIGEST,
            task_id="task:expired",
            skill_digest=SKILL_DIGEST,
            layers={"TERMINAL": "COMPLETED"},
        )["traces"]
        store.ingest("traces", old_payload, ingested_at=now - 100)
        retention = store.prune(cutoff_epoch_seconds=now - 10)
        assert len(retention.deleted_ingestion_ids) == 1
        assert retention.retained_count == 3
        assert retention.canonical_business_evidence_deleted is False
    finally:
        store.close()


def test_otlp_privacy_canary_and_raw_fields_fail_closed(tmp_path: Path) -> None:
    database = tmp_path / "telemetry.sqlite"
    store = TelemetryStore(database)
    try:
        with pytest.raises(IntegrityError, match="OTLP_RESTRICTED_FIELD:secret"):
            store.ingest(
                "traces",
                {"resourceSpans": [{"secret": "ORGREBASE_CANARY_SECRET_case-044"}]},
            )
        with pytest.raises(IntegrityError, match="OTLP_RESTRICTED_FIELD:raw_prompt"):
            store.ingest("logs", {"resourceLogs": [{"raw_prompt": "do not persist"}]})
        with pytest.raises(IntegrityError, match="OTLP_RESTRICTED_ATTRIBUTE:raw_output"):
            store.ingest(
                "logs",
                {
                    "resourceLogs": [
                        {
                            "attributes": [
                                {
                                    "key": "raw_output",
                                    "value": {"stringValue": "must not persist"},
                                }
                            ]
                        }
                    ]
                },
            )
        with (
            OtlpHTTPReceiver(store) as receiver,
            pytest.raises(IntegrityError, match="OTLP_HTTP_EXPORT_FAILED:traces:400"),
        ):
            OtlpHTTPExporter(receiver.base_url, token=receiver.token, retries=0).export(
                run_id=RUN_ID,
                    signal="traces",
                    payload={
                        "resourceSpans": [
                            {
                                "attributes": [
                                    {
                                        "key": "orgrebase.workflow.run_id",
                                        "value": {"stringValue": RUN_ID},
                                    }
                                ],
                                "secret": "ORGREBASE_CANARY_SECRET_receiver-case-044",
                            }
                        ]
                    },
                )
        assert store.connection.execute("SELECT COUNT(*) FROM otlp_ingestions").fetchone()[0] == 0
        rejection = store.connection.execute(
            "SELECT payload_digest,reason_code FROM otlp_rejections"
        ).fetchone()
        assert rejection is not None
        assert str(rejection[0]).startswith("sha256:")
        assert rejection[1] == "OTLP_RESTRICTED_FIELD:secret"
        alert = evaluate_run_alerts(store, run_id=RUN_ID, required_layers=("TERMINAL",))
        assert "RECEIVER_INGESTION_FAILURE" in {item.reason_code for item in alert.alerts}
        assert b"ORGREBASE_CANARY_SECRET_" not in database.read_bytes()
    finally:
        store.close()


@pytest.mark.parametrize(
    "field_name",
    (
        "APIKey",
        "vendor.access-token",
        "clientSecret",
        "PASSWORD",
        "sessionCookie",
        "http.authorization-header",
        "rawResponse",
        "customerEmail",
    ),
)
def test_otlp_privacy_gate_normalizes_sensitive_field_names(
    tmp_path: Path,
    field_name: str,
) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite")
    try:
        with pytest.raises(
            IntegrityError,
            match=rf"OTLP_RESTRICTED_FIELD:{re.escape(field_name)}",
        ):
            store.ingest("logs", {"resourceLogs": [{field_name: "must not persist"}]})
    finally:
        store.close()


@pytest.mark.parametrize(
    "attribute_name",
    (
        "api-key",
        "vendorAccessToken",
        "CLIENT_SECRET",
        "authorizationHeader",
        "raw-response",
        "orgrebase.customerEmail",
    ),
)
def test_otlp_privacy_gate_normalizes_sensitive_attribute_names(
    tmp_path: Path,
    attribute_name: str,
) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite")
    try:
        with pytest.raises(
            IntegrityError,
            match=rf"OTLP_RESTRICTED_ATTRIBUTE:{re.escape(attribute_name)}",
        ):
            store.ingest(
                "logs",
                {
                    "resourceLogs": [
                        {
                            "attributes": [
                                {
                                    "key": attribute_name,
                                    "value": {"stringValue": "must not persist"},
                                }
                            ]
                        }
                    ]
                },
            )
    finally:
        store.close()


def test_otlp_privacy_gate_allows_non_sensitive_operational_fields(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite")
    try:
        payload = {
            "resourceMetrics": [
                {
                    "api_key_rotation_status": "current",
                    "active_fencing_token": 4,
                    "authorization_decision": "allow",
                    "cookie_rejection_count": 0,
                    "customer_email_change_count": 1,
                    "output_tokens": 24,
                    "password_policy_version": "v2",
                    "prompt_cache_hit": True,
                    "raw_response_digest": "sha256:" + "a" * 64,
                    "secretary_role": "approver",
                    "tokenization_latency_ms": 3,
                }
            ]
        }

        _, digest = store.ingest("metrics", payload)

        assert digest.startswith("sha256:")
        assert store.connection.execute(
            "SELECT COUNT(*) FROM otlp_ingestions"
        ).fetchone() == (1,)
    finally:
        store.close()


@pytest.mark.parametrize(
    ("body", "reason"),
    (
        ("Bearer " + "live" + "-customer-secret", "OTLP_RESTRICTED_CONTENT"),
        (
            "eyJhbGciOiJIUzI1NiJ9"
            + ".eyJzdWIiOiJjdXN0b21lciJ9"
            + ".signature123",
            "OTLP_RESTRICTED_CONTENT",
        ),
        ("Please summarize this private customer prompt", "OTLP_UNSTRUCTURED_CONTENT_FORBIDDEN"),
    ),
)
def test_otlp_privacy_gate_rejects_sensitive_or_unstructured_log_body(
    tmp_path: Path,
    body: str,
    reason: str,
) -> None:
    database = tmp_path / "telemetry.sqlite"
    store = TelemetryStore(database)
    try:
        with pytest.raises(IntegrityError, match=reason):
            store.ingest(
                "logs",
                {
                    "resourceLogs": [
                        {
                            "scopeLogs": [
                                {"logRecords": [{"body": {"stringValue": body}}]}
                            ]
                        }
                    ]
                },
            )
        assert store.connection.execute(
            "SELECT COUNT(*) FROM otlp_ingestions"
        ).fetchone() == (0,)
        assert body.encode() not in database.read_bytes()
    finally:
        store.close()


def test_otlp_privacy_gate_allows_structured_operational_log_body(
    tmp_path: Path,
) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite")
    try:
        ingestion_id, digest = store.ingest(
            "logs",
            {
                "resourceLogs": [
                    {
                        "scopeLogs": [
                            {
                                "logRecords": [
                                    {"body": {"stringValue": "SOURCE:SUCCEEDED"}}
                                ]
                            }
                        ]
                    }
                ]
            },
        )
        assert ingestion_id == 1
        assert digest.startswith("sha256:")
    finally:
        store.close()


def test_alerts_detect_failed_chain_approval_mismatch_and_candidate_write(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite")
    try:
        payload = build_joint_otlp(
            run_id="run:broken",
            organization_id="org:northstar",
            receipt_digest=RECEIPT_DIGEST,
            task_id=TASK_ID,
            skill_digest=SKILL_DIGEST,
            layers={
                "AGENTTEAMS": "ACCEPT",
                "SKILL": "QUARANTINED",
                "TOOL": "FAILED",
                "APPROVAL": "REJECTED",
                "APPLY": "SUCCEEDED",
                "TERMINAL": "COMPLETED",
            },
            correlation={
                "orgrebase.attempt.fenced": True,
                "orgrebase.target.write.count": 1,
            },
        )["traces"]
        store.ingest("traces", payload)
        receipt = evaluate_run_alerts(
            store,
            run_id="run:broken",
            required_layers=("AGENTTEAMS", "SKILL", "TOOL", "APPROVAL", "APPLY", "TERMINAL"),
        )
        assert receipt.status == "ALERT"
        assert {item.reason_code for item in receipt.alerts} == {
            "APPROVAL_APPLY_MISMATCH",
            "LATE_FENCED_RESULT",
            "NON_ZERO_CANDIDATE_WRITES",
            "SKILL_QUARANTINED",
            "TOOL_FAILURE",
            "UNHEALTHY_LAYER_STATUS:APPROVAL",
        }
    finally:
        store.close()


def test_alerts_preserve_layer_status_pairing(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite")
    try:
        payload = build_joint_otlp(
            run_id="run:status-pairing",
            organization_id="org:northstar",
            receipt_digest=RECEIPT_DIGEST,
            task_id=TASK_ID,
            skill_digest=SKILL_DIGEST,
            layers={"SOURCE": "COMPLETED", "TERMINAL": "FAILED"},
        )["traces"]
        store.ingest("traces", payload)

        receipt = evaluate_run_alerts(
            store,
            run_id="run:status-pairing",
            required_layers=("SOURCE", "TERMINAL"),
        )

        assert receipt.status == "ALERT"
        assert "TERMINAL_STATE_MISSING" in {
            item.reason_code for item in receipt.alerts
        }
    finally:
        store.close()


def test_alerts_require_a_success_status_for_every_known_required_layer(
    tmp_path: Path,
) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite")
    try:
        payload = build_joint_otlp(
            run_id="run:source-failed",
            organization_id="org:northstar",
            receipt_digest=RECEIPT_DIGEST,
            task_id=TASK_ID,
            skill_digest=SKILL_DIGEST,
            layers={"SOURCE": "FAILED", "TERMINAL": "COMPLETED"},
        )["traces"]
        store.ingest("traces", payload)

        receipt = evaluate_run_alerts(
            store,
            run_id="run:source-failed",
            required_layers=("SOURCE", "TERMINAL"),
        )

        assert receipt.status == "ALERT"
        assert "UNHEALTHY_LAYER_STATUS:SOURCE" in {
            item.reason_code for item in receipt.alerts
        }
    finally:
        store.close()


def test_alerts_reject_mixed_success_and_failure_for_the_same_layer(
    tmp_path: Path,
) -> None:
    store = TelemetryStore(tmp_path / "telemetry.sqlite")
    try:
        for status in ("SUCCEEDED", "FAILED"):
            payload = build_joint_otlp(
                run_id="run:mixed-tool-status",
                organization_id="org:northstar",
                receipt_digest=RECEIPT_DIGEST,
                task_id=TASK_ID,
                skill_digest=SKILL_DIGEST,
                layers={"TOOL": status},
            )["traces"]
            store.ingest("traces", payload)

        receipt = evaluate_run_alerts(
            store,
            run_id="run:mixed-tool-status",
            required_layers=("TOOL",),
        )

        assert receipt.status == "ALERT"
        assert "MIXED_LAYER_STATUS:TOOL" in {
            item.reason_code for item in receipt.alerts
        }
    finally:
        store.close()


def test_backup_restore_and_capacity_are_observed_not_sla_claims(tmp_path: Path) -> None:
    source = tmp_path / "state.sqlite"
    with StateStore(source) as state:
        with state.transaction() as connection:
            state.save_artifact(
                connection,
                "artifact:controlled-local",
                "application/json",
                {"run_id": RUN_ID},
            )
            state.append_event(connection, "CONTROLLED_LOCAL_READY", {"run_id": RUN_ID})
        expected_head = state.verify_event_chain()["head_digest"]

    drill = run_sqlite_backup_restore_drill(
        source,
        backup=tmp_path / "backup.sqlite",
        restored=tmp_path / "restored.sqlite",
    )
    assert drill.status == "PASS"
    assert drill.source_event_head == drill.restored_event_head == expected_head
    assert drill.artifact_count == 1
    assert drill.production_sla_claimed is False
    assert stat.S_IMODE((tmp_path / "backup.sqlite").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "restored.sqlite").stat().st_mode) == 0o600

    smoke = run_capacity_smoke("noop", (lambda: None for _ in range(25)))
    assert smoke.successes == smoke.sample_count == 25
    assert smoke.failures == 0
    assert smoke.observed_requests_per_second > 0
    assert smoke.sla_met_claimed is False
    assert smoke.production_ready_claimed is False
    assert smoke.environment_facts["cpu_count"] >= 1
