from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from orgrebase.agentteams_source import default_agentteams_checkout, load_agentteams_source
from orgrebase.workspace.native_taskflow import (
    DOMAINS,
    EVIDENCE_CLASS,
    AgentTeamsLifecycleReceipt,
    LifecycleJournal,
    NativeTaskflowError,
    admit_candidate,
    digest_json,
    run_native_taskflow_slice,
    verify_native_taskflow_evidence,
    verify_teamharness_checkout,
)

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / "agentteams/teamharness-lock.json"
CHECKOUT = default_agentteams_checkout(ROOT)

BINDING_OBSERVATION_FIELDS = (
    "run_id",
    "nonce",
    "project_id",
    "plan_digest",
    "plan_revision",
    "delegation_digest",
    "task_id",
    "domain",
    "assignee",
    "room_id",
    "context_projection_digest",
    "source_lock_digest",
    "attempt",
    "fencing_token",
    "candidate_only",
    "target_writes",
)


def _observed_binding(binding: dict) -> dict:
    return {field: binding[field] for field in BINDING_OBSERVATION_FIELDS}


def _task_result_bytes(output: Path, receipt: dict, task_id: str) -> bytes:
    check_action = next(
        item
        for item in receipt["actions"]
        if item["action"] == "check_task" and task_id in item["key"]
    )
    raw = json.loads((output / check_action["raw_ref"]).read_text(encoding="utf-8"))
    payload = json.loads(raw["response"]["content"][0]["text"])
    return str(payload["result"]["summary"]).encode("utf-8")


