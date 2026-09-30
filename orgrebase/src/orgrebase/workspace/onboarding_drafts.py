"""Authenticated, retention-bound Enterprise Quote onboarding drafts.

Raw draft files live only in :class:`PrivateRecordStore`.  Canonical artifacts
and idempotency records retain low-sensitivity identities, digests and status so
an operator can recover a lost response without replaying an admission or
approval.  Draft versions are immutable and sequential; there is no second
mutable onboarding queue.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from orgrebase.auth import AuthenticationError, request_principal
from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.workspace.change_proposals import require_action
from orgrebase.workspace.pilot_authoring import (
    EnterpriseQuotePilotAuthoringError,
    preflight_enterprise_quote_pilot_payloads,
    seal_enterprise_quote_pilot_payloads,
    sealed_enterprise_quote_pilot_bytes,
)

ONBOARDING_DRAFT_MEDIA_TYPE = (
    "application/vnd.orgrebase.enterprise-onboarding-draft-receipt+json"
)
ONBOARDING_OPERATION_MEDIA_TYPE = (
    "application/vnd.orgrebase.enterprise-onboarding-operation-receipt+json"
)
MAX_DRAFT_BYTES = 1_048_576
MAX_DRAFT_REVISION = 1_000
SUPPORTED_DRAFT_PATHS = (
    "pack.json",
    "profile.json",
    "components/domain.json",
    "components/knowledge.json",
    "components/authority.json",
    "components/capability.json",
    "components/dependency.json",
)
COMPONENT_PATHS = {
    "DOMAIN": "components/domain.json",
    "KNOWLEDGE": "components/knowledge.json",
    "AUTHORITY": "components/authority.json",
    "CAPABILITY": "components/capability.json",
    "DEPENDENCY": "components/dependency.json",
}
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


class EnterpriseOnboardingDraftError(ValueError):
    """Stable request failure that never includes private draft values."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _Command(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    operation_key: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    draft_id: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[a-z][a-z0-9._-]*$",
    )


class _FilesCommand(_Command):
    files: dict[str, dict[str, JsonValue]]

    @model_validator(mode="after")
    def validate_files(self) -> _FilesCommand:
        if set(self.files) != set(SUPPORTED_DRAFT_PATHS):
            raise ValueError("ONBOARDING_DRAFT_FILE_SET_INVALID")
        try:
            encoded = canonical_json(self.files).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("ONBOARDING_DRAFT_JSON_INVALID") from exc
        if len(encoded) > MAX_DRAFT_BYTES:
            raise ValueError("ONBOARDING_DRAFT_SIZE_LIMIT")
        return self


class CreateOnboardingDraft(_FilesCommand):
    pass


class UpdateOnboardingDraft(_FilesCommand):
    base_revision: int = Field(ge=1, le=MAX_DRAFT_REVISION - 1)
    base_receipt_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class DraftRevisionCommand(_Command):
    revision: int = Field(ge=1, le=MAX_DRAFT_REVISION)
    draft_receipt_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class PreflightOnboardingDraft(DraftRevisionCommand):
    pass


class SealOnboardingDraft(DraftRevisionCommand):
    pass


def _workspace_ref(workspace: Any) -> str:
    return sha256_digest(
        {
            "organization_id": workspace.profile.organization_id,
            "workspace_id": workspace.store.workspace_id,
        }
    )


def _identity(workspace: Any) -> tuple[str, str, str]:
    actor_id = require_action(workspace, "govern")
    principal = request_principal.get()
    if principal is None:
        raise AuthenticationError("ONBOARDING_DRAFT_PRINCIPAL_REQUIRED")
    owner_ref = sha256_digest(
        {
            "issuer": principal.issuer,
            "subject": principal.subject,
            "tenant_id": principal.tenant_id,
            "actor_id": principal.actor_id,
        }
    )
    return actor_id, owner_ref, _workspace_ref(workspace)


def _draft_ref(workspace_ref: str, draft_id: str) -> str:
    return sha256_digest({"workspace_ref": workspace_ref, "draft_id": draft_id})


