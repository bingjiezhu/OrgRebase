"""Server-owned workspace selection over the existing application factory."""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.types import ASGIApp, Receive, Scope, Send

from orgrebase.auth import AuthenticationError

_ID = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"


class WorkspaceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    workspace_id: str = Field(pattern=_ID)
    label: str = Field(min_length=1, max_length=120)
    enterprise_pack: str = Field(min_length=1, max_length=4096)
    allowed_subjects: list[str] = Field(max_length=10_000)
    effect_config: str | None = Field(default=None, min_length=1, max_length=4096)
    source_config: str | None = Field(default=None, min_length=1, max_length=4096)


class WorkspaceCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["orgrebase.workspace-catalog.v1"] = "orgrebase.workspace-catalog.v1"
    workspaces: list[WorkspaceEntry] = Field(min_length=1, max_length=100)

    def entry(self, workspace_id: str) -> WorkspaceEntry:
        for entry in self.workspaces:
            if entry.workspace_id == workspace_id:
                return entry
        raise AuthenticationError("AUTH_WORKSPACE_DENIED", 403)


def _unique_json(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def load_catalog(path: Path) -> WorkspaceCatalog:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o022 or metadata.st_size > 1_048_576:
                raise ValueError("catalog file")
            raw = stream.read(1_048_577)
        if len(raw) > 1_048_576:
            raise ValueError("catalog size")
        catalog = WorkspaceCatalog.model_validate(json.loads(raw, object_pairs_hook=_unique_json))
        identities = [entry.workspace_id for entry in catalog.workspaces]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate workspace")
        entries = []
        for entry in catalog.workspaces:
            subjects = entry.allowed_subjects
            if len(subjects) != len(set(subjects)) or any(not subject.strip() or subject != subject.strip() or subject == "*" for subject in subjects):
                raise ValueError("invalid subject")
            def absolute(value):
                selected = Path(value)
                return str((selected if selected.is_absolute() else path.parent / selected).resolve())
            entries.append(entry.model_copy(update={
                "enterprise_pack": absolute(entry.enterprise_pack),
                "effect_config": absolute(entry.effect_config) if entry.effect_config else None,
                "source_config": absolute(entry.source_config) if entry.source_config else None,
            }))
        return catalog.model_copy(update={"workspaces": entries})
    except (OSError, ValueError, TypeError, ValidationError):
        raise AuthenticationError("AUTH_WORKSPACE_CATALOG_UNAVAILABLE", 503) from None


class WorkspaceDispatcher:
    """Select an existing app; authentication remains inside that same app."""

    def __init__(self, app: ASGIApp, *, applications: dict[str, ASGIApp], default_workspace_id: str):
        self.app = app
        self.applications = applications
        self.default_workspace_id = default_workspace_id

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/workspace/"):
            await self.app(scope, receive, send)
            return
        values = [value for name, value in scope.get("headers", []) if name.lower() == b"x-orgrebase-workspace"]
        selected = values[0].decode("ascii", errors="replace") if values else self.default_workspace_id
        valid = len(values) <= 1 and re.fullmatch(_ID, selected) is not None
        destination = self.app if selected == self.default_workspace_id else self.applications.get(selected)
        scope = dict(scope)
        scope["orgrebase.workspace_selection_denied"] = not valid or destination is None
        # Unknown selections are authenticated by the default app before returning
        # the same denial as a configured workspace outside the subject's scope.
        await (destination or self.app)(scope, receive, send)