def _write_rehashed_receipt(output: Path, receipt: dict) -> None:
    base = dict(receipt)
    base.pop("receipt_digest", None)
    receipt["receipt_digest"] = digest_json(base)
    (output / "lifecycle-receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


@pytest.fixture(scope="module")
def native_pack(tmp_path_factory: pytest.TempPathFactory):
    if not CHECKOUT.is_dir():
        pytest.skip("pinned AgentTeams checkout is unavailable")
    output = tmp_path_factory.mktemp("native-taskflow")
    receipt = run_native_taskflow_slice(
        checkout=CHECKOUT,
        output_dir=output,
        lock_path=LOCK,
    )
    return output, receipt


def test_pinned_source_lock_covers_actual_mcp_boundary() -> None:
    if not CHECKOUT.is_dir():
        pytest.skip("pinned AgentTeams checkout is unavailable")
    result = verify_teamharness_checkout(CHECKOUT, LOCK)
    assert result["status"] == "PASS"
    assert result["checkout"] == "agentteams://pinned-checkout"
    assert result["path_disclosure_status"] == "PUBLIC_PATH_REDACTED"
    assert result["commit"] == load_agentteams_source(ROOT).commit
    assert set(result["files"]) == {
        "plugins/teamharness/mcp/server.py",
        "plugins/teamharness/mcp/message_tool.py",
        "plugins/teamharness/mcp/roomflow_tool.py",
    }


def test_native_positive_chain_is_one_run_four_domains(native_pack) -> None:
    _output, receipt = native_pack
    assert receipt["evidence_class"] == EVIDENCE_CLASS
    assert receipt["external_promotion_status"] == "NOT_RUN"
    assert receipt["project_terminal_state"] == "completed"
    completed = {
        item["domain"]
        for item in receipt["bindings"]
        if item["attempt"] == 1 and item["status"] == "completed"
    }
    assert completed == set(DOMAINS)
    assert {item["run_id"] for item in receipt["bindings"]} == {receipt["run_id"]}
    assert {item["nonce"] for item in receipt["bindings"]} == {receipt["nonce"]}
    assert all(item["target_writes"] == 0 for item in receipt["bindings"])
    assert receipt["claim_boundary"] == "PINNED_IN_PROCESS_CALL_TOOL_NOT_LIVE_WORKER_HANDOFF"
    assert receipt["oac_integration_status"] == "NOT_INTEGRATED"
    positive = [item for item in receipt["actions"] if not item["key"].endswith(":late-submit")]
    assert positive and all(item["ok"] is True for item in positive)
    assert next(item for item in receipt["actions"] if item["key"].endswith(":late-submit"))["ok"] is False
    seen = {(item["tool"], item["action"]) for item in receipt["actions"]}
    assert {
        ("projectflow", "create_project"),
        ("projectflow", "plan_dag"),
        ("projectflow", "accept_task_result"),
        ("projectflow", "complete_project"),
        ("taskflow", "delegate_task"),
        ("taskflow", "ack_task"),
        ("taskflow", "submit_task"),
        ("taskflow", "check_task"),
    }.issubset(seen)


def test_duplicate_delegation_emits_one_matrix_event(native_pack) -> None:
    _output, receipt = native_pack
    product_actions = [
        item
        for item in receipt["actions"]
        if "-product-a1:delegate" in item["key"]
        and item["action"] == "delegate_task"
    ]
    assert len(product_actions) == 2
    # Four accepted Domain tasks + cancelled attempt + replacement attempt.
    assert receipt["matrix_transport"]["unique_assignment_events"] == 6


def test_at_success_control_reject_has_zero_canonical_writes(native_pack) -> None:
    _output, receipt = native_pack
    rejection = next(
        item
        for item in receipt["control_decisions"]
        if "RESULT_DIGEST_MISMATCH" in item["reason_codes"]
    )
    assert rejection["verdict"] == "REJECT"
    assert rejection["target_writes"] == 0
    assert receipt["canonical_target_writes"] == 0


def test_cancel_reassign_fences_late_result(native_pack) -> None:
    output, receipt = native_pack
    old = next(item for item in receipt["bindings"] if item["status"] == "cancelled")
    replacement = next(
        item for item in receipt["bindings"] if item.get("predecessor_task_id") == old["task_id"]
    )
    assert replacement["attempt"] == 2
    assert replacement["plan_revision"] == 2
    assert replacement["plan_digest"] == receipt["terminal_plan_digest"]
    assert replacement["fencing_token"] != old["fencing_token"]
    result = json.loads(
        next(
            json.loads((output / item["raw_ref"]).read_text(encoding="utf-8"))[
                "request"
            ]["payload"]["summary"]
            for item in receipt["actions"]
            if item["key"].endswith(":late-submit")
        )
    )
    stale = admit_candidate(
        old,
        json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(),
        observed_binding=_observed_binding(old),
        active_fencing_token=replacement["fencing_token"],
        active_attempt_ref=replacement["attempt_ref"],
    )
    assert stale["verdict"] == "REJECT"
    assert "STALE_ATTEMPT_FENCED" in stale["reason_codes"]
    assert stale["active_fencing_token"] == replacement["fencing_token"]
    late_action = next(item for item in receipt["actions"] if item["key"].endswith(":late-submit"))
    assert late_action["ok"] is False


def test_independent_verifier_is_restart_idempotent(native_pack) -> None:
    output, receipt = native_pack
    first = verify_native_taskflow_evidence(
        evidence_dir=output, checkout=CHECKOUT, lock_path=LOCK
    )
    second = verify_native_taskflow_evidence(
        evidence_dir=output, checkout=CHECKOUT, lock_path=LOCK
    )
    assert first == second
    assert first["receipt_digest"] == receipt["receipt_digest"]
    assert first["status"] == "PASS"
    assert first["verification_strength"] == "PINNED_CHECKOUT_REPLAY"


def test_retained_lock_replay_does_not_require_checkout(native_pack) -> None:
    output, receipt = native_pack
    result = verify_native_taskflow_evidence(
        evidence_dir=output, lock_path=LOCK
    )
    assert result["status"] == "PASS"
    assert result["receipt_digest"] == receipt["receipt_digest"]
    assert result["verification_strength"] == "RETAINED_LOCK_REPLAY"


def test_public_pack_redacts_every_path_bearing_surface(native_pack) -> None:
    output, receipt = native_pack
    public_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(item for item in output.rglob("*") if item.is_file())
    )
    assert receipt["path_disclosure_status"] == "PUBLIC_PATH_REDACTED"
    assert (
        receipt["raw_mcp_representation"]
        == "SEMANTIC_MCP_WRAPPER_PUBLIC_PATH_REDACTED"
    )
    assert receipt["source_verification"]["checkout"] == "agentteams://pinned-checkout"
    assert (
        receipt["process_storage_adapter"]["path_disclosure_status"]
        == "PUBLIC_PATH_REDACTED"
    )
    assert "orgrebase://runtime-workspace" in public_text
    for forbidden in (
        "/Users/",
        "/private/tmp",
        "/tmp/",
        "/var/folders/",
        "/.semifinal-stage-",
        str(CHECKOUT),
        str(output),
    ):
        assert forbidden not in public_text
    for action in receipt["actions"]:
        raw = json.loads((output / action["raw_ref"]).read_text(encoding="utf-8"))
        assert raw["path_disclosure_status"] == "PUBLIC_PATH_REDACTED"
        assert raw["representation"] == receipt["raw_mcp_representation"]


