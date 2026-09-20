"""Execute the pinned local task lifecycle, without external models or messaging."""

from __future__ import annotations

import contextlib
import copy
import errno
import io
import json
import os
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue
from threading import Event
from types import SimpleNamespace

import pytest

from orgrebase.agentteams_source import default_agentteams_checkout
from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.workspace import change_agentteams as native_module
from orgrebase.workspace.change_agentteams import (
    ChangeAdvisoryNativeSession,
    ChangeAgentTeamsConfig,
    NativeChangeExecutionError,
    verified_native_bundle,
    verify_execution,
)
from orgrebase.workspace.preview_execution import read_attempt_summary
from tests.workspace.test_continuous_changes import make_service, proposal

ROOT = Path(__file__).resolve().parents[2]
CHECKOUT = default_agentteams_checkout(ROOT)
LOCK = ROOT / "agentteams" / "teamharness-lock.json"


def configure(service, root):
    adapter = service.advisory_factory
    adapter.native_config = ChangeAgentTeamsConfig(CHECKOUT, LOCK, root)
    adapter.native_required = True
    adapter.max_elapsed_seconds = 30
    return adapter


@pytest.fixture(scope="module")
def native_change(tmp_path_factory):
    assert CHECKOUT.is_dir(), "Fetch the locked AgentTeams checkout before native integration verification"
    root = tmp_path_factory.mktemp("native-change")
    service = make_service(root / "workspace.sqlite")
    configure(service, root / "native")
    event = proposal(service, "native-change", "product_plan", "Enterprise updated")
    service.register_change(event)
    before = service.current_quote().digest
    bundle = service.preview_change(event.event_id)
    yield service, event, bundle, before
    service.close()


def test_real_native_change_creates_delegates_reviews_and_never_applies(native_change):
    service, event, bundle, before = native_change
    receipt = bundle.advisory.native_execution
    assert receipt["status"] == "COMPLETED"
    assert receipt["native_agentteams_observed"] is True
    assert receipt["distributed_execution"] is False
    assert receipt["binding"]["change_set_digest"] == bundle.change_set.digest
    assert receipt["binding"]["preview_digest"] == bundle.preview.digest
    assert receipt["source_verification"]["status"] == "PASS"
    assert len(receipt["tasks"]) == len(bundle.advisory.orchestration_plan.tasks) + 1
    assert receipt["tasks"][-1]["kind"] == "DETERMINISTIC_CONTRACT_REVIEW"
    assert receipt["results"][-1]["verdict"] == "PASS"
    assert [item["action"] for item in receipt["actions"][:2]] == ["create_project", "plan_dag"]
    assert receipt["actions"][-1]["action"] == "complete_project"
    for task in receipt["tasks"]:
        assert [item["action"] for item in receipt["actions"] if item["key"].startswith(task["task_id"] + ":")] == [
            "delegate_task", "ack_task", "submit_task", "check_task", "accept_task_result"]
    assert service.current_quote().digest == before
    assert service._approval_record(event.event_id) is None
    summary = verified_native_bundle(service, bundle)
    assert summary["receipt_digest"] == receipt["digest"]
    assert "raw_actions" not in summary and "source_verification" not in summary
    assert all(item["ok"] is True for item in summary["actions"])
    attempt = read_attempt_summary(service, command=f"change:{event.event_id}")
    assert attempt["state"] == "COMPLETE"
    assert attempt["native_execution"] == summary
    assert service.preview_change(event.event_id).model_dump(mode="json") == bundle.model_dump(mode="json")


