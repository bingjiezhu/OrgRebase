"""A minimal real, reversible Git tool executed only by the control plane."""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from fcntl import LOCK_EX, LOCK_UN, flock
from pathlib import Path, PurePosixPath
from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.domain import (
    AuthorizationError,
    EvidenceClass,
    FreshnessError,
    ToolContract,
    ToolInvocationReceipt,
)
from orgrebase.store import StateStore

GIT_EXECUTOR = "control-plane:git-executor"
ALLOWED_PREFIX = PurePosixPath("downstream")
_GIT_AUTHOR = (
    "-c",
    "user.name=OrgRebase Control Plane",
    "-c",
    "user.email=control-plane@orgrebase.invalid",
)
_GIT_HOOKLESS = (
    "-c",
    "commit.gpgSign=false",
    "-c",
    "core.hooksPath=/dev/null",
)

GIT_ARTIFACT_CONTRACT = ToolContract(
    id="tool:git-downstream-artifact",
    name="orgrebase.git_downstream_artifact",
    version="1.0.0",
    purpose=(
        "Apply an approved downstream candidate as a real Git commit and compensate it with "
        "git revert. Agents may propose content but cannot execute this tool."
    ),
    endpoint="local-process://orgrebase/git-downstream-artifact",
    method="PATCH|COMPENSATE",
    auth={
        "scheme": "control-plane workload identity + approval digest",
        "agent_credentials": False,
    },
    input_schema={
        "type": "object",
        "required": [
            "relative_path",
            "expected_head",
            "expected_content_digest",
            "approval_digest",
            "idempotency_key",
        ],
        "properties": {
            "relative_path": {"type": "string", "pattern": "^downstream/"},
            "expected_head": {"type": "string", "minLength": 40, "maxLength": 64},
            "expected_content_digest": {"type": "string", "pattern": "^sha256:"},
            "content": {"type": "string", "maxLength": 65536},
            "approval_digest": {"type": "string", "pattern": "^sha256:"},
            "idempotency_key": {"type": "string", "minLength": 8},
            "patch_receipt": {"type": "object"},
        },
        "additionalProperties": False,
    },
    output_schema={
        "type": "object",
        "required": [
            "repository_id",
            "relative_path",
            "parent_commit",
            "commit",
            "before_content_digest",
            "after_content_digest",
            "receipt",
        ],
    },
    error_codes=(
        "AUTHZ_DENIED",
        "APPROVAL_REQUIRED",
        "REPOSITORY_NOT_CLEAN",
        "HEAD_DRIFT",
        "CONTENT_DRIFT",
        "PATH_OUTSIDE_ALLOWLIST",
        "IDEMPOTENCY_CONFLICT",
        "GIT_OPERATION_FAILED",
    ),
    permissions={
        "effect": "write_reversible_external_artifact",
        "allowed_actors": [GIT_EXECUTOR],
        "candidate_submitters": ["gtm-steward", "support-steward"],
        "allowed_path_prefix": "downstream/",
        "canonical_state_write": False,
        "network": False,
    },
    retry_policy={
        "safe": "only with the same idempotency key and request digest",
        "max_attempts": 2,
        "retryable": ["PROCESS_INTERRUPTED_BEFORE_COMMIT"],
        "non_retryable": ["HEAD_DRIFT", "CONTENT_DRIFT", "REPOSITORY_NOT_CLEAN"],
    },
    idempotency={
        "required": True,
        "scope": "repository+actor+operation+request",
        "conflict": "fail-closed",
    },
    audit_fields=(
        "actor_id",
        "approval_digest",
        "request_digest",
        "repository_id",
        "parent_commit",
        "commit",
        "before_content_digest",
        "after_content_digest",
    ),
    degradation={
        "mode": "NO_WRITE",
        "rule": "Any auth, approval, path, worktree, head, or content drift aborts before commit.",
    },
    mcp_migration={
        "transport_change": "Wrap the same executor behind an MCP tool after remote sandboxing.",
        "business_logic_change": False,
        "estimated_adapter_surface": "one transport adapter; Git invariants remain local",
    },
)