@pytest.mark.parametrize(
    ("surface", "leaked_path"),
    [
        ("source", "/Users/alice/private-checkout"),
        ("raw", "/private/tmp/agentteams-secret"),
        ("mc", "/.semifinal-stage-random/workspace"),
        ("extra", "/tmp/untracked-public-path"),
    ],
)
def test_verifier_rejects_public_path_leak_on_every_surface(
    native_pack, tmp_path: Path, surface: str, leaked_path: str
) -> None:
    output, receipt = native_pack
    changed = tmp_path / f"path-leak-{surface}"
    shutil.copytree(output, changed)
    if surface == "source":
        path = changed / "source-verification.json"
        text = path.read_text(encoding="utf-8").replace(
            "agentteams://pinned-checkout", leaked_path
        )
        path.write_text(text, encoding="utf-8")
    elif surface == "raw":
        path = changed / receipt["actions"][0]["raw_ref"]
        text = path.read_text(encoding="utf-8").replace(
            "orgrebase://runtime-workspace", leaked_path
        )
        path.write_text(text, encoding="utf-8")
    elif surface == "mc":
        path = changed / "runtime/mc-process-invocations.jsonl"
        path.write_text(
            path.read_text(encoding="utf-8")
            + json.dumps(["mirror", leaked_path], separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )
    else:
        (changed / "untracked-public-note.txt").write_text(
            leaked_path, encoding="utf-8"
        )
    with pytest.raises(
        NativeTaskflowError, match="NATIVE_EVIDENCE_PUBLIC_PATH_DISCLOSURE"
    ):
        verify_native_taskflow_evidence(evidence_dir=changed, lock_path=LOCK)


def test_strict_models_and_external_schema_refs_validate(native_pack) -> None:
    _output, receipt = native_pack
    assert AgentTeamsLifecycleReceipt.model_validate(receipt).receipt_digest == receipt[
        "receipt_digest"
    ]
    lifecycle_schema = json.loads(
        (ROOT / "schemas/workspace-agentteams-lifecycle-receipt.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert lifecycle_schema["additionalProperties"] is False
    assert (
        lifecycle_schema["properties"]["bindings"]["items"]["$ref"]
        == "workspace-agentteams-task-binding.schema.json"
    )
    assert (
        lifecycle_schema["properties"]["control_decisions"]["items"]["$ref"]
        == "workspace-agentteams-control-decision.schema.json"
    )


@pytest.mark.parametrize(
    ("field", "mutated"),
    [
        ("run_id", "run:drift"),
        ("nonce", "f" * 64),
        ("project_id", "drift-project"),
        ("plan_digest", "sha256:" + "f" * 64),
        ("plan_revision", 99),
        ("delegation_digest", "sha256:" + "e" * 64),
        ("task_id", "drift-task"),
        ("domain", "legal"),
        ("assignee", "@drift:controlled.local"),
        ("room_id", "!drift:controlled.local"),
        ("context_projection_digest", "sha256:" + "d" * 64),
        ("source_lock_digest", "sha256:" + "c" * 64),
        ("attempt", 99),
        ("fencing_token", "sha256:" + "b" * 64),
    ],
)
def test_candidate_admission_rejects_each_binding_drift(
    native_pack, field: str, mutated: object
) -> None:
    output, receipt = native_pack
    binding = next(
        item
        for item in receipt["bindings"]
        if item["domain"] == "product" and item["attempt"] == 1
    )
    result = _task_result_bytes(output, receipt, binding["task_id"])
    observed = _observed_binding(binding)
    exact = admit_candidate(
        binding,
        result,
        observed_binding=observed,
        active_fencing_token=binding["fencing_token"],
        active_attempt_ref=binding["active_attempt_ref"],
    )
    assert exact["verdict"] == "ADMIT"
    observed[field] = mutated
    rejected = admit_candidate(
        binding,
        result,
        observed_binding=observed,
        active_fencing_token=binding["fencing_token"],
        active_attempt_ref=binding["active_attempt_ref"],
    )
    assert rejected["verdict"] == "REJECT"
    assert f"BINDING_{field.upper()}_MISMATCH" in rejected["reason_codes"]


def test_semantic_ok_false_cannot_pass_even_with_rehashed_receipt(
    native_pack, tmp_path: Path
) -> None:
    output, _receipt = native_pack
    changed = tmp_path / "semantic-ok"
    shutil.copytree(output, changed)
    receipt = json.loads((changed / "lifecycle-receipt.json").read_text(encoding="utf-8"))
    action = next(item for item in receipt["actions"] if item["key"] == "project:create")
    action["ok"] = False
    action_base = dict(action)
    action_base.pop("digest")
    action["digest"] = digest_json(action_base)
    _write_rehashed_receipt(changed, receipt)
    with pytest.raises(NativeTaskflowError, match="ACTION_SEMANTIC_SUMMARY"):
        verify_native_taskflow_evidence(
            evidence_dir=changed, checkout=CHECKOUT, lock_path=LOCK
        )


def test_raw_tool_action_tamper_is_rejected(native_pack, tmp_path: Path) -> None:
    output, receipt = native_pack
    changed = tmp_path / "raw-tool"
    shutil.copytree(output, changed)
    action = next(item for item in receipt["actions"] if item["action"] == "ack_task")
    raw_path = changed / action["raw_ref"]
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw["tool"] = "projectflow"
    raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(NativeTaskflowError, match="RAW_TOOL_ACTION"):
        verify_native_taskflow_evidence(
            evidence_dir=changed, checkout=CHECKOUT, lock_path=LOCK
        )


def test_rehashed_raw_semantic_state_tamper_is_rejected(
    native_pack, tmp_path: Path
) -> None:
    output, _receipt = native_pack
    changed = tmp_path / "raw-semantic"
    shutil.copytree(output, changed)
    receipt = json.loads((changed / "lifecycle-receipt.json").read_text(encoding="utf-8"))
    action = next(item for item in receipt["actions"] if item["key"] == "project:create")
    raw_path = changed / action["raw_ref"]
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    payload = json.loads(raw["response"]["content"][0]["text"])
    payload["project"]["status"] = "completed"
    raw["response"]["content"][0]["text"] = json.dumps(payload, ensure_ascii=False)
    raw_path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    action["response_digest"] = digest_json(raw["response"])
    action["payload_digest"] = digest_json(payload)
    action["status"] = "completed"
    action_base = dict(action)
    action_base.pop("digest")
    action["digest"] = digest_json(action_base)
    _write_rehashed_receipt(changed, receipt)
    with pytest.raises(NativeTaskflowError, match="CREATE_PROJECT_STATE"):
        verify_native_taskflow_evidence(
            evidence_dir=changed, checkout=CHECKOUT, lock_path=LOCK
        )


def test_journal_restart_reuses_exact_action_receipt(tmp_path: Path) -> None:
    path = tmp_path / "journal.json"
    request = {"action": "check_task", "taskId": "task-1"}
    response = {"content": [{"type": "text", "text": "{\"ok\":true}"}]}
    first = LifecycleJournal(path).record(
        key="task-1:check",
        tool="taskflow",
        action="check_task",
        arguments=request,
        response=response,
    )
    second = LifecycleJournal(path).record(
        key="task-1:check",
        tool="taskflow",
        action="check_task",
        arguments=request,
        response=response,
    )
    assert first == second
    assert len(json.loads(path.read_text(encoding="utf-8"))) == 1


def test_source_lock_rejects_commit_mismatch(tmp_path: Path) -> None:
    if not CHECKOUT.is_dir():
        pytest.skip("pinned AgentTeams checkout is unavailable")
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    lock["commit"] = "0" * 40
    changed = tmp_path / "lock.json"
    changed.write_text(json.dumps(lock), encoding="utf-8")
    with pytest.raises(NativeTaskflowError, match="TEAMHARNESS_COMMIT_MISMATCH"):
        verify_teamharness_checkout(CHECKOUT, changed)
