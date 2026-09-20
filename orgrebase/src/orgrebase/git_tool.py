"""A minimal real, reversible Git tool executed only by the control plane."""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from fcntl import LOCK_EX, LOCK_UN, flock
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from orgrebase.clock import Clock, SystemClock
from orgrebase.commit_gateway import (
    CommitGateway,
    EffectRequest,
    ResolutionState,
    TargetResolution,
)
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


@dataclass(frozen=True)
class _GitTargetAdapter:
    dispatch: Callable[[], dict[str, Any]]
    lookup: Callable[[], dict[str, Any] | None]

    def execute(self, effect: EffectRequest) -> TargetResolution:
        result = self.dispatch()
        return TargetResolution(
            ResolutionState.CONFIRMED, effect.effect_id, effect.digest,
            operation_id=str(result["commit"]), evidence=result,
        )

    def query_effect(self, effect: EffectRequest) -> TargetResolution:
        result = self.lookup()
        if result is None:
            return TargetResolution(
                ResolutionState.ABSENT_FINAL, effect.effect_id, effect.digest,
                evidence={"proof": "EXCLUSIVE_GIT_HISTORY_ABSENCE", "parent": effect.expected_version},
            )
        return TargetResolution(
            ResolutionState.CONFIRMED, effect.effect_id, effect.digest,
            operation_id=str(result["commit"]), evidence=result,
        )