def _artifact_suffix(digest: str) -> str:
    if not _DIGEST.fullmatch(digest):  # pragma: no cover - internal digest invariant
        raise IntegrityError("ONBOARDING_DRAFT_DIGEST_INVALID")
    return digest.removeprefix("sha256:")


def _draft_artifact_id(draft_ref: str, revision: int) -> str:
    return f"enterprise-onboarding-draft:{_artifact_suffix(draft_ref)}@r{revision}"


def _operation_ref(workspace_ref: str, operation: str, operation_key: str) -> str:
    return sha256_digest(
        {
            "workspace_ref": workspace_ref,
            "operation": operation,
            "operation_key": operation_key,
        }
    )


def _operation_artifact_id(operation_ref: str) -> str:
    return f"enterprise-onboarding-operation:{_artifact_suffix(operation_ref)}"


def _idempotency_key(operation_ref: str) -> str:
    return f"enterprise-onboarding-operation:{_artifact_suffix(operation_ref)}"


def _private_record_ref(
    *,
    draft_ref: str,
    revision: int,
    content_digest: str,
) -> str:
    identity = sha256_digest(
        {
            "draft_ref": draft_ref,
            "revision": revision,
            "content_digest": content_digest,
        }
    )
    return f"enterprise-onboarding-private:{_artifact_suffix(identity)}"


def _request_digest(
    *,
    operation: str,
    command: _Command,
    workspace_ref: str,
    owner_ref: str,
) -> str:
    return sha256_digest(
        {
            "schema_version": "orgrebase.enterprise-onboarding-operation-request.v1",
            "operation": operation,
            "workspace_ref": workspace_ref,
            "owner_ref": owner_ref,
            "command": command.model_dump(mode="json"),
        }
    )


