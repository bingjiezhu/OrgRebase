from __future__ import annotations

import copy
import shutil
import subprocess
import sys
from asyncio import CancelledError
from pathlib import Path

import pytest

from orgrebase.clock import FrozenClock
from orgrebase.commit_gateway import EffectError
from orgrebase.compensation import CompensationCoordinator
from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError, FreshnessError
from orgrebase.git_tool import GIT_EXECUTOR, GitArtifactTool, GitToolError
from orgrebase.service import OrgRebaseService
from orgrebase.store import StateStore


def make_tool(tmp_path: Path) -> tuple[GitArtifactTool, StateStore]:
    repository = GitArtifactTool.initialize_fixture(tmp_path / "downstream-fixture")
    store = StateStore(":memory:")
    return GitArtifactTool(repository, store), store


def approved_digest() -> str:
    return sha256_digest({"approval": "git-tool-test"})


def run_binding() -> dict[str, str]:
    return {"workflow_run_id": "run:git-tool:test@1", "run_nonce": "a" * 64}


def test_real_git_patch_and_git_revert_are_cross_verifiable(tmp_path: Path) -> None:
    tool, store = make_tool(tmp_path)
    baseline_head = tool.head()
    baseline_digest = tool.content_digest("downstream/launch.json")
    patch = tool.patch(
        actor_id=GIT_EXECUTOR,
        **run_binding(),
        relative_path="downstream/launch.json",
        content='{"launch_date":"2026-09-15"}\n',
        expected_head=baseline_head,
        expected_content_digest=baseline_digest,
        approval_digest=approved_digest(),
        idempotency_key="git-patch-test-01",
    )
    patch_commit = patch["result"]["commit"]
    assert patch["receipt"]["evidence_class"] == "LOCAL_REAL_TOOL"
    assert patch["receipt"]["external_operation_id"] == patch_commit
    assert tool.head() == patch_commit
    assert tool.content_digest("downstream/launch.json") == patch["receipt"]["after_state_digest"]

    message = subprocess.run(
        ["git", "show", "-s", "--format=%B", patch_commit],
        cwd=tool.repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert f"OrgRebase-Approval: {approved_digest()}" in message
    assert patch["receipt"]["request_digest"] in message

    compensation = tool.compensate(
        actor_id=GIT_EXECUTOR,
        **run_binding(),
        patch_invocation=patch,
        approval_digest=approved_digest(),
        idempotency_key="git-revert-test-01",
    )
    assert compensation["result"]["compensates_commit"] == patch_commit
    assert compensation["receipt"]["compensation_ref"] == patch["receipt"]["digest"]
    assert compensation["result"]["commit"] != patch_commit
    assert tool.content_digest("downstream/launch.json") == baseline_digest
    assert store.verify_event_chain()["status"] == "PASS"
    store.close()


def test_git_tool_is_idempotent_but_rejects_key_reuse(tmp_path: Path) -> None:
    tool, store = make_tool(tmp_path)
    kwargs = {
        "actor_id": GIT_EXECUTOR,
        **run_binding(),
        "relative_path": "downstream/launch.json",
        "content": '{"launch_date":"2026-09-15"}\n',
        "expected_head": tool.head(),
        "expected_content_digest": tool.content_digest("downstream/launch.json"),
        "approval_digest": approved_digest(),
        "idempotency_key": "git-patch-test-02",
    }
    first = tool.patch(**kwargs)
    assert tool.patch(**kwargs) == first
    changed = dict(kwargs)
    changed["content"] = '{"launch_date":"2026-09-20"}\n'
    with pytest.raises(RuntimeError, match="IDEMPOTENCY_CONFLICT"):
        tool.patch(**changed)
    store.close()


def test_git_tool_denies_agents_and_paths_outside_allowlist_without_commit(tmp_path: Path) -> None:
    tool, store = make_tool(tmp_path)
    before = tool.head()
    common = {
        **run_binding(),
        "relative_path": "downstream/launch.json",
        "content": '{"launch_date":"2026-09-15"}\n',
        "expected_head": before,
        "expected_content_digest": tool.content_digest("downstream/launch.json"),
        "approval_digest": approved_digest(),
        "idempotency_key": "git-denied-test-01",
    }
    with pytest.raises(AuthorizationError, match="control-plane"):
        tool.patch(actor_id="gtm-steward", **common)
    with pytest.raises(AuthorizationError, match="PATH_OUTSIDE_ALLOWLIST"):
        tool.patch(
            actor_id=GIT_EXECUTOR,
            **{**common, "relative_path": "../README.md", "idempotency_key": "git-denied-test-02"},
        )
    assert tool.head() == before
    store.close()


def test_repository_identity_is_relocation_safe_and_multiple_worktrees_fail_closed(
    tmp_path: Path,
) -> None:
    tool, store = make_tool(tmp_path)
    relocated = tmp_path / "relocated-clone"
    shutil.copytree(tool.repository, relocated)
    relocated_tool = GitArtifactTool(relocated, store)
    assert relocated_tool.repository_id == tool.repository_id

    secondary = tmp_path / "secondary-worktree"
    subprocess.run(
        ["git", "worktree", "add", "-b", "security-test-worktree", str(secondary)],
        cwd=tool.repository,
        check=True,
        capture_output=True,
        text=True,
    )
    with pytest.raises(GitToolError, match="MULTIPLE_WORKTREES_UNSUPPORTED"):
        GitArtifactTool(tool.repository, store)
    store.close()


def test_git_tool_fails_closed_on_dirty_worktree_and_head_drift(tmp_path: Path) -> None:
    tool, store = make_tool(tmp_path)
    baseline_head = tool.head()
    baseline_digest = tool.content_digest("downstream/launch.json")
    target = tool.repository / "downstream" / "launch.json"
    target.write_text('{"launch_date":"manual-drift"}\n', encoding="utf-8")
    with pytest.raises(FreshnessError, match="REPOSITORY_NOT_CLEAN"):
        tool.patch(
            actor_id=GIT_EXECUTOR,
            **run_binding(),
            relative_path="downstream/launch.json",
            content='{"launch_date":"2026-09-15"}\n',
            expected_head=baseline_head,
            expected_content_digest=baseline_digest,
            approval_digest=approved_digest(),
            idempotency_key="git-dirty-test-01",
        )
    subprocess.run(
        ["git", "restore", "--", "downstream/launch.json"], cwd=tool.repository, check=True
    )
    with pytest.raises(FreshnessError, match="HEAD_DRIFT"):
        tool.patch(
            actor_id=GIT_EXECUTOR,
            **run_binding(),
            relative_path="downstream/launch.json",
            content='{"launch_date":"2026-09-15"}\n',
            expected_head="0" * 40,
            expected_content_digest=baseline_digest,
            approval_digest=approved_digest(),
            idempotency_key="git-head-drift-test-01",
        )
    store.close()


def test_git_compensation_rejects_tampered_patch_evidence(tmp_path: Path) -> None:
    tool, store = make_tool(tmp_path)
    patch = tool.patch(
        actor_id=GIT_EXECUTOR,
        **run_binding(),
        relative_path="downstream/launch.json",
        content='{"launch_date":"2026-09-15"}\n',
        expected_head=tool.head(),
        expected_content_digest=tool.content_digest("downstream/launch.json"),
        approval_digest=approved_digest(),
        idempotency_key="git-tamper-test-01",
    )
    tampered = copy.deepcopy(patch)
    tampered["result"]["relative_path"] = "downstream/other.json"
    with pytest.raises(GitToolError, match="DIGEST_MISMATCH"):
        tool.compensate(
            actor_id=GIT_EXECUTOR,
            **run_binding(),
            patch_invocation=tampered,
            approval_digest=approved_digest(),
            idempotency_key="git-tamper-revert-test-01",
        )
    store.close()


def test_service_git_demo_binds_apply_and_rollback_approvals(tmp_path: Path) -> None:
    service = OrgRebaseService()
    evidence = service.run_git_tool_demo(tmp_path / "service-git-fixture")
    assert evidence["verification"]["status"] == "PASS"
    assert evidence["evidence_boundary"] == "LOCAL_REAL_TOOL"
    assert evidence["patch"]["receipt"]["request_digest"]
    assert evidence["compensation"]["receipt"]["compensation_ref"] == evidence["patch"][
        "receipt"
    ]["digest"]
    assert evidence["compensation_saga"]["status"] == "ROLLED_BACK_PENDING_REBASE"
    assert evidence["compensation_saga"]["residual_effects"] == []
    assert service.rollback_approval.actor_id != service.approval.actor_id
    service.store.close()


def test_cross_system_compensation_exposes_partial_state_and_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = OrgRebaseService()
    service.apply()
    rollback = service.rollback()
    root = GitArtifactTool.initialize_fixture(tmp_path / "saga-git-fixture")
    tool = GitArtifactTool(root, service.store)
    patch = tool.patch(
        actor_id=GIT_EXECUTOR,
        workflow_run_id=service.run_envelope.run_id,
        run_nonce=service.run_envelope.nonce,
        relative_path="downstream/launch.json",
        content='{"launch_date":"2026-09-15"}\n',
        expected_head=tool.head(),
        expected_content_digest=tool.content_digest("downstream/launch.json"),
        approval_digest=service.approval.digest,
        idempotency_key="git-saga-patch-01",
    )
    patched_head = tool.head()
    coordinator = CompensationCoordinator(service.store)
    original = tool.compensate

    def fail_once(**_: object) -> dict[str, object]:
        raise RuntimeError("INJECTED_EXTERNAL_COMPENSATION_FAILURE")

    monkeypatch.setattr(tool, "compensate", fail_once)
    partial = coordinator.execute(
        rollback_receipt=rollback["receipt"],
        rollback_approval_digest=rollback["approval"].digest,
        git_tool=tool,
        patch_invocation=patch,
        actor_id=GIT_EXECUTOR,
        idempotency_key="cross-system-saga-01",
    )
    assert partial["compensation"] is None
    assert partial["saga"]["status"] == "ROLLBACK_PARTIAL"
    assert partial["saga"]["next_action"] == "RETRY_EXACT_GIT_COMPENSATION"
    assert partial["saga"]["residual_effects"][0]["commit"] == patched_head
    assert tool.head() == patched_head

    monkeypatch.setattr(tool, "compensate", original)
    completed = coordinator.execute(
        rollback_receipt=rollback["receipt"],
        rollback_approval_digest=rollback["approval"].digest,
        git_tool=tool,
        patch_invocation=patch,
        actor_id=GIT_EXECUTOR,
        idempotency_key="cross-system-saga-01",
    )
    assert completed["saga"]["status"] == "ROLLED_BACK_PENDING_REBASE"
    assert completed["saga"]["attempt"] == 2
    assert completed["saga"]["residual_effects"] == []
    assert completed["compensation"]["result"]["compensates_commit"] == patched_head
    service.store.close()


def test_patch_retry_recovers_crash_after_git_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool, store = make_tool(tmp_path)
    kwargs = {
        "actor_id": GIT_EXECUTOR,
        **run_binding(),
        "relative_path": "downstream/launch.json",
        "content": '{"launch_date":"2026-09-15"}\n',
        "expected_head": tool.head(),
        "expected_content_digest": tool.content_digest("downstream/launch.json"),
        "approval_digest": approved_digest(),
        "idempotency_key": "git-crash-recovery-01",
    }
    original = tool._record

    def crash_before_journal(*args: object, **kwargs: object) -> None:
        raise RuntimeError("INJECTED_CRASH_AFTER_COMMIT")

    monkeypatch.setattr(tool, "_record", crash_before_journal)
    with pytest.raises(RuntimeError, match="INJECTED_CRASH"):
        tool.patch(**kwargs)
    committed_head = tool.head()
    monkeypatch.setattr(tool, "_record", original)
    recovered = tool.patch(**kwargs)
    assert recovered["result"]["commit"] == committed_head
    assert store.get_effect(
        f"git-tool:patch:{tool.repository_id}:{GIT_EXECUTOR}:git-crash-recovery-01"
    )["state"] == "CONFIRMED"
    store.close()


def test_compensation_retry_recovers_crash_after_git_revert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool, store = make_tool(tmp_path)
    patch = tool.patch(
        actor_id=GIT_EXECUTOR,
        **run_binding(),
        relative_path="downstream/launch.json",
        content='{"launch_date":"2026-09-15"}\n',
        expected_head=tool.head(),
        expected_content_digest=tool.content_digest("downstream/launch.json"),
        approval_digest=approved_digest(),
        idempotency_key="git-crash-patch-02",
    )
    kwargs = {
        "actor_id": GIT_EXECUTOR,
        **run_binding(),
        "patch_invocation": patch,
        "approval_digest": approved_digest(),
        "idempotency_key": "git-crash-revert-02",
    }
    original = tool._record

    def crash_before_journal(*args: object, **kwargs: object) -> None:
        raise RuntimeError("INJECTED_CRASH_AFTER_REVERT")

    monkeypatch.setattr(tool, "_record", crash_before_journal)
    with pytest.raises(RuntimeError, match="INJECTED_CRASH"):
        tool.compensate(**kwargs)
    reverted_head = tool.head()
    monkeypatch.setattr(tool, "_record", original)
    recovered = tool.compensate(**kwargs)
    assert recovered["result"]["commit"] == reverted_head
    assert tool.verify(patch, recovered)["status"] == "PASS"
    store.close()


def test_git_compensation_cannot_cross_run_nonce(tmp_path: Path) -> None:
    tool, store = make_tool(tmp_path)
    patch = tool.patch(
        actor_id=GIT_EXECUTOR,
        **run_binding(),
        relative_path="downstream/launch.json",
        content='{"launch_date":"2026-09-15"}\n',
        expected_head=tool.head(),
        expected_content_digest=tool.content_digest("downstream/launch.json"),
        approval_digest=approved_digest(),
        idempotency_key="git-cross-run-patch-01",
    )
    with pytest.raises(FreshnessError, match="CROSS_RUN"):
        tool.compensate(
            actor_id=GIT_EXECUTOR,
            workflow_run_id=run_binding()["workflow_run_id"],
            run_nonce="c" * 64,
            patch_invocation=patch,
            approval_digest=approved_digest(),
            idempotency_key="git-cross-run-revert-01",
        )
    store.close()


def test_dispatched_patch_proves_absence_before_dirty_worktree_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool, store = make_tool(tmp_path)
    kwargs = {
        "actor_id": GIT_EXECUTOR,
        **run_binding(),
        "relative_path": "downstream/launch.json",
        "content": '{"launch_date":"2026-09-15"}\n',
        "expected_head": tool.head(),
        "expected_content_digest": tool.content_digest("downstream/launch.json"),
        "approval_digest": approved_digest(),
        "idempotency_key": "git-dirty-prepared-01",
    }
    original_replace = tool._replace_target

    def crash_after_replace(target: Path, payload: bytes) -> None:
        original_replace(target, payload)
        raise RuntimeError("INJECTED_CRASH_AFTER_REPLACE")

    monkeypatch.setattr(tool, "_replace_target", crash_after_replace)
    with pytest.raises(RuntimeError, match="INJECTED_CRASH_AFTER_REPLACE"):
        tool.patch(**kwargs)
    assert tool._is_dirty()
    monkeypatch.setattr(tool, "_replace_target", original_replace)
    with pytest.raises(EffectError, match="EFFECT_ABSENT_FINAL_RETRY_AUTHORIZED"):
        tool.patch(**kwargs)
    assert tool._is_dirty()  # Reconciliation is read-only.
    recovered = tool.patch(**kwargs)
    assert recovered["receipt"]["status"] == "SUCCEEDED"
    assert not tool._is_dirty()
    store.close()


def test_prepared_dirty_and_moved_head_requires_manual_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool, store = make_tool(tmp_path)
    kwargs = {
        "actor_id": GIT_EXECUTOR,
        **run_binding(),
        "relative_path": "downstream/launch.json",
        "content": '{"launch_date":"2026-09-15"}\n',
        "expected_head": tool.head(),
        "expected_content_digest": tool.content_digest("downstream/launch.json"),
        "approval_digest": approved_digest(),
        "idempotency_key": "git-dirty-moved-head-01",
    }
    original_replace = tool._replace_target

    def crash_after_replace(target: Path, payload: bytes) -> None:
        original_replace(target, payload)
        raise RuntimeError("INJECTED_CRASH_AFTER_REPLACE")

    monkeypatch.setattr(tool, "_replace_target", crash_after_replace)
    with pytest.raises(RuntimeError, match="INJECTED_CRASH_AFTER_REPLACE"):
        tool.patch(**kwargs)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=OrgRebase Control Plane",
            "-c",
            "user.email=control-plane@orgrebase.invalid",
            "commit",
            "--allow-empty",
            "-m",
            "unrelated head move",
        ],
        cwd=tool.repository,
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(tool, "_replace_target", original_replace)
    with pytest.raises(GitToolError, match="MANUAL_RECOVERY_REQUIRED:PATCH"):
        tool.patch(**kwargs)
    store.close()


