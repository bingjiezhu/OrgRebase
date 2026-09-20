"""Explicit compatibility adapter for the two synthetic legacy deliverables.

This is not a business renderer. Its deliberately closed input contract keeps
historical local-demo receipts reproducible without making untyped enterprise
objects eligible for a generic payload-copy fallback in the core workflow.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

from orgrebase.domain import (
    ChangeSetRevision,
    ContextManifest,
    IntegrityError,
    ObjectDelta,
    SemanticClassification,
    VersionedObject,
)


class LegacyPremiseRebuildHandler:
    _PAYLOADS: ClassVar[dict[str, dict[str, str]]] = {
        "work:sales_quote_a": {"owner": "sales-owner", "deliverable": "quote-a"},
        "work:support_doc_b": {"owner": "support-owner", "deliverable": "support-doc-b"},
    }

    def __init__(self, change_set: ChangeSetRevision) -> None:
        self.change_set = change_set

    def _validate_inputs(
        self,
        *,
        old: VersionedObject,
        deltas: tuple[ObjectDelta, ...],
        context: ContextManifest,
    ) -> None:
        if len(deltas) != 1:
            raise IntegrityError("LEGACY_REBUILD_DELTA_NOT_ADMITTED")
        delta = deltas[0]
        if (
            old.id not in self._PAYLOADS
            or old.version != "v1"
            or old.kind != "WorkItemVersion"
            or old.domain != "gtm"
            or old.payload != self._PAYLOADS[old.id]
        ):
            raise IntegrityError(f"LEGACY_REBUILD_TARGET_NOT_ADMITTED:{old.ref}")
        if (
            len(self.change_set.deltas) != 1
            or delta.digest != self.change_set.deltas[0].digest
            or delta.object_id != "claim:product.launch_date"
            or delta.semantic_classification != SemanticClassification.SEMANTIC_DELTA
            or delta.changed_fields != ("canonical_value",)
        ):
            raise IntegrityError("LEGACY_REBUILD_DELTA_NOT_ADMITTED")
        premise_ref = f"{delta.object_id}@{delta.proposed_version}"
        if context.target_object_id != old.id or not any(
            item.object_ref == premise_ref
            and item.payload is not None
            and item.payload.get("canonical_value") == delta.proposed_value
            for item in context.included
        ):
            raise IntegrityError("LEGACY_REBUILD_CONTEXT_NOT_ADMITTED")

    def rebuild_payload(
        self,
        *,
        old: VersionedObject,
        deltas: tuple[ObjectDelta, ...],
        context: ContextManifest,
    ) -> Mapping[str, object]:
        self._validate_inputs(old=old, deltas=deltas, context=context)
        delta = deltas[0]
        return {
            **old.payload,
            "rebased_from": old.ref,
            "rebase_change_set": self.change_set.digest,
            "premise": f"{delta.object_id}@{delta.proposed_version}",
            "canonical_premise": delta.proposed_value,
            "context_manifest": context.digest,
        }

    def verify_payload(
        self,
        *,
        old: VersionedObject,
        new_payload: Mapping[str, object],
        deltas: tuple[ObjectDelta, ...],
        context: ContextManifest,
    ) -> None:
        self._validate_inputs(old=old, deltas=deltas, context=context)
        delta = deltas[0]
        # Validate independently of the producer, including unexpected fields.
        expected = {
            **self._PAYLOADS[old.id],
            "rebased_from": old.ref,
            "rebase_change_set": self.change_set.digest,
            "premise": f"{delta.object_id}@{delta.proposed_version}",
            "canonical_premise": delta.proposed_value,
            "context_manifest": context.digest,
        }
        if dict(new_payload) != expected:
            raise IntegrityError("LEGACY_REBUILD_PAYLOAD_MISMATCH")

    def verify_committed(
        self,
        *,
        old: VersionedObject,
        rebuilt: VersionedObject,
        deltas: tuple[ObjectDelta, ...],
        context: ContextManifest,
    ) -> None:
        self._validate_inputs(old=old, deltas=deltas, context=context)
        delta = deltas[0]
        if rebuilt.payload.get("canonical_premise") != delta.proposed_value:
            raise IntegrityError("REBUILD_DID_NOT_WRITE_ADMITTED_VALUE")
        if rebuilt.payload.get("context_manifest") != context.digest:
            raise IntegrityError("LEGACY_REBUILD_CONTEXT_BINDING_MISMATCH")