def _receipt(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["receipt_digest"] = sha256_digest(result)
    return result


def _public_result(workspace: Any, result: dict[str, Any]) -> dict[str, Any]:
    public = {key: value for key, value in result.items() if key != "private_record_ref"}
    record_ref = result.get("private_record_ref")
    public["private_input_status"] = (
        workspace.private_records.record_status(record_ref)
        if isinstance(record_ref, str)
        else "NOT_STORED"
    )
    return public


def _validate_result_binding(
    result: dict[str, Any],
    *,
    workspace_ref: str,
    owner_ref: str,
) -> None:
    if result.get("workspace_ref") != workspace_ref:
        raise AuthenticationError("ONBOARDING_DRAFT_WORKSPACE_DENIED", 403)
    if result.get("owner_ref") != owner_ref:
        raise AuthenticationError("ONBOARDING_DRAFT_OWNER_DENIED", 403)
    receipt_digest = result.get("receipt_digest")
    body = {key: value for key, value in result.items() if key != "receipt_digest"}
    if not isinstance(receipt_digest, str) or sha256_digest(body) != receipt_digest:
        raise IntegrityError("ONBOARDING_DRAFT_RECEIPT_DIGEST_MISMATCH")


def _load_draft(
    workspace: Any,
    *,
    draft_id: str,
    revision: int,
    receipt_digest: str,
    workspace_ref: str,
    owner_ref: str,
) -> dict[str, Any]:
    draft_ref = _draft_ref(workspace_ref, draft_id)
    artifact = workspace.store.load_artifact(
        _draft_artifact_id(draft_ref, revision),
        ONBOARDING_DRAFT_MEDIA_TYPE,
    )
    result = artifact.payload
    _validate_result_binding(result, workspace_ref=workspace_ref, owner_ref=owner_ref)
    if (
        result.get("draft_ref") != draft_ref
        or result.get("revision") != revision
        or result.get("receipt_digest") != receipt_digest
    ):
        raise EnterpriseOnboardingDraftError("ONBOARDING_DRAFT_REVISION_MISMATCH")
    return result


def _read_private_payload(
    workspace: Any,
    *,
    draft: dict[str, Any],
    actor_id: str,
) -> dict[str, Any]:
    record_ref = draft.get("private_record_ref")
    draft_ref = draft.get("draft_ref")
    if not isinstance(record_ref, str) or not isinstance(draft_ref, str):
        raise IntegrityError("ONBOARDING_DRAFT_PRIVATE_BINDING_INVALID")
    try:
        private = workspace.private_records.read_owned(
            record_ref,
            owner_id=actor_id,
            scope_ref=draft_ref,
        )
    except PermissionError as exc:
        raise AuthenticationError("ONBOARDING_DRAFT_PRIVATE_BINDING_DENIED", 403) from exc
    if private is None:
        raise EnterpriseOnboardingDraftError("ONBOARDING_DRAFT_PRIVATE_INPUT_UNAVAILABLE")
    return private


def _read_private_files(
    workspace: Any, *, draft: dict[str, Any], actor_id: str,
) -> dict[str, dict[str, JsonValue]]:
    private = _read_private_payload(workspace, draft=draft, actor_id=actor_id)
    files = private.get("files")
    if "sealed_file_texts" in private:
        texts = private["sealed_file_texts"]
        if not isinstance(texts, dict) or set(texts) != set(SUPPORTED_DRAFT_PATHS):
            raise IntegrityError("ONBOARDING_DRAFT_PRIVATE_PAYLOAD_INVALID")
        try:
            files = {path: json.loads(value) for path, value in texts.items()}
        except (TypeError, ValueError) as exc:
            raise IntegrityError("ONBOARDING_DRAFT_PRIVATE_PAYLOAD_INVALID") from exc
    if not isinstance(files, dict) or set(files) != set(SUPPORTED_DRAFT_PATHS):
        raise IntegrityError("ONBOARDING_DRAFT_PRIVATE_PAYLOAD_INVALID")
    if sha256_digest(files) != draft.get("content_digest"):
        raise IntegrityError("ONBOARDING_DRAFT_PRIVATE_CONTENT_DIGEST_MISMATCH")
    return files


def _component_digests(files: dict[str, dict[str, JsonValue]]) -> dict[str, str]:
    return {
        kind: sha256_digest(files[path])
        for kind, path in COMPONENT_PATHS.items()
    }


def _safe_preflight(receipt: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": receipt["status"],
        "candidate_pack_digest": receipt["candidate_pack_digest"],
        "profile_digest": receipt["profile_digest"],
        "source_admission_receipt_digest": receipt[
            "source_admission_receipt_digest"
        ],
        "runtime_projection_receipt_digest": receipt[
            "runtime_projection_receipt_digest"
        ],
        "required_inputs": [
            {
                "component_kind": item["component_kind"],
                "status": item["status"],
            }
            for item in receipt["required_inputs"]
        ],
        "sealed_output_created": False,
        "profile_admitted_for_workspace": False,
        "workspace_activated": False,
        "canonical_target_writes": 0,
    }


def _save_operation(
    workspace: Any,
    connection: Any,
    *,
    operation_ref: str,
    request_digest: str,
    result: dict[str, Any],
) -> None:
    workspace.store.save_artifact(
        connection,
        _operation_artifact_id(operation_ref),
        ONBOARDING_OPERATION_MEDIA_TYPE,
        result,
    )
    workspace.store.save_idempotent(
        connection,
        _idempotency_key(operation_ref),
        request_digest,
        result,
    )


def _existing_operation(
    workspace: Any,
    *,
    operation_ref: str,
    request_digest: str,
    connection: Any | None = None,
) -> dict[str, Any] | None:
    return workspace.store.get_idempotent(
        _idempotency_key(operation_ref),
        request_digest,
        connection=connection,
    )


def _draft_cas_lock(workspace: Any, connection: Any, draft_ref: str) -> None:
    # PostgreSQL ``get_idempotent`` takes a transaction advisory lock even for
    # an absent key. SQLite's write transaction already serializes writers.
    workspace.store.get_idempotent(
        f"enterprise-onboarding-draft-cas:{_artifact_suffix(draft_ref)}",
        "orgrebase.enterprise-onboarding-draft-cas.v1",
        connection=connection,
    )


def create_draft(workspace: Any, command: CreateOnboardingDraft) -> dict[str, Any]:
    command = CreateOnboardingDraft.model_validate(command.model_dump(mode="json"))
    actor_id, owner_ref, workspace_ref = _identity(workspace)
    operation = "CREATE"
    operation_ref = _operation_ref(workspace_ref, operation, command.operation_key)
    request_digest = _request_digest(
        operation=operation,
        command=command,
        workspace_ref=workspace_ref,
        owner_ref=owner_ref,
    )
    existing = _existing_operation(
        workspace,
        operation_ref=operation_ref,
        request_digest=request_digest,
    )
    if existing is not None:
        _validate_result_binding(existing, workspace_ref=workspace_ref, owner_ref=owner_ref)
        return _public_result(workspace, existing)

    draft_ref = _draft_ref(workspace_ref, command.draft_id)
    content_digest = sha256_digest(command.files)
    record_ref = _private_record_ref(
        draft_ref=draft_ref,
        revision=1,
        content_digest=content_digest,
    )
    with workspace._command_lock, workspace.store.transaction() as connection:
        existing = _existing_operation(
            workspace,
            operation_ref=operation_ref,
            request_digest=request_digest,
            connection=connection,
        )
        if existing is not None:
            return _public_result(workspace, existing)
        actor_id, current_owner_ref, current_workspace_ref = _identity(workspace)
        if (current_owner_ref, current_workspace_ref) != (owner_ref, workspace_ref):
            raise AuthenticationError("ONBOARDING_DRAFT_IDENTITY_CHANGED", 403)
        _draft_cas_lock(workspace, connection, draft_ref)
        artifact_id = _draft_artifact_id(draft_ref, 1)
        if workspace.store.artifact_exists(artifact_id, connection=connection):
            raise EnterpriseOnboardingDraftError("ONBOARDING_DRAFT_ALREADY_EXISTS")
        workspace.private_records.write(
            connection,
            record_id=record_ref,
            scope_ref=draft_ref,
            owner_id=actor_id,
            payload={"files": command.files},
        )
        retained = workspace.private_records.retention_seconds > 0
        result = _receipt(
            {
                "schema_version": "orgrebase.enterprise-onboarding-draft-receipt.v1",
                "operation": operation,
                "operation_ref": operation_ref,
                "request_digest": request_digest,
                "status": "DRAFT_CREATED" if retained else "DRAFT_INPUT_NOT_RETAINED",
                "workspace_ref": workspace_ref,
                "owner_ref": owner_ref,
                "draft_ref": draft_ref,
                "revision": 1,
                "content_digest": content_digest,
                "component_digests": _component_digests(command.files),
                "parent_receipt_digest": None,
                "private_record_ref": record_ref,
                "private_input_status_at_commit": "AVAILABLE" if retained else "DELETED",
                "profile_admitted_for_workspace": False,
                "workspace_activated": False,
                "canonical_target_writes": 0,
            }
        )
        workspace.store.save_artifact(
            connection,
            artifact_id,
            ONBOARDING_DRAFT_MEDIA_TYPE,
            result,
        )
        _save_operation(
            workspace,
            connection,
            operation_ref=operation_ref,
            request_digest=request_digest,
            result=result,
        )
    return _public_result(workspace, result)


def update_draft(workspace: Any, command: UpdateOnboardingDraft) -> dict[str, Any]:
    command = UpdateOnboardingDraft.model_validate(command.model_dump(mode="json"))
    actor_id, owner_ref, workspace_ref = _identity(workspace)
    operation = "UPDATE"
    operation_ref = _operation_ref(workspace_ref, operation, command.operation_key)
    request_digest = _request_digest(
        operation=operation,
        command=command,
        workspace_ref=workspace_ref,
        owner_ref=owner_ref,
    )
    existing = _existing_operation(
        workspace,
        operation_ref=operation_ref,
        request_digest=request_digest,
    )
    if existing is not None:
        _validate_result_binding(existing, workspace_ref=workspace_ref, owner_ref=owner_ref)
        return _public_result(workspace, existing)
    base = _load_draft(
        workspace,
        draft_id=command.draft_id,
        revision=command.base_revision,
        receipt_digest=command.base_receipt_digest,
        workspace_ref=workspace_ref,
        owner_ref=owner_ref,
    )
    _read_private_files(workspace, draft=base, actor_id=actor_id)
    next_revision = command.base_revision + 1
    draft_ref = base["draft_ref"]
    content_digest = sha256_digest(command.files)
    record_ref = _private_record_ref(
        draft_ref=draft_ref,
        revision=next_revision,
        content_digest=content_digest,
    )
    with workspace._command_lock, workspace.store.transaction() as connection:
        existing = _existing_operation(
            workspace,
            operation_ref=operation_ref,
            request_digest=request_digest,
            connection=connection,
        )
        if existing is not None:
            return _public_result(workspace, existing)
        actor_id, current_owner_ref, current_workspace_ref = _identity(workspace)
        if (current_owner_ref, current_workspace_ref) != (owner_ref, workspace_ref):
            raise AuthenticationError("ONBOARDING_DRAFT_IDENTITY_CHANGED", 403)
        _draft_cas_lock(workspace, connection, draft_ref)
        base = _load_draft(
            workspace,
            draft_id=command.draft_id,
            revision=command.base_revision,
            receipt_digest=command.base_receipt_digest,
            workspace_ref=workspace_ref,
            owner_ref=owner_ref,
        )
        _read_private_files(workspace, draft=base, actor_id=actor_id)
        artifact_id = _draft_artifact_id(draft_ref, next_revision)
        if workspace.store.artifact_exists(artifact_id, connection=connection):
            raise EnterpriseOnboardingDraftError("ONBOARDING_DRAFT_REVISION_CONFLICT")
        workspace.private_records.write(
            connection,
            record_id=record_ref,
            scope_ref=draft_ref,
            owner_id=actor_id,
            payload={"files": command.files},
        )
        retained = workspace.private_records.retention_seconds > 0
        result = _receipt(
            {
                "schema_version": "orgrebase.enterprise-onboarding-draft-receipt.v1",
                "operation": operation,
                "operation_ref": operation_ref,
                "request_digest": request_digest,
                "status": "DRAFT_UPDATED" if retained else "DRAFT_INPUT_NOT_RETAINED",
                "workspace_ref": workspace_ref,
                "owner_ref": owner_ref,
                "draft_ref": draft_ref,
                "revision": next_revision,
                "content_digest": content_digest,
                "component_digests": _component_digests(command.files),
                "parent_receipt_digest": command.base_receipt_digest,
                "private_record_ref": record_ref,
                "private_input_status_at_commit": "AVAILABLE" if retained else "DELETED",
                "profile_admitted_for_workspace": False,
                "workspace_activated": False,
                "canonical_target_writes": 0,
            }
        )
        workspace.store.save_artifact(
            connection,
            artifact_id,
            ONBOARDING_DRAFT_MEDIA_TYPE,
            result,
        )
        _save_operation(
            workspace,
            connection,
            operation_ref=operation_ref,
            request_digest=request_digest,
            result=result,
        )
    return _public_result(workspace, result)


def _validate_operation(
    workspace: Any,
    command: DraftRevisionCommand,
    *,
    operation: Literal["PREFLIGHT", "SEAL"],
) -> tuple[
    str,
    str,
    str,
    str,
    dict[str, Any],
    dict[str, dict[str, JsonValue]],
]:
    actor_id, owner_ref, workspace_ref = _identity(workspace)
    operation_ref = _operation_ref(workspace_ref, operation, command.operation_key)
    draft = _load_draft(
        workspace,
        draft_id=command.draft_id,
        revision=command.revision,
        receipt_digest=command.draft_receipt_digest,
        workspace_ref=workspace_ref,
        owner_ref=owner_ref,
    )
    files = _read_private_files(workspace, draft=draft, actor_id=actor_id)
    return actor_id, owner_ref, workspace_ref, operation_ref, draft, files


def preflight_draft(
    workspace: Any,
    command: PreflightOnboardingDraft,
) -> dict[str, Any]:
    command = PreflightOnboardingDraft.model_validate(command.model_dump(mode="json"))
    _, owner_ref, workspace_ref = _identity(workspace)
    operation = "PREFLIGHT"
    operation_ref = _operation_ref(workspace_ref, operation, command.operation_key)
    request_digest = _request_digest(
        operation=operation,
        command=command,
        workspace_ref=workspace_ref,
        owner_ref=owner_ref,
    )
    existing = _existing_operation(
        workspace,
        operation_ref=operation_ref,
        request_digest=request_digest,
    )
    if existing is not None:
        _validate_result_binding(existing, workspace_ref=workspace_ref, owner_ref=owner_ref)
        return _public_result(workspace, existing)
    _, _, _, _, draft, files = _validate_operation(
        workspace,
        command,
        operation=operation,
    )
    source_content_digest = draft["content_digest"]
    try:
        validation = _safe_preflight(preflight_enterprise_quote_pilot_payloads(files))
        status = "DRAFT_PREFLIGHT_PASSED"
        reason_code = None
    except EnterpriseQuotePilotAuthoringError as exc:
        validation = None
        status = "DRAFT_PREFLIGHT_REJECTED"
        reason_code = exc.code

    with workspace._command_lock, workspace.store.transaction() as connection:
        existing = _existing_operation(
            workspace,
            operation_ref=operation_ref,
            request_digest=request_digest,
            connection=connection,
        )
        if existing is not None:
            return _public_result(workspace, existing)
        current_actor, current_owner_ref, current_workspace_ref = _identity(workspace)
        if (current_owner_ref, current_workspace_ref) != (owner_ref, workspace_ref):
            raise AuthenticationError("ONBOARDING_DRAFT_IDENTITY_CHANGED", 403)
        draft = _load_draft(
            workspace,
            draft_id=command.draft_id,
            revision=command.revision,
            receipt_digest=command.draft_receipt_digest,
            workspace_ref=workspace_ref,
            owner_ref=owner_ref,
        )
        committed_files = _read_private_files(
            workspace,
            draft=draft,
            actor_id=current_actor,
        )
        if (
            draft["content_digest"] != source_content_digest
            or sha256_digest(committed_files) != source_content_digest
        ):
            raise EnterpriseOnboardingDraftError("ONBOARDING_DRAFT_CHANGED_DURING_VALIDATION")
        result = _receipt(
            {
                "schema_version": "orgrebase.enterprise-onboarding-operation-receipt.v1",
                "operation": operation,
                "operation_ref": operation_ref,
                "request_digest": request_digest,
                "status": status,
                "reason_code": reason_code,
                "workspace_ref": workspace_ref,
                "owner_ref": owner_ref,
                "draft_ref": draft["draft_ref"],
                "revision": command.revision,
                "content_digest": draft["content_digest"],
                "draft_receipt_digest": command.draft_receipt_digest,
                "private_record_ref": draft["private_record_ref"],
                "validation": validation,
                "profile_admitted_for_workspace": False,
                "workspace_activated": False,
                "canonical_target_writes": 0,
            }
        )
        _save_operation(
            workspace,
            connection,
            operation_ref=operation_ref,
            request_digest=request_digest,
            result=result,
        )
    return _public_result(workspace, result)


def seal_draft(workspace: Any, command: SealOnboardingDraft) -> dict[str, Any]:
    command = SealOnboardingDraft.model_validate(command.model_dump(mode="json"))
    _, owner_ref, workspace_ref = _identity(workspace)
    operation = "SEAL"
    operation_ref = _operation_ref(workspace_ref, operation, command.operation_key)
    request_digest = _request_digest(
        operation=operation,
        command=command,
        workspace_ref=workspace_ref,
        owner_ref=owner_ref,
    )
    existing = _existing_operation(
        workspace,
        operation_ref=operation_ref,
        request_digest=request_digest,
    )
    if existing is not None:
        _validate_result_binding(existing, workspace_ref=workspace_ref, owner_ref=owner_ref)
        return _public_result(workspace, existing)
    _, _, _, _, draft, files = _validate_operation(
        workspace,
        command,
        operation=operation,
    )
    source_content_digest = draft["content_digest"]
    try:
        sealed_files, seal_receipt = seal_enterprise_quote_pilot_payloads(files)
        sealed_file_texts = {
            path: raw.decode("utf-8")
            for path, raw in sealed_enterprise_quote_pilot_bytes(
                sealed_files, expected_pack_digest=seal_receipt["pack_digest"],
            ).items()
        }
        validation = {
            "status": seal_receipt["status"],
            "pack_digest": seal_receipt["pack_digest"],
            "profile_digest": seal_receipt["profile_digest"],
            "source_admission_receipt_digest": seal_receipt[
                "source_admission_receipt_digest"
            ],
            "runtime_projection_receipt_digest": seal_receipt[
                "runtime_projection_receipt_digest"
            ],
            "universe_digest": seal_receipt["universe_digest"],
            "profile_admitted_for_workspace": False,
            "workspace_activated": False,
            "canonical_target_writes": 0,
        }
        status = "DRAFT_SEALED"
        reason_code = None
    except EnterpriseQuotePilotAuthoringError as exc:
        sealed_files = None
        validation = None
        status = "DRAFT_SEAL_REJECTED"
        reason_code = exc.code

    draft_ref = draft["draft_ref"]
    next_revision = command.revision + 1
    if next_revision > MAX_DRAFT_REVISION:
        raise EnterpriseOnboardingDraftError("ONBOARDING_DRAFT_REVISION_LIMIT")
    sealed_digest = sha256_digest(sealed_files) if sealed_files is not None else None
    record_ref = (
        _private_record_ref(
            draft_ref=draft_ref,
            revision=next_revision,
            content_digest=sealed_digest,
        )
        if sealed_digest is not None
        else None
    )
    with workspace._command_lock, workspace.store.transaction() as connection:
        existing = _existing_operation(
            workspace,
            operation_ref=operation_ref,
            request_digest=request_digest,
            connection=connection,
        )
        if existing is not None:
            return _public_result(workspace, existing)
        current_actor, current_owner_ref, current_workspace_ref = _identity(workspace)
        if (current_owner_ref, current_workspace_ref) != (owner_ref, workspace_ref):
            raise AuthenticationError("ONBOARDING_DRAFT_IDENTITY_CHANGED", 403)
        _draft_cas_lock(workspace, connection, draft_ref)
        draft = _load_draft(
            workspace,
            draft_id=command.draft_id,
            revision=command.revision,
            receipt_digest=command.draft_receipt_digest,
            workspace_ref=workspace_ref,
            owner_ref=owner_ref,
        )
        committed_files = _read_private_files(
            workspace,
            draft=draft,
            actor_id=current_actor,
        )
        if (
            draft["content_digest"] != source_content_digest
            or sha256_digest(committed_files) != source_content_digest
        ):
            raise EnterpriseOnboardingDraftError("ONBOARDING_DRAFT_CHANGED_DURING_SEAL")
        if sealed_files is not None:
            artifact_id = _draft_artifact_id(draft_ref, next_revision)
            if workspace.store.artifact_exists(artifact_id, connection=connection):
                raise EnterpriseOnboardingDraftError("ONBOARDING_DRAFT_REVISION_CONFLICT")
            workspace.private_records.write(
                connection,
                record_id=record_ref,
                scope_ref=draft_ref,
                owner_id=current_actor,
                # JSON canonicalization may reorder object keys. Source roots
                # bind exact bytes, so retain their UTF-8 text, not a JSON value
                # that would need to be serialized again at export time.
                payload={"sealed_file_texts": sealed_file_texts},
            )
        retained = sealed_files is not None and workspace.private_records.retention_seconds > 0
        result = _receipt(
            {
                "schema_version": "orgrebase.enterprise-onboarding-operation-receipt.v1",
                "operation": operation,
                "operation_ref": operation_ref,
                "request_digest": request_digest,
                "status": status if retained or sealed_files is None else "DRAFT_INPUT_NOT_RETAINED",
                "reason_code": reason_code,
                "workspace_ref": workspace_ref,
                "owner_ref": owner_ref,
                "draft_ref": draft_ref,
                "revision": next_revision if sealed_files is not None else command.revision,
                "content_digest": sealed_digest or draft["content_digest"],
                "component_digests": (
                    _component_digests(sealed_files) if sealed_files is not None else None
                ),
                "parent_receipt_digest": command.draft_receipt_digest,
                "private_record_ref": record_ref or draft["private_record_ref"],
                "private_input_status_at_commit": (
                    "AVAILABLE" if retained else "DELETED" if sealed_files is not None else "AVAILABLE"
                ),
                "validation": validation,
                "profile_admitted_for_workspace": False,
                "workspace_activated": False,
                "canonical_target_writes": 0,
            }
        )
        if sealed_files is not None:
            workspace.store.save_artifact(
                connection,
                _draft_artifact_id(draft_ref, next_revision),
                ONBOARDING_DRAFT_MEDIA_TYPE,
                result,
            )
        _save_operation(
            workspace,
            connection,
            operation_ref=operation_ref,
            request_digest=request_digest,
            result=result,
        )
    return _public_result(workspace, result)


def read_operation(
    workspace: Any,
    *,
    operation: Literal["CREATE", "UPDATE", "PREFLIGHT", "SEAL"],
    operation_key: str,
) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", operation_key):
        raise EnterpriseOnboardingDraftError("ONBOARDING_OPERATION_KEY_INVALID")
    _, owner_ref, workspace_ref = _identity(workspace)
    operation_ref = _operation_ref(workspace_ref, operation, operation_key)
    artifact = workspace.store.load_artifact(
        _operation_artifact_id(operation_ref),
        ONBOARDING_OPERATION_MEDIA_TYPE,
    )
    result = artifact.payload
    _validate_result_binding(result, workspace_ref=workspace_ref, owner_ref=owner_ref)
    if result.get("operation_ref") != operation_ref or result.get("operation") != operation:
        raise IntegrityError("ONBOARDING_OPERATION_BINDING_MISMATCH")
    return _public_result(workspace, result)


def read_draft(
    workspace: Any,
    *,
    draft_id: str,
    revision: int,
    receipt_digest: str,
) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z][a-z0-9._-]{2,63}", draft_id):
        raise EnterpriseOnboardingDraftError("ONBOARDING_DRAFT_ID_INVALID")
    if not 1 <= revision <= MAX_DRAFT_REVISION or not _DIGEST.fullmatch(receipt_digest):
        raise EnterpriseOnboardingDraftError("ONBOARDING_DRAFT_REVISION_INVALID")
    actor_id, owner_ref, workspace_ref = _identity(workspace)
    draft = _load_draft(
        workspace,
        draft_id=draft_id,
        revision=revision,
        receipt_digest=receipt_digest,
        workspace_ref=workspace_ref,
        owner_ref=owner_ref,
    )
    files = _read_private_files(workspace, draft=draft, actor_id=actor_id)
    return {
        "schema_version": "orgrebase.enterprise-onboarding-private-draft-view.v1",
        "draft_ref": draft["draft_ref"],
        "revision": revision,
        "receipt_digest": receipt_digest,
        "content_digest": draft["content_digest"],
        "private_input_status": "AVAILABLE",
        "files": files,
        "cacheable": False,
    }


