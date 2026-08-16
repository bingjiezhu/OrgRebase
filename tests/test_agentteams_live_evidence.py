from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
NOW_MS = 1_800_000_000_000
NAMES = (
    "change-coordinator",
    "product-steward",
    "legal-steward",
    "gtm-steward",
    "skill-curator",
)
PLAN_DIGEST = "sha256:" + "3" * 64
INPUT_REFS = ["sha256:" + character * 64 for character in ("4", "5", "6")]


def _load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


collector = _load_script("collect_agentteams_evidence")


def _digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(value) for value in values) + "\n", encoding="utf-8")


def _payload(run: dict[str, Any], worker: str, kind: str, **extra: Any) -> dict[str, Any]:
    return {
        "schema_version": collector.MATRIX_PAYLOAD_SCHEMA,
        "run_id": run["run_id"],
        "nonce": run["nonce"],
        "worker_name": worker,
        "event_kind": kind,
        **extra,
    }


def _live_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    skill_path = artifact_root / "runtime-skill.md"
    skill_path.write_text("# enterprise-launch-readiness\nloaded by AgentTeams\n", encoding="utf-8")
    skill_digest = _digest(skill_path)
    run = {
        "schema_version": collector.RUN_SCHEMA,
        "run_id": "run:orgrebase:live:unit-001",
        "nonce": "a" * 64,
        "issued_at_ms": NOW_MS - 1_000,
        "expires_at_ms": NOW_MS + 100_000,
        "agentteams": {
            "version": collector.AGENTTEAMS_VERSION,
            "source_commit": collector.AGENTTEAMS_SOURCE_COMMIT,
        },
        "team": {
            "namespace": "agentteams",
            "name": "orgrebase-change-team",
            "uid": "team-uid",
            "generation": 7,
            "room_id": "!orgrebase:matrix.local",
        },
        "workers": [],
        "runtime_pods": [],
        "skill": {
            "name": "enterprise-launch-readiness",
            "assigned_worker": "skill-curator",
            "digest": skill_digest,
        },
    }
    for name in NAMES:
        run["workers"].append(
            {
                "namespace": "agentteams",
                "resource_name": f"orgrebase-{name}",
                "uid": f"worker-uid-{name}",
                "generation": 3,
                "worker_name": name,
                "matrix_user_id": f"@{name}:matrix.local",
                "role": "team_leader" if name == "change-coordinator" else "worker",
                "model": "gpt-5.4",
                "runtime": "openclaw",
                "image": None,
                "required_skills": (
                    ["enterprise-launch-readiness"] if name == "skill-curator" else []
                ),
            }
        )
        run["runtime_pods"].append(
            {
                "namespace": "agentteams",
                "name": f"pod-{name}",
                "uid": f"pod-uid-{name}",
                "worker_resource_name": f"orgrebase-{name}",
                "container_name": "worker",
                "image": "registry.local/agentteams/openclaw:v1.2.2",
                "image_id": "docker-pullable://openclaw@sha256:" + "2" * 64,
            }
        )

    run_path = tmp_path / "run.json"
    _write_json(run_path, run)
    artifacts: list[dict[str, Any]] = []
    candidate_workers = ("product-steward", "legal-steward", "gtm-steward")
    for worker in candidate_workers:
        path = artifact_root / f"{worker}.json"
        task_digest = "sha256:" + str(candidate_workers.index(worker) + 7) * 64
        _write_json(
            path,
            {
                "schema_version": "orgrebase.worker-candidate.v1",
                "worker": worker,
                "orchestration_plan_digest": PLAN_DIGEST,
                "delegation_task_digest": task_digest,
                "input_refs": INPUT_REFS,
            },
        )
        artifacts.append(
            {
                "ref": f"candidate:{worker}",
                "path": path.name,
                "digest": _digest(path),
                "schema_version": "orgrebase.worker-candidate.v1",
                "producer_worker": worker,
                "producer_matrix_user_id": f"@{worker}:matrix.local",
                "candidate_only": True,
                "delegation_task_digest": task_digest,
            }
        )
    manifest = {
        "schema_version": collector.ARTIFACT_MANIFEST_SCHEMA,
        "run_id": run["run_id"],
        "nonce": run["nonce"],
        "artifacts": artifacts,
        "skill_runtime_evidence": {
            "skill_name": "enterprise-launch-readiness",
            "assigned_worker": "skill-curator",
            "worker_matrix_user_id": "@skill-curator:matrix.local",
            "skill_path": skill_path.name,
            "skill_digest": skill_digest,
            "loaded_event_id": "$skill-loaded",
        },
    }
    manifest_path = tmp_path / "manifest.json"
    _write_json(manifest_path, manifest)

    events: list[dict[str, Any]] = []
    for index, name in enumerate(NAMES):
        events.append(
            {
                "event_id": f"$join-{name}",
                "room_id": run["team"]["room_id"],
                "sender": "@homeserver:matrix.local",
                "type": "m.room.member",
                "state_key": f"@{name}:matrix.local",
                "origin_server_ts": run["issued_at_ms"] + 100 + index,
                "content": {"membership": "join"},
            }
        )
    events.append(
        {
            "event_id": "$delegation",
            "room_id": run["team"]["room_id"],
            "sender": "@change-coordinator:matrix.local",
            "type": "m.room.message",
            "origin_server_ts": run["issued_at_ms"] + 200,
            "content": {
                "body": "delegate bounded candidate work",
                "orgrebase.run": _payload(
                    run,
                    "change-coordinator",
                    "LEADER_DELEGATION",
                    orchestration_plan_digest=PLAN_DIGEST,
                ),
            },
        }
    )
    events.append(
        {
            "event_id": "$skill-loaded",
            "room_id": run["team"]["room_id"],
            "sender": "@skill-curator:matrix.local",
            "type": "m.room.message",
            "origin_server_ts": run["issued_at_ms"] + 250,
            "content": {
                "orgrebase.run": _payload(
                    run,
                    "skill-curator",
                    "SKILL_LOADED",
                    skill_name="enterprise-launch-readiness",
                    skill_digest=skill_digest,
                )
            },
        }
    )
    for index, artifact in enumerate(artifacts):
        worker = artifact["producer_worker"]
        events.append(
            {
                "event_id": f"$candidate-{worker}",
                "room_id": run["team"]["room_id"],
                "sender": artifact["producer_matrix_user_id"],
                "type": "m.room.message",
                "origin_server_ts": run["issued_at_ms"] + 300 + index,
                "content": {
                    "orgrebase.run": _payload(
                        run,
                        worker,
                        "WORKER_CANDIDATE",
                        artifact_ref=artifact["ref"],
                        artifact_digest=artifact["digest"],
                        candidate_only=True,
                        orchestration_plan_digest=PLAN_DIGEST,
                        delegation_task_digest=artifact["delegation_task_digest"],
                        input_refs=INPUT_REFS,
                    )
                },
            }
        )
    events_path = tmp_path / "events.jsonl"
    _write_jsonl(events_path, events)
    calls = [
        {
            "schema_version": collector.MODEL_CALL_SCHEMA,
            "run_id": run["run_id"],
            "nonce": run["nonce"],
            "worker_name": artifact["producer_worker"],
            "status": "SUCCEEDED",
            "provider_request_id": f"req_provider_{index:03d}",
            "model": "gpt-5.4",
            "timestamp_ms": run["issued_at_ms"] + 275 + index,
            "artifact_ref": artifact["ref"],
            "artifact_digest": artifact["digest"],
        }
        for index, artifact in enumerate(artifacts, start=1)
    ]
    calls_path = tmp_path / "model-calls.jsonl"
    _write_jsonl(calls_path, calls)

    worker_items = []
    team_members = []
    pod_items = []
    for worker in run["workers"]:
        worker_items.append(
            {
                "metadata": {
                    "namespace": worker["namespace"],
                    "name": worker["resource_name"],
                    "uid": worker["uid"],
                    "generation": worker["generation"],
                    "resourceVersion": f"rv-{worker['worker_name']}",
                },
                "spec": {
                    "workerName": worker["worker_name"],
                    "model": worker["model"],
                    "runtime": worker["runtime"],
                    "image": worker["image"],
                    "skills": worker["required_skills"],
                },
                "status": {
                    "observedGeneration": worker["generation"],
                    "phase": "Running",
                    "specHash": f"spec-{worker['worker_name']}",
                    "matrixUserID": worker["matrix_user_id"],
                },
            }
        )
        team_members.append(
            {
                "name": worker["resource_name"],
                "runtimeName": worker["worker_name"],
                "role": worker["role"],
                "matrixUserID": worker["matrix_user_id"],
                "observed": True,
                "ready": True,
                "phase": "Running",
                "specHash": f"spec-{worker['worker_name']}",
            }
        )
        pod_items.append(
            {
                "metadata": {
                    "namespace": "agentteams",
                    "name": f"pod-{worker['worker_name']}",
                    "uid": f"pod-uid-{worker['worker_name']}",
                    "resourceVersion": f"pod-rv-{worker['worker_name']}",
                    "labels": {"agentteams.io/worker": worker["resource_name"]},
                },
                "spec": {
                    "containers": [
                        {
                            "name": "worker",
                            "image": "registry.local/agentteams/openclaw:v1.2.2",
                        }
                    ]
                },
                "status": {
                    "phase": "Running",
                    "conditions": [{"type": "Ready", "status": "True"}],
                    "containerStatuses": [
                        {
                            "name": "worker",
                            "imageID": "docker-pullable://openclaw@sha256:" + "2" * 64,
                        }
                    ],
                },
            }
        )
    resources = {
        "workers.agentteams.io": {"items": worker_items},
        "teams.agentteams.io": {
            "items": [
                {
                    "metadata": {
                        "namespace": "agentteams",
                        "name": run["team"]["name"],
                        "uid": run["team"]["uid"],
                        "generation": run["team"]["generation"],
                        "resourceVersion": "team-rv",
                    },
                    "spec": {
                        "teamName": run["team"]["name"],
                        "workerMembers": [
                            {"name": item["resource_name"], "role": item["role"]}
                            for item in run["workers"]
                        ],
                    },
                    "status": {
                        "phase": "Active",
                        "leaderReady": True,
                        "readyWorkers": 5,
                        "teamRoomID": run["team"]["room_id"],
                        "members": team_members,
                    },
                }
            ]
        },
        "pods": {"items": pod_items},
    }
    monkeypatch.setattr(collector, "_kubectl_json", lambda resource: resources[resource])
    monkeypatch.setattr(collector, "_context", lambda: "kind-agentteams-live")
    return {
        "run": run,
        "run_path": run_path,
        "events": events,
        "events_path": events_path,
        "manifest": manifest,
        "manifest_path": manifest_path,
        "calls": calls,
        "calls_path": calls_path,
        "artifact_root": artifact_root,
        "ledger_path": tmp_path / "nonce-ledger.json",
        "resources": resources,
    }


