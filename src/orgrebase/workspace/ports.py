"""Minimal protocol seams for replaceable Workspace adapters."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol, TypeVar

from pydantic import BaseModel, JsonValue

from orgrebase.domain import RunEnvelope, VersionedObject
from orgrebase.workspace.models import (
    ActorContextProjection,
    BenchmarkCase,
    BenchmarkGold,
    CaseRunReceipt,
    CoalitionPlan,
    DomainDelegationTask,
    DomainReadProjection,
    DomainReadRequest,
    DomainTransportCandidate,
    DomainTransportReceipt,
    EvidenceIndex,
    ModelRequest,
    ModelResponseReceipt,
    StoredArtifact,
    TaskTemplateCatalogSummary,
    TaskTemplateVersion,
    UserValidationSummary,
    UserWalkthroughRecord,
    WorkspaceGraphSnapshot,
)

T = TypeVar("T", bound=BaseModel)


class ModelProvider(Protocol):
    def generate_structured(self, *, request: ModelRequest, output_model: type[T]) -> ModelResponseReceipt: ...


class DomainReadPort(Protocol):
    def read_projection(self, *, request: DomainReadRequest) -> DomainReadProjection: ...


class PolicyDecisionPort(Protocol):
    def decide(self, *, actor_id: str, purpose: str, resource_ref: str) -> tuple[bool, str]: ...


class ArtifactStorePort(Protocol):
    def load_verified(self, artifact_id: str, expected_media_type: str | None = None) -> StoredArtifact: ...

    def save_in_transaction(
        self,
        connection: object,
        artifact_id: str,
        media_type: str,
        payload: Mapping[str, JsonValue],
    ) -> str: ...


class GraphSnapshotPort(Protocol):
    def load_current(self, organization_id: str) -> WorkspaceGraphSnapshot: ...

    def validate(self, snapshot: WorkspaceGraphSnapshot) -> None: ...

    def persist(self, snapshot: WorkspaceGraphSnapshot) -> str: ...


class AgentTransportPort(Protocol):
    def execute_delegations(
        self,
        *,
        plan: CoalitionPlan,
        delegations: tuple[DomainDelegationTask, ...],
        run_envelope: RunEnvelope,
    ) -> tuple[tuple[DomainTransportCandidate, ...], DomainTransportReceipt]: ...


class RendererPort(Protocol):
    def render(self, *, values: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]: ...


class EvidenceExporterPort(Protocol):
    def export(self, *, run_id: str, output_dir: str) -> EvidenceIndex: ...


class BenchmarkRepository(Protocol):
    def cases(self, partition: str | None = None) -> tuple[BenchmarkCase, ...]: ...

    def gold(self, case_id: str, *, evaluator_token: object) -> BenchmarkGold: ...


class SystemUnderEvaluation(Protocol):
    def run_case(self, case: BenchmarkCase, *, mode: str) -> CaseRunReceipt: ...


class Clock(Protocol):
    def now(self) -> str: ...


class IdFactory(Protocol):
    def new_id(self, prefix: str) -> str: ...


class TemplateCatalogPort(Protocol):
    def list_templates(self) -> TaskTemplateCatalogSummary: ...

    def describe(self, template_ref: str) -> TaskTemplateVersion: ...


class EvidenceInspectPort(Protocol):
    def inspect_redacted(self, refs: tuple[str, ...]) -> tuple[dict[str, str], ...]: ...


class UserValidationRepository(Protocol):
    def record_session(self, record: UserWalkthroughRecord) -> None: ...

    def summarize(self) -> UserValidationSummary: ...


ArtifactReader = Callable[[str, str | None], StoredArtifact]
ObjectReader = Callable[[str, str | None], VersionedObject]
