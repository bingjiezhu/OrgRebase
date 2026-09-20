"""Audited read-only tool boundary used by agents and the HTTP API."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.domain import (
    AuthorizationError,
    EvidenceClass,
    ToolContract,
    ToolInvocationReceipt,
)
from orgrebase.fixture import EnterpriseFixture
from orgrebase.store import StateStore

DEPENDENCY_EVIDENCE_CONTRACT = ToolContract(
    id="tool:dependency-evidence",
    name="orgrebase.read_dependency_evidence",
    version="1.0.0",
    purpose="Return admitted, purpose-safe dependency evidence for an exact graph revision.",
    endpoint="/api/tools/v1/dependency-evidence",
    method="POST",
    auth={
        "scheme": "X-OrgRebase-Actor",
        "production_replacement": "short-lived workload identity",
    },
    input_schema={
        "type": "object",
        "required": ["target_ids", "graph_revision", "idempotency_key"],
        "properties": {
            "target_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
            "graph_revision": {"type": "string"},
            "idempotency_key": {"type": "string", "minLength": 8},
        },
        "additionalProperties": False,
    },
    output_schema={
        "type": "object",
        "required": ["contract", "result", "receipt"],
        "properties": {
            "contract": {
                "type": "object",
                "required": ["id", "name", "version", "input_schema", "output_schema"],
            },
            "result": {
                "type": "object",
                "required": ["graph_revision", "edges", "coverage"],
                "properties": {
                    "graph_revision": {"type": "string"},
                    "edges": {"type": "array", "items": {"type": "object"}},
                    "coverage": {"type": "array", "items": {"type": "object"}},
                },
                "additionalProperties": False,
            },
            "receipt": {
                "type": "object",
                "required": [
                    "id",
                    "tool_ref",
                    "actor_id",
                    "workflow_run_id",
                    "run_nonce",
                    "request_digest",
                    "result_digest",
                    "status",
                    "audit_event_digest",
                    "evidence_class",
                ],
            },
        },
        "additionalProperties": False,
    },
    error_codes=("AUTHZ_DENIED", "GRAPH_REVISION_MISMATCH", "INVALID_TARGET", "IDEMPOTENCY_CONFLICT"),
    permissions={
        "effect": "read",
        "allowed_actors": ["change-coordinator", "gtm-steward"],
        "sensitivity_ceiling": "INTERNAL",
        "canonical_state_write": False,
    },
    retry_policy={
        "safe": True,
        "max_attempts": 3,
        "backoff": "exponential-jitter",
        "retryable": ["TRANSPORT_UNAVAILABLE"],
    },
    idempotency={"required": True, "scope": "actor+request", "conflict": "fail-closed"},
    audit_fields=(
        "tool_ref",
        "actor_id",
        "request_digest",
        "result_digest",
        "status",
        "started_at",
        "completed_at",
    ),
    degradation={
        "mode": "UNKNOWN",
        "rule": "Tool failure may reduce certainty; it can never be converted into UNAFFECTED.",
    },
    mcp_migration={
        "transport_change": "Expose the same JSON Schemas as one read-only MCP tool.",
        "business_logic_change": False,
        "estimated_adapter_surface": "one transport adapter",
    },
)


@dataclass(frozen=True)
class PreparedDependencyEvidenceInvocation:
    """Side-effect-free invocation plan committed by the selected transaction owner."""

    actor_id: str
    workflow_run_id: str
    run_nonce: str
    idempotency_key: str
    storage_key: str
    request: dict[str, Any]
    request_digest: str
    result: dict[str, Any]
    result_digest: str
    started_at: str
    completed_at: str
    evidence_class: EvidenceClass


class DependencyEvidenceTool:
    """A real local tool call with authorization, idempotency, and an audit receipt."""

    allowed_actors = frozenset(DEPENDENCY_EVIDENCE_CONTRACT.permissions["allowed_actors"])

    def __init__(self, fixture: EnterpriseFixture, store: StateStore) -> None:
        self.fixture = fixture
        self.store = store

    def prepare(
        self,
        *,
        actor_id: str,
        workflow_run_id: str,
        run_nonce: str,
        target_ids: tuple[str, ...],
        graph_revision: str,
        idempotency_key: str,
        started_at: str = "2026-08-14T00:01:00Z",
        completed_at: str = "2026-08-14T00:01:01Z",
        evidence_class: EvidenceClass = EvidenceClass.LOCAL_DETERMINISTIC,
    ) -> PreparedDependencyEvidenceInvocation:
        """Validate and compute the read-only result without writing store state."""

        if actor_id not in self.allowed_actors:
            raise AuthorizationError(f"actor {actor_id} cannot invoke dependency evidence tool")
        if graph_revision != self.fixture.revisions["graph"]:
            raise RuntimeError("GRAPH_REVISION_MISMATCH")
        known_targets = set(self.fixture.impact_targets)
        invalid = sorted(set(target_ids) - known_targets)
        if invalid or not target_ids or len(target_ids) > 20:
            raise ValueError(f"INVALID_TARGET: {', '.join(invalid) if invalid else 'empty/oversized'}")

        request = {
            "actor_id": actor_id,
            "workflow_run_id": workflow_run_id,
            "run_nonce": run_nonce,
            "target_ids": sorted(set(target_ids)),
            "graph_revision": graph_revision,
        }
        request_digest = sha256_digest(request)
        storage_key = f"tool:{actor_id}:{idempotency_key}"
        edges = [
            {
                "edge_id": edge.id,
                "source_id": edge.source_id,
                "target_id": edge.target_id,
                "relation": edge.relation,
                "strength": edge.strength.value,
                "coverage_basis": edge.coverage_basis.value,
                "status": edge.status.value,
                "provenance_refs": list(edge.provenance_refs),
                "edge_digest": edge.digest,
            }
            for edge in sorted(self.fixture.dependencies, key=lambda item: item.id)
            if edge.target_id in target_ids and edge.status.value == "ADMITTED"
        ]
        coverage = [
            {
                "object_id": target_id,
                "dependency_manifest": self.fixture.dependency_manifest(
                    target_id
                ).model_dump(mode="json"),
            }
            for target_id in sorted(set(target_ids))
        ]
        result = {
            "graph_revision": graph_revision,
            "edges": edges,
            "coverage": coverage,
        }
        result_digest = sha256_digest(result)
        return PreparedDependencyEvidenceInvocation(
            actor_id=actor_id,
            workflow_run_id=workflow_run_id,
            run_nonce=run_nonce,
            idempotency_key=idempotency_key,
            storage_key=storage_key,
            request=json.loads(canonical_json(request)),
            request_digest=request_digest,
            result=json.loads(canonical_json(result)),
            result_digest=result_digest,
            started_at=started_at,
            completed_at=completed_at,
            evidence_class=evidence_class,
        )

    def commit(
        self,
        prepared: PreparedDependencyEvidenceInvocation,
        *,
        connection: sqlite3.Connection,
    ) -> dict[str, Any]:
        """Commit one prepared invocation inside a caller-owned transaction."""

        request_binding = (
            prepared.request.get("actor_id") == prepared.actor_id
            and prepared.request.get("workflow_run_id") == prepared.workflow_run_id
            and prepared.request.get("run_nonce") == prepared.run_nonce
            and prepared.storage_key
            == f"tool:{prepared.actor_id}:{prepared.idempotency_key}"
            and prepared.actor_id in self.allowed_actors
            and prepared.result.get("graph_revision")
            == prepared.request.get("graph_revision")
        )
        if not request_binding:
            raise ValueError("PREPARED_TOOL_BINDING_MISMATCH")
        if sha256_digest(prepared.request) != prepared.request_digest:
            raise ValueError("PREPARED_TOOL_REQUEST_DIGEST_MISMATCH")
        if sha256_digest(prepared.result) != prepared.result_digest:
            raise ValueError("PREPARED_TOOL_RESULT_DIGEST_MISMATCH")
        existing = self.store.get_idempotent(
            prepared.storage_key,
            prepared.request_digest,
            connection=connection,
        )
        if existing is not None:
            receipt = ToolInvocationReceipt.model_validate(existing.get("receipt"))
            if (
                receipt.request_digest != prepared.request_digest
                or receipt.result_digest != prepared.result_digest
                or sha256_digest(existing.get("result")) != prepared.result_digest
            ):
                raise RuntimeError("TOOL_IDEMPOTENT_RESULT_BINDING_INVALID")
            return existing
        tool_ref = (
            f"{DEPENDENCY_EVIDENCE_CONTRACT.id}@{DEPENDENCY_EVIDENCE_CONTRACT.version}"
        )
        event_digest = self.store.append_event(
            connection,
            "TOOL_INVOKED",
            {
                "tool_ref": tool_ref,
                "actor_id": prepared.actor_id,
                "workflow_run_id": prepared.workflow_run_id,
                "run_nonce": prepared.run_nonce,
                "request_digest": prepared.request_digest,
                "result_digest": prepared.result_digest,
                "status": "SUCCEEDED",
            },
        )
        receipt = ToolInvocationReceipt(
            id=(
                f"tool-call:{prepared.actor_id}:{prepared.idempotency_key}"
            ),
            tool_ref=tool_ref,
            actor_id=prepared.actor_id,
            workflow_run_id=prepared.workflow_run_id,
            run_nonce=prepared.run_nonce,
            request_digest=prepared.request_digest,
            result_digest=prepared.result_digest,
            status="SUCCEEDED",
            started_at=prepared.started_at,
            completed_at=prepared.completed_at,
            audit_event_digest=event_digest,
            evidence_class=prepared.evidence_class,
        )
        payload = {
            "contract": DEPENDENCY_EVIDENCE_CONTRACT.model_dump(mode="json"),
            "result": prepared.result,
            "receipt": receipt.model_dump(mode="json"),
        }
        self.store.save_artifact(
            connection,
            receipt.id,
            "application/vnd.orgrebase.tool-invocation+json",
            payload,
        )
        self.store.save_idempotent(
            connection,
            prepared.storage_key,
            prepared.request_digest,
            payload,
        )
        return payload

    def invoke(
        self,
        *,
        actor_id: str,
        workflow_run_id: str,
        run_nonce: str,
        target_ids: tuple[str, ...],
        graph_revision: str,
        idempotency_key: str,
        started_at: str = "2026-08-14T00:01:00Z",
        completed_at: str = "2026-08-14T00:01:01Z",
        evidence_class: EvidenceClass = EvidenceClass.LOCAL_DETERMINISTIC,
    ) -> dict[str, Any]:
        """Prepare and atomically commit one standalone tool invocation."""

        prepared = self.prepare(
            actor_id=actor_id,
            workflow_run_id=workflow_run_id,
            run_nonce=run_nonce,
            target_ids=target_ids,
            graph_revision=graph_revision,
            idempotency_key=idempotency_key,
            started_at=started_at,
            completed_at=completed_at,
            evidence_class=evidence_class,
        )
        with self.store.transaction() as connection:
            return self.commit(prepared, connection=connection)