def _collect(bundle: dict[str, Any]) -> dict[str, Any]:
    return collector.collect(
        run_envelope_path=bundle["run_path"],
        matrix_events_path=bundle["events_path"],
        artifact_manifest_path=bundle["manifest_path"],
        model_calls_path=bundle["calls_path"],
        artifact_root=bundle["artifact_root"],
        nonce_ledger_path=bundle["ledger_path"],
        now_ms=NOW_MS,
    )


def _assert_code(bundle: dict[str, Any], code: str) -> None:
    with pytest.raises(collector.EvidenceError) as raised:
        _collect(bundle)
    assert raised.value.code == code


def test_live_collector_binds_all_authoritative_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _live_bundle(tmp_path, monkeypatch)
    report = _collect(bundle)
    assert report["evidence_class"] == "LIVE_AGENTTEAMS"
    assert report["evidence"]["kubernetes_context"] == "kind-agentteams-live"
    assert len(report["evidence"]["matrix"]["candidate_senders"]) == 3
    assert report["evidence"]["matrix"]["orchestration_plan_digest"] == PLAN_DIGEST
    assert len(report["evidence"]["matrix"]["candidate_task_bindings"]) == 3
    assert report["evidence"]["artifacts"]["skill"]["loaded_event_id"] == "$skill-loaded"
    assert report["receipt_digest"].startswith("sha256:")