@pytest.mark.parametrize("field", ["run_id", "nonce", "tenant_id", "workspace_id", "request_digest", "preview_digest"])
def test_other_execution_binding_is_rejected_even_with_rehashed_receipt(native_change, field):
    service, _event, bundle, _before = native_change
    original = bundle.advisory.native_execution
    changed = copy.deepcopy(original)
    changed["binding"][field] = "other" if "digest" not in field else "sha256:" + "1" * 64
    changed["digest"] = sha256_digest({key: value for key, value in changed.items() if key != "digest"})
    with pytest.raises(IntegrityError, match="AGENTTEAMS_EVIDENCE_INVALID"):
        verify_execution(changed, plan=bundle.advisory.orchestration_plan, advisory=bundle.advisory,
                         expected_binding=original["binding"], config=service.advisory_factory.native_config)


def _replace_response(receipt, action_name, mutate):
    index = next(i for i, item in enumerate(receipt["actions"]) if item["action"] == action_name)
    raw, action = receipt["raw_actions"][index], receipt["actions"][index]
    payload = json.loads(raw["response"]["content"][0]["text"])
    mutate(payload)
    raw["response"]["content"][0]["text"] = json.dumps(payload)
    action["response_digest"] = sha256_digest(raw["response"])
    action["payload_digest"] = sha256_digest(payload)
    action["digest"] = sha256_digest({key: value for key, value in action.items() if key != "digest"})
    receipt["digest"] = sha256_digest({key: value for key, value in receipt.items() if key != "digest"})


@pytest.mark.parametrize("action_name,mutate", [
    ("complete_project", lambda payload: payload["project"].update(project_id="other-project")),
    ("accept_task_result", lambda payload: payload.update(taskId="other-task")),
    ("ack_task", lambda payload: payload.update(spec="other-spec\n")),
    ("check_task", lambda payload: payload["result"].update(summary="other-result")),
    ("ack_task", lambda payload: payload["task"].update(assigned_to="@other:controlled.local")),
    ("delegate_task", lambda payload: payload["notification"].update(roomId="!other:controlled.local")),
])
def test_native_roundtrip_identity_cannot_be_substituted(native_change, action_name, mutate):
    service, _event, bundle, _before = native_change
    original = bundle.advisory.native_execution
    changed = copy.deepcopy(original)
    _replace_response(changed, action_name, mutate)
    with pytest.raises(IntegrityError, match="AGENTTEAMS_EVIDENCE_INVALID"):
        verify_execution(changed, plan=bundle.advisory.orchestration_plan, advisory=bundle.advisory,
                         expected_binding=original["binding"], config=service.advisory_factory.native_config)


def test_advisory_apply_verifier_requires_native_receipt_when_configured(native_change):
    service, _event, bundle, _before = native_change
    collaboration = service._collaboration(bundle)
    verified = service.advisory_verifier.verify(fixture=service._fixture_for_change(bundle.change_spec),
        change_set=bundle.change_set, preview=bundle.preview, collaboration=collaboration,
        run_envelope=bundle.run_envelope)
    assert verified.native_execution == bundle.advisory.native_execution
    collaboration.pop("native_execution")
    with pytest.raises(IntegrityError, match="AGENTTEAMS_EVIDENCE_REQUIRED"):
        service.advisory_verifier.verify(fixture=service._fixture_for_change(bundle.change_spec),
            change_set=bundle.change_set, preview=bundle.preview, collaboration=collaboration,
            run_envelope=bundle.run_envelope)


def test_reference_bundle_serialization_keeps_historical_shape(native_change):
    bundle = native_change[2].advisory.model_copy(update={"native_execution": None})
    assert "native_execution" not in bundle.model_dump(mode="json")


