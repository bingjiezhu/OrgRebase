"""Admission and immutable persistence of enterprise source changes."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import nullcontext
from datetime import datetime
from typing import Any

from sqlalchemy import and_, false, or_, select

from orgrebase.auth import current_authorization
from orgrebase.clock import utc_datetime
from orgrebase.database import artifacts, workspace_changes
from orgrebase.digest import canonical_json, sha256_digest
from orgrebase.domain import AuthorizationError, FreshnessError, IntegrityError, ObjectState
from orgrebase.event_projection import scope_view
from orgrebase.store import StateStore
from orgrebase.workspace.models import ChangeEvent, DomainPack, EnterpriseBinding
from orgrebase.workspace.pricing import PricingPolicy, QuoteBasket

CHANGE_EVENT_MEDIA_TYPE = "application/vnd.orgrebase.change-event+json"
CHANGE_EVENT_PREFIX = "workspace-change:"


class _Owners(Mapping[str, str]):
    def __init__(self, registry: ChangeRegistry) -> None:
        self.registry = registry

    def __getitem__(self, key: str) -> str:
        return self.registry.get(key).owner_id

    def __iter__(self) -> Iterator[str]:
        return (event.event_id for event in self.registry.all())

    def __len__(self) -> int:
        return self.registry.count


class ChangeRegistry:
    def __init__(
        self,
        store: StateStore,
        domain: DomainPack,
        enterprise: EnterpriseBinding,
        *,
        verify_approval_actor: Callable[[str, str], Any] | None = None,
        workspace: Any | None = None,
    ) -> None:
        self.store = store
        self.domain = domain
        self._initial_enterprise = enterprise
        self.workspace = workspace
        self.verify_approval_actor = verify_approval_actor
        self._owners = _Owners(self)
        self.refresh()

    @property
    def enterprise(self) -> EnterpriseBinding:
        if self.workspace is None:
            return self._initial_enterprise
        from orgrebase.workspace.enterprise_binding import current_binding
        return current_binding(self.workspace)

    @staticmethod
    def artifact_id(event_id: str) -> str:
        return f"{CHANGE_EVENT_PREFIX}{event_id}@r1"

    def _rows(self, *criteria: Any, limit: int | None = None, descending: bool = False):
        statement = (
            select(
                workspace_changes,
                artifacts.c.payload_json,
                artifacts.c.payload_digest,
                artifacts.c.media_type,
            )
            .select_from(
                workspace_changes.join(
                    artifacts,
                    and_(
                        workspace_changes.c.workspace_id == artifacts.c.workspace_id,
                        workspace_changes.c.artifact_id == artifacts.c.artifact_id,
                    ),
                )
            )
            .where(*criteria)
            .order_by(workspace_changes.c.ordinal.desc() if descending else workspace_changes.c.ordinal)
        )
        if limit is not None:
            statement = statement.limit(limit)
        with self.store.read_connection() as connection:
            return self.store.execute(connection, statement).fetchall()

    @staticmethod
    def _decode(row: Any) -> ChangeEvent:
        import json

        payload = json.loads(row["payload_json"])
        event = ChangeEvent.model_validate(payload)
        if (
            row["media_type"] != CHANGE_EVENT_MEDIA_TYPE
            or canonical_json(payload) != row["payload_json"]
            or sha256_digest(payload) != row["payload_digest"]
            or row["artifact_digest"] != row["payload_digest"]
            or event.event_id != row["event_id"]
            or event.digest != row["event_digest"]
        ):
            raise IntegrityError("CHANGE_EVENT_JOURNAL_BINDING_INVALID")
        return event

    def get(self, event_id: str) -> ChangeEvent:
        rows = self._rows(workspace_changes.c.event_id == event_id, limit=1)
        if not rows:
            raise KeyError(event_id)
        return self._decode(rows[0])

    def all(self) -> tuple[ChangeEvent, ...]:
        """Explicit export operation; ordinary views use a bounded page."""
        return tuple(self._decode(row) for row in self._rows())

    @property
    def owners(self) -> Mapping[str, str]:
        return self._owners

    @property
    def count(self) -> int:
        return self._checkpoint["change_count"]

    @property
    def pending_count(self) -> int:
        return self._checkpoint["pending_change_count"]

    def page(self, *, after: int = 0, limit: int = 50) -> dict[str, Any]:
        if (
            isinstance(after, bool)
            or not isinstance(after, int)
            or after < 0
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
        ):
            raise ValueError("WORKSPACE_CHANGE_PAGE_INVALID")
        rows = self._rows(workspace_changes.c.ordinal > after, limit=limit + 1)
        return {
            "items": tuple(self._decode(row) for row in rows[:limit]),
            "next_cursor": rows[limit - 1]["ordinal"] if len(rows) > limit else None,
            "total": self.count,
        }

    def pending_ids(self) -> tuple[str, ...]:
        with self.store.read_connection() as connection:
            rows = self.store.execute(
                connection,
                select(workspace_changes.c.event_id)
                .where(workspace_changes.c.resolution.is_(None))
                .order_by(workspace_changes.c.ordinal),
            ).fetchall()
        return tuple(row[0] for row in rows)

    def eligible_ids(
        self, *, now: datetime, snapshot_ref: str | None, snapshot_digest: str | None
    ) -> Iterator[str]:
        """Keyset candidates; live authority remains checked by the service."""
        sources = []
        for binding in self.enterprise.resources:
            current = self.store.get_object(binding.object_id)
            operation = (
                "READMIT" if current.state in {ObjectState.STALE, ObjectState.QUARANTINED} else "UPDATE"
            )
            if operation == "UPDATE" and current.state not in {ObjectState.CURRENT, ObjectState.ACTIVE}:
                continue
            sources.append(
                and_(
                    workspace_changes.c.object_id == current.id,
                    workspace_changes.c.base_version == current.version,
                    workspace_changes.c.base_digest == current.digest,
                    workspace_changes.c.operation == operation,
                )
            )
        epoch = utc_datetime(now).timestamp()
        ordinary = and_(
            or_(*sources) if sources else false(),
            workspace_changes.c.valid_from <= epoch,
            or_(workspace_changes.c.valid_to.is_(None), workspace_changes.c.valid_to > epoch),
            or_(
                workspace_changes.c.preview_expires_at.is_(None),
                and_(
                    workspace_changes.c.preview_expires_at > epoch,
                    workspace_changes.c.preview_snapshot_ref == snapshot_ref,
                    workspace_changes.c.preview_snapshot_digest == snapshot_digest,
                ),
            ),
            or_(
                workspace_changes.c.approval_expires_at.is_(None),
                workspace_changes.c.approval_expires_at > epoch,
            ),
        )
        committed = select(artifacts.c.artifact_id).where(
            artifacts.c.workspace_id == workspace_changes.c.workspace_id,
            artifacts.c.artifact_id == "rebase:workspace:workspace-" + workspace_changes.c.event_id + "@r1",
            artifacts.c.media_type == "application/vnd.orgrebase.rebase-receipt+json",
        ).exists()
        criteria = (workspace_changes.c.resolution.is_(None), or_(ordinary, committed))
        after = 0
        while True:
            with self.store.read_connection() as connection:
                rows = self.store.execute(
                    connection,
                    select(workspace_changes.c.event_id, workspace_changes.c.ordinal)
                    .where(*criteria, workspace_changes.c.ordinal > after)
                    .order_by(workspace_changes.c.ordinal)
                    .limit(50),
                ).fetchall()
            for row in rows:
                yield row["event_id"]
            if len(rows) < 50:
                return
            after = rows[-1]["ordinal"]

    def recent(self, limit: int = 50) -> tuple[ChangeEvent, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("WORKSPACE_CHANGE_PAGE_INVALID")
        return tuple(self._decode(row) for row in reversed(self._rows(limit=limit, descending=True)))

    def journal(
        self,
        event_type: str,
        *,
        subject_key: str | None = None,
        limit: int | None = None,
        descending: bool = False,
    ) -> tuple[dict[str, Any], ...]:
        result = []
        after = 0
        while True:
            page = self.store.event_page(
                after=after,
                limit=min(limit, 500) if limit is not None else 500,
                event_types=(event_type,),
                subject_key=subject_key,
                descending=descending,
            )
            result.extend(page["items"])
            if limit is not None or page["next_cursor"] is None:
                return tuple(result)
            after = page["next_cursor"]

    def journal_event(self, digest: str) -> dict[str, Any] | None:
        return self.store.event_by_digest(digest)

    def chain_summary(self, *, record_limit: int | None = 100) -> dict[str, Any]:
        if record_limit is None:
            return {
                **self.store.verify_event_chain(),
                "records": list(self.store.event_records()),
                "verification": "FULL_CHAIN_REPLAY",
                "record_limit": None,
            }
        summary = self.store.history_projection(record_limit=record_limit)
        return {
            key: value
            for key, value in summary.items()
            if key not in {"scope_projection", "change_count", "pending_change_count"}
        }

    def state_history(self) -> tuple[dict[str, Any], dict[str, Any]]:
        summary = self.store.history_projection()
        scopes = scope_view(
            summary.pop("scope_projection"), events=summary["events"], head_digest=summary["head_digest"]
        )
        self._checkpoint.update(
            change_count=summary.pop("change_count"), pending_change_count=summary.pop("pending_change_count")
        )
        return summary, scopes

    def refresh(self) -> None:
        """Refresh a fixed-size checkpoint; migration and audit replay are explicit."""
        self._checkpoint = self.store.get_workspace(self.store.workspace_id)

    def register(self, event: ChangeEvent, *, connection: Any | None = None,
                 _source_observation_admission: bool = False) -> ChangeEvent:
        event = ChangeEvent.model_validate(event.model_dump(mode="json"))
        value = event.proposal.payload.get("canonical_value")
        if value is None:
            raise IntegrityError("CHANGE_EVENT_NULL_OR_DELETE_UNSUPPORTED")
        if event.slot_id in {"quote_basket", "pricing_policy"}:
            if not isinstance(value, dict):
                raise IntegrityError("CHANGE_EVENT_STRUCTURED_VALUE_REQUIRED")
            try:
                raw = json.dumps(value, ensure_ascii=False, allow_nan=False)
                if len(raw.encode("utf-8")) > 65_536:
                    raise ValueError("CHANGE_EVENT_VALUE_TOO_LARGE")
                model = QuoteBasket if event.slot_id == "quote_basket" else PricingPolicy
                model.model_validate_json(raw)
            except (TypeError, ValueError) as exc:
                raise IntegrityError("CHANGE_EVENT_PRICING_VALUE_INVALID") from exc
        elif not isinstance(value, str) or not value.strip():
            raise IntegrityError("CHANGE_EVENT_VALUE_REQUIRED")
        if event.slot_id == "launch_date":
            try:
                parsed = datetime.strptime(value, "%Y-%m-%d")
            except ValueError as exc:
                raise IntegrityError("CHANGE_EVENT_DATE_INVALID") from exc
            if parsed.strftime("%Y-%m-%d") != value:
                raise IntegrityError("CHANGE_EVENT_DATE_INVALID")
        if event.slot_id == "currency" and (
            len(value) != 3 or not value.isascii() or not value.isupper() or not value.isalpha()
        ):
            raise IntegrityError("CHANGE_EVENT_CURRENCY_INVALID")
        if "change_rebase" not in event.proposal.allowed_purposes:
            raise IntegrityError("CHANGE_EVENT_PURPOSE_NOT_ALLOWED")
        owns_transaction = connection is None
        scope = self.store.transaction() if owns_transaction else nullcontext(connection)
        with scope as connection:
            if self.workspace is not None:
                from orgrebase.workspace.enterprise_binding import lock_binding_scope
                lock_binding_scope(self.workspace, connection)
            authorization = current_authorization()
            if authorization is not None:
                authorization()
            try:
                existing = self.get(event.event_id)
            except KeyError:
                existing = None
            if existing is not None:
                if existing.digest != event.digest:
                    raise IntegrityError("CHANGE_EVENT_ID_CONFLICT")
                return existing
            if event.organization_id != self.enterprise.organization_id:
                raise AuthorizationError("CHANGE_EVENT_ORGANIZATION_MISMATCH")
            binding = next((item for item in self.enterprise.resources if item.slot_id == event.slot_id), None)
            if binding is None or event.slot_id not in self.domain.mutable_slots:
                raise IntegrityError("CHANGE_EVENT_SLOT_UNSUPPORTED")
            if event.owner_id != binding.owner_id:
                raise AuthorizationError("CHANGE_EVENT_OWNER_MISMATCH")
            if event.proposal.id != binding.object_id or event.proposal.domain != binding.domain_id:
                raise IntegrityError("CHANGE_EVENT_RESOURCE_BINDING_MISMATCH")
            if self.domain.template_ref == "template:enterprise_quote@v2" and event.slot_id in {"currency", "quote_basket"}:
                by_slot = {item.slot_id: item for item in self.enterprise.resources}
                if "quote_basket" not in by_slot or "currency" not in by_slot:
                    raise IntegrityError("CHANGE_EVENT_PRICING_BINDING_MISSING")
                basket = (value if event.slot_id == "quote_basket" else self.store.get_object(
                    by_slot["quote_basket"].object_id).payload.get("canonical_value"))
                currency = (value if event.slot_id == "currency" else self.store.get_object(
                    by_slot["currency"].object_id).payload.get("canonical_value"))
                if not isinstance(basket, dict) or basket.get("currency") != currency:
                    raise IntegrityError("CHANGE_EVENT_PRICING_CURRENCY_MISMATCH")
            current = self.store.get_object(binding.object_id)
            if current.version != event.base_version or current.digest != event.base_digest:
                raise FreshnessError("CHANGE_EVENT_BASE_STALE")
            # This command changes a slot value, not its admitted source metadata.
            mutable_keys = {"canonical_value", "read_dependencies", "source_observation_ref"}
            metadata = {key: item for key, item in event.proposal.payload.items() if key not in mutable_keys}
            admitted_metadata = {key: item for key, item in current.payload.items() if key not in mutable_keys}
            if canonical_json(metadata) != canonical_json(admitted_metadata):
                raise IntegrityError("CHANGE_EVENT_PAYLOAD_METADATA_MISMATCH")
            if "read_dependencies" in event.proposal.payload:
                from orgrebase.workspace.read_dependencies import ReadDependencies
                try:
                    ReadDependencies.model_validate(event.proposal.payload["read_dependencies"])
                except ValueError as exc:
                    raise IntegrityError("CHANGE_EVENT_READ_DEPENDENCIES_INVALID") from exc
            observation_ref = event.proposal.payload.get("source_observation_ref")
            if "source_observation_ref" in event.proposal.payload:
                try:
                    # Only the synchronizer can attach this evidence. Raw
                    # client proposals cannot turn a stored ID into provenance.
                    if not _source_observation_admission or not isinstance(observation_ref, str) or not re.fullmatch(
                        r"source-observation:[0-9a-f]{64}", observation_ref
                    ):
                        raise ValueError("invalid observation reference")
                    observation = self.store.load_artifact(observation_ref, "application/json").payload
                    if (observation["value_digest"] != sha256_digest(value)
                            or observation["source_ref"] not in event.proposal.source_refs
                            or observation["page_ref"] not in event.proposal.source_refs
                            or observation["observed_at"] != event.proposal.valid_from):
                        raise ValueError("observation does not bind this proposal")
                except (KeyError, TypeError, ValueError) as exc:
                    raise IntegrityError("CHANGE_EVENT_SOURCE_OBSERVATION_INVALID") from exc
            expected_states = (
                {ObjectState.STALE, ObjectState.QUARANTINED}
                if event.operation == "READMIT"
                else {ObjectState.CURRENT, ObjectState.ACTIVE}
            )
            if current.state not in expected_states:
                raise FreshnessError("CHANGE_EVENT_SOURCE_STATE_MISMATCH")
            if current.kind != event.proposal.kind:
                raise IntegrityError("CHANGE_EVENT_OBJECT_KIND_MISMATCH")
            if current.sensitivity != event.proposal.sensitivity or not set(
                event.proposal.allowed_purposes
            ).issubset(current.allowed_purposes):
                raise IntegrityError("CHANGE_EVENT_GOVERNANCE_METADATA_MISMATCH")
            if event.operation == "UPDATE" and current.payload.get("canonical_value") == value:
                raise IntegrityError("CHANGE_EVENT_NO_SEMANTIC_DELTA")
            try:
                proposed = self.store.get_object(event.proposal.id, event.proposal.version)
            except KeyError:
                self.store.insert_version(connection, event.proposal, make_current=False)
            else:
                if proposed.digest != event.proposal.digest:
                    raise IntegrityError("CHANGE_EVENT_PROPOSAL_VERSION_CONFLICT")
            self.store.save_artifact(
                connection,
                self.artifact_id(event.event_id),
                CHANGE_EVENT_MEDIA_TYPE,
                event.model_dump(mode="json"),
            )
            if self.workspace is not None:
                from orgrebase.workspace.enterprise_binding import bind_change_owner
                bind_change_owner(self.workspace, connection, event)
            self.store.append_event(
                connection,
                "WORKSPACE_CHANGE_REGISTERED",
                {
                    "event_id": event.event_id,
                    "event_digest": event.digest,
                    "object_ref": event.proposal.ref,
                    "base_version": event.base_version,
                },
            )
        if owns_transaction:
            self.refresh()
        return event

    def rejection(self, event_id: str) -> dict[str, Any] | None:
        try:
            return self.store.load_artifact(
                f"workspace-rejection:{event_id}@r1", "application/vnd.orgrebase.change-rejection+json"
            ).payload
        except KeyError:
            return None

    def reject(self, event_id: str, actor_id: str, reason: str, *, connection: Any) -> dict[str, Any]:
        if not reason.strip() or len(reason) > 1000:
            raise ValueError("CHANGE_REJECTION_REASON_REQUIRED")
        with nullcontext(connection):
            authorization = current_authorization()
            if authorization is not None:
                authorization()
            event = self.get(event_id)
            if self.verify_approval_actor is not None:
                self.verify_approval_actor(event_id, actor_id)
            elif actor_id != event.owner_id:
                raise AuthorizationError("CHANGE_REJECTION_OWNER_REQUIRED")
            payload = {
                "event_id": event_id,
                "event_digest": event.digest,
                "actor_id": actor_id,
                "reason": reason.strip(),
                "status": "REJECTED",
            }
            existing = self.rejection(event_id)
            if existing is not None:
                if existing != payload:
                    raise IntegrityError("CHANGE_REJECTION_ID_CONFLICT")
                return existing
            self.store.save_artifact(
                connection,
                f"workspace-rejection:{event_id}@r1",
                "application/vnd.orgrebase.change-rejection+json",
                payload,
            )
            self.store.append_event(connection, "WORKSPACE_CHANGE_REJECTED", payload)
        return payload

    def invalidate(
        self, slot_id: str, source_ref: str, reason: str, *, connection: Any | None = None
    ) -> dict[str, Any]:
        binding = next((item for item in self.enterprise.resources if item.slot_id == slot_id), None)
        if binding is None:
            raise IntegrityError("SOURCE_INVALIDATION_SLOT_UNKNOWN")
        if not source_ref.strip() or not reason.strip():
            raise IntegrityError("SOURCE_INVALIDATION_EVIDENCE_REQUIRED")
        media_type = "application/vnd.orgrebase.source-invalidation+json"
        scope = self.store.transaction() if connection is None else nullcontext(connection)
        with scope as selected:
            authorization = current_authorization()
            if authorization is not None:
                authorization()
            source = self.store.get_object(binding.object_id)
            artifact_id = f"workspace-source-invalidation:{sha256_digest((slot_id, source_ref, reason, source.ref, source.digest))[7:]}"
            try:
                existing = self.store.load_artifact(artifact_id, media_type).payload
            except KeyError:
                existing = None
            if existing is not None:
                return existing
            self.store.transition_current(selected, source.id, ObjectState.STALE)
            try:
                quote = self.store.get_object(self.enterprise.quote_object_id)
            except KeyError:
                quote = None
            if quote is not None:
                self.store.transition_current(selected, quote.id, ObjectState.REVIEW_REQUIRED)
            receipt = {
                "status": "OWNER_READMISSION_REQUIRED",
                "slot_id": slot_id,
                "source_ref": source_ref,
                "reason": reason,
                "object_ref": source.ref,
                "object_digest": source.digest,
            }
            self.store.save_artifact(selected, artifact_id, media_type, receipt)
            self.store.append_event(selected, "WORKSPACE_SOURCE_INVALIDATED", receipt)
        return receipt

    def gaps(self) -> tuple[dict[str, str], ...]:
        gaps = []
        for binding in self.enterprise.resources:
            source = self.store.get_object(binding.object_id)
            if source.state not in {ObjectState.CURRENT, ObjectState.ACTIVE, ObjectState.CANARY}:
                gaps.append(
                    {
                        "slot_id": binding.slot_id,
                        "object_ref": source.ref,
                        "owner_id": binding.owner_id,
                        "state": source.state.value,
                        "status": "OWNER_READMISSION_REQUIRED",
                    }
                )
        return tuple(gaps)