def test_text_mentions_cannot_upgrade_to_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _live_bundle(tmp_path, monkeypatch)
    for event in bundle["events"]:
        event["content"].pop("orgrebase.run", None)
        event["content"]["body"] = "product-steward legal-steward gtm-steward"
    _write_jsonl(bundle["events_path"], bundle["events"])
    _assert_code(bundle, "MATRIX_STRUCTURED_EVENTS_MISSING")


def test_matrix_sender_must_equal_worker_status_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _live_bundle(tmp_path, monkeypatch)
    bundle["events"][-1]["sender"] = "@impostor:matrix.local"
    _write_jsonl(bundle["events_path"], bundle["events"])
    _assert_code(bundle, "MATRIX_SENDER_MISMATCH")


def test_latest_matrix_membership_must_be_join(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _live_bundle(tmp_path, monkeypatch)
    bundle["events"].append(
        {
            "event_id": "$leave-gtm",
            "room_id": bundle["run"]["team"]["room_id"],
            "sender": "@homeserver:matrix.local",
            "type": "m.room.member",
            "state_key": "@gtm-steward:matrix.local",
            "origin_server_ts": bundle["run"]["issued_at_ms"] + 299,
            "content": {"membership": "leave"},
        }
    )
    _write_jsonl(bundle["events_path"], bundle["events"])
    _assert_code(bundle, "MATRIX_MEMBERSHIP_INVALID")


def test_conflicting_membership_at_same_timestamp_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _live_bundle(tmp_path, monkeypatch)
    join = next(
        event
        for event in bundle["events"]
        if event.get("state_key") == "@change-coordinator:matrix.local"
    )
    bundle["events"].append(
        {
            **join,
            "event_id": "$conflicting-leave",
            "content": {"membership": "leave"},
        }
    )
    _write_jsonl(bundle["events_path"], bundle["events"])
    _assert_code(bundle, "MATRIX_MEMBERSHIP_INVALID")


def test_matrix_run_nonce_mismatch_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _live_bundle(tmp_path, monkeypatch)
    bundle["events"][-1]["content"]["orgrebase.run"]["nonce"] = "b" * 64
    _write_jsonl(bundle["events_path"], bundle["events"])
    _assert_code(bundle, "MATRIX_RUN_MISMATCH")


def test_candidate_artifact_digest_is_recomputed_from_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _live_bundle(tmp_path, monkeypatch)
    (bundle["artifact_root"] / "gtm-steward.json").write_text("tampered\n", encoding="utf-8")
    _assert_code(bundle, "ARTIFACT_DIGEST_MISMATCH")


def test_skill_assignment_without_runtime_bytes_is_not_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _live_bundle(tmp_path, monkeypatch)
    (bundle["artifact_root"] / "runtime-skill.md").write_text("different Skill\n", encoding="utf-8")
    _assert_code(bundle, "SKILL_DIGEST_MISMATCH")


def test_provider_request_id_is_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _live_bundle(tmp_path, monkeypatch)
    bundle["calls"][0]["provider_request_id"] = ""
    _write_jsonl(bundle["calls_path"], bundle["calls"])
    _assert_code(bundle, "MODEL_CALL_EVIDENCE_MISSING")


def test_each_candidate_artifact_requires_its_own_model_call_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _live_bundle(tmp_path, monkeypatch)
    bundle["calls"][0]["artifact_digest"] = "sha256:" + "f" * 64
    _write_jsonl(bundle["calls_path"], bundle["calls"])
    _assert_code(bundle, "MODEL_CALL_EVIDENCE_MISSING")


def test_skill_event_sender_must_be_joined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _live_bundle(tmp_path, monkeypatch)
    bundle["events"] = [
        event
        for event in bundle["events"]
        if event.get("state_key") != "@skill-curator:matrix.local"
    ]
    _write_jsonl(bundle["events_path"], bundle["events"])
    _assert_code(bundle, "MATRIX_MEMBERSHIP_INVALID")


def test_pod_must_expose_immutable_runtime_image_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _live_bundle(tmp_path, monkeypatch)
    pod = bundle["resources"]["pods"]["items"][0]
    bundle["run"]["runtime_pods"][0]["image"] = "openclaw:latest"
    bundle["run"]["runtime_pods"][0]["image_id"] = "docker://openclaw:latest"
    _write_json(bundle["run_path"], bundle["run"])
    pod["spec"]["containers"][0]["image"] = "openclaw:latest"
    pod["status"]["containerStatuses"][0]["imageID"] = "docker://openclaw:latest"
    _assert_code(bundle, "MUTABLE_RUNTIME_IMAGE")


def test_runtime_pod_image_binding_rejects_snapshot_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _live_bundle(tmp_path, monkeypatch)
    pod = bundle["resources"]["pods"]["items"][0]
    pod["status"]["containerStatuses"][0]["imageID"] = (
        "docker-pullable://openclaw@sha256:" + "9" * 64
    )
    _assert_code(bundle, "POD_IDENTITY_MISMATCH")


def test_same_nonce_cannot_bind_a_different_source_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _live_bundle(tmp_path, monkeypatch)
    first = _collect(bundle)
    assert first["status"] == "PASS"
    bundle["events"][0]["content"]["displayname"] = "changed irrelevant source byte"
    _write_jsonl(bundle["events_path"], bundle["events"])
    _assert_code(bundle, "NONCE_REPLAY_DETECTED")
