from __future__ import annotations

import importlib.util
import json
import stat
import subprocess
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _script(name: str) -> ModuleType:
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DISPATCH = _script("dispatch_fresh_core_agentteams_run")


def _completed(
    command: list[str], return_code: int, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, return_code, stdout, stderr)


def test_session_id_is_a_stable_uuid_bound_to_run_worker_and_attempt() -> None:
    values = {
        DISPATCH._session_id(run_id="run-a", worker_name="product-steward", attempt="a1"),
        DISPATCH._session_id(run_id="run-b", worker_name="product-steward", attempt="a1"),
        DISPATCH._session_id(run_id="run-a", worker_name="legal-steward", attempt="a1"),
        DISPATCH._session_id(run_id="run-a", worker_name="product-steward", attempt="a2"),
    }
    assert len(values) == 4
    first = DISPATCH._session_id(
        run_id="run-a", worker_name="product-steward", attempt="a1"
    )
    assert first == DISPATCH._session_id(
        run_id="run-a", worker_name="product-steward", attempt="a1"
    )
    parsed = uuid.UUID(first)
    assert parsed.version == 5
    assert str(parsed) == first


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ({"result": {"payloads": [{"text": "Candidate written."}]}}, None),
        (
            {
                "result": {
                    "payloads": [
                        {"text": "⚠️ Agent failed before reply: Network error."}
                    ]
                }
            },
            "OPENCLAW_NETWORK_ERROR",
        ),
        (
            {"result": {"payloads": [{"text": "No response generated."}]}},
            "OPENCLAW_NO_RESPONSE",
        ),
        ({"result": {"payloads": []}}, "MISSING_OPENCLAW_PAYLOAD"),
        ({"status": "failed", "result": {"payloads": [{"text": "x"}]}},
         "OPENCLAW_FAILED_STATUS"),
    ],
)
def test_openclaw_machine_response_is_fail_closed(
    response: dict[str, Any], expected: str | None
) -> None:
    assert DISPATCH._openclaw_failure_code(json.dumps(response)) == expected


def test_openclaw_invalid_json_is_fail_closed() -> None:
    assert DISPATCH._openclaw_failure_code("not-json") == "INVALID_OPENCLAW_JSON"


def _bound_product_candidate() -> tuple[dict[str, Any], dict[str, Any]]:
    digest = f"sha256:{'a' * 64}"
    bindings = {
        "run_envelope_digest": f"sha256:{'b' * 64}",
        "candidate_schema_digest": f"sha256:{'c' * 64}",
        "orchestration_plan_digest": f"sha256:{'d' * 64}",
        "delegation_task_digest": f"sha256:{'e' * 64}",
        "input_refs": [f"sha256:{'f' * 64}"],
    }
    projection = {
        "run_id": "run:bound",
        "nonce": "nonce-bound",
        "digest": digest,
        "bindings": bindings,
    }
    candidate = {
        "schema_version": DISPATCH.CANDIDATE_SCHEMA,
        "run_id": projection["run_id"],
        "nonce": projection["nonce"],
        "worker_name": "product-steward",
        "candidate_only": True,
        "run_envelope_digest": bindings["run_envelope_digest"],
        "task_projection_digest": digest,
        "candidate_schema_digest": bindings["candidate_schema_digest"],
        "orchestration_plan_digest": bindings["orchestration_plan_digest"],
        "delegation_task_digest": bindings["delegation_task_digest"],
        "input_refs": bindings["input_refs"],
        "output": {
            "ClaimDeltaCandidate": {
                "object_id": "claim:product.launch_date",
                "base_value": "2026-09-01",
                "proposed_value": "2026-09-15",
            }
        },
        "uncertainty": [],
        "tool_receipt_refs": [],
        "prohibited_actions_respected": sorted(DISPATCH.REQUIRED_PROHIBITIONS),
    }
    return candidate, projection


def test_product_candidate_rejects_unwrapped_primary_output() -> None:
    candidate, projection = _bound_product_candidate()
    DISPATCH._validate_candidate(candidate, projection, "product-steward")
    candidate["output"] = candidate["output"]["ClaimDeltaCandidate"]

    with pytest.raises(DISPATCH.DispatchError, match="primary output kind invalid"):
        DISPATCH._validate_candidate(candidate, projection, "product-steward")


def test_candidate_rejects_missing_top_level_required_key() -> None:
    candidate, projection = _bound_product_candidate()
    del candidate["uncertainty"]

    with pytest.raises(DISPATCH.DispatchError, match="required keys missing: uncertainty"):
        DISPATCH._validate_candidate(candidate, projection, "product-steward")


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        (None, DISPATCH.SPECIALISTS),
        (["legal-steward"], ("legal-steward",)),
        (
            ["skill-curator", "product-steward"],
            ("skill-curator", "product-steward"),
        ),
    ],
)
def test_worker_selection_defaults_to_four_and_preserves_requested_subset(
    requested: list[str] | None, expected: tuple[str, ...]
) -> None:
    assert DISPATCH._selected_workers(requested) == expected