def test_effect_anchor_recovers_commit_even_after_head_was_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool, store = make_tool(tmp_path)
    parent = tool.head()
    kwargs = {
        "actor_id": GIT_EXECUTOR,
        **run_binding(),
        "relative_path": "downstream/launch.json",
        "content": '{"launch_date":"2026-09-15"}\n',
        "expected_head": parent,
        "expected_content_digest": tool.content_digest("downstream/launch.json"),
        "approval_digest": approved_digest(),
        "idempotency_key": "git-external-committed-drift-01",
    }
    original_record = tool._record

    def crash_record(**kwargs: object) -> dict[str, object]:
        raise RuntimeError("INJECTED_CRASH_AFTER_EXTERNAL_COMMITTED")

    monkeypatch.setattr(tool, "_record", crash_record)
    with pytest.raises(RuntimeError, match="INJECTED_CRASH_AFTER_EXTERNAL_COMMITTED"):
        tool.patch(**kwargs)
    subprocess.run(["git", "reset", "--hard", parent], cwd=tool.repository, check=True)
    monkeypatch.setattr(tool, "_record", original_record)
    recovered = tool.patch(**kwargs)
    assert recovered["result"]["commit"] != parent
    assert tool.head() == parent  # Confirmation does not reapply a reverted effect.
    store.close()