def test_partial_line_cannot_outlive_the_absolute_native_deadline(tmp_path):
    session = ChangeAdvisoryNativeSession.__new__(ChangeAdvisoryNativeSession)
    session.deadline = time.monotonic() + 0.2
    session._owns_process_group = True
    session.process = subprocess.Popen([sys.executable, "-c", (
        "import sys,time;sys.stdin.readline();sys.stdout.write('{');sys.stdout.flush();"
        "time.sleep(2);sys.stdout.write('\"ok\":true}\\n');sys.stdout.flush()")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    started = time.monotonic()
    try:
        with pytest.raises(NativeChangeExecutionError, match="RESULT_UNKNOWN"):
            session._exchange({"request": "test"})
        assert time.monotonic() - started < 0.8
    finally:
        session.close()


def test_close_terminates_owned_descendants_and_does_not_leave_local_writes(tmp_path):
    pid_file = tmp_path / "child.pid"
    session = ChangeAdvisoryNativeSession.__new__(ChangeAdvisoryNativeSession)
    session._owns_process_group = True
    session.process = subprocess.Popen([sys.executable, "-c", (
        "import pathlib,subprocess,sys,time;"
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']);"
        "pathlib.Path(sys.argv[1]).write_text(str(p.pid));time.sleep(30)"), str(pid_file)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    child_pid = None
    try:
        deadline = time.monotonic() + 3
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        child_pid = int(pid_file.read_text())
        session.close()
        assert session.process.poll() is not None
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            status = subprocess.run(["ps", "-o", "stat=", "-p", str(child_pid)],
                                    capture_output=True, text=True, check=False).stdout.strip()
            if not status or status.startswith("Z"):
                break
            time.sleep(0.01)
        assert not status or status.startswith("Z"), f"owned descendant still running: {status}"
    finally:
        session.close()
        if child_pid is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(child_pid, 9)


@pytest.mark.parametrize("platform,reaped,probe,expected", [
    ("darwin", True, "vanishes", None),
    ("darwin", True, "live", PermissionError),
    ("darwin", True, "persistent_eperm", PermissionError),
    ("darwin", False, "vanishes", PermissionError),
    ("linux", True, "vanishes", PermissionError),
])
def test_close_only_accepts_confirmed_vanished_darwin_group(monkeypatch, platform, reaped, probe, expected):
    session = ChangeAdvisoryNativeSession.__new__(ChangeAdvisoryNativeSession)
    session._owns_process_group = True

    def wait(*, timeout):
        if not reaped:
            raise subprocess.TimeoutExpired("controlled-host", timeout)
        return -signal.SIGTERM

    session.process = SimpleNamespace(pid=1234567, stdin=io.BytesIO(), stdout=io.BytesIO(),
        wait=wait, poll=lambda: -signal.SIGTERM if reaped else None)
    signals = []
    probes = 0

    def killpg(pid, sig):
        nonlocal probes
        assert pid == session.process.pid
        signals.append(sig)
        if sig == signal.SIGTERM and reaped:
            return
        if sig == 0:
            probes += 1
            if probe == "live":
                return
            if probe == "vanishes" and probes == 2:
                raise ProcessLookupError(errno.ESRCH, "group vanished")
        raise PermissionError(errno.EPERM, "controlled denial")

    ticks = iter((0.0, 0.1, 0.3))
    monkeypatch.setattr(native_module, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(os, "killpg", killpg)
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    if expected:
        with pytest.raises(expected, match="controlled denial"):
            session.close()
        assert session._owns_process_group is True
    else:
        session.close()
        assert session._owns_process_group is False
        before = list(signals)
        session.close()
        assert signals == before
    assert session.process.stdin.closed and session.process.stdout.closed
    if platform != "darwin" or not reaped:
        assert 0 not in signals
    else:
        assert signals[:2] == [signal.SIGTERM, signal.SIGKILL]
        assert all(sig == 0 for sig in signals[2:])


def test_close_does_not_escalate_after_owned_group_is_absent(monkeypatch):
    session = ChangeAdvisoryNativeSession.__new__(ChangeAdvisoryNativeSession)
    session._owns_process_group = True
    session.process = SimpleNamespace(pid=1234567, stdin=io.BytesIO(), stdout=io.BytesIO(),
        wait=lambda **_kwargs: 0, poll=lambda: 0)
    signals = []

    def killpg(pid, sig):
        assert pid == session.process.pid
        signals.append(sig)
        raise ProcessLookupError(errno.ESRCH, "group vanished")

    monkeypatch.setattr(os, "killpg", killpg)
    session.close()
    session.close()
    assert signals == [signal.SIGTERM]
    assert session._owns_process_group is False
    assert session.process.stdout.closed


def test_close_kills_term_ignoring_descendant_and_stops_its_writes(tmp_path, monkeypatch):
    pid_file, writes = tmp_path / "child.pid", tmp_path / "writes"
    child_code = (
        "import os,pathlib,signal,sys,time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\n"
        "while True:\n"
        " with open(sys.argv[2], 'a') as stream: stream.write('alive\\n')\n"
        " time.sleep(0.02)\n"
    )
    session = ChangeAdvisoryNativeSession.__new__(ChangeAdvisoryNativeSession)
    session._owns_process_group = True
    session.process = subprocess.Popen([sys.executable, "-c", (
        "import subprocess,sys,time;"
        "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2],sys.argv[3]]);time.sleep(30)"
    ), child_code, str(pid_file), str(writes)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, start_new_session=True)
    original = os.killpg
    signals = []

    def observed_killpg(pid, sig):
        assert pid == session.process.pid
        signals.append(sig)
        return original(pid, sig)

    monkeypatch.setattr(os, "killpg", observed_killpg)
    try:
        deadline = time.monotonic() + 3
        while (not pid_file.exists() or not writes.exists()) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pid_file.exists() and writes.exists()
        child_pid = int(pid_file.read_text())
        session.close()
        assert session.process.poll() is not None
        assert signal.SIGKILL in signals
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            status = subprocess.run(["ps", "-o", "stat=", "-p", str(child_pid)],
                                    capture_output=True, text=True, check=False).stdout.strip()
            if not status or status.startswith("Z"):
                break
            time.sleep(0.01)
        assert not status or status.startswith("Z"), f"owned descendant still running: {status}"
        contents = writes.read_bytes()
        time.sleep(0.05)
        assert writes.read_bytes() == contents
        before = list(signals)
        session.close()
        assert signals == before
        assert session.process.stdout.closed
    finally:
        session.close()


def test_rejected_native_response_never_enters_public_success_progress(native_change, monkeypatch):
    receipt = native_change[2].advisory.native_execution
    session = ChangeAdvisoryNativeSession.__new__(ChangeAdvisoryNativeSession)
    session.project_id = receipt["project_id"]
    session.actions, session.raw_actions = [], []
    observed = []
    session.on_progress = observed.append
    raw = copy.deepcopy(receipt["raw_actions"][-1])
    payload = json.loads(raw["response"]["content"][0]["text"])
    payload["project"]["project_id"] = "another-project"
    monkeypatch.setattr(session, "_exchange", lambda message: {
        "payload": payload, "action": receipt["actions"][-1], "raw": raw})
    with pytest.raises(NativeChangeExecutionError, match="PROJECT_MISMATCH"):
        session._call((raw["key"], raw["tool"], raw["request"]))
    assert session.actions == session.raw_actions == observed == []


def test_parallel_changes_publish_bound_progress_without_sharing_process_environment(tmp_path, monkeypatch):
    service = make_service(tmp_path / "parallel.sqlite")
    configure(service, tmp_path / "native")
    events = [proposal(service, "parallel-product", "product_plan", "Enterprise Parallel"),
              proposal(service, "parallel-currency", "currency", "GBP")]
    for event in events:
        service.register_change(event)
    entered, release = Queue(), Event()
    original = ChangeAdvisoryNativeSession.begin_task
    env_before = dict(os.environ)
    def block_after_ack(session, task):
        original(session, task)
        if task.id == session.plan.tasks[0].id:
            entered.put(session.project_id)
            assert release.wait(15), "native candidate was not released"
    monkeypatch.setattr(ChangeAdvisoryNativeSession, "begin_task", block_after_ack)
    before = service.current_quote()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = [pool.submit(service.preview_change, event.event_id) for event in events]
            try:
                projects = {entered.get(timeout=10), entered.get(timeout=10)}
                assert len(projects) == 2
                for event in events:
                    attempt = read_attempt_summary(service, command=f"change:{event.event_id}")
                    native = attempt["native_execution"]
                    assert attempt["state"] == "IN_PROGRESS"
                    assert native["status"] == "RUNNING" and native["project_id"] in projects
                    assert native["actions"][-1]["action"] == "ack_task"
                    assert native["tasks"][0]["status"] == "in_progress"
                    assert native["native_agentteams_observed"] is False
                assert service.current_quote() == before
                assert dict(os.environ) == env_before
            finally:
                release.set()
            completed = [future.result(timeout=15) for future in pending]
        bindings = [bundle.advisory.native_execution["binding"] for bundle in completed]
        assert bindings[0]["run_id"] == bindings[1]["run_id"]
        assert bindings[0]["nonce"] == bindings[1]["nonce"]
        assert bindings[0]["change_set_digest"] != bindings[1]["change_set_digest"]
        assert bindings[0]["attempt_key"] != bindings[1]["attempt_key"]
        assert all(verified_native_bundle(service, bundle) for bundle in completed)
    finally:
        release.set()
        service.close()


def test_lost_response_after_real_submission_does_not_redispatch_or_publish_acceptance(tmp_path, monkeypatch):
    service = make_service(tmp_path / "unknown.sqlite")
    configure(service, tmp_path / "native")
    event = proposal(service, "unknown-native", "product_plan", "Enterprise unknown")
    service.register_change(event)
    before = service.current_quote()
    original = ChangeAdvisoryNativeSession._exchange
    submissions = []
    def lose_submission_response(session, message):
        response = original(session, message)
        if message.get("request", {}).get("action") == "submit_task":
            submissions.append(message["key"])
            raise NativeChangeExecutionError("WORKSPACE_ADVISORY_AGENTTEAMS_RESULT_UNKNOWN")
        return response
    monkeypatch.setattr(ChangeAdvisoryNativeSession, "_exchange", lose_submission_response)
    try:
        with pytest.raises(IntegrityError, match="RESULT_UNKNOWN"):
            service.preview_change(event.event_id)
        native = read_attempt_summary(service, command=f"change:{event.event_id}")["native_execution"]
        assert native["status"] == "RESULT_UNKNOWN"
        assert native["actions"][-1]["action"] == "ack_task"
        assert native["native_agentteams_observed"] is False
        assert service._preview_record(event.event_id) is None
        assert service._approval_record(event.event_id) is None
        assert service.current_quote() == before
        with pytest.raises(IntegrityError, match="ADVISORY_ATTEMPT_FAILED"):
            service.preview_change(event.event_id)
        assert len(submissions) == 1
    finally:
        service.close()


def test_failed_independent_contract_review_cannot_complete_native_project(tmp_path, monkeypatch):
    service = make_service(tmp_path / "review.sqlite")
    configure(service, tmp_path / "native")
    event = proposal(service, "rejected-native", "product_plan", "Enterprise rejected")
    service.register_change(event)
    before = service.current_quote()
    def reject(**kwargs):
        raise IntegrityError("INDEPENDENT_CONTRACT_REJECTED")
    monkeypatch.setattr(service.advisory_verifier, "verify", reject)
    try:
        with pytest.raises(IntegrityError, match="INDEPENDENT_CONTRACT_REJECTED"):
            service.preview_change(event.event_id)
        native = read_attempt_summary(service, command=f"change:{event.event_id}")["native_execution"]
        assert native["status"] == "FAILED" and native["native_agentteams_observed"] is False
        assert native["tasks"][-1]["kind"] == "DETERMINISTIC_CONTRACT_REVIEW"
        assert native["tasks"][-1]["last_action"] == "ack_task"
        assert native["review"] is None
        assert native["actions"][-1]["action"] == "ack_task"
        assert service.current_quote() == before and service._preview_record(event.event_id) is None
    finally:
        service.close()