def export_sealed_draft(
    workspace: Any, *, draft_id: str, revision: int, receipt_digest: str,
) -> bytes:
    """Return a deterministic exact-byte archive to the currently authorized author.

    No archive, raw input, or extra receipt is written to disk or canonical
    storage. Admission and activation remain independent operator actions.
    """

    view = read_draft(
        workspace, draft_id=draft_id, revision=revision, receipt_digest=receipt_digest,
    )
    _, owner_ref, workspace_ref = _identity(workspace)
    draft = _load_draft(
        workspace, draft_id=draft_id, revision=revision, receipt_digest=receipt_digest,
        workspace_ref=workspace_ref, owner_ref=owner_ref,
    )
    validation = draft.get("validation") or {}
    if draft.get("status") != "DRAFT_SEALED" or not validation.get("pack_digest"):
        raise EnterpriseOnboardingDraftError("ONBOARDING_DRAFT_SEAL_REQUIRED")
    actor_id, _, _ = _identity(workspace)
    private = _read_private_payload(workspace, draft=draft, actor_id=actor_id)
    payloads = sealed_enterprise_quote_pilot_bytes(
        view["files"], expected_pack_digest=validation["pack_digest"],
        exact_texts=private.get("sealed_file_texts"),
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative, raw in sorted(payloads.items()):
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, raw)
    if _identity(workspace)[1:] != (owner_ref, workspace_ref):
        raise AuthenticationError("ONBOARDING_DRAFT_IDENTITY_CHANGED", 403)
    return buffer.getvalue()