@pytest.mark.parametrize("operation", ["patch", "compensate"])
@pytest.mark.parametrize("later_change", ["delete", "move"])
def test_committed_effect_recovers_after_target_path_is_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, later_change: str,
) -> None:
    tool, store = make_tool(tmp_path)
    try:
        patch_arguments = {
            "actor_id": GIT_EXECUTOR,
            **run_binding(),
            "relative_path": "downstream/launch.json",
            "content": '{"launch_date":"2026-09-15"}\n',
            "expected_head": tool.head(),
            "expected_content_digest": tool.content_digest("downstream/launch.json"),
            "approval_digest": approved_digest(),
            "idempotency_key": "removed-path-patch",
        }
        if operation == "patch":
            def invoke() -> dict:
                return tool.patch(**patch_arguments)
        else:
            patch = tool.patch(**patch_arguments)

            def invoke() -> dict:
                return tool.compensate(
                    actor_id=GIT_EXECUTOR, **run_binding(), patch_invocation=patch,
                    approval_digest=approved_digest(), idempotency_key="removed-path-compensation",
                )
        original_record = tool._record

        def crash_record(**kwargs: object) -> dict:
            raise RuntimeError("INJECTED_RECORD_FAILURE")

        monkeypatch.setattr(tool, "_record", crash_record)
        with pytest.raises(RuntimeError, match="INJECTED_RECORD_FAILURE"):
            invoke()
        committed_effect = tool.head()
        if later_change == "delete":
            tool._git("rm", "--", "downstream/launch.json")
        else:
            tool._git("mv", "--", "downstream/launch.json", "downstream/moved.json")
        tool._git(
            "-c", "user.name=Recovery Test", "-c", "user.email=recovery@example.invalid",
            "commit", "-m", "Independently remove original target path",
        )
        later_head = tool.head()
        monkeypatch.setattr(tool, "_record", original_record)
        recovered = invoke()
        assert recovered["result"]["commit"] == committed_effect
        assert recovered["receipt"]["status"] == "SUCCEEDED"
        assert invoke() == recovered
        assert tool.head() == later_head
        assert not (tool.repository / "downstream/launch.json").exists()
        assert not tool._is_dirty()
    finally:
        store.close()