class GitToolError(RuntimeError):
    """Stable tool error without leaking command output or file contents."""


class GitArtifactTool:
    def __init__(self, repository: Path, store: StateStore) -> None:
        self.repository = repository.resolve()
        self.store = store
        self._assert_repository()

    @staticmethod
    def initialize_fixture(repository: Path) -> Path:
        """Create a tiny dogfood repository once; never replace an existing directory."""

        root = repository.resolve()
        git_dir = root / ".git"
        target = root / "downstream" / "launch.json"
        if git_dir.is_dir():
            return root
        if root.exists() and any(root.iterdir()):
            raise GitToolError("REFUSE_NONEMPTY_NON_GIT_DIRECTORY")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('{"launch_date":"2026-09-01"}\n', encoding="utf-8")
        GitArtifactTool._run_raw(root, "init", "--initial-branch=main")
        GitArtifactTool._run_raw(root, "add", "--", "downstream/launch.json")
        GitArtifactTool._run_raw(
            root,
            *_GIT_AUTHOR,
            "commit",
            "-m",
            "fixture: seed downstream launch artifact",
        )
        return root

    def head(self) -> str:
        return self._git("rev-parse", "HEAD").strip()

    def content_digest(self, relative_path: str) -> str:
        return self._bytes_digest(self._target(relative_path).read_bytes())

    def patch(
        self,
        *,
        actor_id: str,
        workflow_run_id: str,
        run_nonce: str,
        relative_path: str,
        content: str,
        expected_head: str,
        expected_content_digest: str,
        approval_digest: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        with self._exclusive_lock():
            return self._patch_locked(
                actor_id=actor_id,
                workflow_run_id=workflow_run_id,
                run_nonce=run_nonce,
                relative_path=relative_path,
                content=content,
                expected_head=expected_head,
                expected_content_digest=expected_content_digest,
                approval_digest=approval_digest,
                idempotency_key=idempotency_key,
            )

    def _patch_locked(
        self,
        *,
        actor_id: str,
        workflow_run_id: str,
        run_nonce: str,
        relative_path: str,
        content: str,
        expected_head: str,
        expected_content_digest: str,
        approval_digest: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._authorize(actor_id, approval_digest)
        target = self._target(relative_path)
        request = {
            "operation": "PATCH",
            "repository_id": self.repository_id,
            "actor_id": actor_id,
            "workflow_run_id": workflow_run_id,
            "run_nonce": run_nonce,
            "relative_path": relative_path,
            "content_digest": self._bytes_digest(content.encode("utf-8")),
            "expected_head": expected_head,
            "expected_content_digest": expected_content_digest,
            "approval_digest": approval_digest,
        }
        request_digest = sha256_digest(request)
        storage_key = f"git-tool:patch:{self.repository_id}:{actor_id}:{idempotency_key}"
        after_digest = self._bytes_digest(content.encode("utf-8"))

        def record(result: dict[str, Any]) -> dict[str, Any]:
            return self._record(
                storage_key=storage_key,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                actor_id=actor_id,
                workflow_run_id=workflow_run_id,
                run_nonce=run_nonce,
                result=result,
                before_digest=str(result["before_content_digest"]),
                after_digest=str(result["after_content_digest"]),
                commit=str(result["commit"]),
                compensation_ref=None,
            )

        resumed = self._resume_external_operation(
            storage_key=storage_key,
            request_digest=request_digest,
            recover=lambda: self._recover_patch(
                request_digest=request_digest,
                relative_path=relative_path,
                expected_head=expected_head,
                before_digest=expected_content_digest,
                after_digest=after_digest,
            ),
            record=record,
        )
        if resumed is not None:
            return resumed

        self._assert_clean()
        parent_commit = self.head()
        if parent_commit != expected_head:
            raise FreshnessError("HEAD_DRIFT")
        before_digest = self._bytes_digest(target.read_bytes())
        if before_digest != expected_content_digest:
            raise FreshnessError("CONTENT_DRIFT")
        if after_digest == before_digest:
            raise GitToolError("NO_CHANGE")

        with self.store.transaction() as connection:
            self.store.prepare_external_operation(
                connection,
                operation_key=storage_key,
                request_digest=request_digest,
                operation="PATCH",
                repository_id=self.repository_id,
                expected_external_parent=parent_commit,
            )

        self._replace_target(target, content.encode("utf-8"))
        try:
            self._git("add", "--", relative_path)
            if self.head() != parent_commit:
                raise FreshnessError("HEAD_DRIFT_DURING_PREPARE")
            self._git(
                *_GIT_AUTHOR,
                *_GIT_HOOKLESS,
                "commit",
                "--no-verify",
                "-m",
                "orgrebase: apply approved downstream candidate",
                "-m",
                f"OrgRebase-Approval: {approval_digest}",
                "-m",
                f"OrgRebase-Request: {request_digest}",
            )
        except Exception:
            self._git("restore", "--staged", "--worktree", "--", relative_path)
            raise

        commit = self.head()
        if self._commit_parent(commit) != parent_commit:
            raise GitToolError("CONCURRENT_EXTERNAL_WRITER")
        if self._committed_content_digest(commit, relative_path) != after_digest:
            raise GitToolError("COMMITTED_BLOB_POSTCONDITION_FAILED")
        result = {
            "operation": "PATCH",
            "repository_id": self.repository_id,
            "relative_path": relative_path,
            "parent_commit": parent_commit,
            "commit": commit,
            "before_content_digest": before_digest,
            "after_content_digest": after_digest,
        }
        self._mark_external_committed(storage_key, result)
        return record(result)

    def compensate(
        self,
        *,
        actor_id: str,
        workflow_run_id: str,
        run_nonce: str,
        patch_invocation: dict[str, Any],
        approval_digest: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        with self._exclusive_lock():
            return self._compensate_locked(
                actor_id=actor_id,
                workflow_run_id=workflow_run_id,
                run_nonce=run_nonce,
                patch_invocation=patch_invocation,
                approval_digest=approval_digest,
                idempotency_key=idempotency_key,
            )

    def _compensate_locked(
        self,
        *,
        actor_id: str,
        workflow_run_id: str,
        run_nonce: str,
        patch_invocation: dict[str, Any],
        approval_digest: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._authorize(actor_id, approval_digest)
        patch_receipt = ToolInvocationReceipt.model_validate(patch_invocation["receipt"])
        patch_result = dict(patch_invocation["result"])
        if patch_receipt.evidence_class != EvidenceClass.LOCAL_REAL_TOOL:
            raise GitToolError("PATCH_RECEIPT_NOT_REAL_TOOL_EVIDENCE")
        if (
            patch_receipt.workflow_run_id != workflow_run_id
            or patch_receipt.run_nonce != run_nonce
        ):
            raise FreshnessError("CROSS_RUN_COMPENSATION")
        if sha256_digest(patch_result) != patch_receipt.result_digest:
            raise GitToolError("PATCH_RESULT_DIGEST_MISMATCH")
        if patch_result.get("repository_id") != self.repository_id:
            raise GitToolError("REPOSITORY_ID_MISMATCH")
        commit_to_revert = str(patch_result["commit"])
        relative_path = str(patch_result["relative_path"])
        target = self._target(relative_path)
        request = {
            "operation": "COMPENSATE",
            "repository_id": self.repository_id,
            "actor_id": actor_id,
            "workflow_run_id": workflow_run_id,
            "run_nonce": run_nonce,
            "patch_receipt_digest": patch_receipt.digest,
            "approval_digest": approval_digest,
        }
        request_digest = sha256_digest(request)
        storage_key = f"git-tool:compensate:{self.repository_id}:{actor_id}:{idempotency_key}"

        def record(result: dict[str, Any]) -> dict[str, Any]:
            return self._record(
                storage_key=storage_key,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                actor_id=actor_id,
                workflow_run_id=workflow_run_id,
                run_nonce=run_nonce,
                result=result,
                before_digest=str(result["before_content_digest"]),
                after_digest=str(result["after_content_digest"]),
                commit=str(result["commit"]),
                compensation_ref=patch_receipt.digest,
            )

        resumed = self._resume_external_operation(
            storage_key=storage_key,
            request_digest=request_digest,
            recover=lambda: self._recover_compensation(
                request_digest=request_digest,
                patch_receipt=patch_receipt,
                patch_result=patch_result,
            ),
            record=record,
        )
        if resumed is not None:
            return resumed

        self._assert_clean()
        parent_commit = self.head()
        if parent_commit != commit_to_revert:
            raise FreshnessError("HEAD_DRIFT")
        before_digest = self._bytes_digest(target.read_bytes())
        if before_digest != patch_receipt.after_state_digest:
            raise FreshnessError("CONTENT_DRIFT")
        with self.store.transaction() as connection:
            self.store.prepare_external_operation(
                connection,
                operation_key=storage_key,
                request_digest=request_digest,
                operation="COMPENSATE",
                repository_id=self.repository_id,
                expected_external_parent=parent_commit,
            )
        try:
            self._git(
                *_GIT_AUTHOR,
                *_GIT_HOOKLESS,
                "revert",
                "--no-commit",
                "--no-edit",
                commit_to_revert,
            )
            self._git(
                *_GIT_AUTHOR,
                *_GIT_HOOKLESS,
                "commit",
                "--no-verify",
                "-m",
                "orgrebase: compensate approved downstream candidate",
                "-m",
                f"This reverts commit {commit_to_revert}.",
                "-m",
                f"OrgRebase-Approval: {approval_digest}",
                "-m",
                f"OrgRebase-Request: {request_digest}",
            )
        except Exception:
            self._git("revert", "--abort", allow_failure=True)
            self._git(
                "restore",
                "--staged",
                "--worktree",
                "--",
                relative_path,
                allow_failure=True,
            )
            raise
        commit = self.head()
        if self._commit_parent(commit) != parent_commit:
            raise GitToolError("CONCURRENT_EXTERNAL_WRITER")
        after_digest = self._bytes_digest(target.read_bytes())
        if after_digest != patch_receipt.before_state_digest:
            raise GitToolError("COMPENSATION_POSTCONDITION_FAILED")
        if self._committed_content_digest(commit, relative_path) != after_digest:
            raise GitToolError("COMMITTED_BLOB_POSTCONDITION_FAILED")
        result = {
            "operation": "COMPENSATE",
            "repository_id": self.repository_id,
            "relative_path": relative_path,
            "parent_commit": parent_commit,
            "commit": commit,
            "before_content_digest": before_digest,
            "after_content_digest": after_digest,
            "compensates_commit": commit_to_revert,
            "compensates_receipt_digest": patch_receipt.digest,
        }
        self._mark_external_committed(storage_key, result)
        return record(result)

    def verify(
        self,
        patch_invocation: dict[str, Any],
        compensation_invocation: dict[str, Any],
    ) -> dict[str, Any]:
        patch_receipt = ToolInvocationReceipt.model_validate(patch_invocation["receipt"])
        compensation_receipt = ToolInvocationReceipt.model_validate(
            compensation_invocation["receipt"]
        )
        patch_result = dict(patch_invocation["result"])
        compensation_result = dict(compensation_invocation["result"])
        if sha256_digest(patch_result) != patch_receipt.result_digest:
            raise GitToolError("PATCH_RESULT_DIGEST_MISMATCH")
        if sha256_digest(compensation_result) != compensation_receipt.result_digest:
            raise GitToolError("COMPENSATION_RESULT_DIGEST_MISMATCH")
        if compensation_receipt.compensation_ref != patch_receipt.digest:
            raise GitToolError("COMPENSATION_BINDING_MISMATCH")
        if compensation_result.get("compensates_commit") != patch_result.get("commit"):
            raise GitToolError("COMPENSATION_COMMIT_MISMATCH")
        if self.head() != compensation_result.get("commit"):
            raise FreshnessError("HEAD_DRIFT")
        self._assert_clean()
        relative_path = str(patch_result["relative_path"])
        if self.content_digest(relative_path) != patch_receipt.before_state_digest:
            raise GitToolError("COMPENSATION_POSTCONDITION_FAILED")
        if (
            self._committed_content_digest(str(patch_result["commit"]), relative_path)
            != patch_receipt.after_state_digest
            or self._committed_content_digest(
                str(compensation_result["commit"]), relative_path
            )
            != patch_receipt.before_state_digest
        ):
            raise GitToolError("COMMITTED_BLOB_POSTCONDITION_FAILED")
        self._git("fsck", "--no-dangling")
        return {
            "status": "PASS",
            "repository_id": self.repository_id,
            "patch_commit": patch_result["commit"],
            "compensation_commit": compensation_result["commit"],
            "patch_receipt_digest": patch_receipt.digest,
            "compensation_receipt_digest": compensation_receipt.digest,
            "postcondition_digest": patch_receipt.before_state_digest,
            "worktree_clean": True,
            "git_fsck": "PASS",
        }

    @property
    def repository_id(self) -> str:
        root_commits = sorted(
            self._git("rev-list", "--max-parents=0", "--all").splitlines()
        )
        if not root_commits:
            raise GitToolError("REPOSITORY_HAS_NO_ROOT_COMMIT")
        return sha256_digest(
            {
                "tool_contract": f"{GIT_ARTIFACT_CONTRACT.id}@{GIT_ARTIFACT_CONTRACT.version}",
                "root_commits": root_commits,
            }
        )

    def _resume_external_operation(
        self,
        *,
        storage_key: str,
        request_digest: str,
        recover: Callable[[], dict[str, Any] | None],
        record: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any] | None:
        existing = self.store.get_idempotent(storage_key, request_digest)
        if existing is not None:
            return existing
        journal = self.store.get_external_operation(storage_key)
        if journal is None:
            return None
        if journal["request_digest"] != request_digest:
            raise RuntimeError("IDEMPOTENCY_CONFLICT")
        if journal["status"] == "EXTERNAL_COMMITTED":
            recovered = recover()
            if recovered is None:
                raise GitToolError("EXTERNAL_COMMITTED_NOT_PRESENT")
            journaled = dict(journal["result"])
            if recovered.get("commit") != journaled.get("commit"):
                raise GitToolError("EXTERNAL_COMMITTED_HEAD_DRIFT")
            return record(journaled)
        if journal["status"] == "PREPARED":
            recovered = recover()
            if recovered is None:
                return None
            self._mark_external_committed(storage_key, recovered)
            return record(recovered)
        if journal["status"] == "RECORDED":
            raise GitToolError("JOURNAL_RECORDED_WITHOUT_IDEMPOTENCY_RESULT")
        raise GitToolError(f"MANUAL_RECOVERY_REQUIRED:{journal['status']}")

    def _record(
        self,
        *,
        storage_key: str,
        idempotency_key: str,
        request_digest: str,
        actor_id: str,
        workflow_run_id: str,
        run_nonce: str,
        result: dict[str, Any],
        before_digest: str,
        after_digest: str,
        commit: str,
        compensation_ref: str | None,
    ) -> dict[str, Any]:
        result_digest = sha256_digest(result)
        with self.store.transaction() as connection:
            event_digest = self.store.append_event(
                connection,
                "REAL_GIT_TOOL_INVOKED",
                {
                    "tool_ref": f"{GIT_ARTIFACT_CONTRACT.id}@{GIT_ARTIFACT_CONTRACT.version}",
                    "actor_id": actor_id,
                    "workflow_run_id": workflow_run_id,
                    "run_nonce": run_nonce,
                    "request_digest": request_digest,
                    "result_digest": result_digest,
                    "repository_id": self.repository_id,
                    "external_operation_id": commit,
                    "status": "SUCCEEDED",
                },
            )
            receipt = ToolInvocationReceipt(
                id=f"git-tool-call:{idempotency_key}",
                tool_ref=f"{GIT_ARTIFACT_CONTRACT.id}@{GIT_ARTIFACT_CONTRACT.version}",
                actor_id=actor_id,
                workflow_run_id=workflow_run_id,
                run_nonce=run_nonce,
                request_digest=request_digest,
                result_digest=result_digest,
                status="SUCCEEDED",
                started_at="2026-08-14T00:04:30Z",
                completed_at="2026-08-14T00:04:31Z",
                audit_event_digest=event_digest,
                external_operation_id=commit,
                before_state_digest=before_digest,
                after_state_digest=after_digest,
                compensation_ref=compensation_ref,
                evidence_class=EvidenceClass.LOCAL_REAL_TOOL,
            )
            payload = {
                "contract": GIT_ARTIFACT_CONTRACT.model_dump(mode="json"),
                "result": result,
                "receipt": receipt.model_dump(mode="json"),
            }
            self.store.save_artifact(
                connection,
                receipt.id,
                "application/vnd.orgrebase.git-tool-invocation+json",
                payload,
            )
            self.store.save_idempotent(connection, storage_key, request_digest, payload)
            self.store.update_external_operation(
                connection,
                operation_key=storage_key,
                status="RECORDED",
                external_operation_id=commit,
                result=result,
            )
        return payload

    def _mark_external_committed(
        self, operation_key: str, result: dict[str, Any]
    ) -> None:
        with self.store.transaction() as connection:
            self.store.update_external_operation(
                connection,
                operation_key=operation_key,
                status="EXTERNAL_COMMITTED",
                external_operation_id=str(result["commit"]),
                result=result,
            )

    def _recover_patch(
        self,
        *,
        request_digest: str,
        relative_path: str,
        expected_head: str,
        before_digest: str,
        after_digest: str,
    ) -> dict[str, Any] | None:
        current = self.head()
        if self._is_dirty():
            if current != expected_head:
                raise GitToolError("MANUAL_RECOVERY_REQUIRED:PATCH")
            self._git("restore", "--staged", "--worktree", "--", relative_path)
            return None
        if current == expected_head:
            return None
        message = self._git("show", "-s", "--format=%B", current)
        if (
            self._commit_parent(current) != expected_head
            or f"OrgRebase-Request: {request_digest}" not in message
            or self.content_digest(relative_path) != after_digest
            or self._committed_content_digest(current, relative_path) != after_digest
        ):
            raise GitToolError("MANUAL_RECOVERY_REQUIRED:PATCH")
        return {
            "operation": "PATCH",
            "repository_id": self.repository_id,
            "relative_path": relative_path,
            "parent_commit": expected_head,
            "commit": current,
            "before_content_digest": before_digest,
            "after_content_digest": after_digest,
        }

    def _recover_compensation(
        self,
        *,
        request_digest: str,
        patch_receipt: ToolInvocationReceipt,
        patch_result: dict[str, Any],
    ) -> dict[str, Any] | None:
        current = self.head()
        patch_commit = str(patch_result["commit"])
        relative_path = str(patch_result["relative_path"])
        if self._is_dirty():
            if current != patch_commit:
                raise GitToolError("MANUAL_RECOVERY_REQUIRED:COMPENSATE")
            self._git("revert", "--abort", allow_failure=True)
            self._git("restore", "--staged", "--worktree", "--", relative_path)
            return None
        if current == patch_commit:
            return None
        message = self._git("show", "-s", "--format=%B", current)
        if (
            self._commit_parent(current) != patch_commit
            or f"OrgRebase-Request: {request_digest}" not in message
            or self.content_digest(relative_path) != patch_receipt.before_state_digest
            or self._committed_content_digest(current, relative_path)
            != patch_receipt.before_state_digest
        ):
            raise GitToolError("MANUAL_RECOVERY_REQUIRED:COMPENSATE")
        return {
            "operation": "COMPENSATE",
            "repository_id": self.repository_id,
            "relative_path": relative_path,
            "parent_commit": patch_commit,
            "commit": current,
            "before_content_digest": patch_receipt.after_state_digest,
            "after_content_digest": patch_receipt.before_state_digest,
            "compensates_commit": patch_commit,
            "compensates_receipt_digest": patch_receipt.digest,
        }

    def _commit_parent(self, commit: str) -> str:
        return self._git("rev-parse", f"{commit}^").strip()

    def _assert_repository(self) -> None:
        if not (self.repository / ".git").is_dir():
            raise GitToolError("NOT_A_GIT_REPOSITORY")
        top = Path(self._git("rev-parse", "--show-toplevel").strip()).resolve()
        if top != self.repository:
            raise GitToolError("REPOSITORY_ROOT_MISMATCH")
        worktrees = [
            line
            for line in self._git("worktree", "list", "--porcelain").splitlines()
            if line.startswith("worktree ")
        ]
        if len(worktrees) != 1:
            raise GitToolError("MULTIPLE_WORKTREES_UNSUPPORTED")

    def _assert_clean(self) -> None:
        if self._is_dirty():
            raise FreshnessError("REPOSITORY_NOT_CLEAN")

    def _is_dirty(self) -> bool:
        return bool(self._git("status", "--porcelain", "--untracked-files=all").strip())

    @contextmanager
    def _exclusive_lock(self) -> Any:
        lock_path = self.repository / ".git" / "orgrebase.lock"
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            flock(descriptor, LOCK_EX)
            yield
        finally:
            flock(descriptor, LOCK_UN)
            os.close(descriptor)

    @staticmethod
    def _authorize(actor_id: str, approval_digest: str) -> None:
        if actor_id != GIT_EXECUTOR:
            raise AuthorizationError("only the control-plane Git executor may write")
        if not approval_digest.startswith("sha256:") or len(approval_digest) != 71:
            raise AuthorizationError("APPROVAL_REQUIRED")

    def _target(self, relative_path: str) -> Path:
        relative = PurePosixPath(relative_path)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise AuthorizationError("PATH_OUTSIDE_ALLOWLIST")
        try:
            relative.relative_to(ALLOWED_PREFIX)
        except ValueError as exc:
            raise AuthorizationError("PATH_OUTSIDE_ALLOWLIST") from exc
        target = self.repository.joinpath(*relative.parts)
        resolved_parent = target.parent.resolve()
        if not resolved_parent.is_relative_to(self.repository) or target.is_symlink():
            raise AuthorizationError("PATH_OUTSIDE_ALLOWLIST")
        if not target.is_file():
            raise GitToolError("TARGET_NOT_REGULAR_FILE")
        return target

    def _git(self, *args: str, allow_failure: bool = False) -> str:
        return self._run_raw(self.repository, *args, allow_failure=allow_failure)

    def _committed_content_digest(self, commit: str, relative_path: str) -> str:
        completed = subprocess.run(
            ["git", "show", f"{commit}:{relative_path}"],
            cwd=self.repository,
            check=False,
            capture_output=True,
            timeout=15,
        )
        if completed.returncode:
            raise GitToolError("GIT_OPERATION_FAILED:show")
        return self._bytes_digest(completed.stdout)

    def _replace_target(self, target: Path, payload: bytes) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="orgrebase-write-", dir=self.repository / ".git"
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _run_raw(repository: Path, *args: str, allow_failure: bool = False) -> str:
        env = os.environ.copy()
        env["LC_ALL"] = "C"
        env["LANG"] = "C"
        env["GIT_TERMINAL_PROMPT"] = "0"
        completed = subprocess.run(
            ["git", *args],
            cwd=repository,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
        )
        if completed.returncode and not allow_failure:
            raise GitToolError(f"GIT_OPERATION_FAILED:{_git_verb(args)}")
        return completed.stdout

    @staticmethod
    def _bytes_digest(payload: bytes) -> str:
        return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _git_verb(args: tuple[str, ...]) -> str:
    index = 0
    while index + 1 < len(args) and args[index] == "-c":
        index += 2
    return args[index] if index < len(args) else "unknown"