def test_worker_selection_rejects_duplicate_role() -> None:
    with pytest.raises(DISPATCH.DispatchError, match="must be unique"):
        DISPATCH._selected_workers(["product-steward", "product-steward"])


@pytest.mark.parametrize(
    ("worker_args", "expected"),
    [
        ([], DISPATCH.SPECIALISTS),
        (
            ["--worker", "legal-steward", "--worker", "skill-curator"],
            ("legal-steward", "skill-curator"),
        ),
    ],
)
def test_main_all_actions_only_receive_selected_workers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    worker_args: list[str],
    expected: tuple[str, ...],
) -> None:
    entries = {worker: {"worker_name": worker} for worker in DISPATCH.SPECIALISTS}
    monkeypatch.setattr(
        DISPATCH,
        "_validated_bundle",
        lambda _bundle, _now: (
            {"run_id": "run-test"},
            {"team": {"namespace": "test-namespace"}},
            entries,
        ),
    )
    calls: list[tuple[str, tuple[str, ...]]] = []

    def record(name: str):
        def inner(**kwargs: Any) -> None:
            calls.append((name, kwargs["workers"]))

        return inner

    monkeypatch.setattr(DISPATCH, "stage", record("stage"))
    monkeypatch.setattr(DISPATCH, "execute", record("run"))
    monkeypatch.setattr(DISPATCH, "collect", record("collect"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "dispatch",
            "--bundle-dir",
            str(tmp_path),
            "--action",
            "all",
            *worker_args,
        ],
    )

    DISPATCH.main()

    assert calls == [("stage", expected), ("run", expected), ("collect", expected)]
    output = json.loads(capsys.readouterr().out)
    assert output["workers"] == len(expected)
    assert output["worker_names"] == list(expected)


def _execution_bundle(tmp_path: Path) -> tuple[Path, dict[str, Any], dict[str, str]]:
    runtime = {
        "projection": "/runtime/projection.json",
        "prompt": "/runtime/prompt.txt",
        "candidate_schema": "/runtime/candidate.schema.json",
        "run_envelope": "/runtime/run-envelope.json",
        "result": "/runtime/result.json",
    }
    projection = {
        "run_id": "run:bound",
        "runtime_paths": runtime,
    }
    files = {
        "workers/product-steward/projection.json": json.dumps(projection).encode(),
        "workers/product-steward/prompt.txt": b"derive candidate",
        "candidate-result.schema.json": b"{}",
        "run-envelope.json": b"{}",
    }
    for relative, content in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    entry = {
        "worker_name": "product-steward",
        "projection_path": "workers/product-steward/projection.json",
        "prompt_path": "workers/product-steward/prompt.txt",
    }
    digest_by_remote = {
        runtime["projection"]: DISPATCH._bytes_digest(
            files["workers/product-steward/projection.json"]
        ),
        runtime["prompt"]: DISPATCH._bytes_digest(
            files["workers/product-steward/prompt.txt"]
        ),
        runtime["candidate_schema"]: DISPATCH._bytes_digest(
            files["candidate-result.schema.json"]
        ),
        runtime["run_envelope"]: DISPATCH._bytes_digest(files["run-envelope.json"]),
    }
    return tmp_path, entry, digest_by_remote