def test_patch_and_compensation_idempotency_keys_have_distinct_receipt_identity(tmp_path: Path) -> None:
    tool, store = make_tool(tmp_path)
    try:
        patch_arguments = {
            "actor_id": GIT_EXECUTOR, **run_binding(),
            "relative_path": "downstream/launch.json", "content": "changed\n",
            "expected_head": tool.head(),
            "expected_content_digest": tool.content_digest("downstream/launch.json"),
            "approval_digest": approved_digest(), "idempotency_key": "shared-operation-key",
        }
        patch = tool.patch(**patch_arguments)
        compensation_arguments = {
            "actor_id": GIT_EXECUTOR, **run_binding(), "patch_invocation": patch,
            "approval_digest": approved_digest(), "idempotency_key": "shared-operation-key",
        }
        compensated = tool.compensate(**compensation_arguments)
        assert compensated["receipt"]["id"] != patch["receipt"]["id"]
        assert tool.verify(patch, compensated)["status"] == "PASS"
        final_head = tool.head()
        assert tool.patch(**patch_arguments) == patch
        assert tool.compensate(**compensation_arguments) == compensated
        assert tool.head() == final_head
        assert store.load_artifact(patch["receipt"]["id"]).payload == patch
        assert store.load_artifact(compensated["receipt"]["id"]).payload == compensated
    finally:
        store.close()


