"""Closed task, tool and oracle contracts for disposable outcome experiments."""

from __future__ import annotations

from typing import Any, Literal, Protocol, Self

from pydantic import Field, model_validator

from orgrebase.domain import ContentAddressedModel

System = Literal["fixed-team", "initiator-only", "graph-only", "oac"]
SYSTEMS: tuple[System, ...] = ("fixed-team", "initiator-only", "graph-only", "oac")
DIMENSIONS = (
    "task_goal",
    "forbidden_effects",
    "evidence_completion",
    "scope",
    "replayability",
    "unresolved_observations",
)


class LabBudget(ContentAddressedModel):
    tool_calls: int = Field(ge=1, le=1000, strict=True)
    write_calls: int = Field(ge=0, le=1000, strict=True)
    elapsed_seconds: float = Field(gt=0, le=3600, allow_inf_nan=False)


class LabToolRequest(ContentAddressedModel):
    tool: str = Field(min_length=1)
    arguments: dict[str, Any]


class LabToolGrant(ContentAddressedModel):
    request: LabToolRequest
    role: str = Field(min_length=1)
    obligation_ref: str = Field(min_length=1)
    work_unit_ref: str = Field(min_length=1)
    evidence_ref: str = Field(min_length=1)
    mutates_state: bool = Field(strict=True)


class OutcomeTaskMapping(ContentAddressedModel):
    schema_version: Literal["orgrebase.outcome-lab.mapping.v1"] = "orgrebase.outcome-lab.mapping.v1"
    upstream_repository: str = Field(min_length=1)
    upstream_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    task_id: str = Field(min_length=1)
    oac_profile: str = Field(min_length=1)
    task_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    environment_build_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    seed_root: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    snapshot_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    change_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    plan_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    subjects: dict[str, str] = Field(min_length=1)
    grants: tuple[LabToolGrant, ...] = Field(min_length=1)
    work_unit_predecessors: dict[str, tuple[str, ...]] = Field(min_length=1)
    system_roles: dict[System, tuple[str, ...]]
    writable_paths: tuple[str, ...]
    required_evidence: tuple[str, ...] = Field(min_length=1)
    oracle_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    unsupported: tuple[str, ...]
    budget: LabBudget

    @model_validator(mode="after")
    def closed_mapping(self) -> Self:
        if set(self.system_roles) != set(SYSTEMS):
            raise ValueError("LAB_MATCHED_SYSTEM_SET_REQUIRED")
        requests = [grant.request.digest for grant in self.grants]
        if len(requests) != len(set(requests)):
            raise ValueError("LAB_AMBIGUOUS_TOOL_GRANT")
        known_roles = {grant.role for grant in self.grants}
        if any(set(roles) - known_roles for roles in self.system_roles.values()):
            raise ValueError("LAB_UNMAPPED_ROLE")
        if any(not path.startswith("/") or path == "/" for path in self.writable_paths):
            raise ValueError("LAB_UNBOUNDED_WRITE_SCOPE")
        if set(self.required_evidence) - {grant.evidence_ref for grant in self.grants}:
            raise ValueError("LAB_UNMAPPED_EVIDENCE")
        if set(self.work_unit_predecessors) != {grant.work_unit_ref for grant in self.grants}:
            raise ValueError("LAB_UNMAPPED_WORK_UNIT")
        pending = {key: set(value) for key, value in self.work_unit_predecessors.items()}
        while pending:
            ready = {key for key, value in pending.items() if not value}
            if not ready:
                raise ValueError("LAB_INVALID_WORK_UNIT_ORDER")
            pending = {key: value - ready for key, value in pending.items() if key not in ready}
        return self


class OutcomeOracle(ContentAddressedModel):
    """Exact state predicates supplied by the evaluator, never by the acting plan."""

    schema_version: Literal["orgrebase.outcome-lab.oracle.v1"] = "orgrebase.outcome-lab.oracle.v1"
    authority: str = Field(min_length=1)
    policy_source_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    expected: dict[str, Any] = Field(min_length=1)

    @model_validator(mode="after")
    def explicit_paths(self) -> Self:
        if any(not path.startswith("/") for path in self.expected):
            raise ValueError("LAB_ORACLE_POINTER_REQUIRED")
        return self


class DisposableEnvironment(Protocol):
    """Controller-owned adapter; implementations must bound each tool invocation."""

    build_digest: str

    def reset(self) -> str: ...

    def snapshot(self) -> dict[str, Any]: ...

    def tool_mutates_state(self, tool: str) -> bool: ...

    def call(self, request: LabToolRequest, *, timeout: float) -> Any: ...

    def close(self) -> None: ...
