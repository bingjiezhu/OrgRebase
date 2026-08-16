from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLAN_DIGEST = "sha256:" + "c" * 64
TASK_DIGEST = "sha256:" + "d" * 64
INPUT_REFS = ["sha256:" + character * 64 for character in ("1", "2", "3")]


def _load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


publisher = _load_script("publish_agentteams_matrix_event")
exporter = _load_script("export_agentteams_model_calls")


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")


def _envelope() -> dict[str, Any]:
    return {
        "schema_version": publisher.RUN_SCHEMA,
        "run_id": "run:orgrebase:live:test",
        "nonce": "a" * 64,
        "issued_at_ms": 1_000,
        "expires_at_ms": 10_000,
        "team": {"room_id": "!team:matrix.local"},
        "workers": [
            {
                "worker_name": "change-coordinator",
                "role": "team_leader",
                "matrix_user_id": "@change-coordinator:matrix.local",
            },
            {
                "worker_name": "product-steward",
                "role": "worker",
                "matrix_user_id": "@product-steward:matrix.local",
            },
            {
                "worker_name": "legal-steward",
                "role": "worker",
                "matrix_user_id": "@legal-steward:matrix.local",
            },
            {
                "worker_name": "gtm-steward",
                "role": "worker",
                "matrix_user_id": "@gtm-steward:matrix.local",
            },
            {
                "worker_name": "skill-curator",
                "role": "worker",
                "matrix_user_id": "@skill-curator:matrix.local",
            },
        ],
        "skill": {
            "name": "enterprise-launch-readiness",
            "assigned_worker": "skill-curator",
            "digest": "sha256:" + "b" * 64,
        },
    }


def test_candidate_event_is_bound_to_exact_artifact_bytes(tmp_path: Path) -> None:
    run = _envelope()
    artifact = tmp_path / "result.json"
    _write_json(
        artifact,
        {
            "run_id": run["run_id"],
            "nonce": run["nonce"],
            "worker_name": "product-steward",
            "candidate_only": True,
            "orchestration_plan_digest": PLAN_DIGEST,
            "delegation_task_digest": TASK_DIGEST,
            "input_refs": INPUT_REFS,
        },
    )
    content = publisher.build_event_content(
        envelope=run,
        worker_name="product-steward",
        event_kind="WORKER_CANDIDATE",
        artifact_path=artifact,
        artifact_ref="result:product-steward",
    )
    payload = content["orgrebase.run"]
    assert payload["artifact_digest"].startswith("sha256:")
    assert payload["candidate_only"] is True
    assert payload["worker_name"] == "product-steward"
    assert payload["orchestration_plan_digest"] == PLAN_DIGEST
    assert payload["delegation_task_digest"] == TASK_DIGEST
    assert payload["input_refs"] == INPUT_REFS


def test_leader_delegation_commits_plan_before_candidates() -> None:
    content = publisher.build_event_content(
        envelope=_envelope(),
        worker_name="change-coordinator",
        event_kind="LEADER_DELEGATION",
        orchestration_plan_digest=PLAN_DIGEST,
    )
    assert content["orgrebase.run"]["orchestration_plan_digest"] == PLAN_DIGEST


def test_candidate_event_rejects_cross_worker_artifact(tmp_path: Path) -> None:
    run = _envelope()
    artifact = tmp_path / "result.json"
    _write_json(
        artifact,
        {
            "run_id": run["run_id"],
            "nonce": run["nonce"],
            "worker_name": "legal-steward",
            "candidate_only": True,
            "orchestration_plan_digest": PLAN_DIGEST,
            "delegation_task_digest": TASK_DIGEST,
            "input_refs": INPUT_REFS,
        },
    )
    with pytest.raises(publisher.PublicationError, match="not bound"):
        publisher.build_event_content(
            envelope=run,
            worker_name="product-steward",
            event_kind="WORKER_CANDIDATE",
            artifact_path=artifact,
            artifact_ref="result:product-steward",
        )


def test_exporter_proves_exact_model_write_without_prompt_leak(tmp_path: Path) -> None:
    run = _envelope()
    envelope_path = tmp_path / "run.json"
    _write_json(envelope_path, run)
    artifact_root = tmp_path / "artifacts"
    artifact_path = artifact_root / "product-steward" / "result.json"
    artifact_path.parent.mkdir(parents=True)
    artifact_value = {
        "run_id": run["run_id"],
        "nonce": run["nonce"],
        "worker_name": "product-steward",
        "candidate_only": True,
        "orchestration_plan_digest": PLAN_DIGEST,
        "delegation_task_digest": TASK_DIGEST,
        "input_refs": INPUT_REFS,
    }
    _write_json(artifact_path, artifact_value)
    digest = publisher._digest(artifact_path)
    manifest = {
        "run_id": run["run_id"],
        "nonce": run["nonce"],
        "artifacts": [
            {
                "ref": "result:product-steward",
                "path": "product-steward/result.json",
                "digest": digest,
                "producer_worker": "product-steward",
            }
        ]
        * 3,
    }
    for index, item in enumerate(manifest["artifacts"]):
        item["ref"] = f"result:product-steward:{index}"
    manifest_path = tmp_path / "manifest.json"
    _write_json(manifest_path, manifest)
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    record = {
        "timestamp": "1970-01-01T00:00:05.000Z",
        "message": {
            "role": "assistant",
            "provider": "agentteams-gateway",
            "model": "google/gemini-3.1-flash-lite",
            "responseId": "provider-response-001",
            "content": [
                {
                    "type": "toolCall",
                    "name": "write",
                    "arguments": {
                        "path": "/runtime/product-steward/result.json",
                        "content": artifact_path.read_text(encoding="utf-8"),
                    },
                }
            ],
        },
    }
    (sessions / "session.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    with pytest.raises(exporter.ExportError, match="not unique"):
        exporter.export_calls(
            run_envelope_path=envelope_path,
            artifact_manifest_path=manifest_path,
            artifact_root=artifact_root,
            session_roots={"product-steward": sessions},
        )


def test_exporter_rejects_non_exact_write(tmp_path: Path) -> None:
    artifact = b'{"candidate_only":true}'
    records = [
        {
            "timestamp": "2026-08-15T05:13:54.886Z",
            "message": {
                "role": "assistant",
                "provider": "agentteams-gateway",
                "model": "google/gemini-3.1-flash-lite",
                "responseId": "provider-response-001",
                "content": [
                    {
                        "type": "toolCall",
                        "name": "write",
                        "arguments": {
                            "path": "/runtime/product-steward/result.json",
                            "content": '{"candidate_only":false}',
                        },
                    }
                ],
            },
        }
    ]
    assert (
        exporter._matching_writes(
            records=records,
            artifact_bytes=artifact,
            artifact_relative_path="product-steward/result.json",
        )
        == []
    )
