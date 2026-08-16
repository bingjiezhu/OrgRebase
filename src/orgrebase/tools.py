"""Audited read-only tool boundary used by agents and the HTTP API."""

from __future__ import annotations

from typing import Any

from orgrebase.digest import sha256_digest
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


class DependencyEvidenceTool:
    """A real local tool call with authorization, idempotency, and an audit receipt."""

    allowed_actors = frozenset(DEPENDENCY_EVIDENCE_CONTRACT.permissions["allowed_actors"])

    def __init__(self, fixture: EnterpriseFixture, store: StateStore) -> None:
        self.fixture = fixture
        self.store = store

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
        existing = self.store.get_idempotent(storage_key, request_digest)
        if existing is not None:
            return existing

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
        with self.store.transaction() as connection:
            event_digest = self.store.append_event(
                connection,
                "TOOL_INVOKED",
                {
                    "tool_ref": f"{DEPENDENCY_EVIDENCE_CONTRACT.id}@{DEPENDENCY_EVIDENCE_CONTRACT.version}",
                    "actor_id": actor_id,
                    "workflow_run_id": workflow_run_id,
                    "run_nonce": run_nonce,
                    "request_digest": request_digest,
                    "result_digest": result_digest,
                    "status": "SUCCEEDED",
                },
            )
            receipt = ToolInvocationReceipt(
                id=f"tool-call:{actor_id}:{idempotency_key}",
                tool_ref=f"{DEPENDENCY_EVIDENCE_CONTRACT.id}@{DEPENDENCY_EVIDENCE_CONTRACT.version}",
                actor_id=actor_id,
                workflow_run_id=workflow_run_id,
                run_nonce=run_nonce,
                request_digest=request_digest,
                result_digest=result_digest,
                status="SUCCEEDED",
                started_at=started_at,
                completed_at=completed_at,
                audit_event_digest=event_digest,
                evidence_class=evidence_class,
            )
            payload = {
                "contract": DEPENDENCY_EVIDENCE_CONTRACT.model_dump(mode="json"),
                "result": result,
                "receipt": receipt.model_dump(mode="json"),
            }
            self.store.save_artifact(
                connection,
                receipt.id,
                "application/vnd.orgrebase.tool-invocation+json",
                payload,
            )
            self.store.save_idempotent(connection, storage_key, request_digest, payload)
        return payload