def test_process_exit_after_real_commit_recovers_without_a_second_commit(tmp_path: Path) -> None:
    repository = GitArtifactTool.initialize_fixture(tmp_path / "repository")
    database = tmp_path / "state.db"
    script = """
import os
import sys
from pathlib import Path
from orgrebase.clock import FrozenClock
from orgrebase.digest import sha256_digest
from orgrebase.git_tool import GIT_EXECUTOR, GitArtifactTool
from orgrebase.store import StateStore
store = StateStore(sys.argv[2])
tool = GitArtifactTool(Path(sys.argv[1]), store, clock=FrozenClock('2026-09-09T00:00:00Z'))
tool._record = lambda **kwargs: os._exit(77)
tool.patch(
    actor_id=GIT_EXECUTOR, workflow_run_id='run:process-crash', run_nonce='a' * 64,
    relative_path='downstream/launch.json', content='changed\\n',
    expected_head=tool.head(), expected_content_digest=tool.content_digest('downstream/launch.json'),
    approval_digest=sha256_digest({'approval': 'process crash'}), idempotency_key='process-crash',
)
"""
    with StateStore(database) as store:
        tool = GitArtifactTool(repository, store)
        head = tool.head()
        before_digest = tool.content_digest("downstream/launch.json")
    completed = subprocess.run(
        [sys.executable, "-c", script, str(repository), str(database)],
        capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 77, completed.stderr
    with StateStore(database) as store:
        tool = GitArtifactTool(repository, store, clock=FrozenClock("2026-09-09T00:02:00Z"))
        committed = tool.head()
        effect_id = f"git-tool:patch:{tool.repository_id}:{GIT_EXECUTOR}:process-crash"
        assert store.get_effect(effect_id)["state"] == "DISPATCHING"
        result = tool.patch(
            actor_id=GIT_EXECUTOR, workflow_run_id="run:process-crash", run_nonce="a" * 64,
            relative_path="downstream/launch.json", content="changed\n",
            expected_head=head, expected_content_digest=before_digest,
            approval_digest=sha256_digest({"approval": "process crash"}),
            idempotency_key="process-crash",
        )
        assert result["result"]["commit"] == committed
        assert tool.head() == committed
        assert store.get_effect(effect_id)["state"] == "CONFIRMED"


def test_compensation_saga_preserves_operational_error_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = OrgRebaseService()
    service.apply()
    rollback = service.rollback()
    root = GitArtifactTool.initialize_fixture(tmp_path / "saga-error-code-fixture")
    tool = GitArtifactTool(root, service.store)
    patch = tool.patch(
        actor_id=GIT_EXECUTOR,
        workflow_run_id=service.run_envelope.run_id,
        run_nonce=service.run_envelope.nonce,
        relative_path="downstream/launch.json",
        content='{"launch_date":"2026-09-15"}\n',
        expected_head=tool.head(),
        expected_content_digest=tool.content_digest("downstream/launch.json"),
        approval_digest=service.approval.digest,
        idempotency_key="git-saga-error-patch-01",
    )

    def fail_drift(**_: object) -> dict[str, object]:
        raise FreshnessError("HEAD_DRIFT")

    monkeypatch.setattr(tool, "compensate", fail_drift)
    partial = CompensationCoordinator(service.store).execute(
        rollback_receipt=rollback["receipt"],
        rollback_approval_digest=rollback["approval"].digest,
        git_tool=tool,
        patch_invocation=patch,
        actor_id=GIT_EXECUTOR,
        idempotency_key="cross-system-error-code-01",
    )
    assert partial["saga"]["external_compensation"]["error_code"] == "HEAD_DRIFT"
    assert partial["saga"]["residual_effects"][0]["commit"] == tool.head()
    service.store.close()


@pytest.mark.parametrize("failure", [OSError("repository unavailable"), KeyboardInterrupt(), CancelledError()])
def test_compensation_failure_persists_obligation_without_reading_head(tmp_path, monkeypatch, failure):
    service = OrgRebaseService()
    try:
        service.apply()
        rollback = service.rollback()
        tool = GitArtifactTool(GitArtifactTool.initialize_fixture(tmp_path / "recovery"), service.store)
        patch = tool.patch(
            actor_id=GIT_EXECUTOR, workflow_run_id=service.run_envelope.run_id,
            run_nonce=service.run_envelope.nonce, relative_path="downstream/launch.json",
            content='{"launch_date":"2026-09-15"}\n', expected_head=tool.head(),
            expected_content_digest=tool.content_digest("downstream/launch.json"),
            approval_digest=service.approval.digest, idempotency_key="residual-patch",
        )
        def failed(**kwargs):
            raise failure
        def unavailable_head():
            raise AssertionError("error handling must not depend on a second external call")
        monkeypatch.setattr(tool, "compensate", failed)
        monkeypatch.setattr(tool, "head", unavailable_head)
        coordinator = CompensationCoordinator(service.store)
        args = dict(rollback_receipt=rollback["receipt"], rollback_approval_digest=rollback["approval"].digest,
                    git_tool=tool, patch_invocation=patch, actor_id=GIT_EXECUTOR, idempotency_key="residual-saga")
        if not isinstance(failure, Exception):
            with pytest.raises(type(failure)):
                coordinator.execute(**args)
        else:
            assert coordinator.execute(**args)["saga"]["status"] == "ROLLBACK_PARTIAL"
        receipt = service.store.load_artifact("compensation-saga:residual-saga@attempt-1").payload
        assert receipt["residual_effects"][0]["commit"] == patch["receipt"]["external_operation_id"]
        assert receipt["residual_effects"][0]["status"] == "RECONCILIATION_REQUIRED"
        assert receipt["external_compensation"]["error_code"] == type(failure).__name__
        if not isinstance(failure, Exception):
            def unavailable_store(*args):
                raise OSError("RECOVERY_STORE_UNAVAILABLE")
            monkeypatch.setattr(coordinator, "_persist", unavailable_store)
            with pytest.raises(type(failure)) as interrupted:
                coordinator.execute(**{**args, "idempotency_key": "unpersisted-saga"})
            assert interrupted.value is failure
            assert isinstance(interrupted.value.__cause__, OSError)
            with pytest.raises(KeyError):
                service.store.load_artifact("compensation-saga:unpersisted-saga@attempt-1")
    finally:
        service.store.close()