class GitArtifactTool:
    def __init__(self, repository: Path, store: StateStore, *, clock: Clock | None = None) -> None:
        self.repository = repository.resolve()
        self.store = store
        self.clock = clock or SystemClock()
        self._lock_descriptor: int | None = None
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
        self._allowed_relative_path(relative_path)
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
        historical = self._historical_receipt(storage_key, request_digest)
        if historical is not None:
            return historical

        def record(connection: Any, result: Mapping[str, Any]) -> dict[str, Any]:
            return self._record(
                connection=connection,
                storage_key=storage_key,
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

        def dispatch() -> dict[str, Any]:
            target = self._target(relative_path)
            self._restore_interrupted_prepare(storage_key, relative_path, expected_head)
            self._assert_clean()
            parent_commit = self.head()
            if parent_commit != expected_head:
                raise FreshnessError("HEAD_DRIFT")
            before_digest = self._bytes_digest(target.read_bytes())
            if before_digest != expected_content_digest:
                raise FreshnessError("CONTENT_DRIFT")
            if after_digest == before_digest:
                raise GitToolError("NO_CHANGE")

            self._replace_target(target, content.encode("utf-8"))
            try:
                self._git("add", "--", relative_path)
                if self.head() != parent_commit:
                    raise FreshnessError("HEAD_DRIFT_DURING_PREPARE")
                self._commit_effect(
                    parent_commit, approval_digest, request_digest,
                    "orgrebase: apply approved downstream candidate",
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
            return result

        effect = EffectRequest(
            effect_id=storage_key,
            tenant_id=self.store.tenant_id or "controlled-local",
            target_key=f"git:{self.repository_id}",
            action="PATCH",
            expected_version=expected_head,
            approval_digest=approval_digest,
            payload={**request, "content": content},
        )
        adapter = _GitTargetAdapter(dispatch=dispatch, lookup=lambda: self._recover_patch(
                request_digest=request_digest,
                relative_path=relative_path,
                expected_head=expected_head,
                before_digest=expected_content_digest,
                after_digest=after_digest,
            ))
        # Reject a fresh drift before reserving the target; dispatch checks it again.
        if self.store.get_effect(storage_key) is None:
            self._assert_clean()
            if self.head() != expected_head:
                raise FreshnessError("HEAD_DRIFT")
            if self.content_digest(relative_path) != expected_content_digest:
                raise FreshnessError("CONTENT_DRIFT")
        gateway = CommitGateway(
            self.store,
            tenant_id=effect.tenant_id,
            worker_id=f"git-worker:{uuid4()}",
            clock=self.clock,
            authorize=lambda request, action: self._authorize(actor_id, request.approval_digest),
        )
        return gateway.run(effect, adapter, record=record)

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
        self._allowed_relative_path(relative_path)
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
        historical = self._historical_receipt(storage_key, request_digest)
        if historical is not None:
            return historical

        def record(connection: Any, result: Mapping[str, Any]) -> dict[str, Any]:
            return self._record(
                connection=connection,
                storage_key=storage_key,
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

        def dispatch() -> dict[str, Any]:
            target = self._target(relative_path)
            self._restore_interrupted_prepare(storage_key, relative_path, commit_to_revert)
            self._assert_clean()
            parent_commit = self.head()
            if parent_commit != commit_to_revert:
                raise FreshnessError("HEAD_DRIFT")
            before_digest = self._bytes_digest(target.read_bytes())
            if before_digest != patch_receipt.after_state_digest:
                raise FreshnessError("CONTENT_DRIFT")
            try:
                self._git(
                    *_GIT_AUTHOR,
                    *_GIT_HOOKLESS,
                    "revert",
                    "--no-commit",
                    "--no-edit",
                    commit_to_revert,
                )
                self._commit_effect(
                    parent_commit, approval_digest, request_digest,
                    "orgrebase: compensate approved downstream candidate",
                    compensation=commit_to_revert,
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
            return result

        effect = EffectRequest(
            effect_id=storage_key,
            tenant_id=self.store.tenant_id or "controlled-local",
            target_key=f"git:{self.repository_id}",
            action="COMPENSATE",
            expected_version=commit_to_revert,
            approval_digest=approval_digest,
            payload=request,
        )
        adapter = _GitTargetAdapter(dispatch=dispatch, lookup=lambda: self._recover_compensation(
                request_digest=request_digest,
                patch_receipt=patch_receipt,
                patch_result=patch_result,
            ))
        # Reject a fresh drift before reserving the target; dispatch checks it again.
        if self.store.get_effect(storage_key) is None:
            self._assert_clean()
            if self.head() != commit_to_revert:
                raise FreshnessError("HEAD_DRIFT")
            if self.content_digest(relative_path) != patch_receipt.after_state_digest:
                raise FreshnessError("CONTENT_DRIFT")
        gateway = CommitGateway(
            self.store,
            tenant_id=effect.tenant_id,
            worker_id=f"git-worker:{uuid4()}",
            clock=self.clock,
            authorize=lambda request, action: self._authorize(actor_id, request.approval_digest),
        )
        return gateway.run(effect, adapter, record=record)

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

    def _historical_receipt(self, key: str, digest: str) -> dict[str, Any] | None:
        if self.store.get_effect(key) is not None:
            return None
        receipt = self.store.get_idempotent(key, digest)
        if receipt is not None:
            return receipt
        if self.store.get_external_operation(key) is not None:
            raise GitToolError("LEGACY_EFFECT_RECONCILIATION_REQUIRED")
        return None

    def _record(
        self,
        *,
        connection: Any,
        storage_key: str,
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
            id=f"git-tool-call:{sha256_digest(storage_key).removeprefix('sha256:')}",
            tool_ref=f"{GIT_ARTIFACT_CONTRACT.id}@{GIT_ARTIFACT_CONTRACT.version}",
            actor_id=actor_id,
            workflow_run_id=workflow_run_id,
            run_nonce=run_nonce,
            request_digest=request_digest,
            result_digest=result_digest,
            status="SUCCEEDED",
            started_at=self.store.get_effect(storage_key, connection=connection)["created_at"],
            completed_at=self.clock.now(),
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
        return payload

    def _recover_patch(
        self,
        *,
        request_digest: str,
        relative_path: str,
        expected_head: str,
        before_digest: str,
        after_digest: str,
    ) -> dict[str, Any] | None:
        current = self._find_effect_commit(request_digest)
        if current is None:
            if self.head() != expected_head:
                raise GitToolError("MANUAL_RECOVERY_REQUIRED:PATCH")
            return None
        message = self._git("show", "-s", "--format=%B", current)
        if (
            self._commit_parent(current) != expected_head
            or f"OrgRebase-Request: {request_digest}" not in message
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
        current = self._find_effect_commit(request_digest)
        patch_commit = str(patch_result["commit"])
        relative_path = str(patch_result["relative_path"])
        if current is None:
            if self.head() != patch_commit:
                raise GitToolError("MANUAL_RECOVERY_REQUIRED:COMPENSATE")
            return None
        message = self._git("show", "-s", "--format=%B", current)
        if (
            self._commit_parent(current) != patch_commit
            or f"OrgRebase-Request: {request_digest}" not in message
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

    @staticmethod
    def _effect_ref(request_digest: str) -> str:
        return f"refs/orgrebase/effects/{request_digest.removeprefix('sha256:')}"

    def _find_effect_commit(self, request_digest: str) -> str | None:
        commit = self._git(
            "rev-parse", "--verify", "--quiet", self._effect_ref(request_digest),
            allow_failure=True,
        ).strip()
        return commit or None

    def _commit_effect(
        self, parent: str, approval_digest: str, request_digest: str, message: str,
        *, compensation: str | None = None,
    ) -> None:
        tree = self._git("write-tree").strip()
        messages = ("-m", message, "-m", f"OrgRebase-Approval: {approval_digest}",
                    "-m", f"OrgRebase-Request: {request_digest}")
        if compensation is not None:
            messages += ("-m", f"This reverts commit {compensation}.")
        commit = self._git(*_GIT_AUTHOR, "commit-tree", tree, "-p", parent, *messages).strip()
        # The branch precondition and durable effect identity change atomically.
        # The effect ref also prevents pruning a confirmed but subsequently reverted commit.
        commands = (f"start\nupdate HEAD {commit} {parent}\n"
                    f"create {self._effect_ref(request_digest)} {commit}\nprepare\ncommit\n")
        self._run_raw(
            self.repository, "update-ref", "--stdin", input_text=commands,
            lock_descriptor=self._lock_descriptor,
        )

    def _restore_interrupted_prepare(
        self, effect_id: str, relative_path: str, expected_head: str
    ) -> None:
        effect = self.store.get_effect(effect_id)
        proof = effect.get("result") if effect else None
        if not proof or proof.get("proof") != "EXCLUSIVE_GIT_HISTORY_ABSENCE":
            return
        if self.head() != expected_head:
            raise FreshnessError("HEAD_DRIFT")
        changed = set(self._git("diff", "HEAD", "--name-only").splitlines())
        untracked = self._git("ls-files", "--others", "--exclude-standard").strip()
        if changed - {relative_path} or untracked:
            raise GitToolError("MANUAL_RECOVERY_REQUIRED:UNRELATED_WORKTREE_CHANGE")
        self._git("revert", "--abort", allow_failure=True)
        self._git("restore", "--staged", "--worktree", "--", relative_path)

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
            self._lock_descriptor = descriptor
            yield
        finally:
            self._lock_descriptor = None
            flock(descriptor, LOCK_UN)
            os.close(descriptor)

    @staticmethod
    def _authorize(actor_id: str, approval_digest: str) -> None:
        if actor_id != GIT_EXECUTOR:
            raise AuthorizationError("only the control-plane Git executor may write")
        if not approval_digest.startswith("sha256:") or len(approval_digest) != 71:
            raise AuthorizationError("APPROVAL_REQUIRED")

    @staticmethod
    def _allowed_relative_path(relative_path: str) -> PurePosixPath:
        relative = PurePosixPath(relative_path)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise AuthorizationError("PATH_OUTSIDE_ALLOWLIST")
        try:
            relative.relative_to(ALLOWED_PREFIX)
        except ValueError as exc:
            raise AuthorizationError("PATH_OUTSIDE_ALLOWLIST") from exc
        return relative

    def _target(self, relative_path: str) -> Path:
        relative = self._allowed_relative_path(relative_path)
        target = self.repository.joinpath(*relative.parts)
        resolved_parent = target.parent.resolve()
        if not resolved_parent.is_relative_to(self.repository) or target.is_symlink():
            raise AuthorizationError("PATH_OUTSIDE_ALLOWLIST")
        if not target.is_file():
            raise GitToolError("TARGET_NOT_REGULAR_FILE")
        return target

    def _git(self, *args: str, allow_failure: bool = False) -> str:
        return self._run_raw(
            self.repository, *args, allow_failure=allow_failure,
            lock_descriptor=self._lock_descriptor,
        )

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
    def _run_raw(
        repository: Path, *args: str, allow_failure: bool = False,
        lock_descriptor: int | None = None,
        input_text: str | None = None,
    ) -> str:
        env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR") if key in os.environ}
        env["LC_ALL"] = "C"
        env["LANG"] = "C"
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_GLOBAL"] = "/dev/null"
        completed = subprocess.run(
            ["git", *args],
            cwd=repository,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
            pass_fds=() if lock_descriptor is None else (lock_descriptor,),
            input=input_text,
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