@pytest.mark.parametrize(
    ("payload", "remote_result_return_code", "expected_code"),
    [
        (
            {"result": {"payloads": [{"text": "Network error."}]}},
            0,
            "OPENCLAW_NETWORK_ERROR",
        ),
        (
            {"result": {"payloads": [{"text": "Candidate written."}]}},
            1,
            "REMOTE_RESULT_MISSING_OR_EMPTY",
        ),
        (
            {"result": {"payloads": []}},
            0,
            "MISSING_OPENCLAW_PAYLOAD",
        ),
    ],
)
def test_execute_one_rejects_false_zero_exit_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload: dict[str, Any],
    remote_result_return_code: int,
    expected_code: str,
) -> None:
    bundle, entry, digest_by_remote = _execution_bundle(tmp_path)
    monkeypatch.setattr(
        DISPATCH,
        "_remote_digest",
        lambda **kwargs: digest_by_remote[kwargs["path"]],
    )
    observed_session_ids: list[str] = []

    def fake_run(
        command: list[str], *, timeout: int = 30, allow_failure: bool = False
    ) -> subprocess.CompletedProcess[str]:
        del timeout, allow_failure
        if "openclaw" in command:
            assert "--agent" not in command
            observed_session_ids.append(command[command.index("--session-id") + 1])
            return _completed(
                command,
                0,
                json.dumps(payload),
                "Authorization: Bearer must-not-enter-attempt-log",
            )
        if command[-3:-1] == ["test", "-e"]:
            return _completed(command, 1)
        if command[-3:-1] == ["test", "-s"]:
            return _completed(command, remote_result_return_code)
        raise AssertionError(command)

    monkeypatch.setattr(DISPATCH, "_run", fake_run)

    result = DISPATCH._execute_one(
        bundle=bundle,
        entry=entry,
        pod={"name": "product-pod"},
        context="test-context",
        namespace="test-namespace",
        attempt="a1",
        timeout_seconds=60,
    )

    assert result["outcome"] == "FAILED"
    assert expected_code in result["failure_codes"]
    assert "stderr" not in result
    assert result["stderr_present"] is True
    assert "must-not-enter" not in json.dumps(result)
    assert len(observed_session_ids) == 1
    assert uuid.UUID(observed_session_ids[0]).version == 5


@pytest.mark.parametrize(
    ("failure_code", "stdout"),
    [
        ("OPENCLAW_NETWORK_ERROR", "private model payload"),
        ("REMOTE_RESULT_MISSING_OR_EMPTY", "private model payload"),
    ],
)
def test_execute_persists_private_log_then_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_code: str,
    stdout: str,
) -> None:
    worker = "product-steward"
    monkeypatch.setattr(
        DISPATCH,
        "_runtime_pods",
        lambda _envelope, _workers: {worker: {"name": "product-pod"}},
    )
    monkeypatch.setattr(
        DISPATCH,
        "_execute_one",
        lambda **_kwargs: {
            "worker_name": worker,
            "outcome": "FAILED",
            "failure_codes": [failure_code],
            "stdout": stdout,
        },
    )

    with pytest.raises(DISPATCH.DispatchError, match=failure_code):
        DISPATCH.execute(
            bundle=tmp_path,
            entries={worker: {"worker_name": worker}},
            envelope={},
            context="test-context",
            namespace="test-namespace",
            attempt="a1",
            timeout_seconds=60,
            workers=(worker,),
        )

    log_path = tmp_path / "dispatch" / "a1" / f"{worker}.json"
    assert json.loads(log_path.read_bytes())["stdout"] == stdout
    assert stat.S_IMODE(log_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(log_path.parent.stat().st_mode) == 0o700


@pytest.mark.parametrize("copy_failure", [False, True])
def test_collect_removes_empty_file_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    copy_failure: bool,
) -> None:
    worker = "product-steward"
    projection_path = tmp_path / "workers" / worker / "projection.json"
    projection_path.parent.mkdir(parents=True)
    projection_path.write_text(
        json.dumps({"runtime_paths": {"result": "/runtime/result.json"}}),
        encoding="utf-8",
    )
    entry = {
        "projection_path": str(projection_path.relative_to(tmp_path)),
        "result_bundle_path": f"workers/{worker}/result.json",
    }
    monkeypatch.setattr(
        DISPATCH,
        "_runtime_pods",
        lambda _envelope, _workers: {worker: {"name": "product-pod"}},
    )

    def fake_copy(
        command: list[str], *, timeout: int = 30, allow_failure: bool = False
    ) -> subprocess.CompletedProcess[str]:
        del timeout, allow_failure
        destination = Path(command[-3])
        destination.write_bytes(b"")
        if copy_failure:
            raise DISPATCH.DispatchError("kubectl command failed with exit code 1")
        return _completed(command, 0)

    monkeypatch.setattr(DISPATCH, "_run", fake_copy)

    expected = "copy failed closed" if copy_failure else "copy missing or empty"
    with pytest.raises(DISPATCH.DispatchError, match=expected):
        DISPATCH.collect(
            bundle=tmp_path,
            entries={worker: entry},
            envelope={},
            context="test-context",
            namespace="test-namespace",
            workers=(worker,),
        )
    assert not (tmp_path / entry["result_bundle_path"]).exists()


def test_command_failure_does_not_echo_provider_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "Authorization: Bearer super-secret"
    monkeypatch.setattr(
        DISPATCH.subprocess,
        "run",
        lambda *args, **kwargs: _completed(args[0], 1, "", secret),
    )

    with pytest.raises(DISPATCH.DispatchError) as caught:
        DISPATCH._run(["kubectl", "get", "pod"])

    assert secret not in str(caught.value)
    assert "super-secret" not in str(caught.value)
